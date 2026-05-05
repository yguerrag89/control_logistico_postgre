from __future__ import annotations

import streamlit as st

from modules.session import sidebar_user_context, require_admin

from modules.navigation import run_legacy_page
from modules.operational_quality import repair_all_route_statuses, refresh_fuel_quality, route_state_inconsistencies, route_time_inconsistencies
from modules.traceability import normalize_existing_evidence_paths
from modules.db import DB_PATH
from modules.logistics_repository import list_routes, mark_route_type, annul_route_for_testing, delete_route_permanently

ctx = require_admin()
ctx = sidebar_user_context()
usuario = ctx["usuario"]

st.title("🧾 Auditoría y correcciones")
st.caption("Centro de trazabilidad: auditoría, reparación de estados, calidad de combustible y normalización de evidencias.")

section = st.radio(
    "Sección",
    ["Pendientes y reparación", "Auditoría detallada"],
    horizontal=True,
)

if section == "Auditoría detallada":
    run_legacy_page("14_Auditoria_y_Correcciones.py")
    st.stop()

st.subheader("Reparaciones controladas")
st.write("Estas acciones no borran datos; actualizan estados calculados o normalizan rutas con auditoría.")

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("Recalcular estados de rutas", use_container_width=True):
        n = repair_all_route_statuses(usuario=usuario)
        st.success(f"Estados recalculados para {n} rutas.")
with c2:
    if st.button("Recalcular calidad de combustible", use_container_width=True):
        n = refresh_fuel_quality(usuario=usuario)
        st.success(f"Calidad actualizada en {n} cargas.")
with c3:
    if st.button("Normalizar rutas de evidencias", use_container_width=True):
        n = normalize_existing_evidence_paths(usuario=usuario)
        st.success(f"Rutas de evidencias normalizadas: {n}.")




st.divider()
st.subheader("Limpieza de rutas de prueba / capacitación")
st.caption(
    "Usa anulación lógica para limpiar pruebas sin perder trazabilidad. "
    "La eliminación definitiva es solo para desarrollo y borra también entregas, evidencias, matches y gastos de la ruta."
)

routes_all = list_routes({"active_only": False, "fecha_desde": "2026-01-01"})
if routes_all.empty:
    st.info("No hay rutas registradas.")
else:
    show_cols = [c for c in [
        "id", "fecha", "placas", "conductor_nombre", "hora_salida_reportada",
        "hora_regreso_reportada", "estado_ruta", "tipo_ruta", "activo",
        "entregas_capturadas", "motivo_anulacion", "anulado_en"
    ] if c in routes_all.columns]
    st.dataframe(routes_all[show_cols], use_container_width=True, hide_index=True)

    routes_all["label"] = routes_all.apply(
        lambda r: f"#{int(r['id'])} | {r['fecha']} | {r['placas']} | {r['conductor_nombre']} | {r.get('tipo_ruta') or 'OPERATIVA'} | {r['estado_ruta']} | activo={r['activo']}",
        axis=1,
    )
    route_id_cleanup = st.selectbox(
        "Ruta para limpiar",
        options=routes_all["id"].astype(int).tolist(),
        format_func=lambda x: routes_all.loc[routes_all["id"].astype(int) == int(x), "label"].iloc[0],
        key="cleanup_route_id",
    )

    cA, cB = st.columns(2)
    with cA:
        st.markdown("#### Marcar tipo / anular")
        tipo_nuevo = st.selectbox("Tipo de ruta", ["OPERATIVA", "PRUEBA", "CAPACITACION"], key="cleanup_tipo_ruta")
        motivo_tipo = st.text_input("Motivo para cambio de tipo", value="Corrección de ruta de prueba/capacitación", key="cleanup_motivo_tipo")
        if st.button("Guardar tipo de ruta", use_container_width=True):
            if not motivo_tipo.strip():
                st.error("El motivo es obligatorio.")
            else:
                mark_route_type(int(route_id_cleanup), tipo_nuevo, motivo=motivo_tipo.strip(), comentario="Cambio desde limpieza de rutas", usuario=usuario)
                st.success("Tipo de ruta actualizado con auditoría.")
                st.rerun()

        motivo_anular = st.text_input("Motivo de anulación lógica", value="Ruta creada para prueba/capacitación", key="cleanup_motivo_anular")
        comentario_anular = st.text_area("Comentario de anulación", value="", key="cleanup_comentario_anular")
        if st.button("Anular ruta de prueba/capacitación", use_container_width=True, type="primary"):
            if not motivo_anular.strip():
                st.error("El motivo de anulación es obligatorio.")
            else:
                annul_route_for_testing(int(route_id_cleanup), motivo=motivo_anular.strip(), comentario=comentario_anular.strip(), usuario=usuario)
                st.success("Ruta anulada lógicamente. No aparecerá en vistas operativas normales.")
                st.rerun()

    with cB:
        st.markdown("#### Eliminación definitiva")
        st.warning("Solo para desarrollo. Esta acción borra la ruta y sus registros hijos. No la uses para operación real.")
        motivo_delete = st.text_input("Motivo de eliminación definitiva", value="Ruta de prueba creada por error", key="cleanup_motivo_delete")
        confirm_text = st.text_input("Para confirmar escribe ELIMINAR", key="cleanup_confirm_delete")
        if st.button("Eliminar definitivamente", use_container_width=True):
            if confirm_text.strip().upper() != "ELIMINAR":
                st.error("Debes escribir ELIMINAR para confirmar.")
            elif not motivo_delete.strip():
                st.error("El motivo de eliminación es obligatorio.")
            else:
                try:
                    counts = delete_route_permanently(int(route_id_cleanup), motivo=motivo_delete.strip(), comentario="Eliminación definitiva desde modo desarrollo", usuario=usuario)
                    st.success(f"Ruta eliminada definitivamente. Registros eliminados: {counts}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"No se pudo eliminar la ruta: {exc}")

st.divider()
st.subheader("Respaldo de base de datos")
st.caption("En Streamlit Cloud, SQLite sirve para desarrollo, pero los cambios del archivo local no se guardan de forma confiable después de reinicios/redeploys. Descarga respaldos frecuentes mientras migramos a una base externa.")
try:
    db_bytes = DB_PATH.read_bytes()
    st.download_button(
        "⬇️ Descargar respaldo SQLite actual",
        data=db_bytes,
        file_name="fuel_control_respaldo.db",
        mime="application/octet-stream",
        use_container_width=True,
        key="download_sqlite_backup_v17",
    )
except Exception as exc:
    st.warning(f"No se pudo preparar el respaldo de la base: {exc}")

st.divider()
st.subheader("Inconsistencias detectadas")

incons_hora = route_time_inconsistencies()
if incons_hora.empty:
    st.success("No hay entregas con hora fuera del intervalo de ruta.")
else:
    st.warning("Entregas con hora de llegada fuera del intervalo de ruta.")
    st.dataframe(incons_hora, use_container_width=True, hide_index=True)

incons_estado = route_state_inconsistencies()
if incons_estado.empty:
    st.success("No hay rutas con estado de conciliación incoherente.")
else:
    st.warning("Rutas con estado de conciliación incoherente.")
    st.dataframe(incons_estado, use_container_width=True, hide_index=True)
