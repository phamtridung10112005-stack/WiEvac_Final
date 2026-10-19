"""Compact dashboard for already-scored EdgeResult packets."""

from __future__ import annotations

from pathlib import Path


_DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


def render_dashboard_html() -> str:
    return _DASHBOARD_PATH.read_text(encoding="utf-8")


def dashboard_source_bundle() -> str:
    """HTML plus every dashboard script, so contract tests see logic that moved out of the page."""
    root = _DASHBOARD_PATH.parent / "static" / "js"
    parts = [render_dashboard_html()]
    for path in sorted(root.rglob("*.js")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def dashboard_routes() -> dict[str, str]:
    return {
        "/v5": render_dashboard_html(),
        "/api/v5/overview": "overview",
        "/api/v5/nodes": "nodes",
        "/api/v5/labels": "labels",
        "/api/v5/recorder": "recorder",
    }
