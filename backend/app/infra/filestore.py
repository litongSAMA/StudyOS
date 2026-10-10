"""附件文件存储的安全访问辅助。

所有落盘文件都放在 `data/files` 目录内。对外部传入的 file_path
（如 /api/agent/apply 的确认回填）必须先通过 is_inside_files 校验，
删除文件时也必须先校验路径——防止任意路径被引用/误删。
"""
from __future__ import annotations

import time
from pathlib import Path

from app.infra.config import settings

# 未确认入库的临时文件保留时长（启动清理时以此判老）
ORPHAN_FILE_MAX_AGE_S = 24 * 3600


def files_root() -> Path:
    root = settings.data_dir / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_inside_files(path: str | None) -> bool:
    """路径是否落在 data/files 目录内部（且不是目录本身）。"""
    if not path:
        return False
    root = files_root().resolve()
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    return p != root and p.is_relative_to(root)


def safe_unlink(path: str | None) -> None:
    """只删除 data/files 内的文件；其余路径一律忽略。"""
    if not is_inside_files(path):
        return
    p = Path(path)
    try:
        if p.exists() and p.is_file():
            p.unlink()
    except OSError:
        pass


def cleanup_orphans(referenced: set[str], max_age_s: float) -> int:
    """清理 files 目录里未被任何资料引用、且超过 max_age_s 的遗留文件。

    场景：上传后用户取消确认 → 临时文件永远留在磁盘。启动时清理一次即可。
    """
    root = files_root()
    now = time.time()
    removed = 0
    for p in root.iterdir():
        try:
            if not p.is_file():
                continue
            if p.resolve() in referenced:
                continue
            if now - p.stat().st_mtime < max_age_s:
                continue
            p.unlink()
            removed += 1
        except OSError:
            continue
    return removed
