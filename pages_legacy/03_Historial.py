from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.calculations import parse_optional_odometer

from modules.repository import (
    get_charge,
    get_last_charge_for_unit,
    get_unit,
    list_audit,
    list_charges,
    list_conductors,
    list_units,
    soft_delete_charge,
    update_charge,
)
from modules.validators import validate_charge_payload

CHARGE_TYPE_OPTIONS = ["Tanque lleno", "Parcial", "Emergencia", "Garrafón", "Aceite", "Aditivo", "Otro", "No especificada"]
from modules.repository import find_possible_duplicate, save_ticket_image

st.title("📚 Historial de cargas")

units = list_units(active_only=False)
unit_options = {0: "Todas"} | {u["id"]: u["placas"] for u in units}
conductors = list_conductors(active_only=False)
conductor_options = {0: "Todos"} | {c["id"]: c["nombre"] for c in conductors}

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
with c2:
    conductor_id = st.selectbox("Conductor", options=list(conductor_options.keys()), format_func=lambda x: conductor_options[x])
with c3:
    tipo_combustible = st.selectbox("Combustible", options=["Todos", "Magna", "Premium", "Diésel", "Aceite", "Otro"])
with c4:
    fecha_desde = st.date_input("Desde", value=date.today().replace(day=1))
with c5:
    fecha_hasta = st.date_input("Hasta", value=date.today())

df = list_charges({
    "unidad_id": None if unidad_id == 0 else unidad_id,
    "conductor_id": None if conductor_id == 0 else conductor_id,
    "tipo_combustible": None if tipo_combustible == "Todos" else tipo_combustible,
    "fecha_desde": str(fecha_desde),
    "fecha_hasta": str(fecha_hasta),
    "active_only": False,
})

if df.empty:
    st.info("No hay registros con esos filtros.")
else:
    visible_cols = [
        "id", "fecha_carga", "hora_carga", "placas", "conductor_nombre", "tipo_combustible",
        "litros", "precio_litro", "importe_total", "kilometraje", "estado_validacion", "activo"
    ]
    st.dataframe(df[visible_cols], use_container_width=True, hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Exportar CSV", data=csv, file_name="historial_combustible.csv", mime="text/csv")
    with col_b:
        excel_path = Path("data/exports/historial_combustible.xlsx")
        with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Historial")
        st.download_button(
            "Exportar Excel",
            data=excel_path.read_bytes(),
            file_name="historial_combustible.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    selected_id = st.selectbox("Selecciona un registro para editar o dar de baja", options=df["id"].tolist())
    record = get_charge(int(selected_id))

    st.divider()
    st.subheader(f"Detalle del registro #{selected_id}")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.json({k: v for k, v in record.items() if k not in ["ocr_texto"]})
    with c2:
        if record.get("imagen_ticket_path") and Path(record["imagen_ticket_path"]).exists():
            st.image(record["imagen_ticket_path"], caption="Ticket", use_container_width=True)
        else:
            st.info("Sin imagen.")

    with st.expander("Editar registro", expanded=False):
        unit = next(u for u in units if u["id"] == record["unidad_id"])
        conductor_map = {"Sin asignar": None} | {c["nombre"]: c["id"] for c in conductors}
        name_by_id = {v: k for k, v in conductor_map.items()}
        uploaded_replace = st.file_uploader("Reemplazar imagen del ticket", type=["png", "jpg", "jpeg"], key=f"replace_{selected_id}")

        with st.form(f"edit_{selected_id}"):
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                fecha_carga = st.date_input("Fecha", value=pd.to_datetime(record["fecha_carga"]).date(), key=f"f_{selected_id}")
            with cc2:
                hora_carga = st.text_input("Hora", value=record.get("hora_carga") or "", key=f"h_{selected_id}")
            with cc3:
                conductor_nombre = st.selectbox("Conductor", options=list(conductor_map.keys()), index=list(conductor_map.keys()).index(name_by_id.get(record.get("conductor_id"), "Sin asignar")), key=f"c_{selected_id}")

            cc4, cc5, cc6 = st.columns(3)
            with cc4:
                gasolinera = st.text_input("Gasolinera", value=record.get("gasolinera") or "", key=f"g_{selected_id}")
            with cc5:
                ticket_folio = st.text_input("Ticket / Folio", value=record.get("ticket_folio") or "", key=f"t_{selected_id}")
            with cc6:
                tipo_combustible_edit = st.selectbox("Combustible", options=["Magna", "Premium", "Diésel", "Aceite", "Otro"], index=["Magna", "Premium", "Diésel", "Aceite", "Otro"].index(record.get("tipo_combustible") or "Magna"), key=f"tc_{selected_id}")

            cc7, cc8, cc9, cc10 = st.columns(4)
            with cc7:
                litros = st.number_input("Litros", min_value=0.0, value=float(record["litros"]), step=0.1, key=f"l_{selected_id}")
            with cc8:
                precio_litro = st.number_input("Precio/L", min_value=0.0, value=float(record["precio_litro"]), step=0.01, key=f"p_{selected_id}")
            with cc9:
                importe_total = st.number_input("Importe", min_value=0.0, value=float(record["importe_total"]), step=0.01, key=f"i_{selected_id}")
            with cc10:
                kilometraje_txt = st.text_input("Kilometraje / odómetro actual (opcional)", value=str(record.get("kilometraje") or ""), key=f"k_{selected_id}")

            mc1, mc2 = st.columns(2)
            with mc1:
                metodo_pago = st.selectbox("Método de pago", options=["Efectivo", "Tarjeta", "Transferencia", "Vale", "Otro"], index=["Efectivo", "Tarjeta", "Transferencia", "Vale", "Otro"].index(record.get("metodo_pago") or "Efectivo"), key=f"mp_{selected_id}")
            with mc2:
                current_tipo_carga = record.get("tipo_carga_combustible") or "No especificada"
                tipo_carga_combustible = st.selectbox("Tipo de carga combustible", options=CHARGE_TYPE_OPTIONS, index=CHARGE_TYPE_OPTIONS.index(current_tipo_carga) if current_tipo_carga in CHARGE_TYPE_OPTIONS else CHARGE_TYPE_OPTIONS.index("No especificada"), key=f"tcc_{selected_id}")
            observaciones = st.text_area("Observaciones", value=record.get("observaciones") or "", key=f"o_{selected_id}")
            estado_validacion = st.selectbox("Estado de validación", options=["VALIDADO", "PENDIENTE_VALIDACION", "REVISAR"], index=["VALIDADO", "PENDIENTE_VALIDACION", "REVISAR"].index(record.get("estado_validacion") or "VALIDADO"), key=f"ev_{selected_id}")

            motivo_correccion = st.selectbox("Motivo de corrección", options=["Error de captura", "Dato faltante", "Corrección contra ticket", "Corrección contra GPS", "Duplicado", "Ticket equivocado", "Otro"], key=f"motivo_{selected_id}")
            comentario_correccion = st.text_area("Comentario de corrección (obligatorio)", value="", key=f"comentario_{selected_id}")

            save_changes = st.form_submit_button("Guardar cambios")

        if save_changes:
            if not comentario_correccion.strip():
                st.error("Para guardar cambios debes capturar un comentario de corrección.")
                st.stop()
            new_image_path = record.get("imagen_ticket_path")
            if uploaded_replace is not None:
                new_image_path = save_ticket_image(uploaded_replace, record["placas"])

            payload = {
                "unidad_id": record["unidad_id"],
                "conductor_id": conductor_map[conductor_nombre],
                "fecha_carga": str(fecha_carga),
                "hora_carga": hora_carga,
                "gasolinera": gasolinera,
                "estacion_direccion": record.get("estacion_direccion"),
                "ticket_folio": ticket_folio or None,
                "tipo_combustible": tipo_combustible_edit,
                "precio_litro": float(precio_litro),
                "litros": float(litros),
                "importe_total": float(importe_total),
                "kilometraje": parse_optional_odometer(kilometraje_txt),
                "metodo_pago": metodo_pago,
                "tipo_carga_combustible": tipo_carga_combustible,
                "observaciones": observaciones,
                "imagen_ticket_path": new_image_path,
                "ocr_texto": record.get("ocr_texto"),
                "origen_registro": record.get("origen_registro") or "manual",
                "estado_validacion": estado_validacion,
                "alerta_resumen": record.get("alerta_resumen"),
            }
            prev = get_last_charge_for_unit(record["unidad_id"], exclude_charge_id=record["id"])
            duplicate = find_possible_duplicate(record["unidad_id"], payload["fecha_carga"], payload["litros"], payload["importe_total"], payload["ticket_folio"], exclude_charge_id=record["id"])
            errors, warnings = validate_charge_payload(payload, unit, prev, duplicate)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                payload["alerta_resumen"] = " | ".join(warnings) if warnings else None
                update_charge(record["id"], payload, motivo=motivo_correccion, comentario=comentario_correccion.strip())
                st.success("Registro actualizado.")
                for w in warnings:
                    st.warning(w)

    with st.expander("Dar de baja lógica", expanded=False):
        motivo_baja = st.selectbox("Motivo de baja", options=["Duplicado", "Ticket equivocado", "Carga capturada por error", "Otro"], key=f"motivo_baja_{selected_id}")
        comentario_baja = st.text_area("Comentario de baja (obligatorio)", key=f"comentario_baja_{selected_id}")
        confirm = st.checkbox("Confirmo que este registro debe excluirse del control operativo.", key=f"del_{selected_id}")
        if st.button("Dar de baja", type="primary", key=f"btn_del_{selected_id}") and confirm:
            if not comentario_baja.strip():
                st.error("El comentario de baja es obligatorio.")
            else:
                soft_delete_charge(int(selected_id), motivo=motivo_baja, comentario=comentario_baja.strip())
                st.success("Registro dado de baja lógica. Recarga la página para actualizar la tabla.")

st.divider()
st.subheader("Auditoría reciente")
audit_df = list_audit(limit=50)
if audit_df.empty:
    st.info("Sin auditoría todavía.")
else:
    st.dataframe(audit_df, use_container_width=True, hide_index=True)
