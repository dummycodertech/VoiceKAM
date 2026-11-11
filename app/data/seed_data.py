"""Built-in synthetic demo dataset entry points (delegates to Scenarios A-D)."""

from __future__ import annotations

from app.data.context import SellerContext
from app.data.scenarios import build_scenario, list_all_scenarios


def get_scenario_context(scenario_id: str | None = None) -> SellerContext:
    """Return a fresh deterministic SellerContext for scenario A, B, C, or D."""
    return build_scenario(scenario_id)


def list_scenarios() -> list[dict[str, str]]:
    """Return UI metadata for all built-in demo scenarios (A-D)."""
    return list_all_scenarios()
