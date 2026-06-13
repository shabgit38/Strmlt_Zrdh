from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).parent / "portfolio_terminal" / "dist"

_portfolio_terminal = components.declare_component(
    "portfolio_terminal",
    path=str(_COMPONENT_DIR),
)


def render_portfolio_terminal(snapshot: dict[str, Any], *, key: str | None = None) -> None:
    _portfolio_terminal(snapshot=snapshot, key=key, default=None)
