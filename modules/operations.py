from __future__ import annotations

from typing import Any
import pandas as pd

from modules.audit import log_change, log_event
from modules.db import get_connection
from modules.gps_matcher import recalculate_route_status, unmatched_gps_stops_for_route


def route_closure_snapshot(route_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        route = conn.execute(
            """
            SELECT r.*, u.placas, c.nombre AS conductor_nombre
            FROM rutas r
            JOIN unidades u ON u.id=r.unidad_id
            JOIN conductores c ON c.id=r.conductor_id
            WHERE r.id=?
            """,
            (route_id,),
        ).fetchone()
        if not route:
            return {}
        deliveries = conn.execute(
            """
            SELECT e.*, COALESCE(ev.evidencias, 0) AS evidencias
            FROM ruta_entregas e
            LEFT JOIN (
                SELECT entrega_id, COUNT(*) AS evidencias
                FROM ruta_entrega_evidencias
                WHERE COALESCE(estado_evidencia,'activo')='activo'
                GROUP BY entrega_id
            ) ev ON ev.entrega_id=e.id
            WHERE e.ruta_id=? AND COALESCE(e.activo,1)=1
            ORDER BY e.id
            """,
            (route_id,),
        ).fetchall()
        n = len(deliveries)
        associated = sum(1 for d in deliveries if d["estado_conciliacion_gps"] in ("Asociada exacta", "Asociada cercana", "Asociada manualmente"))
        pending = sum(1 for d in deliveries if d["estado_conciliacion_gps"] in ("Pendiente de GPS", "Sin GPS asociado", "Conflicto"))
        no_evidence = sum(1 for d in deliveries if int(d["evidencias"] or 0) == 0)
        failed = sum(1 for d in deliveries if str(d["estatus_entrega"]) not in ("Entregado completo", "Entregado en paquetería"))
        time_bad = conn.execute(
            """
            SELECT COUNT(*)
            FROM ruta_entregas e
            JOIN rutas r ON r.id=e.ruta_id
            WHERE e.ruta_id=? AND COALESCE(e.activo,1)=1
              AND ((r.hora_salida_reportada IS NOT NULL AND e.hora_llegada_reportada < r.hora_salida_reportada)
                OR (r.hora_regreso_reportada IS NOT NULL AND e.hora_llegada_reportada > r.hora_regreso_reportada))
            """,
            (route_id,),
        ).fetchone()[0]
    unmatched = unmatched_gps_stops_for_route(route_id)
    return {
        "route": dict(route),
        "entregas": n,
        "entregas_con_gps": associated,
        "entregas_pendientes_gps": pending,
        "entregas_sin_evidencia": no_evidence,
        "entregas_con_incidencia": failed,
        "entregas_fuera_horario": int(time_bad or 0),
        "paradas_gps_no_asociadas": 0 if unmatched.empty else int(len(unmatched)),
    }


def finalize_route(route_id: int, final_status: str, comentario: str, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT estado_ruta FROM rutas WHERE id=?", (route_id,)).fetchone()
        conn.execute(
            "UPDATE rutas SET estado_ruta=?, observaciones_generales=COALESCE(observaciones_generales,'') || ? , actualizado_en=CURRENT_TIMESTAMP WHERE id=?",
            (final_status, f"\n[Cierre operativo] {comentario}" if comentario else "", route_id),
        )
        log_event(conn, "rutas", route_id, "CIERRE_OPERATIVO", f"Ruta cerrada como {final_status}")
        log_change(conn, "rutas", route_id, "CIERRE_OPERATIVO", "estado_ruta", before["estado_ruta"] if before else None, final_status, "Cierre operativo", comentario, usuario)
        conn.commit()


def link_delivery_destination(delivery_id: int, destination_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT destino_id FROM ruta_entregas WHERE id=?", (delivery_id,)).fetchone()
        conn.execute("UPDATE ruta_entregas SET destino_id=?, actualizado_en=CURRENT_TIMESTAMP WHERE id=?", (destination_id, delivery_id))
        log_change(conn, "ruta_entregas", delivery_id, "LINK_DESTINATION", "destino_id", before["destino_id"] if before else None, destination_id, motivo, comentario, usuario)
        conn.commit()


def deliveries_without_destination() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.id AS entrega_id, e.ruta_id, r.fecha, u.placas, e.cliente_nombre, e.destino_nombre,
                   e.hora_llegada_reportada, e.estatus_entrega
            FROM ruta_entregas e
            JOIN rutas r ON r.id=e.ruta_id
            JOIN unidades u ON u.id=r.unidad_id
            WHERE COALESCE(e.activo,1)=1 AND COALESCE(r.activo,1)=1 AND COALESCE(r.tipo_ruta,'OPERATIVA')='OPERATIVA' AND e.destino_id IS NULL
            ORDER BY r.fecha DESC, e.id DESC
            """
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])
