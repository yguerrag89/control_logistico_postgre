from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.gps_analytics import get_controlled_places
from modules.logistics_repository import list_destinations, merge_destinations, upsert_destination
from modules.operations import deliveries_without_destination, link_delivery_destination
from modules.navigation import run_legacy_page
from modules.session import sidebar_user_context, require_admin, list_app_users, sync_default_users

ctx = require_admin()
ctx = sidebar_user_context()
usuario = ctx["usuario"]

st.title("🗂️ Catálogos")
st.caption("Unidades, conductores, destinos/lugares controlados y vinculación de entregas a catálogo.")

section = st.radio(
    "Sección",
    ["Unidades", "Conductores", "Usuarios", "Destinos / lugares", "Vincular entregas", "Lugares controlados"],
    horizontal=True,
)

st.divider()

if section == "Unidades":
    run_legacy_page("04_Unidades.py")
elif section == "Conductores":
    run_legacy_page("05_Conductores.py")

elif section == "Usuarios":
    st.subheader("Usuarios de acceso")
    st.caption("En esta etapa de desarrollo, los usuarios se sincronizan automáticamente desde los conductores activos: admin y choferes.")

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🔄 Sincronizar usuarios", use_container_width=True):
            try:
                sync_default_users(force=True)
                st.success("Usuarios sincronizados correctamente.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudieron sincronizar usuarios: {exc}")

    try:
        users = list_app_users()
    except Exception as exc:
        users = []
        st.error(f"No se pudieron cargar usuarios: {exc}")

    if not users:
        st.warning("No hay usuarios visibles. Presiona 'Sincronizar usuarios'. Si persiste, revisa que existan conductores activos y que la tabla app_users exista en PostgreSQL.")
        st.code("SELECT COUNT(*) FROM conductores;\nSELECT COUNT(*) FROM app_users;", language="sql")
    else:
        df_users = pd.DataFrame(users)
        show_cols = [c for c in [
            "id", "username", "rol", "conductor_id", "conductor_nombre",
            "activo", "creado_en", "actualizado_en", "ultimo_login"
        ] if c in df_users.columns]
        st.dataframe(df_users[show_cols], use_container_width=True, hide_index=True)

        st.info(
            "Credenciales de desarrollo: admin / admin.2026. "
            "Para choferes: usuario = nombre del chofer, contraseña = nombre del chofer + .2026. "
            "Ejemplo: José Luis / José Luis.2026"
        )

        with st.expander("Verificación rápida en Neon", expanded=False):
            st.code("""
SELECT id, username, rol, conductor_id, activo
FROM app_users
ORDER BY rol, username;
""".strip(), language="sql")

elif section == "Destinos / lugares":
    st.subheader("Catálogo avanzado de destinos")
    st.caption("El objetivo es dejar de depender de texto libre. Valida clientes, bases, gasolineras, talleres y paqueterías para limpiar GPS e inactividad.")
    destinos = list_destinations(active_only=False)
    if not destinos.empty:
        show_cols = [c for c in ["id", "nombre_normalizado", "tipo_destino", "cliente_comercial", "cliente_asociado", "direccion_texto", "validado", "excluir_alertas_inactividad", "tiempo_promedio_servicio_min", "activo"] if c in destinos.columns]
        st.dataframe(destinos[show_cols], use_container_width=True, hide_index=True)
    else:
        st.info("Aún no hay destinos. Puedes crear uno aquí o desde GPS > Paradas frecuentes.")

    st.markdown("### Crear / editar destino")
    options = {0: "Crear nuevo"}
    if not destinos.empty:
        options |= {int(r["id"]): f"#{int(r['id'])} - {r['nombre_normalizado']}" for _, r in destinos.iterrows()}
    selected = st.selectbox("Destino", options=list(options.keys()), format_func=lambda x: options[x])
    rec = {}
    if selected and not destinos.empty:
        rec = destinos.loc[destinos["id"] == selected].iloc[0].to_dict()

    with st.form("destination_advanced_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            nombre = st.text_input("Nombre normalizado", value=str(rec.get("nombre_normalizado") or ""))
        with c2:
            tipo = st.selectbox("Tipo", ["Cliente", "Base", "Gasolinera", "Taller", "Paquetería", "CEDIS", "Almacén", "Autorizado", "Ignorar", "Otro"], index=["Cliente", "Base", "Gasolinera", "Taller", "Paquetería", "CEDIS", "Almacén", "Autorizado", "Ignorar", "Otro"].index(rec.get("tipo_destino")) if rec.get("tipo_destino") in ["Cliente", "Base", "Gasolinera", "Taller", "Paquetería", "CEDIS", "Almacén", "Autorizado", "Ignorar", "Otro"] else 0)
        with c3:
            validado = st.checkbox("Validado", value=bool(rec.get("validado", 0)))
        cliente_comercial = st.text_input("Cliente comercial", value=str(rec.get("cliente_comercial") or rec.get("cliente_asociado") or ""))
        alias = st.text_area("Alias / textos equivalentes", value=str(rec.get("alias") or ""))
        direccion = st.text_area("Dirección / texto GPS", value=str(rec.get("direccion_texto") or ""))
        c4, c5, c6 = st.columns(3)
        with c4:
            excluir = st.checkbox("Excluir de alertas de inactividad", value=bool(rec.get("excluir_alertas_inactividad", 0)))
        with c5:
            radio = st.number_input("Radio futuro de geocerca (m)", min_value=25, max_value=1000, value=int(rec.get("radio_metros") or 100), step=25)
        with c6:
            tiempo_serv = st.number_input("Tiempo servicio promedio (min)", min_value=0.0, value=float(rec.get("tiempo_promedio_servicio_min") or 0), step=5.0)
        c7, c8 = st.columns(2)
        with c7:
            requiere_cita = st.checkbox("Requiere cita", value=bool(rec.get("requiere_cita", 0)))
        with c8:
            horario = st.text_input("Horario recepción", value=str(rec.get("horario_recepcion") or ""), placeholder="Ej. Lun-Vie 8:00-15:00")
        contacto = st.text_input("Contacto", value=str(rec.get("contacto") or ""))
        observaciones = st.text_area("Observaciones", value=str(rec.get("observaciones") or ""))
        activo = st.checkbox("Activo", value=bool(rec.get("activo", 1)))
        comentario = st.text_input("Comentario de cambio", value="")
        save = st.form_submit_button("Guardar destino")
    if save:
        if not nombre.strip():
            st.error("El nombre es obligatorio.")
        else:
            payload = {
                "id": None if selected == 0 else int(selected),
                "nombre_normalizado": nombre.strip(),
                "alias": alias.strip(),
                "tipo_destino": tipo,
                "cliente_asociado": cliente_comercial.strip() or None,
                "cliente_comercial": cliente_comercial.strip() or None,
                "direccion_texto": direccion.strip() or None,
                "latitud": rec.get("latitud"),
                "longitud": rec.get("longitud"),
                "validado": 1 if validado else 0,
                "fuente": rec.get("fuente") or "captura_manual",
                "observaciones": observaciones.strip() or None,
                "excluir_alertas_inactividad": 1 if excluir else 0,
                "radio_metros": float(radio),
                "contacto": contacto.strip() or None,
                "horario_recepcion": horario.strip() or None,
                "requiere_cita": 1 if requiere_cita else 0,
                "tiempo_promedio_servicio_min": float(tiempo_serv) if tiempo_serv else None,
                "activo": 1 if activo else 0,
            }
            did = upsert_destination(payload, motivo="Edición catálogo avanzado", comentario=comentario or "Cambio desde Catálogos", usuario=usuario)
            st.success(f"Destino #{did} guardado.")

    if not destinos.empty and len(destinos) >= 2:
        st.divider()
        st.markdown("### Fusionar destinos duplicados")
        ids = destinos["id"].astype(int).tolist()
        source = st.selectbox("Destino duplicado a desactivar", options=ids, format_func=lambda x: f"#{x} - {destinos.loc[destinos['id']==x, 'nombre_normalizado'].iloc[0]}", key="merge_source_dest")
        target = st.selectbox("Destino correcto", options=[i for i in ids if i != source], format_func=lambda x: f"#{x} - {destinos.loc[destinos['id']==x, 'nombre_normalizado'].iloc[0]}", key="merge_target_dest")
        comentario_merge = st.text_input("Motivo/comentario de fusión", value="Destino duplicado")
        if st.button("Fusionar destinos"):
            merge_destinations(int(source), int(target), motivo="Fusión de destinos", comentario=comentario_merge, usuario=usuario)
            st.success("Destino fusionado; entregas vinculadas por destino_id fueron reasignadas.")

elif section == "Vincular entregas":
    st.subheader("Vincular entregas capturadas a destinos validados")
    st.caption("El chofer puede capturar texto libre; administración vincula ese texto al catálogo para que el análisis sea consistente.")
    pending = deliveries_without_destination()
    destinos = list_destinations(active_only=True)
    if pending.empty:
        st.success("No hay entregas pendientes de vincular a destino.")
    elif destinos.empty:
        st.warning("Hay entregas sin destino_id, pero todavía no hay destinos activos en catálogo.")
        st.dataframe(pending, use_container_width=True, hide_index=True)
    else:
        st.dataframe(pending, use_container_width=True, hide_index=True)
        pending["label"] = pending.apply(lambda r: f"Entrega #{r['entrega_id']} | {r['fecha']} | {r['placas']} | {r['cliente_nombre']} / {r['destino_nombre']}", axis=1)
        delivery_id = st.selectbox("Entrega", options=pending["entrega_id"].tolist(), format_func=lambda x: pending.loc[pending["entrega_id"] == x, "label"].iloc[0])
        dest_options = {int(r["id"]): f"#{int(r['id'])} - {r['nombre_normalizado']} ({r.get('tipo_destino') or '-'})" for _, r in destinos.iterrows()}
        destination_id = st.selectbox("Destino validado", options=list(dest_options.keys()), format_func=lambda x: dest_options[x])
        comentario = st.text_input("Comentario", value="Vinculación manual a catálogo")
        if st.button("Vincular entrega a destino"):
            link_delivery_destination(int(delivery_id), int(destination_id), motivo="Vinculación a catálogo", comentario=comentario, usuario=usuario)
            st.success("Entrega vinculada al destino.")

elif section == "Lugares controlados":
    st.subheader("Lugares controlados / autorizados")
    controlled = get_controlled_places(active_only=True)
    if controlled.empty:
        st.info("No hay lugares controlados activos.")
    else:
        st.dataframe(controlled, use_container_width=True, hide_index=True)
    st.caption("Para editar o crear lugares usa la sección Destinos / lugares.")
