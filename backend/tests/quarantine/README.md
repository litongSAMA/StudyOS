# 隔离用例（quarantine）

这里存放**引用了本仓库中并不存在的模块/符号、会让整轮 pytest 在收集阶段直接中断**的用例文件。
它们不参与默认测试运行（`backend/pytest.ini` 里 `norecursedirs = quarantine`）。

## 为什么要隔离

pytest 在**收集阶段**导入每个 `test_*.py`。其中 3 个文件在**模块顶层**导入不存在的模块，
于是 pytest 报 `Interrupted: 4 errors during collection` 并**放弃整轮测试**——
结果是全套 700 多个用例一个都跑不到，"测试通过" 就成了无法验证的说法。

隔离后 `python -m pytest tests` 才能正常跑完，暴露出的真实失败才是可修的问题。

## 清单与原因

| 文件 | 引用 | 实际情况 |
| --- | --- | --- |
| `test_auth.py` | `app.models` 的 `User` / `AuthSession` / `AuthEvent`；`app.authadmin` 的 `disable_protection` / `list_users` / `reset_password` / `delete_user` | `app.models` 里没有这三个模型类（`auth.py` 用原生 `sqlite3` 自建表）；`app.authadmin` 只定义了 `main()` |
| `test_report.py` | `app.report` 的 `UNSET_SEMESTER` / `build_series` / `week_buckets` | `backend/app/report.py` 在本仓库**从未存在**（`git log -- backend/app/report.py` 为 0 次提交） |
| `test_ssrfguard.py` | `app.ssrfguard` 的 `UnsafeOutboundError` / `check_outbound_url` | `backend/app/ssrfguard.py` 从未存在；同类能力现有实现在 `app/netutil.py`（`is_local_url` / `trust_env_for_url`） |
| `test_timetable_grid.py` | `app.timetable_grid` 的多个符号 | `backend/app/timetable_grid.py` 从未存在；课表网格解析现有实现在 `app/courseimport_sheet.py` 与 `app/ingest/ocrgrid.py` |

## 怎么处理

两条路，都需要**先确认功能到底在哪**，不要在测试里简单地改成"看起来像"的模块名：

1. **功能确实还在**（只是模块改名/被合并）：把用例改成导入现有实现，并逐个跑通；
   注意 `test_auth.py` 还需要先补上用例期望的账号模型，或把用例改写成对
   `app/auth.py` 现有原生 `sqlite3` 实现的断言。
2. **功能确实没有**：这些用例描述的是未实现/已移除的能力，应删除或改写为
   对现有能力的用例，而不是留在仓库里冒充回归覆盖。

**恢复某个文件时**：把它移回 `backend/tests/`，然后 `python -m pytest tests -q` 确认真的通过。

## 相关

- `test_quiz.py`（引用不存在的 `app.quizgen`）**没有**放进这里：它的导入写在
  函数体内部，不会中断收集，因此留在 `tests/` 里作为一条可见的失败记录。
- 本目录的文件是**移动**进来的（`git mv`），历史没有丢失，`git log --follow` 仍可追溯。
