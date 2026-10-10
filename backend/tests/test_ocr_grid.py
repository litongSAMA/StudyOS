"""课表图像 → 二维表格几何重建：行/列聚类、矩形化、上限截断、降级安全。

全部用合成坐标做断言，不加载 OCR 模型、不联网；末尾两条降级用例在
rapidocr 可用或不可用的环境下都必须通过。

合成数据按真实排版比例构造：中文字宽 ≈ 字高（近似方块），课表列距 180px
（1280px 宽截图 7 天），行距 100px，行内 y 抖动 ±3px。
"""
import io

import pytest

from app.infra.ingest.ocrgrid import boxes_to_grid, ocr_grid


def box(text, cx, cy, w=42.0, h=21.0):
    """(text, cx, cy, w, h)：文本框中心坐标与宽高，模拟 rapidocr 的角点换算结果。"""
    return (text, float(cx), float(cy), float(w), float(h))


class FakeOcr:
    """替身：伪装 rapidocr 结果与单例，用于验证角点解析（无需真模型）。"""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        return self.result, 0.01


@pytest.fixture
def fake_ocr(monkeypatch):
    """把 OCR 单例换成替身；返回安装函数（须同时替换 ocrgrid 已导入的 ocr_available）。"""
    from app.infra.ingest import ocr as ocr_mod
    from app.infra.ingest import ocrgrid

    def install(result):
        fake = FakeOcr(result)
        monkeypatch.setattr(ocr_mod, "_ocr", fake)
        monkeypatch.setattr(ocrgrid, "ocr_available", lambda: True)
        return fake

    return install


def blank_png(width, height):
    """纯白 PNG 字节（走内存，不落盘）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


HEADERS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DAY_X = [400 + i * 180 for i in range(7)]  # 各天列中心（左标签列之外）
ROW_Y = [120 + i * 100 for i in range(4)]  # 表头 + 3 个节次行


def timetable_boxes():
    """合成一张 7 天 × 3 节次的课表文本块，每行 y 加 ±3px 抖动模拟真实识别。"""
    boxes = [box(day, x, ROW_Y[0] + (i % 3) - 1) for i, (day, x) in enumerate(zip(HEADERS, DAY_X))]
    rows = [
        ("第1-2节", "08:00-09:40", {1: "高等数学", 2: "计算机组成原理"}),
        ("第3-4节", "10:00-11:40", {0: "大学英语", 3: "线性代数"}),
        ("第5-6节", "14:00-15:40", {4: "数据结构", 5: "大学物理"}),
    ]
    for ri, (section, clock, cells) in enumerate(rows):
        cy = ROW_Y[ri + 1] + (ri % 3) - 1
        boxes.append(box(section, 60, cy, w=80))  # 第1-2节：3 字 + 数字
        boxes.append(box(clock, 200, cy + 2, w=120))  # 08:00-09:40（与节次同列，差 56px < 列距 ）
        for di, name in cells.items():
            boxes.append(box(name, DAY_X[di], cy - (di % 3) + 2, w=len(name) * 21))
    return boxes


def test_grid_shape_and_cell_placement():
    """节次/时间各成一列，7 个星期列对齐，课程落在「星期表头列」。"""
    grid = boxes_to_grid(timetable_boxes(), 1280, 800)
    assert len(grid) == 4
    assert all(len(r) == len(grid[0]) for r in grid)  # 绝不参差不齐
    assert len(grid[0]) >= 8  # 左标签列（节次/时间）+ 7 个星期列

    # 星期列以表头为准：从「周一」列起 7 格正好是周一到周日
    head_idx = grid[0].index("周一")
    assert grid[0][head_idx : head_idx + 7] == HEADERS
    assert head_idx >= 1  # 左边至少有一个标签列
    row1 = grid[1]
    assert row1[head_idx + 1] == "高等数学"  # 周一列
    assert row1[head_idx + 2] == "计算机组成原理"  # 周三列：y 抖动 4px 仍归同一行
    assert row1[head_idx] == ""  # 周二空白格
    assert grid[3][head_idx + 2] == ""  # 第5-6节 周三无课
    assert grid[3][head_idx + 5] == "大学物理"  # 周五列


def test_left_labels_share_one_row_and_stay_left_of_headers():
    """节次与时间标签落在星期表头左侧的标签列，且与对应课程同处一行。"""
    grid = boxes_to_grid(timetable_boxes(), 1280, 800)
    head_idx = grid[0].index("周一")
    labels = " ".join(grid[1][:head_idx])
    assert "第1-2节" in labels and "08:00-09:40" in labels  # 节次与时间同在左标签列
    assert grid[2][head_idx] == "大学英语"  # 第3-4节 周一
    assert grid[2][grid[0].index("周四")] == "线性代数"  # 第3-4节 周四（y 抖动 -3px）


def test_row_merge_tolerates_y_jitter():
    """同一行内 ±5px 抖动不得裂成两行（容差由中位行高推出，非固定像素）。"""
    boxes = [box(name, DAY_X[i], ROW_Y[0] + jitter)
             for i, (name, jitter) in enumerate(zip(HEADERS[:4], [0, 5, -5, 3]))]
    boxes += [box("第1-2节", 60, 120, w=80), box("高等数学", DAY_X[0], 218, w=84),
              box("大学英语", DAY_X[1], 223, w=84)]
    grid = boxes_to_grid(boxes, 1280, 800)
    assert len(grid) == 2
    assert grid[0][1:5] == HEADERS[:4]
    assert grid[1][1] == "高等数学" and grid[1][2] == "大学英语"


def test_weekday_header_row_is_never_merged_into_data_row():
    """表头行与首行数据的间距小于行距时，表头**不得**被并进第一行。

    真实课表（尤其教务系统 PDF）里表头常常比数据行挨得更近：实测表头距首行 60px、
    节次行距 100px 时，纯按距离判定会把「周一…周日」并进第 1-2 节那一行，
    星期列随之消失，整份课表直接解析失败。
    """
    boxes = [box(day, x, 60) for day, x in zip(HEADERS, DAY_X)]
    boxes += [box("第1-2节", 60, 120, w=80), box("高等数学", DAY_X[0], 120, w=84),
              box("第3-4节", 60, 220, w=80), box("大学英语", DAY_X[1], 220, w=84)]
    grid = boxes_to_grid(boxes, 1280, 800, max_rows=20, max_cols=20)
    assert len(grid) == 3, grid
    assert grid[0][1:8] == HEADERS                     # 表头独占一行、7 列齐全
    assert grid[1][1] == "高等数学" and "第1-2节" in " ".join(grid[1][:1])
    assert grid[2][2] == "大学英语"


def test_nearby_boxes_in_one_cell_join_left_to_right():
    """同一格内两个紧邻文本框按 x 从左到右拼接；相距较远的另一个列不并入。"""
    boxes = [box("高等", 100, ROW_Y[1]), box("数学", 148, ROW_Y[1]),
             box("第1-2节", 60, ROW_Y[2], w=80), box("周一", DAY_X[0], ROW_Y[0])]
    grid = boxes_to_grid(boxes, 1280, 800)
    labels = " ".join(grid[1])
    assert "高等 数学" in labels  # 同一格：从左到右用一个空格拼接
    assert "高等 数学" not in " ".join(grid[2])  # 第1-2节 那一格没有课名


def test_ragged_input_makes_rectangular_matrix():
    """参差输入（各行框数不同）必须补空串成矩形。"""
    grid = boxes_to_grid([box("周一", DAY_X[0], ROW_Y[0]), box("第1-2节", 60, ROW_Y[1], w=80),
                          box("高等数学", DAY_X[0], ROW_Y[1], w=84),
                          box("大学英语", DAY_X[2], ROW_Y[1], w=84)], 1280, 800)
    width = len(grid[0])
    assert width == 3  # 节次列 + 周一列 + 周三列
    assert all(len(r) == width for r in grid)
    assert grid[0][0] == "" and grid[0][2] == ""  # 表头缺格补空串
    assert grid[0][1] == "周一" and grid[1][1] == "高等数学" and grid[1][2] == "大学英语"


def test_capped_by_max_rows_and_cols():
    """超过 max_rows/max_cols 的文本框被截断，行数/列数不越界。"""
    boxes = [box(f"c{c}", 100 + c * 100, 50 + r * 80) for r in range(8) for c in range(8)]
    grid = boxes_to_grid(boxes, 1200, 800, max_rows=60, max_cols=20)  # 未超限：完整输出
    assert len(grid) == 8 and len(grid[0]) == 8
    small = boxes_to_grid(boxes, 1200, 800, max_rows=3, max_cols=4)
    assert len(small) == 3
    assert all(len(r) == 4 for r in small)
    assert boxes_to_grid(boxes, 1200, 800, max_rows=0, max_cols=0) == []


def test_cell_truncated_and_empty_input():
    """单格文本截到 80 字符；空输入返回空表。"""
    grid = boxes_to_grid([box("长" * 200, 100, 50, w=400)], 600, 400)
    assert len(grid[0][0]) == 80
    assert boxes_to_grid([], 600, 400) == []


def test_box_geometry_from_four_corners(fake_ocr, tmp_path):
    """4 角点任意顺序 → 外接矩形中心与宽高；'1 2' 空格按 CJK/数字规则清理。"""
    from app.infra.ingest.ocrgrid import ocr_text_boxes

    p = tmp_path / "grid.png"
    p.write_bytes(blank_png(400, 300))
    fake_ocr([
        [[[100.0, 50.0], [140.0, 52.0], [138.0, 70.0], [98.0, 68.0]], "1 2 月", "0.9"],
        [[[30.0, 120.0], [90.0, 120.0], [90.0, 140.0], [30.0, 140.0]], "第1-2节", "0.9"],
    ])
    boxes, w, h = ocr_text_boxes(p)
    assert (w, h) == (400, 300)
    assert boxes[0][0] == "12月"  # _cleanup 合并 CJK/数字之间空格
    assert boxes[0][1] == pytest.approx(119.0)  # cx
    assert boxes[0][3] == pytest.approx(42.0)  # w
    assert boxes[1][2] == pytest.approx(130.0)  # cy
    assert boxes[1][4] == pytest.approx(20.0)  # h


def test_ocr_grid_nonexistent_path(tmp_path):
    """路径不存在：降级为空结果，绝不抛异常。"""
    assert ocr_grid(tmp_path / "not-here.png") == {"rows": [], "text": "", "boxes": 0}


def test_ocr_grid_blank_png(tmp_path):
    """空白小图：返回结构完整的 dict；若装了 rapidocr 并识别出文字，接受非空栅格。"""
    p = tmp_path / "blank.png"
    p.write_bytes(blank_png(64, 64))
    out = ocr_grid(p)
    assert set(out) == {"rows", "text", "boxes"}
    assert isinstance(out["rows"], list) and isinstance(out["text"], str)
    assert isinstance(out["boxes"], int)
    if out["rows"]:  # rapidocr 可用且识别出文字：栅格必须矩形且行列数一致
        assert out["boxes"] > 0
        assert all(len(r) == len(out["rows"][0]) for r in out["rows"])
    else:
        assert out == {"rows": [], "text": "", "boxes": 0}
