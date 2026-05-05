from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from modules.audit import log_change, log_event, log_field_changes
from modules.operational_quality import classify_charge_quality
from modules.traceability import register_attachment, to_relative_path
from modules.db import APP_DIR, TICKETS_DIR, get_connection


def _dicts(rows):
    return [dict(r) for r in rows]


def list_units(active_only: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        sql = "SELECT * FROM unidades"
        params = []
        if active_only:
            sql += " WHERE activo = 1"
        sql += " ORDER BY placas"
        return _dicts(conn.execute(sql, params).fetchall())


def get_unit(unit_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM unidades WHERE id = ?", (unit_id,)).fetchone()
        return dict(row) if row else None


def get_checklist_by_unit(unit_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return _dicts(
            conn.execute("SELECT * FROM checklist_unidad WHERE unidad_id = ? ORDER BY item", (unit_id,)).fetchall()
        )


def upsert_unit(
    data: dict[str, Any],
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    fields = [
        "placas", "marca", "modelo", "color", "tipo_unidad", "combustible_preferido",
        "tipo_carga", "carga_garrafones", "periodo_habil", "limite_litros", "activo",
    ]
    with get_connection() as conn:
        if data.get("id"):
            before_row = conn.execute("SELECT * FROM unidades WHERE id = ?", (data["id"],)).fetchone()
            before = dict(before_row) if before_row else {}
            conn.execute(
                """
                UPDATE unidades
                SET placas = ?, marca = ?, modelo = ?, color = ?, tipo_unidad = ?,
                    combustible_preferido = ?, tipo_carga = ?, carga_garrafones = ?,
                    periodo_habil = ?, limite_litros = ?, activo = ?, actualizado_en = ?
                WHERE id = ?
                """,
                (
                    data["placas"], data.get("marca"), data.get("modelo"), data.get("color"),
                    data.get("tipo_unidad"), data.get("combustible_preferido"), data.get("tipo_carga"),
                    data.get("carga_garrafones"), data.get("periodo_habil"), data.get("limite_litros"),
                    data.get("activo", 1), now, data["id"],
                ),
            )
            log_event(conn, "unidades", int(data["id"]), "UPDATE", f"Unidad {data['placas']} actualizada")
            log_field_changes(conn, "unidades", int(data["id"]), before, data, fields, "UPDATE", motivo, comentario, usuario)
            conn.commit()
            return int(data["id"])

        cur = conn.execute(
            """
            INSERT INTO unidades (
                placas, marca, modelo, color, tipo_unidad, combustible_preferido,
                tipo_carga, carga_garrafones, periodo_habil, limite_litros, activo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["placas"], data.get("marca"), data.get("modelo"), data.get("color"),
                data.get("tipo_unidad"), data.get("combustible_preferido"), data.get("tipo_carga"),
                data.get("carga_garrafones"), data.get("periodo_habil"), data.get("limite_litros"),
                data.get("activo", 1),
            ),
        )
        new_id = int(cur.lastrowid)
        log_event(conn, "unidades", new_id, "INSERT", f"Unidad {data['placas']} creada")
        log_change(conn, "unidades", new_id, "INSERT", None, None, data, motivo, comentario, usuario)
        conn.commit()
        return new_id


def list_conductors(active_only: bool = False) -> list[dict[str, Any]]:
    with get_connection() as conn:
        sql = "SELECT * FROM conductores"
        if active_only:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre"
        return _dicts(conn.execute(sql).fetchall())


def get_conductor(conductor_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM conductores WHERE id = ?", (conductor_id,)).fetchone()
        return dict(row) if row else None


def upsert_conductor(
    data: dict[str, Any],
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
) -> int:
    with get_connection() as conn:
        if data.get("id"):
            before_row = conn.execute("SELECT * FROM conductores WHERE id = ?", (data["id"],)).fetchone()
            before = dict(before_row) if before_row else {}
            conn.execute(
                "UPDATE conductores SET nombre = ?, activo = ? WHERE id = ?",
                (data["nombre"], data.get("activo", 1), data["id"]),
            )
            log_event(conn, "conductores", int(data["id"]), "UPDATE", f"Conductor {data['nombre']} actualizado")
            log_field_changes(conn, "conductores", int(data["id"]), before, data, ["nombre", "activo"], "UPDATE", motivo, comentario, usuario)
            conn.commit()
            return int(data["id"])

        cur = conn.execute("INSERT INTO conductores (nombre, activo) VALUES (?, ?)", (data["nombre"], data.get("activo", 1)))
        new_id = int(cur.lastrowid)
        log_event(conn, "conductores", new_id, "INSERT", f"Conductor {data['nombre']} creado")
        log_change(conn, "conductores", new_id, "INSERT", None, None, data, motivo, comentario, usuario)
        conn.commit()
        return new_id


def merge_conductors(
    source_id: int,
    target_id: int,
    motivo: str,
    comentario: str | None = None,
    usuario: str | None = None,
) -> None:
    if source_id == target_id:
        raise ValueError("El conductor origen y destino no pueden ser el mismo.")
    with get_connection() as conn:
        source = conn.execute("SELECT * FROM conductores WHERE id = ?", (source_id,)).fetchone()
        target = conn.execute("SELECT * FROM conductores WHERE id = ?", (target_id,)).fetchone()
        if not source or not target:
            raise ValueError("No se encontró conductor origen o destino.")
        conn.execute("UPDATE cargas_combustible SET conductor_id = ? WHERE conductor_id = ?", (target_id, source_id))
        conn.execute("UPDATE rutas SET conductor_id = ? WHERE conductor_id = ?", (target_id, source_id))
        conn.execute("UPDATE conductores SET activo = 0 WHERE id = ?", (source_id,))
        log_event(conn, "conductores", source_id, "MERGE", f"Fusionado en conductor_id={target_id}")
        log_change(conn, "conductores", source_id, "MERGE", "fusionado_en", source_id, target_id, motivo, comentario, usuario)
        conn.commit()


def save_ticket_image(uploaded_file, unit_plate: str) -> str | None:
    if uploaded_file is None:
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{unit_plate}_{timestamp}_{uploaded_file.name}".replace(" ", "_")
    target = TICKETS_DIR / safe_name
    with target.open("wb") as out:
        shutil.copyfileobj(uploaded_file, out)
    # Store portable project-relative paths. Existing absolute paths remain readable.
    try:
        return str(target.resolve().relative_to(APP_DIR.resolve()))
    except Exception:
        return str(target)


def get_last_charge_for_unit(unit_id: int, exclude_charge_id: int | None = None) -> dict[str, Any] | None:
    with get_connection() as conn:
        sql = """
            SELECT *
            FROM cargas_combustible
            WHERE unidad_id = ? AND activo = 1
        """
        params = [unit_id]
        if exclude_charge_id:
            sql += " AND id <> ?"
            params.append(exclude_charge_id)
        sql += " ORDER BY fecha_carga DESC, hora_carga DESC, id DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def find_possible_duplicate(unit_id: int, fecha_carga: str, litros: float, importe_total: float, ticket_folio: str | None, exclude_charge_id: int | None = None) -> dict[str, Any] | None:
    with get_connection() as conn:
        sql = """
            SELECT *
            FROM cargas_combustible
            WHERE unidad_id = ?
              AND fecha_carga = ?
              AND ABS(litros - ?) < 0.01
              AND ABS(importe_total - ?) < 0.01
              AND activo = 1
        """
        params = [unit_id, fecha_carga, litros, importe_total]
        if ticket_folio:
            sql += " AND COALESCE(ticket_folio, '') = ?"
            params.append(ticket_folio)
        if exclude_charge_id:
            sql += " AND id <> ?"
            params.append(exclude_charge_id)
        sql += " ORDER BY id DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def create_charge(data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO cargas_combustible (
                unidad_id, conductor_id, fecha_carga, hora_carga, gasolinera,
                estacion_direccion, ticket_folio, tipo_combustible, precio_litro,
                litros, importe_total, kilometraje, metodo_pago, observaciones,
                imagen_ticket_path, ocr_texto, origen_registro, estado_validacion, alerta_resumen,
                tipo_carga_combustible, calidad_registro
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["unidad_id"], data.get("conductor_id"), data["fecha_carga"], data.get("hora_carga"),
                data.get("gasolinera"), data.get("estacion_direccion"), data.get("ticket_folio"),
                data.get("tipo_combustible"), data["precio_litro"], data["litros"], data["importe_total"],
                data.get("kilometraje"), data.get("metodo_pago"), data.get("observaciones"),
                to_relative_path(data.get("imagen_ticket_path")), data.get("ocr_texto"), data.get("origen_registro", "manual"),
                data.get("estado_validacion", "VALIDADO"), data.get("alerta_resumen"),
                data.get("tipo_carga_combustible", "No especificada"),
                data.get("calidad_registro") or classify_charge_quality(data),
            ),
        )
        charge_id = int(cur.lastrowid)
        log_event(conn, "cargas_combustible", charge_id, "INSERT", f"Carga registrada para unidad_id={data['unidad_id']}")
        log_change(conn, "cargas_combustible", charge_id, "INSERT", None, None, data, motivo, comentario, usuario)
        if data.get("imagen_ticket_path"):
            register_attachment(conn, "cargas_combustible", charge_id, "ticket_combustible", data["imagen_ticket_path"], motivo="Alta de carga", comentario="Ticket adjunto al crear carga", usuario=usuario, replace_existing=False)
        conn.commit()
        return charge_id


def update_charge(
    charge_id: int,
    data: dict[str, Any],
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    fields = [
        "unidad_id", "conductor_id", "fecha_carga", "hora_carga", "gasolinera", "estacion_direccion",
        "ticket_folio", "tipo_combustible", "precio_litro", "litros", "importe_total", "kilometraje",
        "metodo_pago", "observaciones", "imagen_ticket_path", "ocr_texto", "origen_registro",
        "estado_validacion", "alerta_resumen", "tipo_carga_combustible", "calidad_registro",
    ]
    with get_connection() as conn:
        before_row = conn.execute("SELECT * FROM cargas_combustible WHERE id = ?", (charge_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        conn.execute(
            """
            UPDATE cargas_combustible
            SET unidad_id = ?, conductor_id = ?, fecha_carga = ?, hora_carga = ?, gasolinera = ?,
                estacion_direccion = ?, ticket_folio = ?, tipo_combustible = ?, precio_litro = ?,
                litros = ?, importe_total = ?, kilometraje = ?, metodo_pago = ?, observaciones = ?,
                imagen_ticket_path = COALESCE(?, imagen_ticket_path),
                ocr_texto = COALESCE(?, ocr_texto),
                origen_registro = ?, estado_validacion = ?, alerta_resumen = ?,
                tipo_carga_combustible = ?, calidad_registro = ?, actualizado_en = ?
            WHERE id = ?
            """,
            (
                data["unidad_id"], data.get("conductor_id"), data["fecha_carga"], data.get("hora_carga"),
                data.get("gasolinera"), data.get("estacion_direccion"), data.get("ticket_folio"),
                data.get("tipo_combustible"), data["precio_litro"], data["litros"], data["importe_total"],
                data.get("kilometraje"), data.get("metodo_pago"), data.get("observaciones"),
                to_relative_path(data.get("imagen_ticket_path")), data.get("ocr_texto"), data.get("origen_registro", "manual"),
                data.get("estado_validacion", "VALIDADO"), data.get("alerta_resumen"),
                data.get("tipo_carga_combustible", "No especificada"),
                data.get("calidad_registro") or classify_charge_quality(data), now, charge_id,
            ),
        )
        after = data.copy()
        if not after.get("imagen_ticket_path"):
            after["imagen_ticket_path"] = before.get("imagen_ticket_path")
        else:
            after["imagen_ticket_path"] = to_relative_path(after.get("imagen_ticket_path"))
        if not after.get("ocr_texto"):
            after["ocr_texto"] = before.get("ocr_texto")
        log_event(conn, "cargas_combustible", charge_id, "UPDATE", "Carga actualizada")
        log_field_changes(conn, "cargas_combustible", charge_id, before, after, fields, "UPDATE", motivo, comentario, usuario)
        if data.get("imagen_ticket_path") and to_relative_path(data.get("imagen_ticket_path")) != before.get("imagen_ticket_path"):
            register_attachment(conn, "cargas_combustible", charge_id, "ticket_combustible", data["imagen_ticket_path"], motivo=motivo or "Reemplazo/alta de ticket", comentario=comentario, usuario=usuario, replace_existing=True)
        conn.commit()


def soft_delete_charge(charge_id: int, motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before_row = conn.execute("SELECT * FROM cargas_combustible WHERE id = ?", (charge_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        conn.execute("UPDATE cargas_combustible SET activo = 0, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (charge_id,))
        log_event(conn, "cargas_combustible", charge_id, "SOFT_DELETE", "Carga dada de baja lógica")
        log_change(conn, "cargas_combustible", charge_id, "SOFT_DELETE", "activo", before.get("activo"), 0, motivo, comentario, usuario)
        conn.commit()


def set_validation_status(charge_id: int, status: str, note: str | None = None) -> None:
    with get_connection() as conn:
        before_row = conn.execute("SELECT estado_validacion FROM cargas_combustible WHERE id = ?", (charge_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        conn.execute("UPDATE cargas_combustible SET estado_validacion = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (status, charge_id))
        log_event(conn, "cargas_combustible", charge_id, "VALIDATION", note or f"Estatus cambiado a {status}")
        log_change(conn, "cargas_combustible", charge_id, "VALIDATION", "estado_validacion", before.get("estado_validacion"), status, "Cambio de validación", note)
        conn.commit()


def get_charge(charge_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT c.*, u.placas, u.combustible_preferido, u.limite_litros, d.nombre AS conductor_nombre
            FROM cargas_combustible c
            JOIN unidades u ON u.id = c.unidad_id
            LEFT JOIN conductores d ON d.id = c.conductor_id
            WHERE c.id = ?
            """,
            (charge_id,),
        ).fetchone()
        return dict(row) if row else None


def list_charges(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT c.*, u.placas, u.marca, u.modelo, u.combustible_preferido, u.limite_litros,
               d.nombre AS conductor_nombre
        FROM cargas_combustible c
        JOIN unidades u ON u.id = c.unidad_id
        LEFT JOIN conductores d ON d.id = c.conductor_id
        WHERE 1 = 1
    """
    params = []
    if filters.get("active_only", True):
        sql += " AND c.activo = 1"
    if filters.get("unidad_id"):
        sql += " AND c.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("conductor_id"):
        sql += " AND c.conductor_id = ?"
        params.append(filters["conductor_id"])
    if filters.get("estado_validacion"):
        sql += " AND c.estado_validacion = ?"
        params.append(filters["estado_validacion"])
    if filters.get("tipo_combustible"):
        sql += " AND c.tipo_combustible = ?"
        params.append(filters["tipo_combustible"])
    if filters.get("fecha_desde"):
        sql += " AND c.fecha_carga >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND c.fecha_carga <= ?"
        params.append(filters["fecha_hasta"])
    sql += " ORDER BY c.fecha_carga DESC, c.hora_carga DESC, c.id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def list_audit(limit: int = 200) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM auditoria_eventos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _audit(conn, tabla: str, registro_id: int, accion: str, detalle: str) -> None:
    log_event(conn, tabla, registro_id, accion, detalle)
