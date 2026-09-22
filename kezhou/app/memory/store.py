"""SQLite 存储层（同步 sqlite3，每次操作独立连接，WAL 模式）。

业务表（checkpointer 库独立分文件，见 runtime/config 注释）：
- messages          短期记忆：会话消息流水（persist 写入，load_context 读取最近 N 条）
- user_profiles     长期记忆：按用户隔离的画像摘要（节流更新）
- handoff_records   转人工记录：阶段四回写知源案例库的数据底座（query_id 串联飞轮链路）
- approvals         审批队列：高风险操作（退款/改单）的审批请求与结论（阶段四）
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    turn_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoff_records (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    query TEXT NOT NULL,
    query_id TEXT,
    transfer_reason TEXT NOT NULL,
    draft_answer TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    review_result TEXT,
    writeback_status TEXT NOT NULL DEFAULT 'none',
    writeback_case_id TEXT,
    writeback_at TEXT,
    writeback_error TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    order_id TEXT,
    amount REAL,
    payload TEXT NOT NULL,
    risk_factors TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewer_note TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # 旧库迁移：handoff_records 补回写列（新库由 _SCHEMA 直接建出）
            existing = {r[1] for r in c.execute("PRAGMA table_info(handoff_records)")}
            for col in ("writeback_status", "writeback_case_id", "writeback_at", "writeback_error"):
                if col not in existing:
                    default = "'none'" if col == "writeback_status" else "NULL"
                    c.execute(f"ALTER TABLE handoff_records ADD COLUMN {col} TEXT DEFAULT {default}")
            c.execute("PRAGMA journal_mode=WAL")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 短期记忆 ----

    def append_message(self, session_id: str, user_id: str, tenant_id: str, role: str, content: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO messages (message_id, session_id, user_id, tenant_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session_id, user_id, tenant_id, role, content, _now()),
            )

    def load_recent_messages(self, session_id: str, limit: int) -> list[dict]:
        """返回最近 limit 条消息（按时间升序），供 LLM 上下文使用。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content FROM messages WHERE session_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def count_messages(self, session_id: str) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)).fetchone()[0]

    def compact_session(self, session_id: str, keep: int) -> int:
        """短期记忆压缩：只保留最近 keep 条，被裁剪内容拼为一条摘要消息。

        阶段一用截断式摘要（控 token 成本）；阶段二可换 LLM 摘要，调用方无感。
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
            total = row[0]
            if total <= keep:
                return 0
            overflow_id = c.execute(
                "SELECT id FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT 1 OFFSET ?",
                (session_id, total - keep),
            ).fetchone()[0]
            dropped = c.execute(
                "SELECT role, content FROM messages WHERE session_id = ? AND id < ? ORDER BY id ASC",
                (session_id, overflow_id),
            ).fetchall()
            summary = "（早期对话摘要）" + "；".join(f"{r['role']}:{r['content'][:60]}" for r in dropped)[:800]
            c.execute("DELETE FROM messages WHERE session_id = ? AND id < ?", (session_id, overflow_id))
            c.execute(
                "INSERT INTO messages (message_id, session_id, user_id, tenant_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?, 'system', ?, ?)",
                (str(uuid.uuid4()), session_id, "", "", summary, _now()),
            )
        return len(dropped)

    # ---- 长期记忆 ----

    def load_profile_summary(self, user_id: str) -> str:
        with self._conn() as c:
            row = c.execute("SELECT summary FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        return row["summary"] if row else ""

    def bump_profile(self, user_id: str, tenant_id: str, topic: str) -> int:
        """轮次计数 +1；每 N 轮把最近咨询主题并入画像摘要（节流写入）。返回最新计数。"""
        with self._conn() as c:
            row = c.execute("SELECT turn_count, summary FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                turns, summary = row["turn_count"] + 1, row["summary"]
            else:
                turns, summary = 1, ""
            if turns % 5 == 0:  # 与 long_term_update_interval 默认值一致，参数化在节点层
                topic_line = f"[{topic[:40]}]"
                summary = (summary + topic_line)[-400:]
            c.execute(
                "INSERT INTO user_profiles (user_id, tenant_id, summary, turn_count, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary, turn_count=excluded.turn_count,"
                " updated_at=excluded.updated_at",
                (user_id, tenant_id, summary, turns, _now()),
            )
        return turns

    # ---- 转人工记录（阶段四回写知源的数据底座）----

    def save_handoff(self, record: dict) -> str:
        rid = record.get("id") or f"ho_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                "INSERT INTO handoff_records (id, session_id, user_id, tenant_id, query, query_id,"
                " transfer_reason, draft_answer, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    record["session_id"],
                    record["user_id"],
                    record["tenant_id"],
                    record["query"],
                    record.get("query_id"),
                    record["transfer_reason"],
                    record.get("draft_answer"),
                    record.get("status", "pending"),
                    _now(),
                ),
            )
        return rid

    def load_handoffs(self, session_id: str | None = None, status: str | None = None) -> list[dict]:
        with self._conn() as c:
            q = "SELECT * FROM handoff_records WHERE 1=1"
            args: list = []
            if session_id:
                q += " AND session_id = ?"
                args.append(session_id)
            if status:
                q += " AND status = ?"
                args.append(status)
            q += " ORDER BY created_at DESC LIMIT 100"
            rows = c.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def get_handoff(self, handoff_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM handoff_records WHERE id = ?", (handoff_id,)).fetchone()
        return dict(row) if row else None

    def review_handoff(self, handoff_id: str, review_result: str) -> dict | None:
        """记录人工审核结论（status pending → reviewed）。非 pending 返回 None。"""
        with self._conn() as c:
            row = c.execute("SELECT status FROM handoff_records WHERE id = ?", (handoff_id,)).fetchone()
            if row is None or row["status"] != "pending":
                return None
            c.execute(
                "UPDATE handoff_records SET status='reviewed', review_result=?, reviewed_at=? WHERE id=?",
                (review_result, _now(), handoff_id),
            )
        return self.get_handoff(handoff_id)

    def mark_writeback(self, handoff_id: str, status: str, case_id: str | None = None, error: str | None = None) -> None:
        """回写结果落库：status ∈ success | failed | skipped。"""
        with self._conn() as c:
            c.execute(
                "UPDATE handoff_records SET writeback_status=?, writeback_case_id=?, writeback_at=?, writeback_error=? WHERE id=?",
                (status, case_id, _now(), error, handoff_id),
            )

    # ---- 审批队列（高风险操作的审批请求，阶段四）----

    def create_approval(self, record: dict) -> str:
        rid = record.get("id") or f"ap_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                "INSERT INTO approvals (id, session_id, user_id, tenant_id, action_type, order_id, amount,"
                " payload, risk_factors, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    record["session_id"],
                    record["user_id"],
                    record["tenant_id"],
                    record["action_type"],
                    record.get("order_id"),
                    record.get("amount"),
                    json.dumps(record.get("payload", {}), ensure_ascii=False),
                    json.dumps(record.get("risk_factors", []), ensure_ascii=False),
                    record.get("status", "pending"),
                    _now(),
                ),
            )
        return rid

    def load_approvals(self, status: str | None = None, session_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM approvals WHERE 1=1"
        args: list = []
        if status:
            q += " AND status = ?"
            args.append(status)
        if session_id:
            q += " AND session_id = ?"
            args.append(session_id)
        q += " ORDER BY created_at DESC LIMIT 100"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [self._approval_dict(r) for r in rows]

    def get_approval(self, approval_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._approval_dict(row) if row else None

    def review_approval(self, approval_id: str, action: str, note: str | None = None) -> dict | None:
        """审批结论（pending → approved/rejected）。非 pending 或不存在返回 None。"""
        if action not in ("approved", "rejected"):
            raise ValueError(action)
        with self._conn() as c:
            row = c.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None or row["status"] != "pending":
                return None
            c.execute(
                "UPDATE approvals SET status=?, reviewer_note=?, reviewed_at=? WHERE id=?",
                (action, note, _now(), approval_id),
            )
            row = c.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._approval_dict(row)

    @staticmethod
    def _approval_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d.get("payload") or "{}")
        d["risk_factors"] = json.loads(d.get("risk_factors") or "[]")
        return d

    # ---- 飞轮指标（阶段五）----

    def stats(self) -> dict:
        with self._conn() as c:
            turns = c.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0]
            transfers = c.execute("SELECT COUNT(*) FROM handoff_records").fetchone()[0]
            transfer_reasons = {
                r[0]: r[1] for r in c.execute(
                    "SELECT transfer_reason, COUNT(*) FROM handoff_records GROUP BY transfer_reason"
                ).fetchall()
            }
            handoff_status = {
                r[0]: r[1] for r in c.execute(
                    "SELECT status, COUNT(*) FROM handoff_records GROUP BY status"
                ).fetchall()
            }
            writeback_status = {
                r[0]: r[1] for r in c.execute(
                    "SELECT writeback_status, COUNT(*) FROM handoff_records GROUP BY writeback_status"
                ).fetchall()
            }
            approval_status = {
                r[0]: r[1] for r in c.execute(
                    "SELECT status, COUNT(*) FROM approvals GROUP BY status"
                ).fetchall()
            }
        return {
            "turns": turns,
            "transfers": transfers,
            "transfer_rate": round(transfers / turns, 4) if turns else 0.0,
            "transfer_reasons": transfer_reasons,
            "handoff_status": handoff_status,
            "writeback_status": writeback_status,
            "approval_status": approval_status,
        }
