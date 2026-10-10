import json
import re

from .common import AgentError, normalize, text
from .deadlines import CHANGE_MARKER, preview_deadline


def context(service, query="", course=None):
    query = text(query, "检索内容", 1000)
    if course is not None:
        course = text(course, "课程范围", 120)
    with service.db.snapshot() as con:
        course_id = None
        if course:
            matches = [row["id"] for row in con.execute("SELECT id,name FROM courses") if normalize(row["name"]) == normalize(course)]
            if not matches:
                raise AgentError(404, "课程不存在")
            course_id = matches[0]
        clauses = " WHERE course_id=?" if course_id else ""
        values = (course_id,) if course_id else ()
        rows = [dict(row) for row in con.execute("SELECT * FROM materials" + clauses + " ORDER BY id DESC", values)]
        terms = [normalize(term) for term in re.split(r"\s+", query) if term.strip()]
        ranked = []
        for row in rows:
            searchable = normalize(" ".join([row["title"], row["chapter"], row["text"], row["tags"]]))
            score = sum(searchable.count(term) for term in terms)
            if not terms or score:
                preview = row["text"]
                match_at = min((normalize(preview).find(term) for term in terms if term in normalize(preview)), default=0)
                start = max(0, match_at - 60)
                ranked.append((score, {"id": row["id"], "title": row["title"], "chapter": row["chapter"], "tags": json.loads(row["tags"]), "snippet": preview[start:start + 240], "href": "/materials"}))
        ranked.sort(key=lambda pair: (-pair[0], -pair[1]["id"]))
        retrieval = service.search(query, course_id) if query and service.search else None
        task_rows = con.execute("SELECT id FROM tasks WHERE done=0" + (" AND course_id=?" if course_id else "") + " ORDER BY due_at,id LIMIT 20", values).fetchall()
        tasks = [service.db.public(con, "tasks", row["id"]) for row in task_rows]
        states = [dict(row) for row in con.execute("SELECT material_reviews.*,materials.title,materials.course_id FROM material_reviews JOIN materials ON materials.id=material_reviews.material_id" + (" WHERE materials.course_id=?" if course_id else "") + " ORDER BY material_reviews.due_at,material_reviews.id", values)]
        due_reviews = [{"material_id": row["material_id"], "title": row["title"], "mastery": row["mastery"], "due_at": service._stored_time(row["due_at"])} for row in states if row["due_at"] and service._stored_time(row["due_at"]) <= service.now()][:20]
        focus = [dict(row) for row in con.execute("SELECT * FROM focus_sessions" + clauses + " ORDER BY started_at DESC,id DESC LIMIT 20", values)]
        plans = [service.db.public(con, "review_plans", row["id"]) for row in con.execute("SELECT id FROM review_plans ORDER BY id DESC LIMIT 10")]
        if course_id:
            plans = [plan for plan in plans if any(session.get("course_id") == course_id or normalize(session.get("course")) == normalize(course) for session in plan["sessions"])]
        return {"materials": retrieval["materials"] if retrieval else [row for _, row in ranked[:8]], "materials_total": retrieval["total"] if retrieval else len(ranked), "retrieval_engine": retrieval["engine"] if retrieval else "lexical", "tasks": tasks, "due_reviews": due_reviews, "focus": {"recent_minutes": sum(row["duration_min"] for row in focus if row["mode"] == "focus"), "recent_sessions": len(focus)}, "review_plans": plans}


def chat(service, message, *, course=None, task_id=None, plan_id=None, new_due_at=None, hours_per_day=3, scope="local"):
    message = text(message, "消息", 10000, True)
    if course is not None:
        course = text(course, "课程范围", 120)
    if CHANGE_MARKER.search(message):
        proposal = preview_deadline(service, message, task_id=task_id, new_due_at=new_due_at, course=course, scope=scope)
        return {"engine": "rule", "intent": "deadline_change", "reply": proposal.get("summary", proposal.get("message")), "proposal": proposal, "tools": ["tasks.lookup", "tasks.deadline.preview"], "requires_confirmation": not proposal.get("needs_selection", False)}
    query_match = re.search(r"(?:搜索|查找|找一下|找|关于)\s*(.+?)(?:的资料|资料|课件|讲义|$)", message)
    query = query_match[1].strip() if query_match else ""
    with service.db.snapshot() as con:
        if not course:
            matches = [row["name"] for row in con.execute("SELECT name FROM courses") if normalize(row["name"]) in normalize(message)]
            if len(matches) == 1:
                course = matches[0]
    knowledge = context(service, query, course)
    if re.search(r"重排|重新安排|调整复习|没完成|赶不上", message):
        from .review import preview_replan
        if plan_id is None:
            return {"engine": "rule", "intent": "review_replan", "reply": "请选择需要调整的复习计划", "context": knowledge, "candidates": [{"id": row["id"], "title": row["title"]} for row in knowledge["review_plans"]], "requires_confirmation": False, "tools": ["review.lookup", "focus.lookup"]}
        proposal = preview_replan(service, plan_id, hours_per_day=hours_per_day, scope=scope)
        return {"engine": "rule", "intent": "review_replan", "reply": proposal["summary"], "proposal": proposal, "context": knowledge, "requires_confirmation": True, "tools": ["review.lookup", "focus.lookup", "review.replan.preview"]}
    if re.search(r"安排.*复习|生成.*复习|制定.*复习|复习计划", message):
        from .review import preview_generate
        exams = [task for task in knowledge["tasks"] if task["kind"] in {"exam", "report"} or any(word in task["title"] for word in ["考试", "答辩", "期末", "期中"])]
        if task_id is not None:
            exams = [task for task in exams if task["id"] == task_id]
        if not exams:
            return {"engine": "rule", "intent": "review_generate", "reply": "请先添加并绑定课程的考试任务", "context": knowledge, "tools": ["tasks.lookup", "review.lookup"], "requires_confirmation": False}
        proposal = preview_generate(service, [task["id"] for task in exams], hours_per_day=hours_per_day, today_only=bool(re.search(r"今天|今晚", message)), scope=scope)
        return {"engine": "rule", "intent": "review_generate", "reply": proposal["summary"], "proposal": proposal, "context": knowledge, "requires_confirmation": True, "tools": ["materials.search", "tasks.lookup", "review.generate.preview", "focus.lookup"]}
    tools = ["materials.search", "tasks.lookup", "review.lookup", "focus.lookup"]
    reply = f"找到 {knowledge['materials_total']} 份相关资料、{len(knowledge['tasks'])} 条未完成任务，{len(knowledge['due_reviews'])} 份资料到期需复习。最近专注累计 {knowledge['focus']['recent_minutes']:g} 分钟。"
    if knowledge["tasks"]:
        reply += f"优先核对最近截止的「{knowledge['tasks'][0]['title']}」。"
    focus_target = knowledge["tasks"][0] if knowledge["tasks"] else None
    focus_link = f"/focus?task={focus_target['id']}" if focus_target else "/focus"
    links = [{"label": "查看资料", "href": "/materials"}, {"label": "查看任务", "href": "/tasks"}, {"label": "查看复习", "href": "/review"}, {"label": "开始专注", "href": focus_link}]
    return {"engine": "rule", "intent": "learning_context", "reply": reply, "context": knowledge, "tools": tools, "links": links, "requires_confirmation": False}
