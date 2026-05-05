from __future__ import annotations

from typing import Any
import pandas as pd

from modules.audit import log_change, log_event, log_field_changes
from modules.db import get_connection


def create_operational_cost(data: dict[str, Any], motivo: str | None = None, comentario: str | None = None, usuario: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO gastos_operativos (
                fecha, unidad_id, ruta_id, tipo_gasto, proveedor, folio, importe,
                metodo_pago, descripcion, estado_validacion, activo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                data["fecha"], data.get("unidad_id"), data.get("ruta_id"), data["tipo_gasto"],
                data.get("proveedor"), data.get("folio"), data["importe"], data.get("metodo_pago"),
                data.get("descripcion"), data.get("estado_validacion", "PENDIENTE_VALIDACION"),
            ),
        )
        cost_id = int(cur.lastrowid)
        log_event(conn, "gastos_operativos", cost_id, "INSERT", f"Gasto operativo {data['tipo_gasto']} registrado")
        log_change(conn, "gastos_operativos", cost_id, "INSERT", None, None, data, motivo or "Alta de gasto", comentario, usuario)
        conn.commit()
        return cost_id


def update_operational_cost(cost_id: int, data: dict[str, Any], motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    fields = ["fecha", "unidad_id", "ruta_id", "tipo_gasto", "proveedor", "folio", "importe", "metodo_pago", "descripcion", "estado_validacion", "activo"]
    with get_connection() as conn:
        before_row = conn.execute("SELECT * FROM gastos_operativos WHERE id=?", (cost_id,)).fetchone()
        before = dict(before_row) if before_row else {}
        conn.execute(
            """
            UPDATE gastos_operativos
            SET fecha=?, unidad_id=?, ruta_id=?, tipo_gasto=?, proveedor=?, folio=?, importe=?,
                metodo_pago=?, descripcion=?, estado_validacion=?, activo=?, actualizado_en=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                data["fecha"], data.get("unidad_id"), data.get("ruta_id"), data["tipo_gasto"],
                data.get("proveedor"), data.get("folio"), data["importe"], data.get("metodo_pago"),
                data.get("descripcion"), data.get("estado_validacion", "PENDIENTE_VALIDACION"),
                int(data.get("activo", 1)), cost_id,
            ),
        )
        log_event(conn, "gastos_operativos", cost_id, "UPDATE", "Gasto operativo actualizado")
        log_field_changes(conn, "gastos_operativos", cost_id, before, data, fields, "UPDATE", motivo, comentario, usuario)
        conn.commit()


def list_operational_costs(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    sql = """
        SELECT g.*, u.placas, r.fecha AS fecha_ruta
        FROM gastos_operativos g
        LEFT JOIN unidades u ON u.id = g.unidad_id
        LEFT JOIN rutas r ON r.id = g.ruta_id
        WHERE COALESCE(g.activo,1)=1
    """
    params: list[Any] = []
    if filters.get("fecha_desde"):
        sql += " AND g.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        sql += " AND g.fecha <= ?"
        params.append(filters["fecha_hasta"])
    if filters.get("unidad_id"):
        sql += " AND g.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("tipo_gasto"):
        sql += " AND g.tipo_gasto = ?"
        params.append(filters["tipo_gasto"])
    sql += " ORDER BY g.fecha DESC, g.id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def cost_summary(filters: dict[str, Any] | None = None) -> dict[str, float | int]:
    df = list_operational_costs(filters)
    if df.empty:
        return {"gastos": 0, "importe_total": 0.0}
    return {"gastos": int(len(df)), "importe_total": float(df["importe"].fillna(0).sum())}
