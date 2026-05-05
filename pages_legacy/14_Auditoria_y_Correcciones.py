from __future__ import annotations

from datetime import date

import streamlit as st

from modules.audit import list_audit_changes
from modules.repository import list_audit

st.title("🧾 Auditoría y correcciones")
st.caption("Aquí puedes revisar quién cambió qué, cuándo, el valor anterior, el nuevo y el motivo documentado.")

c1, c2, c3, c4 = st.columns(4)
with c1:
    tabla = st.selectbox("Tabla", options=["Todas", "cargas_combustible", "conductores", "unidades", "rutas", "ruta_entregas", "destinos", "gps_importaciones"])
with c2:
    accion = st.selectbox("Acción", options=["Todas", "INSERT", "UPDATE", "SOFT_DELETE", "MERGE", "ANULAR", "INVALIDATE_GPS", "STATUS", "VALIDATION"])
with c3:
    fecha_desde = st.date_input("Desde", value=date(2026, 1, 1))
with c4:
    fecha_hasta = st.date_input("Hasta", value=date.today())

registro_txt = st.text_input("ID de registro específico (opcional)", value="")
registro_id = int(registro_txt) if registro_txt.strip().isdigit() else None

changes_df = list_audit_changes({
    "tabla": tabla,
    "accion": accion,
    "fecha_desde": str(fecha_desde),
    "fecha_hasta": str(fecha_hasta),
    "registro_id": registro_id,
}, limit=1000)

st.subheader("Auditoría detallada campo por campo")
if changes_df.empty:
    st.info("No hay cambios detallados con esos filtros.")
else:
    cols = ["creado_en", "tabla", "registro_id", "accion", "campo", "valor_anterior", "valor_nuevo", "motivo", "comentario", "usuario"]
    for col in cols:
        if col not in changes_df.columns:
            changes_df[col] = None
    st.dataframe(changes_df[cols], use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar auditoría detallada CSV",
        data=changes_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="auditoria_cambios.csv",
        mime="text/csv",
        key="download_auditoria_detallada",
    )

st.divider()
st.subheader("Bitácora simple reciente")
events_df = list_audit(limit=300)
if events_df.empty:
    st.info("Sin eventos recientes.")
else:
    st.dataframe(events_df, use_container_width=True, hide_index=True)
