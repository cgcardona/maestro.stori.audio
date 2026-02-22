"""
General MIDI (GM) Instrument Mapping for Stori Maestro.

This module provides:
1. Complete GM instrument program numbers (0-127)
2. Fuzzy matching from natural language to GM programs
3. Default instrument inference from track names

GM Standard Reference:
- Programs 0-7: Piano
- Programs 8-15: Chromatic Percussion
- Programs 16-23: Organ
- Programs 24-31: Guitar
- Programs 32-39: Bass
- Programs 40-47: Strings
- Programs 48-55: Ensemble
- Programs 56-63: Brass
- Programs 64-71: Reed
- Programs 72-79: Pipe
- Programs 80-87: Synth Lead
- Programs 88-95: Synth Pad
- Programs 96-103: Synth Effects
- Programs 104-111: Ethnic
- Programs 112-119: Percussive
- Programs 120-127: Sound Effects

Channel 10 is reserved for drums (no program change needed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GMInstrument:
    """A General MIDI instrument."""
    program: int  # 0-127
    name: str  # Official GM name
    category: str  # Instrument category
    aliases: tuple[str, ...]  # Alternative names for matching


# =============================================================================
# Complete GM Instrument List (128 programs)
# =============================================================================

GM_INSTRUMENTS: list[GMInstrument] = [
    # =========================================================================
    # Piano (0-7)
    # =========================================================================
    GMInstrument(0, "Acoustic Grand Piano", "piano", ("grand piano", "piano", "acoustic piano", "concert piano")),
    GMInstrument(1, "Bright Acoustic Piano", "piano", ("bright piano", "bright acoustic")),
    GMInstrument(2, "Electric Grand Piano", "piano", ("electric grand", "electric piano grand")),
    GMInstrument(3, "Honky-tonk Piano", "piano", ("honky tonk", "honkytonk", "saloon piano", "ragtime piano")),
    GMInstrument(4, "Electric Piano 1", "piano", ("electric piano", "rhodes", "fender rhodes", "ep1", "e-piano")),
    GMInstrument(5, "Electric Piano 2", "piano", ("dx7", "fm piano", "ep2", "fm electric piano")),
    GMInstrument(6, "Harpsichord", "piano", ("harpsichord", "clavecin", "cembalo")),
    GMInstrument(7, "Clavinet", "piano", ("clavinet", "clav", "d6")),

    # =========================================================================
    # Chromatic Percussion (8-15)
    # =========================================================================
    GMInstrument(8, "Celesta", "chromatic_percussion", ("celesta", "celeste")),
    GMInstrument(9, "Glockenspiel", "chromatic_percussion", ("glockenspiel", "glock", "bells")),
    GMInstrument(10, "Music Box", "chromatic_percussion", ("music box", "musicbox")),
    GMInstrument(11, "Vibraphone", "chromatic_percussion", ("vibraphone", "vibes", "vibraharp")),
    GMInstrument(12, "Marimba", "chromatic_percussion", ("marimba",)),
    GMInstrument(13, "Xylophone", "chromatic_percussion", ("xylophone", "xylo")),
    GMInstrument(14, "Tubular Bells", "chromatic_percussion", ("tubular bells", "chimes", "orchestral chimes")),
    GMInstrument(15, "Dulcimer", "chromatic_percussion", ("dulcimer", "hammered dulcimer")),

    # =========================================================================
    # Organ (16-23)
    # =========================================================================
    GMInstrument(16, "Drawbar Organ", "organ", ("drawbar organ", "hammond", "b3", "organ")),
    GMInstrument(17, "Percussive Organ", "organ", ("percussive organ", "perc organ")),
    GMInstrument(18, "Rock Organ", "organ", ("rock organ", "distorted organ")),
    GMInstrument(19, "Church Organ", "organ", ("church organ", "pipe organ", "cathedral organ")),
    GMInstrument(20, "Reed Organ", "organ", ("reed organ", "harmonium")),
    GMInstrument(21, "Accordion", "organ", ("accordion", "accordian")),
    GMInstrument(22, "Harmonica", "organ", ("harmonica", "blues harp", "mouth organ")),
    GMInstrument(23, "Tango Accordion", "organ", ("tango accordion", "bandoneon")),

    # =========================================================================
    # Guitar (24-31)
    # =========================================================================
    GMInstrument(24, "Acoustic Guitar (nylon)", "guitar", ("nylon guitar", "classical guitar", "nylon", "spanish guitar")),
    GMInstrument(25, "Acoustic Guitar (steel)", "guitar", ("steel guitar", "acoustic guitar", "steel string", "folk guitar")),
    GMInstrument(26, "Electric Guitar (jazz)", "guitar", ("jazz guitar", "clean guitar", "hollow body")),
    GMInstrument(27, "Electric Guitar (clean)", "guitar", ("clean electric", "electric guitar clean", "electric guitar", "strat clean")),
    GMInstrument(28, "Electric Guitar (muted)", "guitar", ("muted guitar", "palm mute", "muted electric")),
    GMInstrument(29, "Overdriven Guitar", "guitar", ("overdriven guitar", "overdrive guitar", "crunchy guitar")),
    GMInstrument(30, "Distortion Guitar", "guitar", ("distortion guitar", "distorted guitar", "heavy guitar", "metal guitar")),
    GMInstrument(31, "Guitar Harmonics", "guitar", ("guitar harmonics", "harmonics")),

    # =========================================================================
    # Bass (32-39)
    # =========================================================================
    GMInstrument(32, "Acoustic Bass", "bass", ("acoustic bass", "upright bass", "double bass", "standup bass", "contrabass")),
    GMInstrument(33, "Electric Bass (finger)", "bass", ("electric bass", "finger bass", "bass guitar", "bass", "fingered bass")),
    GMInstrument(34, "Electric Bass (pick)", "bass", ("pick bass", "picked bass")),
    GMInstrument(35, "Fretless Bass", "bass", ("fretless bass", "fretless")),
    GMInstrument(36, "Slap Bass 1", "bass", ("slap bass", "slap")),
    GMInstrument(37, "Slap Bass 2", "bass", ("slap bass 2", "pop bass")),
    GMInstrument(38, "Synth Bass 1", "bass", ("synth bass", "synth bass 1", "analog bass")),
    GMInstrument(39, "Synth Bass 2", "bass", ("synth bass 2", "digital bass", "fm bass")),

    # =========================================================================
    # Strings (40-47)
    # =========================================================================
    GMInstrument(40, "Violin", "strings", ("violin", "fiddle")),
    GMInstrument(41, "Viola", "strings", ("viola",)),
    GMInstrument(42, "Cello", "strings", ("cello", "violoncello")),
    GMInstrument(43, "Contrabass", "strings", ("contrabass", "string bass")),
    GMInstrument(44, "Tremolo Strings", "strings", ("tremolo strings", "tremolo")),
    GMInstrument(45, "Pizzicato Strings", "strings", ("pizzicato strings", "pizzicato", "pizz")),
    GMInstrument(46, "Orchestral Harp", "strings", ("harp", "orchestral harp", "concert harp")),
    GMInstrument(47, "Timpani", "strings", ("timpani", "kettle drums", "kettle drum")),

    # =========================================================================
    # Ensemble (48-55)
    # =========================================================================
    GMInstrument(48, "String Ensemble 1", "ensemble", ("strings", "string ensemble", "orchestra strings", "orchestral strings")),
    GMInstrument(49, "String Ensemble 2", "ensemble", ("slow strings", "string ensemble 2")),
    GMInstrument(50, "Synth Strings 1", "ensemble", ("synth strings", "string synth")),
    GMInstrument(51, "Synth Strings 2", "ensemble", ("synth strings 2",)),
    GMInstrument(52, "Choir Aahs", "ensemble", ("choir", "choir aahs", "vocal", "vocals", "aahs")),
    GMInstrument(53, "Voice Oohs", "ensemble", ("oohs", "voice oohs")),
    GMInstrument(54, "Synth Voice", "ensemble", ("synth voice", "vocoder", "synth choir")),
    GMInstrument(55, "Orchestra Hit", "ensemble", ("orchestra hit", "orch hit", "stab")),

    # =========================================================================
    # Brass (56-63)
    # =========================================================================
    GMInstrument(56, "Trumpet", "brass", ("trumpet", "horn")),
    GMInstrument(57, "Trombone", "brass", ("trombone",)),
    GMInstrument(58, "Tuba", "brass", ("tuba",)),
    GMInstrument(59, "Muted Trumpet", "brass", ("muted trumpet", "harmon mute")),
    GMInstrument(60, "French Horn", "brass", ("french horn",)),
    GMInstrument(61, "Brass Section", "brass", ("brass section", "brass", "horns", "brass ensemble")),
    GMInstrument(62, "Synth Brass 1", "brass", ("synth brass", "synth brass 1")),
    GMInstrument(63, "Synth Brass 2", "brass", ("synth brass 2",)),

    # =========================================================================
    # Reed (64-71)
    # =========================================================================
    GMInstrument(64, "Soprano Sax", "reed", ("soprano sax", "soprano saxophone")),
    GMInstrument(65, "Alto Sax", "reed", ("alto sax", "alto saxophone", "alto")),
    GMInstrument(66, "Tenor Sax", "reed", ("tenor sax", "tenor saxophone", "sax", "saxophone")),
    GMInstrument(67, "Baritone Sax", "reed", ("baritone sax", "baritone saxophone", "bari sax")),
    GMInstrument(68, "Oboe", "reed", ("oboe",)),
    GMInstrument(69, "English Horn", "reed", ("english horn", "cor anglais")),
    GMInstrument(70, "Bassoon", "reed", ("bassoon",)),
    GMInstrument(71, "Clarinet", "reed", ("clarinet",)),

    # =========================================================================
    # Pipe (72-79)
    # =========================================================================
    GMInstrument(72, "Piccolo", "pipe", ("piccolo",)),
    GMInstrument(73, "Flute", "pipe", ("flute",)),
    GMInstrument(74, "Recorder", "pipe", ("recorder",)),
    GMInstrument(75, "Pan Flute", "pipe", ("pan flute", "pan pipes")),
    GMInstrument(76, "Blown Bottle", "pipe", ("blown bottle", "bottle")),
    GMInstrument(77, "Shakuhachi", "pipe", ("shakuhachi",)),
    GMInstrument(78, "Whistle", "pipe", ("whistle", "tin whistle")),
    GMInstrument(79, "Ocarina", "pipe", ("ocarina",)),

    # =========================================================================
    # Synth Lead (80-87)
    # =========================================================================
    GMInstrument(80, "Lead 1 (square)", "synth_lead", ("square lead", "square wave", "synth lead", "lead")),
    GMInstrument(81, "Lead 2 (sawtooth)", "synth_lead", ("sawtooth lead", "saw lead", "saw wave")),
    GMInstrument(82, "Lead 3 (calliope)", "synth_lead", ("calliope", "calliope lead")),
    GMInstrument(83, "Lead 4 (chiff)", "synth_lead", ("chiff lead", "chiff")),
    GMInstrument(84, "Lead 5 (charang)", "synth_lead", ("charang", "distorted lead")),
    GMInstrument(85, "Lead 6 (voice)", "synth_lead", ("voice lead", "synth voice lead")),
    GMInstrument(86, "Lead 7 (fifths)", "synth_lead", ("fifths lead", "power lead")),
    GMInstrument(87, "Lead 8 (bass + lead)", "synth_lead", ("bass lead", "bass + lead")),

    # =========================================================================
    # Synth Pad (88-95)
    # =========================================================================
    GMInstrument(88, "Pad 1 (new age)", "synth_pad", ("new age pad", "pad", "synth pad", "ambient pad")),
    GMInstrument(89, "Pad 2 (warm)", "synth_pad", ("warm pad", "analog pad")),
    GMInstrument(90, "Pad 3 (polysynth)", "synth_pad", ("polysynth", "poly pad")),
    GMInstrument(91, "Pad 4 (choir)", "synth_pad", ("choir pad", "synth choir pad")),
    GMInstrument(92, "Pad 5 (bowed)", "synth_pad", ("bowed pad", "bowed glass")),
    GMInstrument(93, "Pad 6 (metallic)", "synth_pad", ("metallic pad", "metal pad")),
    GMInstrument(94, "Pad 7 (halo)", "synth_pad", ("halo pad", "halo")),
    GMInstrument(95, "Pad 8 (sweep)", "synth_pad", ("sweep pad", "sweep")),

    # =========================================================================
    # Synth Effects (96-103)
    # =========================================================================
    GMInstrument(96, "FX 1 (rain)", "synth_fx", ("rain", "rain fx")),
    GMInstrument(97, "FX 2 (soundtrack)", "synth_fx", ("soundtrack", "cinematic")),
    GMInstrument(98, "FX 3 (crystal)", "synth_fx", ("crystal", "crystal fx")),
    GMInstrument(99, "FX 4 (atmosphere)", "synth_fx", ("atmosphere", "atmos", "atmospheric")),
    GMInstrument(100, "FX 5 (brightness)", "synth_fx", ("brightness", "bright fx")),
    GMInstrument(101, "FX 6 (goblins)", "synth_fx", ("goblins", "goblin")),
    GMInstrument(102, "FX 7 (echoes)", "synth_fx", ("echoes", "echo fx")),
    GMInstrument(103, "FX 8 (sci-fi)", "synth_fx", ("sci-fi", "scifi", "space")),

    # =========================================================================
    # Ethnic (104-111)
    # =========================================================================
    GMInstrument(104, "Sitar", "ethnic", ("sitar",)),
    GMInstrument(105, "Banjo", "ethnic", ("banjo",)),
    GMInstrument(106, "Shamisen", "ethnic", ("shamisen",)),
    GMInstrument(107, "Koto", "ethnic", ("koto",)),
    GMInstrument(108, "Kalimba", "ethnic", ("kalimba", "thumb piano")),
    GMInstrument(109, "Bag Pipe", "ethnic", ("bagpipe", "bag pipe", "bagpipes")),
    GMInstrument(110, "Fiddle", "ethnic", ("fiddle", "folk fiddle")),
    GMInstrument(111, "Shanai", "ethnic", ("shanai", "shehnai")),

    # =========================================================================
    # Percussive (112-119)
    # =========================================================================
    GMInstrument(112, "Tinkle Bell", "percussive", ("tinkle bell", "bell")),
    GMInstrument(113, "Agogo", "percussive", ("agogo",)),
    GMInstrument(114, "Steel Drums", "percussive", ("steel drums", "steel drum", "steel pan")),
    GMInstrument(115, "Woodblock", "percussive", ("woodblock", "wood block")),
    GMInstrument(116, "Taiko Drum", "percussive", ("taiko", "taiko drum")),
    GMInstrument(117, "Melodic Tom", "percussive", ("melodic tom", "tom")),
    GMInstrument(118, "Synth Drum", "percussive", ("synth drum", "electronic drum")),
    GMInstrument(119, "Reverse Cymbal", "percussive", ("reverse cymbal", "cymbal reverse")),

    # =========================================================================
    # Sound Effects (120-127)
    # =========================================================================
    GMInstrument(120, "Guitar Fret Noise", "sfx", ("fret noise", "guitar noise")),
    GMInstrument(121, "Breath Noise", "sfx", ("breath noise", "breath")),
    GMInstrument(122, "Seashore", "sfx", ("seashore", "ocean", "waves")),
    GMInstrument(123, "Bird Tweet", "sfx", ("bird", "bird tweet", "birds")),
    GMInstrument(124, "Telephone Ring", "sfx", ("telephone", "phone ring")),
    GMInstrument(125, "Helicopter", "sfx", ("helicopter", "chopper")),
    GMInstrument(126, "Applause", "sfx", ("applause", "clapping")),
    GMInstrument(127, "Gunshot", "sfx", ("gunshot", "gun")),
]

# Build lookup index
_PROGRAM_TO_INSTRUMENT: dict[int, GMInstrument] = {inst.program: inst for inst in GM_INSTRUMENTS}


def get_instrument_by_program(program: int) -> Optional[GMInstrument]:
    """Get GM instrument by program number (0-127)."""
    return _PROGRAM_TO_INSTRUMENT.get(program)


def get_instrument_name(program: int) -> str:
    """Get the official GM name for a program number."""
    inst = _PROGRAM_TO_INSTRUMENT.get(program)
    return inst.name if inst else f"Program {program}"


# =============================================================================
# Fuzzy Matching
# =============================================================================

def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, remove punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)  # Remove punctuation
    text = re.sub(r'\s+', ' ', text).strip()  # Collapse whitespace
    return text


def _tokenize(text: str) -> set[str]:
    """Split normalized text into tokens."""
    return set(_normalize(text).split())


def infer_gm_program(
    text: str,
    default_program: Optional[int] = None
) -> Optional[int]:
    """
    Infer the best GM program number from natural language text.
    
    Uses fuzzy matching with priority:
    1. Exact alias match
    2. All tokens from alias present in text
    3. Category match with fallback to sensible default
    
    Args:
        text: Track name, instrument field, or user prompt
        default_program: Default to return if no match (None = no default)
        
    Returns:
        GM program number (0-127) or None/default if no match
        
    Examples:
        >>> infer_gm_program("Acoustic Guitar")
        25  # Steel string (default acoustic)
        >>> infer_gm_program("Electric Bass")
        33  # Finger bass
        >>> infer_gm_program("Rhodes")
        4   # Electric Piano 1
        >>> infer_gm_program("Drums")
        None  # Drums use channel 10, no program needed
    """
    if not text:
        return default_program
    
    normalized = _normalize(text)
    text_tokens = _tokenize(text)
    
    # Special case: drums don't need a GM program (channel 10)
    drum_keywords = {"drums", "drum", "kick", "snare", "hihat", "hi-hat", "percussion", "perc", "beat", "kit"}
    if text_tokens & drum_keywords:
        return None  # Drums use channel 10
    
    best_match: Optional[GMInstrument] = None
    best_score = 0
    
    for inst in GM_INSTRUMENTS:
        # Check exact name match
        if _normalize(inst.name) == normalized:
            return inst.program
        
        # Check alias matches
        for alias in inst.aliases:
            alias_normalized = _normalize(alias)
            alias_tokens = _tokenize(alias)
            
            # Exact alias match
            if alias_normalized == normalized:
                return inst.program
            
            # Alias is substring of text
            if alias_normalized in normalized:
                score = len(alias_normalized) * 10  # Longer matches score higher
                if score > best_score:
                    best_score = score
                    best_match = inst
                continue
            
            # All alias tokens present in text
            if alias_tokens and alias_tokens.issubset(text_tokens):
                score = len(alias_tokens) * 5
                if score > best_score:
                    best_score = score
                    best_match = inst
    
    if best_match:
        return best_match.program
    
    # Fallback: check category keywords
    category_defaults: dict[str, int] = {
        "piano": 0,       # Acoustic Grand Piano
        "keys": 0,        # Acoustic Grand Piano
        "keyboard": 0,    # Acoustic Grand Piano
        "organ": 16,      # Drawbar Organ
        "guitar": 25,     # Acoustic Guitar (steel)
        "bass": 33,       # Electric Bass (finger)
        "strings": 48,    # String Ensemble 1
        "brass": 61,      # Brass Section
        "sax": 66,        # Tenor Sax
        "saxophone": 66,  # Tenor Sax
        "flute": 73,      # Flute
        "synth": 80,      # Synth Lead (square)
        "lead": 80,       # Synth Lead
        "pad": 88,        # Synth Pad (new age)
        "choir": 52,      # Choir Aahs
        "voice": 52,      # Choir Aahs
        "violin": 40,     # Violin
        "cello": 42,      # Cello
        "trumpet": 56,    # Trumpet
        "trombone": 57,   # Trombone
    }
    
    for keyword, program in category_defaults.items():
        if keyword in text_tokens:
            return program
    
    return default_program


def get_default_program_for_role(role: str) -> Optional[int]:
    """
    Get a sensible default GM program for a musical role.
    
    Used by the planner/generator when creating tracks for specific roles.
    
    Args:
        role: Musical role like "bass", "chords", "melody", "drums"
        
    Returns:
        GM program number or None (for drums)
    """
    role_lower = role.lower().strip()
    
    role_to_program: dict[str, Optional[int]] = {
        # Drums - no program (channel 10)
        "drums": None,
        "drum": None,
        "percussion": None,
        
        # Bass
        "bass": 33,  # Electric Bass (finger)
        
        # Chords/Harmony
        "chords": 4,  # Electric Piano 1 (Rhodes)
        "keys": 0,    # Acoustic Grand Piano
        "piano": 0,   # Acoustic Grand Piano
        "pads": 88,   # Synth Pad
        "pad": 88,    # Synth Pad
        
        # Melody
        "melody": 80,  # Synth Lead
        "lead": 80,    # Synth Lead
        
        # Arpeggio
        "arp": 80,     # Synth Lead
        
        # Strings
        "strings": 48,  # String Ensemble
        
        # Other
        "fx": 99,      # Atmosphere
        "sfx": 99,     # Atmosphere
    }
    
    return role_to_program.get(role_lower)


@dataclass
class GMInferenceResult:
    """Result of GM program inference with context."""
    program: Optional[int]  # GM program (0-127) or None for drums
    instrument_name: str    # Human-readable name
    confidence: str         # "high", "medium", "low", "none"
    is_drums: bool          # True if this should use channel 10
    
    @property
    def needs_program_change(self) -> bool:
        """Returns True if a MIDI program change should be sent."""
        return self.program is not None and not self.is_drums


def infer_gm_program_with_context(
    track_name: Optional[str] = None,
    instrument: Optional[str] = None,
    role: Optional[str] = None,
) -> GMInferenceResult:
    """
    Infer GM program from multiple context sources.
    
    Priority:
    1. Explicit instrument field
    2. Track name
    3. Role (drums/bass/chords/etc)
    4. Default to Piano
    
    Args:
        track_name: Name of the track (e.g., "Acoustic Guitar")
        instrument: Explicit instrument field (e.g., "rhodes")
        role: Musical role (e.g., "bass", "drums")
        
    Returns:
        GMInferenceResult with program, name, and confidence
    """
    # Check for drums first (any source) — must match infer_gm_program's keyword set.
    drum_keywords = {"drums", "drum", "kick", "snare", "hihat", "hi-hat", "percussion", "perc", "beat", "kit"}
    all_text = " ".join(filter(None, [track_name, instrument, role])).lower()
    
    for kw in drum_keywords:
        if kw in all_text:
            return GMInferenceResult(
                program=None,
                instrument_name="Drums (Channel 10)",
                confidence="high",
                is_drums=True,
            )
    
    # Try instrument field first (highest priority)
    if instrument:
        program = infer_gm_program(instrument)
        if program is not None:
            inst = get_instrument_by_program(program)
            return GMInferenceResult(
                program=program,
                instrument_name=inst.name if inst else f"Program {program}",
                confidence="high",
                is_drums=False,
            )
    
    # Try track name
    if track_name:
        program = infer_gm_program(track_name)
        if program is not None:
            inst = get_instrument_by_program(program)
            return GMInferenceResult(
                program=program,
                instrument_name=inst.name if inst else f"Program {program}",
                confidence="medium",
                is_drums=False,
            )
    
    # Try role
    if role:
        program = get_default_program_for_role(role)
        if program is not None:
            inst = get_instrument_by_program(program)
            return GMInferenceResult(
                program=program,
                instrument_name=inst.name if inst else f"Program {program}",
                confidence="low",
                is_drums=False,
            )
        # Role was drums but handled above
        if role.lower() in drum_keywords:
            return GMInferenceResult(
                program=None,
                instrument_name="Drums (Channel 10)",
                confidence="high",
                is_drums=True,
            )
    
    # Default to Acoustic Grand Piano
    return GMInferenceResult(
        program=0,
        instrument_name="Acoustic Grand Piano",
        confidence="none",
        is_drums=False,
    )


# ---------------------------------------------------------------------------
# GM group → SF Symbol icon mapping (mirrors the macOS app's displayIcon)
# ---------------------------------------------------------------------------

_GM_CATEGORY_ICONS: list[tuple[range, str]] = [
    (range(0,   8),   "pianokeys"),              # Piano
    (range(8,  16),   "instrument.xylophone"),   # Chromatic Percussion
    (range(16, 24),   "music.note.house.fill"),  # Organ
    (range(24, 32),   "guitars.fill"),            # Guitar
    (range(32, 40),   "waveform.path"),           # Bass
    (range(40, 48),   "instrument.violin"),       # Strings
    (range(48, 56),   "instrument.violin"),       # Ensemble / Strings
    (range(56, 64),   "instrument.trumpet"),      # Brass
    (range(64, 72),   "instrument.saxophone"),    # Reed
    (range(72, 80),   "instrument.flute"),        # Pipe
    (range(80, 88),   "waveform"),                # Synth Lead
    (range(88, 96),   "waveform.circle.fill"),    # Synth Pad
    (range(96, 104),  "sparkles"),                # Synth Effects
    (range(104, 112), "globe"),                   # Ethnic
    (range(112, 120), "instrument.drum"),         # Percussive
    (range(120, 128), "speaker.wave.3"),          # Sound Effects
]

DRUM_ICON = "instrument.drum"


def icon_for_gm_program(gm_program: int) -> str:
    """Return the SF Symbol name for a GM program number (0-127).

    Mirrors the macOS app's ``displayIcon`` property so the persisted track
    model round-trips with the same icon the frontend would auto-derive.
    """
    for r, icon in _GM_CATEGORY_ICONS:
        if gm_program in r:
            return icon
    return "pianokeys"  # fallback for out-of-range values
