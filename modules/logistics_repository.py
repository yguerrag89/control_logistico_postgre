from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from modules.audit import log_change, log_event, log_field_changes
from modules.traceability import register_attachment, to_relative_path
from modules.db import APP_DIR, EVIDENCIAS_DIR, get_connection


def _dicts(rows):
    return [dict(r) for r in rows]


def _parse_route_time(route_date: str, time_text: str | None) -> datetime | None:
    if not time_text:
        return None
    text = str(time_text).strip()
    try:
        if len(text) == 5 and ":" in text:
            return datetime.fromisoformat(f"{route_date} {text}:00")
        if len(text) == 8 and text.count(":") == 2:
            return datetime.fromisoformat(f"{route_date} {text}")
        return datetime.fromisoformat(text.replace("T", " "))
    except Exception:
        return None


def validate_delivery_time_against_route(route: dict[str, Any], hora_llegada: str) -> list[str]:
    """Return consistency warnings/errors for a delivery arrival hour.

    This prevents the common mistake of correcting route departure while leaving
    the delivery arrival hour wrong. The GPS match uses delivery arrival time.
    """
    messages: list[str] = []
    fecha = str(route.get("fecha") or "")
    arrival = _parse_route_time(fecha, hora_llegada)
    if arrival is None:
        return ["La hora de llegada no tiene un formato válido. Usa HH:MM, por ejemplo 11:46."]
    start = _parse_route_time(fecha, route.get("hora_salida_reportada"))
    end = _parse_route_time(fecha, route.get("hora_regreso_reportada"))
    if start and end and end < start:
        end = end + timedelta(days=1)
    if start and arrival < start:
        messages.append(
            f"La hora de llegada ({hora_llegada}) es anterior a la salida de la ruta ({route.get('hora_salida_reportada')}). "
            "Corrige la entrega si el error fue la llegada al cliente."
        )
    if end and arrival > end:
        messages.append(
            f"La hora de llegada ({hora_llegada}) es posterior al regreso de la ruta ({route.get('hora_regreso_reportada')})."
        )
    return messages


def create_route(data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO rutas (
                fecha, unidad_id, conductor_id, hora_salida_reportada,
                hora_regreso_reportada, estado_ruta, tipo_ruta, observaciones_generales
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["fecha"], data["unidad_id"], data["conductor_id"],
                data.get("hora_salida_reportada"), data.get("hora_regreso_reportada"),
                data.get("estado_ruta", "Abierta"), data.get("tipo_ruta", "OPERATIVA"), data.get("observaciones_generales"),
            ),
        )
        route_id = int(cur.lastrowid)
        log_event(conn, "rutas", route_id, "INSERT", f"Ruta creada para unidad_id={data['unidad_id']}")
        log_change(conn, "rutas", route_id, "INSERT", None, None, data, motivo, comentario, usuario)
        conn.commit()
        return route_id


def update_route(route_id: int, data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    fields = ["fecha", "unidad_id", "conductor_id", "hora_salida_reportada", "hora_regreso_reportada", "estado_ruta", "tipo_ruta", "observaciones_generales"]
    with get_connection() as conn:
        before_row = conn.execute("SELECT * FROM rutas WHERE id = ?", (route_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        critical_changed = any(str(before.get(f)) != str(data.get(f)) for f in ["fecha", "unidad_id", "hora_salida_reportada", "hora_regreso_reportada"])
        conn.execute(
            """
            UPDATE rutas
            SET fecha = ?, unidad_id = ?, conductor_id = ?, hora_salida_reportada = ?,
                hora_regreso_reportada = ?, estado_ruta = ?, tipo_ruta = ?, observaciones_generales = ?,
                actualizado_en = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data["fecha"], data["unidad_id"], data["conductor_id"],
                data.get("hora_salida_reportada"), data.get("hora_regreso_reportada"),
                data.get("estado_ruta", "Abierta"), data.get("tipo_ruta", "OPERATIVA"), data.get("observaciones_generales"), route_id,
            ),
        )
        if critical_changed:
            conn.execute("DELETE FROM entrega_gps_match WHERE entrega_id IN (SELECT id FROM ruta_entregas WHERE ruta_id = ?)", (route_id,))
            conn.execute("UPDATE ruta_entregas SET estado_conciliacion_gps = 'Pendiente de GPS', actualizado_en = CURRENT_TIMESTAMP WHERE ruta_id = ?", (route_id,))
            if data.get("estado_ruta") == "Conciliada con GPS":
                conn.execute("UPDATE rutas SET estado_ruta='Cerrada pendiente de GPS' WHERE id=?", (route_id,))
        log_event(conn, "rutas", route_id, "UPDATE", "Ruta actualizada")
        log_field_changes(conn, "rutas", route_id, before, data, fields, "UPDATE", motivo, comentario, usuario)
        if critical_changed:
            log_change(conn, "rutas", route_id, "INVALIDATE_GPS", "matches_gps", "vigentes", "pendientes", motivo, "Cambio crítico de fecha/unidad/hora; conciliación invalidada", usuario)
        conn.commit()


def set_route_status(route_id: int, status: str, note: str | None = None, motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT estado_ruta FROM rutas WHERE id = ?", (route_id,)).fetchone()
        conn.execute("UPDATE rutas SET estado_ruta = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (status, route_id))
        log_event(conn, "rutas", route_id, "STATUS", note or f"Estado cambiado a {status}")
        log_change(conn, "rutas", route_id, "STATUS", "estado_ruta", before["estado_ruta"] if before else None, status, motivo, comentario or note, usuario)
        conn.commit()


def get_route(route_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT r.*, u.placas, d.nombre AS conductor_nombre
            FROM rutas r
            JOIN unidades u ON u.id = r.unidad_id
            JOIN conductores d ON d.id = r.conductor_id
            WHERE r.id = ?
            """,
            (route_id,),
        ).fetchone()
        return dict(row) if row else None


def list_routes(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """List routes with captured delivery count.

    PostgreSQL is stricter than SQLite with GROUP BY. The previous query used
    SELECT r.*, u.placas, ... COUNT(e.id) GROUP BY r.id, which SQLite accepts
    but PostgreSQL rejects because u.placas/u.tipo_unidad/conductor_nombre are
    not grouped. To keep the query portable, the delivery count is calculated
    in a subquery and joined by ruta_id, avoiding GROUP BY in the outer SELECT.
    """
    filters = filters or {}
    sql = """
        SELECT
            r.*,
            u.placas,
            u.tipo_unidad,
            d.nombre AS conductor_nombre,
            COALESCE(ec.entregas_capturadas, 0) AS entregas_capturadas
        FROM rutas r
        JOIN unidades u ON u.id = r.unidad_id
        JOIN conductores d ON d.id = r.conductor_id
        LEFT JOIN (
            SELECT ruta_id, COUNT(*) AS entregas_capturadas
            FROM ruta_entregas
            WHERE COALESCE(activo, 1) = 1
            GROUP BY ruta_id
        ) ec ON ec.ruta_id = r.id
        WHERE 1=1
    """
    params: list[Any] = []
    if filters.get("active_only") is not False:
        sql += " AND COALESCE(r.activo, 1) = 1"
    if filters.get("solo_operativas"):
        sql += " AND COALESCE(r.tipo_ruta, 'OPERATIVA') = 'OPERATIVA'"
    if filters.get("tipo_ruta"):
        sql += " AND COALESCE(r.tipo_ruta, 'OPERATIVA') = ?"
        params.append(filters["tipo_ruta"])
    if filters.get("unidad_id"):
        sql += " AND r.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("conductor_id"):
        sql += " AND r.conductor_id = ?"
        params.append(filters["conductor_id"])
    if filters.get("estado_ruta"):
        sql += " AND r.estado_ruta = ?"
        params.append(filters["estado_ruta"])
    if filters.get("fecha_desde"):
        sql += " AND r.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND r.fecha <= ?"
        params.append(filters["fecha_hasta"])
    sql += " ORDER BY r.fecha DESC, r.id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def mark_route_type(route_id: int, tipo_ruta: str, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    """Mark a route as OPERATIVA, PRUEBA or CAPACITACION without deleting it."""
    tipo_ruta = (tipo_ruta or "OPERATIVA").upper()
    if tipo_ruta not in {"OPERATIVA", "PRUEBA", "CAPACITACION"}:
        raise ValueError("tipo_ruta debe ser OPERATIVA, PRUEBA o CAPACITACION")
    with get_connection() as conn:
        before = conn.execute("SELECT tipo_ruta FROM rutas WHERE id = ?", (route_id,)).fetchone()
        conn.execute("UPDATE rutas SET tipo_ruta=?, actualizado_en=CURRENT_TIMESTAMP WHERE id=?", (tipo_ruta, route_id))
        log_event(conn, "rutas", route_id, "TIPO_RUTA", f"Tipo de ruta cambiado a {tipo_ruta}")
        log_change(conn, "rutas", route_id, "TIPO_RUTA", "tipo_ruta", before["tipo_ruta"] if before else None, tipo_ruta, motivo, comentario, usuario)
        conn.commit()


def annul_route_for_testing(route_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    """Soft-annul a test/training route and exclude it from normal reports.

    The records remain in SQLite for traceability; active=0 hides the route from
    operational views, while tipo_ruta keeps the test/training context.
    """
    if not motivo or not motivo.strip():
        raise ValueError("El motivo de anulación es obligatorio.")
    with get_connection() as conn:
        before = conn.execute("SELECT activo, estado_ruta, tipo_ruta FROM rutas WHERE id = ?", (route_id,)).fetchone()
        if not before:
            raise ValueError("No se encontró la ruta.")
        conn.execute(
            """UPDATE rutas
               SET activo=0, estado_ruta='ANULADA_PRUEBA', tipo_ruta=CASE WHEN COALESCE(tipo_ruta,'OPERATIVA')='OPERATIVA' THEN 'PRUEBA' ELSE tipo_ruta END,
                   motivo_anulacion=?, anulado_en=CURRENT_TIMESTAMP, anulado_por=?, actualizado_en=CURRENT_TIMESTAMP
               WHERE id=?""",
            (motivo.strip() + (f" | {comentario.strip()}" if comentario else ""), usuario, route_id),
        )
        conn.execute("DELETE FROM entrega_gps_match WHERE entrega_id IN (SELECT id FROM ruta_entregas WHERE ruta_id=?)", (route_id,))
        log_event(conn, "rutas", route_id, "ANULAR_PRUEBA", motivo)
        log_change(conn, "rutas", route_id, "ANULAR_PRUEBA", "activo", before["activo"], 0, motivo, comentario, usuario)
        log_change(conn, "rutas", route_id, "ANULAR_PRUEBA", "estado_ruta", before["estado_ruta"], "ANULADA_PRUEBA", motivo, comentario, usuario)
        conn.commit()


def delete_route_permanently(route_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> dict[str, int]:
    """Permanently delete a route and child records. Development-only cleanup.

    This is intentionally stricter than soft-annulment and should only be exposed
    to Administrador for test/demo routes. It records an audit event before delete.
    """
    if not motivo or not motivo.strip():
        raise ValueError("El motivo de eliminación es obligatorio.")
    with get_connection() as conn:
        route = conn.execute("SELECT * FROM rutas WHERE id=?", (route_id,)).fetchone()
        if not route:
            raise ValueError("No se encontró la ruta.")
        route_dict = dict(route)
        deliveries = conn.execute("SELECT id FROM ruta_entregas WHERE ruta_id=?", (route_id,)).fetchall()
        delivery_ids = [int(r["id"]) for r in deliveries]
        counts = {"matches": 0, "evidencias": 0, "adjuntos": 0, "entregas": len(delivery_ids), "gastos": 0, "ruta": 1}
        log_event(conn, "rutas", route_id, "DELETE_PERMANENT_PRE", f"Eliminación definitiva solicitada. Motivo: {motivo}")
        log_change(conn, "rutas", route_id, "DELETE_PERMANENT_PRE", None, route_dict, None, motivo, comentario, usuario)
        if delivery_ids:
            ph = ",".join("?" for _ in delivery_ids)
            counts["matches"] = conn.execute(f"SELECT COUNT(*) AS n FROM entrega_gps_match WHERE entrega_id IN ({ph})", delivery_ids).fetchone()["n"]
            counts["evidencias"] = conn.execute(f"SELECT COUNT(*) AS n FROM ruta_entrega_evidencias WHERE entrega_id IN ({ph})", delivery_ids).fetchone()["n"]
            counts["adjuntos"] += conn.execute(f"SELECT COUNT(*) AS n FROM archivos_adjuntos WHERE tabla_origen='ruta_entregas' AND registro_id IN ({ph})", delivery_ids).fetchone()["n"]
            conn.execute(f"DELETE FROM entrega_gps_match WHERE entrega_id IN ({ph})", delivery_ids)
            conn.execute(f"DELETE FROM ruta_entrega_evidencias WHERE entrega_id IN ({ph})", delivery_ids)
            conn.execute(f"DELETE FROM archivos_adjuntos WHERE tabla_origen='ruta_entregas' AND registro_id IN ({ph})", delivery_ids)
        counts["gastos"] = conn.execute("SELECT COUNT(*) AS n FROM gastos_operativos WHERE ruta_id=?", (route_id,)).fetchone()["n"] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gastos_operativos'").fetchone() else 0
        conn.execute("DELETE FROM gastos_operativos WHERE ruta_id=?", (route_id,))
        conn.execute("DELETE FROM ruta_entregas WHERE ruta_id=?", (route_id,))
        conn.execute("DELETE FROM rutas WHERE id=?", (route_id,))
        # Keep audit rows/events; they are the only trace after hard deletion.
        conn.commit()
        return {k: int(v) for k, v in counts.items()}


def create_delivery(data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO ruta_entregas (
                ruta_id, cliente_nombre, destino_nombre, destino_id, hora_llegada_reportada,
                hora_captura_sistema, estatus_entrega, motivo_no_entrega,
                observaciones, estado_conciliacion_gps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["ruta_id"], data["cliente_nombre"], data["destino_nombre"], data.get("destino_id"), data["hora_llegada_reportada"],
                data.get("hora_captura_sistema") or datetime.now().isoformat(sep=" ", timespec="seconds"),
                data["estatus_entrega"], data.get("motivo_no_entrega"), data.get("observaciones"),
                data.get("estado_conciliacion_gps", "Pendiente de GPS"),
            ),
        )
        delivery_id = int(cur.lastrowid)
        log_event(conn, "ruta_entregas", delivery_id, "INSERT", f"Entrega capturada en ruta_id={data['ruta_id']}")
        log_change(conn, "ruta_entregas", delivery_id, "INSERT", None, None, data, motivo, comentario, usuario)
        conn.commit()
    recalculate_delivery_order(data["ruta_id"])
    return delivery_id


def update_delivery(delivery_id: int, data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    fields = ["cliente_nombre", "destino_nombre", "destino_id", "hora_llegada_reportada", "estatus_entrega", "motivo_no_entrega", "observaciones", "estado_conciliacion_gps"]
    with get_connection() as conn:
        before_row = conn.execute("SELECT * FROM ruta_entregas WHERE id = ?", (delivery_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        hora_changed = str(before.get("hora_llegada_reportada")) != str(data.get("hora_llegada_reportada"))
        conn.execute(
            """
            UPDATE ruta_entregas
            SET cliente_nombre = ?, destino_nombre = ?, destino_id = ?, hora_llegada_reportada = ?,
                estatus_entrega = ?, motivo_no_entrega = ?, observaciones = ?,
                estado_conciliacion_gps = ?, actualizado_en = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data["cliente_nombre"], data["destino_nombre"], data.get("destino_id"), data["hora_llegada_reportada"],
                data["estatus_entrega"], data.get("motivo_no_entrega"), data.get("observaciones"),
                "Pendiente de GPS" if hora_changed else data.get("estado_conciliacion_gps", before.get("estado_conciliacion_gps", "Pendiente de GPS")),
                delivery_id,
            ),
        )
        if hora_changed:
            conn.execute("DELETE FROM entrega_gps_match WHERE entrega_id = ?", (delivery_id,))
            log_change(conn, "ruta_entregas", delivery_id, "INVALIDATE_GPS", "match_gps", "vigente", "pendiente", motivo, "Cambio de hora de llegada; conciliación invalidada", usuario)
        log_event(conn, "ruta_entregas", delivery_id, "UPDATE", "Entrega actualizada")
        after = data.copy()
        if hora_changed:
            after["estado_conciliacion_gps"] = "Pendiente de GPS"
        log_field_changes(conn, "ruta_entregas", delivery_id, before, after, fields, "UPDATE", motivo, comentario, usuario)
        conn.commit()
    delivery = get_delivery(delivery_id)
    if delivery:
        recalculate_delivery_order(delivery["ruta_id"])
        # If a correction invalidated or changed GPS reconciliation, keep route status coherent.
        try:
            from modules.gps_matcher import recalculate_route_status
            recalculate_route_status(delivery["ruta_id"], motivo=motivo, comentario="Recalculo posterior a corrección de entrega", usuario=usuario)
        except Exception:
            pass


def get_delivery(delivery_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM ruta_entregas WHERE id = ?", (delivery_id,)).fetchone()
        return dict(row) if row else None


def list_deliveries(route_id: int | None = None, filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT e.*, r.fecha, r.unidad_id, r.conductor_id, u.placas, d.nombre AS conductor_nombre,
               m.tipo_match, m.hora_salida_inferida, m.tiempo_en_cliente_seg, m.confianza,
               p.inicio_gps, p.fin_gps, p.duracion_seg AS duracion_parada_gps_seg, p.direccion_gps
        FROM ruta_entregas e
        JOIN rutas r ON r.id = e.ruta_id
        JOIN unidades u ON u.id = r.unidad_id
        JOIN conductores d ON d.id = r.conductor_id
        LEFT JOIN entrega_gps_match m ON m.entrega_id = e.id
        LEFT JOIN gps_paradas p ON p.id = m.gps_parada_id
        WHERE e.activo = 1
    """
    params: list[Any] = []
    if route_id:
        sql += " AND e.ruta_id = ?"
        params.append(route_id)
    if filters.get("estado_conciliacion_gps"):
        sql += " AND e.estado_conciliacion_gps = ?"
        params.append(filters["estado_conciliacion_gps"])
    if filters.get("fecha_desde"):
        sql += " AND r.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND r.fecha <= ?"
        params.append(filters["fecha_hasta"])
    sql += " ORDER BY r.fecha DESC, r.id DESC, e.orden_calculado ASC, e.hora_llegada_reportada ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def recalculate_delivery_order(route_id: int) -> None:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM ruta_entregas WHERE ruta_id = ? AND activo = 1 ORDER BY hora_llegada_reportada ASC, id ASC", (route_id,)).fetchall()
        for idx, row in enumerate(rows, start=1):
            conn.execute("UPDATE ruta_entregas SET orden_calculado = ? WHERE id = ?", (idx, row["id"]))
        conn.commit()


def soft_delete_delivery(delivery_id: int, motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        row = conn.execute("SELECT ruta_id, activo FROM ruta_entregas WHERE id = ?", (delivery_id,)).fetchone()
        conn.execute("UPDATE ruta_entregas SET activo = 0, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (delivery_id,))
        conn.execute("DELETE FROM entrega_gps_match WHERE entrega_id = ?", (delivery_id,))
        log_event(conn, "ruta_entregas", delivery_id, "SOFT_DELETE", "Entrega dada de baja")
        log_change(conn, "ruta_entregas", delivery_id, "SOFT_DELETE", "activo", row["activo"] if row else None, 0, motivo, comentario, usuario)
        conn.commit()
    if row:
        recalculate_delivery_order(row["ruta_id"])
        try:
            from modules.gps_matcher import recalculate_route_status
            recalculate_route_status(row["ruta_id"], motivo=motivo, comentario="Recalculo posterior a baja de entrega", usuario=usuario)
        except Exception:
            pass


def save_evidence_file(uploaded_file, route: dict[str, Any], delivery_id: int) -> str | None:
    if uploaded_file is None:
        return None
    fecha = str(route["fecha"])
    y, m, d = fecha.split("-")
    plate = str(route.get("placas") or "unidad").replace(" ", "_")
    target_dir = EVIDENCIAS_DIR / y / m / d / plate
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_file.name).suffix.lower() or ".jpg"
    safe_name = f"ruta_{route['id']}_entrega_{delivery_id}_{datetime.now().strftime('%H%M%S')}{suffix}"
    target = target_dir / safe_name
    with target.open("wb") as out:
        shutil.copyfileobj(uploaded_file, out)
    try:
        return str(target.resolve().relative_to(APP_DIR.resolve()))
    except Exception:
        # Portable path inside the project when possible.
        return str(target)


def add_delivery_evidence(delivery_id: int, file_path: str, tipo_evidencia: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    rel_path = to_relative_path(file_path) or file_path
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO ruta_entrega_evidencias (
                entrega_id, ruta_archivo, tipo_evidencia, comentario, estado_evidencia
            ) VALUES (?, ?, ?, ?, 'activo')""",
            (delivery_id, rel_path, tipo_evidencia, comentario),
        )
        evidence_id = int(cur.lastrowid)
        log_event(conn, "ruta_entrega_evidencias", evidence_id, "INSERT", f"Evidencia agregada a entrega_id={delivery_id}")
        log_change(conn, "ruta_entrega_evidencias", evidence_id, "INSERT", None, None, {"entrega_id": delivery_id, "ruta_archivo": rel_path, "tipo_evidencia": tipo_evidencia}, "Alta de evidencia", comentario, usuario)
        register_attachment(conn, "ruta_entregas", delivery_id, "evidencia_entrega", rel_path, motivo="Alta de evidencia", comentario=comentario, usuario=usuario, replace_existing=False)
        conn.commit()
        return evidence_id


def list_evidences(delivery_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM ruta_entrega_evidencias WHERE entrega_id = ? AND COALESCE(estado_evidencia,'activo')='activo' ORDER BY id", (delivery_id,)).fetchall()
        return _dicts(rows)



def annul_delivery_evidence(evidence_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT * FROM ruta_entrega_evidencias WHERE id = ?", (evidence_id,)).fetchone()
        before_dict = dict(before) if before else {}
        conn.execute(
            """UPDATE ruta_entrega_evidencias
            SET estado_evidencia='anulada', motivo_anulacion=?, anulado_en=CURRENT_TIMESTAMP, anulado_por=?
            WHERE id = ?""",
            (motivo, usuario, evidence_id),
        )
        log_change(conn, "ruta_entrega_evidencias", evidence_id, "ANULAR", "estado_evidencia", before_dict.get("estado_evidencia"), "anulada", motivo, comentario, usuario)
        conn.commit()

def upsert_destination(data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    fields = ["nombre_normalizado", "alias", "tipo_destino", "cliente_asociado", "cliente_comercial", "direccion_texto", "latitud", "longitud", "validado", "fuente", "observaciones", "excluir_alertas_inactividad", "radio_metros", "contacto", "horario_recepcion", "requiere_cita", "tiempo_promedio_servicio_min", "activo"]
    with get_connection() as conn:
        if data.get("id"):
            before_row = conn.execute("SELECT * FROM destinos WHERE id = ?", (data["id"],)).fetchone()
            before = dict(before_row) if before_row else {}
            conn.execute(
                """
                UPDATE destinos
                SET nombre_normalizado = ?, alias = ?, tipo_destino = ?, cliente_asociado = ?, cliente_comercial = ?,
                    direccion_texto = ?, latitud = ?, longitud = ?, validado = ?, fuente = ?,
                    observaciones = ?, excluir_alertas_inactividad = ?, radio_metros = ?, contacto = ?,
                    horario_recepcion = ?, requiere_cita = ?, tiempo_promedio_servicio_min = ?, activo = ?,
                    actualizado_en = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    data["nombre_normalizado"], data.get("alias"), data.get("tipo_destino"), data.get("cliente_asociado"), data.get("cliente_comercial"),
                    data.get("direccion_texto"), data.get("latitud"), data.get("longitud"), data.get("validado", 0),
                    data.get("fuente", "captura_manual"), data.get("observaciones"),
                    data.get("excluir_alertas_inactividad", 0), data.get("radio_metros", 100), data.get("contacto"),
                    data.get("horario_recepcion"), data.get("requiere_cita", 0), data.get("tiempo_promedio_servicio_min"),
                    data.get("activo", 1), data["id"],
                ),
            )
            destination_id = int(data["id"])
            log_event(conn, "destinos", destination_id, "UPDATE", "Destino actualizado")
            log_field_changes(conn, "destinos", destination_id, before, data, fields, "UPDATE", motivo, comentario, usuario)
        else:
            cur = conn.execute(
                """
                INSERT INTO destinos (
                    nombre_normalizado, alias, tipo_destino, cliente_asociado, cliente_comercial, direccion_texto,
                    latitud, longitud, validado, fuente, observaciones,
                    excluir_alertas_inactividad, radio_metros, contacto, horario_recepcion, requiere_cita,
                    tiempo_promedio_servicio_min, activo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["nombre_normalizado"], data.get("alias"), data.get("tipo_destino"), data.get("cliente_asociado"), data.get("cliente_comercial"),
                    data.get("direccion_texto"), data.get("latitud"), data.get("longitud"), data.get("validado", 0),
                    data.get("fuente", "captura_manual"), data.get("observaciones"),
                    data.get("excluir_alertas_inactividad", 0), data.get("radio_metros", 100), data.get("contacto"),
                    data.get("horario_recepcion"), data.get("requiere_cita", 0), data.get("tiempo_promedio_servicio_min"),
                    data.get("activo", 1),
                ),
            )
            destination_id = int(cur.lastrowid)
            log_event(conn, "destinos", destination_id, "INSERT", "Destino creado")
            log_change(conn, "destinos", destination_id, "INSERT", None, None, data, motivo, comentario, usuario)
        conn.commit()
        return destination_id


def merge_destinations(source_id: int, target_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    if source_id == target_id:
        raise ValueError("El destino origen y destino no pueden ser el mismo.")
    with get_connection() as conn:
        source = conn.execute("SELECT * FROM destinos WHERE id = ?", (source_id,)).fetchone()
        target = conn.execute("SELECT * FROM destinos WHERE id = ?", (target_id,)).fetchone()
        if not source or not target:
            raise ValueError("No se encontró destino origen o destino.")
        conn.execute("UPDATE ruta_entregas SET destino_id = ? WHERE destino_id = ?", (target_id, source_id))
        conn.execute("UPDATE destinos SET activo = 0, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (source_id,))
        log_event(conn, "destinos", source_id, "MERGE", f"Fusionado en destino_id={target_id}; entregas vinculadas reasignadas")
        log_change(conn, "destinos", source_id, "MERGE", "fusionado_en", source_id, target_id, motivo, comentario, usuario)
        conn.commit()


def list_destinations(active_only: bool = True) -> pd.DataFrame:
    sql = "SELECT * FROM destinos WHERE 1=1"
    params = []
    if active_only:
        sql += " AND activo = 1"
    sql += " ORDER BY validado ASC, nombre_normalizado ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def destination_candidates_from_deliveries(limit: int = 200) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.destino_nombre, e.cliente_nombre, COUNT(*) AS veces,
                   MIN(r.fecha) AS primera_fecha, MAX(r.fecha) AS ultima_fecha
            FROM ruta_entregas e
            JOIN rutas r ON r.id = e.ruta_id
            WHERE e.activo = 1
              AND NOT EXISTS (
                  SELECT 1 FROM destinos d
                  WHERE LOWER(d.nombre_normalizado) = LOWER(e.destino_nombre)
                    AND d.activo = 1
              )
            GROUP BY e.destino_nombre, e.cliente_nombre
            ORDER BY veces DESC, ultima_fecha DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def route_summary_metrics() -> dict[str, Any]:
    with get_connection() as conn:
        return {
            "rutas_abiertas": conn.execute("SELECT COUNT(*) AS n FROM rutas WHERE activo=1 AND COALESCE(tipo_ruta,'OPERATIVA')='OPERATIVA' AND estado_ruta='Abierta'").fetchone()["n"],
            "rutas_pendientes_gps": conn.execute("SELECT COUNT(*) AS n FROM rutas WHERE activo=1 AND COALESCE(tipo_ruta,'OPERATIVA')='OPERATIVA' AND estado_ruta='Cerrada pendiente de GPS'").fetchone()["n"],
            "entregas_pendientes_gps": conn.execute("SELECT COUNT(*) AS n FROM ruta_entregas WHERE activo=1 AND estado_conciliacion_gps='Pendiente de GPS'").fetchone()["n"],
            "entregas_sin_match": conn.execute("SELECT COUNT(*) AS n FROM ruta_entregas WHERE activo=1 AND estado_conciliacion_gps='Sin GPS asociado'").fetchone()["n"],
            "paradas_revision": conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM gps_paradas p
                JOIN gps_importaciones gi ON gi.id = p.importacion_id AND COALESCE(gi.activo,1)=1
                LEFT JOIN entrega_gps_match m ON m.gps_parada_id = p.id
                LEFT JOIN gps_paradas_clasificacion c ON c.gps_parada_id = p.id AND COALESCE(c.activo,1)=1
                WHERE p.requiere_revision = 1
                  AND m.id IS NULL
                  AND c.id IS NULL
                """
            ).fetchone()["n"],
        }


def _audit(conn, tabla: str, registro_id: int, accion: str, detalle: str) -> None:
    log_event(conn, tabla, registro_id, accion, detalle)
