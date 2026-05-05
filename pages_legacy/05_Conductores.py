from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.repository import list_conductors, merge_conductors, upsert_conductor

st.title("🧑‍✈️ Conductores")
st.caption("Permite corregir nombres, activar/desactivar y fusionar duplicados sin perder trazabilidad.")

conductors = list_conductors(active_only=False)
if conductors:
    st.dataframe(pd.DataFrame(conductors), use_container_width=True, hide_index=True)
else:
    st.info("No hay conductores registrados.")

MOTIVOS = ["Error de captura", "Dato faltante", "Corrección administrativa", "Duplicado", "Otro"]

with st.expander("Crear o editar conductor", expanded=False):
    options = {0: "Nuevo conductor"} | {c["id"]: c["nombre"] for c in conductors}
    selected_id = st.selectbox("Registro", options=list(options.keys()), format_func=lambda x: options[x])
    record = next((c for c in conductors if c["id"] == selected_id), None)

    with st.form("driver_form"):
        nombre = st.text_input("Nombre", value=record["nombre"] if record else "")
        activo = st.checkbox("Activo", value=bool(record["activo"]) if record else True)
        motivo = st.selectbox("Motivo de corrección/alta", options=MOTIVOS)
        comentario = st.text_area("Comentario", value="")
        save = st.form_submit_button("Guardar conductor")

    if save:
        if not nombre.strip():
            st.error("El nombre es obligatorio.")
        elif record and not comentario.strip():
            st.error("Para editar un conductor debes agregar un comentario de corrección.")
        else:
            try:
                upsert_conductor(
                    {"id": record["id"] if record else None, "nombre": nombre.strip(), "activo": 1 if activo else 0},
                    motivo=motivo,
                    comentario=comentario.strip(),
                )
                st.success("Conductor guardado.")
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")

with st.expander("Fusionar conductores duplicados", expanded=False):
    if len(conductors) < 2:
        st.info("Necesitas al menos dos conductores para fusionar.")
    else:
        conductor_options = {c["id"]: f"#{c['id']} | {c['nombre']}" for c in conductors}
        source_id = st.selectbox("Conductor duplicado que se desactivará", options=list(conductor_options.keys()), format_func=lambda x: conductor_options[x], key="merge_source")
        target_id = st.selectbox("Conductor correcto que conservará los registros", options=list(conductor_options.keys()), format_func=lambda x: conductor_options[x], key="merge_target")
        motivo_merge = st.selectbox("Motivo", options=["Duplicado", "Error de captura", "Otro"], key="merge_reason")
        comentario_merge = st.text_area("Comentario obligatorio", key="merge_comment")
        confirm = st.checkbox("Confirmo que quiero reasignar las cargas/rutas del conductor duplicado al conductor correcto.")
        if st.button("Fusionar conductores", type="primary"):
            if source_id == target_id:
                st.error("Selecciona dos conductores diferentes.")
            elif not comentario_merge.strip():
                st.error("El comentario es obligatorio para fusionar.")
            elif not confirm:
                st.error("Debes confirmar la fusión.")
            else:
                try:
                    merge_conductors(int(source_id), int(target_id), motivo_merge, comentario_merge.strip())
                    st.success("Conductores fusionados. El duplicado quedó inactivo y sus registros fueron reasignados.")
                except Exception as exc:
                    st.error(f"No se pudo fusionar: {exc}")
