"""Stori DAW adapter — tool vocabulary, validation, and phase mapping.

This package owns the ``stori_*`` tool namespace.  All Stori-specific
tool definitions, metadata registrations, phase groupings, and
validation rules live here.

Maestro core must NOT import from this package directly; use the
``DAWAdapter`` protocol from ``app.daw.ports`` instead.  Only
DI/bootstrap code (e.g. ``app.main``) wires the concrete adapter.
"""
from __future__ import annotations
