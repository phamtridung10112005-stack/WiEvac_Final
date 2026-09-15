"""Compact dashboard for already-scored EdgeResult packets."""

from __future__ import annotations

from pathlib import Path


_DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


def render_dashboard_html() -> str:
    return _DASHBOARD_PATH.read_text(encoding="utf-8")


def dashboard_routes() -> dict[str, str]:
    return {
        "/v5": render_dashboard_html(),
        "/api/v5/overview": "overview",
        "/api/v5/nodes": "nodes",
        "/api/v5/labels": "labels",
        "/api/v5/recorder": "recorder",
    }
