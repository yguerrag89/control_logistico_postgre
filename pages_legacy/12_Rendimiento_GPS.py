from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.business_analytics import default_date_range, fuel_gps_cycles, monthly_fuel_gps_summary
from modules.repository import list_units

st.title("⛽🛰️ Rendimiento GPS")
st.caption(
    "Analiza combustible contra kilómetros GPS. La vista mensual es contable; "
    "la vista por ciclo ayuda a detectar cargas parciales, rendimientos extremos y costo por km."
)

start_default, end_default = default_date_range(days_back=120)
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

monthly = monthly_fuel_gps_summary(filters)
cycles = fuel_gps_cycles(filters)

tab_month, tab_cycles, tab_notes = st.tabs(["Vista mensual", "Ciclos de carga", "Lectura operativa"])

with tab_month:
    st.subheader("Resumen mensual por unidad")
    if monthly.empty:
        st.info("No hay combustible ni GPS en el rango seleccionado.")
    else:
        total_km = float(monthly["km_gps"].sum())
        total_l = float(monthly["litros"].sum())
        total_g = float(monthly["gasto"].sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Km GPS", f"{total_km:,.2f}")
        c2.metric("Litros", f"{total_l:,.2f}")
        c3.metric("Gasto", f"${total_g:,.2f}")
        c4.metric("Rendimiento", "-" if not total_l else f"{total_km/total_l:,.2f} km/L")

        cols = [
            "mes", "placas", "km_gps", "horas_movimiento", "dias_con_movimiento", "movimientos",
            "litros", "gasto", "cargas", "rendimiento_gps_km_l", "costo_por_km_gps",
            "cargas_sin_ticket", "cargas_sin_folio", "cargas_sin_km",
        ]
        st.dataframe(monthly[[c for c in cols if c in monthly.columns]], use_container_width=True, hide_index=True)

        chart = monthly.groupby("placas", as_index=False).agg(km_gps=("km_gps", "sum"), litros=("litros", "sum"), gasto=("gasto", "sum"))
        c1, c2 = st.columns(2)
        with c1:
            if chart["km_gps"].sum() > 0:
                st.subheader("Km GPS por unidad")
                st.bar_chart(chart, x="placas", y="km_gps")
        with c2:
            if chart["gasto"].sum() > 0:
                st.subheader("Gasto por unidad")
                st.bar_chart(chart, x="placas", y="gasto")

with tab_cycles:
    st.subheader("Rendimiento aproximado por ciclo de carga")
    st.warning(
        "Este cálculo usa los km GPS entre una carga y la siguiente, y los litros de la carga de cierre. "
        "Es una lectura operativa; para rendimiento mecánico exacto se necesita controlar cargas a tanque lleno."
    )
    if cycles.empty:
        st.info("No hay ciclos cerrados. Se necesitan al menos dos cargas de la misma unidad en el rango.")
    else:
        cols = [
            "placas", "carga_anterior_id", "carga_actual_id", "inicio_periodo", "fin_periodo",
            "litros", "gasto", "km_gps", "horas_movimiento", "rendimiento_gps_km_l", "costo_por_km_gps",
            "estado_analisis", "alertas", "folio", "gasolinera",
        ]
        st.dataframe(cycles[[c for c in cols if c in cycles.columns]], use_container_width=True, hide_index=True)

        ok = cycles[cycles["estado_analisis"].eq("OK")]
        if not ok.empty:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Rendimiento por ciclo")
                st.bar_chart(ok, x="carga_actual_id", y="rendimiento_gps_km_l")
            with c2:
                st.subheader("Costo/km por ciclo")
                st.bar_chart(ok, x="carga_actual_id", y="costo_por_km_gps")

        revisar = cycles[~cycles["estado_analisis"].eq("OK")]
        if not revisar.empty:
            st.subheader("Ciclos que requieren revisión")
            st.dataframe(revisar[[c for c in cols if c in revisar.columns]], use_container_width=True, hide_index=True)

with tab_notes:
    st.subheader("Cómo leer estos indicadores")
    st.markdown(
        """
- **Vista mensual:** buena para gasto contable, litros y km acumulados. No siempre sirve para rendimiento real si las cargas cruzan de un mes a otro.
- **Ciclo de carga:** mejor para detectar anomalías, pero sigue siendo aproximado si no se registra si la carga fue completa o parcial.
- **Carga parcial/no concluyente:** no debe usarse para juzgar rendimiento de la unidad; sirve como alerta para revisar captura o patrón de carga.
- **Sin hora de carga:** el cálculo usa una hora aproximada. Para mejorar el cruce con GPS, conviene capturar hora real del ticket.
- **Sin folio/ticket:** afecta trazabilidad aunque el cálculo numérico pueda hacerse.
        """
    )
