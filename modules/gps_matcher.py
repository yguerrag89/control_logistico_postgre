from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from modules.audit import log_change, log_event
from modules.db import get_connection


ASSOCIATED_STATES = {"Asociada exacta", "Asociada cercana", "Asociada manualmente"}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("T", " "))
    except Exception:
        return None


def _combine_route_date_and_time(route_date: str, time_text: str) -> datetime:
    text = str(time_text).strip()
    if len(text) == 5 and ":" in text:
        return datetime.fromisoformat(f"{route_date} {text}:00")
    if len(text) == 8 and text.count(":") == 2:
        return datetime.fromisoformat(f"{route_date} {text}")
    # Allow full datetime if user pasted it
    return datetime.fromisoformat(text.replace("T", " "))


def _route_bounds(route: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """Return route start/end datetimes. Handles routes crossing midnight."""
    start = None
    end = None
    if route.get("hora_salida_reportada"):
        try:
            start = _combine_route_date_and_time(route["fecha"], route["hora_salida_reportada"])
        except Exception:
            start = None
    if route.get("hora_regreso_reportada"):
        try:
            end = _combine_route_date_and_time(route["fecha"], route["hora_regreso_reportada"])
        except Exception:
            end = None
    if start and end and end < start:
        end = end + timedelta(days=1)
    return start, end


def _candidate_score(arrival: datetime, start: datetime, end: datetime) -> tuple[str, float, float]:
    if start <= arrival <= end:
        # Exact: confidence decreases slightly if arrival is far from GPS start.
        diff = abs((arrival - start).total_seconds()) / 60
        confidence = max(85, 100 - min(diff, 15))
        return "Asociada exacta", diff, confidence
    nearest_diff = min(abs((arrival - start).total_seconds()), abs((arrival - end).total_seconds())) / 60
    if nearest_diff <= 5:
        return "Asociada cercana", nearest_diff, 80
    if nearest_diff <= 15:
        return "Asociada cercana", nearest_diff, 60
    if nearest_diff <= 30:
        return "Asociada cercana", nearest_diff, 40
    return "Sin GPS asociado", nearest_diff, 0


def _stop_inside_route(stop: Any, route_start: datetime | None, route_end: datetime | None) -> bool:
    if not route_start and not route_end:
        return True
    start = _parse_dt(stop["inicio_gps"])
    end = _parse_dt(stop["fin_gps"])
    if not start or not end:
        return False
    if route_start and end < route_start:
        return False
    if route_end and start > route_end:
        return False
    return True


def _status_from_summary(summary: dict[str, Any]) -> str:
    entregas = int(summary.get("entregas", 0) or 0)
    exactas = int(summary.get("asociadas_exactas", 0) or 0)
    cercanas = int(summary.get("asociadas_cercanas", 0) or 0)
    manuales = int(summary.get("asociadas_manuales", 0) or 0)
    sin_gps = int(summary.get("sin_gps", 0) or 0)
    conflictos = int(summary.get("conflictos", 0) or 0)
    asociadas = exactas + cercanas + manuales

    if entregas == 0:
        return "Cerrada sin entregas"
    if conflictos > 0:
        return "Conciliación con conflictos"
    if sin_gps == 0 and asociadas == entregas and cercanas == 0:
        return "Conciliada completa"
    if sin_gps == 0 and asociadas == entregas:
        return "Conciliación con cercanas"
    if asociadas > 0 and sin_gps > 0:
        return "Conciliación parcial"
    return "Cerrada con inconsistencias"


def recalculate_route_status(route_id: int, motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> str:
    """Recalculate route status from current delivery reconciliation states."""
    with get_connection() as conn:
        current = conn.execute("SELECT estado_ruta FROM rutas WHERE id = ?", (route_id,)).fetchone()
        rows = conn.execute(
            """
            SELECT estado_conciliacion_gps, COUNT(*) AS n
            FROM ruta_entregas
            WHERE ruta_id = ? AND activo = 1
            GROUP BY estado_conciliacion_gps
            """,
            (route_id,),
        ).fetchall()
        total = sum(int(r["n"] or 0) for r in rows)
        counts = {str(r["estado_conciliacion_gps"]): int(r["n"] or 0) for r in rows}
        summary = {
            "entregas": total,
            "asociadas_exactas": counts.get("Asociada exacta", 0),
            "asociadas_cercanas": counts.get("Asociada cercana", 0),
            "asociadas_manuales": counts.get("Asociada manualmente", 0),
            "conflictos": counts.get("Conflicto", 0),
            "sin_gps": counts.get("Sin GPS asociado", 0),
        }
        # Pending deliveries mean GPS was not fully reconciled yet.
        pending = counts.get("Pendiente de GPS", 0)
        if total == 0:
            new_status = "Cerrada sin entregas"
        elif pending == total:
            new_status = "Cerrada pendiente de GPS"
        elif pending > 0:
            new_status = "Conciliación parcial"
        else:
            new_status = _status_from_summary(summary)

        before = current["estado_ruta"] if current else None
        conn.execute("UPDATE rutas SET estado_ruta = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (new_status, route_id))
        if before != new_status:
            log_change(conn, "rutas", route_id, "RECALC_STATUS", "estado_ruta", before, new_status, motivo, comentario, usuario)
        conn.commit()
        return new_status


def reconcile_route_with_gps(route_id: int, close_status: bool = True) -> dict[str, Any]:
    with get_connection() as conn:
        route = conn.execute(
            """
            SELECT r.*, u.placas
            FROM rutas r
            JOIN unidades u ON u.id = r.unidad_id
            WHERE r.id = ?
            """,
            (route_id,),
        ).fetchone()
        if not route:
            raise ValueError(f"Ruta no encontrada: {route_id}")
        route_dict = dict(route)
        route_start, route_end = _route_bounds(route_dict)

        deliveries = conn.execute(
            """
            SELECT *
            FROM ruta_entregas
            WHERE ruta_id = ? AND activo = 1
            ORDER BY hora_llegada_reportada ASC, id ASC
            """,
            (route_id,),
        ).fetchall()
        stops = conn.execute(
            """
            SELECT p.*
            FROM gps_paradas p
            JOIN gps_importaciones gi ON gi.id = p.importacion_id AND COALESCE(gi.activo,1)=1
            WHERE p.unidad_id = ?
              AND p.fecha = ?
              AND p.es_previa_al_primer_movimiento = 0
              AND p.inicio_gps IS NOT NULL
              AND p.fin_gps IS NOT NULL
            ORDER BY p.inicio_gps ASC
            """,
            (route["unidad_id"], route["fecha"]),
        ).fetchall()
        stops = [s for s in stops if _stop_inside_route(s, route_start, route_end)]

        # Remove previous matches for this route to make reconciliation idempotent.
        conn.execute(
            """
            DELETE FROM entrega_gps_match
            WHERE entrega_id IN (
                SELECT id FROM ruta_entregas WHERE ruta_id = ?
            )
            """,
            (route_id,),
        )

        summary = {
            "entregas": len(deliveries),
            "asociadas_exactas": 0,
            "asociadas_cercanas": 0,
            "asociadas_manuales": 0,
            "sin_gps": 0,
            "conflictos": 0,
        }

        used_stop_ids: set[int] = set()
        for delivery in deliveries:
            try:
                arrival = _combine_route_date_and_time(route["fecha"], delivery["hora_llegada_reportada"])
            except Exception:
                conn.execute(
                    "UPDATE ruta_entregas SET estado_conciliacion_gps = ?, actualizado_en=CURRENT_TIMESTAMP WHERE id = ?",
                    ("Sin GPS asociado", delivery["id"]),
                )
                summary["sin_gps"] += 1
                continue

            candidates: list[dict[str, Any]] = []
            for stop in stops:
                start = _parse_dt(stop["inicio_gps"])
                end = _parse_dt(stop["fin_gps"])
                if start is None or end is None:
                    continue
                tipo, diff, conf = _candidate_score(arrival, start, end)
                if conf > 0:
                    candidates.append({
                        "stop": stop,
                        "tipo": tipo,
                        "diff": diff,
                        "confidence": conf,
                        "start": start,
                        "end": end,
                    })

            candidates = sorted(candidates, key=lambda c: (-c["confidence"], c["diff"], -int(c["stop"]["duracion_seg"] or 0)))
            if not candidates:
                conn.execute(
                    "UPDATE ruta_entregas SET estado_conciliacion_gps = ?, actualizado_en=CURRENT_TIMESTAMP WHERE id = ?",
                    ("Sin GPS asociado", delivery["id"]),
                )
                summary["sin_gps"] += 1
                continue

            best = candidates[0]
            tipo = best["tipo"]
            if best["stop"]["id"] in used_stop_ids:
                tipo = "Conflicto"
                summary["conflictos"] += 1
            elif tipo == "Asociada exacta":
                summary["asociadas_exactas"] += 1
            else:
                summary["asociadas_cercanas"] += 1
            used_stop_ids.add(best["stop"]["id"])

            conn.execute(
                """
                INSERT INTO entrega_gps_match (
                    entrega_id, gps_parada_id, tipo_match, diferencia_min,
                    confianza, hora_salida_inferida, tiempo_en_cliente_seg, validado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery["id"],
                    best["stop"]["id"],
                    tipo,
                    round(best["diff"], 2),
                    round(best["confidence"], 2),
                    best["stop"]["fin_gps"],
                    best["stop"]["duracion_seg"],
                    1 if tipo == "Asociada exacta" else 0,
                ),
            )
            conn.execute(
                "UPDATE ruta_entregas SET estado_conciliacion_gps = ?, actualizado_en=CURRENT_TIMESTAMP WHERE id = ?",
                (tipo, delivery["id"]),
            )

        new_status = _status_from_summary(summary)
        if close_status:
            conn.execute(
                "UPDATE rutas SET estado_ruta = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, route_id),
            )
            summary["estado_ruta_resultante"] = new_status
        log_event(conn, "rutas", route_id, "CONCILIACION_GPS", str(summary))
        conn.commit()
        return summary


def route_gps_reconciliation_view(route_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.id AS entrega_id, e.orden_calculado, e.cliente_nombre, e.destino_nombre,
                   e.hora_llegada_reportada, e.estatus_entrega, e.estado_conciliacion_gps,
                   m.tipo_match, m.diferencia_min, m.confianza, m.hora_salida_inferida,
                   m.tiempo_en_cliente_seg,
                   p.id AS gps_parada_id, p.inicio_gps, p.fin_gps, p.direccion_gps, p.duracion_seg,
                   r.fecha AS fecha_ruta, r.hora_salida_reportada, r.hora_regreso_reportada
            FROM ruta_entregas e
            JOIN rutas r ON r.id = e.ruta_id
            LEFT JOIN entrega_gps_match m ON m.entrega_id = e.id
            LEFT JOIN gps_paradas p ON p.id = m.gps_parada_id
            WHERE e.ruta_id = ? AND e.activo = 1
            ORDER BY e.orden_calculado ASC, e.hora_llegada_reportada ASC
            """,
            (route_id,),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def unmatched_gps_stops_for_route(route_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        route = conn.execute("SELECT * FROM rutas WHERE id = ?", (route_id,)).fetchone()
        if not route:
            return pd.DataFrame()
        route_dict = dict(route)
        route_start, route_end = _route_bounds(route_dict)
        rows = conn.execute(
            """
            SELECT p.*
            FROM gps_paradas p
            JOIN gps_importaciones gi ON gi.id = p.importacion_id AND COALESCE(gi.activo,1)=1
            LEFT JOIN entrega_gps_match m ON m.gps_parada_id = p.id
            LEFT JOIN gps_paradas_clasificacion c ON c.gps_parada_id = p.id AND COALESCE(c.activo,1)=1
            WHERE p.unidad_id = ?
              AND p.fecha = ?
              AND p.es_previa_al_primer_movimiento = 0
              AND m.id IS NULL
              AND c.id IS NULL
            ORDER BY p.inicio_gps ASC
            """,
            (route["unidad_id"], route["fecha"]),
        ).fetchall()
    filtered = [r for r in rows if _stop_inside_route(r, route_start, route_end)]
    return pd.DataFrame([dict(r) for r in filtered])


def nearby_stop_suggestions_for_route(route_id: int, max_minutes: int = 120, top_n: int = 3) -> pd.DataFrame:
    """Suggest GPS stops for deliveries without a useful exact match.

    It is intentionally broader than the automatic matcher, because this is a review tool:
    if the operator captured the wrong hour, the correct GPS stop can be 30-60 minutes away.
    """
    with get_connection() as conn:
        route = conn.execute("SELECT * FROM rutas WHERE id = ?", (route_id,)).fetchone()
        if not route:
            return pd.DataFrame()
        route_dict = dict(route)
        route_start, route_end = _route_bounds(route_dict)
        deliveries = conn.execute(
            """
            SELECT e.*
            FROM ruta_entregas e
            LEFT JOIN entrega_gps_match m ON m.entrega_id = e.id
            WHERE e.ruta_id = ? AND e.activo = 1
              AND (m.id IS NULL OR e.estado_conciliacion_gps IN ('Sin GPS asociado','Pendiente de GPS'))
            ORDER BY e.hora_llegada_reportada, e.id
            """,
            (route_id,),
        ).fetchall()
        stops = conn.execute(
            """
            SELECT p.*
            FROM gps_paradas p
            JOIN gps_importaciones gi ON gi.id = p.importacion_id AND COALESCE(gi.activo,1)=1
            WHERE p.unidad_id = ?
              AND p.fecha = ?
              AND p.es_previa_al_primer_movimiento = 0
              AND p.inicio_gps IS NOT NULL
              AND p.fin_gps IS NOT NULL
            ORDER BY p.inicio_gps ASC
            """,
            (route["unidad_id"], route["fecha"]),
        ).fetchall()
    stops = [s for s in stops if _stop_inside_route(s, route_start, route_end)]
    suggestions: list[dict[str, Any]] = []
    for delivery in deliveries:
        try:
            arrival = _combine_route_date_and_time(route_dict["fecha"], delivery["hora_llegada_reportada"])
        except Exception:
            arrival = None
        candidates: list[dict[str, Any]] = []
        for stop in stops:
            start = _parse_dt(stop["inicio_gps"])
            end = _parse_dt(stop["fin_gps"])
            if not start or not end:
                continue
            if arrival:
                if start <= arrival <= end:
                    diff = 0.0
                else:
                    diff = min(abs((arrival - start).total_seconds()), abs((arrival - end).total_seconds())) / 60
            else:
                # If arrival cannot be parsed, prefer long stops within route interval.
                diff = 999999.0
            if arrival and diff > max_minutes:
                continue
            dur_min = int(stop["duracion_seg"] or 0) / 60
            addr = str(stop["direccion_gps"] or "").lower()
            penalty = 0
            # A 3-4 minute stop is usually a manoeuvre, not a delivery.
            if dur_min < 10:
                penalty += 20
            # Base/parking stops should not be the first suggestion for a customer delivery.
            if any(x in addr for x in ["santo domingo", "sta lucia", "sta. lucia", "centeotl"]):
                penalty += 100
            candidates.append({"stop": stop, "start": start, "end": end, "diff": diff, "score": diff + penalty})
        candidates = sorted(candidates, key=lambda c: (c["score"], c["diff"], -int(c["stop"]["duracion_seg"] or 0)))[:top_n]
        for c in candidates:
            start = c["start"]
            end = c["end"]
            suggestions.append({
                "entrega_id": delivery["id"],
                "cliente_nombre": delivery["cliente_nombre"],
                "destino_nombre": delivery["destino_nombre"],
                "hora_llegada_reportada": delivery["hora_llegada_reportada"],
                "gps_parada_id": c["stop"]["id"],
                "inicio_gps": c["stop"]["inicio_gps"],
                "fin_gps": c["stop"]["fin_gps"],
                "duracion_min": round((int(c["stop"]["duracion_seg"] or 0)) / 60, 1),
                "direccion_gps": c["stop"]["direccion_gps"],
                "diferencia_min": round(c["diff"], 1) if c["diff"] < 999999 else None,
                "hora_sugerida": start.strftime("%H:%M"),
                "observacion": "La hora reportada cae fuera de esta parada; revisar/corregir si corresponde." if c["diff"] else "Match exacto posible.",
            })
    return pd.DataFrame(suggestions)


def manual_associate_delivery_to_stop(delivery_id: int, gps_parada_id: int, motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        delivery = conn.execute("SELECT * FROM ruta_entregas WHERE id = ?", (delivery_id,)).fetchone()
        stop = conn.execute("SELECT * FROM gps_paradas WHERE id = ?", (gps_parada_id,)).fetchone()
        if not delivery or not stop:
            raise ValueError("No se encontró la entrega o la parada GPS.")
        conn.execute("DELETE FROM entrega_gps_match WHERE entrega_id = ?", (delivery_id,))
        conn.execute(
            """
            INSERT INTO entrega_gps_match (
                entrega_id, gps_parada_id, tipo_match, diferencia_min,
                confianza, hora_salida_inferida, tiempo_en_cliente_seg, validado, validado_por, validado_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                delivery_id,
                gps_parada_id,
                "Asociada manualmente",
                None,
                75,
                stop["fin_gps"],
                stop["duracion_seg"],
                1,
                usuario or "usuario",
            ),
        )
        before = delivery["estado_conciliacion_gps"]
        conn.execute(
            "UPDATE ruta_entregas SET estado_conciliacion_gps = ?, actualizado_en=CURRENT_TIMESTAMP WHERE id = ?",
            ("Asociada manualmente", delivery_id),
        )
        log_change(conn, "ruta_entregas", delivery_id, "MANUAL_GPS_MATCH", "estado_conciliacion_gps", before, "Asociada manualmente", motivo, comentario, usuario)
        log_event(conn, "ruta_entregas", delivery_id, "MANUAL_GPS_MATCH", f"Asociada manualmente a gps_parada_id={gps_parada_id}")
        conn.commit()
    recalculate_route_status(int(delivery["ruta_id"]), motivo=motivo, comentario="Recalculo posterior a asociación manual GPS", usuario=usuario)
