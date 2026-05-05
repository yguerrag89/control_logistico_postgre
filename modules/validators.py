from __future__ import annotations

from typing import Any

from modules.calculations import parse_optional_odometer


def validate_charge_payload(payload: dict[str, Any], unit: dict[str, Any], previous_charge: dict[str, Any] | None, duplicate: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    litros = float(payload["litros"])
    precio = float(payload["precio_litro"])
    importe = float(payload["importe_total"])
    km = parse_optional_odometer(payload.get("kilometraje"))

    if litros <= 0:
        errors.append("Los litros deben ser mayores que 0.")
    if precio <= 0:
        errors.append("El precio por litro debe ser mayor que 0.")
    if importe <= 0:
        errors.append("El importe total debe ser mayor que 0.")

    if km is None:
        warnings.append("Kilometraje no capturado. El rendimiento se calculará con GPS cuando existan datos importados para la unidad y el periodo.")

    if previous_charge and km is not None:
        prev_km = parse_optional_odometer(previous_charge.get("kilometraje"))
        if prev_km is not None and km < prev_km:
            warnings.append(f"El kilometraje ({km}) es menor que el último registrado ({prev_km}).")

    preferred = (unit.get("combustible_preferido") or "").strip().lower()
    current_fuel = (payload.get("tipo_combustible") or "").strip().lower()
    if preferred and current_fuel and preferred != current_fuel:
        warnings.append(f"El combustible capturado ({payload.get('tipo_combustible')}) no coincide con la preferencia de la unidad ({unit.get('combustible_preferido')}).")

    tipo_carga = (payload.get("tipo_carga_combustible") or "No especificada").strip()
    limite = unit.get("limite_litros")
    if limite is not None and litros > float(limite):
        warnings.append(f"La carga ({litros} L) supera el límite configurado para la unidad ({limite} L).")
    if limite is not None:
        try:
            if float(limite) > 0 and litros < float(limite) * 0.20 and tipo_carga not in {"Parcial", "Emergencia", "Garrafón", "Aceite", "Aditivo"}:
                warnings.append("La carga parece pequeña para el límite de la unidad. Si fue parcial, marca el tipo de carga como Parcial/Emergencia/Garrafón.")
        except Exception:
            pass
    if tipo_carga in {"Parcial", "Emergencia", "Garrafón"}:
        warnings.append("Carga marcada como no concluyente para rendimiento normal. Se conservará para gasto, pero no debe usarse como ciclo completo.")

    total_estimado = round(litros * precio, 2)
    if abs(total_estimado - importe) > 2.0:
        warnings.append(f"El importe no cuadra con litros × precio. Esperado aprox.: {total_estimado:.2f}")

    if not payload.get("imagen_ticket_path"):
        warnings.append("No se adjuntó foto del ticket.")

    if duplicate:
        warnings.append(f"Posible duplicado del registro #{duplicate['id']}.")

    return errors, warnings
