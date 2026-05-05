from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from modules.session import sidebar_user_context, require_admin

from modules.business_analytics import default_date_range
from modules.gps_analytics import (
    get_abnormal_inactivity,
    get_daily_km_by_unit,
    get_daily_km_by_unit_complete,
    get_daily_km_total,
    get_daily_km_total_complete,
    get_frequent_stop_locations,
    get_inactivity_summary_by_unit,
    get_unit_activity_summary,
)
from modules.gps_repository import classify_gps_stop, list_gps_movements
from modules.navigation import run_legacy_page
from modules.repository import list_units
from modules.logistics_repository import upsert_destination

ctx = require_admin()
ctx = sidebar_user_context()
usuario = ctx["usuario"]

st.title("🛰️ GPS y actividad")
st.caption("Importación GPS, kilómetros diarios, actividad por unidad, inactividad anormal y paradas frecuentes.")

section = st.radio(
    "Sección",
    ["Importar GPS", "Km recorridos", "Inactividad anormal", "Paradas frecuentes", "Movimientos crudos"],
    horizontal=True,
)

if section == "Importar GPS":
    st.divider()
    run_legacy_page("09_Importar_GPS.py")
    st.stop()

start_default, end_default = default_date_range(days_back=90)
units = list_units(active_only=False)
unit_options = {0: "Todas"} | {u["id"]: u["placas"] for u in units}

c1, c2, c3 = st.columns(3)
with c1:
    unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
with c2:
    fecha_desde = st.date_input("Desde", value=start_default)
with c3:
    fecha_hasta = st.date_input("Hasta", value=end_default)

filters = {
    "unidad_id": None if unidad_id == 0 else unidad_id,
    "fecha_desde": str(fecha_desde),
    "fecha_hasta": str(fecha_hasta),
}

st.divider()

if section == "Km recorridos":
    daily_unit = get_daily_km_by_unit(filters)
    daily_unit_complete = get_daily_km_by_unit_complete(filters)
    daily_total_complete = get_daily_km_total_complete(filters)
    summary = get_unit_activity_summary(filters)

    st.subheader("Km diarios por unidad")
    st.caption("Incluye todos los días del rango seleccionado. Los días sin movimiento aparecen con 0 km.")
    if daily_unit_complete.empty:
        st.info("No hay movimientos GPS en el rango.")
    else:
        dias_periodo = int(daily_total_complete["fecha"].nunique()) if not daily_total_complete.empty else 0
        dias_con_mov = int((daily_total_complete["km_total"] > 0).sum()) if not daily_total_complete.empty else 0
        dias_sin_mov = max(dias_periodo - dias_con_mov, 0)
        k1, k2, k3 = st.columns(3)
        k1.metric("Días del rango", dias_periodo)
        k2.metric("Días con movimiento", dias_con_mov)
        k3.metric("Días sin movimiento", dias_sin_mov)

        chart_df = daily_unit_complete.copy()
        chart_df["km_gps"] = pd.to_numeric(chart_df["km_gps"], errors="coerce").fillna(0)
        date_order = chart_df.drop_duplicates("fecha").sort_values("fecha")["fecha_label"].tolist()
        chart = alt.Chart(chart_df).mark_line(point=True).encode(
            x=alt.X("fecha_label:N", sort=date_order, title="Día", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("km_gps:Q", title="Kilómetros recorridos (km)"),
            color=alt.Color("placas:N", title="Unidad"),
            tooltip=[
                alt.Tooltip("fecha:N", title="Fecha"),
                alt.Tooltip("placas:N", title="Unidad"),
                alt.Tooltip("km_gps:Q", title="Km", format=",.2f"),
                alt.Tooltip("movimientos:Q", title="Movimientos"),
                alt.Tooltip("horas_movimiento:Q", title="Horas movimiento", format=",.2f"),
            ],
        ).properties(height=360)
        st.altair_chart(chart, use_container_width=True)

        with st.expander("Ver tabla diaria completa por unidad"):
            st.dataframe(
                daily_unit_complete[["fecha", "placas", "km_gps", "movimientos", "horas_movimiento", "es_dia_sin_movimiento"]],
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Km totales de flota por día")
        bar = alt.Chart(daily_total_complete).mark_bar().encode(
            x=alt.X("fecha_label:N", sort=daily_total_complete["fecha_label"].tolist(), title="Día", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("km_total:Q", title="Kilómetros recorridos (km)"),
            tooltip=[
                alt.Tooltip("fecha:N", title="Fecha"),
                alt.Tooltip("km_total:Q", title="Km total", format=",.2f"),
                alt.Tooltip("unidades_activas:Q", title="Unidades activas"),
                alt.Tooltip("movimientos_totales:Q", title="Movimientos"),
            ],
        ).properties(height=260)
        st.altair_chart(bar, use_container_width=True)

    st.subheader("Ranking de actividad por unidad")
    if summary.empty:
        st.info("No hay resumen por unidad.")
    else:
        st.dataframe(summary, use_container_width=True, hide_index=True)

elif section == "Inactividad anormal":
    st.subheader("Paradas largas fuera de lugares autorizados")
    c4, c5, c6 = st.columns(3)
    with c4:
        min_minutes = st.number_input("Duración mínima en minutos", min_value=5, max_value=360, value=30, step=5)
    with c5:
        max_hours = st.number_input("Duración máxima en horas", min_value=1, max_value=48, value=12, step=1)
    with c6:
        exclude_authorized = st.checkbox("Excluir base/clientes/gasolineras/talleres autorizados", value=True)

    abnormal = get_abnormal_inactivity(
        filters,
        min_minutes=float(min_minutes),
        max_hours=float(max_hours),
        exclude_authorized=exclude_authorized,
        unmatched_only=True,
    )
    summary = get_inactivity_summary_by_unit(filters, min_minutes=float(min_minutes))

    if summary.empty:
        st.info("No hay inactividad anormal agrupada por unidad con estos filtros.")
    else:
        st.markdown("**Resumen por unidad**")
        st.dataframe(summary, use_container_width=True, hide_index=True)

    if abnormal.empty:
        st.success("No hay paradas anormales pendientes con esos filtros.")
    else:
        cols = ["id", "fecha", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min", "nivel_alerta", "direccion_gps", "clasificacion_lugar", "lugar_controlado"]
        st.dataframe(abnormal[cols], use_container_width=True, hide_index=True)
        st.caption("Clasifica las paradas revisadas para que no sigan apareciendo como pendientes.")
        selected = st.selectbox("Parada a clasificar", options=abnormal["id"].tolist(), format_func=lambda x: f"Parada #{x}")
        c7, c8 = st.columns(2)
        with c7:
            clasificacion = st.selectbox(
                "Clasificación",
                ["comida", "espera", "taller", "gasolinera", "cliente_no_capturado", "base", "personal", "tráfico", "otro", "ignorar", "revisar"],
            )
        with c8:
            comentario = st.text_input("Comentario", value="")
        if st.button("Guardar clasificación"):
            classify_gps_stop(int(selected), clasificacion, comentario=comentario, usuario=usuario)
            st.success("Parada clasificada. Actualiza la página para ver el cambio.")

elif section == "Paradas frecuentes":
    st.subheader("Lugares frecuentes detectados por GPS")
    c4, c5 = st.columns(2)
    with c4:
        min_minutes = st.number_input("Mínimo minutos por parada", min_value=1, max_value=180, value=15, step=5)
    with c5:
        include_authorized = st.checkbox("Incluir lugares ya autorizados", value=False)
    freq = get_frequent_stop_locations(filters, min_minutes=float(min_minutes), include_authorized=include_authorized)
    if freq.empty:
        st.info("No hay paradas frecuentes con estos filtros.")
    else:
        st.dataframe(freq, use_container_width=True, hide_index=True)
        st.caption("Convierte direcciones frecuentes en clientes, bases, gasolineras o lugares autorizados para limpiar el análisis de inactividad.")
        st.markdown("### Crear lugar controlado desde GPS")
        direcciones = freq["direccion_gps"].dropna().astype(str).tolist()
        selected_dir = st.selectbox("Dirección GPS candidata", options=direcciones)
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            nombre_lugar = st.text_input("Nombre del lugar", value=selected_dir[:80])
        with cc2:
            tipo_lugar = st.selectbox("Tipo", ["Cliente", "Base", "Gasolinera", "Taller", "Paquetería", "CEDIS", "Almacén", "Autorizado", "Ignorar", "Otro"], key="tipo_lugar_gps")
        with cc3:
            excluir = st.checkbox("Excluir de alertas", value=tipo_lugar in {"Base", "Gasolinera", "Taller", "Cliente", "Paquetería", "CEDIS", "Almacén", "Autorizado", "Ignorar"})
        comentario = st.text_input("Comentario", value="Creado desde paradas frecuentes GPS")
        if st.button("Crear lugar controlado"):
            payload = {
                "nombre_normalizado": nombre_lugar.strip() or selected_dir[:80],
                "alias": selected_dir,
                "tipo_destino": tipo_lugar,
                "cliente_asociado": None,
                "direccion_texto": selected_dir,
                "validado": 1 if tipo_lugar != "Otro" else 0,
                "fuente": "gps_paradas_frecuentes",
                "observaciones": comentario,
                "excluir_alertas_inactividad": 1 if excluir else 0,
                "radio_metros": 100,
                "activo": 1,
            }
            upsert_destination(payload, motivo="Alta desde GPS", comentario=comentario, usuario=usuario)
            st.success("Lugar controlado creado. Actualiza la página para que deje de aparecer como pendiente si aplica.")

else:
    st.subheader("Movimientos GPS crudos")
    st.warning("Esta vista puede ser pesada cuando haya muchos meses de GPS. Cárgala solo cuando necesites auditar detalle.")
    max_rows = st.number_input("Máximo de filas a mostrar", min_value=100, max_value=10000, value=1000, step=100)
    if st.button("Cargar movimientos crudos", type="primary"):
        movs = list_gps_movements(filters)
        if movs.empty:
            st.info("No hay movimientos GPS en el rango.")
        else:
            cols = [c for c in ["fecha", "placas_catalogo", "inicio_datetime", "fin_datetime", "km", "duracion_reportada_seg", "velocidad_promedio_kmh", "origen", "destino", "flags_calidad"] if c in movs.columns]
            st.caption(f"Mostrando hasta {int(max_rows):,} filas. Usa filtros de fecha/unidad para auditar más rápido.")
            st.dataframe(movs[cols].head(int(max_rows)), use_container_width=True, hide_index=True)
