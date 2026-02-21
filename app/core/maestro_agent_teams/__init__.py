"""Agent Teams — parallel instrument execution for Maestro."""

from app.core.maestro_agent_teams.constants import _CC_NAMES
from app.core.maestro_agent_teams.agent import _run_instrument_agent
from app.core.maestro_agent_teams.summary import (
    _build_composition_summary,
    _compose_summary_text,
)
from app.core.maestro_agent_teams.coordinator import _handle_composition_agent_team

__all__ = [
    "_CC_NAMES",
    "_run_instrument_agent",
    "_build_composition_summary",
    "_compose_summary_text",
    "_handle_composition_agent_team",
]
