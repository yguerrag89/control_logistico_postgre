from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

try:
    import pytesseract
except Exception:
    pytesseract = None


DEFAULT_TESSERACT_PATHS = [
    os.getenv("TESSERACT_CMD"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

GAS_STATIONS = ["MOBIL", "PEMEX", "BP", "G500", "SHELL", "TOTAL", "REPSOL", "AKRON", "OXXO GAS"]
FUEL_ALIASES = {
    "MAGNA": "Magna",
    "PREMIUM": "Premium",
    "DIESEL": "Diésel",
    "DIESEL": "Diésel",
    "DIÉSEL": "Diésel",
    "REGULAR": "Magna",
}
OCR_CONFIGS = [
    "--oem 3 --psm 6",
    "--oem 3 --psm 4",
    "--oem 3 --psm 11",
]


for candidate in DEFAULT_TESSERACT_PATHS:
    if pytesseract and candidate and os.path.exists(candidate):
        pytesseract.pytesseract.tesseract_cmd = candidate
        break


@dataclass
class OCRCandidate:
    variant: str
    config: str
    raw_text: str
    fields: dict[str, Any]
    warnings: list[str]
    score: int


def ocr_available() -> bool:
    if pytesseract is None:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_status() -> tuple[bool, str]:
    if pytesseract is None:
        return False, "No está instalado el paquete pytesseract en este entorno de Python."
    try:
        version = pytesseract.get_tesseract_version()
        return True, f"OCR disponible. Tesseract detectado: {version}"
    except Exception as exc:
        return False, f"pytesseract está instalado, pero Tesseract no está accesible. Detalle: {exc}"


def preprocess_variants(image_path: str) -> dict[str, Image.Image]:
    image = Image.open(image_path)
    image = ImageOps.exif_transpose(image)

    variants: dict[str, Image.Image] = {}

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    gray = gray.resize((max(1, gray.width * 2), max(1, gray.height * 2)), Image.Resampling.LANCZOS)
    variants["gray"] = gray

    sharp = gray.filter(ImageFilter.SHARPEN)
    variants["sharp"] = sharp

    if cv2 is None or np is None:
        return variants

    arr = np.array(gray)
    arr = _deskew_array(arr)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(arr)
    variants["clahe"] = Image.fromarray(clahe)

    blurred = cv2.GaussianBlur(clahe, (3, 3), 0)
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants["otsu"] = Image.fromarray(otsu)

    adaptive = cv2.adaptiveThreshold(
        clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        15,
    )
    variants["adaptive"] = Image.fromarray(adaptive)

    morph = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, np.ones((1, 1), np.uint8))
    variants["adaptive_open"] = Image.fromarray(morph)

    return variants


def read_ticket(
    image_path: str,
    known_plates: list[str] | None = None,
    selected_plate: str | None = None,
    preferred_fuel: str | None = None,
    unit_limit_liters: float | None = None,
) -> dict[str, Any]:
    available, status_msg = ocr_status()
    if not available:
        return {
            "ok": False,
            "error": status_msg,
            "raw_text": "",
            "fields": {},
            "warnings": [status_msg],
            "debug": [],
        }

    variants = preprocess_variants(image_path)
    candidates: list[OCRCandidate] = []
    errors: list[str] = []

    for variant_name, image in variants.items():
        for config in OCR_CONFIGS:
            try:
                raw_text = pytesseract.image_to_string(image, lang="eng+spa", config=config)
            except Exception as exc:
                errors.append(f"{variant_name} | {config}: {exc}")
                continue

            fields, warnings, score = parse_ticket_text(raw_text)
            candidates.append(
                OCRCandidate(
                    variant=variant_name,
                    config=config,
                    raw_text=raw_text,
                    fields=fields,
                    warnings=warnings,
                    score=score,
                )
            )

    if not candidates:
        detail = errors[0] if errors else "No se generaron resultados OCR."
        return {
            "ok": False,
            "error": detail,
            "raw_text": "",
            "fields": {},
            "warnings": ["No se pudo leer el ticket."],
            "debug": [],
        }

    best = max(candidates, key=lambda c: c.score)
    fields = dict(best.fields)
    warnings = list(best.warnings)

    matched_plate, plate_score = _match_known_plate(fields.get("placas_detectadas"), known_plates or [])
    if matched_plate:
        fields["placas_sugeridas"] = matched_plate
        if not fields.get("placas_detectadas") or plate_score >= 0.86:
            fields["placas_detectadas"] = matched_plate
    elif selected_plate:
        fields.setdefault("placas_sugeridas", selected_plate)

    if selected_plate and fields.get("placas_detectadas") and fields["placas_detectadas"] != selected_plate:
        warnings.append(
            f"Las placas detectadas ({fields['placas_detectadas']}) no coinciden con la unidad elegida ({selected_plate})."
        )
    elif selected_plate and not fields.get("placas_detectadas"):
        warnings.append(f"No se detectaron placas con claridad. Se sugiere conservar la unidad elegida ({selected_plate}).")

    if preferred_fuel and fields.get("tipo_combustible") and fields["tipo_combustible"] != preferred_fuel:
        warnings.append(
            f"El combustible leído ({fields['tipo_combustible']}) no coincide con el preferido de la unidad ({preferred_fuel})."
        )

    liters = fields.get("litros")
    price = fields.get("precio_litro")
    total = fields.get("importe_total")
    if liters and price and total:
        estimated = round(float(liters) * float(price), 2)
        if abs(estimated - float(total)) > 2.5:
            warnings.append(f"El importe no cuadra con litros × precio. Esperado aprox.: {estimated:.2f}")

    if unit_limit_liters and liters and float(liters) > float(unit_limit_liters):
        warnings.append(f"Los litros detectados ({liters}) superan el límite esperado de la unidad ({unit_limit_liters}).")

    debug = [
        {
            "variant": c.variant,
            "config": c.config,
            "score": c.score,
            "fields": c.fields,
            "warnings": c.warnings,
            "raw_text": c.raw_text,
        }
        for c in sorted(candidates, key=lambda c: c.score, reverse=True)
    ]

    return {
        "ok": True,
        "error": None,
        "raw_text": best.raw_text,
        "fields": fields,
        "warnings": warnings,
        "debug": debug,
        "best_variant": best.variant,
        "best_config": best.config,
        "best_score": best.score,
    }


def parse_ticket_text(text: str) -> tuple[dict[str, Any], list[str], int]:
    normalized_text = normalize_ocr_text(text)
    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    full_text = "\n".join(lines)
    flat_text = " ".join(lines)

    warnings: list[str] = []
    fields: dict[str, Any] = {}

    fecha = _extract_date(flat_text)
    hora = _extract_time(flat_text)
    plate_raw = _extract_plate(lines, flat_text)
    folio = _extract_folio(lines, flat_text)
    km = _extract_km(lines, flat_text)
    litros = _extract_liters(lines, flat_text)
    precio = _extract_price(lines, flat_text)
    importe = _extract_total(lines, flat_text)
    combustible = _extract_fuel(flat_text)
    gasolinera = _extract_station(flat_text)

    if fecha:
        fields["fecha_carga"] = fecha
    if hora:
        fields["hora_carga"] = hora
    if plate_raw:
        fields["placas_detectadas"] = plate_raw
    if folio:
        fields["ticket_folio"] = folio
    if km is not None:
        fields["kilometraje"] = km
    if litros is not None:
        fields["litros"] = round(litros, 2)
    if precio is not None:
        fields["precio_litro"] = round(precio, 2)
    if importe is not None:
        fields["importe_total"] = round(importe, 2)
    if combustible:
        fields["tipo_combustible"] = combustible
    if gasolinera:
        fields["gasolinera"] = gasolinera

    for key in ["fecha_carga", "litros", "precio_litro", "importe_total"]:
        if key not in fields:
            warnings.append(f"No se detectó {key}.")

    score = _score_result(fields, flat_text, warnings)
    return fields, warnings, score


def normalize_ocr_text(text: str) -> str:
    text = text.upper()
    replacements = {
        "Ó": "O",
        "Á": "A",
        "É": "E",
        "Í": "I",
        "Ú": "U",
        "LTS.": "LITROS",
        "LTS": "LITROS",
        "LT.": "LITROS",
        "LT ": "LITROS ",
        "LITRO ": "LITROS ",
        "PRECIO/LT": "PRECIO LITRO",
        "PRECIO X LITRO": "PRECIO LITRO",
        "P/U": "PRECIO",
        "P. U.": "PRECIO",
        "IMP. TOTAL": "TOTAL",
        "KMS": "KM",
        "ODOMETRO": "ODOMETRO",
        "$ ": "$",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    text = re.sub(r"[|]{2,}", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _deskew_array(arr):
    if cv2 is None or np is None:
        return arr
    inv = cv2.bitwise_not(arr)
    coords = np.column_stack(np.where(inv > 0))
    if coords.size == 0:
        return arr
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return arr
    h, w = arr.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _extract_date(text: str) -> str | None:
    patterns = [
        r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b",
        r"\b(\d{4}[/-]\d{2}[/-]\d{2})\b",
        r"\b(\d{2}[/-]\d{2}[/-]\d{2})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return _normalize_date(m.group(1))
    return None


def _extract_time(text: str) -> str | None:
    m = re.search(r"\b(\d{2}:\d{2}:\d{2}|\d{2}:\d{2})\b", text)
    return m.group(1) if m else None


def _extract_plate(lines: list[str], text: str) -> str | None:
    for line in lines:
        if any(keyword in line for keyword in ["PLACA", "PLACAS", "PLACA:"]):
            candidate = _plate_from_text(line)
            if candidate:
                return candidate
    return _plate_from_text(text)


def _extract_folio(lines: list[str], text: str) -> str | None:
    patterns = [r"(?:FOLIO|TICKET|TRANSACCION|TRANSACCION|VENTA|FACTURA)\s*[:#-]?\s*([A-Z0-9-]{4,})"]
    for line in lines:
        for pattern in patterns:
            m = re.search(pattern, line)
            if m:
                return m.group(1)
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _extract_km(lines: list[str], text: str) -> int | None:
    value = _first_number_after_keywords(lines, ["KM", "KILOMETRAJE", "ODOMETRO", "ODOMETRO", "ODOMETRO"])
    if value is not None:
        return value
    for line in lines:
        if "KM" in line:
            nums = [int(n) for n in re.findall(r"\b\d{4,7}\b", line)]
            if nums:
                return max(nums)
    return None


def _extract_liters(lines: list[str], text: str) -> float | None:
    value = _first_decimal_after_keywords(lines, ["LITROS", "LITRO", "LTS", "LT"])
    if value is not None:
        return value
    values = _all_decimals(text)
    plausible = [v for v in values if 1 <= v <= 250]
    return plausible[0] if plausible else None


def _extract_price(lines: list[str], text: str) -> float | None:
    value = _first_decimal_after_keywords(lines, ["PRECIO LITRO", "PRECIO", "PPU", "$/L", "$ /L", "COSTO"])
    if value is not None:
        return value
    values = _all_decimals(text)
    plausible = [v for v in values if 5 <= v <= 40]
    return plausible[0] if plausible else None


def _extract_total(lines: list[str], text: str) -> float | None:
    patterns = [r"(?:TOTAL|IMPORTE|VENTA|PAGO)\s*[:$#-]?\s*\$?\s*(\d+[\.,]\d{2,3})"]
    for line in lines:
        for pattern in patterns:
            m = re.search(pattern, line)
            if m:
                return _to_float(m.group(1))

    values = _all_decimals(text)
    plausible = [v for v in values if 50 <= v <= 50000]
    return max(plausible) if plausible else None


def _extract_fuel(text: str) -> str | None:
    for raw, clean in FUEL_ALIASES.items():
        if re.search(rf"\b{re.escape(raw)}\b", text):
            return clean
    return None


def _extract_station(text: str) -> str | None:
    for station in GAS_STATIONS:
        if station in text:
            return station
    return None


def _plate_from_text(text: str) -> str | None:
    tokens = re.findall(r"[A-Z0-9-]{5,10}", text)
    for token in tokens:
        candidate = _normalize_plate_candidate(token)
        if re.fullmatch(r"[A-Z]{3}\d{4}", candidate):
            return candidate
        if re.fullmatch(r"[A-Z]{2}\d{5}", candidate):
            return candidate
        if re.fullmatch(r"[A-Z]{3}\d{3}[A-Z]", candidate):
            return candidate
    return None


def _normalize_plate_candidate(token: str) -> str:
    token = re.sub(r"[^A-Z0-9]", "", token.upper())
    if len(token) < 6:
        return token

    prefixes = [2, 3]
    possibilities = []
    for prefix_len in prefixes:
        if len(token) < prefix_len + 3:
            continue
        prefix = token[:prefix_len]
        suffix = token[prefix_len:]
        prefix = prefix.translate(str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}))
        suffix = suffix.translate(str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}))
        possibilities.append(prefix + suffix)

    possibilities.append(token)
    possibilities.sort(key=len, reverse=True)
    return possibilities[0]


def _match_known_plate(detected: str | None, known_plates: list[str]) -> tuple[str | None, float]:
    if not detected or not known_plates:
        return None, 0.0
    detected_clean = re.sub(r"[^A-Z0-9]", "", detected.upper())
    best_plate = None
    best_score = 0.0
    for plate in known_plates:
        plate_clean = re.sub(r"[^A-Z0-9]", "", plate.upper())
        score = SequenceMatcher(None, detected_clean, plate_clean).ratio()
        if score > best_score:
            best_score = score
            best_plate = plate
    return best_plate, best_score


def _first_decimal_after_keywords(lines: list[str], keywords: list[str]) -> float | None:
    pattern = r"(\d+[\.,]\d{1,3})"
    for line in lines:
        for keyword in keywords:
            if keyword in line:
                m = re.search(pattern, line)
                if m:
                    return _to_float(m.group(1))
    return None


def _first_number_after_keywords(lines: list[str], keywords: list[str]) -> int | None:
    for line in lines:
        for keyword in keywords:
            if keyword in line:
                numbers = re.findall(r"\b\d{2,7}\b", line)
                if numbers:
                    return max(int(n) for n in numbers)
    return None


def _all_decimals(text: str) -> list[float]:
    values: list[float] = []
    for match in re.findall(r"\d+[\.,]\d{1,3}", text):
        try:
            values.append(_to_float(match))
        except Exception:
            pass
    return values


def _to_float(token: str) -> float:
    return float(token.replace(",", "."))


def _normalize_date(date_str: str) -> str:
    parts = re.split(r"[/-]", date_str)
    if len(parts[0]) == 4:
        y, m, d = parts
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    if len(parts[2]) == 2:
        year = int(parts[2])
        year += 2000 if year < 70 else 1900
        d, m = parts[0], parts[1]
        return f"{year}-{m.zfill(2)}-{d.zfill(2)}"
    d, m, y = parts
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _score_result(fields: dict[str, Any], text: str, warnings: list[str]) -> int:
    score = 0
    weights = {
        "fecha_carga": 10,
        "hora_carga": 6,
        "placas_detectadas": 14,
        "ticket_folio": 6,
        "kilometraje": 8,
        "litros": 12,
        "precio_litro": 12,
        "importe_total": 14,
        "tipo_combustible": 8,
        "gasolinera": 4,
    }
    for key, weight in weights.items():
        if fields.get(key) not in (None, ""):
            score += weight

    keyword_bonus = 0
    for keyword in ["TOTAL", "LITROS", "PRECIO", "KM", "PLACAS", "TICKET", "FOLIO"]:
        if keyword in text:
            keyword_bonus += 2
    score += keyword_bonus
    score -= len(warnings)

    if all(fields.get(k) is not None for k in ["litros", "precio_litro", "importe_total"]):
        estimated = round(float(fields["litros"]) * float(fields["precio_litro"]), 2)
        if abs(estimated - float(fields["importe_total"])) <= 2.5:
            score += 8

    return score
