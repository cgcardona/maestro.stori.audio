"""Tour de Force configuration — all knobs in one place."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TDFConfig:
    """Immutable configuration for a Tour de Force run."""

    # Endpoints — defaults use Docker Compose service names (resolved via maestro-stori-net).
    # TDF runs inside the maestro container; both maestro and storpheus resolve via Docker DNS.
    prompt_endpoint: str = "http://maestro:10001/api/v1/prompts/random"
    maestro_url: str = "http://maestro:10001/api/v1/maestro/stream"
    storpheus_url: str = "http://storpheus:10002"
    muse_base_url: str = "http://maestro:10001/api/v1/muse"

    # Auth
    jwt: str = ""

    # Run parameters
    runs: int = 10
    seed: int = 1337
    concurrency: int = 4

    # Timeouts (seconds)
    prompt_fetch_timeout: float = 10.0
    maestro_stream_timeout: float = 180.0
    storpheus_job_timeout: float = 180.0
    global_run_timeout: float = 300.0

    # Concurrency limits
    storpheus_semaphore: int = 2
    maestro_semaphore: int = 4

    # Quality
    quality_preset: str = "balanced"

    # Output
    out_dir: str = ""

    # MUSE
    muse_root: str = "./muse_repo"
    muse_project_id: str = "tourdeforce-project"

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt}"}

    @property
    def output_path(self) -> Path:
        if self.out_dir:
            return Path(self.out_dir)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return Path(f"/data/tdf_{ts}")

    @classmethod
    def from_cli(
        cls,
        *,
        jwt_env: str = "STORI_JWT",
        prompt_endpoint: str | None = None,
        maestro: str | None = None,
        storpheus: str | None = None,
        muse_base_url: str | None = None,
        muse_root: str = "./muse_repo",
        runs: int = 10,
        seed: int = 1337,
        concurrency: int = 4,
        out: str = "",
        quality_preset: str = "balanced",
        maestro_timeout: float = 180.0,
        storpheus_timeout: float = 180.0,
        global_timeout: float = 300.0,
    ) -> TDFConfig:
        jwt = os.environ.get(jwt_env, "")
        if not jwt:
            raise ValueError(
                f"JWT not found in environment variable {jwt_env!r}. "
                f"set it with: export {jwt_env}=<your-token>"
            )

        return cls(
            prompt_endpoint=prompt_endpoint or cls.prompt_endpoint,
            maestro_url=maestro or cls.maestro_url,
            storpheus_url=storpheus or cls.storpheus_url,
            muse_base_url=muse_base_url or cls.muse_base_url,
            jwt=jwt,
            runs=runs,
            seed=seed,
            concurrency=concurrency,
            out_dir=out,
            muse_root=muse_root,
            quality_preset=quality_preset,
            maestro_stream_timeout=maestro_timeout,
            storpheus_job_timeout=storpheus_timeout,
            global_run_timeout=global_timeout,
        )
