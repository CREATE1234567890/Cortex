"""SQLite 存储层：schema、连接、轻量 KV 设置表。

设计约定（对应架构文档 ADR-7）：SQLite 起步，字段只增不改，预留 Postgres 迁移。
"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = _connect()
    return _local.conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,            -- doc_xxxxxxxx
    title         TEXT NOT NULL,
    filename      TEXT NOT NULL,
    pdf_path      TEXT NOT NULL,               -- 原始 PDF 绝对路径
    file_hash     TEXT NOT NULL UNIQUE,        -- sha256，内容去重
    n_pages       INTEGER DEFAULT 0,
    year          INTEGER,
    authors       TEXT,                        -- json list
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending/parsing/indexed/failed
    error         TEXT,
    n_chunks      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now')),
    indexed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,            -- 文档内顺序
    text          TEXT NOT NULL,
    section_path  TEXT NOT NULL DEFAULT '[]',  -- json list, 标题路径
    page_start    INTEGER NOT NULL,
    page_end      INTEGER NOT NULL,
    char_span     TEXT NOT NULL DEFAULT '[0,0]',
    bbox          TEXT NOT NULL DEFAULT '[]',  -- json [x0,y0,x1,y1]（首页坐标，用于高亮）
    block_type    TEXT NOT NULL DEFAULT 'text' -- text/table/caption/abstract
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '新对话',
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role          TEXT NOT NULL,               -- user/assistant
    content       TEXT NOT NULL,
    citations     TEXT,                        -- json: [{n, chunk_id, doc_id, page, snippet, title}]
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- FTS5 全文索引（BM25 兜底）：external-content 模式挂接 chunks 表，
-- 由触发器自动保持同步（contentless 模式不支持 DELETE，无法清理旧文档）。
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- 长期记忆（Hermes 式：FTS5 全文召回 + 会话后沉淀）
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'research_context', -- research_context/preference/fact
    content       TEXT NOT NULL,
    source_conv   TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, content='memories', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TABLE IF NOT EXISTS ingest_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    state         TEXT NOT NULL DEFAULT 'idle', -- idle/running/done/error
    total         INTEGER DEFAULT 0,
    done          INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    current_file  TEXT,
    message       TEXT,
    started_at    TEXT,
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""


def init_db():
    with db() as conn:
        # 迁移：旧版 contentless FTS5 不支持 DELETE，检测到即重建为 external-content
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        if row and "content='chunks'" not in (row["sql"] or ""):
            conn.execute("DROP TABLE chunks_fts")
            conn.execute("DROP TRIGGER IF EXISTS chunks_fts_ai")
            conn.execute("DROP TRIGGER IF EXISTS chunks_fts_ad")
        conn.executescript(SCHEMA)
        # 重建/补齐全文索引内容（external-content 依据 chunks 表全量灌入）
        cnt = conn.execute("SELECT count(*) c FROM chunks_fts").fetchone()["c"]
        total = conn.execute("SELECT count(*) c FROM chunks").fetchone()["c"]
        if cnt != total:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        if not conn.execute("SELECT 1 FROM ingest_jobs WHERE id=1").fetchone():
            conn.execute("INSERT INTO ingest_jobs (id, state) VALUES (1, 'idle')")


# ---------- settings KV ----------

def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_settings_all() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def loads(text, default=None):
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else []
