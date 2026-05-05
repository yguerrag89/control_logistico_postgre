from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

try:
    import streamlit as st
except Exception:  # pragma: no cover - scripts can run without Streamlit context
    st = None

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
except Exception:  # psycopg2 is only required when DB_BACKEND=postgres
    psycopg2 = None

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "fuel_control.db"
TICKETS_DIR = DATA_DIR / "tickets"
EXPORTS_DIR = DATA_DIR / "exports"
EVIDENCIAS_DIR = DATA_DIR / "evidencias"
GPS_UPLOADS_DIR = DATA_DIR / "gps_uploads"

_PG_POOL = None
_PG_POOL_DSN = None
_PG_POOL_LOCK = threading.Lock()


def _pg_pool_size() -> tuple[int, int]:
    try:
        max_conn = int(_secret_or_env("DB_POOL_MAX", "5") or "5")
    except Exception:
        max_conn = 5
    max_conn = max(1, min(max_conn, 20))
    return 1, max_conn


def _get_pg_pool(dsn: str):
    """Return a process-level psycopg2 pool for Neon/PostgreSQL.

    Streamlit reruns the script frequently. Opening a fresh connection for every
    small query makes the app feel slow, especially with serverless Postgres.
    A small pool reuses connections while keeping the existing sqlite-like
    wrapper API.
    """
    global _PG_POOL, _PG_POOL_DSN
    if psycopg2 is None:
        raise RuntimeError("psycopg2-binary no está instalado. Agrégalo a requirements.txt.")
    with _PG_POOL_LOCK:
        if _PG_POOL is None or _PG_POOL_DSN != dsn:
            minconn, maxconn = _pg_pool_size()
            _PG_POOL = psycopg2.pool.SimpleConnectionPool(
                minconn,
                maxconn,
                dsn,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
            _PG_POOL_DSN = dsn
        return _PG_POOL


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "si", "sí", "on"}


def _use_python_pool() -> bool:
    """Client-side pooling is optional.

    Neon already provides a pooler when the host contains `-pooler`. In
    Streamlit Cloud, keeping a client-side psycopg2 pool can exhaust the app
    process pool when reruns overlap or a connection is not returned after an
    exception. Defaulting to direct connections is safer for this MVP. If you
    later want to re-enable the Python pool, set DB_USE_PYTHON_POOL=true and
    DB_POOL_MAX=10/20 in Secrets.
    """
    return _truthy(_secret_or_env("DB_USE_PYTHON_POOL", "false"))


def reset_pg_pool() -> None:
    """Close and reset the local psycopg2 pool. Useful after pool errors."""
    global _PG_POOL, _PG_POOL_DSN
    with _PG_POOL_LOCK:
        if _PG_POOL is not None:
            try:
                _PG_POOL.closeall()
            except Exception:
                pass
        _PG_POOL = None
        _PG_POOL_DSN = None


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCIAS_DIR.mkdir(parents=True, exist_ok=True)
    GPS_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _secret_or_env(name: str, default: str | None = None) -> str | None:
    """Read a setting from Streamlit secrets first, then environment variables."""
    if st is not None:
        try:
            value = st.secrets.get(name)  # type: ignore[attr-defined]
            if value is not None:
                return str(value)
        except Exception:
            pass
    return os.getenv(name, default)


def get_db_backend() -> str:
    return (_secret_or_env("DB_BACKEND", "sqlite") or "sqlite").strip().lower()


def is_postgres_backend() -> bool:
    return get_db_backend() in {"postgres", "postgresql", "neon"}


def get_database_url() -> str | None:
    return _secret_or_env("DATABASE_URL")


def _convert_sqlite_sql_to_postgres(sql: str) -> str:
    """Small compatibility translator for the existing SQLite-style queries.

    The app was developed with sqlite3 placeholders (`?`) and SQLite DDL
    (`AUTOINCREMENT`).  This function keeps the existing repositories usable
    against PostgreSQL/Neon without rewriting every query at once.
    """
    sql = sql.strip()
    if not sql:
        return sql
    # Ignore SQLite pragmas in Postgres.
    if sql.upper().startswith("PRAGMA"):
        return ""
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = sql.replace("AUTOINCREMENT", "")
    # sqlite3 placeholders -> psycopg2 placeholders.
    sql = sql.replace("?", "%s")
    return sql



class RowCompat:
    """Dict-like row that also supports numeric indexing like sqlite3.Row.

    psycopg2 RealDictCursor returns rows that do not support row[0].
    Some legacy SQLite code in the app still uses positional access for
    aggregate queries, so this wrapper keeps PostgreSQL and SQLite behavior
    compatible while preserving dict(row), row["col"] and row.get().
    """

    def __init__(self, data: Any, columns: list[str] | None = None):
        self._data = dict(data or {})
        self._columns = columns or list(self._data.keys())

    def __getitem__(self, key: Any):
        if isinstance(key, int):
            key = self._columns[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    def get(self, key: Any, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def __repr__(self) -> str:
        return repr(self._data)


def _pg_columns(cursor: Any) -> list[str]:
    try:
        return [d.name if hasattr(d, "name") else d[0] for d in (cursor.description or [])]
    except Exception:
        return []


def _wrap_pg_row(row: Any, cursor: Any):
    if row is None:
        return None
    if isinstance(row, RowCompat):
        return row
    try:
        # RealDictRow/dict path.
        data = dict(row)
        return RowCompat(data, _pg_columns(cursor))
    except Exception:
        return row

class PgCursorCompat:
    def __init__(self, cursor: Any, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return getattr(self._cursor, "rowcount", -1)

    def fetchone(self):
        return _wrap_pg_row(self._cursor.fetchone(), self._cursor)

    def fetchall(self):
        return [_wrap_pg_row(r, self._cursor) for r in self._cursor.fetchall()]


class PgConnectionCompat:
    """Minimal sqlite3-like wrapper over psycopg2 for this app.

    It supports the subset used by the codebase: context manager, execute,
    executescript, commit, rollback, close and cursor-like fetch methods.
    """

    is_postgres = True

    def __init__(self, dsn: str):
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary no está instalado. Agrégalo a requirements.txt.")
        self._closed = False
        self._pool = None
        self._from_pool = False

        if _use_python_pool():
            try:
                self._pool = _get_pg_pool(dsn)
                self._conn = self._pool.getconn()
                self._from_pool = True
            except Exception:
                # If the local pool is exhausted or broken, reset it once and retry.
                reset_pg_pool()
                self._pool = _get_pg_pool(dsn)
                self._conn = self._pool.getconn()
                self._from_pool = True
        else:
            # Safer default for Streamlit Cloud + Neon pooler. Each `with` block
            # opens/closes a connection, while Neon pooler handles server-side pooling.
            self._conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> PgCursorCompat:
        pg_sql = _convert_sqlite_sql_to_postgres(sql)
        cur = self._conn.cursor()
        if not pg_sql:
            return PgCursorCompat(cur)
        params = tuple(params or ())
        lastrowid = None
        sql_for_exec = pg_sql
        is_insert = pg_sql.lstrip().upper().startswith("INSERT")
        needs_id = is_insert and " RETURNING " not in pg_sql.upper() and " ON CONFLICT " not in pg_sql.upper()
        if needs_id:
            sql_for_exec = pg_sql.rstrip().rstrip(";") + " RETURNING id"
        if params:
            cur.execute(sql_for_exec, params)
        else:
            cur.execute(sql_for_exec)
        if needs_id:
            row = _wrap_pg_row(cur.fetchone(), cur)
            if row and row.get("id") is not None:
                lastrowid = int(row["id"])
        return PgCursorCompat(cur, lastrowid)

    def executescript(self, script: str) -> None:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            self.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            if getattr(self, "_from_pool", False) and self._pool is not None:
                self._pool.putconn(self._conn)
            else:
                self._conn.close()
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        # Defensive close in case a legacy code path creates a connection without
        # a `with` block or an exception interrupts normal cleanup.
        try:
            self.close()
        except Exception:
            pass


def get_connection():
    if is_postgres_backend():
        url = get_database_url()
        if not url:
            raise RuntimeError("DB_BACKEND=postgres pero DATABASE_URL no está configurado en secrets.")
        return PgConnectionCompat(url)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _is_postgres_conn(conn: Any) -> bool:
    return bool(getattr(conn, "is_postgres", False))


def _table_columns(conn: Any, table: str) -> set[str]:
    if _is_postgres_conn(conn):
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """.replace("%s", "?"),
            (table,),
        ).fetchall()
        return {str(r["column_name"]) for r in rows}
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def _create_performance_indexes(conn: Any) -> None:
    """Indexes used by the operational dashboards.

    These are safe to run at every startup because of IF NOT EXISTS. They are
    especially important in Neon/PostgreSQL once GPS data grows month by month.
    """
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_unidades_activo ON unidades(activo)",
        "CREATE INDEX IF NOT EXISTS idx_conductores_activo ON conductores(activo)",
        "CREATE INDEX IF NOT EXISTS idx_cargas_unidad_fecha ON cargas_combustible(unidad_id, fecha_carga)",
        "CREATE INDEX IF NOT EXISTS idx_cargas_fecha_activo ON cargas_combustible(fecha_carga, activo)",
        "CREATE INDEX IF NOT EXISTS idx_cargas_calidad ON cargas_combustible(calidad_registro)",
        "CREATE INDEX IF NOT EXISTS idx_rutas_fecha_unidad2 ON rutas(fecha, unidad_id)",
        "CREATE INDEX IF NOT EXISTS idx_rutas_tipo_activo2 ON rutas(tipo_ruta, activo)",
        "CREATE INDEX IF NOT EXISTS idx_rutas_conductor_fecha ON rutas(conductor_id, fecha)",
        "CREATE INDEX IF NOT EXISTS idx_entregas_ruta_activo ON ruta_entregas(ruta_id, activo)",
        "CREATE INDEX IF NOT EXISTS idx_entregas_estado_gps ON ruta_entregas(estado_conciliacion_gps)",
        "CREATE INDEX IF NOT EXISTS idx_entregas_destino ON ruta_entregas(destino_id)",
        "CREATE INDEX IF NOT EXISTS idx_gps_import_activo ON gps_importaciones(activo)",
        "CREATE INDEX IF NOT EXISTS idx_gps_import_unidad_periodo ON gps_importaciones(unidad_id, anio, mes)",
        "CREATE INDEX IF NOT EXISTS idx_gps_mov_unidad_fecha2 ON gps_movimientos(unidad_id, fecha)",
        "CREATE INDEX IF NOT EXISTS idx_gps_mov_fecha ON gps_movimientos(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_gps_mov_inicio ON gps_movimientos(inicio_datetime)",
        "CREATE INDEX IF NOT EXISTS idx_gps_mov_importacion ON gps_movimientos(importacion_id)",
        "CREATE INDEX IF NOT EXISTS idx_gps_paradas_unidad_fecha2 ON gps_paradas(unidad_id, fecha)",
        "CREATE INDEX IF NOT EXISTS idx_gps_paradas_fecha ON gps_paradas(fecha)",
        "CREATE INDEX IF NOT EXISTS idx_gps_paradas_inicio ON gps_paradas(inicio_gps)",
        "CREATE INDEX IF NOT EXISTS idx_gps_paradas_duracion ON gps_paradas(duracion_seg)",
        "CREATE INDEX IF NOT EXISTS idx_gps_paradas_importacion ON gps_paradas(importacion_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_entrega ON entrega_gps_match(entrega_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_parada ON entrega_gps_match(gps_parada_id)",
        "CREATE INDEX IF NOT EXISTS idx_clasif_parada_activa ON gps_paradas_clasificacion(gps_parada_id, activo)",
        "CREATE INDEX IF NOT EXISTS idx_destinos_activo_validado ON destinos(activo, validado)",
        "CREATE INDEX IF NOT EXISTS idx_destinos_excluir ON destinos(excluir_alertas_inactividad)",
        "CREATE INDEX IF NOT EXISTS idx_archivos_origen2 ON archivos_adjuntos(tabla_origen, registro_id)",
        "CREATE INDEX IF NOT EXISTS idx_gastos_fecha_unidad2 ON gastos_operativos(fecha, unidad_id)",
    ]
    for sql in indexes:
        try:
            conn.execute(sql)
        except Exception:
            # An index should never prevent the app from starting.
            # PostgreSQL/SQLite differences are handled best-effort here.
            pass
    try:
        conn.commit()
    except Exception:
        pass


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS unidades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                placas TEXT UNIQUE NOT NULL,
                marca TEXT,
                modelo TEXT,
                color TEXT,
                tipo_unidad TEXT,
                combustible_preferido TEXT,
                tipo_carga TEXT,
                carga_garrafones TEXT,
                periodo_habil TEXT,
                limite_litros REAL,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT
            );

            CREATE TABLE IF NOT EXISTS checklist_unidad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unidad_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                valor TEXT,
                FOREIGN KEY (unidad_id) REFERENCES unidades(id)
            );

            CREATE TABLE IF NOT EXISTS conductores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cargas_combustible (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unidad_id INTEGER NOT NULL,
                conductor_id INTEGER,
                fecha_carga TEXT NOT NULL,
                hora_carga TEXT,
                gasolinera TEXT,
                estacion_direccion TEXT,
                ticket_folio TEXT,
                tipo_combustible TEXT,
                precio_litro REAL NOT NULL,
                litros REAL NOT NULL,
                importe_total REAL NOT NULL,
                kilometraje INTEGER,
                metodo_pago TEXT,
                observaciones TEXT,
                imagen_ticket_path TEXT,
                ocr_texto TEXT,
                origen_registro TEXT DEFAULT 'manual',
                estado_validacion TEXT DEFAULT 'VALIDADO',
                alerta_resumen TEXT,
                tipo_carga_combustible TEXT DEFAULT 'No especificada',
                calidad_registro TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT,
                FOREIGN KEY (unidad_id) REFERENCES unidades(id),
                FOREIGN KEY (conductor_id) REFERENCES conductores(id)
            );


            CREATE TABLE IF NOT EXISTS destinos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre_normalizado TEXT NOT NULL,
                alias TEXT,
                tipo_destino TEXT,
                cliente_asociado TEXT,
                direccion_texto TEXT,
                latitud REAL,
                longitud REAL,
                validado INTEGER NOT NULL DEFAULT 0,
                fuente TEXT DEFAULT 'captura_manual',
                observaciones TEXT,
                excluir_alertas_inactividad INTEGER NOT NULL DEFAULT 0,
                radio_metros REAL DEFAULT 100,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT
            );

            CREATE TABLE IF NOT EXISTS rutas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                unidad_id INTEGER NOT NULL,
                conductor_id INTEGER NOT NULL,
                hora_salida_reportada TEXT,
                hora_regreso_reportada TEXT,
                estado_ruta TEXT NOT NULL DEFAULT 'Abierta',
                tipo_ruta TEXT NOT NULL DEFAULT 'OPERATIVA',
                observaciones_generales TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                motivo_anulacion TEXT,
                anulado_en TEXT,
                anulado_por TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT,
                FOREIGN KEY (unidad_id) REFERENCES unidades(id),
                FOREIGN KEY (conductor_id) REFERENCES conductores(id)
            );

            CREATE TABLE IF NOT EXISTS ruta_entregas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ruta_id INTEGER NOT NULL,
                cliente_nombre TEXT NOT NULL,
                destino_nombre TEXT NOT NULL,
                destino_id INTEGER,
                hora_llegada_reportada TEXT NOT NULL,
                hora_captura_sistema TEXT DEFAULT CURRENT_TIMESTAMP,
                estatus_entrega TEXT NOT NULL,
                motivo_no_entrega TEXT,
                observaciones TEXT,
                orden_calculado INTEGER,
                estado_conciliacion_gps TEXT NOT NULL DEFAULT 'Pendiente de GPS',
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT,
                FOREIGN KEY (ruta_id) REFERENCES rutas(id)
            );

            CREATE TABLE IF NOT EXISTS ruta_entrega_evidencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entrega_id INTEGER NOT NULL,
                ruta_archivo TEXT NOT NULL,
                tipo_evidencia TEXT,
                comentario TEXT,
                estado_evidencia TEXT DEFAULT 'activo',
                motivo_anulacion TEXT,
                anulado_en TEXT,
                anulado_por TEXT,
                fecha_captura TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entrega_id) REFERENCES ruta_entregas(id)
            );

            CREATE TABLE IF NOT EXISTS gps_importaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archivo TEXT NOT NULL,
                hoja TEXT NOT NULL,
                unidad_id INTEGER,
                placas TEXT,
                mes INTEGER,
                anio INTEGER,
                tipo_hoja TEXT,
                km_resumen REAL,
                km_calculados REAL,
                diferencia_km REAL,
                tiempo_resumen_seg INTEGER,
                tiempo_calculado_seg INTEGER,
                diferencia_tiempo_seg INTEGER,
                movimientos_detectados INTEGER,
                inmovilizaciones_detectadas INTEGER,
                hash_movimientos TEXT,
                estado_validacion TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                motivo_anulacion TEXT,
                anulado_en TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (unidad_id) REFERENCES unidades(id)
            );

            CREATE TABLE IF NOT EXISTS gps_movimientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                importacion_id INTEGER NOT NULL,
                unidad_id INTEGER,
                placas TEXT,
                fecha TEXT,
                secuencia INTEGER,
                inicio_datetime TEXT,
                fin_datetime TEXT,
                km REAL,
                duracion_reportada_seg INTEGER,
                duracion_calculada_seg INTEGER,
                diferencia_duracion_seg INTEGER,
                velocidad_promedio_kmh REAL,
                origen TEXT,
                destino TEXT,
                flags_calidad TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (importacion_id) REFERENCES gps_importaciones(id),
                FOREIGN KEY (unidad_id) REFERENCES unidades(id)
            );

            CREATE TABLE IF NOT EXISTS gps_paradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                importacion_id INTEGER NOT NULL,
                movimiento_anterior_id INTEGER,
                unidad_id INTEGER,
                placas TEXT,
                fecha TEXT,
                inicio_gps TEXT,
                fin_gps TEXT,
                duracion_seg INTEGER,
                direccion_gps TEXT,
                latitud REAL,
                longitud REAL,
                clasificacion_inicial TEXT,
                requiere_revision INTEGER DEFAULT 0,
                es_previa_al_primer_movimiento INTEGER DEFAULT 0,
                texto_original TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (importacion_id) REFERENCES gps_importaciones(id),
                FOREIGN KEY (movimiento_anterior_id) REFERENCES gps_movimientos(id),
                FOREIGN KEY (unidad_id) REFERENCES unidades(id)
            );

            CREATE TABLE IF NOT EXISTS entrega_gps_match (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entrega_id INTEGER NOT NULL,
                gps_parada_id INTEGER NOT NULL,
                tipo_match TEXT NOT NULL,
                diferencia_min REAL,
                confianza REAL,
                hora_salida_inferida TEXT,
                tiempo_en_cliente_seg INTEGER,
                validado INTEGER DEFAULT 0,
                validado_por TEXT,
                validado_en TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entrega_id) REFERENCES ruta_entregas(id),
                FOREIGN KEY (gps_parada_id) REFERENCES gps_paradas(id)
            );

            CREATE TABLE IF NOT EXISTS gps_paradas_clasificacion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gps_parada_id INTEGER NOT NULL,
                clasificacion TEXT NOT NULL,
                comentario TEXT,
                clasificado_por TEXT,
                clasificado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                activo INTEGER NOT NULL DEFAULT 1,
                motivo_anulacion TEXT,
                anulado_en TEXT,
                anulado_por TEXT,
                FOREIGN KEY (gps_parada_id) REFERENCES gps_paradas(id)
            );

            CREATE TABLE IF NOT EXISTS archivos_adjuntos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tabla_origen TEXT NOT NULL,
                registro_id INTEGER NOT NULL,
                tipo_archivo TEXT NOT NULL,
                ruta_archivo TEXT NOT NULL,
                estado_archivo TEXT NOT NULL DEFAULT 'activo',
                motivo TEXT,
                comentario TEXT,
                usuario TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                anulado_en TEXT,
                anulado_por TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_rutas_fecha_unidad ON rutas(fecha, unidad_id);
            CREATE INDEX IF NOT EXISTS idx_entregas_ruta ON ruta_entregas(ruta_id);
            CREATE INDEX IF NOT EXISTS idx_gps_mov_unidad_fecha ON gps_movimientos(unidad_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_gps_paradas_unidad_fecha ON gps_paradas(unidad_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_gps_hash ON gps_importaciones(hash_movimientos);

            CREATE TABLE IF NOT EXISTS auditoria_eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tabla TEXT NOT NULL,
                registro_id INTEGER NOT NULL,
                accion TEXT NOT NULL,
                detalle TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS auditoria_cambios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tabla TEXT NOT NULL,
                registro_id INTEGER NOT NULL,
                campo TEXT,
                valor_anterior TEXT,
                valor_nuevo TEXT,
                accion TEXT NOT NULL,
                motivo TEXT,
                comentario TEXT,
                usuario TEXT,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP
            );
            '''
        )
        _migrate_optional_kilometraje(conn)
        _migrate_gps_import_status(conn)
        _migrate_destination_control_fields(conn)
        _migrate_v15_traceability(conn)
        _migrate_v16_operations(conn)
        _migrate_v110_route_cleanup(conn)
        _create_performance_indexes(conn)


def _migrate_optional_kilometraje(conn) -> None:
    """Allow cargas_combustible.kilometraje to be NULL in existing SQLite DBs.

    Older MVP versions created this column as NOT NULL. SQLite cannot drop a
    NOT NULL constraint with ALTER COLUMN, so we rebuild only this table when
    needed. Existing records are preserved.
    """
    if _is_postgres_conn(conn):
        return
    cols = conn.execute("PRAGMA table_info(cargas_combustible)").fetchall()
    if not cols:
        return
    km_col = next((dict(c) for c in cols if c["name"] == "kilometraje"), None)
    if not km_col or int(km_col.get("notnull", 0)) == 0:
        return

    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS cargas_combustible_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unidad_id INTEGER NOT NULL,
            conductor_id INTEGER,
            fecha_carga TEXT NOT NULL,
            hora_carga TEXT,
            gasolinera TEXT,
            estacion_direccion TEXT,
            ticket_folio TEXT,
            tipo_combustible TEXT,
            precio_litro REAL NOT NULL,
            litros REAL NOT NULL,
            importe_total REAL NOT NULL,
            kilometraje INTEGER,
            metodo_pago TEXT,
            observaciones TEXT,
            imagen_ticket_path TEXT,
            ocr_texto TEXT,
            origen_registro TEXT DEFAULT 'manual',
            estado_validacion TEXT DEFAULT 'VALIDADO',
            alerta_resumen TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
            actualizado_en TEXT,
            FOREIGN KEY (unidad_id) REFERENCES unidades(id),
            FOREIGN KEY (conductor_id) REFERENCES conductores(id)
        );

        INSERT INTO cargas_combustible_new (
            id, unidad_id, conductor_id, fecha_carga, hora_carga, gasolinera,
            estacion_direccion, ticket_folio, tipo_combustible, precio_litro,
            litros, importe_total, kilometraje, metodo_pago, observaciones,
            imagen_ticket_path, ocr_texto, origen_registro, estado_validacion,
            alerta_resumen, activo, creado_en, actualizado_en
        )
        SELECT
            id, unidad_id, conductor_id, fecha_carga, hora_carga, gasolinera,
            estacion_direccion, ticket_folio, tipo_combustible, precio_litro,
            litros, importe_total, NULLIF(kilometraje, 0), metodo_pago, observaciones,
            imagen_ticket_path, ocr_texto, origen_registro, estado_validacion,
            alerta_resumen, activo, creado_en, actualizado_en
        FROM cargas_combustible;

        DROP TABLE cargas_combustible;
        ALTER TABLE cargas_combustible_new RENAME TO cargas_combustible;
        '''
    )
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.commit()



def _migrate_gps_import_status(conn) -> None:
    """Add soft-annulment columns to existing GPS import tables."""
    cols = _table_columns(conn, "gps_importaciones")
    if "activo" not in cols:
        conn.execute("ALTER TABLE gps_importaciones ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
    if "motivo_anulacion" not in cols:
        conn.execute("ALTER TABLE gps_importaciones ADD COLUMN motivo_anulacion TEXT")
    if "anulado_en" not in cols:
        conn.execute("ALTER TABLE gps_importaciones ADD COLUMN anulado_en TEXT")
    conn.commit()


def _migrate_destination_control_fields(conn) -> None:
    """Add destination fields used to filter/control abnormal inactivity alerts."""
    cols = _table_columns(conn, "destinos")
    if "excluir_alertas_inactividad" not in cols:
        conn.execute("ALTER TABLE destinos ADD COLUMN excluir_alertas_inactividad INTEGER NOT NULL DEFAULT 0")
    if "radio_metros" not in cols:
        conn.execute("ALTER TABLE destinos ADD COLUMN radio_metros REAL DEFAULT 100")
    conn.commit()


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    cols = _table_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate_v15_traceability(conn: sqlite3.Connection) -> None:
    """Additive migrations for v1.5 operational traceability."""
    _add_column_if_missing(conn, "cargas_combustible", "tipo_carga_combustible", "tipo_carga_combustible TEXT DEFAULT 'No especificada'")
    _add_column_if_missing(conn, "cargas_combustible", "calidad_registro", "calidad_registro TEXT")
    _add_column_if_missing(conn, "ruta_entregas", "destino_id", "destino_id INTEGER")
    for col, ddl in [
        ("estado_evidencia", "estado_evidencia TEXT DEFAULT 'activo'"),
        ("motivo_anulacion", "motivo_anulacion TEXT"),
        ("anulado_en", "anulado_en TEXT"),
        ("anulado_por", "anulado_por TEXT"),
    ]:
        _add_column_if_missing(conn, "ruta_entrega_evidencias", col, ddl)
    for col, ddl in [
        ("activo", "activo INTEGER NOT NULL DEFAULT 1"),
        ("motivo_anulacion", "motivo_anulacion TEXT"),
        ("anulado_en", "anulado_en TEXT"),
        ("anulado_por", "anulado_por TEXT"),
    ]:
        _add_column_if_missing(conn, "gps_paradas_clasificacion", col, ddl)
    conn.execute("""CREATE TABLE IF NOT EXISTS archivos_adjuntos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tabla_origen TEXT NOT NULL,
        registro_id INTEGER NOT NULL,
        tipo_archivo TEXT NOT NULL,
        ruta_archivo TEXT NOT NULL,
        estado_archivo TEXT NOT NULL DEFAULT 'activo',
        motivo TEXT,
        comentario TEXT,
        usuario TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        anulado_en TEXT,
        anulado_por TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archivos_origen ON archivos_adjuntos(tabla_origen, registro_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gps_clasificacion_activa ON gps_paradas_clasificacion(gps_parada_id, activo)")
    conn.commit()


def _migrate_v16_operations(conn: sqlite3.Connection) -> None:
    """Additive migrations for v1.6 operational roles, route closing and cost control."""
    # Destination fields for a stronger catalog.
    for col, ddl in [
        ("cliente_comercial", "cliente_comercial TEXT"),
        ("contacto", "contacto TEXT"),
        ("horario_recepcion", "horario_recepcion TEXT"),
        ("requiere_cita", "requiere_cita INTEGER NOT NULL DEFAULT 0"),
        ("tiempo_promedio_servicio_min", "tiempo_promedio_servicio_min REAL"),
    ]:
        _add_column_if_missing(conn, "destinos", col, ddl)

    conn.execute("""CREATE TABLE IF NOT EXISTS gastos_operativos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        unidad_id INTEGER,
        ruta_id INTEGER,
        tipo_gasto TEXT NOT NULL,
        proveedor TEXT,
        folio TEXT,
        importe REAL NOT NULL,
        metodo_pago TEXT,
        descripcion TEXT,
        estado_validacion TEXT DEFAULT 'PENDIENTE_VALIDACION',
        activo INTEGER NOT NULL DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        actualizado_en TEXT,
        FOREIGN KEY (unidad_id) REFERENCES unidades(id),
        FOREIGN KEY (ruta_id) REFERENCES rutas(id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gastos_fecha_unidad ON gastos_operativos(fecha, unidad_id)")
    conn.commit()


def _migrate_v110_route_cleanup(conn: sqlite3.Connection) -> None:
    """Add route cleanup fields used to separate operational routes from tests/training."""
    for col, ddl in [
        ("tipo_ruta", "tipo_ruta TEXT NOT NULL DEFAULT 'OPERATIVA'"),
        ("motivo_anulacion", "motivo_anulacion TEXT"),
        ("anulado_en", "anulado_en TEXT"),
        ("anulado_por", "anulado_por TEXT"),
    ]:
        _add_column_if_missing(conn, "rutas", col, ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rutas_tipo_activo ON rutas(tipo_ruta, activo)")
    conn.commit()
