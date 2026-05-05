from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from modules.calculations import compute_efficiency, compute_totals, infer_cost_per_km, parse_optional_odometer
from modules.ocr import ocr_available, ocr_status, read_ticket
from modules.repository import (
    create_charge,
    find_possible_duplicate,
    get_last_charge_for_unit,
    list_conductors,
    list_units,
    save_ticket_image,
)
from modules.ui import render_alerts
from modules.validators import validate_charge_payload

st.title("🧾 Registrar carga")

FUEL_OPTIONS = ["Magna", "Premium", "Diésel", "Aceite", "Otro"]
CHARGE_TYPE_OPTIONS = ["Tanque lleno", "Parcial", "Emergencia", "Garrafón", "Aceite", "Aditivo", "Otro", "No especificada"]


def normalize_fuel_label(value: str | None, fallback: str = "Magna") -> str:
    if not value:
        return fallback
    raw = value.strip().lower()
    mapping = {
        "magna": "Magna",
        "premium": "Premium",
        "diesel": "Diésel",
        "diésel": "Diésel",
        "aceite": "Aceite",
        "otro": "Otro",
    }
    return mapping.get(raw, fallback)


units = list_units(active_only=True)
if not units:
    st.warning("No hay unidades activas.")
    st.stop()

unit_map = {f'{u["placas"]} | {u["marca"]} {u["modelo"]}'.strip(): u for u in units}
conductors = list_conductors(active_only=True)
conductor_map = {"Sin asignar": None} | {c["nombre"]: c["id"] for c in conductors}
known_plates = [u["placas"] for u in units]

with st.expander("Ver reglas de la unidad seleccionada", expanded=True):
    selected_label = st.selectbox("Unidad", options=list(unit_map.keys()), key="unidad_label")
    unit = unit_map[selected_label]
    st.write(
        {
            "Placas": unit["placas"],
            "Combustible preferido": unit["combustible_preferido"],
            "Límite litros": unit["limite_litros"],
            "Periodo hábil": unit["periodo_habil"],
            "Tipo de carga": unit["tipo_carga"],
        }
    )

uploaded_file = st.file_uploader("Foto del ticket", type=["png", "jpg", "jpeg"], key="ticket_upload")

ocr_result = None
saved_image_path = None
available, ocr_msg = ocr_status()

if uploaded_file is not None:
    st.image(uploaded_file, caption="Vista previa del ticket", use_container_width=True)
    if available:
        if st.button("Leer ticket con OCR"):
            saved_image_path = save_ticket_image(uploaded_file, unit["placas"])
            ocr_result = read_ticket(
                saved_image_path,
                known_plates=known_plates,
                selected_plate=unit["placas"],
                preferred_fuel=normalize_fuel_label(unit.get("combustible_preferido"), fallback="Magna"),
                unit_limit_liters=unit.get("limite_litros"),
            )
            st.session_state["last_saved_image_path"] = saved_image_path
            st.session_state["last_ocr_result"] = ocr_result
    else:
        st.info(ocr_msg)

ocr_result = st.session_state.get("last_ocr_result")
saved_image_path = st.session_state.get("last_saved_image_path")

if ocr_result:
    if ocr_result["ok"]:
        st.subheader("Sugerencia detectada")
        st.json(ocr_result["fields"])
        for w in ocr_result["warnings"]:
            st.warning(w)

        detected_plate = ocr_result["fields"].get("placas_detectadas")
        suggested_plate = ocr_result["fields"].get("placas_sugeridas")
        if suggested_plate and suggested_plate != detected_plate:
            st.info(f"La placa más parecida entre tus unidades es: {suggested_plate}")

        if detected_plate and detected_plate != unit["placas"]:
            st.warning(
                f"Las placas detectadas ({detected_plate}) no coinciden con la unidad elegida ({unit['placas']})."
            )

        with st.expander("Depuración OCR", expanded=False):
            st.write(
                {
                    "best_variant": ocr_result.get("best_variant"),
                    "best_config": ocr_result.get("best_config"),
                    "best_score": ocr_result.get("best_score"),
                }
            )
            st.text_area("Texto OCR seleccionado", value=ocr_result.get("raw_text", ""), height=220)
            for idx, candidate in enumerate((ocr_result.get("debug") or [])[:5], start=1):
                st.markdown(
                    f"**Intento {idx}:** variante `{candidate['variant']}` | config `{candidate['config']}` | score `{candidate['score']}`"
                )
                st.json(candidate["fields"])
                if candidate["warnings"]:
                    st.caption("; ".join(candidate["warnings"]))
    else:
        st.error(ocr_result["error"])

suggested = (ocr_result or {}).get("fields", {})

default_fecha = suggested.get("fecha_carga")
try:
    default_fecha = datetime.fromisoformat(default_fecha).date() if default_fecha else date.today()
except Exception:
    default_fecha = date.today()

st.divider()
st.subheader("Captura de la carga")

suggested_fuel = normalize_fuel_label(
    suggested.get("tipo_combustible"),
    fallback=normalize_fuel_label(unit.get("combustible_preferido"), fallback="Magna"),
)

with st.form("form_carga", clear_on_submit=False):
    c1, c2, c3 = st.columns(3)
    with c1:
        conductor_nombre = st.selectbox("Conductor", options=list(conductor_map.keys()))
    with c2:
        fecha_carga = st.date_input("Fecha de carga", value=default_fecha)
    with c3:
        hora_carga = st.text_input("Hora", value=suggested.get("hora_carga", datetime.now().strftime("%H:%M")))

    c4, c5, c6 = st.columns(3)
    with c4:
        gasolinera = st.text_input("Gasolinera", value=suggested.get("gasolinera", ""))
    with c5:
        ticket_folio = st.text_input("Ticket / Folio", value=suggested.get("ticket_folio", ""))
    with c6:
        tipo_combustible = st.selectbox(
            "Tipo de combustible",
            options=FUEL_OPTIONS,
            index=FUEL_OPTIONS.index(suggested_fuel) if suggested_fuel in FUEL_OPTIONS else 0,
        )

    c7, c8, c9, c10 = st.columns(4)
    with c7:
        litros = st.number_input("Litros", min_value=0.0, step=0.1, value=float(suggested.get("litros", 0.0) or 0.0))
    with c8:
        precio_litro = st.number_input(
            "Precio por litro", min_value=0.0, step=0.01, value=float(suggested.get("precio_litro", 0.0) or 0.0)
        )
    with c9:
        importe_sugerido = compute_totals(litros, precio_litro)
        importe_total = st.number_input(
            "Importe total",
            min_value=0.0,
            step=0.01,
            value=float(suggested.get("importe_total", importe_sugerido) or importe_sugerido),
        )
    with c10:
        kilometraje_txt = st.text_input("Kilometraje / odómetro actual (opcional)", value=str(suggested.get("kilometraje") or ""), help="Si no lo tienes, déjalo vacío. La app calculará rendimiento con GPS cuando esté disponible.")

    c11, c12, c13 = st.columns(3)
    with c11:
        metodo_pago = st.selectbox("Método de pago", options=["Efectivo", "Tarjeta", "Transferencia", "Vale", "Otro"])
    with c12:
        tipo_carga_combustible = st.selectbox("Tipo de carga combustible", options=CHARGE_TYPE_OPTIONS, index=0, help="Clave para no evaluar como rendimiento normal una carga parcial o de emergencia.")
    with c13:
        estacion_direccion = st.text_input("Dirección / estación", value="")

    observaciones = st.text_area("Observaciones", value="")
    submitted = st.form_submit_button("Guardar carga")

if submitted:
    if uploaded_file is not None and not saved_image_path:
        saved_image_path = save_ticket_image(uploaded_file, unit["placas"])

    payload = {
        "unidad_id": unit["id"],
        "conductor_id": conductor_map[conductor_nombre],
        "fecha_carga": str(fecha_carga),
        "hora_carga": hora_carga,
        "gasolinera": gasolinera.strip(),
        "estacion_direccion": estacion_direccion.strip(),
        "ticket_folio": ticket_folio.strip() or None,
        "tipo_combustible": tipo_combustible,
        "precio_litro": float(precio_litro),
        "litros": float(litros),
        "importe_total": float(importe_total),
        "kilometraje": parse_optional_odometer(kilometraje_txt),
        "metodo_pago": metodo_pago,
        "tipo_carga_combustible": tipo_carga_combustible,
        "observaciones": observaciones.strip(),
        "imagen_ticket_path": saved_image_path,
        "ocr_texto": (ocr_result or {}).get("raw_text"),
        "origen_registro": "ocr_asistido" if ocr_result and ocr_result.get("ok") else "manual",
        "estado_validacion": "PENDIENTE_VALIDACION" if ocr_result and ocr_result.get("ok") else "VALIDADO",
    }

    previous_charge = get_last_charge_for_unit(unit["id"])
    duplicate = find_possible_duplicate(
        unit["id"], payload["fecha_carga"], payload["litros"], payload["importe_total"], payload["ticket_folio"]
    )
    errors, warnings = validate_charge_payload(payload, unit, previous_charge, duplicate)

    eff = compute_efficiency(payload["kilometraje"], payload["litros"], previous_charge.get("kilometraje") if previous_charge else None)
    if eff["rendimiento_km_l"] is not None:
        cost_per_km = infer_cost_per_km(payload["importe_total"], eff["km_recorridos"])
        if eff["rendimiento_km_l"] < 2:
            warnings.append(f"Rendimiento muy bajo detectado: {eff['rendimiento_km_l']} km/L.")
        if eff["rendimiento_km_l"] > 25:
            warnings.append(f"Rendimiento muy alto detectado: {eff['rendimiento_km_l']} km/L.")
    else:
        cost_per_km = None

    payload["alerta_resumen"] = " | ".join(warnings) if warnings else None
    render_alerts(errors, warnings)

    if not errors:
        new_id = create_charge(payload)
        st.success(f"Carga guardada con ID #{new_id}.")
        if eff["km_recorridos"] is not None:
            st.info(
                {
                    "km_recorridos": eff["km_recorridos"],
                    "rendimiento_km_l": eff["rendimiento_km_l"],
                    "costo_por_km": cost_per_km,
                }
            )
