"""测试辅助函数：与 app/agent/timeparse.py 相同规则计算期望时间。

测试不硬编码具体日期，而是以「运行当天」为基准推算，
保证任何一天跑测试都成立（与后端 now()/today() 逻辑一一对应）。

期望值一律是**本地 naive**；API 返回带 Z 的 UTC ISO，
比较时用 `expect_utc()` 把本地期望换算成 aware UTC。
"""
from __future__ import annotations

import struct
import zlib
from datetime import date, datetime, time, timedelta, timezone


def expect_utc(dt: datetime) -> datetime:
    """本地 naive 期望值 → aware UTC（与 API 返回的 Z 后缀 ISO 对齐）。"""
    return dt.astimezone(timezone.utc)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def expect_abs(month: int, day: int, t: time | None = None,
               year: int | None = None) -> datetime:
    """绝对日期（缺少年份时：过去超 180 天自动 +1 年，与后端一致）。"""
    today = date.today()
    if year is None:
        year = today.year
        if date(year, month, day) < today - timedelta(days=180):
            year += 1
    return datetime.combine(date(year, month, day), t or time(23, 59))


def expect_next_weekday(wd: int, week_offset: int = 0,
                        t: time | None = None) -> datetime:
    """周几表达。wd: 0=周一 … 6=周日；week_offset: 0=本周, 1=下周, 2=下下周。"""
    d = monday_of(date.today()) + timedelta(weeks=week_offset, days=wd)
    return datetime.combine(d, t or time(23, 59))


def expect_n_days(n: int, t: time | None = None) -> datetime:
    """今天/N天后（无时刻默认 23:59 当天结束）。"""
    return datetime.combine(date.today() + timedelta(days=n), t or time(23, 59))


def expect_semester_week(semester_start: str, week: int, wd: int,
                         t: time | None = None) -> datetime:
    """第N周周X：以学期起始日所在周的周一为第 1 周。"""
    d = monday_of(date.fromisoformat(semester_start)) + timedelta(
        weeks=week - 1, days=wd
    )
    return datetime.combine(d, t or time(23, 59))


def png_bytes(width: int, height: int) -> bytes:
    """构造「文件头声明指定宽高、但几乎无像素数据」的 PNG（仅几十字节）。

    用于解压炸弹防护测试：磁盘体积极小，header 却声明超大尺寸，
    不经校验直接交给 OCR 会解出 GB 级内存。Pillow 只读 header 即可取到 size。
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"")
        + chunk(b"IEND", b"")
    )


def gif_bytes(width: int, height: int) -> bytes:
    """构造声明指定宽高的最小 GIF（header + 逻辑屏幕描述 + 空图像数据）。"""
    out = b"GIF89a" + struct.pack("<HHBBB", width, height, 0x00, 0, 0)
    out += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0x00)
    out += b"\x02\x02\x44\x01\x00\x3b"
    return out
