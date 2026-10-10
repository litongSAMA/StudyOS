"""测试用临时目录助手。

背景（真实的 Windows 环境问题，不是"洁癖"）：
`tempfile.TemporaryDirectory()` 用 `mkdtemp` 建目录，权限为 0o700。在部分
Windows 环境（受限账户 / 由宿主进程收紧 ACL 的临时目录 / 沙箱）下会出现
「刚建好的目录里无法创建文件」或「删不掉刚写入的文件」，表现为：

    sqlite3.OperationalError: unable to open database file
    PermissionError: [WinError 5] 拒绝访问: ...\\tmpXXXXXXXX\\test.sqlite

于是 agent 侧用临时库的用例会成片误报失败。这里统一用 `makedirs` 建目录
（权限宽松、Windows 上行为与普通目录一致），并支持 `STUDYOS_TEST_TMP`
把临时库根目录固定到指定位置（CI / 受限环境 / 想把临时数据留在构建目录时）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path


def test_tmp_root() -> Path:
    """临时目录根：`STUDYOS_TEST_TMP` 指定则用它，否则用系统临时目录。"""
    override = os.environ.get("STUDYOS_TEST_TMP", "").strip()
    root = Path(override) if override else Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return root


class TempDir:
    """替代 `tempfile.TemporaryDirectory` 的临时目录（makedirs 建目录，可显式清理）。

    用法与 `TemporaryDirectory` 一致：`with TempDir() as d:` 或
    `t = TempDir(); ...; t.cleanup()`，`t.name` / `str(t)` 都是目录路径。
    """

    def __init__(self, prefix: str = "studyos-"):
        self._path = test_tmp_root() / f"{prefix}{uuid.uuid4().hex[:12]}"
        self._path.mkdir(mode=0o755, parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return str(self._path)

    def __str__(self) -> str:  # pragma: no cover - 便捷用法
        return str(self._path)

    def __fspath__(self) -> str:  # 支持 Path(t) / open(t / "x")
        return str(self._path)

    def cleanup(self) -> None:
        shutil.rmtree(self._path, ignore_errors=True)

    def __enter__(self) -> "TempDir":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()
