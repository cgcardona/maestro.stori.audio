"""Prompt templates for the Maestro UI."""

from app.models.maestro_ui import PromptSection, PromptTemplate



# ---------------------------------------------------------------------------
# Full prompt templates — keyed by template ID slug (single lookup endpoint)
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
