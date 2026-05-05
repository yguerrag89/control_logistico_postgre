from __future__ import annotations

from datetime import datetime

import streamlit as st

from modules.time_utils import now_mx, today_mx, hhmm, time_no_seconds
from modules.gps_matcher import reconcile_route_with_gps, route_gps_reconciliation_view
from modules.logistics_repository import (
    add_delivery_evidence,
    create_delivery,
    create_route,
    get_route,
    list_deliveries,
    list_routes,
    save_evidence_file,
    update_delivery,
    validate_delivery_time_against_route,
)
from modules.operations import finalize_route, route_closure_snapshot
from modules.repository import list_conductors, list_units
from modules.session import sidebar_user_context
from modules.navigation import run_legacy_page

ctx = sidebar_user_context()
usuario = ctx["usuario"]
rol = ctx["rol"]
conductor_id_sesion = ctx.get("conductor_id")

st.title("🚚 Rutas y entregas")

if rol == "Chofer":
    st.caption("Modo móvil para chofer: captura solo tu ruta, entregas, evidencias y cierre.")
    section = "Modo chofer"
else:
    st.caption("Captura operativa, modo chofer, cierre de ruta y conciliación GPS en un solo flujo.")
    sections = ["Modo chofer", "Panel de rutas", "Cierre operativo", "Conciliación GPS", "Administración avanzada"]
    section = st.radio("Sección", sections, index=1, horizontal=True)
    st.divider()

units = list_units(active_only=True)
conductors = list_conductors(active_only=True)
unit_options = {u["id"]: u["placas"] for u in units}
conductor_options = {c["id"]: c["nombre"] for c in conductors}

if section == "Modo chofer":
    st.subheader("📱 Modo chofer")

    if rol == "Chofer":
        if not conductor_id_sesion:
            st.error("Tu usuario de chofer no está vinculado a un conductor activo. Pide al administrador revisar Catálogos > Usuarios.")
            st.stop()
        selected_conductor = int(conductor_id_sesion)
        st.info(f"Chofer: **{ctx.get('conductor_nombre') or usuario}**")
    else:
        st.caption("Administrador probando el modo chofer.")
        if conductor_options:
            selected_conductor = st.selectbox("Chofer", options=list(conductor_options.keys()), format_func=lambda x: conductor_options[x])
        else:
            st.error("No hay conductores activos.")
            st.stop()

    fecha = st.date_input("Fecha de ruta", value=today_mx())
    routes = list_routes({"fecha_desde": str(fecha), "fecha_hasta": str(fecha), "conductor_id": selected_conductor})

    if routes.empty:
        st.warning("No tienes ruta creada para esta fecha. Crea una ruta rápida antes de registrar entregas.")

        manual_key = f"quick_route_salida_manual_{selected_conductor}_{fecha}"
        if manual_key not in st.session_state:
            st.session_state[manual_key] = time_no_seconds(now_mx())

        with st.form("quick_route_form", border=True):
            if not unit_options:
                st.error("No hay unidades activas.")
                st.stop()

            unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
            usar_hora_actual = st.checkbox(
                "Usar hora actual al guardar",
                value=True,
                help="Recomendado. La hora se toma cuando presionas Iniciar ruta; no se actualiza sola mientras llenas el formulario.",
            )
            salida_manual = st.time_input(
                "Hora manual de salida, solo si desactivas la opción anterior",
                key=manual_key,
            )
            obs = st.text_area("Observaciones iniciales", value="", placeholder="Opcional")
            submit = st.form_submit_button("🚚 Iniciar ruta", use_container_width=True, type="primary")

        if submit:
            hora_salida = hhmm(now_mx()) if usar_hora_actual else salida_manual.strftime("%H:%M")
            route_id = create_route({
                "fecha": str(fecha),
                "unidad_id": int(unidad_id),
                "conductor_id": int(selected_conductor),
                "hora_salida_reportada": hora_salida,
                "hora_regreso_reportada": None,
                "estado_ruta": "Abierta",
                "tipo_ruta": "OPERATIVA",
                "observaciones_generales": obs.strip(),
            }, motivo="Alta rápida chofer", comentario="Ruta creada desde modo chofer", usuario=usuario)
            st.success(f"Ruta #{route_id} creada a las {hora_salida} y guardada en la base de datos.")
            st.rerun()
        st.stop()

    routes["label"] = routes.apply(
        lambda r: f"#{r['id']} | {r['placas']} | salida {r.get('hora_salida_reportada') or '-'} | {r['estado_ruta']}",
        axis=1,
    )
    route_id = st.selectbox("Ruta del día", options=routes["id"].tolist(), format_func=lambda x: routes.loc[routes["id"] == x, "label"].iloc[0])
    route = get_route(int(route_id))
    if not route:
        st.error("No se encontró la ruta seleccionada.")
        st.stop()

    st.success(f"Ruta #{route_id} | Unidad {route['placas']} | Estado: {route['estado_ruta']}")

    st.markdown("### 📍 Registrar llegada al cliente")
    st.caption("Primero registra la llegada. El resultado final y la evidencia se capturan al cerrar la entrega.")

    arrival_key = f"arrival_manual_time_{route_id}"
    if arrival_key not in st.session_state:
        st.session_state[arrival_key] = time_no_seconds(now_mx())

    with st.form("arrival_form", border=True):
        cliente = st.text_input("Cliente", placeholder="Ej. Inova")
        destino = st.text_input("Destino / punto operativo", placeholder="Ej. Naucalpan / Tresguerras / CEDIS")

        usar_hora_actual_llegada = st.checkbox(
            "Usar hora actual al guardar llegada",
            value=True,
            help="Recomendado. La hora se toma cuando presionas Registrar llegada; no se actualiza sola mientras llenas el formulario.",
        )
        hora_llegada_manual = st.time_input(
            "Hora manual de llegada, solo si desactivas la opción anterior",
            key=arrival_key,
        )
        observaciones_llegada = st.text_area(
            "Observaciones de llegada",
            value="",
            placeholder="Opcional. Ej. En espera de recepción, fila, entrada por caseta...",
        )
        save_arrival = st.form_submit_button("📍 Registrar llegada", use_container_width=True, type="primary")

    if save_arrival:
        h = hhmm(now_mx()) if usar_hora_actual_llegada else hora_llegada_manual.strftime("%H:%M")
        errs = []
        if not cliente.strip():
            errs.append("Captura el cliente.")
        if not destino.strip():
            errs.append("Captura el destino.")

        warnings = validate_delivery_time_against_route(route, h)

        if errs:
            for e in errs:
                st.error(e)
        else:
            # En modo chofer no bloqueamos por horario inconsistente: guardamos y dejamos revisión administrativa.
            for w in warnings:
                st.warning(f"{w} Se guardará la llegada, pero quedará para revisión administrativa.")

            delivery_id = create_delivery({
                "ruta_id": int(route_id),
                "cliente_nombre": cliente.strip(),
                "destino_nombre": destino.strip(),
                "destino_id": None,
                "hora_llegada_reportada": h,
                "estatus_entrega": "Pendiente de cierre",
                "motivo_no_entrega": None,
                "observaciones": observaciones_llegada.strip(),
                "estado_conciliacion_gps": "Pendiente de GPS",
                "hora_captura_sistema": now_mx().isoformat(sep=" ", timespec="seconds"),
            }, motivo="Llegada chofer", comentario="Llegada registrada desde modo chofer", usuario=usuario)

            st.success(f"Llegada registrada a las {h}. Entrega #{delivery_id} pendiente de cierre.")
            st.rerun()

    st.markdown("### ✅ Cerrar entrega pendiente")
    deliveries = list_deliveries(route_id=int(route_id))

    if deliveries.empty:
        st.info("Todavía no hay entregas capturadas.")
    else:
        pending = deliveries[
            (deliveries["estatus_entrega"].fillna("") == "Pendiente de cierre")
            & (deliveries["activo"].fillna(1).astype(int) == 1)
        ].copy()

        if pending.empty:
            st.success("No hay entregas pendientes de cierre.")
        else:
            pending["label"] = pending.apply(
                lambda r: f"#{r['id']} | {r['cliente_nombre']} | {r['destino_nombre']} | llegada {r['hora_llegada_reportada']}",
                axis=1,
            )
            selected_delivery_id = st.selectbox(
                "Entrega pendiente",
                options=pending["id"].tolist(),
                format_func=lambda x: pending.loc[pending["id"] == x, "label"].iloc[0],
            )
            selected_delivery = pending[pending["id"] == selected_delivery_id].iloc[0].to_dict()

            with st.form("close_delivery_form", border=True):
                resultado = st.selectbox(
                    "Resultado final",
                    [
                        "Entregado completo",
                        "Entregado parcial",
                        "No entregado",
                        "Rechazado",
                        "Cliente cerrado",
                        "Reprogramado",
                        "Entregado en paquetería",
                        "Visita sin entrega",
                    ],
                )
                motivo = None
                if resultado != "Entregado completo":
                    motivo = st.selectbox(
                        "Motivo",
                        [
                            "Cliente cerrado",
                            "No recibió por horario",
                            "Producto incompleto",
                            "Producto incorrecto",
                            "Producto dañado",
                            "Falta de documentación",
                            "Dirección incorrecta",
                            "No había cita",
                            "Rechazo del cliente",
                            "No dio tiempo",
                            "Otro",
                        ],
                    )
                foto = st.file_uploader(
                    "Foto de evidencia",
                    type=["jpg", "jpeg", "png"],
                    accept_multiple_files=False,
                    help="La evidencia se captura al cerrar, no necesariamente al llegar.",
                )
                observaciones_cierre = st.text_area(
                    "Observaciones de cierre",
                    value="",
                    placeholder="Ej. Recibió completo, recibió parcial, cliente no recibió, etc.",
                )
                close_delivery = st.form_submit_button("✅ Cerrar entrega", use_container_width=True, type="primary")

            if close_delivery:
                if resultado != "Entregado completo" and foto is None:
                    st.error("Para entregas no completas/rechazadas/no entregadas, sube una evidencia o foto.")
                else:
                    observacion_original = selected_delivery.get("observaciones") or ""
                    observacion_final = observacion_original
                    if observaciones_cierre.strip():
                        observacion_final = (observacion_original + "\n\nCierre: " + observaciones_cierre.strip()).strip()

                    update_delivery(
                        int(selected_delivery_id),
                        {
                            "cliente_nombre": selected_delivery["cliente_nombre"],
                            "destino_nombre": selected_delivery["destino_nombre"],
                            "destino_id": selected_delivery.get("destino_id"),
                            "hora_llegada_reportada": selected_delivery["hora_llegada_reportada"],
                            "estatus_entrega": resultado,
                            "motivo_no_entrega": motivo,
                            "observaciones": observacion_final,
                            "estado_conciliacion_gps": selected_delivery.get("estado_conciliacion_gps", "Pendiente de GPS"),
                        },
                        motivo="Cierre entrega chofer",
                        comentario=f"Cierre registrado a las {hhmm(now_mx())}",
                        usuario=usuario,
                    )

                    if foto is not None:
                        path = save_evidence_file(foto, route, int(selected_delivery_id))
                        if path:
                            add_delivery_evidence(
                                int(selected_delivery_id),
                                path,
                                "Evidencia cierre entrega",
                                observaciones_cierre.strip(),
                                usuario=usuario,
                            )

                    st.success("Entrega cerrada correctamente.")
                    st.rerun()

    st.markdown("### Entregas capturadas")
    deliveries = list_deliveries(route_id=int(route_id))
    if deliveries.empty:
        st.info("Todavía no hay entregas capturadas.")
    else:
        st.caption("Las entregas con estatus 'Pendiente de cierre' ya tienen llegada registrada, pero aún falta capturar resultado final/evidencia.")
        cols = ["orden_calculado", "cliente_nombre", "destino_nombre", "hora_llegada_reportada", "estatus_entrega", "estado_conciliacion_gps"]
        st.dataframe(deliveries[cols], use_container_width=True, hide_index=True)

    st.markdown("### 🏁 Cerrar ruta")
    close_key = f"close_route_manual_time_{route_id}"
    if close_key not in st.session_state:
        st.session_state[close_key] = time_no_seconds(now_mx())

    with st.form("close_route_driver", border=True):
        usar_hora_actual_regreso = st.checkbox(
            "Usar hora actual al cerrar ruta",
            value=True,
            help="Recomendado. La hora se toma cuando presionas Cerrar ruta.",
        )
        regreso_manual = st.time_input(
            "Hora manual de regreso, solo si desactivas la opción anterior",
            key=close_key,
        )
        comentario = st.text_area("Observación de cierre", value="", placeholder="Opcional")
        close = st.form_submit_button("🏁 Cerrar ruta", use_container_width=True)

    if close:
        from modules.logistics_repository import update_route

        hora_regreso = hhmm(now_mx()) if usar_hora_actual_regreso else regreso_manual.strftime("%H:%M")
        payload = route.copy()
        payload.update({
            "hora_regreso_reportada": hora_regreso,
            "estado_ruta": "Cerrada pendiente de GPS",
            "observaciones_generales": (route.get("observaciones_generales") or "") + ("\n" + comentario if comentario else ""),
        })
        update_route(int(route_id), payload, motivo="Cierre chofer", comentario=comentario, usuario=usuario)
        st.success(f"Ruta cerrada a las {hora_regreso}. Queda pendiente de GPS/validación administrativa.")
        st.rerun()

elif section == "Panel de rutas":
    st.subheader("Panel de rutas")
    st.caption("Vista de supervisión: rutas, entregas, evidencias y estados.")
    run_legacy_page("07_Rutas.py")
    st.divider()
    run_legacy_page("08_Entregas_Ruta.py")

elif section == "Cierre operativo":
    st.subheader("Cierre operativo de ruta")
    routes = list_routes({"fecha_desde": "2026-01-01"})
    if routes.empty:
        st.info("No hay rutas para cerrar.")
        st.stop()
    routes["label"] = routes.apply(lambda r: f"#{r['id']} | {r['fecha']} | {r['placas']} | {r['conductor_nombre']} | {r['estado_ruta']} | {r['entregas_capturadas']} entregas", axis=1)
    route_id = st.selectbox("Ruta", options=routes["id"].tolist(), format_func=lambda x: routes.loc[routes["id"] == x, "label"].iloc[0])
    snapshot = route_closure_snapshot(int(route_id))
    if not snapshot:
        st.error("No se encontró la ruta.")
        st.stop()
    route = snapshot["route"]
    st.info(f"Ruta #{route['id']} | {route['fecha']} | {route['placas']} | {route['conductor_nombre']} | Estado actual: {route['estado_ruta']}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entregas", snapshot["entregas"])
    m2.metric("Con GPS", snapshot["entregas_con_gps"])
    m3.metric("Sin evidencia", snapshot["entregas_sin_evidencia"])
    m4.metric("Paradas GPS no asociadas", snapshot["paradas_gps_no_asociadas"])
    m5, m6, m7 = st.columns(3)
    m5.metric("Pendientes GPS", snapshot["entregas_pendientes_gps"])
    m6.metric("Incidencias entrega", snapshot["entregas_con_incidencia"])
    m7.metric("Fuera de horario", snapshot["entregas_fuera_horario"])

    if st.button("Ejecutar conciliación GPS", type="primary"):
        try:
            result = reconcile_route_with_gps(int(route_id))
            st.success(f"Conciliación ejecutada. Estado: {result.get('estado_ruta_resultante')}")
            st.json(result)
        except Exception as exc:
            st.error(f"No se pudo conciliar: {exc}")

    view = route_gps_reconciliation_view(int(route_id))
    if not view.empty:
        st.markdown("### Entregas conciliadas")
        cols = ["entrega_id", "cliente_nombre", "destino_nombre", "hora_llegada_reportada", "estatus_entrega", "estado_conciliacion_gps", "inicio_gps", "fin_gps", "tiempo_en_cliente_seg", "direccion_gps"]
        st.dataframe(view[[c for c in cols if c in view.columns]], use_container_width=True, hide_index=True)

    st.markdown("### Validación final")
    recommended = "Validada completa" if snapshot["entregas"] and snapshot["entregas_pendientes_gps"] == 0 and snapshot["entregas_fuera_horario"] == 0 else "Validada con incidencias"
    final_status = st.selectbox("Estado final", ["Validada completa", "Validada con incidencias", "Pendiente de corrección", "Pendiente de GPS", "Anulada"], index=["Validada completa", "Validada con incidencias", "Pendiente de corrección", "Pendiente de GPS", "Anulada"].index(recommended))
    comentario = st.text_area("Comentario de cierre obligatorio", value="")
    confirm = st.checkbox("Confirmo que revisé entregas, evidencias y GPS disponibles.")
    if st.button("Guardar cierre operativo"):
        if not comentario.strip():
            st.error("El comentario de cierre es obligatorio.")
        elif not confirm:
            st.error("Confirma la revisión antes de cerrar.")
        else:
            finalize_route(int(route_id), final_status, comentario.strip(), usuario=usuario)
            st.success("Cierre operativo guardado con auditoría.")

elif section == "Conciliación GPS":
    run_legacy_page("10_Conciliacion_GPS.py")

else:
    st.subheader("Administración avanzada")
    st.caption("Acceso a páginas técnicas heredadas para correcciones detalladas.")
    tab1, tab2 = st.tabs(["Rutas", "Entregas"])
    with tab1:
        run_legacy_page("07_Rutas.py")
    with tab2:
        run_legacy_page("08_Entregas_Ruta.py")
