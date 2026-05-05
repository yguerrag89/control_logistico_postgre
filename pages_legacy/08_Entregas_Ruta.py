from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.logistics_repository import (
    add_delivery_evidence,
    create_delivery,
    get_route,
    list_deliveries,
    list_evidences,
    list_routes,
    save_evidence_file,
    soft_delete_delivery,
    update_delivery,
    validate_delivery_time_against_route,
)
from modules.repository import list_units

st.title("📦 Entregas de ruta")

st.caption("El chofer captura la hora de llegada. La hora de salida y el tiempo en cliente se infieren después con GPS.")

ESTATUS = [
    "Entregado completo",
    "Entregado parcial",
    "No entregado",
    "Rechazado",
    "Cliente cerrado",
    "Reprogramado",
    "Entregado en paquetería",
    "Visita sin entrega",
    "Cancelado",
]
MOTIVOS = [
    "No aplica",
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
    "Problema de capacidad",
    "Problema mecánico",
    "Otro",
]
TIPOS_EVIDENCIA = ["Sello / acuse", "Producto entregado", "Cliente cerrado", "Producto rechazado", "Daño / incidencia", "Entrega en paquetería", "Otro"]

routes_df = list_routes({"fecha_desde": "2026-01-01"})
if routes_df.empty:
    st.warning("Primero crea una ruta en la página Rutas.")
    st.stop()

routes_df["label"] = routes_df.apply(
    lambda r: f"#{r['id']} | {r['fecha']} | {r['placas']} | {r['conductor_nombre']} | {r['estado_ruta']}",
    axis=1,
)
route_id = st.selectbox("Ruta", options=routes_df["id"].tolist(), format_func=lambda x: routes_df.loc[routes_df["id"] == x, "label"].iloc[0])
route = get_route(int(route_id))

if route is None:
    st.error("No se encontró la ruta seleccionada.")
    st.stop()

st.info(
    f"Ruta #{route['id']} | Fecha: {route['fecha']} | Unidad: {route['placas']} | "
    f"Chofer: {route['conductor_nombre']} | Estado: {route['estado_ruta']}"
)
st.caption(
    "Importante: la conciliación GPS usa la **hora de llegada de la entrega**, no la hora de salida de la ruta. "
    "Si el error fue la llegada al cliente, corrígela aquí en Entregas."
)

with st.expander("Capturar entrega / visita", expanded=True):
    with st.form("delivery_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            cliente = st.text_input("Cliente", value="")
        with c2:
            destino = st.text_input("Destino", value="")
        with c3:
            hora_llegada = st.text_input("Hora de llegada", value=datetime.now().strftime("%H:%M"))

        c4, c5 = st.columns(2)
        with c4:
            estatus = st.selectbox("Estatus de entrega", options=ESTATUS)
        with c5:
            motivo = st.selectbox("Motivo de no entrega", options=MOTIVOS, index=0)

        observaciones = st.text_area("Observaciones", value="")
        evidencia = st.file_uploader("Foto de evidencia", type=["png", "jpg", "jpeg"], key=f"evidencia_{route_id}")
        tipo_evidencia = st.selectbox("Tipo de evidencia", options=TIPOS_EVIDENCIA)
        comentario_evidencia = st.text_input("Comentario de evidencia", value="")
        save = st.form_submit_button("Guardar entrega")

    if save:
        errors = []
        if not cliente.strip():
            errors.append("El cliente es obligatorio.")
        if not destino.strip():
            errors.append("El destino es obligatorio.")
        if not hora_llegada.strip():
            errors.append("La hora de llegada es obligatoria.")
        if estatus != "Entregado completo" and motivo == "No aplica":
            errors.append("Si la entrega no fue completa, captura un motivo.")
        errors.extend(validate_delivery_time_against_route(route, hora_llegada.strip()))
        if errors:
            for e in errors:
                st.error(e)
        else:
            delivery_id = create_delivery({
                "ruta_id": route["id"],
                "cliente_nombre": cliente.strip(),
                "destino_nombre": destino.strip(),
                "hora_llegada_reportada": hora_llegada.strip(),
                "estatus_entrega": estatus,
                "motivo_no_entrega": None if motivo == "No aplica" else motivo,
                "observaciones": observaciones.strip(),
                "estado_conciliacion_gps": "Pendiente de GPS",
            })
            if evidencia is not None:
                path = save_evidence_file(evidencia, route, delivery_id)
                if path:
                    add_delivery_evidence(delivery_id, path, tipo_evidencia, comentario_evidencia.strip())
            st.success(f"Entrega guardada con ID #{delivery_id}.")

st.divider()
st.subheader("Entregas capturadas")

deliveries_df = list_deliveries(route_id=int(route_id))
if deliveries_df.empty:
    st.info("Aún no hay entregas capturadas en esta ruta.")
    st.stop()

show_cols = [
    "id", "orden_calculado", "cliente_nombre", "destino_nombre", "hora_llegada_reportada",
    "estatus_entrega", "motivo_no_entrega", "estado_conciliacion_gps",
    "hora_salida_inferida", "tiempo_en_cliente_seg", "direccion_gps"
]
for col in show_cols:
    if col not in deliveries_df.columns:
        deliveries_df[col] = None
st.dataframe(deliveries_df[show_cols], use_container_width=True, hide_index=True)

selected_delivery = st.selectbox("Ver detalle/evidencias de entrega", options=deliveries_df["id"].tolist())
record = deliveries_df[deliveries_df["id"] == selected_delivery].iloc[0].to_dict()

c1, c2 = st.columns([2, 1])
with c1:
    st.json({k: v for k, v in record.items() if k in show_cols + ["observaciones"]})
with c2:
    evidences = list_evidences(int(selected_delivery))
    if not evidences:
        st.info("Sin evidencias.")
    for ev in evidences:
        st.caption(f"{ev.get('tipo_evidencia') or 'Evidencia'} | {ev.get('fecha_captura')}")
        path = Path(ev["ruta_archivo"])
        if path.exists():
            st.image(str(path), use_container_width=True)
        else:
            st.warning("Archivo de evidencia no encontrado.")

with st.expander("Editar entrega / agregar evidencia", expanded=False):
    with st.form(f"edit_delivery_{selected_delivery}"):
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            edit_cliente = st.text_input("Cliente", value=record.get("cliente_nombre") or "")
        with ec2:
            edit_destino = st.text_input("Destino", value=record.get("destino_nombre") or "")
        with ec3:
            edit_hora = st.text_input("Hora de llegada", value=record.get("hora_llegada_reportada") or "")
        ec4, ec5 = st.columns(2)
        with ec4:
            edit_estatus = st.selectbox("Estatus", options=ESTATUS, index=ESTATUS.index(record.get("estatus_entrega")) if record.get("estatus_entrega") in ESTATUS else 0)
        with ec5:
            motivo_actual = record.get("motivo_no_entrega") or "No aplica"
            edit_motivo = st.selectbox("Motivo", options=MOTIVOS, index=MOTIVOS.index(motivo_actual) if motivo_actual in MOTIVOS else 0)
        edit_obs = st.text_area("Observaciones", value=record.get("observaciones") or "")
        nueva_evidencia = st.file_uploader("Agregar nueva evidencia", type=["png", "jpg", "jpeg"], key=f"new_ev_{selected_delivery}")
        edit_tipo_ev = st.selectbox("Tipo de nueva evidencia", options=TIPOS_EVIDENCIA, key=f"tipo_new_ev_{selected_delivery}")
        edit_com_ev = st.text_input("Comentario evidencia", key=f"com_new_ev_{selected_delivery}")
        motivo_corr = st.selectbox("Motivo de corrección", options=["Error de captura", "Dato faltante", "Corrección contra evidencia", "Corrección contra GPS", "Otro"], key=f"motivo_edit_delivery_{selected_delivery}")
        comentario_corr = st.text_area("Comentario de corrección (obligatorio)", key=f"comentario_edit_delivery_{selected_delivery}")
        save_edit = st.form_submit_button("Guardar corrección")

    if save_edit:
        errs = []
        if not edit_cliente.strip(): errs.append("El cliente es obligatorio.")
        if not edit_destino.strip(): errs.append("El destino es obligatorio.")
        if not edit_hora.strip(): errs.append("La hora de llegada es obligatoria.")
        if edit_estatus != "Entregado completo" and edit_motivo == "No aplica": errs.append("Si la entrega no fue completa, captura un motivo.")
        errs.extend(validate_delivery_time_against_route(route, edit_hora.strip()))
        if not comentario_corr.strip(): errs.append("El comentario de corrección es obligatorio.")
        if errs:
            for e in errs: st.error(e)
        else:
            update_delivery(int(selected_delivery), {
                "cliente_nombre": edit_cliente.strip(),
                "destino_nombre": edit_destino.strip(),
                "hora_llegada_reportada": edit_hora.strip(),
                "estatus_entrega": edit_estatus,
                "motivo_no_entrega": None if edit_motivo == "No aplica" else edit_motivo,
                "observaciones": edit_obs.strip(),
                "estado_conciliacion_gps": record.get("estado_conciliacion_gps") or "Pendiente de GPS",
            }, motivo=motivo_corr, comentario=comentario_corr.strip())
            if nueva_evidencia is not None:
                path = save_evidence_file(nueva_evidencia, route, int(selected_delivery))
                if path:
                    add_delivery_evidence(int(selected_delivery), path, edit_tipo_ev, edit_com_ev.strip())
            st.success("Entrega corregida. Si cambiaste la hora, la conciliación GPS quedó pendiente de recalcular.")

with st.expander("Dar de baja entrega", expanded=False):
    motivo_baja = st.selectbox("Motivo de baja", options=["Duplicado", "Captura equivocada", "Entrega cancelada", "Otro"], key=f"motivo_delete_delivery_{selected_delivery}")
    comentario_baja = st.text_area("Comentario de baja (obligatorio)", key=f"comentario_delete_delivery_{selected_delivery}")
    confirm = st.checkbox("Confirmo que esta entrega debe darse de baja.", key=f"confirm_delete_delivery_{selected_delivery}")
    if st.button("Dar de baja entrega", type="primary", key=f"delete_delivery_{selected_delivery}") and confirm:
        if not comentario_baja.strip():
            st.error("El comentario de baja es obligatorio.")
        else:
            soft_delete_delivery(int(selected_delivery), motivo=motivo_baja, comentario=comentario_baja.strip())
            st.success("Entrega dada de baja. Recarga la página para actualizar.")
