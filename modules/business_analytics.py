from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from modules.db import get_connection
from modules.repository import list_charges
from modules.gps_repository import gps_summary_by_unit, list_gps_movements, list_gps_stops
from modules.performance import cache_data


def _empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _parse_datetime(fecha: Any, hora: Any = None, end_of_day: bool = False) -> pd.Timestamp | None:
    if fecha is None or str(fecha).strip() == "":
        return None
    fecha_s = str(fecha).strip()
    hora_s = ""
    if hora is not None and str(hora).strip() not in {"", "None", "NaT"}:
        hora_s = str(hora).strip()
    if not hora_s:
        hora_s = "23:59:59" if end_of_day else "00:00:00"
    value = pd.to_datetime(f"{fecha_s} {hora_s}", errors="coerce")
    if pd.isna(value):
        value = pd.to_datetime(fecha_s, errors="coerce")
        if pd.notna(value) and end_of_day:
            value = value + pd.Timedelta(hours=23, minutes=59, seconds=59)
    return None if pd.isna(value) else value


@cache_data(ttl=60)
def get_data_bounds() -> dict[str, date | None]:
    """Return first/last date available across fuel, GPS and routes."""
    queries = [
        "SELECT MIN(fecha_carga) AS min_d, MAX(fecha_carga) AS max_d FROM cargas_combustible WHERE activo=1",
        """
        SELECT MIN(gm.fecha) AS min_d, MAX(gm.fecha) AS max_d
        FROM gps_movimientos gm
        JOIN gps_importaciones gi ON gi.id = gm.importacion_id AND COALESCE(gi.activo,1)=1
        """,
        "SELECT MIN(fecha) AS min_d, MAX(fecha) AS max_d FROM rutas WHERE activo=1 AND COALESCE(tipo_ruta,'OPERATIVA')='OPERATIVA'",
    ]
    mins: list[pd.Timestamp] = []
    maxs: list[pd.Timestamp] = []
    with get_connection() as conn:
        for sql in queries:
            row = conn.execute(sql).fetchone()
            if not row:
                continue
            for target, container in [(row["min_d"], mins), (row["max_d"], maxs)]:
                ts = pd.to_datetime(target, errors="coerce")
                if pd.notna(ts):
                    container.append(ts)
    return {
        "min_date": min(mins).date() if mins else None,
        "max_date": max(maxs).date() if maxs else None,
    }


@cache_data(ttl=60)
def default_date_range(days_back: int = 31) -> tuple[date, date]:
    bounds = get_data_bounds()
    max_d = bounds.get("max_date") or date.today()
    min_d = bounds.get("min_date") or max_d
    start = max(min_d, max_d - timedelta(days=days_back - 1))
    return start, max_d


@cache_data(ttl=60)
def monthly_fuel_gps_summary(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    filters = filters or {}
    charges = list_charges({**filters, "active_only": True})
    gps = list_gps_movements(filters)

    fuel_cols = ["unidad_id", "placas", "mes", "litros", "gasto", "cargas", "cargas_sin_ticket", "cargas_sin_folio", "cargas_sin_km"]
    gps_cols = ["unidad_id", "placas_gps", "mes", "km_gps", "horas_movimiento", "movimientos", "dias_con_movimiento"]

    if charges.empty:
        fuel_summary = _empty_df(fuel_cols)
    else:
        charges = charges.copy()
        charges["fecha_carga"] = pd.to_datetime(charges["fecha_carga"], errors="coerce")
        charges["mes"] = charges["fecha_carga"].dt.to_period("M").astype(str)
        charges["sin_ticket"] = charges.get("imagen_ticket_path", pd.Series(index=charges.index, dtype=object)).isna() | (charges.get("imagen_ticket_path", "").astype(str).str.strip() == "")
        charges["sin_folio"] = charges.get("ticket_folio", pd.Series(index=charges.index, dtype=object)).isna() | (charges.get("ticket_folio", "").astype(str).str.strip() == "")
        charges["sin_km"] = pd.to_numeric(charges.get("kilometraje"), errors="coerce").isna()
        fuel_summary = charges.groupby(["unidad_id", "placas", "mes"], as_index=False, dropna=False).agg(
            litros=("litros", "sum"),
            gasto=("importe_total", "sum"),
            cargas=("id", "count"),
            cargas_sin_ticket=("sin_ticket", "sum"),
            cargas_sin_folio=("sin_folio", "sum"),
            cargas_sin_km=("sin_km", "sum"),
        )

    if gps.empty:
        gps_summary = _empty_df(gps_cols)
    else:
        gps = gps.copy()
        gps["fecha"] = pd.to_datetime(gps["fecha"], errors="coerce")
        gps["mes"] = gps["fecha"].dt.to_period("M").astype(str)
        gps["horas"] = pd.to_numeric(gps.get("duracion_reportada_seg"), errors="coerce").fillna(0) / 3600
        gps_summary = gps.groupby(["unidad_id", "placas_catalogo", "mes"], as_index=False, dropna=False).agg(
            km_gps=("km", "sum"),
            horas_movimiento=("horas", "sum"),
            movimientos=("id", "count"),
            dias_con_movimiento=("fecha", lambda s: s.dt.date.nunique()),
        ).rename(columns={"placas_catalogo": "placas_gps"})

    if fuel_summary.empty and gps_summary.empty:
        return _empty_df(["unidad_id", "placas", "mes", "km_gps", "litros", "gasto", "rendimiento_gps_km_l", "costo_por_km_gps"])

    out = pd.merge(fuel_summary, gps_summary, on=["unidad_id", "mes"], how="outer")
    if "placas" not in out:
        out["placas"] = None
    if "placas_gps" not in out:
        out["placas_gps"] = None
    out["placas"] = out["placas"].fillna(out["placas_gps"]).fillna("Sin unidad vinculada")
    for col in ["litros", "gasto", "cargas", "cargas_sin_ticket", "cargas_sin_folio", "cargas_sin_km", "km_gps", "horas_movimiento", "movimientos", "dias_con_movimiento"]:
        if col not in out:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["km_gps"] = out["km_gps"].round(2)
    out["horas_movimiento"] = out["horas_movimiento"].round(2)
    out["rendimiento_gps_km_l"] = out.apply(lambda r: round(r["km_gps"] / r["litros"], 2) if r["litros"] else None, axis=1)
    out["costo_por_km_gps"] = out.apply(lambda r: round(r["gasto"] / r["km_gps"], 2) if r["km_gps"] else None, axis=1)
    return out.sort_values(["mes", "placas"], ascending=[False, True]).reset_index(drop=True)


@cache_data(ttl=60)
def fuel_gps_cycles(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """Approximate GPS km between consecutive fuel charges per unit.

    The liters of the closing charge are used for the interval. This is a useful
    operational approximation, but not a mechanical full-tank efficiency unless
    the charge type is controlled.
    """
    filters = filters or {}
    charges = list_charges({**filters, "active_only": True})
    if charges.empty:
        return _empty_df([
            "unidad_id", "placas", "carga_anterior_id", "carga_actual_id", "inicio_periodo", "fin_periodo",
            "litros", "gasto", "km_gps", "rendimiento_gps_km_l", "costo_por_km_gps", "estado_analisis",
            "alertas", "folio", "gasolinera",
        ])

    charges = charges.copy()
    charges["dt_carga"] = charges.apply(lambda r: _parse_datetime(r.get("fecha_carga"), r.get("hora_carga")), axis=1)
    charges = charges.dropna(subset=["dt_carga"]).sort_values(["unidad_id", "dt_carga", "id"])

    gps_all = list_gps_movements({
        "unidad_id": filters.get("unidad_id"),
        "fecha_desde": filters.get("fecha_desde"),
        "fecha_hasta": filters.get("fecha_hasta"),
    })
    if gps_all.empty:
        gps_all = _empty_df(["unidad_id", "inicio_datetime", "fin_datetime", "km", "duracion_reportada_seg"])
    else:
        gps_all = gps_all.copy()
        gps_all["inicio_dt"] = pd.to_datetime(gps_all["inicio_datetime"], errors="coerce")
        gps_all["km"] = pd.to_numeric(gps_all["km"], errors="coerce").fillna(0)
        gps_all["duracion_reportada_seg"] = pd.to_numeric(gps_all.get("duracion_reportada_seg"), errors="coerce").fillna(0)

    rows: list[dict[str, Any]] = []
    for unit_id, group in charges.groupby("unidad_id", dropna=False):
        group = group.sort_values(["dt_carga", "id"]).reset_index(drop=True)
        for i in range(1, len(group)):
            prev = group.iloc[i - 1]
            cur = group.iloc[i]
            start = prev["dt_carga"]
            end = cur["dt_carga"]
            if pd.isna(start) or pd.isna(end) or end <= start:
                continue
            unit_gps = gps_all[gps_all["unidad_id"] == unit_id]
            mask = (unit_gps["inicio_dt"] > start) & (unit_gps["inicio_dt"] <= end)
            km = float(unit_gps.loc[mask, "km"].sum()) if not unit_gps.empty else 0.0
            horas = float(unit_gps.loc[mask, "duracion_reportada_seg"].sum() / 3600.0) if not unit_gps.empty else 0.0
            litros = float(cur.get("litros") or 0)
            gasto = float(cur.get("importe_total") or 0)
            rendimiento = round(km / litros, 2) if litros else None
            costo = round(gasto / km, 2) if km else None
            limite_litros = pd.to_numeric(cur.get("limite_litros"), errors="coerce") if "limite_litros" in cur.index else None
            prev_litros = float(prev.get("litros") or 0)
            alertas: list[str] = []
            estado = "OK"
            tipo_carga = str(cur.get("tipo_carga_combustible") or "No especificada")
            prev_tipo_carga = str(prev.get("tipo_carga_combustible") or "No especificada")
            if tipo_carga in {"Parcial", "Emergencia", "Garrafón"}:
                alertas.append(f"carga_actual_{tipo_carga.lower()}")
                estado = "NO_CONCLUYENTE"
            if prev_tipo_carga in {"Parcial", "Emergencia", "Garrafón"}:
                alertas.append(f"carga_anterior_{prev_tipo_carga.lower()}")
                estado = "NO_CONCLUYENTE"
            if not cur.get("hora_carga"):
                alertas.append("sin_hora_carga")
            if km == 0:
                alertas.append("sin_km_gps")
                estado = "REVISAR"
            if litros <= 0:
                alertas.append("litros_invalidos")
                estado = "REVISAR"
            if limite_litros is not None and pd.notna(limite_litros) and float(limite_litros) > 0:
                if litros < float(limite_litros) * 0.20:
                    alertas.append("posible_carga_parcial")
                    estado = "NO_CONCLUYENTE"
                if prev_litros < float(limite_litros) * 0.20:
                    alertas.append("carga_anterior_parcial")
                    estado = "NO_CONCLUYENTE"
            if rendimiento is not None and (rendimiento < 3 or rendimiento > 12):
                alertas.append("rendimiento_extremo")
                estado = "REVISAR" if estado == "OK" else estado
            if not cur.get("ticket_folio"):
                alertas.append("sin_folio")
            if not cur.get("imagen_ticket_path"):
                alertas.append("sin_ticket")
            rows.append({
                "unidad_id": unit_id,
                "placas": cur.get("placas"),
                "carga_anterior_id": int(prev.get("id")),
                "carga_actual_id": int(cur.get("id")),
                "inicio_periodo": start,
                "fin_periodo": end,
                "litros": round(litros, 2),
                "gasto": round(gasto, 2),
                "km_gps": round(km, 2),
                "horas_movimiento": round(horas, 2),
                "rendimiento_gps_km_l": rendimiento,
                "costo_por_km_gps": costo,
                "estado_analisis": estado,
                "tipo_carga_combustible": tipo_carga,
                "alertas": ", ".join(alertas) if alertas else "",
                "folio": cur.get("ticket_folio"),
                "gasolinera": cur.get("gasolinera"),
            })
    return pd.DataFrame(rows)


@cache_data(ttl=60)
def gps_activity_by_day(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    gps = list_gps_movements(filters or {})
    if gps.empty:
        return _empty_df(["fecha", "unidad_id", "placas", "km_gps", "movimientos", "horas_movimiento"])
    gps = gps.copy()
    gps["horas_movimiento"] = pd.to_numeric(gps.get("duracion_reportada_seg"), errors="coerce").fillna(0) / 3600
    out = gps.groupby(["fecha", "unidad_id", "placas_catalogo"], as_index=False, dropna=False).agg(
        km_gps=("km", "sum"), movimientos=("id", "count"), horas_movimiento=("horas_movimiento", "sum")
    ).rename(columns={"placas_catalogo": "placas"})
    out["km_gps"] = out["km_gps"].round(2)
    out["horas_movimiento"] = out["horas_movimiento"].round(2)
    return out.sort_values(["fecha", "placas"], ascending=[False, True])


@cache_data(ttl=60)
def stops_operational_view(filters: dict[str, Any] | None = None, unmatched_only: bool = True, min_minutes: float = 15, max_hours: float | None = 8, exclude_probable_base: bool = True) -> pd.DataFrame:
    stops = list_gps_stops(filters or {}, unmatched_only=unmatched_only)
    if stops.empty:
        return _empty_df(["id", "fecha", "placas_catalogo", "inicio_gps", "fin_gps", "duracion_min", "direccion_gps", "categoria_sugerida"])
    stops = stops.copy()
    stops["duracion_min"] = pd.to_numeric(stops.get("duracion_seg"), errors="coerce").fillna(0) / 60
    stops["direccion_gps"] = stops.get("direccion_gps", "").fillna("").astype(str)
    stops["categoria_sugerida"] = stops["duracion_min"].apply(_categorize_stop_by_duration)
    stops["es_probable_base"] = stops.apply(_is_probable_base_stop, axis=1)
    stops = stops[stops["duracion_min"] >= float(min_minutes)]
    if max_hours is not None:
        stops = stops[stops["duracion_min"] <= float(max_hours) * 60]
    if exclude_probable_base:
        stops = stops[~stops["es_probable_base"]]
    return stops.sort_values("duracion_min", ascending=False).reset_index(drop=True)


@cache_data(ttl=60)
def base_like_stops(filters: dict[str, Any] | None = None) -> pd.DataFrame:
    stops = list_gps_stops(filters or {}, unmatched_only=False)
    if stops.empty:
        return _empty_df(["direccion_gps", "placas_catalogo", "paradas", "duracion_total_h", "duracion_prom_h", "primera_fecha", "ultima_fecha"])
    stops = stops.copy()
    stops["duracion_h"] = pd.to_numeric(stops.get("duracion_seg"), errors="coerce").fillna(0) / 3600
    stops = stops[stops.apply(_is_probable_base_stop, axis=1)]
    if stops.empty:
        return _empty_df(["direccion_gps", "placas_catalogo", "paradas", "duracion_total_h", "duracion_prom_h", "primera_fecha", "ultima_fecha"])
    out = stops.groupby(["direccion_gps", "placas_catalogo"], as_index=False, dropna=False).agg(
        paradas=("id", "count"),
        duracion_total_h=("duracion_h", "sum"),
        duracion_prom_h=("duracion_h", "mean"),
        primera_fecha=("fecha", "min"),
        ultima_fecha=("fecha", "max"),
    )
    out["duracion_total_h"] = out["duracion_total_h"].round(2)
    out["duracion_prom_h"] = out["duracion_prom_h"].round(2)
    return out.sort_values("duracion_total_h", ascending=False)


@cache_data(ttl=60)
def destination_candidates_from_gps(filters: dict[str, Any] | None = None, min_minutes: float = 15, limit: int = 200) -> pd.DataFrame:
    stops = stops_operational_view(filters or {}, unmatched_only=False, min_minutes=min_minutes, max_hours=8, exclude_probable_base=True)
    if stops.empty:
        return _empty_df(["direccion_gps", "placas", "veces", "duracion_total_min", "duracion_prom_min", "primera_fecha", "ultima_fecha", "unidades"])
    # Remove stops already manually classified as ignorar/comida/personal/taller/gasolinera if present
    if "clasificacion_manual" in stops.columns:
        stops = stops[~stops["clasificacion_manual"].fillna("").isin(["ignorar", "comida", "personal", "taller", "gasolinera"])]
    grouped = stops.groupby("direccion_gps", as_index=False).agg(
        veces=("id", "count"),
        duracion_total_min=("duracion_min", "sum"),
        duracion_prom_min=("duracion_min", "mean"),
        primera_fecha=("fecha", "min"),
        ultima_fecha=("fecha", "max"),
        unidades=("placas_catalogo", lambda s: ", ".join(sorted({str(x) for x in s.dropna()}))),
    )
    grouped["duracion_total_min"] = grouped["duracion_total_min"].round(1)
    grouped["duracion_prom_min"] = grouped["duracion_prom_min"].round(1)
    grouped = grouped.sort_values(["veces", "duracion_total_min"], ascending=[False, False]).head(limit)
    return grouped.reset_index(drop=True)


@cache_data(ttl=60)
def fuel_quality_summary(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    charges = list_charges({**(filters or {}), "active_only": True})
    if charges.empty:
        return {"cargas": 0, "sin_ticket": 0, "sin_folio": 0, "sin_km": 0, "no_concluyentes": 0, "gasto": 0.0, "litros": 0.0}
    sin_ticket = charges.get("imagen_ticket_path", pd.Series(index=charges.index, dtype=object)).isna() | (charges.get("imagen_ticket_path", "").astype(str).str.strip() == "")
    sin_folio = charges.get("ticket_folio", pd.Series(index=charges.index, dtype=object)).isna() | (charges.get("ticket_folio", "").astype(str).str.strip() == "")
    sin_km = pd.to_numeric(charges.get("kilometraje"), errors="coerce").isna()
    no_concluyentes = charges.get("tipo_carga_combustible", pd.Series(index=charges.index, dtype=object)).fillna("No especificada").isin(["Parcial", "Emergencia", "Garrafón"]) | charges.get("calidad_registro", pd.Series(index=charges.index, dtype=object)).fillna("").astype(str).str.contains("NO_CONCLUYENTE|POSIBLE_CARGA_PARCIAL", regex=True)
    return {
        "cargas": int(len(charges)),
        "sin_ticket": int(sin_ticket.sum()),
        "sin_folio": int(sin_folio.sum()),
        "sin_km": int(sin_km.sum()),
        "no_concluyentes": int(no_concluyentes.sum()),
        "gasto": float(pd.to_numeric(charges.get("importe_total"), errors="coerce").fillna(0).sum()),
        "litros": float(pd.to_numeric(charges.get("litros"), errors="coerce").fillna(0).sum()),
    }


def _categorize_stop_by_duration(minutes: float) -> str:
    if minutes < 5:
        return "ruido_maniobra"
    if minutes < 15:
        return "parada_corta"
    if minutes < 30:
        return "parada_relevante"
    if minutes < 60:
        return "revisar"
    return "prioridad_alta"


def _is_probable_base_stop(row: pd.Series) -> bool:
    minutes = float(row.get("duracion_min", 0) or 0)
    if minutes <= 0 and row.get("duracion_seg") is not None:
        minutes = float(row.get("duracion_seg") or 0) / 60
    address = str(row.get("direccion_gps") or "").lower()
    # Strong business-context heuristic from the current data. It is intentionally visible
    # and should later become a configurable catálogo de bases.
    base_terms = ["santo domingo", "nueva santo domingo", "sta lucia", "santa lucia", "centeotl"]
    if minutes >= 60 and any(term in address for term in base_terms):
        return True
    if minutes >= 720:
        return True
    return False
