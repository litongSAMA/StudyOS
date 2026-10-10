"""向后兼容入口：`python -m app.authadmin ...`

真正的实现在 `app/api/authadmin.py`（api 层）。
保留本文件的原因：`python -m app.authadmin` 是**对外承诺的命令行接口**，
已被 README / 合规说明 / 界面文案（web/dist、mobile/dist 已构建产物）引用；
前端产物随包交付、不重新构建，所以这个路径不能改。

等价写法：`python -m app.api.authadmin ...`
"""
from __future__ import annotations

from app.api.authadmin import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover - 命令行入口
    main()
