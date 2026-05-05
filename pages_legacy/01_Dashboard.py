from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.business_analytics import (
    default_date_range,
    destination_candidates_from_gps,
    fuel_gps_cycles,
    fuel_quality_summary,
    gps_activity_by_day,
    monthly_fuel_gps_summary,
    stops_operational_view,
)
from modules.gps_repository import gps_summary_by_unit
from modules.logistics_repository import route_summary_metrics
from modules.repository import list_charges, list_units

st.title("📊 Dashboard de control logístico")
st.caption("Panel ejecutivo con combustible, GPS, calidad de datos y alertas operativas.")

start_default, end_default = default_date_range(days_back=45)
units = list_units(active_only=False)
unit_options = {0: "Todas"} | {u["id"]: u["placas"] for u in units}

with st.container():
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

# --- Executive KPIs ---
log_metrics = route_summary_metrics()
gps_summary = gps_summary_by_unit(filters)
fuel_quality = fuel_quality_summary(filters)
monthly = monthly_fuel_gps_summary(filters)
critical_stops = stops_operational_view(filters, unmatched_only=True, min_minutes=30, max_hours=8, exclude_probable_base=True)
gps_candidates = destination_candidates_from_gps(filters, min_minutes=15, limit=50)

km_total = float(monthly["km_gps"].sum()) if not monthly.empty and "km_gps" in monthly else 0.0
litros_total = float(fuel_quality.get("litros", 0) or 0)
gasto_total = float(fuel_quality.get("gasto", 0) or 0)
rendimiento = (km_total / litros_total) if litros_total else None
costo_km = (gasto_total / km_total) if km_total else None

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Km GPS", f"{km_total:,.1f}")
k2.metric("Gasto combustible", f"${gasto_total:,.2f}")
k3.metric("Litros", f"{litros_total:,.2f} L")
k4.metric("Rendimiento GPS", "-" if rendimiento is None else f"{rendimiento:,.2f} km/L")
k5.metric("Costo/km GPS", "-" if costo_km is None else f"${costo_km:,.2f}")

op1, op2, op3, op4, op5 = st.columns(5)
op1.metric("Rutas abiertas", log_metrics.get("rutas_abiertas", 0))
op2.metric("Pendientes GPS", log_metrics.get("rutas_pendientes_gps", 0))
op3.metric("Entregas pendientes GPS", log_metrics.get("entregas_pendientes_gps", 0))
op4.metric("Paradas críticas", len(critical_stops))
op5.metric("Destinos GPS candidatos", len(gps_candidates))

tab_resumen, tab_gps, tab_comb, tab_alertas = st.tabs(["Resumen", "GPS", "Combustible", "Alertas y calidad"])

with tab_resumen:
    st.subheader("Resumen mensual combustible + GPS")
    if monthly.empty:
        st.info("No hay datos de combustible ni GPS en el rango seleccionado.")
    else:
        cols = [
            "mes", "placas", "km_gps", "horas_movimiento", "dias_con_movimiento",
            "litros", "gasto", "cargas", "rendimiento_gps_km_l", "costo_por_km_gps",
        ]
        st.dataframe(monthly[[c for c in cols if c in monthly.columns]], use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        with c1:
            chart = monthly.groupby("placas", as_index=False)["km_gps"].sum().sort_values("km_gps", ascending=False)
            if not chart.empty and chart["km_gps"].sum() > 0:
                st.bar_chart(chart, x="placas", y="km_gps")
        with c2:
            chart2 = monthly.groupby("placas", as_index=False)["gasto"].sum().sort_values("gasto", ascending=False)
            if not chart2.empty and chart2["gasto"].sum() > 0:
                st.bar_chart(chart2, x="placas", y="gasto")

with tab_gps:
    st.subheader("Actividad GPS por día")
    activity = gps_activity_by_day(filters)
    if activity.empty:
        st.info("No hay movimientos GPS en el rango.")
    else:
        st.dataframe(activity.head(100), use_container_width=True, hide_index=True)
        top_days = activity.sort_values("km_gps", ascending=False).head(15)
        st.caption("Top días por km GPS")
        st.dataframe(top_days, use_container_width=True, hide_index=True)

    st.subheader("Paradas operativas no asociadas")
    if critical_stops.empty:
        st.success("No hay paradas operativas grandes pendientes en el rango seleccionado.")
    else:
        show_cols = ["id", "fecha", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min", "direccion_gps", "categoria_sugerida"]
        st.dataframe(critical_stops[[c for c in show_cols if c in critical_stops.columns]].head(50), use_container_width=True, hide_index=True)

with tab_comb:
    st.subheader("Ciclos de carga con GPS")
    cycles = fuel_gps_cycles(filters)
    if cycles.empty:
        st.info("Se necesitan al menos dos cargas de una misma unidad dentro del rango para crear ciclos.")
    else:
        cols = [
            "placas", "carga_anterior_id", "carga_actual_id", "inicio_periodo", "fin_periodo",
            "litros", "gasto", "km_gps", "rendimiento_gps_km_l", "costo_por_km_gps", "estado_analisis", "alertas",
        ]
        st.dataframe(cycles[[c for c in cols if c in cycles.columns]], use_container_width=True, hide_index=True)

    st.subheader("Calidad de registros de combustible")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Cargas", fuel_quality.get("cargas", 0))
    q2.metric("Sin ticket", fuel_quality.get("sin_ticket", 0))
    q3.metric("Sin folio", fuel_quality.get("sin_folio", 0))
    q4.metric("Sin kilometraje", fuel_quality.get("sin_km", 0))

    charges = list_charges({**filters, "active_only": True})
    if not charges.empty:
        st.caption("Últimas cargas")
        show = ["id", "fecha_carga", "hora_carga", "placas", "conductor_nombre", "litros", "importe_total", "ticket_folio", "kilometraje", "estado_validacion"]
        st.dataframe(charges[[c for c in show if c in charges.columns]].sort_values(["fecha_carga", "id"], ascending=False), use_container_width=True, hide_index=True)

with tab_alertas:
    st.subheader("Alertas accionables")
    alerts: list[str] = []
    if fuel_quality.get("sin_ticket", 0):
        alerts.append(f"Hay {fuel_quality['sin_ticket']} cargas sin foto de ticket.")
    if fuel_quality.get("sin_folio", 0):
        alerts.append(f"Hay {fuel_quality['sin_folio']} cargas sin folio.")
    if fuel_quality.get("sin_km", 0):
        alerts.append(f"Hay {fuel_quality['sin_km']} cargas sin odómetro; se pueden analizar con GPS, pero no comparar odómetro vs GPS.")
    if not critical_stops.empty:
        alerts.append(f"Hay {len(critical_stops)} paradas operativas >= 30 min sin entrega, combustible o clasificación.")
    if len(gps_candidates):
        alerts.append(f"Hay {len(gps_candidates)} direcciones GPS candidatas para alimentar el catálogo de destinos.")
    if monthly.empty:
        alerts.append("No hay datos en el rango seleccionado. Revisa los filtros.")

    if not alerts:
        st.success("No hay alertas críticas con los filtros actuales.")
    else:
        for a in alerts:
            st.warning(a)

    if not gps_candidates.empty:
        st.subheader("Candidatos rápidos de destinos desde GPS")
        st.dataframe(gps_candidates.head(20), use_container_width=True, hide_index=True)
