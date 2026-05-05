from __future__ import annotations

import streamlit as st

from modules.business_analytics import base_like_stops, default_date_range, stops_operational_view
from modules.gps_repository import classify_gps_stop
from modules.repository import list_units

st.title("⏱️ Paradas GPS por revisar")
st.caption("Filtra ruido operativo para enfocarte en paradas relevantes no asociadas a entregas, combustible o clasificaciones.")

start_default, end_default = default_date_range(days_back=60)
units = list_units(active_only=False)
unit_options = {0: "Todas"} | {u["id"]: u["placas"] for u in units}

c1, c2, c3, c4 = st.columns(4)
with c1:
    unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
with c2:
    fecha_desde = st.date_input("Desde", value=start_default)
with c3:
    fecha_hasta = st.date_input("Hasta", value=end_default)
with c4:
    min_minutes = st.number_input("Mín. minutos", min_value=1, max_value=240, value=30, step=5)

c5, c6, c7 = st.columns(3)
with c5:
    max_hours = st.number_input("Máx. horas para revisar", min_value=1, max_value=24, value=8, step=1)
with c6:
    exclude_base = st.checkbox("Excluir base/nocturnas probables", value=True)
with c7:
    unmatched_only = st.checkbox("Solo no asociadas/no clasificadas", value=True)

filters = {
    "unidad_id": None if unidad_id == 0 else unidad_id,
    "fecha_desde": str(fecha_desde),
    "fecha_hasta": str(fecha_hasta),
}

tab_review, tab_base = st.tabs(["Paradas operativas", "Base / nocturnas probables"])

with tab_review:
    stops = stops_operational_view(
        filters,
        unmatched_only=unmatched_only,
        min_minutes=float(min_minutes),
        max_hours=float(max_hours),
        exclude_probable_base=exclude_base,
    )
    if stops.empty:
        st.success("No hay paradas pendientes con esos filtros.")
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric("Paradas", len(stops))
        k2.metric("Horas detenidas", f"{stops['duracion_min'].sum()/60:,.2f}")
        k3.metric("Promedio min", f"{stops['duracion_min'].mean():,.1f}")
        show_cols = [
            "id", "fecha", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min",
            "direccion_gps", "categoria_sugerida", "clasificacion_inicial",
        ]
        st.dataframe(stops[[c for c in show_cols if c in stops.columns]], use_container_width=True, hide_index=True)

        st.subheader("Clasificar parada")
        selected_id = st.selectbox("Parada", options=stops["id"].tolist(), format_func=lambda x: f"#{x}")
        record = stops[stops["id"] == selected_id].iloc[0].to_dict()
        st.json({
            "fecha": record.get("fecha"),
            "unidad": record.get("placas_catalogo"),
            "inicio": record.get("inicio_gps"),
            "fin": record.get("fin_gps"),
            "duracion_min": round(float(record.get("duracion_min") or 0), 2),
            "direccion": record.get("direccion_gps"),
        })
        with st.form(f"classify_{selected_id}"):
            clasificacion = st.selectbox(
                "Clasificación",
                options=["comida", "espera", "taller", "gasolinera", "cliente_no_capturado", "personal", "tráfico", "base", "otro", "ignorar"],
            )
            comentario = st.text_area("Comentario / explicación", value="")
            save = st.form_submit_button("Guardar clasificación")
        if save:
            classify_gps_stop(int(selected_id), clasificacion, comentario.strip())
            st.success("Parada clasificada. Actualiza la página para ver el cambio.")

with tab_base:
    base_df = base_like_stops(filters)
    if base_df.empty:
        st.info("No se detectaron paradas nocturnas/base probables con estos filtros.")
    else:
        st.caption("Estas paradas se excluyen por defecto para evitar ruido. Revísalas solo si sospechas uso fuera de horario o estacionamiento inusual.")
        st.dataframe(base_df, use_container_width=True, hide_index=True)
