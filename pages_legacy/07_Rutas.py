from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from modules.logistics_repository import create_route, get_route, list_routes, set_route_status, update_route
from modules.repository import list_conductors, list_units

st.title("🚚 Rutas")

st.caption("Captura la salida/regreso de cada ruta. Las entregas se capturan en la página Entregas de ruta.")

units = list_units(active_only=True)
conductors = list_conductors(active_only=True)
if not units:
    st.warning("Primero registra unidades activas.")
    st.stop()
if not conductors:
    st.warning("Primero registra conductores activos.")
    st.stop()

unit_options = {u["id"]: u["placas"] for u in units}
driver_options = {d["id"]: d["nombre"] for d in conductors}
status_options = ["Abierta", "Cerrada pendiente de GPS", "GPS cargado", "Conciliada con GPS", "Validada", "Con incidencias", "ANULADA_PRUEBA"]
type_options = ["OPERATIVA", "PRUEBA", "CAPACITACION"]

with st.expander("Crear nueva ruta", expanded=True):
    with st.form("create_route_form"):
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            fecha = st.date_input("Fecha", value=date.today())
        with c2:
            unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
        with c3:
            conductor_id = st.selectbox("Chofer", options=list(driver_options.keys()), format_func=lambda x: driver_options[x])
        with c4:
            hora_salida = st.text_input("Hora salida", value=datetime.now().strftime("%H:%M"))
        with c5:
            tipo_ruta = st.selectbox("Tipo ruta", options=type_options, index=0)
        observaciones = st.text_area("Observaciones iniciales", value="")
        save = st.form_submit_button("Crear ruta")

    if save:
        if not hora_salida.strip():
            st.error("La hora de salida es obligatoria.")
        else:
            route_id = create_route({
                "fecha": str(fecha),
                "unidad_id": unidad_id,
                "conductor_id": conductor_id,
                "hora_salida_reportada": hora_salida.strip(),
                "estado_ruta": "Abierta",
                "tipo_ruta": tipo_ruta,
                "observaciones_generales": observaciones.strip(),
            })
            st.success(f"Ruta creada con ID #{route_id}.")

st.divider()
st.subheader("Rutas registradas")

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    unidad_filter = st.selectbox("Filtrar unidad", options=[0] + list(unit_options.keys()), format_func=lambda x: "Todas" if x == 0 else unit_options[x])
with c2:
    estado_filter = st.selectbox("Estado", options=["Todos"] + status_options)
with c3:
    desde = st.date_input("Desde", value=date.today().replace(day=1))
with c4:
    hasta = st.date_input("Hasta", value=date.today())
with c5:
    tipo_filter = st.selectbox("Tipo", options=["Todas"] + type_options)

routes_df = list_routes({
    "unidad_id": None if unidad_filter == 0 else unidad_filter,
    "estado_ruta": None if estado_filter == "Todos" else estado_filter,
    "fecha_desde": str(desde),
    "fecha_hasta": str(hasta),
    "tipo_ruta": None if tipo_filter == "Todas" else tipo_filter,
})

if routes_df.empty:
    st.info("No hay rutas con esos filtros.")
    st.stop()

show_cols = [
    "id", "fecha", "placas", "conductor_nombre", "hora_salida_reportada",
    "hora_regreso_reportada", "estado_ruta", "tipo_ruta", "entregas_capturadas", "observaciones_generales"
]
st.dataframe(routes_df[show_cols], use_container_width=True, hide_index=True)

selected_route_id = st.selectbox("Selecciona ruta para editar/cerrar", options=routes_df["id"].tolist())
route = get_route(int(selected_route_id))
if route:
    st.subheader(f"Ruta #{selected_route_id} | {route['fecha']} | {route['placas']}")
    with st.form(f"edit_route_{selected_route_id}"):
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            fecha_edit = st.date_input("Fecha", value=pd.to_datetime(route["fecha"]).date(), key=f"fecha_{selected_route_id}")
        with c2:
            unidad_edit = st.selectbox("Unidad", options=list(unit_options.keys()), index=list(unit_options.keys()).index(route["unidad_id"]), format_func=lambda x: unit_options[x], key=f"unidad_{selected_route_id}")
        with c3:
            conductor_edit = st.selectbox("Chofer", options=list(driver_options.keys()), index=list(driver_options.keys()).index(route["conductor_id"]), format_func=lambda x: driver_options[x], key=f"chofer_{selected_route_id}")
        with c4:
            estado_edit = st.selectbox("Estado", options=status_options, index=status_options.index(route["estado_ruta"]) if route["estado_ruta"] in status_options else 0, key=f"estado_{selected_route_id}")
        with c5:
            tipo_edit = st.selectbox("Tipo ruta", options=type_options, index=type_options.index(route.get("tipo_ruta") or "OPERATIVA") if (route.get("tipo_ruta") or "OPERATIVA") in type_options else 0, key=f"tipo_{selected_route_id}")
        c6, c7 = st.columns(2)
        with c6:
            hora_salida_edit = st.text_input("Hora salida", value=route.get("hora_salida_reportada") or "", key=f"salida_{selected_route_id}")
        with c7:
            hora_regreso_edit = st.text_input("Hora regreso", value=route.get("hora_regreso_reportada") or "", key=f"regreso_{selected_route_id}")
        obs_edit = st.text_area("Observaciones", value=route.get("observaciones_generales") or "", key=f"obs_{selected_route_id}")
        motivo_edit = st.selectbox("Motivo de corrección", options=["Error de captura", "Dato faltante", "Cambio operativo", "Corrección contra GPS", "Otro"], key=f"motivo_route_{selected_route_id}")
        comentario_edit = st.text_area("Comentario de corrección (obligatorio)", key=f"comentario_route_{selected_route_id}")
        save_edit = st.form_submit_button("Guardar cambios")

    if save_edit:
        if not comentario_edit.strip():
            st.error("El comentario de corrección es obligatorio.")
            st.stop()
        update_route(int(selected_route_id), {
            "fecha": str(fecha_edit),
            "unidad_id": unidad_edit,
            "conductor_id": conductor_edit,
            "hora_salida_reportada": hora_salida_edit.strip(),
            "hora_regreso_reportada": hora_regreso_edit.strip(),
            "estado_ruta": estado_edit,
            "tipo_ruta": tipo_edit,
            "observaciones_generales": obs_edit.strip(),
        }, motivo=motivo_edit, comentario=comentario_edit.strip())
        st.success("Ruta actualizada.")

    with st.expander("Cerrar ruta rápido", expanded=False):
        hora_regreso_now = st.text_input("Hora de regreso", value=datetime.now().strftime("%H:%M"), key=f"close_time_{selected_route_id}")
        comentario_cierre = st.text_input("Comentario de cierre", value="Cierre de ruta", key=f"close_comment_{selected_route_id}")
        if st.button("Cerrar como pendiente de GPS", key=f"close_btn_{selected_route_id}"):
            update_route(int(selected_route_id), {
                "fecha": route["fecha"],
                "unidad_id": route["unidad_id"],
                "conductor_id": route["conductor_id"],
                "hora_salida_reportada": route.get("hora_salida_reportada"),
                "hora_regreso_reportada": hora_regreso_now.strip(),
                "estado_ruta": "Cerrada pendiente de GPS",
                "tipo_ruta": route.get("tipo_ruta") or "OPERATIVA",
                "observaciones_generales": route.get("observaciones_generales"),
            }, motivo="Cierre de ruta", comentario=comentario_cierre.strip() or "Cierre de ruta")
            st.success("Ruta cerrada como pendiente de GPS.")
