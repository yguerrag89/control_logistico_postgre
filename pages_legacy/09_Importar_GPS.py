from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.db import GPS_UPLOADS_DIR
from modules.gps_parser import parse_excel_gps, seconds_to_human
from modules.gps_repository import annul_gps_import, find_unit_id_by_plate, import_hash_exists, list_gps_imports, save_gps_sheet
from modules.repository import list_units

st.title("🛰️ Importar GPS SkyAngel")

st.caption("Carga el Excel descargado de SkyAngel/GpsGate. El importador detecta hojas mensuales, duplicados y movimientos.")

uploaded = st.file_uploader("Archivo Excel GPS", type=["xlsx"], key="gps_upload")

if uploaded is not None:
    file_bytes = uploaded.getvalue()
    parsed = parse_excel_gps(BytesIO(file_bytes), uploaded.name)

    st.subheader("Resumen de hojas detectadas")
    summary_rows = []
    for sh in parsed["sheets"]:
        summary_rows.append({
            "usar": sh.get("usar"),
            "hoja": sh.get("hoja"),
            "unidad": sh.get("unidad"),
            "tipo_hoja": sh.get("tipo_hoja"),
            "mes": sh.get("mes"),
            "anio": sh.get("anio"),
            "km_resumen": sh.get("km_resumen"),
            "km_calculados": sh.get("km_calculados"),
            "dif_km": sh.get("diferencia_km"),
            "movimientos": sh.get("movimientos_detectados"),
            "inmovilizaciones": sh.get("inmovilizaciones_detectadas"),
            "estado": sh.get("estado_validacion"),
            "motivo_descarte": sh.get("descartar_motivo", ""),
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    valid_sheets = parsed["valid_sheets"]
    if not valid_sheets:
        st.error("No se detectaron hojas válidas para importar.")
    else:
        st.subheader("Hojas que se importarán")
        for sh in valid_sheets:
            unit_id = find_unit_id_by_plate(sh.get("unidad"))
            exists = import_hash_exists(sh.get("hash_movimientos"), unit_id, sh.get("mes"), sh.get("anio"))
            with st.expander(f"{sh['hoja']} | {sh.get('unidad')} | {sh.get('movimientos_detectados')} movimientos", expanded=False):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Km calculados", f"{sh.get('km_calculados') or 0:,.2f}")
                c2.metric("Km resumen", "-" if sh.get("km_resumen") is None else f"{sh.get('km_resumen'):,.2f}")
                c3.metric("Dif. km", "-" if sh.get("diferencia_km") is None else f"{sh.get('diferencia_km'):,.2f}")
                c4.metric("Tiempo calculado", seconds_to_human(sh.get("tiempo_calculado_seg")))
                if unit_id is None:
                    st.warning(f"La unidad {sh.get('unidad')} no existe en el catálogo. Crea la unidad antes de importar para vincularla correctamente.")
                if exists:
                    st.warning("Esta hoja parece ya importada por hash/unidad/mes/año. Si la guardas otra vez, duplicarás datos.")
                movements = pd.DataFrame(sh.get("movimientos", []))
                if not movements.empty:
                    st.dataframe(movements.head(10), use_container_width=True, hide_index=True)

        if st.button("Guardar hojas válidas en base de datos", type="primary"):
            saved = 0
            skipped = 0
            GPS_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            saved_file = GPS_UPLOADS_DIR / uploaded.name
            if not saved_file.exists():
                saved_file.write_bytes(file_bytes)
            for sh in valid_sheets:
                unit_id = find_unit_id_by_plate(sh.get("unidad"))
                if import_hash_exists(sh.get("hash_movimientos"), unit_id, sh.get("mes"), sh.get("anio")):
                    skipped += 1
                    continue
                save_gps_sheet(sh, uploaded.name, unidad_id=unit_id)
                saved += 1
            st.success(f"Importación terminada. Hojas guardadas: {saved}. Omitidas por duplicado: {skipped}.")
            if saved > 0:
                st.info("Ahora puedes ir a Conciliación GPS para cruzar las entregas capturadas contra las paradas GPS.")

st.divider()
st.subheader("Importaciones GPS registradas")
show_inactive = st.checkbox("Mostrar importaciones anuladas", value=False)
imports_df = list_gps_imports({"include_inactive": show_inactive})
if imports_df.empty:
    st.info("Aún no hay importaciones GPS guardadas.")
else:
    show_cols = [
        "id", "archivo", "hoja", "placas", "placas_catalogo", "mes", "anio",
        "km_resumen", "km_calculados", "diferencia_km", "movimientos_detectados",
        "inmovilizaciones_detectadas", "estado_validacion", "activo", "motivo_anulacion", "creado_en"
    ]
    st.dataframe(imports_df[show_cols], use_container_width=True, hide_index=True)


st.divider()
st.subheader("Anular importación GPS")
st.caption("Anular excluye la importación de reportes y conciliaciones sin borrar físicamente los movimientos/paradas.")
imports_for_cancel = list_gps_imports()
if imports_for_cancel.empty:
    st.info("No hay importaciones activas para anular.")
else:
    cancel_id = st.selectbox(
        "Importación activa",
        options=imports_for_cancel["id"].tolist(),
        format_func=lambda x: f"#{x} | {imports_for_cancel.loc[imports_for_cancel['id']==x, 'archivo'].iloc[0]} | {imports_for_cancel.loc[imports_for_cancel['id']==x, 'hoja'].iloc[0]}",
        key="cancel_gps_import",
    )
    motivo_cancel = st.selectbox("Motivo", options=["Duplicado", "Archivo equivocado", "Unidad equivocada", "Periodo equivocado", "Otro"], key="motivo_cancel_gps")
    comentario_cancel = st.text_area("Comentario obligatorio", key="comentario_cancel_gps")
    confirm_cancel = st.checkbox("Confirmo que quiero anular esta importación GPS.", key="confirm_cancel_gps")
    if st.button("Anular importación GPS", type="primary"):
        if not comentario_cancel.strip():
            st.error("El comentario es obligatorio.")
        elif not confirm_cancel:
            st.error("Debes confirmar la anulación.")
        else:
            annul_gps_import(int(cancel_id), motivo_cancel, comentario_cancel.strip())
            st.success("Importación GPS anulada. Sus movimientos/paradas quedan excluidos de reportes y conciliaciones.")
