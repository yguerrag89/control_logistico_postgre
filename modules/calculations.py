from __future__ import annotations

from typing import Any


def compute_totals(litros: float, precio_litro: float) -> float:
    return round(float(litros) * float(precio_litro), 2)


def parse_optional_odometer(value: Any) -> int | None:
    """Return a positive odometer value or None when it was not captured.

    The legacy app used 0 as a placeholder. From this version onward,
    None means "kilometraje no capturado". Values <= 0 are treated as missing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(',', '')
        if not value:
            return None
    try:
        km = int(float(value))
    except (TypeError, ValueError):
        return None
    return km if km > 0 else None


def compute_efficiency(current_odometer: int | None, current_liters: float, previous_odometer: int | None) -> dict[str, Any]:
    result = {
        "km_recorridos": None,
        "rendimiento_km_l": None,
        "costo_por_km": None,
    }
    current_km = parse_optional_odometer(current_odometer)
    previous_km = parse_optional_odometer(previous_odometer)
    if current_km is None or previous_km is None:
        return result
    km = current_km - previous_km
    result["km_recorridos"] = km
    if km > 0 and current_liters and current_liters > 0:
        result["rendimiento_km_l"] = round(km / current_liters, 2)
    return result


def infer_cost_per_km(importe_total: float, km_recorridos: int | float | None) -> float | None:
    if km_recorridos and km_recorridos > 0:
        return round(float(importe_total) / float(km_recorridos), 2)
    return None
