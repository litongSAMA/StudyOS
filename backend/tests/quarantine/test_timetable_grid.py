# -*- coding: utf-8 -*-
"""课表导入新通道：Excel/CSV 二维网格解析、单元格课程解析、图片 OCR 表格重建、路由分流。"""
import csv
import io

import pytest

from app.courseimport import ImportParseError
from app.timetable_grid import (
    Grid,
    _match_label_periods,
    _match_periods,
    _match_weekday,
    grid_from_ocr_boxes,
    parse_course_cell,
    parse_excel,
    parse_grid,
)


@pytest.fixture(autouse=True)
def _ensure_schema(client):
    """纯函数测试不直接用 client，但 conftest 的 autouse clean_db 在 teardown
    需要表已存在；client 是 session 级，这里触发一次 lifespan 建表即可。"""
    pass


# ---------------------------------------------------------------------------
# 小工具：星期 / 节次识别
# ---------------------------------------------------------------------------

def test_match_weekday_variants():
    assert _match_weekday("星期一") == 1
    assert _match_weekday("周二") == 2
    assert _match_weekday("礼拜三") == 3
    assert _match_weekday("Sunday") == 7
    assert _match_weekday("星期一 9月1日") == 1
    assert _match_weekday("课程名") is None


def test_match_periods():
    assert _match_periods("第1-2节") == [1, 2]
    assert _match_periods("3-4节") == [3, 4]
    assert _match_periods("第5节") == [5]
    assert _match_periods("上午") is None


# ---------------------------------------------------------------------------
# 单元格课程文本解析
# ---------------------------------------------------------------------------

def test_cell_standard_newlines():
    frags = parse_course_cell("高等数学\n张三\n教1-101\n1-16周", 1, [1, 2])
    assert len(frags) == 1
    f = frags[0]
    assert f["name"] == "高等数学"
    assert f["teacher"] == "张三"
    assert f["location"] == "教1-101"
    assert f["slot"]["weeks"] == list(range(1, 17))
    assert f["slot"]["start"] == "08:00" and f["slot"]["end"] == "09:40"


def test_cell_at_and_hash_separators():
    frags = parse_course_cell("大学英语@教2-202#李四#1-8周", 2, [1, 2])
    f = frags[0]
    assert f["name"] == "大学英语"
    assert f["teacher"] == "李四"
    assert f["location"] == "教2-202"
    assert f["slot"]["weeks"] == list(range(1, 9))


def test_cell_odd_weeks_and_paren_teacher():
    # 2-17 单周 → 奇数周 3,5,...,17；括号内老师
    frags = parse_course_cell("线性代数（2-17单周）(赵六) 教3-305", 1, [3, 4])
    f = frags[0]
    assert f["name"] == "线性代数"
    assert f["teacher"] == "赵六"
    assert f["location"] == "教3-305"
    assert f["slot"]["weeks"] == list(range(3, 18, 2))
    assert f["slot"]["start"] == "10:00" and f["slot"]["end"] == "11:40"


def test_cell_even_weeks():
    frags = parse_course_cell("体育\n孙八\n体育馆\n1-16周(双)", 5, [3, 4])
    f = frags[0]
    assert f["slot"]["weeks"] == list(range(2, 17, 2))
    assert f["location"] == "体育馆"


def test_cell_teacher_title_stripped():
    frags = parse_course_cell("马克思主义原理\n吴十老师\n教4-401\n第1-12周", 4, [5, 6])
    assert frags[0]["teacher"] == "吴十"
    assert frags[0]["slot"]["start"] == "14:00"


def test_cell_room_number_not_mistaken_as_weeks():
    # 教1-101 不得被当成「第1-101周」，课程名里不得残留教室号
    frags = parse_course_cell("高等数学\n张三\n教1-101\n1-16周", 1, [1, 2])
    f = frags[0]
    assert f["location"] == "教1-101"
    assert "教" not in f["name"]
    assert max(f["slot"]["weeks"]) == 16


def test_cell_two_courses_split_by_week_anchor():
    raw = "1-8周 英语 李四 教2-202\n9-16周 日语 王五 教2-203"
    frags = parse_course_cell(raw, 3, [1, 2])
    assert len(frags) == 2
    assert frags[0]["name"].startswith("英语")
    assert frags[0]["slot"]["weeks"] == list(range(1, 9))
    assert frags[0]["location"] == "教2-202"
    assert frags[1]["name"].startswith("日语")
    assert frags[1]["slot"]["weeks"] == list(range(9, 17))
    assert frags[1]["location"] == "教2-203"


def test_cell_online_location():
    frags = parse_course_cell("形势与政策\n线上\n1-4周", 6, [7, 8])
    assert frags[0]["location"] == "线上"
    assert frags[0]["slot"]["weeks"] == [1, 2, 3, 4]


def test_cell_requires_time():
    # 既无节次也无时间范围 → 无法定位，跳过
    assert parse_course_cell("某课程\n张三", 1, None, None) is None


# ---------------------------------------------------------------------------
# 逻辑网格解析（直接构造 Grid，不依赖 openpyxl）
# ---------------------------------------------------------------------------

def _simple_grid():
    # 行0 表头；行1 第1-2节；行2 第3-4节
    return Grid([
        ["节次", "星期一", "星期二", "星期三"],
        ["第1-2节", "高等数学\n张三\n教1-101\n1-16周", "大学英语\n李四\n教2-202\n1-16周", ""],
        ["第3-4节", "", "操作系统\n周九\n教1-105\n1-8周", "大学物理\n钱七\n物理楼201\n1-16周"],
    ])


def test_parse_grid_basic():
    res = parse_grid(_simple_grid(), "excel")
    by_name = {e["name"]: e for e in res["entries"]}
    assert set(by_name) == {"高等数学", "大学英语", "操作系统", "大学物理"}
    math = by_name["高等数学"]
    assert math["teacher"] == "张三" and math["location"] == "教1-101"
    slot = math["slots"][0]
    assert slot["weekday"] == 1 and slot["start"] == "08:00" and slot["end"] == "09:40"
    # 操作系统在周二第3-4节
    os_slot = by_name["操作系统"]["slots"][0]
    assert os_slot["weekday"] == 2 and os_slot["start"] == "10:00"


def test_parse_grid_merged_period_cells():
    # 节次列纵向合并（第二行节次为空，由合并填充）——构造 owner 来模拟
    g = Grid(
        [
            ["节次", "星期一", "星期二", "星期三"],
            ["第1-2节", "高等数学\n张三\n教1-101\n1-16周", "", ""],
            ["第1-2节", "", "大学英语\n李四\n教2-202\n1-8周", ""],  # 合并填充导致重复节次
            ["第3-4节", "线性代数\n赵六\n教3-305\n1-16周", "", ""],
        ],
        merges=[(1, 0, 2, 0)],
    )
    res = parse_grid(g, "excel")
    by_name = {e["name"]: e for e in res["entries"]}
    assert "高等数学" in by_name and "线性代数" in by_name
    # 大学英语虽在合并节次的第二行，仍归入第1-2节
    eng = by_name["大学英语"]["slots"][0]
    assert eng["start"] == "08:00" and eng["weekday"] == 2


def test_parse_grid_missing_header_raises():
    g = Grid([["随便", "列"], ["a", "b"]])
    with pytest.raises(ImportParseError):
        parse_grid(g)


def test_parse_grid_time_column_instead_of_period():
    # 没有「第N节」，时间列直接写 08:00-09:40
    g = Grid([
        ["时间", "星期一", "星期二", "星期三"],
        ["08:00-09:40", "高等数学\n张三\n教1-101\n1-16周", "", ""],
        ["10:00-11:40", "", "大学英语\n李四\n教2-202\n1-16周", ""],
    ])
    res = parse_grid(g, "csv")
    by_name = {e["name"]: e for e in res["entries"]}
    assert by_name["高等数学"]["slots"][0]["start"] == "08:00"
    assert by_name["高等数学"]["slots"][0]["end"] == "09:40"


# ---------------------------------------------------------------------------
# Excel / CSV 字节流
# ---------------------------------------------------------------------------

def _build_xlsx_bytes():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["节次/星期", "星期一", "星期二", "星期三", "星期四", "星期五"])
    ws.append(["第1-2节", "高等数学\n张三\n教1-101\n1-16周", "大学英语\n李四\n教2-202\n1-16周", "", "", ""])
    ws.append(["第3-4节", "", "", "大学物理\n钱七\n物理楼201\n2-17单周", "", "体育\n孙八\n体育馆\n1-16周"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_xlsx():
    res = parse_excel(_build_xlsx_bytes(), "kb.xlsx")
    assert res["format"] == "excel"
    names = {e["name"] for e in res["entries"]}
    assert {"高等数学", "大学英语", "大学物理", "体育"} <= names
    phys = next(e for e in res["entries"] if e["name"] == "大学物理")
    assert phys["slots"][0]["weekday"] == 3
    assert phys["slots"][0]["weeks"] == list(range(3, 18, 2))  # 2-17 单周


def test_parse_excel_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["节次", "星期一", "星期二", "星期三"])
    w.writerow(["第1-2节", "高等数学\n张三\n教1-101\n1-16周", "大学英语\n李四\n教2-202\n1-8周", ""])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    res = parse_excel(data, "kb.csv")
    assert res["format"] == "csv"
    assert len(res["entries"]) == 2


# ---------------------------------------------------------------------------
# 图片 OCR 表格重建（用带坐标的文本块模拟 OCR 输出）
# ---------------------------------------------------------------------------

def _box(text, cx, cy, w=70, h=24):
    return {"text": text, "cx": cx, "cy": cy,
            "x1": cx - w / 2, "x2": cx + w / 2, "y1": cy - h / 2, "y2": cy + h / 2}


def _mock_timetable_boxes():
    # 5 列：节次(x≈60) + 周一(x≈200) + 周二(x≈360) + 周三(x≈520)；表头 y≈40
    boxes = [
        _box("节次", 60, 40), _box("星期一", 200, 40), _box("星期二", 360, 40), _box("星期三", 520, 40),
        # 第1-2节 y≈110
        _box("第1-2节", 60, 110, w=60),
        _box("高等数学", 200, 90), _box("张三", 200, 120), _box("教1-101", 200, 150), _box("1-16周", 200, 180),
        _box("大学英语", 360, 90), _box("李四", 360, 120), _box("教2-202", 360, 150), _box("1-16周", 360, 180),
        # 第3-4节 y≈260
        _box("第3-4节", 60, 260, w=60),
        _box("操作系统", 360, 240), _box("周九", 360, 270), _box("教1-105", 360, 300), _box("1-8周", 360, 330),
        _box("大学物理", 520, 240), _box("钱七", 520, 270), _box("物理楼201", 520, 300), _box("1-16周", 520, 330),
    ]
    return boxes


def test_grid_from_ocr_boxes_structure():
    g = grid_from_ocr_boxes(_mock_timetable_boxes())
    # 表头行 + 2 个节次行；列 = 标签 + 3 天
    assert g.n_rows == 3 and g.n_cols == 4
    assert g.at(0, 1) == "星期一"
    # 周一第1-2节单元格应包含课程名/老师/地点/周次（按 y 排序）
    cell = g.at(1, 1)
    assert "高等数学" in cell and "张三" in cell and "教1-101" in cell and "1-16周" in cell
    assert cell.index("高等数学") < cell.index("张三")  # 纵向顺序保留


def test_parse_image_boxes_end_to_end():
    g = grid_from_ocr_boxes(_mock_timetable_boxes())
    res = parse_grid(g, "image")
    by_name = {e["name"]: e for e in res["entries"]}
    assert set(by_name) == {"高等数学", "大学英语", "操作系统", "大学物理"}
    math = by_name["高等数学"]
    assert math["teacher"] == "张三" and math["location"] == "教1-101"
    assert math["slots"][0]["weekday"] == 1
    assert math["slots"][0]["start"] == "08:00" and math["slots"][0]["end"] == "09:40"
    os_ = by_name["操作系统"]
    assert os_["slots"][0]["weekday"] == 2 and os_["slots"][0]["weeks"] == list(range(1, 9))


def test_ocr_boxes_missing_header_raises():
    with pytest.raises(ImportParseError):
        grid_from_ocr_boxes([_box("随便", 100, 40), _box("文字", 200, 80)])


def test_ocr_boxes_empty_raises():
    with pytest.raises(ImportParseError):
        grid_from_ocr_boxes([])


# ---------------------------------------------------------------------------
# 路由层：/api/courses/import/preview 多格式分流
# ---------------------------------------------------------------------------

def test_preview_endpoint_excel(client):
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.xlsx", _build_xlsx_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["format"] == "excel"
    names = [e["name"] for e in pv["entries"]]
    assert "高等数学" in names
    # 每个条目都带颜色与 exists 标记
    assert all(e["color"].startswith("#") and "exists" in e for e in pv["entries"])


def test_preview_endpoint_csv(client):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["节次", "星期一", "星期二", "星期三"])
    w.writerow(["第1-2节", "高等数学\n张三\n教1-101\n1-16周", "", ""])
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["format"] == "csv"


def test_preview_endpoint_bad_excel_returns_422(client):
    # 内容不是合法课表（无星期表头）→ 422 中文错误
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.xlsx", b"not-a-real-xlsx", "application/octet-stream")},
    )
    assert r.status_code == 422


def test_preview_endpoint_image_disabled_without_ocr(client, monkeypatch):
    # 测试环境 ocr_enabled=False：图片通道返回 422（不是 500）
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 422


def test_preview_endpoint_image_loading_returns_503(client, monkeypatch):
    from app.routers import courses

    monkeypatch.setattr(courses.ocr, "ocr_status", lambda: "loading")
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 503
    assert "加载中" in r.json()["detail"]


def test_preview_endpoint_image_parsed_when_available(client, monkeypatch):
    """OCR 可用时，图片走 parse_timetable_image 并返回课程（mock 掉真实 OCR）。"""
    from app.routers import courses

    monkeypatch.setattr(courses.ocr, "ocr_status", lambda: "available")
    fake = {
        "format": "image", "term_start_date": None, "semester": "",
        "entries": [{
            "name": "高等数学", "code": "", "teacher": "张三", "location": "教1-101",
            "slots": [{"weekday": 1, "start": "08:00", "end": "09:40",
                       "weeks": list(range(1, 17)), "location": "教1-101", "teacher": "张三"}],
        }],
    }
    monkeypatch.setattr(courses, "parse_timetable_image", lambda p: fake)
    # check_image_size 需要真实图片，mock 为 no-op
    monkeypatch.setattr(courses, "check_image_size", lambda p: None)
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["format"] == "image"
    assert pv["entries"][0]["name"] == "高等数学"


# ---------------------------------------------------------------------------
# 增强：宽松节次标签匹配 + LLM 智能兜底
# ---------------------------------------------------------------------------

def test_match_label_periods_variants():
    """节次标签列（供网格/OCR 左侧标签检测）：容忍省略「节」、时段前缀、纯数字。"""
    assert _match_label_periods("第1-2节") == [1, 2]
    assert _match_label_periods("1-2节") == [1, 2]
    assert _match_label_periods("第3节") == [3]
    assert _match_label_periods("1") == [1]
    assert _match_label_periods("1,2") == [1, 2]
    assert _match_label_periods("第1、2节") == [1, 2]
    assert _match_label_periods("上午第5-6节") == [5, 6]
    assert _match_label_periods("下午 7-8") == [7, 8]
    # 负例：时间范围 / 周次 / 非节次文本
    assert _match_label_periods("8:00-9:40") is None
    assert _match_label_periods("1-16周") is None
    assert _match_label_periods("1-16") is None        # 范围超 12 且无「节」→ 周次
    assert _match_label_periods("节次") is None
    assert _match_label_periods("星期一") is None
    assert _match_label_periods("课程名") is None


def _llm_mocks(monkeypatch, boxes, llm_response):
    """让 parse_timetable_image 走「OCR 可用 + LLM 可用」的兜底链路。"""
    from app.ingest import ocr
    from app.agent import llm

    monkeypatch.setattr(ocr, "ocr_status", lambda: "available")
    monkeypatch.setattr(ocr, "ocr_boxes", lambda p: boxes)
    monkeypatch.setattr(llm, "llm_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: llm_response)
    return ocr, llm


def test_normalize_llm_courses():
    """LLM 返回 → 标准条目：宽松校验、weekday/weeks 归一、缺时间时段跳过。"""
    from app.timetable_grid import _normalize_llm_courses

    res = _normalize_llm_courses({
        "courses": [
            {"name": "高等数学", "teacher": "张三", "location": "教1-101",
             "slots": [{"weekday": 1, "start": "08:00", "end": "09:40", "weeks": "1-16"}]},
            {"name": "操作系统", "slots": [
                {"weekday": 2, "start_period": 3, "end_period": 4, "weeks": [1, 2, 3]},
                {"weekday": "周三", "start": "10:00", "end": "11:40"},
            ]},
            {"name": "无时间课程", "slots": [{"weekday": 5}]},  # 缺时间 → 时段被跳过
            {"name": 123},  # 非法 name → 跳过
        ]
    })
    assert res is not None and res["ai_assisted"] is True
    names = [e["name"] for e in res["entries"]]
    assert names == ["高等数学", "操作系统"]
    os_ = res["entries"][1]
    assert len(os_["slots"]) == 2
    assert os_["slots"][0]["start"] == "10:00" and os_["slots"][0]["end"] == "11:40"
    assert os_["slots"][0]["weeks"] == [1, 2, 3]
    assert os_["slots"][1]["weekday"] == 3


def test_parse_timetable_image_llm_fallback_when_no_period_labels(monkeypatch):
    """确定性网格解析失败（OCR 漏节次标签）→ LLM 兜底恢复课程。"""
    from app.timetable_grid import parse_timetable_image

    # 去掉节次标签块，复现「识别到星期表头但没解析出课程」
    boxes = [b for b in _mock_timetable_boxes()
             if not (b["text"].startswith("第") and "节" in b["text"])]
    llm_response = {
        "courses": [
            {"name": "高等数学", "teacher": "张三", "location": "教1-101",
             "slots": [{"weekday": 1, "start": "08:00", "end": "09:40",
                        "weeks": list(range(1, 17))}]},
            {"name": "操作系统", "teacher": "周九", "location": "教1-105",
             "slots": [{"weekday": 2, "start": "10:00", "end": "11:40",
                        "weeks": list(range(1, 9))}]},
        ]
    }
    _llm_mocks(monkeypatch, boxes, llm_response)
    res = parse_timetable_image("tmp.png")
    assert res["format"] == "image"
    assert res["ai_assisted"] is True
    names = [e["name"] for e in res["entries"]]
    assert "高等数学" in names and "操作系统" in names
    os_ = next(e for e in res["entries"] if e["name"] == "操作系统")
    assert os_["slots"][0]["start"] == "10:00" and os_["slots"][0]["weeks"] == list(range(1, 9))


def test_parse_timetable_image_deterministic_preferred_without_ai(monkeypatch):
    """确定性路径成功时不触发 LLM，结果不带 ai_assisted。"""
    from app.timetable_grid import parse_timetable_image

    boxes = _mock_timetable_boxes()
    _llm_mocks(monkeypatch, boxes, None)  # 即使 LLM 不可用也不影响确定性路径
    res = parse_timetable_image("tmp.png")
    assert res["format"] == "image"
    assert not res.get("ai_assisted", False)
    assert {e["name"] for e in res["entries"]} == {"高等数学", "大学英语", "操作系统", "大学物理"}


def test_parse_timetable_image_raises_when_both_fail(monkeypatch):
    """确定性失败且 LLM 也失败 → 报可操作的中文错误。"""
    import pytest

    from app.timetable_grid import parse_timetable_image

    boxes = [b for b in _mock_timetable_boxes()
             if not (b["text"].startswith("第") and "节" in b["text"])]
    _llm_mocks(monkeypatch, boxes, None)  # chat_json 返回 None → LLM 失败
    with pytest.raises(ImportParseError, match="AI 辅助识别"):
        parse_timetable_image("tmp.png")


def test_parse_image_llm_falls_back_to_reading_text_when_grid_fails(monkeypatch):
    """网格重建整体失败（如星期表头不足）→ 用阅读顺序文本交给 LLM，仍能恢复。"""
    from app.timetable_grid import parse_timetable_image

    called = {}

    def fake_chat_json(system, user, **kw):
        called["user"] = user
        return {
            "courses": [
                {"name": "大学物理", "teacher": "钱七", "location": "物理楼201",
                 "slots": [{"weekday": 3, "start": "10:00", "end": "11:40",
                            "weeks": list(range(1, 17))}]},
            ]
        }

    from app.agent import llm

    _llm_mocks(monkeypatch, _mock_timetable_boxes(), None)  # 仅设置 ocr / llm_available
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)   # 单独注入可追踪的 chat_json
    monkeypatch.setattr(
        "app.timetable_grid.grid_from_ocr_boxes",
        lambda boxes: (_ for _ in ()).throw(ImportParseError("no weekday headers")),
    )
    res = parse_timetable_image("tmp.png")
    assert res["ai_assisted"] is True
    assert res["entries"][0]["name"] == "大学物理"
    # 应走阅读顺序文本（带坐标）而非网格分列文本
    assert "(" in called["user"] and "OCR" in called["user"]

def test_preview_endpoint_image_ai_assisted_flag(client, monkeypatch):
    """图片走 LLM 兜底时，预览响应携带 ai_assisted=True。"""
    from app.routers import courses

    monkeypatch.setattr(courses.ocr, "ocr_status", lambda: "available")
    fake = {
        "format": "image", "term_start_date": None, "semester": "",
        "ai_assisted": True,
        "entries": [{
            "name": "高等数学", "code": "", "teacher": "张三", "location": "教1-101",
            "slots": [{"weekday": 1, "start": "08:00", "end": "09:40",
                       "weeks": list(range(1, 17)), "location": "教1-101", "teacher": "张三"}],
        }],
    }
    monkeypatch.setattr(courses, "parse_timetable_image", lambda p: fake)
    monkeypatch.setattr(courses, "check_image_size", lambda p: None)
    r = client.post(
        "/api/courses/import/preview",
        files={"file": ("kb.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["format"] == "image" and pv["ai_assisted"] is True
