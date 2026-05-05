from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.repository import get_checklist_by_unit, get_unit, list_units, upsert_unit

st.title("🚚 Unidades")

units = list_units(active_only=False)
if units:
    st.dataframe(pd.DataFrame(units), use_container_width=True, hide_index=True)

with st.expander("Crear o editar unidad", expanded=False):
    options = {0: "Nueva unidad"} | {u["id"]: u["placas"] for u in units}
    selected_id = st.selectbox("Registro", options=list(options.keys()), format_func=lambda x: options[x])
    record = next((u for u in units if u["id"] == selected_id), None)

    with st.form("unit_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            placas = st.text_input("Placas", value=record["placas"] if record else "")
        with c2:
            marca = st.text_input("Marca", value=record["marca"] if record else "")
        with c3:
            modelo = st.text_input("Modelo", value=record["modelo"] if record else "")

        c4, c5, c6 = st.columns(3)
        with c4:
            color = st.text_input("Color", value=record["color"] if record else "")
        with c5:
            tipo_unidad = st.text_input("Tipo unidad", value=record["tipo_unidad"] if record else "")
        with c6:
            combustible_preferido = st.selectbox("Combustible preferido", options=["Magna", "Premium", "Diésel", "Aceite", "Otro"], index=["Magna", "Premium", "Diésel", "Aceite", "Otro"].index(record["combustible_preferido"]) if record and record.get("combustible_preferido") in ["Magna", "Premium", "Diésel", "Aceite", "Otro"] else 0)

        c7, c8, c9, c10 = st.columns(4)
        with c7:
            tipo_carga = st.text_input("Tipo de carga", value=record["tipo_carga"] if record else "")
        with c8:
            carga_garrafones = st.selectbox("Carga en garrafones", options=["Si", "No"], index=["Si", "No"].index(record["carga_garrafones"]) if record and record.get("carga_garrafones") in ["Si", "No"] else 1)
        with c9:
            periodo_habil = st.text_input("Periodo hábil", value=record["periodo_habil"] if record else "")
        with c10:
            limite_litros = st.number_input("Límite litros", min_value=0.0, step=1.0, value=float(record["limite_litros"] or 0.0) if record else 0.0)

        activo = st.checkbox("Activo", value=bool(record["activo"]) if record else True)
        motivo_correccion = st.selectbox("Motivo", options=["Alta manual", "Error de captura", "Dato faltante", "Corrección administrativa", "Otro"], key="motivo_unidad")
        comentario_correccion = st.text_area("Comentario", value="", key="comentario_unidad")
        save = st.form_submit_button("Guardar unidad")

    if save:
        if record and not comentario_correccion.strip():
            st.error("Para editar una unidad debes agregar un comentario de corrección.")
            st.stop()
        upsert_unit({
            "id": record["id"] if record else None,
            "placas": placas.strip(),
            "marca": marca.strip(),
            "modelo": modelo.strip(),
            "color": color.strip(),
            "tipo_unidad": tipo_unidad.strip(),
            "combustible_preferido": combustible_preferido,
            "tipo_carga": tipo_carga.strip(),
            "carga_garrafones": carga_garrafones,
            "periodo_habil": periodo_habil.strip(),
            "limite_litros": float(limite_litros),
            "activo": 1 if activo else 0,
        }, motivo=motivo_correccion, comentario=comentario_correccion.strip())
        st.success("Unidad guardada.")

st.divider()
st.subheader("Checklist documental / equipo")

selected_unit_id = st.selectbox("Unidad para ver checklist", options=[u["id"] for u in units], format_func=lambda x: next(u["placas"] for u in units if u["id"] == x))
items = get_checklist_by_unit(selected_unit_id)
if items:
    st.dataframe(pd.DataFrame(items)[["item", "valor"]], use_container_width=True, hide_index=True)
else:
    st.info("Esta unidad todavía no tiene checklist sembrado.")
