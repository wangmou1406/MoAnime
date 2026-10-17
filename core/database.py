# -*- coding: utf-8 -*-
"""SQLite 持久层：动漫源、下载任务、下载历史、搜索历史。"""
import json
import os
import sqlite3
import threading
import time

from .config import DATABASE_FILE
from .utils import now_str

# 当前源列表的排序模式（进程内状态，由 /api/sources/sort 设置）。
# manual/smart/health/hits/enabled/ad 都把结果物理写进 sort_order，
# 读取时统一走「广告源在后 + sort_order」；只有 name 走纯名称排序。
_SOURCE_SORT_MODE = "manual"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL DEFAULT 'generic',
    base_url      TEXT DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    builtin       INTEGER NOT NULL DEFAULT 0,
    hidden        INTEGER NOT NULL DEFAULT 0,
    ad_flag       INTEGER NOT NULL DEFAULT 0,
    ad_kind       TEXT DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    note          TEXT DEFAULT '',
    config        TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT DEFAULT '',
    updated_at    TEXT DEFAULT '',
    last_ok_at    TEXT DEFAULT '',
    last_error    TEXT DEFAULT '',
    ok_count      INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    hit_count     INTEGER NOT NULL DEFAULT 0,   -- 检索命中过的条目数（累计，用于质量分）
    last_ms       INTEGER NOT NULL DEFAULT 0    -- 最近一次测活耗时（毫秒，用于质量分）
);

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    source        TEXT DEFAULT '',
    anime_name    TEXT DEFAULT '',
    episode_name  TEXT DEFAULT '',
    episode_num   REAL DEFAULT 0,
    url           TEXT DEFAULT '',
    save_path     TEXT DEFAULT '',
    status        TEXT DEFAULT 'queued',
    priority      INTEGER NOT NULL DEFAULT 0,
    progress      REAL NOT NULL DEFAULT 0,
    total_bytes   INTEGER NOT NULL DEFAULT 0,
    done_bytes    INTEGER NOT NULL DEFAULT 0,
    speed         REAL NOT NULL DEFAULT 0,
    segments_total INTEGER NOT NULL DEFAULT 0,
    segments_done  INTEGER NOT NULL DEFAULT 0,
    retries       INTEGER NOT NULL DEFAULT 0,
    message       TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    created_at    TEXT DEFAULT '',
    started_at    TEXT DEFAULT '',
    finished_at   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT DEFAULT '',
    anime_name    TEXT DEFAULT '',
    episode_name  TEXT DEFAULT '',
    path          TEXT DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS search_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword       TEXT NOT NULL,
    created_at    TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_history_anime ON history(anime_name, episode_name);
"""


class Database:
    """轻量 SQLite 封装：每个线程独立连接，写操作串行化。"""

    def __init__(self, path: str = DATABASE_FILE):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------ 连接
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self._write_lock:
            self.conn.executescript(_SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """给已有数据库补上后来新增的列（SQLite 的 ADD COLUMN 是安全的）。"""
        wanted = {
            "sources": {
                "ad_flag": "INTEGER NOT NULL DEFAULT 0",
                "ad_kind": "TEXT DEFAULT ''",
                "hit_count": "INTEGER NOT NULL DEFAULT 0",
                "last_ms": "INTEGER NOT NULL DEFAULT 0",
            },
        }
        for table, columns in wanted.items():
            try:
                existing = {row["name"] for row in
                            self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()}
            except Exception:  # noqa: BLE001
                continue
            for column, spec in columns.items():
                if column not in existing:
                    try:
                        self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s"
                                          % (table, column, spec))
                    except Exception:  # noqa: BLE001
                        pass

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._write_lock:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor

    def query(self, sql: str, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 源
    def list_sources(self, include_hidden: bool = True):
        sql = "SELECT * FROM sources"
        if not include_hidden:
            sql += " WHERE hidden = 0"
        if _SOURCE_SORT_MODE == "name":
            # 用户明确要求按名称排：此时不把广告源单独沉底，否则名字排序会乱
            sql += " ORDER BY name COLLATE NOCASE ASC, id ASC"
        elif _SOURCE_SORT_MODE == "ad":
            # 「广告置顶」是临时排查视图：此时必须让广告源真的在最前，
            # 否则读路径的 ad_flag ASC 会立刻把它们压回最后，排序形同无效
            sql += " ORDER BY ad_flag DESC, sort_order ASC, id ASC"
        else:
            # 其余模式由排序接口写入 sort_order；广告源始终沉底
            sql += " ORDER BY ad_flag ASC, sort_order ASC, id ASC"
        rows = self.query(sql)
        for row in rows:
            row["config"] = self._loads(row.get("config"))
            row["enabled"] = bool(row.get("enabled"))
            row["builtin"] = bool(row.get("builtin"))
            row["hidden"] = bool(row.get("hidden"))
            row["ad_flag"] = bool(row.get("ad_flag"))
        return rows

    def get_source(self, source_id=None, name: str = None):
        row = None
        if source_id is not None:
            row = self.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
        if row is None and name:
            row = self.query_one("SELECT * FROM sources WHERE name = ?", (name,))
        if row:
            row["config"] = self._loads(row.get("config"))
            row["enabled"] = bool(row.get("enabled"))
            row["builtin"] = bool(row.get("builtin"))
            row["ad_flag"] = bool(row.get("ad_flag"))
            row["hidden"] = bool(row.get("hidden"))
        return row

    def add_source(self, data: dict) -> int:
        order = self.query_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM sources")["n"]
        cursor = self.execute(
            """INSERT INTO sources (name, type, base_url, enabled, builtin, hidden,
                                    sort_order, note, config, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"], data.get("type", "generic"), data.get("base_url", ""),
                1 if data.get("enabled", True) else 0,
                1 if data.get("builtin", False) else 0,
                1 if data.get("hidden", False) else 0,
                data.get("sort_order", order),
                data.get("note", ""),
                json.dumps(data.get("config", {}), ensure_ascii=False),
                now_str(), now_str(),
            ),
        )
        return int(cursor.lastrowid)

    def update_source(self, source_id: int, data: dict) -> None:
        fields, params = [], []
        for key in ("name", "type", "base_url", "note", "ad_kind"):
            if key in data and data[key] is not None:
                fields.append("%s = ?" % key)
                params.append(data[key])
        for key in ("enabled", "hidden", "builtin", "ad_flag"):
            if key in data and data[key] is not None:
                fields.append("%s = ?" % key)
                params.append(1 if data[key] else 0)
        if "sort_order" in data and data["sort_order"] is not None:
            fields.append("sort_order = ?")
            params.append(int(data["sort_order"]))
        if "config" in data and data["config"] is not None:
            fields.append("config = ?")
            params.append(json.dumps(data["config"], ensure_ascii=False))
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_str())
        params.append(source_id)
        self.execute("UPDATE sources SET %s WHERE id = ?" % ", ".join(fields), tuple(params))

    def delete_source(self, source_id: int) -> None:
        self.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def reorder_sources(self, ordered_ids) -> None:
        with self._write_lock:
            for index, source_id in enumerate(ordered_ids):
                self.conn.execute("UPDATE sources SET sort_order = ? WHERE id = ?",
                                  (index, int(source_id)))
            self.conn.commit()

    def record_source_result(self, source_id: int, ok: bool, error: str = "",
                             elapsed_ms: int = None) -> None:
        """记录一次测活结果。elapsed_ms 用于「智能排序」评估源的响应速度。"""
        if ok:
            if elapsed_ms is None:
                self.execute(
                    """UPDATE sources SET last_ok_at = ?, last_error = '', ok_count = ok_count + 1
                       WHERE id = ?""", (now_str(), source_id))
            else:
                self.execute(
                    """UPDATE sources SET last_ok_at = ?, last_error = '',
                          ok_count = ok_count + 1, last_ms = ? WHERE id = ?""",
                    (now_str(), int(elapsed_ms), source_id))
        else:
            self.execute(
                """UPDATE sources SET last_error = ?, fail_count = fail_count + 1
                   WHERE id = ?""", ((error or "")[:500], source_id))

    def record_source_hits(self, source_id: int, count: int) -> None:
        """记录一次检索命中多少条目（累加）。命中多说明这个源内容更全。"""
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            count = 0
        self.execute("UPDATE sources SET hit_count = hit_count + ? WHERE id = ?",
                     (count, source_id))

    def set_sort_mode(self, mode: str) -> None:
        """记住当前的源排序模式。

        - manual / smart / health / hits / enabled / ad：由排序接口**物理写入 sort_order**，
          这里仍按「广告源在后 + sort_order」读取，保证广告源始终沉底。
        - name：按名称排序不受广告标记影响，否则名字排序会被打乱。
        """
        global _SOURCE_SORT_MODE
        _SOURCE_SORT_MODE = mode or "manual"

    # ------------------------------------------------------------ 任务
    _TASK_FIELDS = (
        "source", "anime_name", "episode_name", "episode_num", "url", "save_path",
        "status", "priority", "progress", "total_bytes", "done_bytes", "speed",
        "segments_total", "segments_done", "retries", "message", "error",
        "started_at", "finished_at",
    )

    def add_task(self, data: dict) -> str:
        task_id = data["id"]
        columns = ["id", "created_at"] + [f for f in self._TASK_FIELDS if f in data]
        values = [task_id, data.get("created_at") or now_str()] + [data[f] for f in self._TASK_FIELDS if f in data]
        placeholders = ", ".join("?" for _ in columns)
        self.execute("INSERT OR REPLACE INTO tasks (%s) VALUES (%s)" % (", ".join(columns), placeholders),
                     tuple(values))
        return task_id

    def update_task(self, task_id: str, data: dict) -> None:
        fields = [f for f in self._TASK_FIELDS if f in data]
        if not fields:
            return
        assignments = ", ".join("%s = ?" % f for f in fields)
        params = [data[f] for f in fields] + [task_id]
        self.execute("UPDATE tasks SET %s WHERE id = ?" % assignments, tuple(params))

    def get_task(self, task_id: str):
        return self.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def list_tasks(self, status: str = None, limit: int = 500):
        if status and status != "all":
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            placeholders = ", ".join("?" for _ in statuses)
            return self.query(
                "SELECT * FROM tasks WHERE status IN (%s) ORDER BY created_at DESC LIMIT ?" % placeholders,
                tuple(statuses) + (limit,))
        return self.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))

    def delete_task(self, task_id: str) -> None:
        self.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def delete_tasks(self, task_ids, only_finished: bool = True) -> int:
        if not task_ids:
            return 0
        placeholders = ", ".join("?" for _ in task_ids)
        sql = "DELETE FROM tasks WHERE id IN (%s)" % placeholders
        if only_finished:
            sql += " AND status IN ('completed','failed','canceled')"
        cursor = self.execute(sql, tuple(task_ids))
        return cursor.rowcount or 0

    def clear_tasks(self, statuses=("completed", "failed", "canceled")) -> int:
        placeholders = ", ".join("?" for _ in statuses)
        cursor = self.execute("DELETE FROM tasks WHERE status IN (%s)" % placeholders, tuple(statuses))
        return cursor.rowcount or 0

    def purge_unfinished_tasks(self) -> int:
        cursor = self.execute(
            "DELETE FROM tasks WHERE status IN ('queued','running','paused')")
        return cursor.rowcount or 0

    # ------------------------------------------------------------ 历史
    def add_history(self, source: str, anime_name: str, episode_name: str, path: str, size: int = 0) -> None:
        existing = self.query_one(
            "SELECT id FROM history WHERE anime_name = ? AND episode_name = ?", (anime_name, episode_name))
        if existing:
            self.execute("UPDATE history SET source = ?, path = ?, size = ?, created_at = ? WHERE id = ?",
                         (source, path, size, now_str(), existing["id"]))
        else:
            self.execute(
                """INSERT INTO history (source, anime_name, episode_name, path, size, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source, anime_name, episode_name, path, size, now_str()))

    def history_set(self) -> set:
        rows = self.query("SELECT anime_name, episode_name FROM history")
        return {(row["anime_name"], row["episode_name"]) for row in rows}

    def history_map(self) -> dict:
        """{(动漫名, 集名): 保存路径}，用于核对文件是否还在磁盘上。"""
        rows = self.query("SELECT anime_name, episode_name, path FROM history")
        return {(row["anime_name"], row["episode_name"]): (row["path"] or "") for row in rows}

    def clear_history_missing(self) -> int:
        """删除历史中「文件已不存在」的记录（用户在系统里删了视频时用）。"""
        rows = self.query("SELECT id, path FROM history")
        removed = 0
        for row in rows:
            path = row["path"] or ""
            if not path or not os.path.exists(path):
                self.execute("DELETE FROM history WHERE id = ?", (row["id"],))
                removed += 1
        return removed

    def list_history(self) -> list:
        return self.query("SELECT * FROM history ORDER BY created_at DESC")

    def delete_history(self, history_id: int) -> None:
        self.execute("DELETE FROM history WHERE id = ?", (history_id,))

    # ------------------------------------------------------------ 搜索历史
    def add_search_history(self, keyword: str) -> None:
        keyword = (keyword or "").strip()
        if not keyword:
            return
        self.execute("DELETE FROM search_history WHERE keyword = ?", (keyword,))
        self.execute("INSERT INTO search_history (keyword, created_at) VALUES (?, ?)",
                     (keyword, now_str()))

    def list_search_history(self, limit: int = 30) -> list:
        return self.query("SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,))

    def clear_search_history(self) -> None:
        self.execute("DELETE FROM search_history")

    # ------------------------------------------------------------ 出厂重置
    def purge_all_tasks(self) -> int:
        """删除**全部**任务记录（出厂重置用）。"""
        cursor = self.execute("DELETE FROM tasks")
        return cursor.rowcount or 0

    def purge_history(self) -> int:
        """删除全部下载历史（出厂重置用；不动磁盘文件）。"""
        cursor = self.execute("DELETE FROM history")
        return cursor.rowcount or 0

    def purge_search_history(self) -> int:
        """删除全部检索历史，返回删掉的条数。"""
        row = self.query_one("SELECT COUNT(*) AS n FROM search_history")
        total = (row or {}).get("n", 0)
        self.execute("DELETE FROM search_history")
        return total

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _loads(value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


db = Database()
