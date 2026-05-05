from __future__ import annotations

from typing import Any

import pandas as pd

from modules.audit import log_change
from modules.db import get_connection


def classify_charge_quality(row: dict[str, Any]) -> str:
    issues: list[str] = []
    if not str(row.get("ticket_folio") or "").strip():
        issues.append("SIN_FOLIO")
    if not str(row.get("imagen_ticket_path") or "").strip():
        issues.append("SIN_TICKET")
    if row.get("kilometraje") in (None, ""):
        issues.append("SIN_ODOMETRO")
    tipo = str(row.get("tipo_carga_combustible") or "No especificada")
    litros = float(row.get("litros") or 0)
    limite = row.get("limite_litros")
    if tipo.lower() in {"parcial", "emergencia", "garrafón", "garrafon"}:
        issues.append("NO_CONCLUYENTE")
    if limite not in (None, ""):
        try:
            lim = float(limite)
            if lim > 0 and litros > lim * 1.03:
                issues.append("SUPERA_LIMITE")
            if lim > 0 and litros < lim * 0.20 and tipo.lower() not in {"parcial", "emergencia", "garrafón", "garrafon", "aceite", "aditivo"}:
                issues.append("POSIBLE_CARGA_PARCIAL")
        except Exception:
            pass
    return "OK" if not issues else " | ".join(dict.fromkeys(issues))


def refresh_fuel_quality(usuario: str | None = "sistema") -> int:
    """Recalculate calidad_registro for active fuel charges."""
    changed = 0
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.limite_litros
            FROM cargas_combustible c
            JOIN unidades u ON u.id = c.unidad_id
            WHERE COALESCE(c.activo,1)=1
            """
        ).fetchall()
        for row in rows:
            data = dict(row)
            quality = classify_charge_quality(data)
            before = data.get("calidad_registro")
            if (before or "") != quality:
                conn.execute("UPDATE cargas_combustible SET calidad_registro = ?, actualizado_en=CURRENT_TIMESTAMP WHERE id = ?", (quality, data["id"]))
                log_change(conn, "cargas_combustible", data["id"], "QUALITY", "calidad_registro", before, quality, "Recalculo de calidad", "Actualización automática v1.5", usuario)
                changed += 1
        conn.commit()
    return changed


def route_time_inconsistencies(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT e.id AS entrega_id, e.ruta_id, r.fecha, u.placas, c.nombre AS conductor_nombre,
               r.hora_salida_reportada, r.hora_regreso_reportada,
               e.cliente_nombre, e.destino_nombre, e.hora_llegada_reportada, e.estado_conciliacion_gps,
               CASE
                 WHEN r.hora_salida_reportada IS NOT NULL AND e.hora_llegada_reportada < r.hora_salida_reportada THEN 'LLEGADA_ANTES_DE_SALIDA'
                 WHEN r.hora_regreso_reportada IS NOT NULL AND e.hora_llegada_reportada > r.hora_regreso_reportada THEN 'LLEGADA_DESPUES_DE_REGRESO'
                 ELSE NULL
               END AS problema
        FROM ruta_entregas e
        JOIN rutas r ON r.id = e.ruta_id
        JOIN unidades u ON u.id = r.unidad_id
        JOIN conductores c ON c.id = r.conductor_id
        WHERE COALESCE(e.activo,1)=1 AND COALESCE(r.activo,1)=1 AND COALESCE(r.tipo_ruta,'OPERATIVA')='OPERATIVA'
          AND (
                (r.hora_salida_reportada IS NOT NULL AND e.hora_llegada_reportada < r.hora_salida_reportada)
             OR (r.hora_regreso_reportada IS NOT NULL AND e.hora_llegada_reportada > r.hora_regreso_reportada)
          )
    """
    params: list[Any] = []
    if filters.get("fecha_desde"):
        sql += " AND r.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND r.fecha <= ?"
        params.append(filters["fecha_hasta"])
    if filters.get("unidad_id"):
        sql += " AND r.unidad_id = ?"
        params.append(filters["unidad_id"])
    sql += " ORDER BY r.fecha DESC, e.id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def route_state_inconsistencies() -> pd.DataFrame:
    """Find routes whose route status does not match delivery GPS status.

    Uses an aggregate subquery so the SQL works in both SQLite and PostgreSQL.
    PostgreSQL does not allow selecting u.placas/r.estado_ruta with GROUP BY r.id
    only, and it also does not allow HAVING to reference SELECT aliases in the
    same way SQLite tolerates.
    """
    sql = """
        SELECT
            r.id AS ruta_id,
            r.fecha,
            u.placas,
            r.estado_ruta,
            COALESCE(es.entregas, 0) AS entregas,
            COALESCE(es.entregas_asociadas, 0) AS entregas_asociadas,
            COALESCE(es.entregas_no_conciliadas, 0) AS entregas_no_conciliadas
        FROM rutas r
        JOIN unidades u ON u.id = r.unidad_id
        LEFT JOIN (
            SELECT
                ruta_id,
                COUNT(*) AS entregas,
                SUM(CASE WHEN estado_conciliacion_gps IN ('Asociada exacta','Asociada cercana','Asociada manualmente') THEN 1 ELSE 0 END) AS entregas_asociadas,
                SUM(CASE WHEN estado_conciliacion_gps IN ('Sin GPS asociado','Pendiente de GPS','Conflicto') THEN 1 ELSE 0 END) AS entregas_no_conciliadas
            FROM ruta_entregas
            WHERE COALESCE(activo, 1) = 1
            GROUP BY ruta_id
        ) es ON es.ruta_id = r.id
        WHERE COALESCE(r.activo, 1) = 1
          AND COALESCE(r.tipo_ruta, 'OPERATIVA') = 'OPERATIVA'
          AND (
                (r.estado_ruta LIKE 'Conciliada%' AND COALESCE(es.entregas_no_conciliadas, 0) > 0)
             OR (r.estado_ruta = 'Conciliada con GPS')
          )
        ORDER BY r.fecha DESC, r.id DESC
    """
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def operational_pending_counts(filters: dict[str, Any] | None = None) -> dict[str, int]:
    filters = filters or {}
    with get_connection() as conn:
        def one(sql: str, params=()):
            row = conn.execute(sql, params).fetchone()
            if not row:
                return 0
            return int(row[0] or 0)
        counts = {
            "cargas_sin_ticket": one("SELECT COUNT(*) FROM cargas_combustible WHERE COALESCE(activo,1)=1 AND COALESCE(imagen_ticket_path,'')=''"),
            "cargas_sin_folio": one("SELECT COUNT(*) FROM cargas_combustible WHERE COALESCE(activo,1)=1 AND COALESCE(ticket_folio,'')=''"),
            "cargas_no_concluyentes": one("SELECT COUNT(*) FROM cargas_combustible WHERE COALESCE(activo,1)=1 AND (COALESCE(tipo_carga_combustible,'No especificada') IN ('Parcial','Emergencia','Garrafón') OR COALESCE(calidad_registro,'') LIKE '%NO_CONCLUYENTE%')"),
            "entregas_fuera_horario": len(route_time_inconsistencies()),
            "rutas_estado_incoherente": len(route_state_inconsistencies()),
            "evidencias_absolutas": one("SELECT COUNT(*) FROM ruta_entrega_evidencias WHERE ruta_archivo LIKE 'C:%' OR ruta_archivo LIKE '/%'"),
            "destinos_pendientes_validar": one("SELECT COUNT(*) FROM destinos WHERE COALESCE(activo,1)=1 AND COALESCE(validado,0)=0"),
        }
        # Active abnormal stops without active classification/match.
        counts["paradas_largas_sin_clasificar"] = one("""
            SELECT COUNT(*)
            FROM gps_paradas p
            JOIN gps_importaciones gi ON gi.id=p.importacion_id AND COALESCE(gi.activo,1)=1
            LEFT JOIN entrega_gps_match m ON m.gps_parada_id=p.id
            LEFT JOIN gps_paradas_clasificacion c ON c.gps_parada_id=p.id AND COALESCE(c.activo,1)=1
            WHERE p.es_previa_al_primer_movimiento=0
              AND COALESCE(p.duracion_seg,0) >= 1800
              AND m.id IS NULL AND c.id IS NULL
        """)
    return counts


def repair_all_route_statuses(usuario: str | None = "admin") -> int:
    from modules.gps_matcher import recalculate_route_status
    with get_connection() as conn:
        route_ids = [int(r["id"] if "id" in r else r[0]) for r in conn.execute("SELECT id FROM rutas WHERE COALESCE(activo,1)=1 AND COALESCE(tipo_ruta,'OPERATIVA')='OPERATIVA'").fetchall()]
    for route_id in route_ids:
        recalculate_route_status(route_id, motivo="Reparación global de estados", comentario="Recalculo masivo v1.5", usuario=usuario)
    return len(route_ids)
