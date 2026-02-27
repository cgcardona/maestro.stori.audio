"""PromptServiceClient — fetch random prompts with JWT auth."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from tourdeforce.config import TDFConfig
from tourdeforce.models import (
    Component, EventType, Prompt, Severity, TraceContext,
    sha256_payload, stable_hash,
)
from tourdeforce.collectors.events import EventCollector
from tourdeforce.collectors.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class PromptServiceClient:
    """Fetches prompts from the backend and deterministically selects one."""

    def __init__(
        self,
        config: TDFConfig,
        event_collector: EventCollector,
        metrics: MetricsCollector,
        payload_dir: Path,
    ) -> None:
        self._config = config
        self._events = event_collector
        self._metrics = metrics
        self._payload_dir = payload_dir / "prompt_fetch"
        self._payload_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(timeout=config.prompt_fetch_timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_and_select(
        self,
        run_id: str,
        seed: int,
        trace: TraceContext,
    ) -> Prompt:
        """Fetch random prompts and deterministically select one."""
        span = trace.new_span("prompt_fetch")

        await self._events.emit(
            run_id=run_id,
            scenario="prompt_fetch",
            component=Component.PROMPT_SERVICE,
            event_type=EventType.HTTP_REQUEST,
            trace=trace,
            data={"url": self._config.prompt_endpoint, "method": "GET"},
        )

        async with self._metrics.timer("prompt_fetch", run_id):
            resp = await self._client.get(
                self._config.prompt_endpoint,
                headers=self._config.auth_headers,
            )

        raw_body = resp.text
        resp_hash = sha256_payload(raw_body)

        await self._events.emit(
            run_id=run_id,
            scenario="prompt_fetch",
            component=Component.PROMPT_SERVICE,
            event_type=EventType.HTTP_RESPONSE,
            trace=trace,
            severity=Severity.INFO if resp.status_code == 200 else Severity.ERROR,
            data={
                "status": resp.status_code,
                "hash": resp_hash,
                "size_bytes": len(raw_body),
            },
        )

        # Persist raw response
        out_file = self._payload_dir / f"{run_id}_response.json"
        out_file.write_text(raw_body)

        if resp.status_code != 200:
            trace.end_span()
            raise PromptFetchError(
                f"Prompt fetch failed: HTTP {resp.status_code} — {raw_body[:500]}"
            )

        body = resp.json()
        prompts = self._parse_prompts(body)

        if not prompts:
            trace.end_span()
            raise PromptFetchError("No prompts returned from endpoint")

        # Deterministic selection
        prompt_ids = sorted(p.id for p in prompts)
        idx = stable_hash(prompt_ids, seed) % len(prompts)
        selected = prompts[idx]

        logger.info(
            "Selected prompt %s (index %d of %d) for run %s",
            selected.id[:12], idx, len(prompts), run_id,
        )

        # Persist selection
        selection_file = self._payload_dir / f"{run_id}_selected.json"
        selection_file.write_text(json.dumps({
            "selected_id": selected.id,
            "selected_index": idx,
            "all_ids": prompt_ids,
            "seed": seed,
            "text": selected.text,
        }, indent=2))

        trace.end_span()
        return selected

    def _parse_prompts(self, body: Any) -> list[Prompt]:
        """Adapt to the actual response shape — auto-detect format.

        The Maestro UI endpoint returns { prompts: [{ id, title, preview, fullPrompt }] }.
        We use fullPrompt as the prompt text (complete MAESTRO PROMPT YAML).
        """
        if isinstance(body, dict) and "prompts" in body:
            return [
                Prompt(
                    id=p.get("id", f"p_{i}"),
                    text=_extract_text(p),
                    metadata={
                        k: v for k, v in p.items()
                        if k not in ("id", "text", "prompt", "fullPrompt", "full_prompt")
                    },
                )
                for i, p in enumerate(body["prompts"])
            ]

        if isinstance(body, list):
            return [
                Prompt(
                    id=p.get("id", f"p_{i}") if isinstance(p, dict) else f"p_{i}",
                    text=_extract_text(p) if isinstance(p, dict) else str(p),
                    metadata=p.get("metadata", {}) if isinstance(p, dict) else {},
                )
                for i, p in enumerate(body)
            ]

        logger.warning("Unexpected prompt response shape: %s", type(body).__name__)
        if isinstance(body, dict):
            return [Prompt(id="p_0", text=_extract_text(body))]

        return []


def _extract_text(p: dict) -> str:
    """Extract prompt text from a response item, trying all known field names."""
    return (
        p.get("fullPrompt")
        or p.get("full_prompt")
        or p.get("text")
        or p.get("prompt")
        or ""
    )


class PromptFetchError(Exception):
    pass
