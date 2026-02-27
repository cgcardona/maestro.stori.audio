"""DAW adapter layer — ports-and-adapters boundary for client integrations.

Maestro core depends on ``DAWAdapter`` (the port), never on a concrete
adapter package.  Only DI/wiring code imports ``app.daw.stori``.
"""
from __future__ import annotations
