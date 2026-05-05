from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.gps_matcher import (
    manual_associate_delivery_to_stop,
    nearby_stop_suggestions_for_route,
    reconcile_route_with_gps,
    route_gps_reconciliation_view,
    unmatched_gps_stops_for_route,
)
from modules.logistics_repository import get_delivery, get_route, list_routes, update_delivery

st.title("🔗 Conciliación GPS vs entregas")

st.caption(
    "La entrega se asocia automáticamente cuando la hora de llegada capturada cae dentro de una inmovilización GPS. "
    "Si capturaste mal la hora de llegada, corrige la entrega; no basta con corregir la salida/regreso de la ruta."
)

routes_df = list_routes({"fecha_desde": "2026-01-01"})
if routes_df.empty:
    st.warning("No hay rutas registradas.")
    st.stop()

routes_df["label"] = routes_df.apply(
    lambda r: f"#{r['id']} | {r['fecha']} | {r['placas']} | {r['conductor_nombre']} | {r['estado_ruta']} | {r['entregas_capturadas']} entregas",
    axis=1,
)
route_id = st.selectbox("Ruta", options=routes_df["id"].tolist(), format_func=lambda x: routes_df.loc[routes_df["id"] == x, "label"].iloc[0])
route = get_route(int(route_id))
if not route:
    st.error("No se encontró la ruta.")
    st.stop()

st.info(
    f"Ruta #{route['id']} | {route['fecha']} | Unidad {route['placas']} | Chofer {route['conductor_nombre']} | "
    f"Salida {route.get('hora_salida_reportada') or '-'} | Regreso {route.get('hora_regreso_reportada') or '-'} | Estado {route.get('estado_ruta')}"
)

if st.button("Ejecutar conciliación automática", type="primary"):
    try:
        result = reconcile_route_with_gps(int(route_id))
        estado = result.get("estado_ruta_resultante")
        if estado in {"Conciliada completa", "Conciliación con cercanas"}:
            st.success(f"Conciliación terminada. Estado de ruta: {estado}")
        elif estado in {"Conciliación parcial", "Conciliación con conflictos"}:
            st.warning(f"Conciliación terminada con pendientes. Estado de ruta: {estado}")
        else:
            st.error(f"Conciliación terminada, pero hay inconsistencias. Estado de ruta: {estado}")
        st.json(result)
    except Exception as exc:
        st.error(f"No se pudo conciliar: {exc}")

st.divider()
st.subheader("Resultado de conciliación")

view_df = route_gps_reconciliation_view(int(route_id))
if view_df.empty:
    st.info("La ruta no tiene entregas capturadas.")
else:
    show_cols = [
        "entrega_id", "orden_calculado", "cliente_nombre", "destino_nombre", "hora_llegada_reportada",
        "estatus_entrega", "estado_conciliacion_gps", "inicio_gps", "fin_gps",
        "tiempo_en_cliente_seg", "direccion_gps", "confianza"
    ]
    for col in show_cols:
        if col not in view_df.columns:
            view_df[col] = None
    st.dataframe(view_df[show_cols], use_container_width=True, hide_index=True)

    # Data consistency warnings for route vs delivery hours.
    bad_rows = []
    for _, row in view_df.iterrows():
        try:
            h = str(row.get("hora_llegada_reportada") or "")
            salida = str(row.get("hora_salida_reportada") or "")
            regreso = str(row.get("hora_regreso_reportada") or "")
            if salida and h and h < salida:
                bad_rows.append(f"Entrega #{row.get('entrega_id')} ({row.get('cliente_nombre')}): llegada {h} anterior a salida {salida}.")
            if regreso and h and h > regreso:
                bad_rows.append(f"Entrega #{row.get('entrega_id')} ({row.get('cliente_nombre')}): llegada {h} posterior a regreso {regreso}.")
        except Exception:
            pass
    if bad_rows:
        st.error("Hay entregas con horas fuera del intervalo de la ruta:")
        for msg in bad_rows:
            st.write(f"- {msg}")

st.subheader("Sugerencias para entregas sin GPS asociado")
suggestions = nearby_stop_suggestions_for_route(int(route_id), max_minutes=120, top_n=5)
if suggestions.empty:
    st.info("No hay sugerencias pendientes o no hay paradas GPS cercanas dentro del horario de la ruta.")
else:
    st.caption("Estas sugerencias no modifican nada hasta que confirmes una acción. Úsalas para corregir una hora mal capturada o asociar manualmente una parada.")
    st.dataframe(suggestions, use_container_width=True, hide_index=True)

    suggestions["label"] = suggestions.apply(
        lambda r: f"Entrega #{r['entrega_id']} | {r['cliente_nombre']} → parada #{r['gps_parada_id']} | {r['inicio_gps']} - {r['fin_gps']} | diff {r['diferencia_min']} min | {str(r['direccion_gps'])[:70]}",
        axis=1,
    )
    selected_label = st.selectbox("Selecciona una sugerencia para corregir/asociar", options=suggestions["label"].tolist())
    selected = suggestions[suggestions["label"] == selected_label].iloc[0].to_dict()
    st.write(
        f"**Sugerencia seleccionada:** entrega #{selected['entrega_id']} con parada GPS #{selected['gps_parada_id']} "
        f"desde {selected['inicio_gps']} hasta {selected['fin_gps']}."
    )

    with st.form("apply_suggestion_form"):
        action = st.radio(
            "Acción",
            [
                "Corregir hora de llegada al inicio de la parada GPS y volver a conciliar",
                "Asociar manualmente sin cambiar la hora capturada",
            ],
        )
        motivo = st.selectbox("Motivo", options=["Corrección contra GPS", "Error de captura", "Asociación manual validada", "Otro"])
        comentario = st.text_area("Comentario obligatorio", value="")
        apply = st.form_submit_button("Aplicar acción")

    if apply:
        if not comentario.strip():
            st.error("El comentario es obligatorio.")
        else:
            try:
                delivery = get_delivery(int(selected["entrega_id"]))
                if not delivery:
                    raise ValueError("No se encontró la entrega seleccionada.")
                if action.startswith("Corregir hora"):
                    update_delivery(
                        int(selected["entrega_id"]),
                        {
                            "cliente_nombre": delivery["cliente_nombre"],
                            "destino_nombre": delivery["destino_nombre"],
                            "hora_llegada_reportada": selected["hora_sugerida"],
                            "estatus_entrega": delivery["estatus_entrega"],
                            "motivo_no_entrega": delivery["motivo_no_entrega"],
                            "observaciones": delivery["observaciones"],
                            "estado_conciliacion_gps": "Pendiente de GPS",
                        },
                        motivo=motivo,
                        comentario=comentario.strip(),
                    )
                    result = reconcile_route_with_gps(int(route_id))
                    st.success(f"Hora corregida a {selected['hora_sugerida']} y conciliación recalculada.")
                    st.json(result)
                else:
                    manual_associate_delivery_to_stop(
                        int(selected["entrega_id"]),
                        int(selected["gps_parada_id"]),
                        motivo=motivo,
                        comentario=comentario.strip(),
                    )
                    st.success("Entrega asociada manualmente a la parada GPS seleccionada.")
            except Exception as exc:
                st.error(f"No se pudo aplicar la sugerencia: {exc}")

st.subheader("Paradas GPS no asociadas de esta ruta")
unmatched = unmatched_gps_stops_for_route(int(route_id))
if unmatched.empty:
    st.info("No hay paradas GPS no asociadas para esta ruta o todavía no hay GPS cargado.")
else:
    show = ["id", "inicio_gps", "fin_gps", "duracion_seg", "direccion_gps", "clasificacion_inicial", "requiere_revision"]
    st.dataframe(unmatched[show], use_container_width=True, hide_index=True)
