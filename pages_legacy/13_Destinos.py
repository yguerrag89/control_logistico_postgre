from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.business_analytics import default_date_range, destination_candidates_from_gps
from modules.logistics_repository import (
    destination_candidates_from_deliveries,
    list_destinations,
    merge_destinations,
    upsert_destination,
)

st.title("📍 Catálogo de destinos")
st.caption("Construye y corrige el catálogo progresivamente desde captura manual, entregas y paradas GPS.")

TIPOS = ["Cliente", "Paquetería", "CEDIS", "Almacén", "Gasolinera", "Taller", "Otro"]
MOTIVOS = ["Alta manual", "Error de captura", "Dato faltante", "Validación de dirección", "Duplicado", "Otro"]


def _form_payload(nombre, alias, tipo, cliente, direccion, latitud, longitud, validado, fuente, observaciones, activo=1, record_id=None):
    return {
        "id": record_id,
        "nombre_normalizado": nombre.strip(),
        "alias": alias.strip(),
        "tipo_destino": tipo,
        "cliente_asociado": cliente.strip(),
        "direccion_texto": direccion.strip(),
        "latitud": None if latitud == 0 else float(latitud),
        "longitud": None if longitud == 0 else float(longitud),
        "validado": 1 if validado else 0,
        "fuente": fuente,
        "observaciones": observaciones.strip(),
        "activo": 1 if activo else 0,
    }


with st.expander("Crear destino manualmente", expanded=True):
    with st.form("destination_create_form"):
        c1, c2, c3 = st.columns(3)
        with c1: nombre = st.text_input("Nombre normalizado", value="")
        with c2: tipo = st.selectbox("Tipo destino", options=TIPOS)
        with c3: cliente = st.text_input("Cliente asociado", value="")
        alias = st.text_area("Alias / formas como lo escriben", value="")
        direccion = st.text_area("Dirección", value="")
        c4, c5, c6 = st.columns(3)
        with c4: latitud = st.number_input("Latitud", value=0.0, format="%.8f")
        with c5: longitud = st.number_input("Longitud", value=0.0, format="%.8f")
        with c6: validado = st.checkbox("Validado", value=False)
        observaciones = st.text_area("Observaciones", value="")
        motivo = st.selectbox("Motivo", options=MOTIVOS, index=0)
        comentario = st.text_area("Comentario", value="")
        save = st.form_submit_button("Guardar destino")

    if save:
        if not nombre.strip():
            st.error("El nombre normalizado es obligatorio.")
        else:
            upsert_destination(_form_payload(nombre, alias, tipo, cliente, direccion, latitud, longitud, validado, "captura_manual", observaciones), motivo=motivo, comentario=comentario.strip())
            st.success("Destino guardado.")

st.divider()
st.subheader("Destinos registrados")
destinos_df = list_destinations(active_only=False)
if destinos_df.empty:
    st.info("Aún no hay destinos registrados.")
else:
    st.dataframe(destinos_df, use_container_width=True, hide_index=True)

    with st.expander("Editar / validar / desactivar destino", expanded=False):
        selected_id = st.selectbox("Destino", options=destinos_df["id"].tolist(), format_func=lambda x: f"#{x} | {destinos_df.loc[destinos_df['id']==x, 'nombre_normalizado'].iloc[0]}")
        rec = destinos_df[destinos_df["id"] == selected_id].iloc[0].to_dict()
        with st.form(f"edit_destination_{selected_id}"):
            e1, e2, e3 = st.columns(3)
            with e1: enombre = st.text_input("Nombre normalizado", value=rec.get("nombre_normalizado") or "")
            with e2: etipo = st.selectbox("Tipo", options=TIPOS, index=TIPOS.index(rec.get("tipo_destino")) if rec.get("tipo_destino") in TIPOS else 0)
            with e3: ecliente = st.text_input("Cliente asociado", value=rec.get("cliente_asociado") or "")
            ealias = st.text_area("Alias", value=rec.get("alias") or "")
            edireccion = st.text_area("Dirección", value=rec.get("direccion_texto") or "")
            e4, e5, e6 = st.columns(3)
            with e4: elat = st.number_input("Latitud", value=float(rec.get("latitud") or 0.0), format="%.8f", key=f"elat_{selected_id}")
            with e5: elon = st.number_input("Longitud", value=float(rec.get("longitud") or 0.0), format="%.8f", key=f"elon_{selected_id}")
            with e6:
                evalidado = st.checkbox("Validado", value=bool(rec.get("validado")))
                eactivo = st.checkbox("Activo", value=bool(rec.get("activo")))
            eobs = st.text_area("Observaciones", value=rec.get("observaciones") or "")
            emotivo = st.selectbox("Motivo de corrección", options=MOTIVOS, index=1)
            ecomentario = st.text_area("Comentario de corrección (obligatorio)")
            esave = st.form_submit_button("Guardar corrección")
        if esave:
            if not enombre.strip():
                st.error("El nombre es obligatorio.")
            elif not ecomentario.strip():
                st.error("El comentario es obligatorio para editar un destino.")
            else:
                upsert_destination(_form_payload(enombre, ealias, etipo, ecliente, edireccion, elat, elon, evalidado, rec.get("fuente") or "captura_manual", eobs, eactivo, selected_id), motivo=emotivo, comentario=ecomentario.strip())
                st.success("Destino actualizado.")

    with st.expander("Fusionar destinos duplicados", expanded=False):
        if len(destinos_df) < 2:
            st.info("Necesitas al menos dos destinos.")
        else:
            options = {int(r["id"]): f"#{int(r['id'])} | {r['nombre_normalizado']}" for _, r in destinos_df.iterrows()}
            source_id = st.selectbox("Destino duplicado que se desactivará", options=list(options.keys()), format_func=lambda x: options[x], key="dest_source")
            target_id = st.selectbox("Destino correcto que se conservará", options=list(options.keys()), format_func=lambda x: options[x], key="dest_target")
            motivo_merge = st.selectbox("Motivo", options=["Duplicado", "Error de captura", "Otro"], key="dest_merge_reason")
            comentario_merge = st.text_area("Comentario obligatorio", key="dest_merge_comment")
            confirm = st.checkbox("Confirmo que quiero fusionar conceptualmente estos destinos.", key="dest_merge_confirm")
            if st.button("Fusionar destinos", type="primary"):
                if source_id == target_id:
                    st.error("Selecciona dos destinos diferentes.")
                elif not comentario_merge.strip():
                    st.error("El comentario es obligatorio.")
                elif not confirm:
                    st.error("Debes confirmar la fusión.")
                else:
                    merge_destinations(int(source_id), int(target_id), motivo_merge, comentario_merge.strip())
                    st.success("Destino duplicado desactivado y fusión auditada.")

st.divider()
st.subheader("Candidatos para construir catálogo")
tab_entregas, tab_gps = st.tabs(["Desde entregas capturadas", "Desde paradas GPS"])

with tab_entregas:
    candidates = destination_candidates_from_deliveries()
    if candidates.empty:
        st.info("No hay candidatos pendientes desde entregas.")
    else:
        st.dataframe(candidates, use_container_width=True, hide_index=True)
        selected_idx = st.selectbox("Crear destino desde candidato", options=list(candidates.index), format_func=lambda i: f"{candidates.loc[i, 'destino_nombre']} | {candidates.loc[i, 'cliente_nombre']} | {candidates.loc[i, 'veces']} veces")
        row = candidates.loc[selected_idx]
        if st.button("Crear destino desde este candidato"):
            upsert_destination({
                "nombre_normalizado": str(row["destino_nombre"]).strip(),
                "alias": "",
                "tipo_destino": "Cliente",
                "cliente_asociado": str(row["cliente_nombre"]).strip(),
                "direccion_texto": "",
                "validado": 0,
                "fuente": "entrega_capturada",
                "observaciones": f"Creado desde candidato. Veces detectado: {row['veces']}",
                "activo": 1,
            }, motivo="Alta desde candidato", comentario="Creado desde entregas capturadas")
            st.success("Destino candidato creado. Puedes editarlo después para validar dirección/alias.")

with tab_gps:
    st.caption("Agrupa direcciones GPS donde hubo paradas relevantes. Útil antes de tener rutas capturadas.")
    start_default, end_default = default_date_range(days_back=120)
    g1, g2, g3 = st.columns(3)
    with g1:
        gps_desde = st.date_input("GPS desde", value=start_default, key="dest_gps_desde")
    with g2:
        gps_hasta = st.date_input("GPS hasta", value=end_default, key="dest_gps_hasta")
    with g3:
        min_minutes = st.number_input("Mín. minutos detenido", min_value=5, max_value=240, value=15, step=5)

    gps_candidates = destination_candidates_from_gps({"fecha_desde": str(gps_desde), "fecha_hasta": str(gps_hasta)}, min_minutes=float(min_minutes), limit=200)
    if gps_candidates.empty:
        st.info("No hay candidatos GPS con esos filtros.")
    else:
        st.dataframe(gps_candidates, use_container_width=True, hide_index=True)
        idx = st.selectbox(
            "Crear destino desde dirección GPS",
            options=list(gps_candidates.index),
            format_func=lambda i: f"{gps_candidates.loc[i, 'direccion_gps']} | {gps_candidates.loc[i, 'veces']} paradas | {gps_candidates.loc[i, 'duracion_total_min']} min",
            key="gps_candidate_idx",
        )
        row = gps_candidates.loc[idx]
        with st.form("create_from_gps_candidate"):
            nombre_gps = st.text_input("Nombre normalizado", value=str(row["direccion_gps"])[:120])
            tipo_gps = st.selectbox("Tipo destino", options=TIPOS, index=0, key="gps_tipo_destino")
            cliente_gps = st.text_input("Cliente asociado", value="")
            alias_gps = st.text_area("Alias", value="")
            obs_gps = st.text_area("Observaciones", value=f"Creado desde GPS. Unidades: {row.get('unidades','')}. Veces: {row['veces']}. Duración total min: {row['duracion_total_min']}")
            save_gps_dest = st.form_submit_button("Crear destino desde GPS")
        if save_gps_dest:
            if not nombre_gps.strip():
                st.error("El nombre normalizado es obligatorio.")
            else:
                upsert_destination({
                    "nombre_normalizado": nombre_gps.strip(),
                    "alias": alias_gps.strip(),
                    "tipo_destino": tipo_gps,
                    "cliente_asociado": cliente_gps.strip(),
                    "direccion_texto": str(row["direccion_gps"]).strip(),
                    "validado": 0,
                    "fuente": "gps_parada",
                    "observaciones": obs_gps.strip(),
                    "activo": 1,
                }, motivo="Alta desde GPS", comentario="Creado desde paradas GPS frecuentes")
                st.success("Destino creado desde GPS. Valídalo después con nombre/cliente/dirección correcta.")
