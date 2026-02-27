"""Agent Teams — three-level parallel instrument execution for Maestro.

Architecture:
  Level 1 — Coordinator (coordinator.py)
  Level 2 — Instrument Parent (agent.py)
  Level 3 — Section Child (section_agent.py)
"""
from __future__ import annotations

from maestro.core.maestro_agent_teams.constants import _CC_NAMES
from maestro.core.maestro_agent_teams.agent import _run_instrument_agent
from maestro.core.maestro_agent_teams.signals import SectionSignals, SectionState
from maestro.core.maestro_agent_teams.section_agent import (
    _run_section_child,
    SectionResult,
)
from maestro.core.telemetry import SectionTelemetry, compute_section_telemetry
from maestro.core.maestro_agent_teams.summary import (
    _build_composition_summary,
    _compose_summary_text,
)
from maestro.core.maestro_agent_teams.coordinator import _handle_composition_agent_team

__all__ = [
    "_CC_NAMES",
    "_run_instrument_agent",
    "_run_section_child",
    "SectionResult",
    "SectionSignals",
    "SectionState",
    "SectionTelemetry",
    "compute_section_telemetry",
    "_build_composition_summary",
    "_compose_summary_text",
    "_handle_composition_agent_team",
]
