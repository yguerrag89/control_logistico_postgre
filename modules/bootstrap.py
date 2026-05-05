from __future__ import annotations

import json
from pathlib import Path

from modules.db import APP_DIR, get_connection


def bootstrap_if_needed() -> None:
    seed_path = APP_DIR / "data" / "seed" / "seed_data.json"
    with get_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM unidades").fetchone()["n"]
        if existing > 0:
            return

        seed = json.loads(seed_path.read_text(encoding="utf-8"))

        for item in seed["units"]:
            conn.execute(
                '''
                INSERT INTO unidades (
                    placas, marca, modelo, color, tipo_unidad,
                    combustible_preferido, tipo_carga, carga_garrafones,
                    periodo_habil, limite_litros, activo
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    item.get("placas"),
                    item.get("marca"),
                    item.get("modelo"),
                    item.get("color"),
                    item.get("tipo_unidad"),
                    item.get("combustible_preferido"),
                    item.get("tipo_carga"),
                    item.get("carga_garrafones"),
                    item.get("periodo_habil"),
                    item.get("limite_litros"),
                    item.get("activo", 1),
                ),
            )

        for conductor in seed.get("conductores", []):
            conn.execute(
                "INSERT OR IGNORE INTO conductores (nombre, activo) VALUES (?, ?)",
                (conductor["nombre"], conductor.get("activo", 1)),
            )

        for placas, items in seed.get("inventory_checklist", {}).items():
            unidad = conn.execute(
                "SELECT id FROM unidades WHERE placas = ?",
                (placas,),
            ).fetchone()
            if not unidad:
                continue
            for entry in items:
                conn.execute(
                    "INSERT INTO checklist_unidad (unidad_id, item, valor) VALUES (?, ?, ?)",
                    (unidad["id"], entry["item"], entry["valor"]),
                )

        conn.commit()
