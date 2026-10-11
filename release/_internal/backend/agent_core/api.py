"""WSGI adapter. The host supplies authorization; request bodies cannot select a scope."""
from http import HTTPStatus
from urllib.parse import parse_qs
import json
import re

from .assistant import chat, context
from .common import AgentError, canonical
from .courses import preview_import
from .deadlines import preview_deadline
from .review import preview_generate, preview_replan


class AgentAPI:
    def __init__(self, service, *, authorize):
        if not callable(authorize):
            raise TypeError("an explicit host authorization callback is required")
        self.service, self.authorize = service, authorize

    @staticmethod
    def public_plan(proposal):
        if not isinstance(proposal, dict):
            return proposal
        return {key: value for key, value in proposal.items() if key not in {"checks", "dependencies", "actions"}}

    def dispatch(self, method, path, data, query, scope, key):
        service = self.service
        if path == "/api/agent/capabilities" and method == "GET":
            return {"version": 1, "idempotency": True, "deduplication": True, "deadline_change": True, "chat": True, "undo": True, "review_replan": True, "course_import": True}
        if path == "/api/agent/apply" and method == "POST":
            return service.apply(data, request_key=key, scope=scope)
        if path == "/api/agent/operations" and method == "GET":
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                raise AgentError(422, "limit 格式无效") from None
            return service.operations(scope=scope, limit=limit)
        receipt = re.fullmatch(r"/api/agent/receipts/([A-Za-z0-9_.:-]{1,128})", path)
        if receipt and method == "GET":
            with service.db.snapshot() as con:
                row = con.execute("SELECT response,undone_at FROM agent_operations WHERE scope=? AND request_key=?", (scope, receipt[1])).fetchone()
                if not row:
                    remote=con.execute("SELECT response FROM agent_remote_receipts WHERE scope=? AND request_key=?",(scope,receipt[1])).fetchone()
                    if remote:
                        from .receipts import materialize
                        return materialize(service.db,con,remote["response"])
                    raise AgentError(404, "尚未找到已提交回执，可以使用原幂等键重试")
                return {**json.loads(row["response"]), "replayed": True, "undone_at": row["undone_at"]}
        if path == "/api/agent/context" and method == "GET":
            return context(service, query.get("query", [""])[0], query.get("course", [None])[0])
        if path == "/api/agent/deadline/preview" and method == "POST":
            return self.public_plan(preview_deadline(service, data.get("message"), task_id=data.get("task_id"), new_due_at=data.get("new_due_at"), course=data.get("course"), scope=scope))
        if path == "/api/agent/review/replan" and method == "POST":
            return self.public_plan(preview_replan(service, data.get("plan_id"), hours_per_day=data.get("hours_per_day", 3), scope=scope))
        if path == "/api/agent/review/generate" and method == "POST":
            return self.public_plan(preview_generate(service, data.get("exam_task_ids"), hours_per_day=data.get("hours_per_day", 3), today_only=data.get("today_only", False), scope=scope))
        if path == "/api/agent/courses/preview" and method == "POST":
            # 只接受 entries / term_start_date / semester；scope 由宿主授权回调给出，
            # 请求体里的 scope、表名等字段一律不读取。
            return self.public_plan(preview_import(service, data.get("entries"), term_start_date=data.get("term_start_date"), semester=data.get("semester", ""), scope=scope))
        if path == "/api/agent/chat" and method == "POST":
            if data.get("conversation_id") or data.get("turn_id"):
                from .conversations import conversation_chat
                response = conversation_chat(service, data, scope)
            else:
                response = chat(service, data.get("message"), course=data.get("course"), task_id=data.get("task_id"), plan_id=data.get("plan_id"), new_due_at=data.get("new_due_at"), hours_per_day=data.get("hours_per_day", 3), scope=scope)
            if "proposal" in response:
                response["proposal"] = self.public_plan(response["proposal"])
            return response
        if path == "/api/agent/conversations" and method == "GET":
            with service.db.snapshot() as con:
                return [dict(row) for row in con.execute("SELECT id,revision,updated_at FROM agent_conversations WHERE scope=? ORDER BY updated_at DESC LIMIT 20", (scope,))]
        conversation = re.fullmatch(r"/api/agent/conversations/([a-f0-9-]{36})", path)
        if conversation and method == "GET":
            from .conversations import get_conversation
            return get_conversation(service, conversation[1], scope)
        execute = re.fullmatch(r"/api/agent/plans/([a-f0-9-]{36})/apply", path)
        if execute and method == "POST":
            return service.execute_plan(execute[1], request_key=key, scope=scope)
        undo = re.fullmatch(r"/api/agent/operations/([a-f0-9-]{36})/undo", path)
        if undo and method == "POST":
            return service.undo(undo[1], request_key=key, scope=scope)
        raise AgentError(404, "Agent 接口不存在")

    def __call__(self, environ, start_response):
        try:
            scope = self.authorize(environ)
            if not scope:
                raise AgentError(401, "需要登录或提供核心服务令牌")
            scope = self.service.scope(scope)
            method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "")
            data = {}
            if method == "POST":
                if "application/json" not in environ.get("CONTENT_TYPE", ""):
                    raise AgentError(415, "请求需要使用 JSON 格式")
                try:
                    length = int(environ.get("CONTENT_LENGTH", "0"))
                except ValueError:
                    raise AgentError(422, "请求长度无效") from None
                if not 0 <= length <= 2 * 1024 * 1024:
                    raise AgentError(413, "请求超过 2 MB")
                try:
                    data = json.loads(environ["wsgi.input"].read(length), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                    if not isinstance(data, dict):
                        raise ValueError("object required")
                except (ValueError, UnicodeDecodeError):
                    raise AgentError(422, "JSON 请求格式无效") from None
            result = self.dispatch(method, path, data, parse_qs(environ.get("QUERY_STRING", "")), scope, environ.get("HTTP_IDEMPOTENCY_KEY"))
            status, body = 200, canonical(result).encode()
        except AgentError as error:
            status, body = error.status, canonical({"detail": error.detail}).encode()
        except Exception:
            # Do not disclose database paths, SQL, credentials, or source text in API errors.
            status, body = 500, canonical({"detail": "Agent 操作失败，事务已回滚；请使用原幂等键查询或重试"}).encode()
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]
        if status == 401:
            headers.append(("WWW-Authenticate", "Bearer"))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [body]
