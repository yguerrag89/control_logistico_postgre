from __future__ import annotations

from datetime import datetime

import streamlit as st

from modules.time_utils import now_mx, today_mx, hhmm, time_no_seconds
from modules.business_analytics import default_date_range
from modules.calculations import compute_efficiency, compute_totals, infer_cost_per_km, parse_optional_odometer
from modules.logistics_costs import create_operational_cost, list_operational_costs, cost_summary
from modules.navigation import run_legacy_page
from modules.repository import (
    create_charge,
    find_possible_duplicate,
    get_last_charge_for_unit,
    list_units,
    save_ticket_image,
)
from modules.session import sidebar_user_context
from modules.validators import validate_charge_payload

FUEL_OPTIONS = ["Magna", "Premium", "Diésel", "Aceite", "Otro"]
CHARGE_TYPE_OPTIONS = ["Tanque lleno", "Parcial", "Emergencia", "Garrafón", "Aceite", "Aditivo", "Otro", "No especificada"]


def _normalize_fuel_label(value: str | None, fallback: str = "Magna") -> str:
    if not value:
        return fallback
    raw = str(value).strip().lower()
    mapping = {
        "magna": "Magna",
        "premium": "Premium",
        "diesel": "Diésel",
        "diésel": "Diésel",
        "aceite": "Aceite",
        "otro": "Otro",
    }
    return mapping.get(raw, fallback)


def _render_driver_fuel_capture(ctx: dict) -> None:
    """Mobile-first fuel capture for driver users.

    Driver users can only create their own fuel records. They cannot see global
    fuel history, corrections, costs, GPS performance or tickets from other
    users. Records are persisted in cargas_combustible with conductor_id linked
    to the authenticated driver's conductor_id and with full audit/attachment
    traceability through create_charge().
    """
    usuario = ctx["usuario"]
    conductor_id = ctx.get("conductor_id")
    conductor_nombre = ctx.get("conductor_nombre") or usuario

    st.title("⛽ Registrar combustible")
    st.caption("Modo chofer: captura rápida desde celular. Solo puedes registrar cargas asociadas a tu usuario.")

    if not conductor_id:
        st.error("Tu usuario de chofer no está vinculado a un conductor activo. Pide al administrador revisar Catálogos > Usuarios.")
        st.stop()

    units = list_units(active_only=True)
    if not units:
        st.warning("No hay unidades activas para registrar combustible.")
        st.stop()

    unit_map = {int(u["id"]): u for u in units}
    unit_labels = {int(u["id"]): f"{u['placas']} | {u.get('marca') or ''} {u.get('modelo') or ''}".strip() for u in units}

    st.info(f"Chofer: **{conductor_nombre}**")

    with st.form("driver_fuel_form", clear_on_submit=False, border=True):
        unidad_id = st.selectbox("Unidad", options=list(unit_labels.keys()), format_func=lambda x: unit_labels[x])
        unit = unit_map[int(unidad_id)]

        fuel_time_key = f"driver_fuel_manual_time_{conductor_id}"
        if fuel_time_key not in st.session_state:
            st.session_state[fuel_time_key] = time_no_seconds(now_mx())

        fecha_carga = st.date_input("Fecha de carga", value=today_mx())
        usar_hora_actual_carga = st.checkbox(
            "Usar hora actual al guardar carga",
            value=True,
            help="Recomendado. La hora se toma cuando presionas Guardar carga; no se actualiza sola mientras llenas el formulario.",
        )
        hora_carga_manual = st.time_input(
            "Hora manual de carga, solo si desactivas la opción anterior",
            key=fuel_time_key,
        )
        foto_ticket = st.file_uploader("Foto del ticket", type=["png", "jpg", "jpeg"], accept_multiple_files=False)

        gasolinera = st.text_input("Gasolinera / estación", placeholder="Ej. G500, Pemex, Mobil...")
        estacion_direccion = st.text_input("Dirección / referencia", placeholder="Opcional")
        ticket_folio = st.text_input("Ticket / folio", placeholder="Muy recomendado")

        suggested_fuel = _normalize_fuel_label(unit.get("combustible_preferido"), fallback="Magna")
        tipo_combustible = st.selectbox(
            "Tipo de combustible",
            options=FUEL_OPTIONS,
            index=FUEL_OPTIONS.index(suggested_fuel) if suggested_fuel in FUEL_OPTIONS else 0,
        )
        tipo_carga_combustible = st.selectbox(
            "Tipo de carga",
            options=CHARGE_TYPE_OPTIONS,
            index=0,
            help="Marca Parcial/Emergencia/Garrafón cuando no sea una carga normal. Eso evita contaminar el cálculo de rendimiento.",
        )

        litros = st.number_input("Litros", min_value=0.0, step=0.1, format="%.3f")
        precio_litro = st.number_input("Precio por litro", min_value=0.0, step=0.01, format="%.2f")
        importe_sugerido = compute_totals(litros, precio_litro)
        importe_total = st.number_input("Importe total", min_value=0.0, step=0.01, value=float(importe_sugerido), format="%.2f")
        kilometraje_txt = st.text_input(
            "Kilometraje / odómetro (opcional)",
            help="Si no lo tienes, déjalo vacío. La app calculará rendimiento con GPS cuando haya datos importados.",
        )
        metodo_pago = st.selectbox("Método de pago", options=["Empresa", "Tarjeta", "Efectivo", "Vale", "Transferencia", "Otro"])
        observaciones = st.text_area("Observaciones", placeholder="Opcional")

        submitted = st.form_submit_button("💾 Guardar carga", type="primary", use_container_width=True)

    if not submitted:
        st.markdown(
            """
**Regla para choferes**
- Captura la carga el mismo día.
- Sube foto del ticket cuando la tengas.
- Si fue una carga pequeña o de emergencia, marca `Parcial` o `Emergencia`.
"""
        )
        return

    unit = unit_map[int(unidad_id)]
    saved_image_path = save_ticket_image(foto_ticket, unit["placas"]) if foto_ticket is not None else None

    payload = {
        "unidad_id": int(unidad_id),
        "conductor_id": int(conductor_id),
        "fecha_carga": str(fecha_carga),
        "hora_carga": hhmm(now_mx()) if usar_hora_actual_carga else hora_carga_manual.strftime("%H:%M"),
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
        "ocr_texto": None,
        "origen_registro": "chofer_movil",
        "estado_validacion": "PENDIENTE_VALIDACION",
    }

    previous_charge = get_last_charge_for_unit(int(unidad_id))
    duplicate = find_possible_duplicate(
        int(unidad_id), payload["fecha_carga"], payload["litros"], payload["importe_total"], payload["ticket_folio"]
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

    if errors:
        for e in errors:
            st.error(e)
        if saved_image_path:
            st.warning("La imagen del ticket se guardó, pero la carga no se registró porque faltan datos obligatorios. Vuelve a guardar cuando corrijas los datos.")
        return

    for w in warnings:
        st.warning(w)

    charge_id = create_charge(
        payload,
        motivo="Alta de combustible por chofer",
        comentario="Carga registrada desde modo chofer móvil",
        usuario=usuario,
    )
    st.success(f"Carga #{charge_id} guardada correctamente en la base de datos.")
    st.info("Quedó pendiente de validación administrativa. El administrador podrá revisar folio, ticket, rendimiento y calidad del dato.")
    if eff["km_recorridos"] is not None:
        st.write({
            "km_recorridos_por_odometro": eff["km_recorridos"],
            "rendimiento_km_l": eff["rendimiento_km_l"],
            "costo_por_km": cost_per_km,
        })


ctx = sidebar_user_context()
usuario = ctx["usuario"]
rol = ctx["rol"]

if rol == "Chofer":
    _render_driver_fuel_capture(ctx)
    st.stop()

st.title("⛽ Combustible y costos")
st.caption("Captura, corrección, historial, tickets, rendimiento GPS y costos operativos asociados a logística.")

section = st.radio(
    "Sección",
    ["Registrar carga", "Historial y correcciones", "Rendimiento GPS", "Tickets por validar", "Costos adicionales"],
    horizontal=True,
)

st.divider()
if section == "Registrar carga":
    run_legacy_page("02_Registrar_Carga.py")
elif section == "Historial y correcciones":
    run_legacy_page("03_Historial.py")
elif section == "Rendimiento GPS":
    run_legacy_page("12_Rendimiento_GPS.py")
elif section == "Tickets por validar":
    run_legacy_page("06_Tickets_por_Validar.py")
else:
    st.subheader("Costos adicionales de operación")
    st.caption("Registra casetas, maniobras, estacionamientos, viáticos, mantenimiento u otros costos para acercarte al costo real por ruta/unidad.")
    units = list_units(active_only=True)
    unit_options = {0: "Sin unidad específica"} | {u["id"]: u["placas"] for u in units}
    from modules.logistics_repository import list_routes
    routes = list_routes({"fecha_desde": "2026-01-01"})
    route_options = {0: "Sin ruta específica"}
    if not routes.empty:
        route_options |= {int(r["id"]): f"#{int(r['id'])} | {r['fecha']} | {r['placas']} | {r['conductor_nombre']}" for _, r in routes.iterrows()}

    with st.form("operational_cost_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            fecha = st.date_input("Fecha", value=datetime.now().date())
        with c2:
            unidad_id = st.selectbox("Unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x])
        with c3:
            ruta_id = st.selectbox("Ruta relacionada", options=list(route_options.keys()), format_func=lambda x: route_options[x])
        c4, c5, c6 = st.columns(3)
        with c4:
            tipo = st.selectbox("Tipo de gasto", ["Caseta", "Maniobra", "Estacionamiento", "Viático", "Mantenimiento", "Refacción", "Multa", "Lavado", "Otro"])
        with c5:
            importe = st.number_input("Importe", min_value=0.0, step=10.0, format="%.2f")
        with c6:
            metodo = st.selectbox("Método de pago", ["Empresa", "Tarjeta", "Efectivo", "Vale", "Otro"])
        proveedor = st.text_input("Proveedor / lugar", value="")
        folio = st.text_input("Folio / referencia", value="")
        descripcion = st.text_area("Descripción / observaciones", value="")
        save = st.form_submit_button("Guardar costo")
    if save:
        if importe <= 0:
            st.error("El importe debe ser mayor a 0.")
        else:
            cost_id = create_operational_cost({
                "fecha": str(fecha),
                "unidad_id": None if unidad_id == 0 else int(unidad_id),
                "ruta_id": None if ruta_id == 0 else int(ruta_id),
                "tipo_gasto": tipo,
                "proveedor": proveedor.strip() or None,
                "folio": folio.strip() or None,
                "importe": float(importe),
                "metodo_pago": metodo,
                "descripcion": descripcion.strip() or None,
                "estado_validacion": "PENDIENTE_VALIDACION",
            }, motivo="Alta de costo operativo", comentario=descripcion.strip(), usuario=usuario)
            st.success(f"Costo #{cost_id} registrado.")

    st.divider()
    start_default, end_default = default_date_range(days_back=90)
    f1, f2, f3 = st.columns(3)
    with f1:
        desde = st.date_input("Desde", value=start_default, key="cost_desde")
    with f2:
        hasta = st.date_input("Hasta", value=end_default, key="cost_hasta")
    with f3:
        unit_filter = st.selectbox("Filtrar unidad", options=list(unit_options.keys()), format_func=lambda x: unit_options[x], key="cost_unit_filter")
    filters = {"fecha_desde": str(desde), "fecha_hasta": str(hasta), "unidad_id": None if unit_filter == 0 else int(unit_filter)}
    summary = cost_summary(filters)
    c1, c2 = st.columns(2)
    c1.metric("Registros de costo", summary["gastos"])
    c2.metric("Importe total", f"${summary['importe_total']:,.2f}")
    df = list_operational_costs(filters)
    if df.empty:
        st.info("No hay costos adicionales en el rango.")
    else:
        cols = ["fecha", "placas", "tipo_gasto", "proveedor", "folio", "importe", "metodo_pago", "estado_validacion", "descripcion"]
        st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True, hide_index=True)
