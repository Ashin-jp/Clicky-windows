"""
storage.py — SQLite Database & Storage Manager

Central persistence layer for Clicky. Stores macros, schedules,
folder watchers, session trust approvals, analytics, and all
subsystem data in a single SQLite database at D:/Clicky/clicky.db.
"""

import logging
import os
import sqlite3
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Storage Paths ────────────────────────────────────────────────────
import config as _cfg
CLICKY_DATA_DIR = Path(_cfg.CLICKY_DATA_DIR)
DB_PATH = CLICKY_DATA_DIR / "clicky.db"
MACROS_DIR = CLICKY_DATA_DIR / "macros"
LOGS_DIR = CLICKY_DATA_DIR / "logs"
WORKSPACE_DIR = CLICKY_DATA_DIR / "workspace"
RAG_DIR = CLICKY_DATA_DIR / "rag_index"


def ensure_directories():
    """Create all required storage directories."""
    for d in (CLICKY_DATA_DIR, MACROS_DIR, LOGS_DIR, WORKSPACE_DIR, RAG_DIR):
        d.mkdir(parents=True, exist_ok=True)


class ClickyDatabase:
    """SQLite database manager for all Clicky persistent data."""

    def __init__(self, db_path: Path = DB_PATH):
        ensure_directories()
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._last_vacuum = time.time()
        logger.info(f"Database initialized at {db_path}")
        self._migrate_schema()
        self._populate_defaults()

    def _create_tables(self):
        """Create all tables if they don't exist."""
        self._conn.executescript("""
            -- Session trust approvals
            CREATE TABLE IF NOT EXISTS session_trust (
                command_prefix TEXT PRIMARY KEY,
                approved_at TEXT NOT NULL,
                session_id TEXT NOT NULL,
                expiry_type TEXT DEFAULT 'session',
                app_context TEXT DEFAULT '',
                expires_at TEXT
            );

            -- Macros (recorded action sequences)
            CREATE TABLE IF NOT EXISTS macros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                actions_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_run_at TEXT,
                run_count INTEGER DEFAULT 0,
                is_broken INTEGER DEFAULT 0
            );

            -- Folder watchers
            CREATE TABLE IF NOT EXISTS folder_watchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                event_types TEXT NOT NULL,
                callback_action TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );

            -- Scheduled tasks
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                cron_expr TEXT,
                run_at TEXT,
                action_json TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                days_of_week TEXT,
                trigger_type TEXT DEFAULT 'time',
                trigger_params TEXT
            );

            -- Action history (audit log)
            CREATE TABLE IF NOT EXISTS action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                params TEXT,
                trust_level TEXT NOT NULL,
                result TEXT,
                executed_at TEXT NOT NULL,
                app_context TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0
            );

            -- Config settings
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Config profiles
            CREATE TABLE IF NOT EXISTS config_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 0
            );

            -- Saved conversation messages
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            -- Clipboard history
            CREATE TABLE IF NOT EXISTS clipboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_preview TEXT NOT NULL,
                content_type TEXT NOT NULL,
                full_content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            -- STT Corrections
            CREATE TABLE IF NOT EXISTS stt_corrections (
                misheard TEXT PRIMARY KEY,
                correct TEXT NOT NULL,
                apply_when_preceded_by TEXT DEFAULT NULL
            );

            -- Ambient context log
            CREATE TABLE IF NOT EXISTS ambient_context_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT NOT NULL,
                window_title TEXT,
                focused_element TEXT,
                duration_in_app REAL DEFAULT 0,
                previous_app TEXT,
                timestamp TEXT NOT NULL
            );

            -- Workspace snapshots
            CREATE TABLE IF NOT EXISTS workspace_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Knowledge base
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                source_app TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            -- Notification history
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_app TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                priority_flag INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            );

            -- Suggestion log
            CREATE TABLE IF NOT EXISTS suggestion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT NOT NULL,
                pattern_description TEXT,
                was_accepted INTEGER DEFAULT 0,
                suggested_at TEXT NOT NULL
            );

            -- Focus sessions
            CREATE TABLE IF NOT EXISTS focus_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                planned_minutes INTEGER DEFAULT 25,
                focused_seconds INTEGER DEFAULT 0,
                distracted_seconds INTEGER DEFAULT 0,
                distraction_apps TEXT DEFAULT ''
            );

            -- Health history
            CREATE TABLE IF NOT EXISTS health_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cpu_percent REAL,
                ram_percent REAL,
                ram_available_mb REAL,
                disk_percent REAL,
                cpu_temp REAL,
                top_processes TEXT,
                timestamp TEXT NOT NULL
            );

            -- RAG index metadata
            CREATE TABLE IF NOT EXISTS rag_index_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                indexed_at TEXT NOT NULL,
                file_type TEXT DEFAULT ''
            );

            -- Intent routing overrides
            CREATE TABLE IF NOT EXISTS intent_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT UNIQUE NOT NULL,
                task_type TEXT NOT NULL,
                action_tag TEXT
            );

            -- System 1: UI Guidance Tables
            CREATE TABLE IF NOT EXISTS app_categories (
                exe_name TEXT PRIMARY KEY,
                window_class TEXT,
                category TEXT,
                uia_support INTEGER DEFAULT 0,
                confirmed INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS app_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_exe TEXT,
                ui_fact TEXT,
                tags TEXT,
                source TEXT,
                created_at TEXT NOT NULL,
                access_count INTEGER DEFAULT 0,
                high_confidence INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS guidance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_exe TEXT,
                request TEXT,
                found INTEGER DEFAULT 0,
                confidence REAL,
                steps_taken INTEGER DEFAULT 0,
                user_confirmed INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            );

            -- Transform prompts
            CREATE TABLE IF NOT EXISTS transform_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_name TEXT UNIQUE NOT NULL,
                prompt_text TEXT NOT NULL
            );

            -- Transform history
            CREATE TABLE IF NOT EXISTS transform_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_text TEXT NOT NULL,
                transformed_text TEXT NOT NULL,
                transform_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- System 2: Linux Assistant Tables
            CREATE TABLE IF NOT EXISTS linux_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                subtopic TEXT,
                content TEXT,
                difficulty TEXT,
                example_command TEXT,
                example_output TEXT,
                distro_specific INTEGER DEFAULT 0,
                distro TEXT
            );

            CREATE TABLE IF NOT EXISTS linux_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                env_type TEXT,
                distro TEXT,
                distro_version TEXT,
                detected_at TEXT NOT NULL,
                commands_executed INTEGER DEFAULT 0,
                errors_encountered INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS linux_command_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT,
                output_preview TEXT,
                exit_code INTEGER,
                executed_at TEXT NOT NULL,
                explained INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS linux_lessons_completed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                completed_at TEXT NOT NULL,
                quiz_score REAL
            );
        """)
        self._conn.commit()

    def _migrate_schema(self):
        """Add missing columns to existing tables from older DB versions."""
        migrations = [
            ("session_trust", "expiry_type", "TEXT DEFAULT 'session'"),
            ("session_trust", "app_context", "TEXT DEFAULT ''"),
            ("session_trust", "expires_at", "TEXT"),
            ("macros", "is_broken", "INTEGER DEFAULT 0"),
            ("scheduled_tasks", "days_of_week", "TEXT"),
            ("scheduled_tasks", "trigger_type", "TEXT DEFAULT 'time'"),
            ("scheduled_tasks", "trigger_params", "TEXT"),
            ("action_history", "app_context", "TEXT DEFAULT ''"),
            ("action_history", "duration_ms", "INTEGER DEFAULT 0"),
            ("stt_corrections", "apply_when_preceded_by", "TEXT DEFAULT NULL"),
        ]
        for table, column, col_type in migrations:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                logger.debug(f"Migration: added {table}.{column}")
            except Exception:
                pass  # Column already exists
        self._conn.commit()

    # ─── Session Trust ────────────────────────────────────────────────

    def is_command_approved(self, prefix: str, session_id: str) -> bool:
        """Check if a command prefix was approved this session."""
        row = self._conn.execute(
            "SELECT 1 FROM session_trust WHERE command_prefix=? AND session_id=?",
            (prefix, session_id),
        ).fetchone()
        return row is not None

    def is_command_approved_with_context(self, prefix: str, session_id: str, app_context: str = "") -> bool:
        """Check if a command is approved for a specific app context."""
        now = datetime.now().isoformat()
        row = self._conn.execute(
            """SELECT 1 FROM session_trust
               WHERE command_prefix=? AND (session_id=? OR expiry_type IN ('permanent','per-week','per-day'))
               AND (app_context='' OR app_context=?)
               AND (expires_at IS NULL OR expires_at > ?)""",
            (prefix, session_id, app_context, now),
        ).fetchone()
        return row is not None

    def approve_command(self, prefix: str, session_id: str, expiry_type: str = "session", app_context: str = ""):
        """Record that a command prefix was approved."""
        expires_at = None
        if expiry_type == "per-day":
            expires_at = (datetime.now() + timedelta(days=1)).isoformat()
        elif expiry_type == "per-week":
            expires_at = (datetime.now() + timedelta(weeks=1)).isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO session_trust
               (command_prefix, approved_at, session_id, expiry_type, app_context, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (prefix, datetime.now().isoformat(), session_id, expiry_type, app_context, expires_at),
        )
        self._conn.commit()

    def clear_session_trust(self):
        """Clear session-only trust approvals (keep persistent ones)."""
        self._conn.execute("DELETE FROM session_trust WHERE expiry_type='session'")
        self._conn.execute("DELETE FROM session_trust WHERE expires_at IS NOT NULL AND expires_at < ?",
                           (datetime.now().isoformat(),))
        self._conn.commit()

    # ─── Macros ───────────────────────────────────────────────────────

    def save_macro(self, name: str, actions: list[dict], description: str = ""):
        """Save or update a macro."""
        self._conn.execute(
            """INSERT OR REPLACE INTO macros (name, description, actions_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, description, json.dumps(actions), datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_macro(self, name: str) -> dict | None:
        """Get a macro by name."""
        row = self._conn.execute(
            "SELECT * FROM macros WHERE name=?", (name,)
        ).fetchone()
        if row:
            return {
                "name": row["name"],
                "description": row["description"],
                "actions": json.loads(row["actions_json"]),
                "created_at": row["created_at"],
                "run_count": row["run_count"],
                "is_broken": bool(row["is_broken"]) if "is_broken" in row.keys() else False,
            }
        return None

    def list_macros(self) -> list[dict]:
        """List all saved macros."""
        rows = self._conn.execute(
            "SELECT name, description, run_count, created_at FROM macros ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_macro(self, name: str):
        """Delete a macro."""
        self._conn.execute("DELETE FROM macros WHERE name=?", (name,))
        self._conn.commit()

    def increment_macro_run(self, name: str):
        """Increment run count and update last_run_at."""
        self._conn.execute(
            "UPDATE macros SET run_count = run_count + 1, last_run_at = ? WHERE name = ?",
            (datetime.now().isoformat(), name),
        )
        self._conn.commit()

    def mark_macro_broken(self, name: str, broken: bool = True):
        """Mark a macro as broken/working."""
        self._conn.execute("UPDATE macros SET is_broken=? WHERE name=?", (int(broken), name))
        self._conn.commit()

    # ─── Scheduled Tasks ──────────────────────────────────────────────

    def save_schedule(self, name: str, action: dict, run_at: str = None, cron_expr: str = None,
                      days_of_week: str = None, trigger_type: str = "time", trigger_params: str = None):
        self._conn.execute(
            """INSERT INTO scheduled_tasks
               (name, cron_expr, run_at, action_json, created_at, days_of_week, trigger_type, trigger_params)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, cron_expr, run_at, json.dumps(action), datetime.now().isoformat(),
             days_of_week, trigger_type, trigger_params),
        )
        self._conn.commit()

    def get_active_schedules(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM scheduled_tasks WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    def deactivate_schedule(self, task_id: int):
        self._conn.execute("UPDATE scheduled_tasks SET active=0 WHERE id=?", (task_id,))
        self._conn.commit()

    # ─── Folder Watchers ──────────────────────────────────────────────

    def save_watcher(self, path: str, event_types: str, callback_action: str = None):
        self._conn.execute(
            """INSERT INTO folder_watchers (path, event_types, callback_action, created_at)
               VALUES (?, ?, ?, ?)""",
            (path, event_types, callback_action, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_active_watchers(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM folder_watchers WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    # ─── Action History ───────────────────────────────────────────────

    def log_action(self, action_type: str, params: str, trust_level: str, result: str,
                   app_context: str = "", duration_ms: int = 0):
        """Log an executed action for audit purposes."""
        self._conn.execute(
            """INSERT INTO action_history
               (action_type, params, trust_level, result, executed_at, app_context, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (action_type, params, trust_level, result, datetime.now().isoformat(),
             app_context, duration_ms),
        )
        self._conn.commit()

    def get_recent_actions(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM action_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_actions(self, app_name: str = None, action_type: str = None,
                       since: str = None, until: str = None, limit: int = 100) -> list[dict]:
        """Search action history with filters."""
        query = "SELECT * FROM action_history WHERE 1=1"
        params = []
        if app_name:
            query += " AND app_context LIKE ?"
            params.append(f"%{app_name}%")
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if since:
            query += " AND executed_at >= ?"
            params.append(since)
        if until:
            query += " AND executed_at <= ?"
            params.append(until)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ─── Config Settings ──────────────────────────────────────────────

    def set_config(self, key: str, value: str):
        """Save a config value."""
        self._conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value),
        )
        self._conn.commit()

    def get_config(self, key: str, default: str = None) -> str:
        """Get a config value."""
        row = self._conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ─── Config Profiles ──────────────────────────────────────────────

    def save_profile(self, name: str, settings: dict):
        """Save a named config profile."""
        self._conn.execute(
            "INSERT OR REPLACE INTO config_profiles (name, settings_json, created_at) VALUES (?, ?, ?)",
            (name, json.dumps(settings), datetime.now().isoformat()),
        )
        self._conn.commit()

    def load_profile(self, name: str) -> dict | None:
        """Load a config profile by name."""
        row = self._conn.execute("SELECT settings_json FROM config_profiles WHERE name=?", (name,)).fetchone()
        return json.loads(row["settings_json"]) if row else None

    def list_profiles(self) -> list[str]:
        """List all profile names."""
        rows = self._conn.execute("SELECT name FROM config_profiles ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def set_active_profile(self, name: str):
        """Set the active profile."""
        self._conn.execute("UPDATE config_profiles SET is_active=0")
        self._conn.execute("UPDATE config_profiles SET is_active=1 WHERE name=?", (name,))
        self._conn.commit()

    # ─── Messages ─────────────────────────────────────────────────────

    def save_messages(self, messages: list[tuple[str, str]]):
        """Save a list of (role, content) messages."""
        self._conn.execute("DELETE FROM messages")
        for idx, (role, content) in enumerate(messages):
            self._conn.execute(
                "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                (role, content, datetime.now().isoformat() + f"_{idx}"),
            )
        self._conn.commit()

    def load_messages(self) -> list[tuple[str, str]]:
        """Load saved messages."""
        rows = self._conn.execute("SELECT role, content FROM messages ORDER BY id ASC").fetchall()
        return [(row["role"], row["content"]) for row in rows]

    def clear_messages(self):
        """Clear all saved messages."""
        self._conn.execute("DELETE FROM messages")
        self._conn.commit()

    def prune_messages(self, keep_recent: int = 10):
        """Keep only the last N messages, delete older ones."""
        count = self._conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        if count > keep_recent:
            self._conn.execute(
                "DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT ?)",
                (keep_recent,),
            )
            self._conn.commit()
            logger.debug(f"Storage: pruned messages, kept {keep_recent}")

    # ─── Clipboard History ────────────────────────────────────────────

    def save_clipboard_entry(self, content_preview: str, content_type: str, full_content: str):
        """Save a clipboard entry (max 50 kept)."""
        self._conn.execute(
            "INSERT INTO clipboard_history (content_preview, content_type, full_content, timestamp) VALUES (?,?,?,?)",
            (content_preview[:200], content_type, full_content, datetime.now().isoformat()),
        )
        self._conn.execute(
            "DELETE FROM clipboard_history WHERE id NOT IN (SELECT id FROM clipboard_history ORDER BY id DESC LIMIT 50)"
        )
        self._conn.commit()

    def get_recent_clipboard(self, limit: int = 5) -> list[dict]:
        """Get recent clipboard entries."""
        rows = self._conn.execute(
            "SELECT * FROM clipboard_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Ambient Context Log ─────────────────────────────────────────

    def log_ambient_context(self, app_name: str, window_title: str = "",
                            focused_element: str = "", duration_in_app: float = 0,
                            previous_app: str = ""):
        """Log an ambient context snapshot."""
        self._conn.execute(
            """INSERT INTO ambient_context_log
               (app_name, window_title, focused_element, duration_in_app, previous_app, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (app_name, window_title, focused_element, duration_in_app,
             previous_app, datetime.now().isoformat()),
        )
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        self._conn.execute("DELETE FROM ambient_context_log WHERE timestamp < ?", (cutoff,))
        self._conn.commit()

    # ─── Workspace Snapshots ─────────────────────────────────────────

    def save_workspace(self, name: str, snapshot: dict):
        self._conn.execute(
            "INSERT OR REPLACE INTO workspace_snapshots (name, snapshot_json, created_at) VALUES (?,?,?)",
            (name, json.dumps(snapshot), datetime.now().isoformat()),
        )
        self._conn.commit()

    def load_workspace(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT snapshot_json FROM workspace_snapshots WHERE name=?", (name,)).fetchone()
        return json.loads(row["snapshot_json"]) if row else None

    def list_workspaces(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM workspace_snapshots ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    # ─── Knowledge Base ──────────────────────────────────────────────

    def save_knowledge(self, content: str, tags: str = "", source_app: str = "") -> int:
        cursor = self._conn.execute(
            "INSERT INTO knowledge_base (content, tags, source_app, created_at) VALUES (?,?,?,?)",
            (content, tags, source_app, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def search_knowledge(self, query: str, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge_base WHERE content LIKE ? OR tags LIKE ? ORDER BY access_count DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_knowledge(self, entry_id: int):
        self._conn.execute("DELETE FROM knowledge_base WHERE id=?", (entry_id,))
        self._conn.commit()

    def get_all_knowledge(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM knowledge_base ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ─── Notification History ────────────────────────────────────────

    def save_notification(self, source_app: str, title: str, body: str = "", priority: bool = False):
        self._conn.execute(
            "INSERT INTO notification_history (source_app, title, body, priority_flag, timestamp) VALUES (?,?,?,?,?)",
            (source_app, title, body, int(priority), datetime.now().isoformat()),
        )
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        self._conn.execute("DELETE FROM notification_history WHERE timestamp < ?", (cutoff,))
        self._conn.commit()

    def get_recent_notifications(self, hours: int = 1, priority_only: bool = False) -> list[dict]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM notification_history WHERE timestamp >= ?"
        params = [cutoff]
        if priority_only:
            query += " AND priority_flag = 1"
        query += " ORDER BY id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ─── Focus Sessions ──────────────────────────────────────────────

    def start_focus_session(self, planned_minutes: int) -> int:
        cursor = self._conn.execute(
            "INSERT INTO focus_sessions (started_at, planned_minutes) VALUES (?, ?)",
            (datetime.now().isoformat(), planned_minutes),
        )
        self._conn.commit()
        return cursor.lastrowid

    def update_focus_session(self, session_id: int, focused_secs: int, distracted_secs: int,
                             distraction_apps: str = ""):
        self._conn.execute(
            "UPDATE focus_sessions SET focused_seconds=?, distracted_seconds=?, distraction_apps=? WHERE id=?",
            (focused_secs, distracted_secs, distraction_apps, session_id),
        )
        self._conn.commit()

    def end_focus_session(self, session_id: int):
        self._conn.execute(
            "UPDATE focus_sessions SET ended_at=? WHERE id=?",
            (datetime.now().isoformat(), session_id),
        )
        self._conn.commit()

    # ─── Health History ──────────────────────────────────────────────

    def log_health(self, cpu: float, ram: float, ram_avail: float, disk: float,
                   temp: float = None, top_procs: str = ""):
        self._conn.execute(
            """INSERT INTO health_history
               (cpu_percent, ram_percent, ram_available_mb, disk_percent, cpu_temp, top_processes, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cpu, ram, ram_avail, disk, temp, top_procs, datetime.now().isoformat()),
        )
        cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
        self._conn.execute("DELETE FROM health_history WHERE timestamp < ?", (cutoff,))
        self._conn.commit()

    # ─── Suggestion Log ──────────────────────────────────────────────

    def log_suggestion(self, pattern_hash: str, description: str, accepted: bool = False):
        self._conn.execute(
            "INSERT INTO suggestion_log (pattern_hash, pattern_description, was_accepted, suggested_at) VALUES (?,?,?,?)",
            (pattern_hash, description, int(accepted), datetime.now().isoformat()),
        )
        self._conn.commit()

    def was_pattern_suggested(self, pattern_hash: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM suggestion_log WHERE pattern_hash=?", (pattern_hash,)).fetchone()
        return row is not None

    # ─── Maintenance ──────────────────────────────────────────────────

    def maybe_vacuum(self):
        """Run VACUUM if it hasn't been run in 7 days."""
        last = self.get_config("last_vacuum_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (datetime.now() - last_dt).days < 7:
                    return
            except ValueError:
                pass
        logger.info("Storage: running VACUUM")
        self._conn.execute("VACUUM")
        self.set_config("last_vacuum_at", datetime.now().isoformat())

    # ─── System 1 & 2 Setup ──────────────────────────────────────────

    def _populate_defaults(self):
        """Pre-populate app_categories and linux_knowledge with default records."""
        # Check if app_categories is empty
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM app_categories")
        if cursor.fetchone()[0] == 0:
            default_categories = [
                ("code.exe", "ELECTRON"), ("photoshop.exe", "GPU_RENDERED"),
                ("blender.exe", "GPU_RENDERED"), ("proteus.exe", "GPU_RENDERED"),
                ("autocad.exe", "GPU_RENDERED"), ("chrome.exe", "WEB_APP"),
                ("firefox.exe", "WEB_APP"), ("msedge.exe", "WEB_APP"),
                ("WINWORD.EXE", "STANDARD_WIN32"), ("EXCEL.EXE", "STANDARD_WIN32"),
                ("POWERPNT.EXE", "STANDARD_WIN32"), ("notepad.exe", "STANDARD_WIN32"),
                ("notepad++.exe", "STANDARD_WIN32"), ("devenv.exe", "STANDARD_WIN32"),
                ("cmd.exe", "TERMINAL"), ("WindowsTerminal.exe", "TERMINAL"),
                ("powershell.exe", "TERMINAL"), ("wsl.exe", "TERMINAL"),
                ("ubuntu.exe", "TERMINAL"), ("idea64.exe", "ELECTRON"),
                ("pycharm64.exe", "ELECTRON"), ("unity.exe", "GPU_RENDERED"),
                ("godot.exe", "GPU_RENDERED")
            ]
            cursor.executemany("INSERT INTO app_categories (exe_name, category) VALUES (?, ?)", default_categories)
            self._conn.commit()
            logger.info("Populated app_categories with default data.")

        # Check if transform_prompts is empty
        cursor.execute("SELECT COUNT(*) FROM transform_prompts")
        if cursor.fetchone()[0] == 0:
            default_transforms = [
                ("formalize", "Rewrite the following text to sound professional, formal, and polite. Keep the core meaning exactly the same. Do not add any filler or introduction."),
                ("simplify", "Rewrite the following text so it is extremely simple and easy to understand. Use plain English and short sentences. Do not add any filler."),
                ("bullet_points", "Convert the following text into a concise list of bullet points highlighting the main ideas. Do not add an introduction."),
                ("action_items", "Extract all actionable tasks or action items from the following text and list them clearly as bullet points. If none exist, output 'No action items found.'"),
                ("email_format", "Format the following text into a professional email structure with a subject line, greeting, body, and sign-off. Use placeholders like [Name] if details are missing."),
                ("summarize", "Provide a brief, one-paragraph summary of the following text that captures the main ideas without losing context."),
                ("expand", "Expand the following text by adding more detail, elaboration, and descriptive language while keeping the original intent intact."),
                ("shorten", "Shorten the following text by removing fluff and redundant words. Make it as concise as possible while retaining the original meaning.")
            ]
            cursor.executemany("INSERT INTO transform_prompts (trigger_name, prompt_text) VALUES (?, ?)", default_transforms)
            self._conn.commit()
            logger.info("Populated transform_prompts with default data.")

        # Check if linux_knowledge is empty
        cursor.execute("SELECT COUNT(*) FROM linux_knowledge")
        if cursor.fetchone()[0] == 0:
            default_linux = [
                ("File System Navigation", "basics", "What is the Linux file system structure (/, /home, /etc, /var, /usr, /tmp, /dev, /proc)", "beginner", "ls /", ""),
                ("File System Navigation", "pwd", "pwd - print working directory", "beginner", "pwd", "/home/user"),
                ("File System Navigation", "ls", "ls, ls -la, ls -lh - list files with explanations of each flag", "beginner", "ls -la", "total 0"),
                ("File System Navigation", "cd", "cd, cd .., cd ~, cd / - changing directories", "beginner", "cd /etc", ""),
                ("File System Navigation", "find", "find, locate - finding files", "intermediate", "find / -name '*.txt'", ""),
                ("File Operations", "cp/mv/rm", "cp, mv, rm, rm -rf (with warning), mkdir, rmdir", "beginner", "mkdir test", ""),
                ("File Operations", "touch", "touch - creating empty files", "beginner", "touch file.txt", ""),
                ("File Operations", "cat", "cat, less, more, head, tail - reading files", "beginner", "cat file.txt", ""),
                ("File Operations", "nano", "nano, vim basics", "beginner", "nano file.txt", ""),
                ("File Operations", "chmod", "File permissions: chmod, chown, rwx explained visually", "intermediate", "chmod 755 script.sh", ""),
                ("Package Management", "apt", "Ubuntu/Debian: apt update, apt upgrade, apt install", "beginner", "sudo apt update", ""),
                ("Package Management", "pacman", "Arch: pacman -Syu, pacman -S, pacman -R, yay", "beginner", "sudo pacman -Syu", ""),
                ("Package Management", "dnf", "Fedora/RHEL: dnf install, dnf update", "beginner", "sudo dnf update", ""),
                ("Process Management", "ps", "ps aux - what processes are running", "intermediate", "ps aux", ""),
                ("Process Management", "top", "top, htop - live process monitor", "beginner", "htop", ""),
                ("Process Management", "kill", "kill, killall - stopping processes", "intermediate", "killall firefox", ""),
                ("Networking", "ping", "ping, curl, wget", "beginner", "ping google.com", ""),
                ("Networking", "ssh", "ssh basics - connecting to remote machines", "intermediate", "ssh user@host", ""),
                ("Text Processing", "grep", "grep - searching text", "intermediate", "grep 'error' log.txt", ""),
                ("Text Processing", "pipe", "| pipe operator explained", "intermediate", "ls -l | grep 'txt'", ""),
                ("Shell Basics", "history", "Shell history: history, ctrl+r reverse search", "beginner", "history", ""),
                ("System Information", "uname", "uname -a - kernel info", "beginner", "uname -a", ""),
                ("System Information", "df", "df -h - disk space", "beginner", "df -h", ""),
                ("Permissions", "sudo", "sudo vs su", "beginner", "sudo su", ""),
                ("Beginner Mistakes", "rm -rf /", "What it does and why it's dangerous", "beginner", "rm -rf /", "DO NOT RUN")
            ]
            cursor.executemany("INSERT INTO linux_knowledge (topic, subtopic, content, difficulty, example_command, example_output) VALUES (?, ?, ?, ?, ?, ?)", default_linux)
            self._conn.commit()
            logger.info("Populated linux_knowledge with default data.")

        # Check if stt_corrections is empty
        cursor.execute("SELECT COUNT(*) FROM stt_corrections")
        if cursor.fetchone()[0] == 0:
            default_stt_corrections = [
                ("produce", "Proteus"), ("prot", "Proteus"), ("protest", "Proteus"),
                ("arduino", "Arduino"), ("pie charm", "PyCharm"), 
                ("pie torch", "PyTorch"), ("tensor flow", "TensorFlow"), 
                ("jupiter", "Jupyter"), ("get hub", "GitHub"), 
                ("vs code", "VS Code"), ("pi side", "PySide"), 
                ("sequel", "SQL"), ("my sequel", "MySQL"), ("postgress", "PostgreSQL")
            ]
            cursor.executemany("INSERT INTO stt_corrections (misheard, correct) VALUES (?, ?)", default_stt_corrections)
            self._conn.commit()
            logger.info("Populated stt_corrections with default data.")

    # ─── STT Corrections ──────────────────────────────────────────────

    def get_stt_corrections(self) -> dict[str, dict]:
        """Get all STT corrections as {misheard: {"correct": str, "context": list|None}}."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT misheard, correct, apply_when_preceded_by FROM stt_corrections")
        result = {}
        for row in cursor.fetchall():
            context = None
            if row["apply_when_preceded_by"]:
                try:
                    context = json.loads(row["apply_when_preceded_by"])
                except Exception:
                    pass
            result[row["misheard"]] = {"correct": row["correct"], "context": context}
        return result

    def add_stt_correction(self, misheard: str, correct: str, context_words: list[str] = None) -> bool:
        """Add or update an STT correction with optional context words."""
        misheard = misheard.strip().lower()
        context_json = json.dumps(context_words) if context_words else None
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO stt_corrections (misheard, correct, apply_when_preceded_by) VALUES (?, ?, ?)",
                (misheard, correct.strip(), context_json)
            )
            self._conn.commit()
            logger.info(f"Storage: added STT correction '{misheard}' -> '{correct}' (context={context_words})")
            return True
        except sqlite3.Error as e:
            logger.error(f"Storage: failed to add STT correction: {e}")
            return False

    # ─── System 2 Helper Methods ──────────────────────────────────────

    def get_linux_knowledge(self, topic: str, subtopic: str = None, distro: str = None) -> list[dict]:
        """Fetch Linux knowledge matching the criteria."""
        query = "SELECT * FROM linux_knowledge WHERE topic = ?"
        params = [topic]
        if subtopic:
            query += " AND subtopic = ?"
            params.append(subtopic)
        if distro:
            query += " AND (distro IS NULL OR distro = ?)"
            params.append(distro)
        
        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def save_linux_command(self, command: str, output: str, exit_code: int):
        """Save a command executed in WSL to history."""
        preview = output[:500] if output else ""
        self._conn.execute(
            "INSERT INTO linux_command_history (command, output_preview, exit_code, executed_at) VALUES (?, ?, ?, ?)",
            (command, preview, exit_code, datetime.now().isoformat())
        )
        self._conn.commit()

    def get_command_history(self, limit: int = 50) -> list[dict]:
        """Fetch recent Linux command history."""
        cursor = self._conn.execute(
            "SELECT * FROM linux_command_history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self._conn.close()


    # ─── System 3: Suggestion & Transform Helpers ─────────────────────

    def get_action_history_recent(self, days: int = 30) -> list[dict]:
        """Fetch action history for the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM action_history WHERE executed_at >= ? ORDER BY executed_at ASC",
            (cutoff,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_suggestion_outcome(self, pattern_hash: str) -> dict:
        """Check if a suggestion was already made for a pattern hash."""
        row = self._conn.execute(
            "SELECT * FROM suggestion_log WHERE pattern_hash = ?",
            (pattern_hash,)
        ).fetchone()
        return dict(row) if row else None

    def save_suggestion_outcome(self, pattern_hash: str, description: str, was_accepted: bool):
        """Save the outcome of a proactive suggestion."""
        self._conn.execute(
            "INSERT INTO suggestion_log (pattern_hash, pattern_description, was_accepted, suggested_at) VALUES (?, ?, ?, ?)",
            (pattern_hash, description, 1 if was_accepted else 0, datetime.now().isoformat())
        )
        self._conn.commit()

    def get_transform_prompt(self, trigger_name: str) -> str:
        """Fetch the prompt text for a given transform trigger."""
        row = self._conn.execute(
            "SELECT prompt_text FROM transform_prompts WHERE trigger_name = ?",
            (trigger_name,)
        ).fetchone()
        return row["prompt_text"] if row else ""

    def save_transform_history(self, original: str, transformed: str, transform_type: str):
        """Save a text transformation to history."""
        self._conn.execute(
            "INSERT INTO transform_history (original_text, transformed_text, transform_type, created_at) VALUES (?, ?, ?, ?)",
            (original, transformed, transform_type, datetime.now().isoformat())
        )
        self._conn.commit()
        # Keep only the last 10 transformations
        self._conn.execute(
            "DELETE FROM transform_history WHERE id NOT IN (SELECT id FROM transform_history ORDER BY id DESC LIMIT 10)"
        )
        self._conn.commit()

    def get_last_transform(self) -> dict:
        """Get the most recent transformation for undo purposes."""
        row = self._conn.execute(
            "SELECT * FROM transform_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ─── Workspace Snapshots ──────────────────────────────────────────

    def save_workspace_snapshot(self, name: str, snapshot_json: str):
        """Save or update a workspace snapshot."""
        self._conn.execute(
            """INSERT OR REPLACE INTO workspace_snapshots (name, snapshot_json, created_at)
               VALUES (?, ?, ?)""",
            (name, snapshot_json, datetime.now().isoformat())
        )
        self._conn.commit()

    def get_workspace_snapshot(self, name: str) -> Optional[dict]:
        """Retrieve a workspace snapshot by name."""
        row = self._conn.execute(
            "SELECT * FROM workspace_snapshots WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_workspace_snapshots(self) -> list[dict]:
        """List all saved workspace snapshots."""
        cursor = self._conn.execute(
            "SELECT id, name, created_at FROM workspace_snapshots ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_workspace_snapshot(self, name: str) -> bool:
        """Delete a workspace snapshot by name. Returns True if deleted."""
        cursor = self._conn.execute("DELETE FROM workspace_snapshots WHERE name = ?", (name,))
        self._conn.commit()
        return cursor.rowcount > 0


# ─── Singleton Instance ──────────────────────────────────────────────
_db_instance = None


def get_db(db_path: Path = DB_PATH) -> ClickyDatabase:
    """Get the global database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = ClickyDatabase(db_path)
    return _db_instance
