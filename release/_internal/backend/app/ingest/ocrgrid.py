"""课表截图 → 二维表格几何重建（OCR 带位置版本）。

`app/ingest/ocr.py` 只吐出一行行纯文本，课表的行列结构（行 = 节次/时间，
列 = 星期）在纯文本里丢失；本模块改用 rapidocr 的文本框坐标把版面还原成
rows × cols 矩阵，交给下游模块做语义解读（哪一列是周几、哪一格是课程）。

只做几何，不做语义：不认「周一」也不认课名，因此对任意版式都能跑。
尺寸安全校验复用 imagecheck（防解压炸弹 OOM），失败一律降级为空结果。
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from .imagecheck import check_image_size
from .ocr import _cleanup, ocr_available

# 同一行/列的判定容差系数与下限：容差 = 系数 × 该方向的中位尺寸，
# 不能用固定像素值——手机截图 1080px 宽、电脑截图 2560px 宽，字号差一倍。
_TOL_RATIO = 0.6
_MIN_TOL = 1.0
# 容差还要受行距/列距封顶：紧凑排版时行距可能小于字高，避免把相邻行/列并成一行
_PITCH_RATIO = 0.45
# 相邻两行中心差小于「实测行距 × 该比例」时视为同一个网格行（格内错行）
_ROW_MERGE_RATIO = 0.75

# 中心距与间隙的双重容差上限（见 _cluster_axis）
_CENTER_SLACK = 0.25
_MAX_CELL_CHARS = 80  # 单格文本上限，防某格 OCR 出整行文字撑爆下游
_MAX_TEXT_CHARS = 200_000  # 原文上限，与 files.py 各抽取器保持一致


def _median(values: list[float]) -> float:
    """中位数；空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _bounds(
    boxes: list[tuple[str, float, float, float, float]], members: list[int], axis: int
) -> tuple[float, float]:
    """一组成员在 x(axis=1)/y(axis=2) 轴上的外接区间。"""
    parts = []
    for i in members:
        _, cx, cy, w, h = boxes[i]
        parts.append((cx - w / 2, cx + w / 2) if axis == 1 else (cy - h / 2, cy + h / 2))
    return min(p[0] for p in parts), max(p[1] for p in parts)


def _tolerance(
    boxes: list[tuple[str, float, float, float, float]], axis: int, ratio: float
) -> float:
    """聚类容差：ratio × 该方向中位尺寸，并受「表内自然间距」封顶。

    不能用固定像素值——手机截图 1080px 宽、电脑截图 2560px 宽，字号差一倍；
    也不能只按字高算：同一格里「第1-2节」和「08:00-09:40」各占一条 OCR 线、
    中心差可达一个半字高，按字高算容差会把它们拆成不同行。`pitches` 传入时用
    它封顶（那是表的行距/列距，由 _row_pitch 之类先从结构里量出来）。
    """
    sizes = [b[3] if axis == 1 else b[4] for b in boxes]
    med = _median([s for s in sizes if s > 0])
    base = max(ratio * med, _MIN_TOL) if med > 0 else _MIN_TOL
    pitch = _pitch([b[axis] for b in boxes], base)
    if pitch > 0:
        base = min(base, max(_PITCH_RATIO * pitch, _MIN_TOL))
    return base


def _pitch(centers: list[float], tol: float) -> float:
    """估算「格内错行」的自然间距：把中心坐标按 tol 聚成位置，取其中较小的那批间距。

    不能直接对排序后的中心求间距——同一行内的 ±3px 抖动会伪装成很小的间距，
    把容差压到 1px，反而让同一行裂开。课表里表头、节次标签、首行课名常常各占
    一条 OCR 线（彼此间距 0.5~1.5 字高），而相邻网格行/列的间距要大得多，
    所以舍掉最大的那部分间距，取剩下的中位数。
    """
    positions: list[float] = []
    group: list[float] = []
    for v in sorted(centers):
        if group and v - group[-1] > tol:
            positions.append(_median(group))
            group = []
        group.append(v)
    if group:
        positions.append(_median(group))
    gaps = sorted(b - a for a, b in zip(positions, positions[1:]) if b - a > 0)
    if not gaps:
        return 0.0
    keep = gaps[: max(1, (len(gaps) * 2) // 3)]  # 丢掉最大的 1/3（那是网格行/列间距）
    return _median(keep)


_ROW_LABEL_RE = re.compile(r"^\s*(?:第\s*)?\d{1,2}\s*(?:[-—~–、,，]|至|到)?\s*\d{0,2}\s*(?:节|课)?\s*$")
_TIME_CELL_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}")


def _is_row_label(text: str) -> bool:
    """文本是否像行标签（「第1-2节」「08:00-09:40」「3-4」）。"""
    s = (text or "").strip()
    if not s or len(s) > 16:
        return False
    if _TIME_CELL_RE.search(s):
        return True
    return bool(_ROW_LABEL_RE.match(s.replace("第", "").replace("节", "").strip() or "x"))


def _merge_short_rows(
    boxes: list[tuple[str, float, float, float, float]], rows: list[list[int]]
) -> list[list[int]]:
    """把靠得太近的相邻「行」合并：课表里表头、节次标签、首行课名各占一条 OCR 线。

    判定用实测行距 `_row_pitch`：两行的中心差小于行距的 75% 就属于同一个网格行，
    合并后按 x 重新排布自然还原成一格。行距量不出来（没有节次/时间标签）时不合并。

    例外：**含 ≥2 个星期名的行（「周一…周日」表头）永不参与合并**。
    表头与首行数据的间距有时小于行距（PDF 排版常见），按纯距离判定会把表头并进
    第一行，星期列随之消失，整份课表直接解析失败（实测：表头距首行 30pt、行距 40pt
    时 100% 触发）。表头是网格的列定义，本来就不该被合并掉。
    """
    if len(rows) < 2:
        return rows
    base = _tolerance(boxes, 2, _TOL_RATIO)
    pitch = _row_pitch(boxes, base)
    if pitch <= 0:
        return rows
    threshold = pitch * _ROW_MERGE_RATIO
    merged: list[list[int]] = [list(rows[0])]
    for row in rows[1:]:
        prev = merged[-1]
        gap = abs(
            _median([boxes[i][2] for i in row]) - _median([boxes[i][2] for i in prev])
        )
        if (
            gap <= threshold
            and _weekday_count(boxes, prev) < 2
            and _weekday_count(boxes, row) < 2
        ):
            prev.extend(row)
        else:
            merged.append(list(row))
    return merged


#: 单元格里的星期名（中文「周一」/英文「Mon」）
_WEEKDAY_CELL_RE = re.compile(
    r"(?:周|星期|礼拜)\s*[一二三四五六日天1-7]|^(?:mon|tue|wed|thu|fri|sat|sun)", re.I
)


def _weekday_count(
    boxes: list[tuple[str, float, float, float, float]], row: list[int]
) -> int:
    """一行里有多少个「星期名」单元格（≥2 说明这是课表的表头行）。"""
    return sum(1 for i in row if _WEEKDAY_CELL_RE.search(boxes[i][0] or ""))


def _row_pitch(
    boxes: list[tuple[str, float, float, float, float]], base_tol: float
) -> float:
    """量出表的行距：用左侧「节次/时间」标签行的高度差。

    每行恰好一个标签，所以标签中心之间的间距就是真实行距；它比字高大得多
    （通常是 3~5 倍字高），拿它当容差上限，既能把同一格里错行的文字并进一行，
    又不会把相邻两行并成一行。量不出来（没识别到标签）时返回 0。
    """
    labels = [b for b in boxes if _is_row_label(b[0])]
    if len(labels) < 2:
        return 0.0
    centers = sorted(b[2] for b in labels)
    groups: list[list[float]] = [[centers[0]]]
    for cy in centers[1:]:
        if cy - groups[-1][-1] <= base_tol:
            groups[-1].append(cy)
        else:
            groups.append([cy])
    if len(groups) < 2:
        return 0.0
    gaps = [b - a for a, b in zip([_median(g) for g in groups], [_median(g) for g in groups][1:])]
    return _median([g for g in gaps if g > 0])


def _cluster_axis(
    boxes: list[tuple[str, float, float, float, float]], axis: int, ratio: float
) -> list[list[int]]:
    """沿某一轴聚类：把文本框归入最合适的已有组，否则另起一组。

    归组判据只看**与组内某个成员**的区间关系：明显重叠（≥ 较窄区间的一半），
    或中心距与边缘间隙都在容差内。刻意不用「与本组中心的距离」——组中心会被
    新并入的成员拖着走（左标签列并入表头后中心偏 70px），判定就成了顺序相关的：
    宽课名会把隔壁表头一起拖进标签列。
    """
    if not boxes:
        return []
    tol = _tolerance(boxes, axis, ratio)

    def _joins(i: int, group: list[int]) -> bool:
        box_lo, box_hi = _bounds(boxes, [i], axis)
        for j in group:
            j_lo, j_hi = _bounds(boxes, [j], axis)
            if _merge_pair(box_lo, box_hi, j_lo, j_hi, tol):
                return True
            if (
                abs(boxes[i][axis] - boxes[j][axis]) <= tol
                and _axis_gap(box_lo, box_hi, j_lo, j_hi) <= tol
            ):
                return True
        return False

    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][axis], i))
    groups: list[list[int]] = []
    for i in order:
        if not groups:
            groups.append([i])
            continue
        near = min(
            range(len(groups)),
            key=lambda g: min(abs(boxes[i][axis] - boxes[j][axis]) for j in groups[g]),
        )
        if _joins(i, groups[near]):
            groups[near].append(i)
        else:
            groups.append([i])
    groups.sort(key=lambda g: _median([boxes[i][axis] for i in g]))
    return groups


def _axis_gap(lo1: float, hi1: float, lo2: float, hi2: float) -> float:
    """两区间沿某一轴的间隙；重叠返回 0。"""
    return max(0.0, max(lo1, lo2) - min(hi1, hi2))


def _merge_pair(lo1: float, hi1: float, lo2: float, hi2: float, tol: float) -> bool:
    """两个区间是否属于同一行/列：明显重叠，或边缘间隙在容差内。"""
    overlap = min(hi1, hi2) - max(lo1, lo2)
    if overlap > 0 and overlap >= 0.5 * min(hi1 - lo1, hi2 - lo2):
        return True
    return _axis_gap(lo1, hi1, lo2, hi2) <= tol


def _parse_box(raw: object) -> tuple[float, float, float, float] | None:
    """rapidocr 的 4 角点 → (cx, cy, w, h)；角点顺序任意，取外接矩形。"""
    try:
        points = [(float(p[0]), float(p[1])) for p in raw]  # type: ignore[union-attr]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, w, h)


def ocr_text_boxes(path: Path) -> tuple[list[tuple[str, float, float, float, float]], int, int]:
    """rapidocr 识别 → ([(text, cx, cy, w, h)], image_width, image_height)；不可用/失败 → ([], 0, 0)。

    cx, cy 是文本框中心坐标，w/h 是文本框宽高（均为像素）。text 已做中文空格清理。
    """
    try:
        check_image_size(path)  # 只读 header 校验像素，防解压炸弹 OOM
    except Exception:
        return [], 0, 0
    if not ocr_available():
        return [], 0, 0

    try:
        with Image.open(path) as im:  # 已通过尺寸校验，此处只取宽高
            img_w, img_h = im.size
    except Exception:
        return [], 0, 0

    try:
        from . import ocr as _ocr_mod  # 取当前单例：ocr_available() 内部才完成初始化

        result, _ = _ocr_mod._ocr(str(path))
        if not result:
            return [], img_w, img_h
        boxes: list[tuple[str, float, float, float, float]] = []
        for item in result:
            try:
                box, text, _score = item[0], item[1], item[2]
            except (TypeError, IndexError, KeyError):
                continue
            text = _cleanup(str(text or "")).strip()
            if not text:
                continue
            geom = _parse_box(box)
            if geom is None:
                continue
            cx, cy, w, h = geom
            boxes.append((text, cx, cy, w, h))
        return boxes, img_w, img_h
    except Exception:
        return [], img_w, img_h


def _cluster_rows(boxes: list[tuple[str, float, float, float, float]]) -> list[list[int]]:
    """按 cy 聚类成行：先用左侧节次/时间标签量出行距，再用它当容差上限。

    只用字高当容差会把「表头 / 节次标签 / 首行课名」各拆成一行（它们各占一条
    OCR 线、中心差到一个半字高）；用真实行距封顶后，同一格里的错行并回一行，
    相邻两行仍分得开。
    """
    rows = _cluster_axis(boxes, 2, _TOL_RATIO)
    return _merge_short_rows(boxes, rows)


def _cluster_cols(boxes: list[tuple[str, float, float, float, float]]) -> list[list[int]]:
    """按 cx 聚类成列：表头（周一）与列内单元格共享列号。

    容差取 0.6 × 文本框中位宽度，并用列距封顶，避免相邻星期被并成一列。
    """
    return _cluster_axis(boxes, 1, _TOL_RATIO)


def boxes_to_grid(
    boxes: list[tuple[str, float, float, float, float]],
    img_w: int,
    img_h: int,
    max_rows: int = 60,
    max_cols: int = 20,
) -> list[list[str]]:
    """把带位置的 OCR 文本块重建成二维表格：同一行的文本块按 x 合并成一格，行按 y 自上而下排序。

    返回 rows × cols 的字符串矩阵（等宽矩形，空串表示空格）。
    """
    if not boxes:
        return []
    row_cap = max(0, int(max_rows))
    col_cap = max(0, int(max_cols))
    if row_cap == 0 or col_cap == 0:
        return []

    rows = _cluster_rows(boxes)[:row_cap]  # 已按 cy 自上而下排序
    cols = _cluster_cols(boxes)[:col_cap]  # 已按 cx 自左向右排序

    row_of: dict[int, int] = {i: ri for ri, r in enumerate(rows) for i in r}
    col_of: dict[int, int] = {i: ci for ci, c in enumerate(cols) for i in c}

    cells: dict[tuple[int, int], list[int]] = {}
    for i in range(len(boxes)):
        ri, ci = row_of.get(i), col_of.get(i)
        if ri is None or ci is None:  # 被 max_rows/max_cols 截掉的框
            continue
        cells.setdefault((ri, ci), []).append(i)

    grid = [["" for _ in range(len(cols))] for _ in range(len(rows))]
    for (ri, ci), members in cells.items():
        # 同一格多个文本框按 x 自左向右拼接，保留阅读顺序
        members.sort(key=lambda i: (boxes[i][1], boxes[i][2]))
        grid[ri][ci] = " ".join(boxes[i][0] for i in members)[:_MAX_CELL_CHARS]
    return grid


def _rows_to_text(
    rows: list[list[str]], boxes: list[tuple[str, float, float, float, float]]
) -> str:
    """按行拼接 OCR 原文（同行以空格分隔，行间换行），供下游 LLM 兜底解读。"""
    if rows:
        text = "\n".join(" ".join(cell for cell in row if cell) for row in rows)
    else:
        ordered = sorted(boxes, key=lambda b: (b[2], b[1]))
        text = "\n".join(b[0] for b in ordered)
    return text.strip()[:_MAX_TEXT_CHARS]


def ocr_grid(path: Path) -> dict:
    """入口：图片 → {"rows": [[str,...], ...], "text": "按行拼接的 OCR 原文", "boxes": int}

    识别不可用或没有任何文本 → {"rows": [], "text": "", "boxes": 0}（绝不抛异常）。
    """
    empty: dict = {"rows": [], "text": "", "boxes": 0}
    try:
        boxes, img_w, img_h = ocr_text_boxes(path)
        if not boxes:
            return empty
        rows = boxes_to_grid(boxes, img_w, img_h)
        if not rows:
            return empty
        return {
            "rows": rows,
            "text": _rows_to_text(rows, boxes),
            "boxes": len(boxes),
        }
    except Exception:
        return empty
