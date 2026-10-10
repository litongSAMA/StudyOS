from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3

from .common import AgentError, canonical


from .conversations import SCHEMA as CONVERSATION_SCHEMA
from .receipts import SCHEMA as RECEIPT_SCHEMA

SIDE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_operations (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, kind TEXT NOT NULL, changes TEXT NOT NULL,
 response TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT, guards TEXT NOT NULL DEFAULT '[]',
 UNIQUE(scope, request_key)
);
CREATE TABLE IF NOT EXISTS agent_plans (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, intent TEXT NOT NULL,
 proposal TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
 operation_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_agent_operations_scope ON agent_operations(scope, created_at);
"""
ENTITIES = {"courses", "tasks", "materials", "review_plans", "push_deliveries"}
JSON_FIELDS = {"tags", "slots", "sessions", "exam_task_ids", "remind_offsets_h", "history", "material_ids"}


class Database:
    @staticmethod
    def entity_name(table):
        return table

    def __init__(self, path, *, bootstrap=False):
        self.path = str(Path(path).resolve())
        if not bootstrap and not Path(self.path).is_file():
            raise AgentError(503, "学习数据库不存在；请指定原数据库，或显式初始化独立测试库")
        if bootstrap:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            if bootstrap:
                con.executescript(Path(__file__).with_name("schema.sql").read_text())
            missing = {"courses", "tasks", "materials", "review_plans", "material_reviews", "focus_sessions", "push_deliveries", "tombstones"} - {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if missing:
                raise AgentError(503, "数据库缺少核心表：" + ", ".join(sorted(missing)))
            con.executescript(SIDE_SCHEMA + CONVERSATION_SCHEMA + RECEIPT_SCHEMA)
            if "guards" not in {row[1] for row in con.execute("PRAGMA table_info(agent_operations)")}:
                con.execute("ALTER TABLE agent_operations ADD COLUMN guards TEXT NOT NULL DEFAULT '[]'")
                con.commit()
        con.close()

    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @contextmanager
    def snapshot(self):
        con = self.connect()
        try:
            con.execute("BEGIN")
            yield con
        finally:
            con.rollback()
            con.close()

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def row(con, table, row_id):
        if table not in ENTITIES:
            raise ValueError("unsupported entity")
        found = con.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(found) if found else None

    @staticmethod
    def write(con, table, values, row_id=None):
        if table not in ENTITIES:
            raise ValueError("unsupported entity")
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if not set(values) <= columns:
            raise ValueError("unknown column")
        if row_id is None:
            keys = list(values)
            cursor = con.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", [values[key] for key in keys])
            return cursor.lastrowid
        fields = {key: value for key, value in values.items() if key != "id"}
        con.execute(f"UPDATE {table} SET {','.join(key+'=?' for key in fields)} WHERE id=?", [*fields.values(), row_id])
        return row_id

    @staticmethod
    def public(con, table, row_id):
        row = Database.row(con, table, row_id)
        if row is None:
            raise AgentError(404, "对象不存在")
        for key in JSON_FIELDS & row.keys():
            row[key] = json.loads(row[key])
        for key in {"done", "exam_focus", "completed"} & row.keys():
            row[key] = bool(row[key])
        if "course_id" in row:
            course = con.execute("SELECT name FROM courses WHERE id=?", (row["course_id"],)).fetchone()
            row["course_name"] = course[0] if course else None
        return row

    @staticmethod
    def change(table, before, after):
        return {"table": table, "id": (after or before)["id"], "before": before, "after": after}

    @staticmethod
    def operation(con, operation_id, scope, key, request_hash, kind, changes, response, now, guards=None):
        response = {**response, "operation_id": operation_id, "replayed": False}
        con.execute("INSERT INTO agent_operations (id,scope,request_key,request_hash,kind,changes,response,created_at,guards) VALUES (?,?,?,?,?,?,?,?,?)", (operation_id, scope, key, request_hash, kind, canonical(changes), canonical(response), now, canonical(guards or [])))
        return response

    def replay(self, con, scope, key, request_hash):
        record = con.execute("SELECT * FROM agent_operations WHERE scope=? AND request_key=?", (scope, key)).fetchone()
        if record is None:
            remote = con.execute("SELECT * FROM agent_remote_receipts WHERE scope=? AND request_key=?", (scope,key)).fetchone()
            if not remote:
                return None
            if remote["request_hash"] != request_hash:
                raise AgentError(409, "相同幂等键不能用于不同内容")
            from .receipts import materialize
            return materialize(self,con,remote["response"])
        if record["request_hash"] != request_hash:
            raise AgentError(409, "相同幂等键不能用于不同内容")
        if record["undone_at"]:
            raise AgentError(409, "该操作已撤销；如需重新执行请创建新的请求")
        return {**json.loads(record["response"]), "replayed": True}
