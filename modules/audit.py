from __future__ import annotations

from typing import Any
import json
import pandas as pd

from modules.db import get_connection


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def log_event(conn, tabla: str, registro_id: int, accion: str, detalle: str | None = None) -> None:
    conn.execute(
        "INSERT INTO auditoria_eventos (tabla, registro_id, accion, detalle) VALUES (?, ?, ?, ?)",
        (tabla, registro_id, accion, detalle),
    )


def log_change(
    conn,
    tabla: str,
    registro_id: int,
    accion: str,
    campo: str | None = None,
    valor_anterior: Any = None,
    valor_nuevo: Any = None,
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO auditoria_cambios (
            tabla, registro_id, campo, valor_anterior, valor_nuevo,
            accion, motivo, comentario, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tabla,
            registro_id,
            campo,
            _to_text(valor_anterior),
            _to_text(valor_nuevo),
            accion,
            motivo,
            comentario,
            usuario,
        ),
    )


def log_field_changes(
    conn,
    tabla: str,
    registro_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    fields: list[str],
    accion: str = "UPDATE",
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
) -> int:
    before = before or {}
    changes = 0
    for field in fields:
        old = before.get(field)
        new = after.get(field)
        if _to_text(old) != _to_text(new):
            log_change(conn, tabla, registro_id, accion, field, old, new, motivo, comentario, usuario)
            changes += 1
    if changes == 0:
        log_change(conn, tabla, registro_id, accion, None, None, None, motivo, comentario or "Sin cambios de campos", usuario)
    return changes


def list_audit_changes(filters: dict[str, Any] | None = None, limit: int = 500) -> pd.DataFrame:
    filters = filters or {}
    sql = "SELECT * FROM auditoria_cambios WHERE 1=1"
    params: list[Any] = []
    if filters.get("tabla") and filters["tabla"] != "Todas":
        sql += " AND tabla = ?"
        params.append(filters["tabla"])
    if filters.get("registro_id"):
        sql += " AND registro_id = ?"
        params.append(filters["registro_id"])
    if filters.get("accion") and filters["accion"] != "Todas":
        sql += " AND accion = ?"
        params.append(filters["accion"])
    if filters.get("fecha_desde"):
        sql += " AND date(creado_en) >= date(?)"
        params.append(str(filters["fecha_desde"]))
    if filters.get("fecha_hasta"):
        sql += " AND date(creado_en) <= date(?)"
        params.append(str(filters["fecha_hasta"]))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])
