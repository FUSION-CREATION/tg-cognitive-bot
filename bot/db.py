from __future__ import annotations

import json
import sqlite3
from pathlib import Path


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
        emotion_before: int,
        emotion_after: int,
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

    def save_checkin(self, tg_id: int, mood: int, stress: int, energy: int, note: str) -> None:
        user_id = self._ensure_user(tg_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checkins(user_id, mood, stress, energy, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, mood, stress, energy, note),
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

            checkin_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(mood), 2) AS avg_mood,
                    ROUND(AVG(stress), 2) AS avg_stress,
                    ROUND(AVG(energy), 2) AS avg_energy
                FROM checkins WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

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
                "checkins_total": checkin_row["total"] if checkin_row else 0,
                "avg_mood": checkin_row["avg_mood"] if checkin_row else None,
                "avg_stress": checkin_row["avg_stress"] if checkin_row else None,
                "avg_energy": checkin_row["avg_energy"] if checkin_row else None,
                "top_distortions": top_distortions,
            }
