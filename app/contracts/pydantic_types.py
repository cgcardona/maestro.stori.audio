"""Pydantic-compatible JSON type primitives and boundary converters.

## Why this module exists

``JSONValue`` (from ``app.contracts.json_types``) is a recursive type alias
with string forward-references (``list["JSONValue"]``, ``dict[str, "JSONValue"]``).
Pydantic v2 cannot resolve these implicit recursive aliases at schema generation
time — it raises ``RecursionError``.  The fix is a *named* recursive
``RootModel`` subclass that Pydantic resolves via ``model_rebuild()``.

## Entity catalog

``PydanticJson``
    Named recursive ``RootModel`` — use in every Pydantic ``BaseModel`` field
    that must hold arbitrary JSON.  This is the only type that Pydantic can
    generate a valid schema for when the value is recursive.

``unwrap(v)``
    ``PydanticJson`` → ``JSONValue``.  Pydantic→internal boundary.
    Recurses into lists and dicts; no ``cast()`` needed.

``unwrap_dict(d)``
    ``dict[str, PydanticJson]`` → ``dict[str, JSONValue]``.
    The standard conversion for Pydantic ``arguments``-style fields.

``wrap(v)``
    ``JSONValue`` → ``PydanticJson``.  Internal→Pydantic boundary.
    Recurses into lists and dicts; the inverse of ``unwrap``.

``wrap_dict(d)``
    ``dict[str, JSONValue]`` → ``dict[str, PydanticJson]``.
    The standard conversion for passing internal pipeline data into Pydantic fields.

## Usage patterns

**Pydantic model field (inbound — request body):**

    class MyRequest(CamelModel):
        arguments: dict[str, PydanticJson] = {}

    # Inside the route handler — cross the boundary once:
    args: dict[str, JSONValue] = unwrap_dict(req.arguments)

**Pydantic model field (outbound — response body):**

    class MyResponse(CamelModel):
        params: dict[str, PydanticJson]

    # Inside the handler — wrap internal data before constructing the model:
    resp = MyResponse(params=wrap_dict(tool_params))

**Rule:** ``PydanticJson`` stays inside Pydantic models.  ``JSONValue`` /
``JSONObject`` stay inside internal code.  ``wrap``/``unwrap`` cross the
boundary exactly once per request/response.
"""

from __future__ import annotations

from pydantic import RootModel

from app.contracts.json_types import JSONValue


class PydanticJson(RootModel[
    str | int | float | bool | None
    | list["PydanticJson"]
    | dict[str, "PydanticJson"]
]):
    """Named recursive Pydantic JSON type — the only safe recursive JSON field type.

    **Why a ``RootModel``?**  ``JSONValue`` is a plain recursive type alias.
    Pydantic v2 resolves ``RootModel`` subclasses by name via ``model_rebuild()``;
    it cannot resolve implicit recursive string forward-references at schema
    generation time.  Using ``PydanticJson`` avoids the ``RecursionError`` that
    occurs whenever ``JSONValue`` appears in a Pydantic ``BaseModel`` field.

    **Usage:** Use as a Pydantic field type anywhere a Pydantic model must hold
    arbitrary JSON.  Access the underlying Python value via ``.root``, or
    convert to ``JSONValue`` once (at the Pydantic→internal boundary) using
    ``unwrap()`` or ``unwrap_dict()``.

    **Never index ``PydanticJson.root`` directly in internal code** — call
    ``unwrap()`` first to get a plain ``JSONValue`` that mypy understands.
    """

    model_config = {"arbitrary_types_allowed": False}


# Pydantic must resolve forward-references (``"PydanticJson"``) at import time.
# Without this call the class is incomplete and validation will fail at runtime.
PydanticJson.model_rebuild()


def unwrap(v: PydanticJson) -> JSONValue:
    """Convert a single ``PydanticJson`` to a ``JSONValue``.

    This is the Pydantic→internal boundary conversion.  Because ``PydanticJson``
    is a ``RootModel`` and Pydantic wraps list/dict elements as ``PydanticJson``
    instances, we must recurse to produce a plain ``JSONValue`` tree.

    No ``cast`` or ``type: ignore`` needed: mypy can trace each branch of the
    ``PydanticJson.root`` union to a valid ``JSONValue`` arm.
    """
    raw = v.root
    if raw is None or isinstance(raw, (str, int, float, bool)):
        return raw
    if isinstance(raw, list):
        # raw: list[PydanticJson] — each element is a PydanticJson
        result_list: list[JSONValue] = [unwrap(item) for item in raw]
        return result_list
    # raw: dict[str, PydanticJson]
    result_dict: dict[str, JSONValue] = {k: unwrap(val) for k, val in raw.items()}
    return result_dict


def unwrap_dict(d: dict[str, PydanticJson]) -> dict[str, JSONValue]:
    """Unwrap a ``dict[str, PydanticJson]`` to ``dict[str, JSONValue]``.

    The designated conversion point for Pydantic BaseModel ``arguments``-style
    fields into internal pipeline types.  Callers receive a clean
    ``dict[str, JSONValue]`` with no further coercion needed.

    Example::

        from app.contracts.pydantic_types import unwrap_dict

        class MyRoute(APIRouter):
            async def handle(self, req: MyRequest) -> Response:
                args: dict[str, JSONValue] = unwrap_dict(req.arguments)
                return await server.call_tool(req.name, args)
    """
    return {k: unwrap(v) for k, v in d.items()}


def wrap(v: JSONValue) -> PydanticJson:
    """Wrap a plain ``JSONValue`` recursively into a ``PydanticJson``.

    This is the internal→Pydantic boundary conversion — the exact inverse of
    ``unwrap``.  Must recurse into lists and dicts because ``PydanticJson``
    expects its children to also be ``PydanticJson`` instances.

    Use ``wrap_dict`` for the common case of converting a ``dict[str, JSONValue]``
    field into ``dict[str, PydanticJson]``.

    Example::

        stats = CheckoutExecutionStats(
            events=[wrap_dict(e) for e in execution.events],
        )
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return PydanticJson(v)
    if isinstance(v, list):
        return PydanticJson([wrap(item) for item in v])
    # v: dict[str, JSONValue]
    return PydanticJson({k: wrap(val) for k, val in v.items()})


def wrap_dict(d: dict[str, JSONValue]) -> dict[str, PydanticJson]:
    """Wrap a ``dict[str, JSONValue]`` into ``dict[str, PydanticJson]``.

    The internal→Pydantic boundary conversion for ``arguments``-style dicts.
    Use when passing internal pipeline data into Pydantic BaseModel fields.
    """
    return {k: wrap(v) for k, v in d.items()}
