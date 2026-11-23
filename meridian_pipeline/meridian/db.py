from pathlib import Path

import psycopg2

SQL_DIR = Path(__file__).resolve().parent / "sql"


def connect(dsn):
    return psycopg2.connect(dsn)


def apply_schema(conn):
    """Create tables/views if missing. Safe to call on every run."""
    with conn.cursor() as cur:
        cur.execute((SQL_DIR / "01_schema.sql").read_text(encoding="utf-8"))
        cur.execute((SQL_DIR / "03_views.sql").read_text(encoding="utf-8"))
    if not conn.autocommit:
        conn.commit()


def reset_all(conn):
    """Throw everything away (used by --reset and by tests)."""
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS raw CASCADE; DROP SCHEMA IF EXISTS staging CASCADE; "
                    "DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    if not conn.autocommit:
        conn.commit()


def final_build_sql():
    return (SQL_DIR / "02_final_build.sql").read_text(encoding="utf-8")
