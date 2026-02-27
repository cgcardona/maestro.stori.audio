"""CLI interface for Tour de Force harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from tourdeforce import __version__

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tourdeforce",
        description="Stori Tour de Force — end-to-end integration harness for Maestro x Orpheus x MUSE",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ───────────────────────────────────────────────────────────────
    run_parser = sub.add_parser("run", help="Execute Tour de Force runs")
    run_parser.add_argument("--jwt-env", default="JWT", help="Env var containing JWT (default: JWT)")
    run_parser.add_argument("--prompt-endpoint", default=None, help="Prompt fetch endpoint URL")
    run_parser.add_argument("--maestro", default=None, help="Maestro stream endpoint URL")
    run_parser.add_argument("--storpheus", default=None, help="Storpheus base URL")
    run_parser.add_argument("--muse-url", default=None, help="MUSE API base URL")
    run_parser.add_argument("--muse-root", default="./muse_repo", help="MUSE local repo root")
    run_parser.add_argument("--runs", type=int, default=10, help="Number of runs (default: 10)")
    run_parser.add_argument("--seed", type=int, default=1337, help="Random seed (default: 1337)")
    run_parser.add_argument("--concurrency", type=int, default=4, help="Max concurrent runs (default: 4)")
    run_parser.add_argument("--out", default="", help="Output directory (default: auto-generated)")
    run_parser.add_argument("--quality-preset", default="balanced", choices=["fast", "balanced", "quality"])
    run_parser.add_argument("--maestro-timeout", type=float, default=180.0, help="Maestro stream timeout (s)")
    run_parser.add_argument("--storpheus-timeout", type=float, default=180.0, help="Storpheus job timeout (s)")
    run_parser.add_argument("--global-timeout", type=float, default=300.0, help="Global run timeout (s)")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # ── report ────────────────────────────────────────────────────────────
    report_parser = sub.add_parser("report", help="Generate report from existing artifacts")
    report_parser.add_argument("--in", dest="input_dir", required=True, help="Artifact directory to analyze")

    # ── replay ────────────────────────────────────────────────────────────
    replay_parser = sub.add_parser("replay", help="Replay a specific run from artifacts")
    replay_parser.add_argument("--run-id", required=True, help="Run ID to replay")
    replay_parser.add_argument("--in", dest="input_dir", required=True, help="Artifact directory")

    args = parser.parse_args()

    if args.command == "run":
        return _cmd_run(args)
    elif args.command == "report":
        return _cmd_report(args)
    elif args.command == "replay":
        return _cmd_replay(args)

    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    """Execute Tour de Force runs."""
    from tourdeforce.config import TDFConfig
    from tourdeforce.runner import Runner
    from tourdeforce.report import ReportBuilder

    try:
        config = TDFConfig.from_cli(
            jwt_env=args.jwt_env,
            prompt_endpoint=args.prompt_endpoint,
            maestro=args.maestro,
            storpheus=args.storpheus,
            muse_base_url=args.muse_url,
            muse_root=args.muse_root,
            runs=args.runs,
            seed=args.seed,
            concurrency=args.concurrency,
            out=args.out,
            quality_preset=args.quality_preset,
            maestro_timeout=args.maestro_timeout,
            storpheus_timeout=args.storpheus_timeout,
            global_timeout=args.global_timeout,
        )
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    runner = Runner(config)
    results = asyncio.run(runner.run_all())

    # Generate report
    report_builder = ReportBuilder(results, config.output_path)
    html_path = report_builder.build()

    print(f"\nTour de Force complete!")
    print(f"  Runs: {len(results)}")
    print(f"  Successful: {sum(1 for r in results if r.status.value == 'success')}")
    print(f"  Artifacts: {config.output_path}")
    print(f"  Report: {html_path}")

    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Generate report from existing artifact directory."""
    from tourdeforce.models import RunResult, RunStatus

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Artifact directory not found: {input_dir}", file=sys.stderr)
        return 1

    runs_file = input_dir / "runs.jsonl"
    if not runs_file.exists():
        print(f"runs.jsonl not found in {input_dir}", file=sys.stderr)
        return 1

    # Reconstruct RunResults from JSONL
    results: list[RunResult] = []
    for line in runs_file.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        r = RunResult(
            run_id=data.get("run_id", ""),
            status=RunStatus(data.get("status", "maestro_error")),
            start_ts=data.get("start_ts", ""),
            end_ts=data.get("end_ts", ""),
            duration_ms=data.get("duration_ms", 0),
            seed=data.get("seed", 0),
            scenario=data.get("scenario", ""),
            intent=data.get("intent", ""),
            storpheus_note_count=data.get("note_count", 0),
            midi_metrics={"quality_score": data.get("quality_score", 0)},
            error_message=data.get("error", ""),
        )
        results.append(r)

    from tourdeforce.report import ReportBuilder
    builder = ReportBuilder(results, input_dir)
    html_path = builder.build()
    print(f"Report generated: {html_path}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a run from persisted payloads."""
    input_dir = Path(args.input_dir)
    run_id = args.run_id

    # Load SSE stream
    sse_file = input_dir / "payloads" / "maestro_sse" / f"{run_id}_sse_parsed.jsonl"
    if not sse_file.exists():
        print(f"SSE data not found for run {run_id}", file=sys.stderr)
        return 1

    print(f"Replaying run {run_id}...")
    print(f"SSE events:")

    for line in sse_file.read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event_type = event.get("type", "unknown")
        seq = event.get("seq", -1)
        print(f"  [{seq:>4}] {event_type}")

        if event_type == "toolCall":
            data = event.get("data", {})
            print(f"         tool: {data.get('name', '?')} | params: {json.dumps(data.get('params', {}))[:100]}")
        elif event_type == "state":
            data = event.get("data", {})
            print(f"         state={data.get('state')} intent={data.get('intent')} mode={data.get('executionMode')}")
        elif event_type == "complete":
            data = event.get("data", {})
            print(f"         success={data.get('success')} traceId={data.get('traceId', '')[:16]}")

    # Load request
    req_file = input_dir / "payloads" / "maestro_requests" / f"{run_id}_request.json"
    if req_file.exists():
        req = json.loads(req_file.read_text())
        print(f"\nRequest:")
        print(f"  Prompt: {req.get('prompt', '')[:200]}")
        print(f"  Mode: {req.get('mode', '')}")

    # Load MIDI summary
    midi_file = input_dir / "midi" / f"run_{run_id}" / "midi_summary.json"
    if midi_file.exists():
        midi = json.loads(midi_file.read_text())
        print(f"\nMIDI Metrics:")
        print(f"  Notes: {midi.get('note_count_total', 0)}")
        print(f"  Quality: {midi.get('quality_score', 0):.1f}")
        print(f"  Entropy: {midi.get('pitch_class_entropy', 0):.2f}")

    return 0
