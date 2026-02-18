"""
One-time migration: copy telemetry events from SQLite to PostgreSQL.

Usage (from project root):
    python scripts/migrate_sqlite_to_pg.py
"""

import sqlite3
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = PROJECT_ROOT / "backend" / "instance" / "telemetry.db"
PG_DSN = "postgresql://telemetry_user:telemetry_pass@localhost:5432/telemetry_db"

BATCH_SIZE = 5000


def main():
    if not SQLITE_PATH.exists():
        print(f"SQLite database not found at {SQLITE_PATH}")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(str(SQLITE_PATH))
    sqlite_conn.row_factory = sqlite3.Row
    cur = sqlite_conn.cursor()

    cur.execute("SELECT COUNT(*) FROM telemetry_events")
    total = cur.fetchone()[0]
    print(f"Source: {total} events in SQLite")

    pg_conn = psycopg2.connect(PG_DSN)
    pg_cur = pg_conn.cursor()

    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_events (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(128) NOT NULL DEFAULT 'default',
            timestamp TIMESTAMP NOT NULL,
            app_name VARCHAR(256) NOT NULL DEFAULT 'unknown',
            window_title VARCHAR(1024) NOT NULL DEFAULT '',
            keystroke_count INTEGER NOT NULL DEFAULT 0,
            mouse_clicks INTEGER NOT NULL DEFAULT 0,
            mouse_distance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            idle_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            distraction_visible BOOLEAN NOT NULL DEFAULT false
        )
    """)
    pg_cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_telemetry_events_timestamp
        ON telemetry_events (timestamp)
    """)
    pg_cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_telemetry_events_user_id
        ON telemetry_events (user_id)
    """)
    pg_conn.commit()

    cur.execute("""
        SELECT id, user_id, timestamp, app_name, window_title,
               keystroke_count, mouse_clicks, mouse_distance,
               idle_seconds, distraction_visible
        FROM telemetry_events
        ORDER BY id
    """)

    migrated = 0
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            break

        values = []
        for r in rows:
            values.append((
                r["user_id"] if r["user_id"] else "default",
                r["timestamp"],
                r["app_name"] or "unknown",
                r["window_title"] or "",
                r["keystroke_count"] or 0,
                r["mouse_clicks"] or 0,
                r["mouse_distance"] or 0.0,
                r["idle_seconds"] or 0.0,
                bool(r["distraction_visible"]) if r["distraction_visible"] is not None else False,
            ))

        execute_values(
            pg_cur,
            """
            INSERT INTO telemetry_events
                (user_id, timestamp, app_name, window_title,
                 keystroke_count, mouse_clicks, mouse_distance,
                 idle_seconds, distraction_visible)
            VALUES %s
            """,
            values,
        )
        pg_conn.commit()
        migrated += len(values)
        print(f"  Migrated {migrated}/{total} events ...", flush=True)

    pg_cur.execute("SELECT COUNT(*) FROM telemetry_events")
    pg_total = pg_cur.fetchone()[0]
    print(f"\nDone. PostgreSQL now has {pg_total} events.")

    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
