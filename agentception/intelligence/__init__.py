"""AgentCeption intelligence layer — anomaly detection and guard functions.

This package contains domain logic for detecting pipeline problems that require
human intervention. Unlike the readers (data access) or routes (HTTP handlers),
guards reason about the *state* of the pipeline and produce actionable signals.
"""
from __future__ import annotations
