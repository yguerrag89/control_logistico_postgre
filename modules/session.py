from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import streamlit as st

from modules.db import get_connection

ROLES = ["Administrador", "Chofer"]
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin.2026"
_DEV_SALT = "baro_logistica_dev_v1"


@dataclass
class UserContext:
    usuario: str
    rol: str
    conductor_id: int | None = None
    conductor_nombre: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "rol": self.rol,
            "conductor_id": self.conductor_id,
            "conductor_nombre": self.conductor_nombre,
        }


def _hash_password(username: str, password: str) -> str:
    raw = f"{username.strip()}|{password}|{_DEV_SALT}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _verify_password(username: str, password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(username, password), stored_hash or "")


def ensure_user_tables() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL CHECK (rol IN ('Administrador','Chofer')),
                conductor_id INTEGER,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TEXT,
                ultimo_login TEXT,
                FOREIGN KEY (conductor_id) REFERENCES conductores(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_users_rol ON app_users(rol, activo)")
        conn.commit()


def sync_default_users(force: bool = False) -> None:
    """Create/update development users.

    Development policy requested:
    - Administrator: admin / admin.2026
    - Each active driver: username = driver name, password = driver name + .2026

    This is intentionally simple for MVP development. It is not a production
    authentication scheme.
    """
    if st is not None and not force and st.session_state.get("_default_users_synced"):
        return

    ensure_user_tables()
    with get_connection() as conn:
        # Admin user.
        admin_hash = _hash_password(DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD)
        conn.execute(
            """
            INSERT INTO app_users (username, password_hash, rol, conductor_id, activo)
            VALUES (?, ?, 'Administrador', NULL, 1)
            ON CONFLICT(username) DO UPDATE SET
                password_hash=excluded.password_hash,
                rol='Administrador',
                conductor_id=NULL,
                activo=1,
                actualizado_en=CURRENT_TIMESTAMP
            """,
            (DEFAULT_ADMIN_USER, admin_hash),
        )

        drivers = conn.execute("SELECT id, nombre, activo FROM conductores").fetchall()
        active_driver_ids = {int(d["id"]) for d in drivers if int(d["activo"] or 0) == 1}
        for d in drivers:
            conductor_id = int(d["id"])
            nombre = str(d["nombre"] or "").strip()
            if not nombre:
                continue
            active = 1 if int(d["activo"] or 0) == 1 else 0
            pwd = f"{nombre}.2026"
            pwd_hash = _hash_password(nombre, pwd)

            # If this conductor already had a user under an old name, remove that
            # potential conflict by updating by conductor_id first when possible.
            existing_by_conductor = conn.execute(
                "SELECT id, username FROM app_users WHERE conductor_id = ? AND rol = 'Chofer'",
                (conductor_id,),
            ).fetchone()
            if existing_by_conductor:
                conn.execute(
                    """
                    UPDATE app_users
                    SET username=?, password_hash=?, rol='Chofer', activo=?, actualizado_en=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (nombre, pwd_hash, active, int(existing_by_conductor["id"])),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO app_users (username, password_hash, rol, conductor_id, activo)
                    VALUES (?, ?, 'Chofer', ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        password_hash=excluded.password_hash,
                        rol='Chofer',
                        conductor_id=excluded.conductor_id,
                        activo=excluded.activo,
                        actualizado_en=CURRENT_TIMESTAMP
                    """,
                    (nombre, pwd_hash, conductor_id, active),
                )

        # Disable driver users whose conductor no longer exists as active. Keep audit/history.
        if active_driver_ids:
            placeholders = ",".join("?" for _ in active_driver_ids)
            conn.execute(
                f"""
                UPDATE app_users
                SET activo=0, actualizado_en=CURRENT_TIMESTAMP
                WHERE rol='Chofer' AND conductor_id IS NOT NULL AND conductor_id NOT IN ({placeholders})
                """,
                tuple(active_driver_ids),
            )
        conn.commit()

    if st is not None:
        st.session_state["_default_users_synced"] = True


def authenticate(username: str, password: str) -> UserContext | None:
    sync_default_users()
    username = username.strip()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.*, c.nombre AS conductor_nombre
            FROM app_users u
            LEFT JOIN conductores c ON c.id = u.conductor_id
            WHERE u.username = ? AND u.activo = 1
            """,
            (username,),
        ).fetchone()
        if not row:
            return None
        if not _verify_password(username, password, row["password_hash"]):
            return None
        conn.execute("UPDATE app_users SET ultimo_login=CURRENT_TIMESTAMP WHERE id=?", (int(row["id"]),))
        conn.commit()
        return UserContext(
            usuario=str(row["username"]),
            rol=str(row["rol"]),
            conductor_id=int(row["conductor_id"]) if row["conductor_id"] is not None else None,
            conductor_nombre=row["conductor_nombre"],
        )


def login_screen() -> None:
    sync_default_users()
    st.title("🚚 Control Logístico de Unidades")
    st.caption("Acceso de desarrollo: Administrador o Chofer.")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Entrar", type="primary", use_container_width=True)
    if submit:
        ctx = authenticate(username, password)
        if ctx is None:
            st.error("Usuario o contraseña incorrectos, o usuario inactivo.")
        else:
            st.session_state["auth"] = ctx.as_dict()
            st.session_state["usuario_operador"] = ctx.usuario
            st.session_state["rol_operador"] = ctx.rol
            st.session_state["conductor_id"] = ctx.conductor_id
            st.session_state["conductor_nombre"] = ctx.conductor_nombre
            st.rerun()

    with st.expander("Usuarios de desarrollo", expanded=False):
        st.markdown(
            """
            - Administrador: `admin` / `admin.2026`
            - Choferes: usuario = nombre del chofer, contraseña = `nombre del chofer.2026`

            Ejemplo: si el chofer se llama `José Luis`, su contraseña es `José Luis.2026`.
            """
        )


def is_authenticated() -> bool:
    return isinstance(st.session_state.get("auth"), dict) and bool(st.session_state["auth"].get("usuario"))


def require_auth() -> dict[str, Any]:
    if not is_authenticated():
        login_screen()
        st.stop()
    return st.session_state["auth"]


def logout_button() -> None:
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        for key in ["auth", "usuario_operador", "rol_operador", "conductor_id", "conductor_nombre"]:
            st.session_state.pop(key, None)
        st.rerun()


def sidebar_user_context() -> dict[str, Any]:
    """Authenticated context used by all pages.

    This replaced the old free role selector. Chofer users no longer can switch
    roles or operate admin pages.
    """
    ctx = require_auth()
    with st.sidebar:
        st.markdown("### 👤 Sesión")
        st.write(f"**Usuario:** {ctx.get('usuario')}")
        st.write(f"**Rol:** {ctx.get('rol')}")
        if ctx.get("rol") == "Chofer" and ctx.get("conductor_nombre"):
            st.write(f"**Chofer:** {ctx.get('conductor_nombre')}")
        logout_button()
    return ctx


def current_user() -> str:
    ctx = st.session_state.get("auth") or {}
    return ctx.get("usuario") or st.session_state.get("usuario_operador", "admin")


def current_role() -> str:
    ctx = st.session_state.get("auth") or {}
    return ctx.get("rol") or st.session_state.get("rol_operador", "Administrador")


def current_conductor_id() -> int | None:
    ctx = st.session_state.get("auth") or {}
    cid = ctx.get("conductor_id")
    return int(cid) if cid is not None else None


def is_admin() -> bool:
    return current_role() == "Administrador"


def is_driver() -> bool:
    return current_role() == "Chofer"


def require_admin() -> dict[str, Any]:
    ctx = require_auth()
    if ctx.get("rol") != "Administrador":
        with st.sidebar:
            st.markdown("### 👤 Sesión")
            st.write(f"**Usuario:** {ctx.get('usuario')}")
            st.write(f"**Rol:** {ctx.get('rol')}")
            logout_button()
        st.title("🚫 Acceso restringido")
        st.warning("Esta sección es solo para Administrador. Tu usuario de chofer solo puede capturar rutas, entregas y combustible.")
        st.info("Entra a **Rutas y entregas** o **Combustible** para capturar datos operativos.")
        st.stop()
    return ctx


def list_app_users() -> list[dict[str, Any]]:
    sync_default_users()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.rol, u.conductor_id, c.nombre AS conductor_nombre,
                   u.activo, u.creado_en, u.actualizado_en, u.ultimo_login
            FROM app_users u
            LEFT JOIN conductores c ON c.id = u.conductor_id
            ORDER BY CASE u.rol WHEN 'Administrador' THEN 0 ELSE 1 END, u.username
            """
        ).fetchall()
        return [dict(r) for r in rows]
