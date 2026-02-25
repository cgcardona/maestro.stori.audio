"""Shared Pydantic base with camelCase wire-format serialization."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def to_camel(name: str) -> str:
    """Convert snake_case to camelCase for JSON serialization."""
    parts = name.split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:])


class CamelModel(BaseModel):
    """Base model that serializes to camelCase on the wire.

    - Python code uses snake_case field names (PEP 8)
    - JSON on the wire uses camelCase (web convention)
    - ``model_dump()`` returns snake_case (internal use)
    - ``model_dump(by_alias=True)`` returns camelCase (wire use)
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
