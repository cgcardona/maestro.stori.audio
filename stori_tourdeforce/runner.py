"""Runner — orchestrates end-to-end Tour de Force scenarios with concurrency."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from stori_tourdeforce import __version__
from stori_tourdeforce.config import TDFConfig
from stori_tourdeforce.models import (
    Component, Event, EventType, MidiMetrics, Prompt, RunResult, RunStatus,
    Severity, TraceContext, make_run_id, sha256_payload, stable_hash,
)
from stori_tourdeforce.clients.prompt import PromptFetchError, PromptServiceClient
from stori_tourdeforce.clients.maestro import MaestroClient, MaestroError, MaestroResult
from stori_tourdeforce.clients.orpheus import OrpheusClient, OrpheusError
from stori_tourdeforce.clients.muse import CheckoutResult, MergeResult, MuseClient, MuseError
from stori_tourdeforce.collectors.events import EventCollector
from stori_tourdeforce.collectors.logs import LogCollector
from stori_tourdeforce.collectors.metrics import MetricsCollector
from stori_tourdeforce.analyzers.midi import analyze_tool_call_notes
from stori_tourdeforce.analyzers.graph import GraphAnalyzer
from stori_tourdeforce.analyzers.run import RunAnalyzer
from stori_tourdeforce.scenarios import (
    CheckoutStep, ConflictBranchSpec, Scenario, Wave, build_edit_stori_prompt,
    get_scenario,
)

logger = logging.getLogger(__name__)

_TDF_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _conv_uuid(run_id: str, suffix: str = "") -> str:
    """Deterministic UUID from run_id — valid for Maestro's conversationId."""
    return str(uuid.uuid5(_TDF_NAMESPACE, f"{run_id}:{suffix}"))


class Runner:
    """Orchestrates Tour de Force runs with concurrency and fault tolerance."""

    def __init__(self, config: TDFConfig) -> None:
        self._config = config
        self._output = config.output_path
        self._output.mkdir(parents=True, exist_ok=True)

        # Directories
        self._payload_dir = self._output / "payloads"
        self._midi_dir = self._output / "midi"
        self._muse_dir = self._output / "muse"
        self._report_dir = self._output / "report"
        for d in [self._payload_dir, self._midi_dir, self._muse_dir, self._report_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Collectors
        self._log_collector = LogCollector(self._output)
        self._event_collector = EventCollector(self._output)
        self._metrics = MetricsCollector(self._output)

        # Clients
        self._prompt_client = PromptServiceClient(
            config, self._event_collector, self._metrics, self._payload_dir,
        )
        self._maestro_client = MaestroClient(
            config, self._event_collector, self._metrics, self._payload_dir,
        )
        self._orpheus_client = OrpheusClient(
            config, self._event_collector, self._metrics, self._payload_dir, self._midi_dir,
        )
        self._muse_client = MuseClient(
            config, self._event_collector, self._metrics, self._payload_dir, self._muse_dir,
        )

        # Semaphores
        self._maestro_sem = asyncio.Semaphore(config.maestro_semaphore)
        self._orpheus_sem = asyncio.Semaphore(config.orpheus_semaphore)

        # Results
        self._results: list[RunResult] = []

    async def run_all(self) -> list[RunResult]:
        """Run all scenarios with concurrency control."""
        logger.info(
            "Starting Tour de Force: %d runs, concurrency=%d, seed=%d",
            self._config.runs, self._config.concurrency, self._config.seed,
        )

        # Save config
        config_file = self._output / "config.json"
        config_file.write_text(json.dumps({
            "runs": self._config.runs,
            "seed": self._config.seed,
            "concurrency": self._config.concurrency,
            "maestro_url": self._config.maestro_url,
            "orpheus_url": self._config.orpheus_url,
            "muse_base_url": self._config.muse_base_url,
            "quality_preset": self._config.quality_preset,
            "maestro_timeout": self._config.maestro_stream_timeout,
            "orpheus_timeout": self._config.orpheus_job_timeout,
        }, indent=2))

        # Run with concurrency
        sem = asyncio.Semaphore(self._config.concurrency)
        tasks = []
        for i in range(self._config.runs):
            run_id = make_run_id(i)
            run_seed = self._config.seed + i
            scenario = get_scenario(i, self._config.seed)

            async def _run_with_sem(rid: str, rseed: int, scen: Scenario) -> RunResult:
                async with sem:
                    return await self._execute_run(rid, rseed, scen)

            tasks.append(_run_with_sem(run_id, run_seed, scenario))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, RunResult):
                self._results.append(r)
            elif isinstance(r, Exception):
                logger.error("Run failed with exception: %s", r)

        # Cleanup
        await self._prompt_client.close()
        await self._maestro_client.close()
        await self._orpheus_client.close()
        await self._muse_client.close()

        # Post-processing
        await self._generate_manifest()

        logger.info(
            "Tour de Force complete: %d/%d successful",
            sum(1 for r in self._results if r.status == RunStatus.SUCCESS),
            len(self._results),
        )

        return self._results

    async def _execute_run(self, run_id: str, seed: int, scenario: Scenario) -> RunResult:
        """Execute a single run with full error handling."""
        trace = TraceContext()
        trace.new_span("run")
        start_ts = Event.now()
        start_time = time.monotonic()

        result = RunResult(
            run_id=run_id,
            status=RunStatus.SUCCESS,
            seed=seed,
            start_ts=start_ts,
            scenario=scenario.name,
        )

        await self._event_collector.emit(
            run_id=run_id,
            scenario=scenario.name,
            component=Component.CLIENT,
            event_type=EventType.RUN_START,
            trace=trace,
            data={"seed": seed, "scenario": scenario.name},
        )

        try:
            result = await asyncio.wait_for(
                self._run_scenario(run_id, seed, scenario, trace, result),
                timeout=self._config.global_run_timeout,
            )
        except asyncio.TimeoutError:
            result.status = RunStatus.TIMEOUT
            result.error_type = "timeout"
            result.error_message = f"Global timeout ({self._config.global_run_timeout}s)"
            logger.error("Run %s timed out", run_id)
        except PromptFetchError as e:
            result.status = RunStatus.PROMPT_ERROR
            result.error_type = "prompt_fetch"
            result.error_message = str(e)
            logger.error("Run %s prompt fetch failed: %s", run_id, e)
        except MaestroError as e:
            result.status = RunStatus.MAESTRO_ERROR
            result.error_type = "maestro"
            result.error_message = str(e)
            logger.error("Run %s Maestro error: %s", run_id, e)
        except OrpheusError as e:
            result.status = RunStatus.ORPHEUS_ERROR
            result.error_type = "orpheus"
            result.error_message = str(e)
            logger.error("Run %s Orpheus error: %s", run_id, e)
        except MuseError as e:
            result.status = RunStatus.MUSE_ERROR
            result.error_type = "muse"
            result.error_message = str(e)
            logger.error("Run %s MUSE error: %s", run_id, e)
        except Exception as e:
            result.status = RunStatus.MAESTRO_ERROR
            result.error_type = "unexpected"
            result.error_message = str(e)
            logger.exception("Run %s unexpected error: %s", run_id, e)

        result.end_ts = Event.now()
        result.duration_ms = (time.monotonic() - start_time) * 1000

        # Persist last SSE events on failure
        if result.status != RunStatus.SUCCESS and result.sse_events:
            result.last_sse_events = [
                {"type": e.event_type, "seq": e.seq, "data": e.data}
                for e in result.sse_events[-10:]
            ]

        await self._event_collector.emit(
            run_id=run_id,
            scenario=scenario.name,
            component=Component.CLIENT,
            event_type=EventType.RUN_END,
            trace=trace,
            severity=Severity.INFO if result.status == RunStatus.SUCCESS else Severity.ERROR,
            data={
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "error": result.error_message or None,
            },
        )

        await self._event_collector.emit_run({
            "run_id": run_id,
            "status": result.status.value,
            "seed": seed,
            "scenario": scenario.name,
            "duration_ms": result.duration_ms,
            "start_ts": result.start_ts,
            "end_ts": result.end_ts,
            "prompt_id": result.prompt.id if result.prompt else "",
            "intent": result.intent,
            "orpheus_job_id": result.orpheus_job_id,
            "note_count": result.orpheus_note_count,
            "quality_score": result.midi_metrics.get("quality_score", 0) if result.midi_metrics else 0,
            "muse_commits": len(result.muse_commit_ids),
            "muse_merges": len(result.muse_merge_ids),
            "error": result.error_message or None,
        })

        logger.info(
            "Run %s: %s (%.1fs, %d notes, quality=%.0f)",
            run_id, result.status.value, result.duration_ms / 1000,
            result.orpheus_note_count,
            result.midi_metrics.get("quality_score", 0) if result.midi_metrics else 0,
        )

        return result

    async def _run_scenario(
        self,
        run_id: str,
        seed: int,
        scenario: Scenario,
        trace: TraceContext,
        result: RunResult,
    ) -> RunResult:
        """Execute the full scenario: fetch → compose → analyze → branch → merge → conflict → checkout."""

        # ── Step 1: Fetch prompt ──────────────────────────────────────────
        prompt = await self._prompt_client.fetch_and_select(run_id, seed, trace)
        result.prompt = prompt

        # ── Step 2: Compose via Maestro ───────────────────────────────────
        async with self._maestro_sem:
            maestro_result = await self._maestro_client.compose(
                run_id=run_id,
                prompt_text=prompt.text,
                trace=trace,
                conversation_id=_conv_uuid(run_id),
            )

        result.sse_events = maestro_result.events
        result.tool_calls = maestro_result.tool_calls
        result.maestro_trace_id = maestro_result.trace_id
        result.intent = maestro_result.intent
        result.execution_mode = maestro_result.execution_mode
        result.payload_hashes["maestro_request"] = maestro_result.payload_hash

        if not maestro_result.success:
            result.status = RunStatus.MAESTRO_ERROR
            result.error_message = maestro_result.complete.get("error", "Stream not successful")
            return result

        # ── Step 3: Extract Orpheus data from tool calls ──────────────────
        notes = self._extract_notes_from_tool_calls(maestro_result.tool_calls)
        result.orpheus_note_count = len(notes)

        # ── Step 4: Analyze MIDI quality ──────────────────────────────────
        if notes:
            midi_metrics = analyze_tool_call_notes(notes)
            result.midi_metrics = midi_metrics.to_dict()

            run_midi_dir = self._midi_dir / f"run_{run_id}"
            run_midi_dir.mkdir(parents=True, exist_ok=True)
            summary_file = run_midi_dir / "midi_summary.json"
            summary_file.write_text(json.dumps(result.midi_metrics, indent=2, default=str))

            await self._event_collector.emit(
                run_id=run_id,
                scenario=scenario.name,
                component=Component.CLIENT,
                event_type=EventType.MIDI_METRIC,
                trace=trace,
                data=result.midi_metrics,
            )

            await self._metrics.gauge("midi.quality_score", run_id, midi_metrics.quality_score)
            await self._metrics.gauge("midi.note_count", run_id, float(midi_metrics.note_count_total))

        # ── Step 4b: Download Orpheus artifacts (WAV, MIDI, plot) ─────────
        result.artifact_files = await self._download_orpheus_artifacts(run_id, maestro_result)

        # ── Step 5: MUSE commit (initial compose) — C1 ────────────────────
        tracks = self._extract_tracks_from_tool_calls(maestro_result.tool_calls)
        regions = self._extract_regions_from_tool_calls(maestro_result.tool_calls)

        c1_id = await self._muse_client.save_variation(
            run_id=run_id,
            trace=trace,
            intent="compose",
            affected_tracks=[t["name"] for t in tracks],
            affected_regions=[r["id"] for r in regions] if regions else [],
            conversation_id=_conv_uuid(run_id),
        )
        await self._muse_client.set_head(run_id, trace, c1_id)
        result.muse_commit_ids.append(c1_id)

        # Symbolic ref map for checkout traversal
        ref_map: dict[str, str] = {"C1": c1_id}

        # ── Steps 6-9: Execute waves (N edit-branch-merge cycles) ─────────
        wave_parent = c1_id
        carried_commits: dict[str, str] = {}
        merge_counter = 0

        compose_ctx = self._extract_compose_context(prompt.text)
        accumulated_tool_calls: list[dict[str, Any]] = list(maestro_result.tool_calls)

        for wave_idx, wave in enumerate(scenario.waves):
            wave_commits: dict[str, str] = {}

            for edit_step in wave.edits:
                try:
                    edit_cid, edit_mr = await self._run_edit_branch(
                        run_id=run_id,
                        trace=trace,
                        scenario=scenario,
                        parent_commit_id=wave_parent,
                        edit_step=edit_step,
                        accumulated_tool_calls=accumulated_tool_calls,
                        compose_ctx=compose_ctx,
                    )
                    accumulated_tool_calls.extend(edit_mr.tool_calls)
                    edit_notes = self._extract_notes_from_tool_calls(edit_mr.tool_calls)
                    result.orpheus_note_count += len(edit_notes)
                    wave_commits[edit_step.branch_name] = edit_cid
                    result.muse_commit_ids.append(edit_cid)
                    result.muse_branch_names.append(edit_step.branch_name)
                    ref_map[f"C{len(ref_map)+1}_{edit_step.branch_name}"] = edit_cid
                except (MaestroError, MuseError) as e:
                    logger.warning("Edit branch %s failed for run %s: %s", edit_step.branch_name, run_id, e)

            all_available = {**wave_commits, **carried_commits}

            if wave.merge and len(all_available) >= 2:
                left = all_available.get(wave.merge.left_branch)
                right = all_available.get(wave.merge.right_branch)
                if left and right:
                    merge_counter += 1
                    merge_ref = f"M{merge_counter}"
                    mr = await self._muse_client.merge(
                        run_id=run_id, trace=trace, left_id=left, right_id=right,
                    )
                    await self._event_collector.emit(
                        run_id=run_id,
                        scenario=scenario.name,
                        component=Component.MUSE,
                        event_type=EventType.MUSE_MERGE,
                        trace=trace,
                        tags={"merge_type": f"wave_{wave_idx}", "merge_ref": merge_ref},
                        data={
                            "left": left, "right": right,
                            "success": mr.success,
                            "conflict_count": len(mr.conflicts),
                        },
                    )
                    if mr.success:
                        result.muse_merge_ids.append(mr.merge_variation_id)
                        ref_map[merge_ref] = mr.merge_variation_id
                        wave_parent = mr.merge_variation_id
                        logger.info(
                            "Clean merge %s succeeded: %s (run %s)",
                            merge_ref, mr.merge_variation_id[:8], run_id,
                        )
                    else:
                        logger.warning(
                            "Wave %d merge conflict in run %s: %d conflicts",
                            wave_idx + 1, run_id, len(mr.conflicts),
                        )
                        await self._record_conflict(run_id, trace, scenario, mr)
            elif wave_commits:
                first_cid = next(iter(wave_commits.values()))
                wave_parent = first_cid

            carried_commits = {}
            if wave.carry_over:
                for name in wave.carry_over:
                    cid = all_available.get(name)
                    if cid:
                        carried_commits[name] = cid

        # ── Step 10: Deliberate conflict merge ────────────────────────────
        if scenario.conflict_spec:
            await self._run_conflict_exercise(
                run_id=run_id,
                trace=trace,
                scenario=scenario,
                parent_commit_id=wave_parent,
                conflict_spec=scenario.conflict_spec,
                accumulated_tool_calls=accumulated_tool_calls,
                result=result,
                ref_map=ref_map,
            )

        # ── Step 11: Checkout traversal ───────────────────────────────────
        if scenario.checkout_traversal:
            await self._run_checkout_traversal(
                run_id=run_id,
                trace=trace,
                scenario=scenario,
                ref_map=ref_map,
                result=result,
            )

        # ── Step 12: Drift detection test ─────────────────────────────────
        if scenario.test_drift_detection and len(result.muse_commit_ids) >= 2:
            await self._run_drift_test(
                run_id=run_id,
                trace=trace,
                scenario=scenario,
                ref_map=ref_map,
                result=result,
            )

        # ── Step 13: Export final graph ────────────────────────────────────
        try:
            graph_data = await self._muse_client.get_log(run_id, trace)
            graph_analyzer = GraphAnalyzer(graph_data)
            run_graph_dir = self._muse_dir / f"run_{run_id}"
            run_graph_dir.mkdir(parents=True, exist_ok=True)
            graph_analyzer.export_ascii(run_graph_dir / "graph.txt")
            graph_analyzer.export_graph_json(run_graph_dir / "graph_viz.json")

            await self._event_collector.emit(
                run_id=run_id,
                scenario=scenario.name,
                component=Component.MUSE,
                event_type=EventType.TIMING,
                trace=trace,
                tags={"operation": "graph_export"},
                data=graph_analyzer.to_metrics(),
            )
        except Exception as e:
            logger.warning("Graph export failed for run %s: %s", run_id, e)

        return result

    async def _run_conflict_exercise(
        self,
        run_id: str,
        trace: TraceContext,
        scenario: Scenario,
        parent_commit_id: str,
        conflict_spec: ConflictBranchSpec,
        accumulated_tool_calls: list[dict[str, Any]],
        result: RunResult,
        ref_map: dict[str, str],
    ) -> None:
        """Create two branches that deliberately conflict, attempt merge, and record outcome."""
        logger.info("Running conflict exercise for run %s", run_id)
        snapshot = self._build_project_snapshot(accumulated_tool_calls)

        # Branch A: edit keys region one way
        try:
            async with self._maestro_sem:
                edit_a = await self._maestro_client.edit(
                    run_id=f"{run_id}_conflict_a",
                    edit_prompt=conflict_spec.branch_a_prompt,
                    trace=trace,
                    project=snapshot,
                    conversation_id=_conv_uuid(run_id),
                )
            ca_id = await self._muse_client.save_variation(
                run_id=run_id,
                trace=trace,
                intent=f"conflict_branch_a:{conflict_spec.branch_a_name}",
                parent_variation_id=parent_commit_id,
                affected_tracks=[conflict_spec.target_track],
                affected_regions=[conflict_spec.target_region],
                conversation_id=_conv_uuid(run_id),
            )
            await self._muse_client.set_head(run_id, trace, ca_id)
            result.muse_commit_ids.append(ca_id)
            result.muse_branch_names.append(conflict_spec.branch_a_name)
            ref_map[f"conflict_{conflict_spec.branch_a_name}"] = ca_id
        except (MaestroError, MuseError) as e:
            logger.warning("Conflict branch A failed: %s", e)
            return

        # Branch B: edit same keys region a different way
        try:
            async with self._maestro_sem:
                edit_b = await self._maestro_client.edit(
                    run_id=f"{run_id}_conflict_b",
                    edit_prompt=conflict_spec.branch_b_prompt,
                    trace=trace,
                    project=snapshot,
                    conversation_id=_conv_uuid(run_id),
                )
            cb_id = await self._muse_client.save_variation(
                run_id=run_id,
                trace=trace,
                intent=f"conflict_branch_b:{conflict_spec.branch_b_name}",
                parent_variation_id=parent_commit_id,
                affected_tracks=[conflict_spec.target_track],
                affected_regions=[conflict_spec.target_region],
                conversation_id=_conv_uuid(run_id),
            )
            result.muse_commit_ids.append(cb_id)
            result.muse_branch_names.append(conflict_spec.branch_b_name)
            ref_map[f"conflict_{conflict_spec.branch_b_name}"] = cb_id
        except (MaestroError, MuseError) as e:
            logger.warning("Conflict branch B failed: %s", e)
            return

        # Attempt merge — expect conflict (409)
        merge_result = await self._muse_client.merge(
            run_id=run_id, trace=trace, left_id=ca_id, right_id=cb_id,
        )

        await self._event_collector.emit(
            run_id=run_id,
            scenario=scenario.name,
            component=Component.MUSE,
            event_type=EventType.MUSE_MERGE,
            trace=trace,
            tags={"merge_type": "conflict_exercise"},
            data={
                "left": ca_id,
                "right": cb_id,
                "success": merge_result.success,
                "conflict_count": len(merge_result.conflicts),
                "conflicts": merge_result.conflicts,
            },
        )

        if merge_result.success:
            logger.info(
                "Conflict exercise unexpectedly succeeded (run %s) — branches may have been disjoint",
                run_id,
            )
            result.muse_merge_ids.append(merge_result.merge_variation_id)
        else:
            logger.info(
                "Conflict exercise: merge correctly returned %d conflict(s) (run %s)",
                len(merge_result.conflicts), run_id,
            )
            await self._record_conflict(run_id, trace, scenario, merge_result)

    async def _run_checkout_traversal(
        self,
        run_id: str,
        trace: TraceContext,
        scenario: Scenario,
        ref_map: dict[str, str],
        result: RunResult,
    ) -> None:
        """Exercise checkout traversal across the commit DAG."""
        logger.info("Running checkout traversal for run %s (%d steps)", run_id, len(scenario.checkout_traversal))

        for i, step in enumerate(scenario.checkout_traversal):
            target_id = ref_map.get(step.target_ref)
            if target_id is None:
                logger.warning(
                    "Checkout ref %s not found in ref_map (run %s, step %d) — skipping",
                    step.target_ref, run_id, i,
                )
                continue

            co_result = await self._muse_client.checkout(
                run_id=run_id,
                trace=trace,
                target_variation_id=target_id,
                force=step.force,
                conversation_id=_conv_uuid(run_id),
            )

            await self._event_collector.emit(
                run_id=run_id,
                scenario=scenario.name,
                component=Component.MUSE,
                event_type=EventType.MUSE_COMMIT,
                trace=trace,
                tags={"operation": "checkout", "ref": step.target_ref, "force": str(step.force)},
                data=co_result.to_dict(),
            )

            if step.expect_blocked and not co_result.blocked:
                logger.warning(
                    "Expected checkout to be blocked (drift) but it succeeded (run %s, ref %s)",
                    run_id, step.target_ref,
                )
            elif not step.expect_blocked and co_result.blocked:
                logger.warning(
                    "Checkout blocked by drift (run %s, ref %s, severity=%s, changes=%d)",
                    run_id, step.target_ref, co_result.drift_severity, co_result.drift_total_changes,
                )

            logger.info(
                "Checkout step %d/%d: ref=%s, success=%s, blocked=%s, executed=%d (run %s)",
                i + 1, len(scenario.checkout_traversal),
                step.target_ref, co_result.success, co_result.blocked,
                co_result.executed, run_id,
            )

    async def _run_drift_test(
        self,
        run_id: str,
        trace: TraceContext,
        scenario: Scenario,
        ref_map: dict[str, str],
        result: RunResult,
    ) -> None:
        """Test drift detection by attempting a non-force checkout.

        MUSE returns 409 when the working tree has uncommitted changes relative to HEAD.
        We first force-checkout to the latest commit, then attempt a non-force
        checkout to an earlier commit to observe drift behavior.
        """
        if len(ref_map) < 2:
            return

        refs = list(ref_map.items())
        latest_ref, latest_id = refs[-1]
        earliest_ref, earliest_id = refs[0]

        # Force-checkout to latest
        await self._muse_client.checkout(
            run_id=run_id, trace=trace, target_variation_id=latest_id,
            force=True, conversation_id=_conv_uuid(run_id),
        )

        # Non-force checkout to earliest — may be blocked by drift
        co_result = await self._muse_client.checkout(
            run_id=run_id, trace=trace, target_variation_id=earliest_id,
            force=False, conversation_id=_conv_uuid(run_id),
        )

        await self._event_collector.emit(
            run_id=run_id,
            scenario=scenario.name,
            component=Component.MUSE,
            event_type=EventType.MUSE_COMMIT,
            trace=trace,
            tags={"operation": "drift_test", "force": "false"},
            data={
                "target": earliest_ref,
                "blocked": co_result.blocked,
                "drift_severity": co_result.drift_severity,
                "drift_total_changes": co_result.drift_total_changes,
            },
        )

        if co_result.blocked:
            logger.info(
                "Drift test: checkout correctly blocked (severity=%s, changes=%d) — run %s",
                co_result.drift_severity, co_result.drift_total_changes, run_id,
            )
            # Force-checkout to recover
            recovery = await self._muse_client.checkout(
                run_id=run_id, trace=trace, target_variation_id=earliest_id,
                force=True, conversation_id=_conv_uuid(run_id),
            )
            logger.info(
                "Drift test: force recovery %s (run %s)",
                "succeeded" if recovery.success else "failed", run_id,
            )
        else:
            logger.info("Drift test: no drift detected (run %s)", run_id)

    async def _record_conflict(
        self,
        run_id: str,
        trace: TraceContext,
        scenario: Scenario,
        merge_result: MergeResult,
    ) -> None:
        """Record merge conflict details as an artifact."""
        conflict_file = self._muse_dir / f"run_{run_id}" / "merge_conflicts.json"
        conflict_file.parent.mkdir(parents=True, exist_ok=True)
        conflict_file.write_text(json.dumps({
            "run_id": run_id,
            "conflict_count": len(merge_result.conflicts),
            "conflicts": merge_result.conflicts,
        }, indent=2, default=str))

        await self._event_collector.emit(
            run_id=run_id,
            scenario=scenario.name,
            component=Component.MUSE,
            event_type=EventType.ERROR,
            trace=trace,
            severity=Severity.WARN,
            tags={"error_type": "merge_conflict"},
            data={"conflicts": merge_result.conflicts},
        )

    async def _run_edit_branch(
        self,
        run_id: str,
        trace: TraceContext,
        scenario: Scenario,
        parent_commit_id: str,
        edit_step: Any,
        accumulated_tool_calls: list[dict[str, Any]],
        compose_ctx: dict[str, Any],
    ) -> tuple[str, MaestroResult]:
        """Run a single edit branch: STORI PROMPT compose → MUSE commit.

        Returns (commit_id, edit_maestro_result) so callers can accumulate
        the new tool calls and download artifacts.
        """
        branch_run_id = f"{run_id}_{edit_step.branch_name}"

        project_snapshot = self._build_project_snapshot(
            accumulated_tool_calls,
            tempo=compose_ctx.get("tempo", 90),
        )

        stori_prompt = build_edit_stori_prompt(
            request=edit_step.edit_prompt,
            roles=compose_ctx.get("roles", ["bass", "drums"]),
            style=compose_ctx.get("style", "boom bap"),
            key=compose_ctx.get("key", "Am"),
            tempo=compose_ctx.get("tempo", 90),
        )

        async with self._maestro_sem:
            edit_result = await self._maestro_client.compose(
                run_id=branch_run_id,
                prompt_text=stori_prompt,
                trace=trace,
                project=project_snapshot,
                conversation_id=_conv_uuid(run_id),
            )

        # Download artifacts from this edit step
        edit_artifacts = await self._download_orpheus_artifacts(
            f"{run_id}_edit_{edit_step.branch_name}", edit_result,
        )
        if edit_artifacts:
            logger.info(
                "Edit branch %s produced %d artifacts (run %s)",
                edit_step.branch_name, len(edit_artifacts), run_id,
            )

        commit_id = await self._muse_client.save_variation(
            run_id=run_id,
            trace=trace,
            intent=f"edit:{edit_step.edit_type}",
            parent_variation_id=parent_commit_id,
            affected_tracks=[edit_step.target_track],
            conversation_id=_conv_uuid(run_id),
        )
        await self._muse_client.set_head(run_id, trace, commit_id)

        return commit_id, edit_result

    def _extract_notes_from_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Extract notes from addNotes tool calls."""
        notes: list[dict] = []
        for tc in tool_calls:
            if tc.get("name") == "addNotes":
                notes.extend(tc.get("params", {}).get("notes", []))
        return notes

    def _extract_tracks_from_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Extract track info from addMidiTrack tool calls."""
        return [
            tc.get("params", {})
            for tc in tool_calls
            if tc.get("name") == "addMidiTrack"
        ]

    def _extract_regions_from_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Extract region info from addMidiRegion tool calls."""
        return [
            {"id": tc.get("id", ""), **tc.get("params", {})}
            for tc in tool_calls
            if tc.get("name") == "addMidiRegion"
        ]

    @staticmethod
    def _extract_compose_context(prompt_text: str) -> dict[str, Any]:
        """Parse style/key/tempo/roles from a STORI PROMPT for re-use in edits."""
        ctx: dict[str, Any] = {
            "style": "boom bap",
            "key": "Am",
            "tempo": 90,
            "roles": [],
        }
        for line in prompt_text.splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("style:"):
                ctx["style"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("key:"):
                ctx["key"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("tempo:"):
                try:
                    ctx["tempo"] = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lower.startswith("role:"):
                raw = stripped.split(":", 1)[1].strip().strip("[]")
                ctx["roles"] = [r.strip() for r in raw.split(",") if r.strip()]
        return ctx

    @staticmethod
    def _build_project_snapshot(
        tool_calls: list[dict[str, Any]],
        tempo: int = 90,
    ) -> dict[str, Any]:
        """Build a ProjectSnapshot-shaped dict with notes from tool calls.

        Produces the tracks -> regions -> notes hierarchy that
        ``app.protocol.schemas.project.ProjectSnapshot`` validates.
        """
        tracks_by_id: dict[str, dict[str, Any]] = {}
        regions_by_track: dict[str, list[dict[str, Any]]] = {}
        notes_by_region: dict[str, list[dict[str, Any]]] = {}

        for tc in tool_calls:
            name = tc.get("name")
            p = tc.get("params", {})
            if name == "addMidiTrack":
                tid = tc.get("id", p.get("trackId", ""))
                tracks_by_id[tid] = {
                    "id": tid,
                    "name": p.get("name", ""),
                    "gmProgram": p.get("instrument", 0),
                    "regions": [],
                }
            elif name == "addMidiRegion":
                rid = tc.get("id", p.get("regionId", ""))
                tid = p.get("trackId", "")
                regions_by_track.setdefault(tid, []).append({
                    "id": rid,
                    "startBeat": p.get("startBeat", 0),
                    "durationBeats": p.get("lengthBeats", 16),
                    "notes": [],
                })
            elif name == "addNotes":
                rid = p.get("regionId", "")
                for note in p.get("notes", []):
                    notes_by_region.setdefault(rid, []).append({
                        "pitch": note.get("pitch", 60),
                        "startBeat": note.get("startBeat", 0),
                        "durationBeats": note.get("durationBeats", 1),
                        "velocity": note.get("velocity", 100),
                    })

        for tid, track in tracks_by_id.items():
            for region in regions_by_track.get(tid, []):
                region["notes"] = notes_by_region.get(region["id"], [])
                region["noteCount"] = len(region["notes"])
                track["regions"].append(region)

        return {
            "id": "tdf-project",
            "tempo": tempo,
            "tracks": list(tracks_by_id.values()),
        }

    async def _download_orpheus_artifacts(self, run_id: str, maestro_result: MaestroResult) -> list[str]:
        """Download WAV, MIDI, and plot files from Orpheus artifact endpoint."""
        comp_id = maestro_result.trace_id
        if not comp_id:
            logger.debug("No trace_id available — skipping artifact download for %s", run_id)
            return []

        artifact_dir = self._midi_dir / f"run_{run_id}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[str] = []

        try:
            async with httpx.AsyncClient(
                base_url=self._config.orpheus_url,
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0),
            ) as client:
                list_resp = await client.get(f"/artifacts/{comp_id}")
                if list_resp.status_code != 200:
                    logger.debug("Orpheus artifacts not found for comp_id=%s (status %d)", comp_id, list_resp.status_code)
                    return []

                listing = list_resp.json()
                files = listing.get("files", [])
                if not files:
                    logger.debug("No artifact files for comp_id=%s", comp_id)
                    return []

                for filename in files:
                    try:
                        file_resp = await client.get(f"/artifacts/{comp_id}/{filename}")
                        if file_resp.status_code == 200:
                            dest = artifact_dir / filename
                            dest.write_bytes(file_resp.content)
                            downloaded.append(filename)
                            logger.info("Downloaded artifact: %s (%d bytes)", dest.name, len(file_resp.content))
                    except Exception as e:
                        logger.warning("Failed to download artifact %s: %s", filename, e)

        except Exception as e:
            logger.debug("Artifact download failed for run %s: %s", run_id, e)

        return downloaded

    async def _generate_manifest(self) -> None:
        """Generate the manifest.json with run summary."""
        manifest = {
            "version": __version__,
            "config": {
                "runs": self._config.runs,
                "seed": self._config.seed,
                "concurrency": self._config.concurrency,
            },
            "total_runs": len(self._results),
            "successful": sum(1 for r in self._results if r.status == RunStatus.SUCCESS),
            "failed": sum(1 for r in self._results if r.status != RunStatus.SUCCESS),
            "events_recorded": self._event_collector.count,
            "output_dir": str(self._output),
        }
        manifest_file = self._output / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))
