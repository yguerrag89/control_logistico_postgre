from __future__ import annotations

import runpy
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
LEGACY_PAGES_DIR = APP_DIR / "pages_legacy"


def run_legacy_page(filename: str) -> None:
    """Execute a legacy Streamlit page inside a consolidated page section."""
    path = LEGACY_PAGES_DIR / filename
    if not path.exists():
        st.error(f"No se encontró la página heredada: {filename}")
        return
    runpy.run_path(str(path), run_name=f"__legacy_{filename.replace('.', '_')}")
