from __future__ import annotations

import streamlit as st


def render_alerts(errors: list[str], warnings: list[str]) -> None:
    for e in errors:
        st.error(e)
    for w in warnings:
        st.warning(w)


def validation_badge(status: str) -> str:
    status = (status or "").upper()
    if status == "VALIDADO":
        return "✅ VALIDADO"
    if status == "PENDIENTE_VALIDACION":
        return "🟠 PENDIENTE"
    if status == "REVISAR":
        return "🔎 REVISAR"
    return f"ℹ️ {status}"
