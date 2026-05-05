from __future__ import annotations

"""Migrate the local SQLite database to PostgreSQL/Neon.

Usage from the project root:

    # Option A: use environment variable
    set DATABASE_URL=postgresql://USER:PASSWORD@HOST/neondb?sslmode=require
    set DB_BACKEND=postgres
    python scripts/migrate_sqlite_to_postgres.py --sqlite data/fuel_control.db

    # Option B: use .streamlit/secrets.toml with DATABASE_URL and DB_BACKEND=postgres
    python scripts/migrate_sqlite_to_postgres.py --sqlite data/fuel_control.db

The script creates/updates the PostgreSQL schema using modules.db.init_db(),
truncates the target tables, loads the SQLite rows preserving IDs, and resets
PostgreSQL sequences.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import psycopg2
    import psycopg2.extras
except Exception as exc:  # pragma: no cover
    raise SystemExit("Falta psycopg2-binary. Ejecuta: pip install psycopg2-binary") from exc

from modules.db import get_database_url, init_db

TABLES_IN_LOAD_ORDER = [
    "unidades",
    "checklist_unidad",
    "conductores",
    "app_users",
    "cargas_combustible",
    "destinos",
    "rutas",
    "ruta_entregas",
    "ruta_entrega_evidencias",
    "gps_importaciones",
    "gps_movimientos",
    "gps_paradas",
    "entrega_gps_match",
    "gps_paradas_clasificacion",
    "archivos_adjuntos",
    "gastos_operativos",
    "auditoria_eventos",
    "auditoria_cambios",
]


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r[0]) for r in rows}


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def pg_columns(pg_conn, table: str) -> list[str]:
    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [r["column_name"] for r in cur.fetchall()]


def table_exists_pg(pg_conn, table: str) -> bool:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def reset_sequence(pg_conn, table: str) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
        seq = cur.fetchone()[0]
        if not seq:
            return
        cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
        max_id = cur.fetchone()[0] or 0
        if max_id <= 0:
            cur.execute("SELECT setval(%s, 1, false)", (seq,))
        else:
            cur.execute("SELECT setval(%s, %s, true)", (seq, max_id))


def migrate(sqlite_path: Path, database_url: str) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"No existe SQLite: {sqlite_path}")

    # Force app schema creation against PostgreSQL.
    os.environ["DB_BACKEND"] = "postgres"
    os.environ["DATABASE_URL"] = database_url
    init_db()

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    source_tables = sqlite_tables(sqlite_conn)

    pg_conn = psycopg2.connect(database_url)
    try:
        with pg_conn.cursor() as cur:
            existing = [t for t in TABLES_IN_LOAD_ORDER if table_exists_pg(pg_conn, t)]
            if existing:
                cur.execute("TRUNCATE " + ", ".join(existing) + " RESTART IDENTITY CASCADE")
        pg_conn.commit()

        for table in TABLES_IN_LOAD_ORDER:
            if table not in source_tables:
                print(f"[SKIP] {table}: no existe en SQLite")
                continue
            if not table_exists_pg(pg_conn, table):
                print(f"[SKIP] {table}: no existe en PostgreSQL")
                continue

            src_cols = sqlite_columns(sqlite_conn, table)
            dst_cols = pg_columns(pg_conn, table)
            cols = [c for c in src_cols if c in dst_cols]
            if not cols:
                print(f"[SKIP] {table}: sin columnas compatibles")
                continue

            rows = sqlite_conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            if not rows:
                print(f"[OK] {table}: 0 filas")
                continue

            values = [tuple(row[c] for c in cols) for row in rows]
            insert_sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
            with pg_conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, insert_sql, values, page_size=500)
            pg_conn.commit()
            print(f"[OK] {table}: {len(rows)} filas")

        for table in TABLES_IN_LOAD_ORDER:
            if table_exists_pg(pg_conn, table):
                reset_sequence(pg_conn, table)
        pg_conn.commit()
        print("Migración terminada correctamente.")
    finally:
        sqlite_conn.close()
        pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="data/fuel_control.db", help="Ruta al archivo SQLite local")
    parser.add_argument("--database-url", default=None, help="Connection string PostgreSQL/Neon")
    args = parser.parse_args()

    url = args.database_url or os.getenv("DATABASE_URL") or get_database_url()
    if not url:
        raise SystemExit("No hay DATABASE_URL. Pásalo por --database-url o secrets/env.")
    migrate(Path(args.sqlite), url)


if __name__ == "__main__":
    main()
