from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None


def cache_data(ttl: int = 60) -> Callable[[F], F]:
    """Small safe wrapper around st.cache_data.

    The same modules are used by CLI migration scripts where Streamlit context
    may not exist. In that case the decorator becomes a no-op.
    """
    def decorator(func: F) -> F:
        if st is None:
            return func
        try:
            return st.cache_data(ttl=ttl, show_spinner=False)(func)  # type: ignore[return-value]
        except Exception:
            return func
    return decorator


def cache_resource(func: F) -> F:
    if st is None:
        return func
    try:
        return st.cache_resource(show_spinner=False)(func)  # type: ignore[return-value]
    except Exception:
        return func


def clear_caches() -> None:
    """Clear Streamlit caches after critical writes, when available."""
    if st is None:
        return
    try:
        st.cache_data.clear()
    except Exception:
        pass
