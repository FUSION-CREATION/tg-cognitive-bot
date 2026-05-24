from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


_WORD_RE = re.compile(r"[а-яёa-z0-9-]{4,}", re.IGNORECASE)
_STOP_WORDS = {
    "когда", "почему", "потому", "поэтому", "который", "которая", "которые", "просто",
    "очень", "сейчас", "вроде", "вообще", "такой", "такое", "этого", "этого", "только",
    "снова", "после", "между", "чтобы", "можно", "нужно", "будет", "бывает", "должен",
    "всегда", "никогда", "кажется", "наверное", "здесь", "ситуация", "проблема", "думал",
}


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    situation TEXT,
                    thought TEXT,
                    distortions_json TEXT,
                    reframe TEXT,
                    action TEXT,
                    emotion_before INTEGER,
                    emotion_after INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS checkins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mood INTEGER NOT NULL,
                    stress INTEGER NOT NULL,
                    energy INTEGER NOT NULL,
                    note TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    tg_id INTEGER,
                    source TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    audio_seconds REAL DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS admin_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_key TEXT UNIQUE NOT NULL,
                    payload TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_status (
                    user_id INTEGER PRIMARY KEY,
                    is_blocked INTEGER DEFAULT 0,
                    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_delivery_ok_at TEXT,
                    last_delivery_error_at TEXT,
                    last_delivery_error TEXT,
                    blocked_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS user_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    tg_id INTEGER,
                    event_type TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_events_created_at
                ON user_events(created_at DESC);

                CREATE TABLE IF NOT EXISTS admin_users (
                    tg_id INTEGER PRIMARY KEY,
                    granted_by INTEGER,
                    note TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS broadcast_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_by_tg_id INTEGER,
                    segment TEXT NOT NULL,
                    include_blocked INTEGER DEFAULT 0,
                    text_preview TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    total_targets INTEGER DEFAULT 0,
                    sent_count INTEGER DEFAULT 0,
                    blocked_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    error_top TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS broadcast_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    tg_id INTEGER NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error_text TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES broadcast_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_broadcast_attempts_run_id
                ON broadcast_attempts(run_id);

                CREATE TABLE IF NOT EXISTS reality_profiles (
                    user_id INTEGER PRIMARY KEY,
                    raw_input TEXT,
                    profile_json TEXT,
                    profile_quality INTEGER DEFAULT 0,
                    last_check_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS reality_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tg_id INTEGER NOT NULL,
                    source_text TEXT,
                    profile_quality INTEGER DEFAULT 0,
                    result_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_reality_checks_user_id
                ON reality_checks(user_id, id DESC);
                """
            )

    def _ensure_user(self, tg_id: int) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(tg_id) VALUES (?)
                ON CONFLICT(tg_id) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
                """,
                (tg_id,),
            )
            row = conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
            return int(row["id"])

    def save_session(
        self,
        tg_id: int,
        mode: str,
        situation: str,
        thought: str,
        distortions: list[str],
        reframe: str,
        action: str,
        emotion_before: int | None,
        emotion_after: int | None,
    ) -> None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(
                    user_id, mode, situation, thought, distortions_json, reframe, action,
                    emotion_before, emotion_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    mode,
                    situation,
                    thought,
                    json.dumps(distortions, ensure_ascii=False),
                    reframe,
                    action,
                    emotion_before,
                    emotion_after,
                ),
            )

    def get_stats(self, tg_id: int) -> dict:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            sessions_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(emotion_before), 2) AS avg_before,
                    ROUND(AVG(emotion_after), 2) AS avg_after,
                    ROUND(AVG(emotion_before - emotion_after), 2) AS avg_delta
                FROM sessions WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            mode_rows = conn.execute(
                """
                SELECT mode, COUNT(*) AS cnt
                FROM sessions
                WHERE user_id = ?
                GROUP BY mode
                ORDER BY cnt DESC
                """,
                (user_id,),
            ).fetchall()
            mode_counts = {row["mode"]: row["cnt"] for row in mode_rows}

            distortions_counter: dict[str, int] = {}
            rows = conn.execute(
                "SELECT distortions_json FROM sessions WHERE user_id = ? AND distortions_json IS NOT NULL",
                (user_id,),
            ).fetchall()
            for row in rows:
                items = json.loads(row["distortions_json"])
                for item in items:
                    distortions_counter[item] = distortions_counter.get(item, 0) + 1

            top_distortions = sorted(distortions_counter.items(), key=lambda x: x[1], reverse=True)[:5]

            return {
                "sessions_total": sessions_row["total"] if sessions_row else 0,
                "avg_before": sessions_row["avg_before"] if sessions_row else None,
                "avg_after": sessions_row["avg_after"] if sessions_row else None,
                "avg_delta": sessions_row["avg_delta"] if sessions_row else None,
                "top_distortions": top_distortions,
                "mode_counts": mode_counts,
            }

    def get_user_context(self, tg_id: int) -> dict:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT mode, situation, thought, distortions_json, action, emotion_before, emotion_after, created_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 40
                """,
                (user_id,),
            ).fetchall()

        if not rows:
            return {
                "sessions_total": 0,
                "top_distortions": [],
                "frequent_modes": [],
                "avg_emotion_delta": None,
                "recurring_topics": [],
                "recent_actions": [],
            }

        mode_counter: Counter[str] = Counter()
        distortion_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        deltas: list[float] = []
        recent_actions: list[str] = []

        for row in rows:
            mode_counter.update([row["mode"]])

            try:
                distortions = json.loads(row["distortions_json"] or "[]")
            except json.JSONDecodeError:
                distortions = []
            for item in distortions:
                if isinstance(item, str) and item.strip():
                    distortion_counter.update([item.strip()])

            combined = f"{row['situation'] or ''} {row['thought'] or ''}".lower()
            for token in _WORD_RE.findall(combined):
                if token in _STOP_WORDS or token.isdigit():
                    continue
                topic_counter.update([token])

            before = row["emotion_before"]
            after = row["emotion_after"]
            if before is not None and after is not None:
                deltas.append(float(before - after))

            action = (row["action"] or "").strip()
            if action and len(recent_actions) < 5:
                recent_actions.append(action)

        avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else None

        return {
            "sessions_total": int(total_row["total"] if total_row else 0),
            "top_distortions": [name for name, _ in distortion_counter.most_common(5)],
            "frequent_modes": [name for name, _ in mode_counter.most_common(3)],
            "avg_emotion_delta": avg_delta,
            "recurring_topics": [name for name, _ in topic_counter.most_common(8)],
            "recent_actions": recent_actions,
        }

    def record_usage_event(
        self,
        tg_id: int | None,
        source: str,
        model: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        audio_seconds: float = 0.0,
    ) -> None:
        user_id: int | None = None
        if tg_id is not None:
            user_id = self._ensure_user(tg_id)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events(
                    user_id, tg_id, source, model, input_tokens, output_tokens, audio_seconds, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    tg_id,
                    source,
                    model,
                    int(input_tokens or 0),
                    int(output_tokens or 0),
                    float(audio_seconds or 0.0),
                    float(cost_usd or 0.0),
                ),
            )

    def get_usage_summary_last_hours(self, hours: int) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    ROUND(COALESCE(SUM(cost_usd), 0), 6) AS cost_usd,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    ROUND(COALESCE(SUM(audio_seconds), 0), 2) AS audio_seconds,
                    COUNT(*) AS events
                FROM usage_events
                WHERE datetime(created_at) >= datetime('now', ?)
                """,
                (f"-{int(hours)} hours",),
            ).fetchone()

        return dict(row) if row else {
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "audio_seconds": 0.0,
            "events": 0,
        }

    def get_usage_daily(self, days: int = 7) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    date(created_at) AS day,
                    ROUND(COALESCE(SUM(cost_usd), 0), 6) AS cost_usd,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    ROUND(COALESCE(SUM(audio_seconds), 0), 2) AS audio_seconds,
                    COUNT(*) AS events
                FROM usage_events
                WHERE date(created_at) >= date('now', ?)
                GROUP BY date(created_at)
                ORDER BY day DESC
                """,
                (f"-{int(days)} days",),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_cost_between(self, start_utc: datetime, end_utc: datetime) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ROUND(COALESCE(SUM(cost_usd), 0), 6) AS total
                FROM usage_events
                WHERE datetime(created_at) >= datetime(?) AND datetime(created_at) < datetime(?)
                """,
                (
                    start_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    end_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            ).fetchone()
        return float(row["total"]) if row and row["total"] is not None else 0.0

    def has_alert_been_sent(self, alert_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM admin_alerts WHERE alert_key = ? LIMIT 1",
                (alert_key,),
            ).fetchone()
        return row is not None

    def mark_alert_sent(self, alert_key: str, payload: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_alerts(alert_key, payload)
                VALUES (?, ?)
                ON CONFLICT(alert_key) DO NOTHING
                """,
                (alert_key, payload),
            )

    def grant_admin(self, tg_id: int, granted_by: int | None = None, note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_users(tg_id, granted_by, note)
                VALUES (?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    granted_by = excluded.granted_by,
                    note = excluded.note
                """,
                (int(tg_id), int(granted_by) if granted_by else None, (note or "")[:120]),
            )

    def revoke_admin(self, tg_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM admin_users WHERE tg_id = ?", (int(tg_id),))

    def is_admin_tg_id(self, tg_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM admin_users WHERE tg_id = ? LIMIT 1",
                (int(tg_id),),
            ).fetchone()
        return row is not None

    def list_admins(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tg_id, granted_by, note, created_at
                FROM admin_users
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_user_active(self, tg_id: int) -> None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_status(
                    user_id, is_blocked, last_seen_at, updated_at
                ) VALUES (?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_blocked = 0,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id,),
            )

    def mark_delivery_ok(self, tg_id: int) -> None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_status(
                    user_id, is_blocked, last_delivery_ok_at, updated_at
                ) VALUES (?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_blocked = 0,
                    last_delivery_ok_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id,),
            )

    def mark_delivery_failed(self, tg_id: int, error_text: str, blocked: bool = False) -> None:
        user_id = self._ensure_user(tg_id)
        clean_error = (error_text or "").strip()[:240]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_status(
                    user_id, is_blocked, last_delivery_error_at, last_delivery_error, blocked_at, updated_at
                ) VALUES (?, ?, CURRENT_TIMESTAMP, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_blocked = ?,
                    last_delivery_error_at = CURRENT_TIMESTAMP,
                    last_delivery_error = ?,
                    blocked_at = CASE
                        WHEN ? = 1 AND blocked_at IS NULL THEN CURRENT_TIMESTAMP
                        WHEN ? = 0 THEN blocked_at
                        ELSE blocked_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    1 if blocked else 0,
                    clean_error,
                    1 if blocked else 0,
                    1 if blocked else 0,
                    clean_error,
                    1 if blocked else 0,
                    1 if blocked else 0,
                ),
            )

    def log_user_event(self, tg_id: int, event_type: str, payload: str = "") -> None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_events(user_id, tg_id, event_type, payload)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, tg_id, event_type[:64], (payload or "")[:400]),
            )

    def get_admin_user_overview(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS users_total,
                    (SELECT COUNT(*)
                     FROM users u
                     LEFT JOIN user_status s ON s.user_id = u.id
                     WHERE COALESCE(s.is_blocked, 0) = 1) AS blocked_total,
                    (SELECT COUNT(*)
                     FROM users u
                     LEFT JOIN user_status s ON s.user_id = u.id
                     WHERE datetime(COALESCE(s.last_seen_at, u.last_seen_at)) >= datetime('now', '-7 days')) AS active_7d,
                    (SELECT COUNT(*)
                     FROM users u
                     LEFT JOIN user_status s ON s.user_id = u.id
                     WHERE datetime(COALESCE(s.last_seen_at, u.last_seen_at)) >= datetime('now', '-24 hours')) AS active_24h
                """
            ).fetchone()
        return dict(row) if row else {
            "users_total": 0,
            "blocked_total": 0,
            "active_7d": 0,
            "active_24h": 0,
        }

    def get_admin_user_rows(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    u.tg_id,
                    COALESCE(s.is_blocked, 0) AS is_blocked,
                    COALESCE(s.last_seen_at, u.last_seen_at) AS last_seen_at,
                    s.last_delivery_ok_at,
                    s.last_delivery_error_at,
                    s.last_delivery_error,
                    (
                        SELECT e.event_type
                        FROM user_events e
                        WHERE e.user_id = u.id
                        ORDER BY e.id DESC
                        LIMIT 1
                    ) AS last_event
                FROM users u
                LEFT JOIN user_status s ON s.user_id = u.id
                ORDER BY datetime(COALESCE(s.last_seen_at, u.last_seen_at)) DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_user_events(self, limit: int = 40, event_prefix: str = "") -> list[dict]:
        with self._connect() as conn:
            if event_prefix:
                rows = conn.execute(
                    """
                    SELECT tg_id, event_type, payload, created_at
                    FROM user_events
                    WHERE event_type LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (f"{event_prefix}%", int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT tg_id, event_type, payload, created_at
                    FROM user_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_broadcast_targets(self, include_blocked: bool = False, segment: str = "all") -> list[int]:
        segment = (segment or "all").strip().lower()
        with self._connect() as conn:
            if segment == "active_24h":
                condition = "datetime(COALESCE(s.last_seen_at, u.last_seen_at)) >= datetime('now', '-24 hours')"
            elif segment == "active_7d":
                condition = "datetime(COALESCE(s.last_seen_at, u.last_seen_at)) >= datetime('now', '-7 days')"
            elif segment == "power_7d":
                condition = (
                    "u.id IN (SELECT user_id FROM sessions "
                    "WHERE datetime(created_at) >= datetime('now', '-7 days') "
                    "GROUP BY user_id HAVING COUNT(*) >= 5)"
                )
            else:
                condition = "1=1"

            blocked_filter = "1=1" if include_blocked else "COALESCE(s.is_blocked, 0) = 0"
            rows = conn.execute(
                f"""
                SELECT u.tg_id
                FROM users u
                LEFT JOIN user_status s ON s.user_id = u.id
                WHERE {blocked_filter}
                  AND {condition}
                ORDER BY u.id DESC
                """
            ).fetchall()
        return [int(row["tg_id"]) for row in rows]

    def create_broadcast_run(
        self,
        created_by_tg_id: int,
        segment: str,
        include_blocked: bool,
        text_preview: str,
        total_targets: int,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO broadcast_runs(
                    created_by_tg_id, segment, include_blocked, text_preview, status, total_targets, started_at
                ) VALUES (?, ?, ?, ?, 'running', ?, CURRENT_TIMESTAMP)
                """,
                (
                    int(created_by_tg_id),
                    (segment or "all")[:24],
                    1 if include_blocked else 0,
                    (text_preview or "")[:300],
                    int(total_targets),
                ),
            )
            run_id = int(cur.lastrowid)
        return run_id

    def log_broadcast_attempt(self, run_id: int, tg_id: int, attempt_no: int, status: str, error_text: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO broadcast_attempts(run_id, tg_id, attempt_no, status, error_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(tg_id),
                    int(attempt_no),
                    (status or "unknown")[:24],
                    (error_text or "")[:240],
                ),
            )

    def finish_broadcast_run(
        self,
        run_id: int,
        sent_count: int,
        blocked_count: int,
        failed_count: int,
        retry_count: int,
        error_top: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE broadcast_runs
                SET status = 'done',
                    sent_count = ?,
                    blocked_count = ?,
                    failed_count = ?,
                    retry_count = ?,
                    error_top = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(sent_count),
                    int(blocked_count),
                    int(failed_count),
                    int(retry_count),
                    (error_top or "")[:300],
                    int(run_id),
                ),
            )

    def get_recent_broadcast_runs_summary(self, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_by_tg_id, segment, include_blocked, status, total_targets,
                       sent_count, blocked_count, failed_count, retry_count, created_at, finished_at, text_preview, error_top
                FROM broadcast_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_broadcast_top_errors(self, run_id: int, limit: int = 3) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT error_text, COUNT(*) AS cnt
                FROM broadcast_attempts
                WHERE run_id = ?
                  AND status IN ('failed', 'blocked')
                  AND COALESCE(error_text, '') <> ''
                GROUP BY error_text
                ORDER BY cnt DESC, id DESC
                LIMIT ?
                """,
                (int(run_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_admin_delivery_kpi_last_days(self, days: int = 7) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(sent_count), 0) AS sent_count,
                    COALESCE(SUM(blocked_count), 0) AS blocked_count,
                    COALESCE(SUM(failed_count), 0) AS failed_count,
                    COALESCE(SUM(retry_count), 0) AS retry_count,
                    COUNT(*) AS runs_count
                FROM broadcast_runs
                WHERE datetime(created_at) >= datetime('now', ?)
                """,
                (f"-{int(days)} days",),
            ).fetchone()
        return dict(row) if row else {
            "sent_count": 0,
            "blocked_count": 0,
            "failed_count": 0,
            "retry_count": 0,
            "runs_count": 0,
        }

    def cleanup_old_admin_data(self, keep_days: int = 45) -> dict:
        keep_days = max(7, int(keep_days))
        with self._connect() as conn:
            before = {
                "user_events": conn.execute("SELECT COUNT(*) AS c FROM user_events").fetchone()["c"],
                "usage_events": conn.execute("SELECT COUNT(*) AS c FROM usage_events").fetchone()["c"],
                "admin_alerts": conn.execute("SELECT COUNT(*) AS c FROM admin_alerts").fetchone()["c"],
                "broadcast_attempts": conn.execute("SELECT COUNT(*) AS c FROM broadcast_attempts").fetchone()["c"],
            }
            conn.execute(
                "DELETE FROM user_events WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            conn.execute(
                "DELETE FROM usage_events WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            conn.execute(
                "DELETE FROM admin_alerts WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            conn.execute(
                "DELETE FROM broadcast_attempts WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            after = {
                "user_events": conn.execute("SELECT COUNT(*) AS c FROM user_events").fetchone()["c"],
                "usage_events": conn.execute("SELECT COUNT(*) AS c FROM usage_events").fetchone()["c"],
                "admin_alerts": conn.execute("SELECT COUNT(*) AS c FROM admin_alerts").fetchone()["c"],
                "broadcast_attempts": conn.execute("SELECT COUNT(*) AS c FROM broadcast_attempts").fetchone()["c"],
            }
        return {
            "keep_days": keep_days,
            "deleted_user_events": int(before["user_events"] - after["user_events"]),
            "deleted_usage_events": int(before["usage_events"] - after["usage_events"]),
            "deleted_admin_alerts": int(before["admin_alerts"] - after["admin_alerts"]),
            "deleted_broadcast_attempts": int(before["broadcast_attempts"] - after["broadcast_attempts"]),
        }

    def get_progress_snapshot(self, tg_id: int) -> dict:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            total = int(total_row["total"] if total_row else 0)

            mode_row = conn.execute(
                """
                SELECT mode, COUNT(*) AS cnt
                FROM sessions
                WHERE user_id = ?
                GROUP BY mode
                ORDER BY cnt DESC
                """,
                (user_id,),
            ).fetchall()
            mode_counts = {str(r["mode"]): int(r["cnt"]) for r in mode_row}

            days7_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS sessions_7d,
                    COUNT(DISTINCT date(created_at)) AS active_days_7d
                FROM sessions
                WHERE user_id = ? AND datetime(created_at) >= datetime('now', '-7 days')
                """,
                (user_id,),
            ).fetchone()

            rows = conn.execute(
                """
                SELECT DISTINCT date(created_at) AS d
                FROM sessions
                WHERE user_id = ?
                ORDER BY d DESC
                """,
                (user_id,),
            ).fetchall()

            unique_days = [datetime.strptime(r["d"], "%Y-%m-%d").date() for r in rows if r["d"]]
            streak = 0
            if unique_days:
                expected = unique_days[0]
                for day in unique_days:
                    if day == expected:
                        streak += 1
                        expected = expected - timedelta(days=1)
                    else:
                        break

            top_dist_row = conn.execute(
                """
                SELECT distortions_json
                FROM sessions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 25
                """,
                (user_id,),
            ).fetchall()
            dist_counter: Counter[str] = Counter()
            for row in top_dist_row:
                try:
                    items = json.loads(row["distortions_json"] or "[]")
                except json.JSONDecodeError:
                    items = []
                for item in items:
                    if isinstance(item, str) and item.strip():
                        dist_counter.update([item.strip()])

            recent_rows = conn.execute(
                """
                SELECT mode, reframe, action, created_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 5
                """,
                (user_id,),
            ).fetchall()

        sessions_7d = int(days7_row["sessions_7d"] if days7_row else 0)
        active_days_7d = int(days7_row["active_days_7d"] if days7_row else 0)
        level = max(1, (total // 5) + 1)
        badges: list[str] = []
        if total >= 1:
            badges.append("Старт")
        if total >= 10:
            badges.append("Практик")
        if total >= 25:
            badges.append("Системный")
        if streak >= 3:
            badges.append("Серия 3")
        if streak >= 7:
            badges.append("Серия 7")
        if len(mode_counts) >= 4:
            badges.append("Универсал")
        if sessions_7d >= 5:
            badges.append("Ритм")

        recent_focus = []
        for r in recent_rows:
            mode = str(r["mode"] or "")
            action = (r["action"] or "").strip()
            reframe = (r["reframe"] or "").strip()
            line = action or reframe
            if line:
                recent_focus.append({"mode": mode, "line": line[:140]})

        return {
            "level": level,
            "sessions_total": total,
            "sessions_7d": sessions_7d,
            "active_days_7d": active_days_7d,
            "streak_days": streak,
            "mode_counts": mode_counts,
            "top_distortions": [name for name, _ in dist_counter.most_common(3)],
            "badges": badges,
            "recent_focus": recent_focus,
        }

    def get_last_session(
        self,
        tg_id: int,
        include_modes: tuple[str, ...] | None = None,
        exclude_modes: tuple[str, ...] | None = None,
        max_age_hours: int | None = None,
    ) -> dict | None:
        user_id = self._ensure_user(tg_id)
        clauses = ["user_id = ?"]
        params: list = [user_id]

        if include_modes:
            placeholders = ", ".join("?" for _ in include_modes)
            clauses.append(f"mode IN ({placeholders})")
            params.extend(include_modes)

        if exclude_modes:
            placeholders = ", ".join("?" for _ in exclude_modes)
            clauses.append(f"mode NOT IN ({placeholders})")
            params.extend(exclude_modes)

        if max_age_hours is not None and max_age_hours > 0:
            clauses.append("datetime(created_at) >= datetime('now', ?)")
            params.append(f"-{int(max_age_hours)} hours")

        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT mode, situation, thought, reframe, action, created_at
                FROM sessions
                WHERE {where_sql}
                ORDER BY id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return dict(row) if row else None

    def get_reality_profile(self, tg_id: int) -> dict | None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT raw_input, profile_json, profile_quality, last_check_json, updated_at
                FROM reality_profiles
                WHERE user_id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None

        try:
            profile = json.loads(row["profile_json"] or "{}")
        except json.JSONDecodeError:
            profile = {}

        try:
            last_check = json.loads(row["last_check_json"] or "{}")
        except json.JSONDecodeError:
            last_check = {}

        return {
            "raw_input": row["raw_input"] or "",
            "profile": profile if isinstance(profile, dict) else {},
            "profile_quality": int(row["profile_quality"] or 0),
            "last_check": last_check if isinstance(last_check, dict) else {},
            "updated_at": row["updated_at"] or "",
        }

    def save_reality_profile(
        self,
        tg_id: int,
        source_text: str,
        profile: dict,
        profile_quality: int,
        check_payload: dict,
    ) -> None:
        user_id = self._ensure_user(tg_id)
        safe_quality = max(0, min(100, int(profile_quality)))
        profile_json = json.dumps(profile or {}, ensure_ascii=False)[:12000]
        check_json = json.dumps(check_payload or {}, ensure_ascii=False)[:12000]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reality_profiles(user_id, raw_input, profile_json, profile_quality, last_check_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    raw_input = excluded.raw_input,
                    profile_json = excluded.profile_json,
                    profile_quality = excluded.profile_quality,
                    last_check_json = excluded.last_check_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    (source_text or "")[:4000],
                    profile_json,
                    safe_quality,
                    check_json,
                ),
            )
            conn.execute(
                """
                INSERT INTO reality_checks(user_id, tg_id, source_text, profile_quality, result_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    int(tg_id),
                    (source_text or "")[:4000],
                    safe_quality,
                    check_json,
                ),
            )
