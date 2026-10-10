"""StudyOS 演示种子数据：一键生成真实感课程/资料/DDL/复习计划。

用法（**先启动后端**，再执行本脚本）：
    cd backend
    .venv\\Scripts\\python.exe scripts\\seed.py            # Windows
    .venv/bin/python scripts/seed.py                      # macOS / Linux

常用参数：
    --base http://127.0.0.1:8800/api   后端地址（默认取环境变量 STUDYOS_BASE 或 8800）
    --reset                            先清空已有学习数据再灌演示数据（答辩/评审前重置用）
    --check                            只检查后端是否可达与当前数据量，不写任何数据
    --dry-run                          只打印将要执行的动作，不实际写入

数据说明：**本脚本生成的全部是合成演示数据**（虚构课程/资料/DDL/错题），
用于说明功能与流程，可随时在界面上删除；不包含任何真实用户的学习数据。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import httpx

DEFAULT_BASE = os.environ.get("STUDYOS_BASE") or "http://127.0.0.1:8800/api"
BASE = DEFAULT_BASE
c = httpx.Client(base_url=BASE, timeout=30)
DRY = False


def post(path: str, body: dict, headers: dict | None = None):
    if DRY:
        print(f"  [dry-run] POST {path}")
        return {"id": 0, "created_tasks": [], "created_materials": []}  # 占位：后续引用不会崩
    r = c.post(path, json=body, headers=headers or {})
    if r.status_code >= 400:  # 失败时把后端的中文原因带出来，别只留一行 HTTP 码
        raise httpx.HTTPStatusError(
            f"{r.status_code} {r.text[:300]}", request=r.request, response=r
        )
    return r.json()


def get(path: str, **params):
    r = c.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


def safe_count(path: str, **params) -> int:
    """统计条数：接口不可用时返回 -1 而不是中断（脚本要对半升级的后端也友好）。"""
    try:
        data = get(path, **params)
    except Exception:
        return -1
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return int(data.get("total", len(data)))
    return -1


def _fmt_count(value: int) -> str:
    return "不可用" if value < 0 else str(value)


def delete(path: str):
    if DRY:
        print(f"  [dry-run] DELETE {path}")
        return
    r = c.delete(path)
    if r.status_code not in (204, 404):
        r.raise_for_status()


def _listing(path: str, **params) -> list:
    """取列表：接口不可用或返回非列表时返回空列表（重置流程不因此中断）。"""
    try:
        data = get(path, **params)
    except Exception:
        print(f"  [跳过] {path} 不可用")
        return []
    return data if isinstance(data, list) else []


def reset_all():
    """清空已有学习数据（课程 / 资料 / 任务 / 笔记 / 错题 / 自测 / 清单 / 复习计划 / 专注记录）。

    顺序按引用关系从叶到根，避免外键约束报错；演示数据与真实数据都在同一个本地库里，
    所以这一步是**破坏性**的——只在明确要重置演示环境时使用。
    """
    print("[重置] 清空已有学习数据…")
    for item in _listing("/checklists"):
        delete(f"/checklists/{item['id']}")
    for item in _listing("/quiz"):
        delete(f"/quiz/{item['id']}")
    for item in _listing("/notes"):
        delete(f"/notes/{item['id']}")
    for item in _listing("/wrongbook"):
        delete(f"/wrongbook/{item['id']}")
    for item in _listing("/review"):
        delete(f"/review/{item['id']}")
    for item in _listing("/focus/sessions", limit=500):
        delete(f"/focus/sessions/{item['id']}")
    for done in ("true", "false"):
        for item in _listing("/tasks", done=done):
            delete(f"/tasks/{item['id']}")
    for item in _listing("/materials", limit=500):
        delete(f"/materials/{item['id']}")
    for item in _listing("/courses"):
        delete(f"/courses/{item['id']}")
    print("[重置] 完成")


def seed():
    """灌入演示数据主体（在 --reset 清空之后调用）。"""
    today = datetime.now()

    # 1. 课程（带学期锚点）
    courses = {}
    for name, code, start in [
        ("操作系统", "CS301", "2026-09-01"),
        ("机器学习", "CS401", "2026-09-01"),
        ("数据结构", "CS201", "2026-09-01"),
        ("大学英语", "EN101", "2026-09-01"),
    ]:
        try:
            courses[name] = post("/courses", {
                "name": name, "code": code,
                "semester": "2026-2027-1", "start_date": start,
            })
            print(f"[课程] {name}")
        except Exception:
            courses[name] = next(
                x for x in c.get("/courses").json() if x["name"] == name
            )

    # 2. 资料 + DDL（通过 agent/extract 抽取 → agent/apply 落库，走完整链路）
    def ingest(text, course_hint, key):
        ext = post("/agent/extract", {"text": text, "course_hint": course_hint})
        mats = ext.get("materials", [])
        tasks = ext.get("tasks", [])
        if not tasks and not mats:
            print(f"  [跳过] {text[:30]}...")
            return
        # agent/apply 契约：tasks/materials + source ∈ {text, file, image}，
        # 并要求幂等键（同键同内容只落库一次，重复执行脚本不会灌出重复数据）
        r = post(
            "/agent/apply",
            {"tasks": tasks, "materials": mats, "source": "text"},
            headers={"Idempotency-Key": key},
        )
        print(
            f"[入库] {text[:36]}… → {len(r.get('created_tasks', []))} 任务, "
            f"{len(r.get('created_materials', []))} 资料"
        )

    d = lambda n: (today + timedelta(days=n)).strftime("%m月%d日")
    # 幂等键按「批次序号」生成：同一脚本重复执行（不重置）也只会读回原回执，不重复入库
    ingest(f"操作系统课件：第2章 进程调度与死锁，{d(2)} 课堂上讲", "操作系统", "seed-01")
    ingest(f"操作系统课件：第3章 内存管理 分页与段页式（考试重点，难度较大）", "操作系统", "seed-02")
    ingest(f"操作系统课件：第4章 文件系统（基础入门）", "操作系统", "seed-03")
    ingest(f"操作系统第三次作业：实现银行家算法，提交实验报告，{d(7)} 23:59 截止", "操作系统", "seed-04")
    ingest(f"操作系统期中考试，{d(14)} 上午9点，范围1-6章", "操作系统", "seed-05")
    ingest(f"机器学习课件：第5章 神经网络与反向传播（必考考点），含梯度下降推导", "机器学习", "seed-06")
    ingest(f"机器学习实验二：手写数字识别，{d(10)} 晚上10点前提交实验报告", "机器学习", "seed-07")
    ingest(f"机器学习期末大作业：垃圾邮件分类器，{d(21)} 截止", "机器学习", "seed-08")
    ingest(f"数据结构作业：AVL树旋转，{d(5)} 下午5点前交", "数据结构", "seed-09")
    ingest(f"数据结构课件：第7章 图论与最短路径 Dijkstra 算法", "数据结构", "seed-10")
    ingest(f"英语四六级真题讲解（高频考点），{d(9)} 晚上7点教室3102", "大学英语", "seed-11")
    ingest(f"操作系统复习笔记：第3章 内存管理重点整理（考点）", "操作系统", "seed-12")
    ingest(f"机器学习复习笔记：SVM 与集成学习重点（进阶难点）", "机器学习", "seed-13")

    # 3. 生成复习计划
    exams = [t for t in c.get("/tasks", params={"done": "false"}).json()
             if t["kind"] == "exam"]
    if exams:
        plan = post("/review/generate",
                    {"exam_task_ids": [t["id"] for t in exams], "hours_per_day": 3})
        print(f"[复习计划] {plan['title']}，{len(plan['sessions'])} 个复习块")

    # 4. 间隔重复演示：给几份资料打上不同掌握程度的评分
    mats = c.get("/materials").json()
    if mats:
        by_title = {m["title"]: m["id"] for m in mats}
        ratings = [
            ("操作系统课件：第2章 进程调度与死锁", ["good", "good", "easy"]),
            ("操作系统课件：第3章 内存管理 分页与段页式（考试重点，难度较大）", ["good", "again"]),
            ("操作系统复习笔记：第3章 内存管理重点整理（考点）", ["easy"]),
        ]
        for title, seq in ratings:
            if title not in by_title:
                continue
            mid = by_title[title]
            for r in seq:
                post("/review/rate", {"material_id": mid, "rating": r})
            print(f"[掌握度] {title[:24]}… → {seq[-1]}")
        # 评分后再生成一次：计划体现掌握程度加权与到期复习
        post("/review/generate",
             {"exam_task_ids": [t["id"] for t in exams], "hours_per_day": 3})
        print("[复习计划] 已按掌握程度重新生成（未掌握资料多分配时间）")

    # 5. 错题本演示：归集几道典型错题（章节/难度留空自动规则抽取；幂等——已有则跳过）
    if c.get("/wrongbook/stats").json()["total"] == 0:
        wrongs = [
            {
                "course_id": courses["操作系统"]["id"],
                "title": "银行家算法安全性判断（第3章）",
                "question": "某系统 5 个进程共享 3 类资源，给出当前分配矩阵与需求矩阵，判断系统是否处于安全状态。",
                "my_answer": "安全，因为可用资源足够",
                "answer": "不安全：按银行家算法执行安全性检测找不到安全序列，系统已处于死锁风险状态。",
                "reason": "把「当前够用」当成了「安全」，漏算了各进程的最大需求",
                "source": "2026 期中考试",
                "tags": ["死锁", "银行家算法"],
            },
            {
                "course_id": courses["操作系统"]["id"],
                "title": "分页地址转换计算题",
                "question": "逻辑地址 0x2A5C，页面大小 4KB，页表给出页框号，求物理地址。（较难）",
                "my_answer": "直接拼接页框号与偏移",
                "answer": "物理地址 = 页框号 × 页大小 + 页内偏移；页内偏移 = 逻辑地址 mod 页面大小，共 12 位。",
                "reason": "页内偏移位数算错（4KB = 2^12，不是 2^10）",
                "source": "第3章 课后习题",
                "tags": ["内存管理", "分页"],
            },
            {
                "course_id": courses["数据结构"]["id"],
                "title": "AVL 树插入后失衡旋转",
                "question": "向 AVL 树依次插入 3、2、1 后出现哪种失衡？如何调整？",
                "my_answer": "需要两次旋转",
                "answer": "LL 型左单旋：以 3 为根右旋一次即恢复平衡。",
                "reason": "把 LL 型记成了 LR 型，多旋了一次",
                "source": "作业三",
                "tags": ["AVL树", "旋转"],
            },
            {
                "course_id": courses["机器学习"]["id"],
                "title": "过拟合与正则化辨析",
                "question": "训练误差持续下降而验证误差先降后升，说明什么？L2 正则为何能缓解？",
                "my_answer": "说明模型欠拟合",
                "answer": "典型过拟合：模型记住了训练集噪声。L2 正则通过惩罚大的权重把解收缩到更简单的假设空间。",
                "reason": "把过拟合的「验证误差上升」看成了欠拟合信号",
                "source": "期末模拟卷",
                "tags": ["过拟合", "正则化"],
            },
        ]
        created = [post("/wrongbook", w) for w in wrongs]
        print(f"[错题本] 归集 {len(created)} 道错题")
        # 重练演示：第一道连续答对 2 次 → 已掌握（展示状态流转）
        post(f"/wrongbook/{created[0]['id']}/practice", {"ok": True})
        post(f"/wrongbook/{created[0]['id']}/practice", {"ok": True})
        print("[错题本] 已演示重练：1 道答对两次 → 已掌握，其余待攻克")

    # 6. 全局学期锚点
    if not DRY:
        c.put("/settings", json={"key": "semester_start", "value": "2026-09-01"})
    print("[设置] semester_start=2026-09-01")

    print(f"\n✅ 种子数据完成。打开 {BASE[:-4]} 查看（演示数据全部为合成内容，可随时删除）")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="StudyOS 演示种子数据（全部为合成数据，不含任何真实用户数据）",
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"后端 API 地址（默认 {DEFAULT_BASE}，也可用环境变量 STUDYOS_BASE）",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空已有学习数据再灌演示数据（破坏性，仅用于重置演示环境）",
    )
    parser.add_argument(
        "--check", action="store_true", help="只检查后端可达性与当前数据量，不写入"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的动作，不写入"
    )
    return parser.parse_args(argv)


def main():
    global BASE, c, DRY
    args = parse_args()
    BASE = args.base.rstrip("/")
    c = httpx.Client(base_url=BASE, timeout=30)
    DRY = args.dry_run

    try:
        health = get("/health")
    except Exception as exc:  # 后端没起 / 地址不对：给可执行的下一步，而不是抛栈
        print(f"[错误] 连接不上后端 {BASE}：{exc}")
        print("       请先启动后端（项目根目录运行 start.bat 或 ./start.sh），")
        print("       或用 --base 指定正确地址（例如 --base http://127.0.0.1:8800/api）。")
        return 1

    counts = {
        "课程": safe_count("/courses"),
        "资料": safe_count("/materials", limit=500),
        "任务": safe_count("/tasks"),
        "笔记": safe_count("/notes"),
        "错题": safe_count("/wrongbook"),
        "自测": safe_count("/quiz"),
        "清单": safe_count("/checklists"),
    }
    print(f"[连接] {BASE} → {health.get('status')} · LLM={health.get('llm')} · OCR={health.get('ocr')}")
    print("[现状] " + " · ".join(f"{k} {_fmt_count(v)}" for k, v in counts.items()))

    if args.check:
        print("[检查] 仅检查，未写入任何数据。")
        return 0

    if args.reset:
        reset_all()

    seed()

    print("[复核] 灌入后：" + " · ".join(
        f"{k} {_fmt_count(v)}" for k, v in {
            "课程": safe_count("/courses"),
            "资料": safe_count("/materials", limit=500),
            "任务": safe_count("/tasks"),
            "笔记": safe_count("/notes"),
            "错题": safe_count("/wrongbook"),
        }.items()
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
