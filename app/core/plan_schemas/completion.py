"""Plan completion — infer missing tracks/regions and prune empty ones."""

from __future__ import annotations

import logging
from typing import Optional

from app.core.plan_schemas.models import EditStep, ExecutionPlanSchema, GenerationStep

logger = logging.getLogger(__name__)


def infer_edits_from_generations(generations: list[GenerationStep]) -> list[EditStep]:
    """
    Infer required track/region edits from generation steps.

    Used when the LLM only provided generations without explicit edit steps.
    """
    edits: list[EditStep] = []
    seen_tracks: set[str] = set()

    for gen in generations:
        track_name = gen.role.capitalize()
        if track_name.lower() not in seen_tracks:
            edits.append(EditStep(action="add_track", name=track_name))
            seen_tracks.add(track_name.lower())
        edits.append(EditStep(action="add_region", track=track_name, barStart=0, bars=gen.bars))

    return edits


def _find_track_for_role(role: str, existing_tracks: set[str]) -> Optional[str]:
    """Find an existing track that fuzzy-matches a generation role."""
    role_lower = role.lower()
    if role_lower in existing_tracks:
        return role_lower
    for track in existing_tracks:
        if role_lower in track:
            return track
    return None


def complete_plan(plan: ExecutionPlanSchema) -> ExecutionPlanSchema:
    """
    Complete a partial plan by inferring missing tracks/regions and removing
    tracks that have no corresponding generation (prevents empty tracks).

    Uses fuzzy matching so a track named "Jam Drums" is recognized as a
    match for the "drums" generation role.
    """
    if not plan.generations:
        return plan

    generation_roles = {gen.role.lower() for gen in plan.generations}

    existing_tracks: set[str] = set()
    track_name_map: dict[str, str] = {}
    existing_regions: dict[str, int] = {}

    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            lower_name = edit.name.lower()
            existing_tracks.add(lower_name)
            track_name_map[lower_name] = edit.name
        elif edit.action == "add_region" and edit.track and edit.bars:
            existing_regions[edit.track.lower()] = edit.bars

    inferred_edits: list[EditStep] = []

    for gen in plan.generations:
        matching_track = _find_track_for_role(gen.role, existing_tracks)

        if matching_track:
            original_name = track_name_map.get(matching_track, gen.role.capitalize())
            track_lower = matching_track
            logger.debug(f"📋 Found existing track '{original_name}' for role '{gen.role}'")
        else:
            track_name = gen.role.capitalize()
            track_lower = track_name.lower()
            inferred_edits.append(EditStep(action="add_track", name=track_name))
            existing_tracks.add(track_lower)
            track_name_map[track_lower] = track_name
            original_name = track_name
            logger.debug(f"📋 Inferred missing track: {track_name}")

        if track_lower not in existing_regions or existing_regions[track_lower] != gen.bars:
            inferred_edits.append(EditStep(action="add_region", track=original_name, barStart=0, bars=gen.bars))
            existing_regions[track_lower] = gen.bars
            logger.debug(f"📋 Inferred missing region for {original_name}: {gen.bars} bars")

    # Remove tracks that have no corresponding generation
    filtered_edits: list[EditStep] = []
    removed_tracks: list[str] = []
    removed_track_lowers: set[str] = set()

    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            track_lower = edit.name.lower()
            has_generation = any(
                role in track_lower or track_lower in role
                for role in generation_roles
            )
            if has_generation:
                filtered_edits.append(edit)
            else:
                removed_tracks.append(edit.name)
                removed_track_lowers.add(track_lower)
                logger.warning(f"🗑️ Removing track '{edit.name}' - no corresponding generation")
        elif edit.action == "add_region" and edit.track:
            if edit.track.lower() not in removed_track_lowers:
                filtered_edits.append(edit)
            else:
                logger.warning(f"🗑️ Removing region for removed track '{edit.track}'")
        else:
            filtered_edits.append(edit)

    if removed_tracks:
        logger.info(f"🗑️ Removed {len(removed_tracks)} empty tracks: {removed_tracks}")

    if inferred_edits or removed_tracks:
        logger.info(f"📋 Plan completion: +{len(inferred_edits)} inferred, -{len(removed_tracks)} removed")
        all_edits = filtered_edits + inferred_edits
        filtered_mix = [
            s for s in plan.mix
            if s.track.lower() not in [t.lower() for t in removed_tracks]
        ]
        if len(filtered_mix) < len(plan.mix):
            logger.info(f"🗑️ Removed {len(plan.mix) - len(filtered_mix)} mix steps for empty tracks")
        return ExecutionPlanSchema(generations=plan.generations, edits=all_edits, mix=filtered_mix, explanation=plan.explanation)

    return plan
