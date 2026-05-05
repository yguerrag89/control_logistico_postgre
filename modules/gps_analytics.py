from __future__ import annotations

import re
from typing import Any

import pandas as pd

from modules.db import get_connection
from modules.gps_repository import list_gps_movements, list_gps_stops
from modules.performance import cache_data


def _empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _to_minutes(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0) / 60.0


@cache_data(ttl=60)
def get_daily_km_by_unit(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """Km diarios por unidad a partir de movimientos GPS activos.

    v1.12: el agregado se calcula en SQL para no traer todos los movimientos
    a pandas cada vez que se abre el panel.
    """
    filters = filters or {}
    cols = [
        "fecha", "unidad_id", "placas", "km_gps", "movimientos",
        "horas_movimiento", "hora_primer_movimiento", "hora_ultimo_movimiento",
    ]
    sql = """
        SELECT
            gm.fecha AS fecha,
            gm.unidad_id AS unidad_id,
            u.placas AS placas,
            ROUND(SUM(COALESCE(gm.km, 0))::numeric, 2) AS km_gps,
            COUNT(gm.id) AS movimientos,
            ROUND((SUM(COALESCE(gm.duracion_reportada_seg, 0)) / 3600.0)::numeric, 2) AS horas_movimiento,
            MIN(gm.inicio_datetime) AS primer_dt,
            MAX(gm.fin_datetime) AS ultimo_dt
        FROM gps_movimientos gm
        LEFT JOIN unidades u ON u.id = gm.unidad_id
        JOIN gps_importaciones gi ON gi.id = gm.importacion_id AND COALESCE(gi.activo,1)=1
        WHERE 1=1
    """
    # SQLite does not support ::numeric casts. The compatibility layer does not
    # translate that syntax, so use SQLite-safe SQL when the active connection is
    # SQLite.
    sql_sqlite = """
        SELECT
            gm.fecha AS fecha,
            gm.unidad_id AS unidad_id,
            u.placas AS placas,
            ROUND(SUM(COALESCE(gm.km, 0)), 2) AS km_gps,
            COUNT(gm.id) AS movimientos,
            ROUND(SUM(COALESCE(gm.duracion_reportada_seg, 0)) / 3600.0, 2) AS horas_movimiento,
            MIN(gm.inicio_datetime) AS primer_dt,
            MAX(gm.fin_datetime) AS ultimo_dt
        FROM gps_movimientos gm
        LEFT JOIN unidades u ON u.id = gm.unidad_id
        JOIN gps_importaciones gi ON gi.id = gm.importacion_id AND COALESCE(gi.activo,1)=1
        WHERE 1=1
    """
    params: list[Any] = []
    where = ""
    if filters.get("unidad_id"):
        where += " AND gm.unidad_id = ?"
        params.append(filters["unidad_id"])
    if filters.get("fecha_desde"):
        where += " AND gm.fecha >= ?"
        params.append(filters["fecha_desde"])
    if filters.get("fecha_hasta"):
        where += " AND gm.fecha <= ?"
        params.append(filters["fecha_hasta"])
    group_order = " GROUP BY gm.fecha, gm.unidad_id, u.placas ORDER BY gm.fecha DESC, u.placas ASC"
    with get_connection() as conn:
        use_pg = bool(getattr(conn, "is_postgres", False))
        rows = conn.execute((sql if use_pg else sql_sqlite) + where + group_order, params).fetchall()
    if not rows:
        return _empty_df(cols)
    out = pd.DataFrame([dict(r) for r in rows])
    out["km_gps"] = pd.to_numeric(out["km_gps"], errors="coerce").fillna(0).round(2)
    out["horas_movimiento"] = pd.to_numeric(out["horas_movimiento"], errors="coerce").fillna(0).round(2)
    out["movimientos"] = pd.to_numeric(out["movimientos"], errors="coerce").fillna(0).astype(int)
    out["hora_primer_movimiento"] = pd.to_datetime(out["primer_dt"], errors="coerce").dt.strftime("%H:%M")
    out["hora_ultimo_movimiento"] = pd.to_datetime(out["ultimo_dt"], errors="coerce").dt.strftime("%H:%M")
    return out[cols].sort_values(["fecha", "placas"], ascending=[False, True]).reset_index(drop=True)


@cache_data(ttl=60)
def get_daily_km_total(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    daily = get_daily_km_by_unit(filters)
    cols = ["fecha", "km_total", "unidades_activas", "km_promedio_unidad_activa", "movimientos_totales", "horas_movimiento"]
    if daily.empty:
        return _empty_df(cols)
    out = daily.groupby("fecha", as_index=False).agg(
        km_total=("km_gps", "sum"),
        unidades_activas=("unidad_id", lambda s: s.dropna().nunique()),
        movimientos_totales=("movimientos", "sum"),
        horas_movimiento=("horas_movimiento", "sum"),
    )
    out["km_total"] = out["km_total"].round(2)
    out["horas_movimiento"] = out["horas_movimiento"].round(2)
    out["km_promedio_unidad_activa"] = out.apply(
        lambda r: round(r["km_total"] / r["unidades_activas"], 2) if r["unidades_activas"] else 0,
        axis=1,
    )
    return out[cols].sort_values("fecha", ascending=False).reset_index(drop=True)


@cache_data(ttl=60)
def get_unit_activity_summary(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    daily = get_daily_km_by_unit(filters)
    cols = ["unidad_id", "placas", "km_total", "dias_activos", "km_promedio_dia_activo", "movimientos", "horas_movimiento"]
    if daily.empty:
        return _empty_df(cols)
    out = daily.groupby(["unidad_id", "placas"], as_index=False, dropna=False).agg(
        km_total=("km_gps", "sum"),
        dias_activos=("fecha", "nunique"),
        movimientos=("movimientos", "sum"),
        horas_movimiento=("horas_movimiento", "sum"),
    )
    out["km_total"] = out["km_total"].round(2)
    out["horas_movimiento"] = out["horas_movimiento"].round(2)
    out["km_promedio_dia_activo"] = out.apply(
        lambda r: round(r["km_total"] / r["dias_activos"], 2) if r["dias_activos"] else 0,
        axis=1,
    )
    return out[cols].sort_values("km_total", ascending=False).reset_index(drop=True)




def _unit_label_lookup(unit_ids: list[int] | None = None) -> pd.DataFrame:
    """Return unit labels from SQLite without depending on repository imports."""
    sql = "SELECT id AS unidad_id, placas FROM unidades WHERE 1=1"
    params: list[Any] = []
    if unit_ids:
        placeholders = ",".join("?" for _ in unit_ids)
        sql += f" AND id IN ({placeholders})"
        params.extend([int(x) for x in unit_ids])
    sql += " ORDER BY placas"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@cache_data(ttl=60)
def get_daily_km_by_unit_complete(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """Km diarios por unidad, rellenando con 0 los días sin movimiento.

    Esta función está pensada para gráficas operativas: si el rango es 01-04 a
    30-04, devuelve todos los días de ese rango para cada unidad con actividad
    en el periodo (o la unidad seleccionada), aunque no haya movimiento ese día.
    """
    filters = filters or {}
    base_cols = [
        "fecha", "fecha_label", "unidad_id", "placas", "km_gps", "movimientos",
        "horas_movimiento", "es_dia_sin_movimiento",
    ]
    daily = get_daily_km_by_unit(filters)

    start_raw = filters.get("fecha_desde")
    end_raw = filters.get("fecha_hasta")
    start = pd.to_datetime(start_raw, errors="coerce") if start_raw else pd.NaT
    end = pd.to_datetime(end_raw, errors="coerce") if end_raw else pd.NaT

    if pd.isna(start):
        start = pd.to_datetime(daily["fecha"].min(), errors="coerce") if not daily.empty else pd.NaT
    if pd.isna(end):
        end = pd.to_datetime(daily["fecha"].max(), errors="coerce") if not daily.empty else pd.NaT
    if pd.isna(start) or pd.isna(end):
        return _empty_df(base_cols)
    if start > end:
        start, end = end, start

    selected_unit = filters.get("unidad_id")
    if selected_unit:
        unit_ids = [int(selected_unit)]
        units = _unit_label_lookup(unit_ids)
    elif not daily.empty:
        units = daily[["unidad_id", "placas"]].drop_duplicates().copy()
        units = units[units["unidad_id"].notna()]
    else:
        return _empty_df(base_cols)

    if units.empty:
        return _empty_df(base_cols)

    date_range = pd.date_range(start=start.normalize(), end=end.normalize(), freq="D")
    grid = pd.MultiIndex.from_product(
        [date_range, units["unidad_id"].astype(int).tolist()],
        names=["fecha_dt", "unidad_id"],
    ).to_frame(index=False)
    grid = grid.merge(units.assign(unidad_id=units["unidad_id"].astype(int)), on="unidad_id", how="left")

    if daily.empty:
        merged = grid.copy()
        for col in ["km_gps", "movimientos", "horas_movimiento"]:
            merged[col] = 0
    else:
        d = daily.copy()
        d["fecha_dt"] = pd.to_datetime(d["fecha"], errors="coerce")
        d = d.dropna(subset=["fecha_dt"])
        d["fecha_dt"] = d["fecha_dt"].dt.normalize()
        d["unidad_id"] = pd.to_numeric(d["unidad_id"], errors="coerce").astype("Int64")
        d = d.dropna(subset=["unidad_id"])
        d["unidad_id"] = d["unidad_id"].astype(int)
        keep = ["fecha_dt", "unidad_id", "km_gps", "movimientos", "horas_movimiento"]
        merged = grid.merge(d[keep], on=["fecha_dt", "unidad_id"], how="left")
        for col in ["km_gps", "movimientos", "horas_movimiento"]:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)

    merged["fecha"] = merged["fecha_dt"].dt.strftime("%Y-%m-%d")
    merged["fecha_label"] = merged["fecha_dt"].dt.strftime("%d/%m")
    merged["km_gps"] = merged["km_gps"].round(2)
    merged["horas_movimiento"] = merged["horas_movimiento"].round(2)
    merged["movimientos"] = merged["movimientos"].astype(int)
    merged["es_dia_sin_movimiento"] = merged["km_gps"] <= 0
    return merged[base_cols].sort_values(["fecha", "placas"]).reset_index(drop=True)


@cache_data(ttl=60)
def get_daily_km_total_complete(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """Km diarios de flota con todos los días del rango, incluyendo 0 km."""
    complete = get_daily_km_by_unit_complete(filters)
    cols = ["fecha", "fecha_label", "km_total", "unidades_activas", "movimientos_totales", "horas_movimiento"]
    if complete.empty:
        return _empty_df(cols)
    out = complete.groupby(["fecha", "fecha_label"], as_index=False).agg(
        km_total=("km_gps", "sum"),
        unidades_activas=("km_gps", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum())),
        movimientos_totales=("movimientos", "sum"),
        horas_movimiento=("horas_movimiento", "sum"),
    )
    out["km_total"] = out["km_total"].round(2)
    out["horas_movimiento"] = out["horas_movimiento"].round(2)
    return out[cols].sort_values("fecha").reset_index(drop=True)

@cache_data(ttl=60)
def get_controlled_places(active_only: bool = True) -> pd.DataFrame:
    sql = """
        SELECT id, nombre_normalizado, alias, tipo_destino, cliente_asociado, direccion_texto,
               validado, COALESCE(excluir_alertas_inactividad,0) AS excluir_alertas_inactividad,
               COALESCE(radio_metros,100) AS radio_metros, activo
        FROM destinos
        WHERE 1=1
    """
    if active_only:
        sql += " AND activo = 1"
    sql += " ORDER BY nombre_normalizado"
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _build_place_terms(places: pd.DataFrame) -> list[dict[str, Any]]:
    if places.empty:
        return []
    terms: list[dict[str, Any]] = []
    allowed_default_types = {"base", "gasolinera", "taller", "cliente", "paquetería", "paqueteria", "cedis", "almacén", "almacen", "autorizado"}
    for _, row in places.iterrows():
        exclude = int(row.get("excluir_alertas_inactividad") or 0) == 1
        tipo = _normalize_text(row.get("tipo_destino"))
        validado = int(row.get("validado") or 0) == 1
        if not exclude and not (validado and tipo in allowed_default_types):
            continue
        raw_terms: list[str] = []
        for col in ["nombre_normalizado", "direccion_texto", "cliente_asociado"]:
            val = str(row.get(col) or "").strip()
            if len(val) >= 4:
                raw_terms.append(val)
        aliases = str(row.get("alias") or "")
        raw_terms.extend([a.strip() for a in re.split(r"[,;|/]", aliases) if len(a.strip()) >= 4])
        for raw in raw_terms:
            t = _normalize_text(raw)
            if len(t) >= 4:
                terms.append({
                    "destino_id": row.get("id"),
                    "term": t,
                    "nombre": row.get("nombre_normalizado"),
                    "tipo": row.get("tipo_destino"),
                })
    return terms


def classify_stop_against_places(address: str, place_terms: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Classify a stop by text matching against validated/authorized places."""
    address_norm = _normalize_text(address)
    if not address_norm:
        return {"clasificacion_lugar": "sin_direccion", "lugar_controlado": None, "tipo_lugar": None, "excluir_por_lugar": False}

    # Contextual heuristic from current data. It should later become configured base entries.
    base_terms = ["santo domingo", "nueva santo domingo", "sta lucia", "santa lucia", "centeotl"]
    if any(term in address_norm for term in base_terms):
        return {"clasificacion_lugar": "base_probable", "lugar_controlado": "Base probable", "tipo_lugar": "Base", "excluir_por_lugar": True}

    for item in place_terms or []:
        term = item["term"]
        if term and (term in address_norm or address_norm in term):
            return {
                "clasificacion_lugar": "lugar_controlado",
                "lugar_controlado": item.get("nombre"),
                "tipo_lugar": item.get("tipo"),
                "excluir_por_lugar": True,
            }
    return {"clasificacion_lugar": "sin_clasificar", "lugar_controlado": None, "tipo_lugar": None, "excluir_por_lugar": False}


def _duration_level(minutes: float) -> str:
    if minutes < 15:
        return "Baja"
    if minutes < 30:
        return "Relevante"
    if minutes < 60:
        return "Media"
    if minutes < 120:
        return "Alta"
    return "Crítica"


@cache_data(ttl=60)
def get_abnormal_inactivity(
    filters: dict[str, Any] | None = None,
    min_minutes: float = 30,
    exclude_authorized: bool = True,
    unmatched_only: bool = True,
    max_hours: float | None = None,
) -> pd.DataFrame:
    """Detecta paradas largas potencialmente anormales."""
    stops = list_gps_stops(filters or {}, unmatched_only=unmatched_only)
    cols = [
        "id", "fecha", "unidad_id", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min",
        "direccion_gps", "clasificacion_lugar", "lugar_controlado", "tipo_lugar",
        "nivel_alerta", "motivo_revision", "clasificacion_manual",
    ]
    if stops.empty:
        return _empty_df(cols)

    stops = stops.copy()
    stops["duracion_min"] = _to_minutes(stops.get("duracion_seg"))
    stops["direccion_gps"] = stops.get("direccion_gps", "").fillna("").astype(str)
    stops = stops[stops["duracion_min"] >= float(min_minutes)]
    if max_hours:
        stops = stops[stops["duracion_min"] <= float(max_hours) * 60]
    if stops.empty:
        return _empty_df(cols)

    places = get_controlled_places(active_only=True)
    terms = _build_place_terms(places)
    cls_df = pd.DataFrame(list(stops["direccion_gps"].apply(lambda x: classify_stop_against_places(x, terms))))
    stops = pd.concat([stops.reset_index(drop=True), cls_df.reset_index(drop=True)], axis=1)

    if "clasificacion_manual" in stops.columns:
        stops["clasificacion_manual"] = stops["clasificacion_manual"].fillna("")
        stops = stops[stops["clasificacion_manual"].isin(["", "revisar", "sin_clasificar"])]

    if exclude_authorized:
        stops = stops[~stops["excluir_por_lugar"].fillna(False)]

    stops["nivel_alerta"] = stops["duracion_min"].apply(_duration_level)
    stops["motivo_revision"] = "Parada larga sin entrega, combustible o lugar autorizado asociado"
    stops["duracion_min"] = stops["duracion_min"].round(1)
    return stops[cols].sort_values("duracion_min", ascending=False).reset_index(drop=True)


@cache_data(ttl=60)
def get_inactivity_summary_by_unit(filters: dict[str, Any] | None = None, min_minutes: float = 30) -> pd.DataFrame:
    abn = get_abnormal_inactivity(filters, min_minutes=min_minutes, exclude_authorized=True, unmatched_only=True)
    cols = ["unidad_id", "placas", "paradas_anormales", "minutos_anormales", "promedio_min_por_parada"]
    if abn.empty:
        return _empty_df(cols)
    out = abn.groupby(["unidad_id", "placas_catalogo"], as_index=False, dropna=False).agg(
        paradas_anormales=("id", "count"),
        minutos_anormales=("duracion_min", "sum"),
        promedio_min_por_parada=("duracion_min", "mean"),
    ).rename(columns={"placas_catalogo": "placas"})
    out["minutos_anormales"] = out["minutos_anormales"].round(1)
    out["promedio_min_por_parada"] = out["promedio_min_por_parada"].round(1)
    return out[cols].sort_values("minutos_anormales", ascending=False).reset_index(drop=True)


@cache_data(ttl=60)
def get_frequent_stop_locations(
    filters: dict[str, Any] | None = None,
    min_minutes: float = 15,
    include_authorized: bool = False,
    limit: int = 200,
) -> pd.DataFrame:
    stops = list_gps_stops(filters or {}, unmatched_only=False)
    cols = ["direccion_gps", "veces", "duracion_total_min", "duracion_prom_min", "unidades", "primera_fecha", "ultima_fecha", "clasificacion_lugar", "lugar_controlado"]
    if stops.empty:
        return _empty_df(cols)
    stops = stops.copy()
    stops["duracion_min"] = _to_minutes(stops.get("duracion_seg"))
    stops = stops[stops["duracion_min"] >= float(min_minutes)]
    if stops.empty:
        return _empty_df(cols)

    places = get_controlled_places(active_only=True)
    terms = _build_place_terms(places)
    cls_df = pd.DataFrame(list(stops["direccion_gps"].fillna("").apply(lambda x: classify_stop_against_places(x, terms))))
    stops = pd.concat([stops.reset_index(drop=True), cls_df.reset_index(drop=True)], axis=1)
    if not include_authorized:
        stops = stops[~stops["excluir_por_lugar"].fillna(False)]
    if stops.empty:
        return _empty_df(cols)

    grouped = stops.groupby(["direccion_gps", "clasificacion_lugar", "lugar_controlado"], as_index=False, dropna=False).agg(
        veces=("id", "count"),
        duracion_total_min=("duracion_min", "sum"),
        duracion_prom_min=("duracion_min", "mean"),
        primera_fecha=("fecha", "min"),
        ultima_fecha=("fecha", "max"),
        unidades=("placas_catalogo", lambda s: ", ".join(sorted({str(x) for x in s.dropna()}))),
    )
    grouped["duracion_total_min"] = grouped["duracion_total_min"].round(1)
    grouped["duracion_prom_min"] = grouped["duracion_prom_min"].round(1)
    return grouped[cols].sort_values(["veces", "duracion_total_min"], ascending=[False, False]).head(limit).reset_index(drop=True)
