"""识图提炼接口（/refine-image）：图片格式校验 / OCR 不可用指引 / 成功路径 / 参数边界。

OCR 由资料抽取链路统一调用（`ingest/files.py` 会先问 `ocr_available()`），
所以「假装 OCR 可用 + 返回固定文本」需要同时替换这两个函数 —— 见 `fake_ocr()`。
"""
import base64

import app.api.routers.notes as notes_router
import helpers

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def fake_ocr(monkeypatch, text):
    """模拟「OCR 可用且识别出 text」（替换可用性判断与识别实现）。"""
    monkeypatch.setattr(notes_router.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(notes_router.ocr, "ocr_image", lambda path: text)


def upload(client, mode="keypoints", name="课堂笔记.png", content=PNG, content_type="image/png"):
    return client.post(
        "/api/notes/refine-image",
        files={"file": (name, content, content_type)},
        data={"mode": mode},
    )


def test_refine_image_rejects_non_image(client):
    r = upload(client, name="notes.txt", content=b"hello", content_type="text/plain")
    assert r.status_code == 422
    assert "图片" in r.json()["detail"]


def test_refine_image_bad_mode(client):
    r = upload(client, mode="weird")
    assert r.status_code == 422
    assert "模式" in r.json()["detail"]


def test_refine_image_ocr_unavailable_guidance(client):
    """测试环境 OCR 关闭 → 明确指引，而不是崩溃。"""
    r = upload(client)
    assert r.status_code == 422
    assert "识别出文字" in r.json()["detail"]


def test_refine_image_ok(client, monkeypatch):
    """OCR 成功 → 返回提炼结果 + OCR 原文（规则引擎离线可用）。"""
    text = (
        "数据库期末重点：事务的ACID特性，原子性、一致性、隔离性、持久性。"
        "考试重点：两阶段锁协议与死锁检测。"
    )
    fake_ocr(monkeypatch, text)
    r = upload(client, mode="exam")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "exam"
    assert body["engine"] == "rule"  # 测试环境强制离线规则
    assert body["source_text"] == text
    assert body["points"], "应提炼出要点"
    assert any("ACID" in p["point"] or "事务" in p["point"] for p in body["points"])
    # 考点模式：含考点信号词的句子应入选
    assert any("两阶段锁" in p["point"] or "死锁" in p["point"] for p in body["points"])


def test_refine_image_title_hint(client, monkeypatch):
    fake_ocr(monkeypatch, "操作系统：进程调度算法包括先来先服务、短作业优先。")
    r = client.post(
        "/api/notes/refine-image",
        files={"file": ("第三章课件.png", PNG, "image/png")},
        data={"mode": "keypoints", "title_hint": "第三章 进程调度"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "第三章 进程调度"


def test_refine_image_filename_title_fallback(client, monkeypatch):
    """未给 title_hint 时用文件名兜底。"""
    fake_ocr(monkeypatch, "计算机网络：TCP三次握手的过程。")
    r = upload(client, name="计网重点.png")
    assert r.status_code == 200
    assert r.json()["title"] == "计网重点.png"


def test_refine_image_too_large(client, monkeypatch):
    monkeypatch.setattr(notes_router, "MAX_IMAGE_BYTES", 8)
    r = upload(client)
    assert r.status_code == 413


def test_refine_image_pixel_bomb(client):
    """文件头声明 20000×20000 的小文件 → 413 拦截，不进入 OCR（防解压炸弹）。"""
    r = upload(client, content=helpers.png_bytes(20000, 20000))
    assert r.status_code == 413
    assert "像素" in r.json()["detail"] or "尺寸" in r.json()["detail"]


def test_refine_image_wide_but_small_pixels(client):
    """单边超 12000px（即使总像素不大）→ 413。"""
    r = upload(client, content=helpers.png_bytes(12001, 2000))
    assert r.status_code == 413
    assert "单边" in r.json()["detail"]


def test_refine_image_large_but_allowed(client, monkeypatch):
    """12000×4000 未超上限 → 正常进入 OCR。"""
    fake_ocr(monkeypatch, "重点：进程调度。")
    r = upload(client, content=helpers.png_bytes(12000, 4000))
    assert r.status_code == 200
    assert "进程调度" in r.json()["source_text"]


def test_refine_image_empty_ocr(client, monkeypatch):
    """OCR 返回空串 → 422 指引（与扫描件 OCR 失败同一口径）。"""
    fake_ocr(monkeypatch, "")
    r = upload(client)
    assert r.status_code == 422
    assert "识别出文字" in r.json()["detail"]
