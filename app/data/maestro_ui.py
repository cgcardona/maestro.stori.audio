"""Static seed data for the Maestro Default UI endpoints.

All content here is returned verbatim by the API today.  When we add a CMS or
per-user personalisation, these dicts become the *fallback* defaults.
"""

from app.models.maestro_ui import (
    PromptChip,
    PromptCard,
    PromptSection,
    PromptTemplate,
)


# ---------------------------------------------------------------------------
# Placeholders — rotated every 4 s in the hero prompt input
# ---------------------------------------------------------------------------

PLACEHOLDERS: list[str] = [
    "Describe a groove\u2026",
    "Build a cinematic swell\u2026",
    "Make something nobody has heard before\u2026",
    "A lo-fi beat for a rainy afternoon\u2026",
    "Jazz trio warming up in a dim club\u2026",
    "Epic orchestral buildup to a drop\u2026",
    "Funky bassline with a pocket feel\u2026",
    "Ambient textures for a midnight drive\u2026",
]


# ---------------------------------------------------------------------------
# Chips — quick-start genre chips for the flow grid
# ---------------------------------------------------------------------------

CHIPS: list[PromptChip] = [
    PromptChip(
        id="lofi",
        title="Lo-fi Chill",
        icon="cloud.rain",
        prompt_template_id="lofi_chill",
        full_prompt=(
            "Lo-fi hip hop beat at 85 BPM with dusty samples, "
            "vinyl crackle, and a chill late-night groove"
        ),
    ),
    PromptChip(
        id="trap",
        title="Dark Trap",
        icon="bolt.fill",
        prompt_template_id="dark_trap",
        full_prompt=(
            "Dark trap at 140 BPM with booming 808s, "
            "rolling hi-hats, and sparse haunting melodies"
        ),
    ),
    PromptChip(
        id="jazz",
        title="Jazz Trio",
        icon="music.quarternote.3",
        prompt_template_id="jazz_trio",
        full_prompt=(
            "Jazz trio with brushed drums, walking upright bass, "
            "and piano comping in a smoky club vibe"
        ),
    ),
    PromptChip(
        id="synthwave",
        title="Synthwave",
        icon="waveform",
        prompt_template_id="synthwave",
        full_prompt=(
            "80s synthwave with arpeggiated leads, pulsing bass, "
            "and reverb-drenched pads"
        ),
    ),
    PromptChip(
        id="cinematic",
        title="Cinematic",
        icon="film",
        prompt_template_id="cinematic",
        full_prompt=(
            "Cinematic orchestral swell in C minor, "
            "building tension with strings and brass"
        ),
    ),
    PromptChip(
        id="funk",
        title="Funk Groove",
        icon="guitars.fill",
        prompt_template_id="funk_groove",
        full_prompt=(
            "Funk groove with slap bass, tight horns, "
            "and a driving rhythm section"
        ),
    ),
    PromptChip(
        id="ambient",
        title="Ambient",
        icon="leaf",
        prompt_template_id="ambient",
        full_prompt=(
            "Ambient soundscape with evolving pads, "
            "gentle arpeggios, and ethereal textures"
        ),
    ),
    PromptChip(
        id="house",
        title="Deep House",
        icon="speaker.wave.3.fill",
        prompt_template_id="deep_house",
        full_prompt=(
            "Deep house at 122 BPM with a warm bassline, "
            "shuffled hats, and soulful chord stabs"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Cards — advanced structured template cards for the horizontal carousel
# ---------------------------------------------------------------------------

_FULL_PRODUCTION_SECTIONS: list[PromptSection] = [
    PromptSection(heading="Style", content="Genre, tempo, key, and overall vibe"),
    PromptSection(heading="Arrangement", content="Song structure: intro, verse, chorus, bridge, outro"),
    PromptSection(heading="Instruments", content="Drums, bass, keys, guitar, synths, strings"),
    PromptSection(heading="Production Notes", content="Effects, mix balance, stereo placement"),
    PromptSection(heading="Creative Intent", content="The feeling and story behind the music"),
]

_BEAT_LAB_SECTIONS: list[PromptSection] = [
    PromptSection(heading="Style", content="Boom bap, trap, house, or hybrid"),
    PromptSection(heading="Arrangement", content="4-8 bar loop with variations"),
    PromptSection(heading="Instruments", content="Kick, snare, hats, percussion, bass"),
    PromptSection(heading="Production Notes", content="Swing, velocity variation, sidechain"),
    PromptSection(heading="Creative Intent", content="Head-nod factor and energy level"),
]

_MOOD_PIECE_SECTIONS: list[PromptSection] = [
    PromptSection(heading="Style", content="Ambient, cinematic, or electronic"),
    PromptSection(heading="Arrangement", content="Evolving layers that build and release"),
    PromptSection(heading="Instruments", content="Pads, strings, ethereal synths"),
    PromptSection(heading="Production Notes", content="Reverb depth, stereo width, dynamics"),
    PromptSection(heading="Creative Intent", content="The scene, emotion, or narrative"),
]

CARDS: list[PromptCard] = [
    PromptCard(
        id="full_production",
        title="Full Song Production",
        description="Multi-track arrangement with drums, bass, chords, and melody",
        preview_tags=["Multi-track", "Arrangement", "Production"],
        template_id="full_production",
        sections=_FULL_PRODUCTION_SECTIONS,
    ),
    PromptCard(
        id="beat_lab",
        title="Beat Laboratory",
        description="Rhythm-first production with layered percussion and bass",
        preview_tags=["Drums", "808s", "Rhythm"],
        template_id="beat_lab",
        sections=_BEAT_LAB_SECTIONS,
    ),
    PromptCard(
        id="mood_piece",
        title="Mood Piece",
        description="Atmospheric composition driven by emotion and texture",
        preview_tags=["Ambient", "Pads", "Texture"],
        template_id="mood_piece",
        sections=_MOOD_PIECE_SECTIONS,
    ),
]


# ---------------------------------------------------------------------------
# Full prompt templates — keyed by template ID slug
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, PromptTemplate] = {
    "lofi_chill": PromptTemplate(
        id="lofi_chill",
        title="Lo-fi Chill",
        full_prompt=(
            "Lo-fi hip hop beat at 85 BPM with dusty samples, "
            "vinyl crackle, and a chill late-night groove"
        ),
        sections=[
            PromptSection(heading="Style", content="Lo-fi hip hop, 85 BPM, key of Dm"),
            PromptSection(heading="Arrangement", content="4-bar loop, mellow intro"),
            PromptSection(heading="Instruments", content="Dusty drums, muted Rhodes, vinyl texture, soft sub bass"),
            PromptSection(heading="Production Notes", content="Tape saturation, gentle sidechain, lo-pass filter"),
            PromptSection(heading="Creative Intent", content="Late-night study session vibe, nostalgic warmth"),
        ],
    ),
    "dark_trap": PromptTemplate(
        id="dark_trap",
        title="Dark Trap",
        full_prompt=(
            "Dark trap at 140 BPM with booming 808s, "
            "rolling hi-hats, and sparse haunting melodies"
        ),
        sections=[
            PromptSection(heading="Style", content="Dark trap, 140 BPM, key of Fm"),
            PromptSection(heading="Arrangement", content="8-bar loop with drop"),
            PromptSection(heading="Instruments", content="808 bass, TR-808 kit, dark pads, sparse bells"),
            PromptSection(heading="Production Notes", content="Heavy sidechain, distorted bass, spacious reverb"),
            PromptSection(heading="Creative Intent", content="Menacing energy, heavy low end, minimal but impactful"),
        ],
    ),
    "jazz_trio": PromptTemplate(
        id="jazz_trio",
        title="Jazz Trio",
        full_prompt=(
            "Jazz trio with brushed drums, walking upright bass, "
            "and piano comping in a smoky club vibe"
        ),
        sections=[
            PromptSection(heading="Style", content="Jazz, 120 BPM swing, key of Bb"),
            PromptSection(heading="Arrangement", content="32-bar AABA form"),
            PromptSection(heading="Instruments", content="Brushed drums, upright bass, grand piano"),
            PromptSection(heading="Production Notes", content="Warm room reverb, natural dynamics, wide stereo"),
            PromptSection(heading="Creative Intent", content="Smoky late-night club, intimate and conversational"),
        ],
    ),
    "synthwave": PromptTemplate(
        id="synthwave",
        title="Synthwave",
        full_prompt=(
            "80s synthwave with arpeggiated leads, pulsing bass, "
            "and reverb-drenched pads"
        ),
        sections=[
            PromptSection(heading="Style", content="Synthwave / retrowave, 118 BPM, key of Am"),
            PromptSection(heading="Arrangement", content="Intro, build, main section with arp lead"),
            PromptSection(heading="Instruments", content="Saw lead, pulse bass, lush pads, electronic drums"),
            PromptSection(heading="Production Notes", content="Gated reverb, chorus, sidechain pumping"),
            PromptSection(heading="Creative Intent", content="Neon-lit night drive, 80s nostalgia, cinematic energy"),
        ],
    ),
    "cinematic": PromptTemplate(
        id="cinematic",
        title="Cinematic",
        full_prompt=(
            "Cinematic orchestral swell in C minor, "
            "building tension with strings and brass"
        ),
        sections=[
            PromptSection(heading="Style", content="Cinematic orchestral, 90 BPM, key of Cm"),
            PromptSection(heading="Arrangement", content="Slow build from ppp to fff with timpani crescendo"),
            PromptSection(heading="Instruments", content="String ensemble, French horns, timpani, choir pad"),
            PromptSection(heading="Production Notes", content="Large hall reverb, dynamic swells, wide stereo image"),
            PromptSection(heading="Creative Intent", content="Epic tension and release, film trailer energy"),
        ],
    ),
    "funk_groove": PromptTemplate(
        id="funk_groove",
        title="Funk Groove",
        full_prompt=(
            "Funk groove with slap bass, tight horns, "
            "and a driving rhythm section"
        ),
        sections=[
            PromptSection(heading="Style", content="Funk, 108 BPM, key of E"),
            PromptSection(heading="Arrangement", content="Tight 4-bar groove with horn stabs"),
            PromptSection(heading="Instruments", content="Slap bass, clav, horns (trumpet, sax), tight drums"),
            PromptSection(heading="Production Notes", content="Punchy compression, short reverb, everything in the pocket"),
            PromptSection(heading="Creative Intent", content="Head-bobbing groove, tight and infectious"),
        ],
    ),
    "ambient": PromptTemplate(
        id="ambient",
        title="Ambient",
        full_prompt=(
            "Ambient soundscape with evolving pads, "
            "gentle arpeggios, and ethereal textures"
        ),
        sections=[
            PromptSection(heading="Style", content="Ambient / atmospheric, 70 BPM, key of D"),
            PromptSection(heading="Arrangement", content="Slowly evolving layers over 16 bars"),
            PromptSection(heading="Instruments", content="Evolving pads, granular textures, soft arp, sub bass drone"),
            PromptSection(heading="Production Notes", content="Long reverb tails, stereo modulation, gentle filter sweeps"),
            PromptSection(heading="Creative Intent", content="Meditative calm, floating in space, time dissolving"),
        ],
    ),
    "deep_house": PromptTemplate(
        id="deep_house",
        title="Deep House",
        full_prompt=(
            "Deep house at 122 BPM with a warm bassline, "
            "shuffled hats, and soulful chord stabs"
        ),
        sections=[
            PromptSection(heading="Style", content="Deep house, 122 BPM, key of Gm"),
            PromptSection(heading="Arrangement", content="8-bar loop with filter build"),
            PromptSection(heading="Instruments", content="Four-on-the-floor kick, shuffled hats, warm sub bass, Rhodes stabs"),
            PromptSection(heading="Production Notes", content="Sidechain compression, warm saturation, lo-fi processing"),
            PromptSection(heading="Creative Intent", content="Late-night dancefloor, soulful and hypnotic"),
        ],
    ),
    "full_production": PromptTemplate(
        id="full_production",
        title="Full Song Production",
        full_prompt=(
            "Full song production with multi-track arrangement — "
            "drums, bass, chords, and melody"
        ),
        sections=_FULL_PRODUCTION_SECTIONS,
    ),
    "beat_lab": PromptTemplate(
        id="beat_lab",
        title="Beat Laboratory",
        full_prompt=(
            "Rhythm-first production with layered percussion and bass — "
            "boom bap, trap, house, or hybrid"
        ),
        sections=_BEAT_LAB_SECTIONS,
    ),
    "mood_piece": PromptTemplate(
        id="mood_piece",
        title="Mood Piece",
        full_prompt=(
            "Atmospheric composition driven by emotion and texture — "
            "ambient, cinematic, or electronic"
        ),
        sections=_MOOD_PIECE_SECTIONS,
    ),
}

ALL_TEMPLATE_IDS: set[str] = set(TEMPLATES.keys())
