"""MAESTRO PROMPT section parser for multi-section MIDI composition.

Detects named musical sections (intro, verse, chorus, bridge, outro, build,
drop, breakdown) in the user's MAESTRO PROMPT and maps each to beat ranges so
every instrument agent can generate section-appropriate MIDI regions.

If no sections are detected the full arrangement is returned as one section.
"""

from __future__ import annotations

import re
import logging

from app.contracts.json_types import SectionDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section keyword patterns and their default share of the total arrangement.
# Weights are used when multiple sections are found but no explicit bar counts
# are mentioned for individual sections.
# ---------------------------------------------------------------------------

_SECTION_KEYWORDS: list[tuple[str, str, float]] = [
    # (regex pattern, canonical name, default weight)
    # --- standard pop/rock/electronic ---
    (r"\bintro(?:duction)?\b|\bopening\s+section\b", "intro", 0.15),
    (r"\bverse[s]?\b|\ba[\s-]?section\b", "verse", 0.35),
    (r"\bpre-?chorus\b|\bpre\s+chorus\b|\blift\b", "pre-chorus", 0.10),
    (r"\bchorus\b|\bdrop\b|\bhook\b|\brefrain\b", "chorus", 0.30),
    (r"\bbridge\b|\bbreakdown\b|\bbreakdwn\b|\bmiddle\s+eight\b|\bb[\s-]?section\b", "bridge", 0.10),
    (r"\boutro\b|\bcoda\b|\bfade[\s-]?out\b|\btag\b|\bending\b", "outro", 0.10),
    (r"\bbuild[\s-]?up\b|\bbuild\b|\briser\b|\bascen(?:t|d)\b", "build", 0.15),
    # --- jazz / classical / world ---
    (r"\bhead\b(?!\s*(?:room|phone|set|band|liner|line))", "verse", 0.35),
    (r"\bsolo\b|\bimprov(?:isation)?\b", "solo", 0.25),
    (r"\binterlude\b|\btransition\b|\bturnaround\b", "interlude", 0.10),
    (r"\bvamp\b|\bgroove\b|\block(?:ed)?\s+groove\b", "groove", 0.25),
    (r"\btheme\b|\bmain\s+theme\b|\bmotif\b", "verse", 0.35),
    (r"\bvariation\b|\bvariaci[oó]n\b", "solo", 0.25),
    (r"\brecap(?:itulation)?\b|\breturn\b|\breprise\b", "verse", 0.35),
    # --- cinematic / classical form ---
    (r"\bexposition\b|\bstatement\b", "verse", 0.35),
    (r"\bdevelopment\b", "bridge", 0.10),
    (r"\bclimax\b|\bpeak\b|\bapex\b", "chorus", 0.30),
    (r"\bcadenza\b", "solo", 0.25),
    # --- percussion / DJ / electronic ---
    (r"\bbreak\b(?!\s*(?:fast|through|ing|s\b))", "breakdown", 0.10),
    (r"\briff\b|\bmain\s+riff\b", "verse", 0.35),
    (r"\bfill\b", "build", 0.15),
]

# Phrases that imply structure change even without explicit section names.
_INFERRED_SECTIONS: list[tuple[str, str]] = [
    (r"\bbuilds?\s+up\b|\bgradually\s+(grows?|rises?|intensif|builds?)", "build"),
    (r"\bswells?\b|\bcrescendo\b|\brising\s+(tension|energy|intensity)", "build"),
    (r"\bstripped[\s-]?back\b|\bbare\b|\bminimal(?:ist)?\b", "breakdown"),
    (r"\bcalm\s+(?:section|part|passage)\b|\bquiet\s+(?:section|part)\b|\bstill(?:ness)?\b", "breakdown"),
    (r"\b(full|big|heavy)\s+(drop|hit|section)\b", "chorus"),
    (r"\bexplo(?:des?|sion)\b|\bimpact\b|\bmaximum\s+(energy|intensity)\b", "chorus"),
    (r"\bopening\b|\bsets?\s+the\s+scene\b|\bestablish", "intro"),
    (r"\bclosing\b|\bends?\s+(?:with|on|in)\b|\bending\b|\bwind(?:ing|s)?\s+down\b", "outro"),
    (r"\bfade(?:s)?\s+(away|out|to)\b|\bdiminish", "outro"),
    (r"\btakes?\s+a\s+solo\b|\bimprovise[sd]?\b|\bsoloist\b", "solo"),
    (r"\btransitions?\s+(?:to|into)\b|\bsegue", "interlude"),
]

# Roles that are typically silent or minimal in an intro/breakdown.
_SPARSE_ROLES: set[str] = {"chords", "lead", "melody", "pads", "arp"}

# Map section name → description templates per instrument role.
_SECTION_ROLE_TEMPLATES: dict[str, dict[str, str]] = {
    "intro": {
        "drums": "Sparse, minimal groove — hi-hats and subtle percussion only. Kick-snare enters on bar 4.",
        "bass": "Root-only holding pattern, long notes, minimal movement. Establishes key center.",
        "chords": "Sparse pad chords, very long sustains, no rhythmic stabs. Enters gently.",
        "lead": "Absent or very sparse — a single melodic hint, leave space.",
        "melody": "Absent or a brief 2-bar motif. Hold back for the verse.",
        "pads": "Sustained ambient pad, soft attack, fills harmonic space quietly.",
        "arp": "Quiet arpeggiated figure, low velocity, establishes the mood.",
        "fx": "Atmospheric texture, risers, light noise — sets the scene.",
        "perc": "Absent or subtle shakers only.",
    },
    "verse": {
        "drums": "Mid-energy groove, full pattern but controlled. Snare on 2 and 4, hi-hat driving.",
        "bass": "Active melodic bass line, rhythmically engaging, locks to kick drum.",
        "chords": "Rhythmic chord stabs or comping pattern, mid-density.",
        "lead": "Call-and-response phrasing, 4-bar motifs with breathing space.",
        "melody": "Main melodic phrase, room to breathe between phrases.",
        "pads": "Sustained background chords, supporting the melodic content.",
        "arp": "Active arpeggiated pattern, medium velocity.",
        "fx": "Subtle rhythmic texture, light synth effects.",
        "perc": "Shakers and additional percussion complementing the main groove.",
    },
    "chorus": {
        "drums": "Full energy — all drum elements active, fills at phrase ends, high velocity.",
        "bass": "Driving, rhythmically dense bass line. Locks tightly to kick on all strong beats.",
        "chords": "Full stab chords on upbeats, high energy, maximum density.",
        "lead": "Peak melodic statement — high register, climactic phrase, maximum expressiveness.",
        "melody": "Most energetic phrase — wide range, dense phrasing, fills the frequency space.",
        "pads": "Thick layered pad, full velocity, supports the harmonic climax.",
        "arp": "Fast arpeggiated figure, high velocity, maximum energy.",
        "fx": "Full texture with risers, impacts, and rhythmic effects.",
        "perc": "Full percussion layer — claps, rimshots, shakers, high energy.",
    },
    "bridge": {
        "drums": "Stripped back — only kick and snare, or fully silent. Contrast with chorus.",
        "bass": "Minimal or held notes. Creates space before the final chorus return.",
        "chords": "Sparse long chords. Harmonic shift or new chord colour.",
        "lead": "New melodic idea, different register from verse/chorus. 4-bar statement.",
        "melody": "Contrasting melodic phrase — different contour and register from the verse.",
        "pads": "New pad texture or silence. Creates contrast.",
        "arp": "Absent or slow arpeggiation. Reduce energy.",
        "fx": "Descending filter sweeps, breakdown textures.",
        "perc": "Reduced to minimal — just a quiet shaker or absent.",
    },
    "breakdown": {
        "drums": "Stripped to minimal or silence. Builds tension before the drop.",
        "bass": "Long held notes or silence. Tension.",
        "chords": "Sparse long chords, maximum space. Quiet.",
        "lead": "Absent or a whispered melodic fragment.",
        "melody": "Absent or very sparse single notes.",
        "pads": "Slow swell, very quiet, harmonic tension.",
        "arp": "Absent.",
        "fx": "Riser, tension sweep, preparing for the drop.",
        "perc": "Absent or minimal.",
    },
    "build": {
        "drums": "Progressive density increase — layers enter gradually, velocity rising.",
        "bass": "Increasingly active with rhythmic embellishments building toward the drop.",
        "chords": "Chords tighten in rhythm, density increasing toward the chorus.",
        "lead": "Rising melodic phrase building to the peak.",
        "melody": "Ascending melodic arc building toward climax.",
        "pads": "Swell upward in velocity, increasing harmonic density.",
        "arp": "Tempo of arpeggio accelerates or intensity increases.",
        "fx": "Riser, upward sweep, increasing intensity.",
        "perc": "Progressive percussion build — more elements added bar by bar.",
    },
    "pre-chorus": {
        "drums": "Anticipatory groove, slightly tighter feel than verse, builds toward chorus.",
        "bass": "Rising bass line or rhythmic anticipation of chorus drop.",
        "chords": "Pushing chords, building harmonic tension toward chorus resolution.",
        "lead": "Escalating melodic phrase leading into the chorus hook.",
        "melody": "Bridge phrase between verse and chorus energy.",
        "pads": "Swelling pad, increasing volume and brightness.",
        "arp": "Increasing arpeggio density.",
        "fx": "Rising texture, pre-chorus sweep.",
        "perc": "Building percussion layer.",
    },
    "outro": {
        "drums": "Gradual reduction, elements dropping out one by one. Fade or stop.",
        "bass": "Simplifying back to root notes. Longer sustains.",
        "chords": "Sparse long chords, fading out.",
        "lead": "Final melodic statement or silence.",
        "melody": "Brief farewell phrase or silence.",
        "pads": "Long slow fade to silence.",
        "arp": "Slowing or absent.",
        "fx": "Fade-out texture, reverse reverb tail.",
        "perc": "Dropping elements, minimising to just a shaker or silence.",
    },
    "solo": {
        "drums": "Supportive groove — stays in the pocket, lighter touch. Follows the soloist dynamically.",
        "bass": "Walking or rhythmically supportive pattern. Locks to kick, leaves harmonic space for solo.",
        "chords": "Comping underneath the solo — sparse, responsive to soloist phrasing.",
        "lead": "Improvised solo — full register, virtuosic phrasing, expressive bends and ornaments.",
        "melody": "Improvised solo passage — wide range, call-and-response with self, builds intensity.",
        "pads": "Quiet harmonic bed under the solo. Low velocity, sustained.",
        "arp": "Very sparse or absent — don't compete with the soloist.",
        "fx": "Subtle texture, extra reverb on solo instrument.",
        "perc": "Light accompaniment, supporting the solo instrument's dynamics.",
    },
    "groove": {
        "drums": "Locked-in groove, consistent pattern. The anchor the whole band leans on.",
        "bass": "Repetitive, infectious bass line — the rhythmic engine with the drums.",
        "chords": "Rhythmic stabs or vamp pattern. Repetitive, locked to the groove.",
        "lead": "Riff-based melodic figure, hooks into the groove pattern.",
        "melody": "Short repeating melodic hook that rides the groove.",
        "pads": "Sustained harmonic bed supporting the vamp.",
        "arp": "Synced arpeggiated pattern locked to the groove tempo.",
        "fx": "Rhythmic effect synced to groove — filter, tremolo, or delay.",
        "perc": "Additional percussion layers reinforcing the groove.",
    },
    "interlude": {
        "drums": "Transitional groove — lighter than verse, bridging two sections.",
        "bass": "Simplified pattern or pedal tone. Connecting passage.",
        "chords": "Transition chords — linking harmonic areas between sections.",
        "lead": "Brief melodic fragment or silence. Transitional.",
        "melody": "Short connecting phrase or rest. Prepares the ear for the next section.",
        "pads": "Transitional pad — shifting texture or harmonic colour.",
        "arp": "Light transitional arpeggiation or silence.",
        "fx": "Transitional texture — sweeps, reverse hits, or ambient wash.",
        "perc": "Minimal — a cymbal roll or silence.",
    },
}


def _get_section_role_description(section_name: str, role: str) -> str:
    """Return a template description for a role within a section."""
    templates = _SECTION_ROLE_TEMPLATES.get(section_name.lower(), {})
    if not templates:
        return ""
    role_lower = role.lower()
    if role_lower in templates:
        return templates[role_lower]
    # Fuzzy match
    for key, desc in templates.items():
        if key in role_lower or role_lower in key:
            return desc
    return ""


def _parse_form_structure(prompt: str) -> list[str] | None:
    """Extract section names from the ``Form: structure:`` field in a MAESTRO PROMPT.

    This is the authoritative source of section layout — it takes precedence
    over keyword scanning of narrative text, which produces false positives
    when descriptive prose mentions words like "chorus" or "bridge".

    Returns a list of canonical section names, or *None* if the field is absent.
    """
    m = re.search(
        r"^Form:\s*\n\s+structure:\s*(.+)",
        prompt,
        re.MULTILINE,
    )
    if not m:
        return None

    raw = m.group(1).strip()
    parts = re.split(r"[-–—_→>]+", raw)
    names = [p.strip().lower().replace(" ", "_") for p in parts if p.strip()]
    if len(names) < 2:
        return None

    return names


def parse_sections(
    prompt: str,
    bars: int,
    roles: list[str],
) -> list[SectionDict]:
    """Parse a MAESTRO PROMPT into musical sections with beat ranges.

    Args:
        prompt: Raw MAESTRO PROMPT text from the user.
        bars: Total number of bars in the composition.
        roles: Instrument roles being generated (e.g. ['drums', 'bass', 'chords']).

    Returns:
        list of section dicts, each containing:
          - name: str (e.g. 'verse')
          - start_beat: float
          - length_beats: float
          - description: str (overall section description)
          - per_track_description: dict[role → description]

        Returns a single full-arrangement section if no sections are detected.
    """
    beats_total = bars * 4

    # ── Priority 1: explicit Form.structure field (authoritative) ──
    form_sections = _parse_form_structure(prompt)
    if form_sections:
        logger.info(
            f"Using Form.structure field: {form_sections} "
            f"(skipping keyword scan)"
        )
        ordered = form_sections
    else:
        # ── Priority 2: keyword scan of prompt text (legacy fallback) ──
        ordered = _detect_sections_by_keywords(prompt)

    if not ordered:
        logger.debug(
            "No sections detected in MAESTRO PROMPT "
            "— using single full-arrangement section"
        )
        return _single_section(beats_total, roles)

    if len(ordered) < 2:
        return _single_section(beats_total, roles)

    return _build_sections(ordered, beats_total, roles)


def _detect_sections_by_keywords(prompt: str) -> list[str]:
    """Detect section names by scanning the prompt for keywords.

    Falls back to this when the MAESTRO PROMPT lacks an explicit
    ``Form: structure:`` field.
    """
    prompt_lower = prompt.lower()
    detected: list[tuple[int, str]] = []

    for pattern, name, _ in _SECTION_KEYWORDS:
        for m in re.finditer(pattern, prompt_lower):
            detected.append((m.start(), name))

    for pattern, name in _INFERRED_SECTIONS:
        for m in re.finditer(pattern, prompt_lower):
            if not any(n == name for _, n in detected):
                detected.append((m.start(), name))

    if not detected:
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for _, name in sorted(detected, key=lambda x: x[0]):
        if name not in seen:
            ordered.append(name)
            seen.add(name)

    return ordered


def _build_sections(
    ordered: list[str],
    beats_total: float,
    roles: list[str],
) -> list[SectionDict]:
    """Distribute *beats_total* across *ordered* section names proportionally."""
    # Distribute total beats across sections proportionally using default weights.
    weights = {name: w for _, name, w in _SECTION_KEYWORDS}
    total_weight = sum(weights.get(n, 0.2) for n in ordered)
    sections: list[SectionDict] = []
    current_beat: float = 0.0

    for i, name in enumerate(ordered):
        w = weights.get(name, 0.2)
        length = round((w / total_weight) * beats_total / 4) * 4  # snap to bar boundary
        # Ensure last section reaches the end exactly
        if i == len(ordered) - 1:
            length = int(beats_total - current_beat)

        length = max(4, length)

        per_track: dict[str, str] = {}
        for role in roles:
            desc = _get_section_role_description(name, role)
            if desc:
                per_track[role.lower()] = desc

        sections.append(SectionDict(
            name=name,
            start_beat=current_beat,
            length_beats=length,
            description=_section_overall_description(name),
            per_track_description=per_track,
        ))
        current_beat += length

    logger.info(
        f"Parsed {len(sections)} sections from MAESTRO PROMPT: "
        + ", ".join(f"{s['name']}({int(s['length_beats'])}b)" for s in sections)
    )
    return sections


def _single_section(beats_total: float, roles: list[str]) -> list[SectionDict]:
    """Return a single full-arrangement section."""
    per_track: dict[str, str] = {}
    for role in roles:
        per_track[role.lower()] = ""
    return [SectionDict(
        name="full",
        start_beat=0.0,
        length_beats=float(beats_total),
        description="Full arrangement.",
        per_track_description=per_track,
    )]


_SECTION_OVERALL_DESCRIPTIONS: dict[str, str] = {
    "intro": "Opening section — sparse, establishing mood. Minimal instrumentation.",
    "verse": "Mid-energy verse — full groove, melodic content, controlled density.",
    "pre-chorus": "Building tension — density increasing, anticipating the chorus.",
    "chorus": "Peak energy drop — all instruments at full intensity, maximum density.",
    "bridge": "Contrasting section — harmonic or rhythmic variation, reduced energy.",
    "breakdown": "Stripped back — tension before the drop. Most instruments silent or minimal.",
    "build": "Progressive build — layered entry of instruments, rising intensity.",
    "outro": "Closing section — gradual fade or element removal, winding down.",
    "solo": "Solo section — one instrument takes the lead, others provide supportive accompaniment.",
    "groove": "Locked-in groove/vamp — repetitive, hypnotic pattern. All instruments ride the pocket.",
    "interlude": "Transitional passage — connects two sections, lighter texture.",
    "full": "Full arrangement.",
}


def _section_overall_description(name: str) -> str:
    return _SECTION_OVERALL_DESCRIPTIONS.get(name.lower(), f"{name.capitalize()} section.")
