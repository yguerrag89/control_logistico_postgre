from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


DATE_TIME_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm|AM|PM)$", re.I)
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm|AM|PM)$", re.I)
KM_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*km$", re.I)
UNIT_RE = re.compile(r"\bLF\d+\b", re.I)
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def clean_cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def read_vertical_values(excel_source: Any, sheet_name: str) -> list[str]:
    df = pd.read_excel(excel_source, sheet_name=sheet_name, header=None, dtype=object)
    if df.empty:
        return []
    values: list[str] = []
    for value in df.iloc[:, 0].tolist():
        text = clean_cell(value)
        if text:
            values.append(text)
    return values


def parse_spanish_ampm_time(hour_text: str) -> tuple[int, int]:
    text = hour_text.strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{2})(a\.m\.|p\.m\.|am|pm)$", text)
    if not m:
        raise ValueError(f"Hora no reconocida: {hour_text}")
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3)
    if "p" in ampm and hour != 12:
        hour += 12
    if "a" in ampm and hour == 12:
        hour = 0
    return hour, minute


def parse_datetime_text(value: str) -> datetime:
    date_part, time_part, ampm = re.match(
        r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*(a\.m\.|p\.m\.|am|pm|AM|PM)$",
        value.strip(),
        re.I,
    ).groups()
    day, month, year = [int(x) for x in date_part.split("/")]
    hour, minute = parse_spanish_ampm_time(f"{time_part}{ampm}")
    return datetime(year, month, day, hour, minute)


def parse_time_for_date(value: str, base_date: datetime) -> datetime:
    hour, minute = parse_spanish_ampm_time(value)
    dt = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt < base_date:
        dt += timedelta(days=1)
    return dt


def parse_km(value: str) -> float | None:
    m = KM_RE.match(value.strip())
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_duration_seconds(value: str) -> int | None:
    text = value.strip().lower()
    if text.startswith("inmovilizado:"):
        text = text.split(":", 1)[1].strip()
    text = text.replace("día", "dia").replace("días", "dias")
    total = 0
    matched = False
    for pattern, multiplier in [
        (r"(\d+(?:[.,]\d+)?)\s*dias?", 86400),
        (r"(\d+(?:[.,]\d+)?)\s*horas?", 3600),
        (r"(\d+(?:[.,]\d+)?)\s*min", 60),
        (r"(\d+(?:[.,]\d+)?)\s*seg", 1),
    ]:
        for m in re.finditer(pattern, text):
            matched = True
            total += int(round(float(m.group(1).replace(",", ".")) * multiplier))
    if text == "0 seg":
        return 0
    return total if matched else None


def infer_unit(values: list[str], file_name: str) -> str | None:
    for value in values[:25]:
        m = UNIT_RE.search(value)
        if m:
            return m.group(0).upper()
    m = UNIT_RE.search(file_name)
    return m.group(0).upper() if m else None


def infer_month_from_sheet(sheet_name: str) -> int | None:
    return MONTHS.get(sheet_name.strip().lower())


def classify_sheet(values: list[str]) -> tuple[str, int | None, int | None]:
    first_move_idx = next((i for i, v in enumerate(values) if DATE_TIME_RE.match(v)), None)
    if first_move_idx is None:
        has_summary = any(KM_RE.match(v) for v in values[:5])
        return ("sin_movimientos" if has_summary else "desconocida", None, first_move_idx)
    if first_move_idx <= 1:
        return "solo_movimientos", None, first_move_idx
    return "mensual_con_resumen", first_move_idx, first_move_idx


def parse_summary(values: list[str], first_move_idx: int | None) -> dict[str, Any]:
    end = first_move_idx if first_move_idx is not None else len(values)
    pre = values[:end]
    km_resumen = None
    tiempo_resumen_seg = None
    inmov_previas: list[dict[str, Any]] = []
    for value in pre:
        if km_resumen is None and KM_RE.match(value):
            km_resumen = parse_km(value)
            continue
        if tiempo_resumen_seg is None:
            dur = parse_duration_seconds(value)
            if dur is not None and not value.lower().startswith("inmovilizado:"):
                tiempo_resumen_seg = dur
                continue
        if value.lower().startswith("inmovilizado:"):
            dur = parse_duration_seconds(value)
            inmov_previas.append({"texto_original": value, "duracion_seg": dur or 0})
    return {
        "km_resumen": km_resumen,
        "tiempo_resumen_seg": tiempo_resumen_seg,
        "inmovilizaciones_previas": inmov_previas,
    }


def movement_flags(movement: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    km = movement.get("km") or 0
    dur = movement.get("duracion_reportada_seg") or 0
    origen = (movement.get("origen") or "").strip().lower()
    destino = (movement.get("destino") or "").strip().lower()
    speed = movement.get("velocidad_promedio_kmh")
    if km == 0:
        flags.append("km_cero")
    if dur == 0:
        flags.append("duracion_cero")
    if origen and destino and origen == destino:
        flags.append("mismo_origen_destino")
    if "unnamed road" in origen:
        flags.append("origen_sin_direccion")
    if "unnamed road" in destino:
        flags.append("destino_sin_direccion")
    if re.search(r"\b[A-Z0-9]{4}\+[A-Z0-9]{2,}\b", movement.get("origen", ""), re.I) or re.search(r"\b[A-Z0-9]{4}\+[A-Z0-9]{2,}\b", movement.get("destino", ""), re.I):
        flags.append("plus_code")
    if speed is not None and speed > 100:
        flags.append("velocidad_promedio_alta")
    if abs(movement.get("diferencia_duracion_seg") or 0) > 120:
        flags.append("duracion_no_cuadra")
    return flags


def parse_movements(values: list[str], first_move_idx: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    movements: list[dict[str, Any]] = []
    paradas: list[dict[str, Any]] = []
    if first_move_idx is None:
        return movements, paradas

    i = first_move_idx
    sec = 1
    last_movement_index = None
    while i < len(values):
        if not DATE_TIME_RE.match(values[i]):
            i += 1
            continue
        if i + 5 >= len(values):
            break
        try:
            start = parse_datetime_text(values[i])
            end = parse_time_for_date(values[i + 1], start)
            km = parse_km(values[i + 2])
            duration = parse_duration_seconds(values[i + 3])
            origen = values[i + 4]
            destino = values[i + 5]
        except Exception:
            i += 1
            continue
        if km is None or duration is None:
            i += 1
            continue

        calc_duration = int((end - start).total_seconds())
        speed = round(km / (duration / 3600), 2) if duration > 0 else None
        movement = {
            "secuencia": sec,
            "fecha": start.date().isoformat(),
            "inicio_datetime": start.isoformat(sep=" ", timespec="seconds"),
            "fin_datetime": end.isoformat(sep=" ", timespec="seconds"),
            "km": km,
            "duracion_reportada_seg": duration,
            "duracion_calculada_seg": calc_duration,
            "diferencia_duracion_seg": calc_duration - duration,
            "velocidad_promedio_kmh": speed,
            "origen": origen,
            "destino": destino,
        }
        movement["flags_calidad"] = "|".join(movement_flags(movement))
        movements.append(movement)
        last_movement_index = sec
        sec += 1

        cursor = end
        i += 6
        while i < len(values) and not DATE_TIME_RE.match(values[i]):
            if values[i].lower().startswith("inmovilizado:"):
                dur = parse_duration_seconds(values[i]) or 0
                fin = cursor + timedelta(seconds=dur)
                if dur < 5 * 60:
                    clasificacion = "ruido_maniobra"
                elif dur < 15 * 60:
                    clasificacion = "parada_corta"
                elif dur < 30 * 60:
                    clasificacion = "parada_relevante"
                elif dur < 60 * 60:
                    clasificacion = "revisar"
                else:
                    clasificacion = "prioridad_alta"
                paradas.append({
                    "movimiento_secuencia": last_movement_index,
                    "fecha": cursor.date().isoformat(),
                    "inicio_gps": cursor.isoformat(sep=" ", timespec="seconds"),
                    "fin_gps": fin.isoformat(sep=" ", timespec="seconds"),
                    "duracion_seg": dur,
                    "direccion_gps": destino,
                    "clasificacion_inicial": clasificacion,
                    "requiere_revision": 1 if dur >= 30 * 60 else 0,
                    "es_previa_al_primer_movimiento": 0,
                    "texto_original": values[i],
                })
                cursor = fin
            i += 1
    return movements, paradas


def build_hash(movements: list[dict[str, Any]]) -> str:
    if not movements:
        return ""
    joined = "\n".join(
        f"{m['inicio_datetime']}|{m['fin_datetime']}|{m['km']}|{m['origen']}|{m['destino']}"
        for m in movements
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def sheet_score(sheet_name: str, sheet_type: str, unit_explicit: bool, has_summary: bool, movements_count: int) -> int:
    score = movements_count
    if has_summary:
        score += 1000
    if infer_month_from_sheet(sheet_name) is not None:
        score += 500
    if unit_explicit:
        score += 100
    if re.match(r"hoja\d+", sheet_name.strip(), re.I):
        score -= 200
    if sheet_type == "solo_movimientos":
        score -= 100
    return score


def parse_excel_gps(excel_source: Any, file_name: str) -> dict[str, Any]:
    xl = pd.ExcelFile(excel_source)
    sheets: list[dict[str, Any]] = []
    for sheet_name in xl.sheet_names:
        # recreate source for uploaded BytesIO if needed outside; pandas handles seekable objects.
        try:
            values = read_vertical_values(excel_source, sheet_name)
        except Exception as exc:
            sheets.append({"hoja": sheet_name, "error": str(exc), "usar": False})
            continue

        first_move_idx = next((i for i, v in enumerate(values) if DATE_TIME_RE.match(v)), None)
        tipo_hoja = "desconocida"
        if first_move_idx is None:
            tipo_hoja = "sin_movimientos" if any(KM_RE.match(v) for v in values[:5]) else "desconocida"
        elif first_move_idx <= 1:
            tipo_hoja = "solo_movimientos"
        else:
            tipo_hoja = "mensual_con_resumen"

        unit = infer_unit(values, file_name)
        unit_explicit = any(UNIT_RE.search(v) for v in values[:25])
        summary = parse_summary(values, first_move_idx)
        movements, paradas = parse_movements(values, first_move_idx)
        # prepend previous immobilizations as paradas without interval
        pre_paradas = []
        for idx, p in enumerate(summary["inmovilizaciones_previas"], start=1):
            pre_paradas.append({
                "movimiento_secuencia": None,
                "fecha": None,
                "inicio_gps": None,
                "fin_gps": None,
                "duracion_seg": p["duracion_seg"],
                "direccion_gps": None,
                "clasificacion_inicial": "inmovilizacion_previa",
                "requiere_revision": 0,
                "es_previa_al_primer_movimiento": 1,
                "texto_original": p["texto_original"],
            })
        all_paradas = pre_paradas + paradas
        km_calc = round(sum(m["km"] for m in movements), 2)
        time_calc = sum((m["duracion_reportada_seg"] or 0) for m in movements) + sum((p["duracion_seg"] or 0) for p in all_paradas)
        hash_mov = build_hash(movements)
        years = sorted({int(m["fecha"][:4]) for m in movements if m.get("fecha")})
        months = sorted({int(m["fecha"][5:7]) for m in movements if m.get("fecha")})
        mes = infer_month_from_sheet(sheet_name) or (months[0] if len(months) == 1 else None)
        anio = years[0] if len(years) == 1 else None
        has_summary = summary["km_resumen"] is not None or summary["tiempo_resumen_seg"] is not None
        score = sheet_score(sheet_name, tipo_hoja, unit_explicit, has_summary, len(movements))
        estado = "OK"
        diff_km = None
        if summary["km_resumen"] is not None:
            diff_km = round(km_calc - float(summary["km_resumen"]), 2)
            if abs(diff_km) > 5:
                estado = "REVISAR_DIFERENCIA_KM"
            elif abs(diff_km) > 2:
                estado = "REVISAR_LEVE_KM"
        diff_time = None
        if summary["tiempo_resumen_seg"] is not None:
            diff_time = int(time_calc - summary["tiempo_resumen_seg"])
        sheets.append({
            "hoja": sheet_name,
            "unidad": unit,
            "tipo_hoja": tipo_hoja,
            "mes": mes,
            "anio": anio,
            "km_resumen": summary["km_resumen"],
            "km_calculados": km_calc,
            "diferencia_km": diff_km,
            "tiempo_resumen_seg": summary["tiempo_resumen_seg"],
            "tiempo_calculado_seg": time_calc,
            "diferencia_tiempo_seg": diff_time,
            "movimientos_detectados": len(movements),
            "inmovilizaciones_detectadas": len(all_paradas),
            "hash_movimientos": hash_mov,
            "estado_validacion": estado,
            "score": score,
            "usar": True if tipo_hoja in {"mensual_con_resumen", "solo_movimientos", "sin_movimientos"} else False,
            "movimientos": movements,
            "paradas": all_paradas,
        })

    # de-duplicate sheets with identical movement hashes; keep highest score
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for sh in sheets:
        h = sh.get("hash_movimientos")
        if h:
            by_hash.setdefault(h, []).append(sh)
    for group in by_hash.values():
        if len(group) <= 1:
            continue
        best = max(group, key=lambda x: x.get("score", 0))
        for sh in group:
            if sh is not best:
                sh["usar"] = False
                sh["descartar_motivo"] = f"Duplicada de {best['hoja']}"
        best["descartar_motivo"] = ""

    return {
        "archivo": file_name,
        "sheets": sheets,
        "valid_sheets": [s for s in sheets if s.get("usar")],
        "discarded_sheets": [s for s in sheets if not s.get("usar")],
    }


def seconds_to_human(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(abs(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if sec and not parts:
        parts.append(f"{sec} seg")
    return " ".join(parts) or "0 seg"
