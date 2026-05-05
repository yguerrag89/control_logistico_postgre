from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from modules.business_analytics import default_date_range, fuel_quality_summary, monthly_fuel_gps_summary
from modules.gps_analytics import (
    get_abnormal_inactivity,
    get_daily_km_by_unit_complete,
    get_daily_km_total,
    get_daily_km_total_complete,
    get_unit_activity_summary,
)
from modules.logistics_repository import route_summary_metrics
from modules.operational_quality import operational_pending_counts, route_state_inconsistencies, route_time_inconsistencies
from modules.repository import list_units
from modules.session import sidebar_user_context, require_admin
from modules.logistics_costs import cost_summary

ctx = require_admin()
ctx = sidebar_user_context()

st.title("📊 Panel general")
st.caption("Resumen ejecutivo de flota, GPS, combustible, alertas y calidad de datos.")

start_default, end_default = default_date_range(days_back=45)
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

fuel_q = fuel_quality_summary(filters)
activity = get_unit_activity_summary(filters)
daily_total = get_daily_km_total(filters)
routes = route_summary_metrics()
costs_extra = cost_summary(filters)
pendientes = operational_pending_counts(filters)

km_total = float(activity["km_total"].sum()) if not activity.empty and "km_total" in activity else 0.0
active_days = int(daily_total["fecha"].nunique()) if not daily_total.empty else 0
litros = float(fuel_q.get("litros", 0) or 0)
gasto = float(fuel_q.get("gasto", 0) or 0)
rend = round(km_total / litros, 2) if litros else None
gasto_extra = float(costs_extra.get("importe_total", 0) or 0)
gasto_total_logistico = gasto + gasto_extra
cost_km = round(gasto_total_logistico / km_total, 2) if km_total else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Km GPS", f"{km_total:,.1f}")
m2.metric("Días con movimiento", f"{active_days}")
m3.metric("Litros", f"{litros:,.1f}")
m4.metric("Gasto combustible", f"${gasto:,.2f}")

m5, m6, m7, m8 = st.columns(4)
m5.metric("Rendimiento GPS", f"{rend:,.2f} km/L" if rend is not None else "—")
m6.metric("Costo logístico/km GPS", f"${cost_km:,.2f}" if cost_km is not None else "—")
m7.metric("Paradas largas sin clasificar", f"{pendientes.get("paradas_largas_sin_clasificar", 0):,}")
m8.metric("Pendientes críticos", f"{sum(pendientes.values()):,}")

tab_oper, tab_gps, tab_fuel, tab_alerts = st.tabs(["Operación", "GPS", "Combustible", "Alertas / calidad"])

with tab_oper:
    st.subheader("Estado operativo")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rutas abiertas", routes.get("rutas_abiertas", 0))
    c2.metric("Rutas pendientes GPS", routes.get("rutas_pendientes_gps", 0))
    c3.metric("Entregas pendientes GPS", routes.get("entregas_pendientes_gps", 0))
    c4.metric("Entregas sin match", routes.get("entregas_sin_match", 0))
    st.info("Cuando empieces a capturar rutas, esta pestaña mostrará el flujo operativo completo. Por ahora el valor principal viene de GPS + combustible.")

with tab_gps:
    st.subheader("Actividad GPS diaria por unidad")
    st.caption("La gráfica incluye todos los días del rango seleccionado. Los días sin movimiento aparecen con 0 km para evitar lecturas incompletas.")

    daily_unit_complete = get_daily_km_by_unit_complete(filters)
    daily_total_complete = get_daily_km_total_complete(filters)

    if daily_unit_complete.empty:
        st.info("No hay actividad GPS en el rango seleccionado.")
    else:
        dias_periodo = int(daily_total_complete["fecha"].nunique()) if not daily_total_complete.empty else 0
        dias_con_mov = int((daily_total_complete["km_total"] > 0).sum()) if not daily_total_complete.empty else 0
        dias_sin_mov = max(dias_periodo - dias_con_mov, 0)
        km_prom_dia = (float(daily_total_complete["km_total"].sum()) / dias_periodo) if dias_periodo else 0

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Días del rango", dias_periodo)
        g2.metric("Días con movimiento", dias_con_mov)
        g3.metric("Días sin movimiento", dias_sin_mov)
        g4.metric("Km promedio/día calendario", f"{km_prom_dia:,.1f}")

        chart_df = daily_unit_complete.copy()
        chart_df["km_gps"] = pd.to_numeric(chart_df["km_gps"], errors="coerce").fillna(0)
        date_order = chart_df.drop_duplicates("fecha").sort_values("fecha")["fecha_label"].tolist()

        line = alt.Chart(chart_df).mark_line(point=True).encode(
            x=alt.X(
                "fecha_label:N",
                sort=date_order,
                title="Día",
                axis=alt.Axis(labelAngle=-45),
            ),
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
        st.altair_chart(line, use_container_width=True)

        with st.expander("Ver tabla diaria completa por unidad"):
            cols = ["fecha", "placas", "km_gps", "movimientos", "horas_movimiento", "es_dia_sin_movimiento"]
            st.dataframe(daily_unit_complete[cols], use_container_width=True, hide_index=True)

        st.subheader("Km totales de flota por día")
        if not daily_total_complete.empty:
            bar = alt.Chart(daily_total_complete).mark_bar().encode(
                x=alt.X(
                    "fecha_label:N",
                    sort=daily_total_complete["fecha_label"].tolist(),
                    title="Día",
                    axis=alt.Axis(labelAngle=-45),
                ),
                y=alt.Y("km_total:Q", title="Kilómetros recorridos (km)"),
                tooltip=[
                    alt.Tooltip("fecha:N", title="Fecha"),
                    alt.Tooltip("km_total:Q", title="Km total", format=",.2f"),
                    alt.Tooltip("unidades_activas:Q", title="Unidades activas"),
                    alt.Tooltip("movimientos_totales:Q", title="Movimientos"),
                ],
            ).properties(height=260)
            st.altair_chart(bar, use_container_width=True)

    st.subheader("Resumen por unidad")
    if activity.empty:
        st.info("No hay actividad por unidad.")
    else:
        display_activity = activity.copy()
        if "km_promedio_dia_activo" in display_activity.columns:
            display_activity = display_activity.rename(columns={"km_promedio_dia_activo": "km_promedio_por_día_activo"})
        st.dataframe(display_activity, use_container_width=True, hide_index=True)

with tab_fuel:
    st.subheader("Combustible + GPS")
    st.caption(f"Costos adicionales registrados en el rango: ${gasto_extra:,.2f}. El costo logístico/km usa combustible + costos adicionales.")
    monthly = monthly_fuel_gps_summary(filters)
    if monthly.empty:
        st.info("No hay datos de combustible/GPS en el rango.")
    else:
        cols = [c for c in ["mes", "placas", "km_gps", "litros", "gasto", "rendimiento_gps_km_l", "costo_por_km_gps", "cargas", "cargas_sin_ticket", "cargas_sin_folio"] if c in monthly.columns]
        st.dataframe(monthly[cols], use_container_width=True, hide_index=True)

with tab_alerts:
    st.subheader("Pendientes operativos críticos")
    incons_horario = route_time_inconsistencies(filters)
    incons_estado = route_state_inconsistencies()
    pendientes_df = pd.DataFrame([
        {"pendiente": "Cargas sin ticket", "cantidad": pendientes.get("cargas_sin_ticket", 0), "accion_sugerida": "Completar evidencia en Combustible > Historial"},
        {"pendiente": "Cargas sin folio", "cantidad": pendientes.get("cargas_sin_folio", 0), "accion_sugerida": "Completar folios de ticket"},
        {"pendiente": "Cargas no concluyentes/parciales", "cantidad": pendientes.get("cargas_no_concluyentes", 0), "accion_sugerida": "Revisar tipo de carga combustible"},
        {"pendiente": "Entregas fuera del horario de ruta", "cantidad": pendientes.get("entregas_fuera_horario", 0), "accion_sugerida": "Corregir hora de llegada o ruta"},
        {"pendiente": "Rutas con estado incoherente", "cantidad": pendientes.get("rutas_estado_incoherente", 0), "accion_sugerida": "Recalcular estados en Auditoría"},
        {"pendiente": "Paradas largas sin clasificar", "cantidad": pendientes.get("paradas_largas_sin_clasificar", 0), "accion_sugerida": "Clasificar en GPS y Actividad"},
        {"pendiente": "Evidencias con ruta absoluta", "cantidad": pendientes.get("evidencias_absolutas", 0), "accion_sugerida": "Normalizar rutas en Auditoría"},
        {"pendiente": "Destinos pendientes de validar", "cantidad": pendientes.get("destinos_pendientes_validar", 0), "accion_sugerida": "Validar Catálogos > Lugares controlados"},
    ])
    st.dataframe(pendientes_df, use_container_width=True, hide_index=True)
    if not incons_horario.empty:
        st.warning("Hay entregas con hora de llegada fuera del intervalo de ruta.")
        st.dataframe(incons_horario.head(10), use_container_width=True, hide_index=True)
    if not incons_estado.empty:
        st.warning("Hay rutas con estado de conciliación incoherente. Recalcula estados desde Auditoría y Correcciones.")
        st.dataframe(incons_estado.head(10), use_container_width=True, hide_index=True)

    st.subheader("Calidad de datos")
    q1, q2, q3, q4, q5 = st.columns(5)
    q1.metric("Cargas", fuel_q.get("cargas", 0))
    q2.metric("Sin ticket", fuel_q.get("sin_ticket", 0))
    q3.metric("Sin folio", fuel_q.get("sin_folio", 0))
    q4.metric("Sin odómetro", fuel_q.get("sin_km", 0))
    q5.metric("No concluyentes", fuel_q.get("no_concluyentes", 0))

    st.subheader("Paradas anormales detectadas")
    abnormal = get_abnormal_inactivity(filters, min_minutes=30, exclude_authorized=True, unmatched_only=True)
    if abnormal.empty:
        st.success("No hay paradas anormales sin clasificar con los filtros actuales.")
    else:
        cols = ["fecha", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min", "nivel_alerta", "direccion_gps"]
        st.dataframe(abnormal[cols].head(25), use_container_width=True, hide_index=True)
        st.caption("Estas paradas excluyen base probable y lugares controlados. Revísalas en GPS y Actividad > Inactividad anormal.")
