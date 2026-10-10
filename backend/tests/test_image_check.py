"""图片尺寸校验（解压炸弹防护）：只读 header，不解码像素。"""
import pytest
from fastapi import HTTPException

import helpers
from app.infra.ingest.imagecheck import (
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_SIDE,
    check_image_size,
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_db(client):
    """本文件不发起请求，但全局 clean_db fixture 需要 lifespan 先建表。"""


def check(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content)
    return check_image_size(p)


def test_ok_normal_png(tmp_path):
    assert check(tmp_path, "ok.png", helpers.png_bytes(640, 480)) is None


def test_ok_at_pixel_limit(tmp_path):
    """恰好 5000 万像素（10000×5000）可通过。"""
    assert check(tmp_path, "edge.png", helpers.png_bytes(10000, MAX_IMAGE_PIXELS // 10000)) is None


def test_ok_large_but_allowed(tmp_path):
    """12000×4000 = 4800 万像素，单边与总量都未超限 → 通过。"""
    assert check(tmp_path, "large.png", helpers.png_bytes(12000, 4000)) is None


def test_reject_side_too_large(tmp_path):
    """12001×2000 总像素仅 2400 万，但单边超 12000px → 413。"""
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "wide.png", helpers.png_bytes(MAX_IMAGE_SIDE + 1, 2000))
    assert ei.value.status_code == 413
    assert "单边" in ei.value.detail
    assert str(MAX_IMAGE_SIDE) in ei.value.detail


def test_reject_pixels_too_many(tmp_path):
    """10000×5001 = 5001 万像素，略超总像素上限 → 413。"""
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "big.png", helpers.png_bytes(10000, MAX_IMAGE_PIXELS // 10000 + 1))
    assert ei.value.status_code == 413
    assert "5001 万" in ei.value.detail


def test_reject_png_bomb(tmp_path):
    """20000×20000 PNG 头（57 字节）→ 超过 Pillow 内置炸弹阈值 → 413。"""
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "bomb.png", helpers.png_bytes(20000, 20000))
    assert ei.value.status_code == 413
    assert "40000 万" in ei.value.detail  # 从异常信息解析出的像素数


def test_reject_gif_bomb(tmp_path):
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "bomb.gif", helpers.gif_bytes(20000, 20000))
    assert ei.value.status_code == 413


def test_reject_gif_warning_band(tmp_path):
    """10000×10000（1 亿像素）：Pillow 只告警不报错，必须由自身上限拦截。"""
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "band.gif", helpers.gif_bytes(10000, 10000))
    assert ei.value.status_code == 413
    assert "10000 万" in ei.value.detail


def test_reject_corrupt_image(tmp_path):
    """扩展名是 .png 但不是图片 → 422 明确指引。"""
    with pytest.raises(HTTPException) as ei:
        check(tmp_path, "fake.png", b"definitely not an image")
    assert ei.value.status_code == 422
    assert "图片" in ei.value.detail
