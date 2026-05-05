from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from modules.repository import get_charge, list_charges, set_validation_status
from modules.ui import validation_badge

st.title("🟠 Tickets por validar")

df = list_charges({"active_only": True})

if df.empty:
    st.info("No hay registros.")
    st.stop()

pending_mask = (
    df["estado_validacion"].fillna("").isin(["PENDIENTE_VALIDACION", "REVISAR"])
    | df["alerta_resumen"].fillna("").ne("")
    | df["origen_registro"].fillna("").eq("ocr_asistido")
)
pending = df[pending_mask].copy()

if pending.empty:
    st.success("No hay tickets pendientes por validar.")
    st.stop()

cols = [
    "id", "fecha_carga", "placas", "tipo_combustible", "litros", "importe_total",
    "kilometraje", "origen_registro", "estado_validacion", "alerta_resumen"
]
st.dataframe(pending[cols], use_container_width=True, hide_index=True)

selected_id = st.selectbox("Selecciona un registro", options=pending["id"].tolist())
record = get_charge(int(selected_id))

st.subheader(f"Registro #{selected_id} - {validation_badge(record.get('estado_validacion'))}")
c1, c2 = st.columns([2, 1])
with c1:
    st.json({k: v for k, v in record.items() if k != "ocr_texto"})
with c2:
    if record.get("imagen_ticket_path") and Path(record["imagen_ticket_path"]).exists():
        st.image(record["imagen_ticket_path"], caption="Ticket", use_container_width=True)
    else:
        st.info("Sin imagen del ticket.")

if record.get("ocr_texto"):
    with st.expander("Texto OCR detectado", expanded=False):
        st.text(record["ocr_texto"])

st.write("**Alertas:**", record.get("alerta_resumen") or "Sin alertas")

c3, c4 = st.columns(2)
with c3:
    if st.button("Marcar como VALIDADO"):
        set_validation_status(record["id"], "VALIDADO", "Validado desde bandeja de tickets")
        st.success("Registro marcado como VALIDADO.")
with c4:
    if st.button("Marcar como REVISAR"):
        set_validation_status(record["id"], "REVISAR", "Marcado para revisión manual")
        st.warning("Registro marcado como REVISAR.")
