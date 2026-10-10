"""SQLite + SQLAlchemy 会话与 FTS5 全文索引管理。"""
import uuid
import sqlite3
from datetime import date, datetime
sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
sqlite3.register_adapter(date, lambda value: value.isoformat())
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.infra.config import settings
from app.infra.timeutil import legacy_naive_to_utc, utcnow


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


# ---------------------------------------------------------------------------
# 多端同步钩子：任何会话写入/删除时自动补 sync_uuid / updated_at / 墓碑。
# 同步导入过程会设置 session.info["syncing"]=True 跳过此钩子（保留远端时间戳）。
# ---------------------------------------------------------------------------

SYNCABLE_TABLES = (
    "courses", "materials", "tasks", "review_plans", "notes", "material_reviews",
    "wrong_questions", "focus_sessions", "quizzes",
)


def _sync_entity_name(obj) -> str:
    return "review" if obj.__class__.__name__ == "ReviewPlan" else obj.__class__.__name__.lower()


@event.listens_for(Session, "before_flush")
def _sync_track_hook(session, _flush_context, _instances):
    if session.info.get("syncing"):
        return
    now_dt = utcnow()
    for obj in session.new:
        if obj.__class__.__tablename__ in SYNCABLE_TABLES:
            if not getattr(obj, "sync_uuid", ""):
                obj.sync_uuid = uuid.uuid4().hex
            obj.updated_at = now_dt
        elif obj.__class__.__tablename__ == "app_settings":
            obj.updated_at = now_dt
    from sqlalchemy import inspect
    from app.infra.models import PushDelivery
    for obj in list(session.deleted)+list(session.dirty):
        if obj.__class__.__tablename__ == 'tasks' and (obj in session.deleted or any(inspect(obj).attrs[key].history.has_changes() for key in ['due_at','remind_offsets_h','done'])):
            session.execute(PushDelivery.__table__.delete().where(PushDelivery.task_id==obj.id))
    for obj in session.dirty:
        if obj.__class__.__tablename__ in SYNCABLE_TABLES:
            if not getattr(obj, "sync_uuid", ""):
                obj.sync_uuid = uuid.uuid4().hex
            obj.updated_at = now_dt
        elif obj.__class__.__tablename__ == "app_settings":
            obj.updated_at = now_dt
    for obj in session.deleted:
        if obj.__class__.__tablename__ in SYNCABLE_TABLES:
            from app.infra.models import Tombstone

            session.add(Tombstone(
                sync_uuid=getattr(obj, "sync_uuid", "") or uuid.uuid4().hex,
                entity=_sync_entity_name(obj),
                deleted_at=now_dt,
            ))
        elif obj.__class__.__tablename__ == "app_settings":
            from app.infra.models import Tombstone

            session.add(Tombstone(
                sync_uuid=obj.key,
                entity="setting",
                deleted_at=now_dt,
            ))


def _table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _migrate_sync_columns() -> None:
    """轻量迁移：旧库补 sync_uuid / updated_at 列并回填（幂等，自管事务）。"""
    from app.infra.models import AppSetting, Course, Material, ReviewPlan, Task, FocusSession

    with engine.begin() as conn:
        for table, cls in (
            ("courses", Course), ("materials", Material),
            ("tasks", Task), ("review_plans", ReviewPlan), ("focus_sessions", FocusSession),
        ):
            cols = _table_columns(conn, table)
            if not cols:
                continue  # 表尚未创建
            if "sync_uuid" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN sync_uuid VARCHAR(36) NOT NULL DEFAULT ''"))
            if "updated_at" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME"))
        cols = _table_columns(conn, "app_settings")
        if cols and "updated_at" not in cols:
            conn.execute(text("ALTER TABLE app_settings ADD COLUMN updated_at DATETIME"))

    with SessionLocal() as db:
        changed = False
        for cls in (Course, Material, Task, ReviewPlan, FocusSession):
            for row in db.execute(select(cls)).scalars().all():
                if not row.sync_uuid:
                    row.sync_uuid = uuid.uuid4().hex
                    changed = True
                if row.updated_at is None:
                    row.updated_at = row.created_at or utcnow()
                    changed = True
        for row in db.execute(select(AppSetting)).scalars().all():
            if row.updated_at is None:
                row.updated_at = utcnow()
                changed = True
        if changed:
            db.commit()
    # 唯一索引（回填后不会冲突）
    with engine.begin() as conn:
        for table in SYNCABLE_TABLES:
            if "sync_uuid" in _table_columns(conn, table):
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_sync_uuid ON {table}(sync_uuid)"
                ))


def _migrate_export_records() -> None:
    """轻量迁移：旧库补增量导出所需列，并规范化历史 kind（幂等，自管事务）。"""
    with engine.begin() as conn:
        cols = _table_columns(conn, "export_records")
        if not cols:
            return  # 表尚未创建（create_all 会按新结构建表）
        for name, ddl in (
            ("since_at", "DATETIME"),
            ("until_at", "DATETIME"),
            ("item_count", "INTEGER NOT NULL DEFAULT 0"),
            ("incremental", "BOOLEAN NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                conn.execute(text(f"ALTER TABLE export_records ADD COLUMN {name} {ddl}"))
        # 旧版把 kind 写成文件名前缀（studyos-tasks）→ 规范化为 tasks/materials/review
        conn.execute(text(
            "UPDATE export_records SET kind = REPLACE(kind, 'studyos-', '') "
            "WHERE kind LIKE 'studyos-%'"
        ))
        # 旧记录无时间窗口 → until_at 回填为 created_at，可直接作为增量基线
        conn.execute(text(
            "UPDATE export_records SET until_at = created_at WHERE until_at IS NULL"
        ))


# 资料归档字段（章节/难度/考点）一次性回填标记：旧资料自动补抽一次，
# 之后用户手动改过/清空的字段不再被规则引擎覆盖
MATERIAL_ARCHIVE_BACKFILL_MARKER = "material_archive_backfill_v1"


def _migrate_material_fields() -> None:
    """轻量迁移：旧库资料表补章节/难度/考试重点列及索引（幂等，自管事务）。

    列补齐后做**一次性**回填：对旧资料按「标题 + 正文」跑规则抽取，
    补抽章节号/难度/考试重点（仅当字段为空时填充），让升级前的归档资料
    立刻可用；标记写入 app_settings，重启不会重复回填、也不会覆盖用户
    之后的手动修改/清空。
    """
    with engine.begin() as conn:
        cols = _table_columns(conn, "materials")
        if not cols:
            return  # 表尚未创建（create_all 会按新结构建表）
        for name, ddl in (
            ("file_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("chapter", "VARCHAR(120) NOT NULL DEFAULT ''"),
            ("difficulty", "VARCHAR(20) NOT NULL DEFAULT ''"),
            ("exam_focus", "BOOLEAN NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                conn.execute(text(f"ALTER TABLE materials ADD COLUMN {name} {ddl}"))
    with engine.begin() as conn:
        for name in ("chapter", "difficulty", "exam_focus"):
            if name in _table_columns(conn, "materials"):
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_materials_{name} ON materials({name})"
                ))

    # 一次性回填（带标记，幂等；只补空字段，不覆盖已有值）
    with SessionLocal() as db:
        from app.domain.agent.common import classify_difficulty, extract_chapter, has_exam_focus
        from app.infra.models import AppSetting, Material

        if db.get(AppSetting, MATERIAL_ARCHIVE_BACKFILL_MARKER):
            return
        changed = False
        for m in db.execute(select(Material)).scalars().all():
            haystack = " ".join(filter(None, [m.title, m.text or ""]))
            if not m.chapter:
                ch = extract_chapter(haystack)
                if ch:
                    m.chapter = ch[:120]
                    changed = True
            if not m.difficulty:
                diff = classify_difficulty(haystack)
                if diff:
                    m.difficulty = diff
                    changed = True
            if not m.exam_focus and has_exam_focus(haystack):
                m.exam_focus = True
                changed = True
        db.add(AppSetting(key=MATERIAL_ARCHIVE_BACKFILL_MARKER, value="1"))
        db.commit()


def _migrate_course_schedule_fields() -> None:
    """轻量迁移：旧库课程表补课表列（teacher/location/slots），幂等。

    slots 是 JSON 列（课表时段），旧库补列时给 `'[]'` 默认值让存量行可读；
    必须先于 _migrate_sync_columns（其按 ORM 模型 SELECT Course）执行。
    """
    with engine.begin() as conn:
        cols = _table_columns(conn, "courses")
        if not cols:
            return  # 表尚未创建（create_all 会按新结构建表）
        for name, ddl in (
            ("teacher", "VARCHAR(120) NOT NULL DEFAULT ''"),
            ("location", "VARCHAR(200) NOT NULL DEFAULT ''"),
            ("slots", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in cols:
                conn.execute(text(f"ALTER TABLE courses ADD COLUMN {name} {ddl}"))


# 旧库「naive 本地时间 → naive UTC」一次性迁移的完成标记
UTC_MIGRATION_MARKER = "tz_utc_migration_v1"


def _migrate_utc_timestamps() -> None:
    """一次性迁移：旧库所有 naive 时间都是「本机本地时间」→ 统一折算为 naive UTC。

    - 数据换算与完成标记在同一事务提交：崩溃重跑不会出现「一半已换算」；
    - 标记存在即跳过（幂等）；换算全程抑制同步钩子（`updated_at` 保留折算原值，
      不被误刷成 now，否则 LWW 排序会被破坏）；
    - 跨设备同步来的旧行无法还原其**原始**设备时区，按本机时区 best-effort
      折算——两端各自升级后，LWW 比较口径变为 UTC，一轮同步即收敛。
    """
    from .models import (
        AppSetting, Course, ExportRecord, Material, ReviewPlan, Task, Tombstone,
    )

    with SessionLocal() as db:
        if db.get(AppSetting, UTC_MIGRATION_MARKER):
            return
        db.add(AppSetting(key=UTC_MIGRATION_MARKER, value="1"))
        db.info["syncing"] = True  # 只换算值，不碰 updated_at
        try:
            for cls, cols in (
                (Course, ("created_at", "updated_at")),
                (Material, ("created_at", "updated_at")),
                (Task, ("created_at", "updated_at", "due_at")),
                (ReviewPlan, ("created_at", "updated_at")),
                (ExportRecord, ("created_at", "since_at", "until_at")),
                (Tombstone, ("deleted_at",)),
                (AppSetting, ("updated_at",)),
            ):
                for obj in db.execute(select(cls)).scalars().all():
                    for col in cols:
                        v = getattr(obj, col, None)
                        if isinstance(v, datetime) and v.tzinfo is None:
                            setattr(obj, col, legacy_naive_to_utc(v))
            db.commit()
        finally:
            db.info["syncing"] = False


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表 + 资料全文索引 FTS5 表（独立存储分词后文本，应用层同步）。"""
    from . import models  # noqa: F401  确保模型注册
    from app.infra.models import Material
    from app.infra.searchutil import seg

    Base.metadata.create_all(engine)
    # 内置清单模版幂等播种/升级（首次建库补插，老库按 key 同步最新内容）
    from app.domain.checklistlib import seed_builtin_templates

    with SessionLocal() as db:
        seed_builtin_templates(db)
    # 旧库轻量迁移：先补业务列（后续迁移会按 ORM 模型 SELECT，列必须已存在）
    _migrate_material_fields()  # 资料表补章节/难度/考试重点列
    _migrate_course_schedule_fields()  # 课程表补 teacher/location/slots 列
    # 旧库轻量迁移：补 sync_uuid / updated_at 列并回填（幂等）
    _migrate_sync_columns()
    # 旧库轻量迁移：导出记录补增量列（since_at/until_at/item_count/incremental）
    _migrate_export_records()
    # 旧库一次性迁移：naive 本地时间 → naive UTC（跨时区同步口径统一）
    _migrate_utc_timestamps()
    fts_created = False
    with engine.begin() as conn:
        # 清理早期版本的触发器残留（防止与新表结构冲突）
        conn.execute(text("DROP TRIGGER IF EXISTS materials_ai"))
        conn.execute(text("DROP TRIGGER IF EXISTS materials_ad"))
        exists = bool(conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='materials_fts'")
        ).scalar())
        if not exists:
            conn.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE materials_fts USING fts5(
                        title, text, tags, raw UNINDEXED,
                        tokenize='unicode61'
                    )
                    """
                )
            )
            fts_created = True
        # 语义向量缓存表（embedding API 后端使用；文本哈希变化时惰性重建）
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS semantic_vectors (
                    material_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL DEFAULT '',
                    dim INTEGER NOT NULL DEFAULT 0,
                    nchunks INTEGER NOT NULL DEFAULT 0,
                    vecs BLOB NOT NULL,
                    text_hash TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )

    # 已有数据全量重建（分词后入库）——仅在索引表缺失/为空时；
    # 正常重启不再全量重建（大资料库启动更快、避免与上方 DDL 竞争锁）。
    with SessionLocal() as db:
        mats = db.execute(select(Material)).scalars().all()
        if mats:
            n_fts = db.execute(
                text("SELECT COUNT(*) FROM materials_fts")
            ).scalar() or 0
            if fts_created or n_fts == 0:
                for m in mats:
                    fts_sync_add(db, m)
                db.commit()
        # 清理上传后未确认入库的遗留临时文件（保留 24 小时内的）
        from app.infra.filestore import ORPHAN_FILE_MAX_AGE_S, cleanup_orphans

        referenced = {
            str(Path(m.file_path).resolve()) for m in mats if m.file_path
        }
        cleanup_orphans(referenced, ORPHAN_FILE_MAX_AGE_S)


def fts_sync_add(db, material) -> None:
    """新增/更新资料时同步 FTS（title/text/tags 存分词，raw 存原文供摘录）。

    章节并入 title 索引列（避免改动 FTS 表结构也能检索「第3章」），
    同时进 raw 让命中摘录能展示章节名。
    """
    from app.infra.searchutil import seg

    indexed_title = " ".join(filter(None, [material.title, material.chapter or ""]))
    raw = " | ".join(filter(None, [material.title, material.chapter or "", material.text, " ".join(material.tags or [])]))
    db.execute(
        text(
            "INSERT INTO materials_fts(rowid, title, text, tags, raw) "
            "VALUES (:id, :t, :x, :g, :r)"
        ),
        {
            "id": material.id,
            "t": seg(indexed_title),
            "x": seg(material.text),
            "g": seg(" ".join(material.tags or [])),
            "r": raw,
        },
    )


def fts_sync_update(db, material) -> None:
    fts_sync_delete(db, material.id)
    fts_sync_add(db, material)


def fts_sync_delete(db, material_id: int) -> None:
    db.execute(
        text("DELETE FROM materials_fts WHERE rowid = :id"), {"id": material_id}
    )
