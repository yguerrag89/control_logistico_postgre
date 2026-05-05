from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from modules.db import get_connection
from modules.audit import log_event, log_change


def _dicts(rows):
    return [dict(r) for r in rows]


def find_unit_id_by_plate(plate: str | None) -> int | None:
    if not plate:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM unidades WHERE UPPER(placas)=UPPER(?)", (plate,)).fetchone()
        return int(row["id"]) if row else None


def import_hash_exists(hash_movimientos: str, unidad_id: int | None, mes: int | None, anio: int | None) -> bool:
    if not hash_movimientos:
        return False
    sql = "SELECT COUNT(*) AS n FROM gps_importaciones WHERE hash_movimientos = ? AND COALESCE(activo,1)=1"
    params: list[Any] = [hash_movimientos]
    if unidad_id:
        sql += " AND unidad_id = ?"
        params.append(unidad_id)
    if mes:
        sql += " AND mes = ?"
        params.append(mes)
    if anio:
        sql += " AND anio = ?"
        params.append(anio)
    with get_connection() as conn:
        return conn.execute(sql, params).fetchone()["n"] > 0


def save_gps_sheet(
    parsed_sheet: dict[str, Any],
    archivo: str,
    unidad_id: int | None = None,
    unit_id: int | None = None,
) -> int:
    """Guarda una hoja GPS parseada.

    unidad_id es el nombre canónico. unit_id queda como alias
    para evitar romper llamadas generadas en versiones anteriores.
    """
    if unidad_id is None and unit_id is not None:
        unidad_id = unit_id

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO gps_importaciones (
                archivo, hoja, unidad_id, placas, mes, anio, tipo_hoja,
                km_resumen, km_calculados, diferencia_km,
                tiempo_resumen_seg, tiempo_calculado_seg, diferencia_tiempo_seg,
                movimientos_detectados, inmovilizaciones_detectadas, hash_movimientos,
                estado_validacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archivo,
                parsed_sheet["hoja"],
                unidad_id,
                parsed_sheet.get("unidad"),
                parsed_sheet.get("mes"),
                parsed_sheet.get("anio"),
                parsed_sheet.get("tipo_hoja"),
                parsed_sheet.get("km_resumen"),
                parsed_sheet.get("km_calculados"),
                parsed_sheet.get("diferencia_km"),
                parsed_sheet.get("tiempo_resumen_seg"),
                parsed_sheet.get("tiempo_calculado_seg"),
                parsed_sheet.get("diferencia_tiempo_seg"),
                parsed_sheet.get("movimientos_detectados"),
                parsed_sheet.get("inmovilizaciones_detectadas"),
                parsed_sheet.get("hash_movimientos"),
                parsed_sheet.get("estado_validacion"),
            ),
        )
        import_id = cur.lastrowid
        seq_to_id: dict[int, int] = {}
        for mov in parsed_sheet.get("movimientos", []):
            curm = conn.execute(
                """
                INSERT INTO gps_movimientos (
                    importacion_id, unidad_id, placas, fecha, secuencia,
                    inicio_datetime, fin_datetime, km, duracion_reportada_seg,
                    duracion_calculada_seg, diferencia_duracion_seg,
                    velocidad_promedio_kmh, origen, destino, flags_calidad
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    unidad_id,
                    parsed_sheet.get("unidad"),
                    mov.get("fecha"),
                    mov.get("secuencia"),
                    mov.get("inicio_datetime"),
                    mov.get("fin_datetime"),
                    mov.get("km"),
                    mov.get("duracion_reportada_seg"),
                    mov.get("duracion_calculada_seg"),
                    mov.get("diferencia_duracion_seg"),
                    mov.get("velocidad_promedio_kmh"),
                    mov.get("origen"),
                    mov.get("destino"),
                    mov.get("flags_calidad"),
                ),
            )
            seq_to_id[mov.get("secuencia")] = curm.lastrowid

        for p in parsed_sheet.get("paradas", []):
            conn.execute(
                """
                INSERT INTO gps_paradas (
                    importacion_id, movimiento_anterior_id, unidad_id, placas, fecha,
                    inicio_gps, fin_gps, duracion_seg, direccion_gps,
                    clasificacion_inicial, requiere_revision, es_previa_al_primer_movimiento,
                    texto_original
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    seq_to_id.get(p.get("movimiento_secuencia")),
                    unidad_id,
                    parsed_sheet.get("unidad"),
                    p.get("fecha"),
                    p.get("inicio_gps"),
                    p.get("fin_gps"),
                    p.get("duracion_seg"),
                    p.get("direccion_gps"),
                    p.get("clasificacion_inicial"),
                    p.get("requiere_revision", 0),
                    p.get("es_previa_al_primer_movimiento", 0),
                    p.get("texto_original"),
                ),
            )
        _audit(conn, "gps_importaciones", import_id, "INSERT", f"GPS importado hoja={parsed_sheet['hoja']}")
        conn.commit()
        return import_id


def list_gps_imports(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT gi.*, u.placas AS placas_catalogo
        FROM gps_importaciones gi
        LEFT JOIN unidades u ON u.id = gi.unidad_id
        WHERE 1=1
    """
    if not filters.get("include_inactive"):
        sql += " AND COALESCE(gi.activo,1)=1"
    params: list[Any] = []
    if filters.get("unidad_id"):
        sql += " AND gi.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("anio"):
        sql += " AND gi.anio = ?"
        params.append(filters["anio"])
    if filters.get("mes"):
        sql += " AND gi.mes = ?"
        params.append(filters["mes"])
    sql += " ORDER BY gi.anio DESC, gi.mes DESC, gi.id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def list_gps_movements(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT gm.*, u.placas AS placas_catalogo
        FROM gps_movimientos gm
        LEFT JOIN unidades u ON u.id = gm.unidad_id
        JOIN gps_importaciones gi ON gi.id = gm.importacion_id AND COALESCE(gi.activo,1)=1
        WHERE 1=1
    """
    params: list[Any] = []
    if filters.get("unidad_id"):
        sql += " AND gm.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("fecha_desde"):
        sql += " AND gm.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND gm.fecha <= ?"
        params.append(filters["fecha_hasta"])
    sql += " ORDER BY gm.inicio_datetime ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def list_gps_stops(filters: dict[str, Any] | None = None, unmatched_only: bool = False) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT p.*, u.placas AS placas_catalogo, m.id AS match_id, c.clasificacion AS clasificacion_manual
        FROM gps_paradas p
        LEFT JOIN unidades u ON u.id = p.unidad_id
        JOIN gps_importaciones gi ON gi.id = p.importacion_id AND COALESCE(gi.activo,1)=1
        LEFT JOIN entrega_gps_match m ON m.gps_parada_id = p.id
        LEFT JOIN gps_paradas_clasificacion c ON c.gps_parada_id = p.id AND COALESCE(c.activo,1)=1
        WHERE p.es_previa_al_primer_movimiento = 0
    """
    params: list[Any] = []
    if filters.get("unidad_id"):
        sql += " AND p.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("fecha_desde"):
        sql += " AND p.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND p.fecha <= ?"
        params.append(filters["fecha_hasta"])
    if filters.get("requiere_revision") is not None:
        sql += " AND p.requiere_revision = ?"
        params.append(filters["requiere_revision"])
    if unmatched_only:
        sql += " AND m.id IS NULL AND c.id IS NULL"
    sql += " ORDER BY p.fecha DESC, p.inicio_gps DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def classify_gps_stop(stop_id: int, clasificacion: str, comentario: str | None = None, usuario: str | None = None) -> int:
    """Classify a GPS stop, keeping only one active classification per stop."""
    with get_connection() as conn:
        previous = conn.execute(
            "SELECT id, clasificacion FROM gps_paradas_clasificacion WHERE gps_parada_id = ? AND COALESCE(activo,1)=1",
            (stop_id,),
        ).fetchall()
        conn.execute(
            """
            UPDATE gps_paradas_clasificacion
            SET activo = 0, anulado_en = CURRENT_TIMESTAMP, anulado_por = ?, motivo_anulacion = ?
            WHERE gps_parada_id = ? AND COALESCE(activo,1)=1
            """,
            (usuario, "Reclasificación", stop_id),
        )
        cur = conn.execute(
            """
            INSERT INTO gps_paradas_clasificacion (gps_parada_id, clasificacion, comentario, clasificado_por, activo)
            VALUES (?, ?, ?, ?, 1)
            """,
            (stop_id, clasificacion, comentario, usuario),
        )
        new_id = int(cur.lastrowid)
        for prev in previous:
            log_change(conn, "gps_paradas_clasificacion", int(prev["id"]), "RECLASSIFY", "activo", 1, 0, "Reclasificación", comentario, usuario)
        log_change(conn, "gps_paradas_clasificacion", new_id, "INSERT", "clasificacion", None, clasificacion, "Clasificación de parada", comentario, usuario)
        _audit(conn, "gps_paradas_clasificacion", new_id, "INSERT", f"Parada GPS {stop_id} clasificada como {clasificacion}")
        conn.commit()
        return new_id


def gps_summary_by_unit(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT u.placas, gm.unidad_id,
               COUNT(gm.id) AS movimientos,
               ROUND(SUM(gm.km), 2) AS km_gps,
               ROUND(SUM(gm.duracion_reportada_seg)/3600.0, 2) AS horas_movimiento,
               COUNT(DISTINCT gm.fecha) AS dias_con_movimiento
        FROM gps_movimientos gm
        LEFT JOIN unidades u ON u.id = gm.unidad_id
        JOIN gps_importaciones gi ON gi.id = gm.importacion_id AND COALESCE(gi.activo,1)=1
        WHERE 1=1
    """
    params: list[Any] = []
    if filters.get("unidad_id"):
        sql += " AND gm.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("fecha_desde"):
        sql += " AND gm.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND gm.fecha <= ?"
        params.append(filters["fecha_hasta"])
    sql += " GROUP BY gm.unidad_id, u.placas ORDER BY km_gps DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def annul_gps_import(import_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT activo FROM gps_importaciones WHERE id = ?", (import_id,)).fetchone()
        conn.execute(
            """
            UPDATE gps_importaciones
            SET activo = 0, motivo_anulacion = ?, anulado_en = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (motivo if not comentario else f"{motivo} | {comentario}", import_id),
        )
        # Los datos GPS quedan físicamente guardados, pero todos los reportes los excluyen por importación inactiva.
        log_event(conn, "gps_importaciones", import_id, "ANULAR", "Importación GPS anulada")
        log_change(conn, "gps_importaciones", import_id, "ANULAR", "activo", before["activo"] if before else None, 0, motivo, comentario, usuario)
        conn.commit()


def _audit(conn, tabla: str, registro_id: int, accion: str, detalle: str) -> None:
    log_event(conn, tabla, registro_id, accion, detalle)
