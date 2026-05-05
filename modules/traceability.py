from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.audit import log_change, log_event
from modules.db import APP_DIR, get_connection


def to_relative_path(path: str | None) -> str | None:
    """Return a portable path relative to the app directory when possible."""
    if not path:
        return None
    text = str(path).strip()
    if not text:
        return None
    p = Path(text)
    try:
        if p.is_absolute():
            return str(p.resolve().relative_to(APP_DIR.resolve()))
    except Exception:
        pass
    return text.replace("\\\\", "/")


def resolve_app_path(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(str(path))
    return p if p.is_absolute() else APP_DIR / p


def register_attachment(
    conn,
    tabla_origen: str,
    registro_id: int,
    tipo_archivo: str,
    ruta_archivo: str,
    motivo: str | None = None,
    comentario: str | None = None,
    usuario: str | None = None,
    replace_existing: bool = False,
) -> int:
    rel_path = to_relative_path(ruta_archivo) or ruta_archivo
    if replace_existing:
        conn.execute(
            """
            UPDATE archivos_adjuntos
            SET estado_archivo = 'reemplazado', anulado_en = CURRENT_TIMESTAMP, anulado_por = ?
            WHERE tabla_origen = ? AND registro_id = ? AND tipo_archivo = ? AND estado_archivo = 'activo'
            """,
            (usuario, tabla_origen, registro_id, tipo_archivo),
        )
    cur = conn.execute(
        """
        INSERT INTO archivos_adjuntos (
            tabla_origen, registro_id, tipo_archivo, ruta_archivo, estado_archivo,
            motivo, comentario, usuario
        ) VALUES (?, ?, ?, ?, 'activo', ?, ?, ?)
        """,
        (tabla_origen, registro_id, tipo_archivo, rel_path, motivo, comentario, usuario),
    )
    attachment_id = int(cur.lastrowid)
    log_event(conn, "archivos_adjuntos", attachment_id, "INSERT", f"Adjunto {tipo_archivo} para {tabla_origen}#{registro_id}")
    log_change(conn, "archivos_adjuntos", attachment_id, "INSERT", None, None, {
        "tabla_origen": tabla_origen,
        "registro_id": registro_id,
        "tipo_archivo": tipo_archivo,
        "ruta_archivo": rel_path,
    }, motivo, comentario, usuario)
    return attachment_id


def list_attachments(tabla_origen: str | None = None, registro_id: int | None = None, active_only: bool = True):
    sql = "SELECT * FROM archivos_adjuntos WHERE 1=1"
    params: list[Any] = []
    if tabla_origen:
        sql += " AND tabla_origen = ?"
        params.append(tabla_origen)
    if registro_id:
        sql += " AND registro_id = ?"
        params.append(registro_id)
    if active_only:
        sql += " AND estado_archivo = 'activo'"
    sql += " ORDER BY creado_en DESC, id DESC"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def annul_attachment(attachment_id: int, motivo: str, comentario: str | None = None, usuario: str | None = None) -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT * FROM archivos_adjuntos WHERE id = ?", (attachment_id,)).fetchone()
        before_dict = dict(before) if before else {}
        conn.execute(
            """
            UPDATE archivos_adjuntos
            SET estado_archivo='anulado', motivo=?, comentario=COALESCE(?, comentario),
                anulado_en=CURRENT_TIMESTAMP, anulado_por=?
            WHERE id = ?
            """,
            (motivo, comentario, usuario, attachment_id),
        )
        log_change(conn, "archivos_adjuntos", attachment_id, "ANULAR", "estado_archivo", before_dict.get("estado_archivo"), "anulado", motivo, comentario, usuario)
        conn.commit()


def normalize_existing_evidence_paths(usuario: str | None = "admin") -> int:
    """Normalize legacy absolute evidence paths stored in ruta_entrega_evidencias."""
    count = 0
    with get_connection() as conn:
        rows = conn.execute("SELECT id, ruta_archivo FROM ruta_entrega_evidencias").fetchall()
        for row in rows:
            old = row["ruta_archivo"]
            new = to_relative_path(old)
            if new and new != old:
                conn.execute("UPDATE ruta_entrega_evidencias SET ruta_archivo = ? WHERE id = ?", (new, row["id"]))
                log_change(conn, "ruta_entrega_evidencias", row["id"], "NORMALIZE_PATH", "ruta_archivo", old, new, "Migración de ruta relativa", "Normalización automática v1.5", usuario)
                count += 1
        conn.commit()
    return count
