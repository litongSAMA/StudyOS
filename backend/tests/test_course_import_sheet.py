"""课表导入：Excel(.xlsx/.xls)/CSV 二维课表解析 + 格式识别与接口预览。

grid 解析的纯函数用例用合成网格；接口用例覆盖 xlsx 文件上传、老式 .xls
（测试用 biff8 现场生成）、CSV、图片分支与「无法识别文件」的明确报错。
"""
from __future__ import annotations

import csv
import io

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.domain.courseimport import ImportParseError
from app.domain.courseimport_sheet import (
    detect_kind,
    is_spreadsheet,
    parse_grid,
    parse_spreadsheet,
    read_grid,
)
from app.infra.models import Course
from tests.biff8 import grid_to_xls

# 典型教务系统二维课表：第 1 行学年学期，第 2 行星期表头，左侧节次/时间标签
GRID = [
    ["2025-2026学年第一学期 学生个人课表", "", "", "", "", "", ""],
    ["节次", "时间", "周一", "周二", "周三", "周四", "周五"],
    ["第1-2节", "08:00-09:40", "", "", "计算机组成原理\n王五\n教3-101\n1-16周", "", ""],
    ["第3-4节", "10:00-11:40", "高等数学\n李四\n教1-202\n1-16周", "", "", "", "大学英语\n1-16周"],
    ["第5-6节", "14:00-15:40", "", "数据结构\n赵六\n实验楼A201\n1-16周(单)", "", "", ""],
    ["备注：以教务系统为准", "", "", "", "", "", ""],
]


def xlsx_bytes(grid) -> bytes:
    """合成 .xlsx（openpyxl 写出，和用户上传的真实文件同一读取路径）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "课表"
    for row in grid:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(grid, delimiter=",") -> bytes:
    """CSV 字节：含换行的单元格必须加引号（教务系统导出的 CSV 也是这么转义的）。"""
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter).writerows(grid)
    return buf.getvalue().encode("utf-8")


def by_name(parsed) -> dict:
    return {e["name"]: e for e in parsed["entries"]}


# ---------------------------------------------------------------------------
# 纯解析器
# ---------------------------------------------------------------------------

def test_parse_grid_multiline_cell_extracts_teacher_location_weeks():
    parsed = parse_grid(GRID, fmt="excel")
    assert parsed["format"] == "excel"
    assert parsed["semester"] == "2025-2026-1"
    entries = by_name(parsed)
    assert set(entries) == {"计算机组成原理", "高等数学", "大学英语", "数据结构"}

    comp = entries["计算机组成原理"]
    assert comp["teacher"] == "王五" and comp["location"] == "教3-101"
    assert comp["slots"] == [{
        "weekday": 3, "start": "08:00", "end": "09:40",
        "weeks": list(range(1, 17)), "location": "教3-101", "teacher": "王五",
    }]

    # 「1-16周(单)」→ 奇数周；带单双周的格子不能被当成 1-16 全周
    assert entries["数据结构"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]
    assert entries["大学英语"]["slots"][0]["weekday"] == 5


def test_parse_grid_ignores_footer_and_title_rows():
    """表头以上、备注行以下都不该变成课程。"""
    names = {e["name"] for e in parse_grid(GRID)["entries"]}
    assert "节次" not in names and "备注" not in names
    assert not any(n.startswith("2025-2026") for n in names)


def test_parse_grid_period_labels_without_clock_column():
    """没有时刻列时用「第N节」查标准 12 节时间表。"""
    grid = [
        ["", "周一", "周二"],
        ["第1-2节", "高等数学", ""],
        ["第3-4节", "", "线性代数"],
    ]
    entries = by_name(parse_grid(grid))
    assert entries["高等数学"]["slots"][0]["start"] == "08:00"
    assert entries["高等数学"]["slots"][0]["end"] == "09:40"
    assert entries["线性代数"]["slots"][0]["weekday"] == 2
    assert entries["线性代数"]["slots"][0]["start"] == "10:00"


def test_parse_grid_single_line_cell_forms():
    """单行写法也要能解析：'高等数学(1-16周)@教3-101'、带【】前缀。"""
    grid = [
        ["", "周一", "周三"],
        ["08:00-09:40", "高等数学(1-16周)@教3-101", "【选修】大学英语 1-16周(双)"],
    ]
    entries = by_name(parse_grid(grid))
    assert entries["高等数学"]["location"] == "教3-101"
    assert entries["高等数学"]["slots"][0]["weeks"] == list(range(1, 17))
    assert entries["大学英语"]["slots"][0]["weeks"] == [2, 4, 6, 8, 10, 12, 14, 16]


def test_parse_grid_rejects_layout_without_weekday_header():
    with pytest.raises(ImportParseError, match="周一"):
        parse_grid([["a", "b"], ["c", "d"]])


def test_parse_grid_rejects_empty_and_header_only():
    with pytest.raises(ImportParseError):
        parse_grid([])
    with pytest.raises(ImportParseError, match="节次"):
        parse_grid([["", "周一", "周二"], ["", "", ""]])


def test_label_column_is_not_treated_as_a_course():
    """左标签列（节次+时间）不得解析出「第3-4节」这种假课程。"""
    grid = [
        ["", "周一", "周二"],
        ["第1-2节 08:00-09:40", "高等数学", ""],
        ["第3-4节 10:00-11:40", "", "线性代数"],
    ]
    names = {e["name"] for e in parse_grid(grid)["entries"]}
    assert names == {"高等数学", "线性代数"}


# ---------------------------------------------------------------------------
# 南理工等教务系统的「大节 + [周] + 职称」版式
# ---------------------------------------------------------------------------

# 真实导出的形状：左右两行表头，行标签是「第一大节…第五大节」（没有时刻列），
# 周次写成「4-6,8-18[周]」，老师带职称括注，一格多门课用空行隔开
MAJOR_GRID = [
    ["南京理工大学 刘星宇 学生个人课表", "", "", "", ""],
    ["学年学期：2026-2027-1        班级：        打印日期：2026-08-12", "", "", "", ""],
    ["", "星期一", "星期二", "星期三", "星期四", "星期五"],
    ["第一大节", "毛泽东思想和中国特色社会主义理论体系概论\n张新义(讲师)\n4-6,8-18[周]\nⅣ-A103",
     "", "工程数学\n华踏红(讲师)\n4-6,8-18[周]\nⅣ-C103",
     "体育（Ⅲ）\n耿瑞楠(讲师)\n4-5,7-19[周]\n二运篮球场",
     "大学物理实验（Ⅱ）\n郝松()\n4,8,10,12,14,16,18[周]\n物理实验中心\n"
     "\n马克思主义基本原理概论\n李红亮(教授)\n4-5,7-18[周]\nⅣ-B303"],
    ["第二大节", "", "", "大学物理（Ⅱ）\n吴海平(副教授),钱彦(副教授)\n4-6,8-16[周]\nI-101",
     "电路综合实验\n蔡小玲(副教授)\n6[周]\n\n\n电路\n徐行健(副教授)\n1-5,7-15[周]\nI-101", "", ""],
    ["第四大节", "电路\n徐行健(副教授)\n1-6,8-15[周]\nI-101", "", "", "", ""],
    ["第五大节", "", "", "", "百年奥斯卡经典电影赏析\n陈捷(教授)\n4-5,7-15[周]\nⅣ-C101"],
    ["备注：电路综合实验 蔡小玲 6周;大学物理实验（Ⅱ） 李慧 4,6,8,10,12,14,16,18周", "", "", "", ""],
]


def slot_weeks(entry, weekday: int, start: str) -> list[int]:
    """取某门课某个时段的周次（同一门课不同时段周次可以不同）。"""
    hit = next((s for s in entry["slots"] if s["weekday"] == weekday and s["start"] == start), None)
    assert hit is not None, f"{entry['name']} 缺少 周{weekday} {start} 的时段"
    return hit["weeks"]


def test_major_period_labels_map_to_standard_periods():
    """「第一大节…第五大节」按标准 12 节时间表换算成时刻，且不解析成课程。"""
    entries = by_name(parse_grid(MAJOR_GRID, fmt="excel"))
    assert entries["毛泽东思想和中国特色社会主义理论体系概论"]["slots"][0]["start"] == "08:00"
    assert entries["毛泽东思想和中国特色社会主义理论体系概论"]["slots"][0]["end"] == "09:40"
    # 第二大节 = 第3-4节；第五大节 = 第9-10节（晚上）
    assert entries["大学物理（Ⅱ）"]["slots"][0]["start"] == "10:00"
    assert entries["百年奥斯卡经典电影赏析"]["slots"][0]["start"] == "19:00"
    assert not any("大节" in name for name in entries)


def test_bracket_week_token_is_parsed():
    """「4-6,8-18[周]」这种方括号写法要完整取出周次（含逗号后的第二段）。"""
    entries = by_name(parse_grid(MAJOR_GRID, fmt="excel"))
    assert slot_weeks(entries["毛泽东思想和中国特色社会主义理论体系概论"], 1, "08:00") == \
        [4, 5, 6, *range(8, 19)]
    # 「6[周]」单周也不能漏
    assert slot_weeks(entries["电路综合实验"], 4, "10:00") == [6]


def test_teacher_title_suffix_is_stripped():
    """「张新义(讲师)」只把姓名入库，职称不跟着走，也不会变成第二门课。"""
    entries = by_name(parse_grid(MAJOR_GRID, fmt="excel"))
    assert "张新义(讲师)" not in entries
    assert entries["毛泽东思想和中国特色社会主义理论体系概论"]["teacher"] == "张新义"
    # 多名教师逐个剥职称
    assert entries["大学物理（Ⅱ）"]["teacher"] == "吴海平、钱彦"
    # 空括注「郝松()」也不能让老师行变成新课
    assert "郝松()" not in entries and "郝松" not in entries
    assert entries["大学物理实验（Ⅱ）"]["teacher"] == "郝松"


def test_roman_numeral_rooms_are_locations():
    """「Ⅳ-A103」「I-101」是教室号，不能当成课程名。"""
    entries = by_name(parse_grid(MAJOR_GRID, fmt="excel"))
    assert entries["毛泽东思想和中国特色社会主义理论体系概论"]["location"] == "Ⅳ-A103"
    assert entries["大学物理（Ⅱ）"]["location"] == "I-101"
    assert "Ⅳ-A103" not in entries and "I-101" not in entries


def test_blank_line_splits_two_courses_in_one_cell():
    """一格塞两门课：空行分隔，各自带自己的老师/地点/周次，元数据不能互相污染。"""
    entries = by_name(parse_grid(MAJOR_GRID, fmt="excel"))
    assert set(entries) == {
        "毛泽东思想和中国特色社会主义理论体系概论", "工程数学", "体育（Ⅲ）",
        "大学物理实验（Ⅱ）", "马克思主义基本原理概论", "大学物理（Ⅱ）",
        "电路综合实验", "电路", "百年奥斯卡经典电影赏析",
    }
    physics = entries["大学物理实验（Ⅱ）"]
    marx = entries["马克思主义基本原理概论"]
    assert physics["location"] == "物理实验中心" and physics["teacher"] == "郝松"
    assert marx["teacher"] == "李红亮" and marx["location"] == "Ⅳ-B303"
    # 同一格里两门课的周次也不同，不能被合并
    assert slot_weeks(physics, 5, "08:00") == [4, 8, 10, 12, 14, 16, 18]
    assert slot_weeks(marx, 5, "08:00") == [4, 5, *range(7, 19)]


def test_compact_semester_and_odd_saturday_column():
    """「学年学期：2026-2027-1」认得出来；周六列也能建课。"""
    parsed = parse_grid(MAJOR_GRID, fmt="excel")
    assert parsed["semester"] == "2026-2027-1"


def test_legacy_xls_with_major_period_layout():
    """整条链路（BIFF8 .xls → 网格 → 解析）跑通南理工版式。"""
    entries = by_name(parse_spreadsheet(grid_to_xls(MAJOR_GRID)))
    assert entries["电路"]["teacher"] == "徐行健"
    # 同一门课跨两个大节，周次各按自己格子里写的算
    assert slot_weeks(entries["电路"], 1, "16:00") == [1, 2, 3, 4, 5, 6, *range(8, 16)]
    assert slot_weeks(entries["电路"], 4, "10:00") == [1, 2, 3, 4, 5, *range(7, 16)]


# ---------------------------------------------------------------------------
# 文件读取（xlsx / xls / csv）
# ---------------------------------------------------------------------------

def test_xlsx_bytes_roundtrip():
    data = xlsx_bytes(GRID)
    assert detect_kind(data) == "xlsx"
    assert is_spreadsheet(data)
    parsed = parse_spreadsheet(data)
    assert parsed["format"] == "excel"
    assert by_name(parsed)["高等数学"]["teacher"] == "李四"


def test_legacy_xls_bytes_roundtrip():
    """老式 .xls（BIFF8/OLE2）走 xlrd 分支——教务系统导出的就是这种文件。"""
    data = grid_to_xls(GRID)
    assert detect_kind(data) == "xls"
    parsed = parse_spreadsheet(data)
    entries = by_name(parsed)
    assert set(entries) == {"计算机组成原理", "高等数学", "大学英语", "数据结构"}
    assert entries["计算机组成原理"]["location"] == "教3-101"
    assert entries["数据结构"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_csv_bytes_roundtrip_and_delimiter_detection():
    parsed = parse_spreadsheet(csv_bytes(GRID))
    assert parsed["format"] == "csv"
    assert by_name(parsed)["大学英语"]["slots"][0]["weekday"] == 5
    # TSV（教务系统导出常见）同样识别
    tsv = parse_spreadsheet(csv_bytes(GRID, delimiter="\t"))
    assert set(by_name(tsv)) == set(by_name(parsed))


def test_csv_gb18030_encoding():
    data = csv_bytes(GRID).decode("utf-8").encode("gb18030")
    assert by_name(parse_spreadsheet(data))["高等数学"]["teacher"] == "李四"


def test_corrupt_excel_gives_actionable_error():
    bad = b"PK\x03\x04" + b"\x00" * 200
    with pytest.raises(ImportParseError, match="另存为|损坏"):
        read_grid(bad)
    with pytest.raises(ImportParseError, match="另存为|损坏"):
        read_grid(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200)


def test_is_spreadsheet_rejects_binary():
    assert not is_spreadsheet(b"%PDF-1.7\x00\x01\x02 binary junk")
    assert is_spreadsheet("周一,周二\n高等数学,".encode("utf-8"))


# ---------------------------------------------------------------------------
# 接口：预览
# ---------------------------------------------------------------------------

def post_file(client, name: str, data: bytes, content_type="application/octet-stream"):
    return client.post(
        "/api/courses/import/preview",
        files={"file": (name, data, content_type)},
    )


def test_preview_xlsx_file(client):
    r = post_file(client, "课表.xlsx", xlsx_bytes(GRID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "excel"
    assert body["semester"] == "2025-2026-1"
    assert body["ai_assisted"] is False
    assert {e["name"] for e in body["entries"]} == {
        "计算机组成原理", "高等数学", "大学英语", "数据结构",
    }
    assert all(e["exists"] is False for e in body["entries"])


def test_preview_marks_existing_course(client):
    client.post("/api/courses", json={"name": "高等数学", "slots": []})
    r = post_file(client, "课表.xlsx", xlsx_bytes(GRID))
    entries = {e["name"]: e for e in r.json()["entries"]}
    assert entries["高等数学"]["exists"] is True
    assert entries["高等数学"]["existing_id"] is not None


def test_preview_legacy_xls_file(client):
    r = post_file(client, "学生个人课表_925106840501.xls", grid_to_xls(GRID))
    assert r.status_code == 200, r.text
    assert {e["name"] for e in r.json()["entries"]} == {
        "计算机组成原理", "高等数学", "大学英语", "数据结构",
    }


def test_preview_csv_file(client):
    r = post_file(client, "课表.csv", csv_bytes(GRID), "text/csv")
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "csv"


def test_preview_unknown_binary_gives_actionable_error(client):
    """以前这里会报「未在 .ics 内容中找到带时间的课程事件」，误导用户。"""
    r = post_file(client, "课表.pdf", b"%PDF-1.7\n\x00\x01\x02 some binary payload")
    assert r.status_code == 422
    assert "无法识别的课表文件" in r.json()["detail"]


def test_preview_ics_text_still_works(client):
    ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        "DTSTART;TZID=Asia/Shanghai:20250901T080000\n"
        "DTEND;TZID=Asia/Shanghai:20250901T094000\n"
        "RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO\nSUMMARY:操作系统\n"
        "END:VEVENT\nEND:VCALENDAR"
    )
    r = client.post("/api/courses/import/preview", json={"text": ics})
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "ics"
    assert r.json()["entries"][0]["name"] == "操作系统"


def test_preview_json_text_still_works(client):
    body = {"text": '{"courses":[{"name":"线性代数","slots":[{"weekday":2,"start":"08:00","end":"09:40","weeks":"1-8"}]}]}'}
    r = client.post("/api/courses/import/preview", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "json"


def test_apply_import_creates_courses_with_slots(client, clean_db):
    preview = post_file(client, "课表.xlsx", xlsx_bytes(GRID)).json()
    r = client.post("/api/courses/import", json={
        "entries": preview["entries"],
        "semester": preview["semester"],
        "start_date": preview["term_start_date"],
    })
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 4
    from app.infra.db import SessionLocal

    with SessionLocal() as db:
        math = db.execute(select(Course).where(Course.name == "高等数学")).scalar_one()
    assert math.teacher == "李四"
    assert math.location == "教1-202"
    assert math.slots[0]["weekday"] == 1
    assert math.semester == "2025-2026-1"


# ---------------------------------------------------------------------------
# 接口：图片课表（OCR 网格 / LLM 兜底，OCR 与 LLM 都用替身，不加载模型）
# ---------------------------------------------------------------------------

def png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


OCR_ROWS = [
    ["", "周一", "周三"],
    ["08:00-09:40", "高等数学\n李四\n教1-202\n1-16周", ""],
    ["10:00-11:40", "", "计算机组成原理\n王五\n教3-101\n1-16周(单)"],
]


def test_preview_image_uses_ocr_grid(client, monkeypatch):
    """截图 → OCR 网格 → 正常解析：format=image 且 ai_assisted=False。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(
        courses_router, "ocr_grid", lambda p: {"rows": OCR_ROWS, "text": "x", "boxes": 12}
    )
    r = post_file(client, "课表.png", png_bytes(), "image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "image" and body["ai_assisted"] is False
    entries = {e["name"]: e for e in body["entries"]}
    assert entries["高等数学"]["teacher"] == "李四"
    assert entries["高等数学"]["slots"][0]["weekday"] == 1
    assert entries["计算机组成原理"]["slots"][0]["weeks"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_preview_image_falls_back_to_llm(client, monkeypatch):
    """网格重建失败（识别出 0 门课）→ 交给 LLM 兜底，预览带 ai_assisted 标记。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(
        courses_router, "ocr_grid",
        lambda p: {"rows": [["看不懂的版式"]], "text": "周一 高等数学 08:00-09:40", "boxes": 3},
    )
    monkeypatch.setattr(courses_router, "llm_extract_source", lambda text: {
        "format": "image", "ai_assisted": True, "term_start_date": None, "semester": "",
        "entries": [{"name": "高等数学", "code": "", "teacher": "", "location": "",
                     "slots": [{"weekday": 1, "start": "08:00", "end": "09:40",
                                "weeks": [], "location": "", "teacher": ""}]}],
    })
    r = post_file(client, "课表.jpg", png_bytes(), "image/jpeg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_assisted"] is True
    assert [e["name"] for e in body["entries"]] == ["高等数学"]


def test_preview_image_without_ocr_engine(client, monkeypatch):
    """OCR 引擎不可用：明确提示可改用其它格式，而不是抛 500。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: False)
    r = post_file(client, "课表.png", png_bytes(), "image/png")
    assert r.status_code == 422
    assert "OCR" in r.json()["detail"]


def test_preview_image_nothing_recognised(client, monkeypatch):
    """OCR 没识别出文字、LLM 也没结果：给出可操作的中文提示。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(courses_router, "ocr_grid", lambda p: {"rows": [], "text": "", "boxes": 0})
    r = post_file(client, "课表.png", png_bytes(), "image/png")
    assert r.status_code == 422
    assert "更清晰" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 追加：新增来源格式在入口层的行为（PDF/HTML/Word/ODS 的逐格式覆盖见
# test_course_import_formats.py）
# ---------------------------------------------------------------------------

def test_detect_kind_dispatches_recognised_non_grid_formats():
    """老式 .doc（OLE2 + WordDocument 流）不能当 Excel 读，要给「另存为 .docx」的提示。"""
    doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32 + "WordDocument".encode("utf-16-le")
    assert detect_kind(doc) == "doc"
    with pytest.raises(ImportParseError, match=".docx"):
        read_grid(doc)


def test_preview_unknown_binary_lists_all_supported_formats(client):
    """认不出的二进制：提示里要把支持的格式列全（含新增的 PDF/网页/Word/ODS）。"""
    r = post_file(client, "课表.dat", b"\x00\x01\x02\x03 binary junk")
    assert r.status_code == 422
    detail = r.json()["detail"]
    for token in ("Excel", "CSV", "PDF", "网页", "Word", "ODS"):
        assert token in detail, detail


def test_preview_gif_screenshot_enters_ocr_channel(client, monkeypatch):
    """GIF 魔数也要进 OCR 通道（图片魔数表新增了 GIF/TIFF/AVIF/HEIC）。"""
    from app.api.routers import courses as courses_router

    monkeypatch.setattr(courses_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(
        courses_router, "ocr_grid", lambda p: {"rows": OCR_ROWS, "text": "x", "boxes": 12}
    )
    r = post_file(client, "课表.gif", b"GIF89a" + b"\x00" * 64)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "image" and body["format_label"] == "课表截图"
    assert {e["name"] for e in body["entries"]} == {"高等数学", "计算机组成原理"}


def test_preview_format_label_covers_existing_sources(client):
    """既有来源也要给出中文格式名（前端徽标用 format_label || format）。"""
    ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        "DTSTART;TZID=Asia/Shanghai:20250901T080000\n"
        "DTEND;TZID=Asia/Shanghai:20250901T094000\n"
        "RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO\nSUMMARY:操作系统\n"
        "END:VEVENT\nEND:VCALENDAR"
    )
    assert client.post("/api/courses/import/preview", json={"text": ics}).json()["format_label"] \
        == "iCal 日历课表"
    assert post_file(client, "课表.xlsx", xlsx_bytes(GRID)).json()["format_label"] == "Excel 课表"
    assert post_file(client, "课表.csv", csv_bytes(GRID), "text/csv").json()["format_label"] \
        == "CSV 课表"
