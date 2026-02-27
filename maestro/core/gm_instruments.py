"""
General MIDI (GM) Instrument Mapping for Maestro.

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


@dataclass
class GMInstrument:
    """A General MIDI instrument."""
    program: int  # 0-127
    name: str  # Official GM name
    category: str  # Instrument category
    aliases: tuple[str, ...]  # Alternative names for matching


# =============================================================================
# Complete GM Instrument list (128 programs)
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
    GMInstrument(25, "Acoustic Guitar (steel)", "guitar", ("steel guitar", "acoustic guitar", "steel string", "folk guitar", "mandolin")),
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


def get_instrument_by_program(program: int) -> GMInstrument | None:
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
    default_program: int | None = None
) -> int | None:
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
    
    best_match: GMInstrument | None = None
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


# =============================================================================
# Genre-specific GM voice guidance
# =============================================================================

GENRE_GM_GUIDANCE: dict[str, dict[str, list[tuple[int, str]]]] = {
    # =========================================================================
    # Caribbean / Latin America
    # =========================================================================
    "dancehall": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — standard dancehall bass"),
            (36, "Slap Bass 1 — punchier dancehall thump"),
        ],
        "chords": [
            (16, "Drawbar Organ — classic choppy offbeat 'bubble' stab (short notes, offbeats on 2+ and 4+)"),
            (17, "Percussive Organ — snappy organ bubble variant"),
        ],
        "lead": [
            (80, "Square Lead — buzzy digital synth texture"),
            (85, "Lead (Voice) — melodica-style dancehall lead"),
        ],
        "melody": [
            (80, "Square Lead — digital dancehall synth"),
            (85, "Lead (Voice) — melodica feel"),
        ],
        "pads": [(88, "Pad (New Age) — ambient dancehall wash")],
    },
    "reggae": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — deep reggae bass"),
            (32, "Acoustic Bass — roots reggae upright"),
        ],
        "chords": [
            (16, "Drawbar Organ — reggae organ bubble"),
            (27, "Electric Guitar (Clean) — reggae skank chop"),
        ],
        "lead": [
            (56, "Trumpet — reggae horn lead"),
            (85, "Lead (Voice) — melodica lead"),
        ],
        "melody": [(56, "Trumpet — reggae horn melody")],
        "pads": [(88, "Pad (New Age)")],
    },
    "reggaeton": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — reggaeton 808 sub bass"),
            (39, "Synth Bass 2 — deep digital sub"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — reggaeton keys"),
            (90, "Polysynth Pad — perreo synth stabs"),
        ],
        "lead": [
            (80, "Square Lead — reggaeton synth lead"),
            (62, "Synth Brass 1 — brass stab hits"),
        ],
        "melody": [(80, "Square Lead — reggaeton hook synth")],
        "pads": [(88, "Pad (New Age) — atmospheric reggaeton wash")],
    },
    "soca": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — soca bass groove"),
            (38, "Synth Bass 1 — modern soca sub"),
        ],
        "chords": [
            (114, "Steel Drums — iconic Caribbean steel pan chords"),
            (61, "Brass Section — soca horn stabs"),
        ],
        "lead": [
            (114, "Steel Drums — steel pan melody lead"),
            (56, "Trumpet — soca horn lead"),
        ],
        "melody": [(114, "Steel Drums — Caribbean steel pan")],
        "pads": [(88, "Pad (New Age) — tropical wash")],
    },
    "bossa nova": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — essential bossa nova upright")],
        "chords": [(24, "Nylon Guitar — classic bossa nova guitar comping")],
        "lead": [
            (73, "Flute — Jobim-style bossa flute"),
            (66, "Tenor Sax — bossa nova sax"),
        ],
        "melody": [(73, "Flute — bossa nova flute melody")],
        "pads": [(48, "String Ensemble — lush bossa strings")],
    },
    "cumbia": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — cumbia bass")],
        "chords": [
            (21, "Accordion — essential cumbia accordion"),
            (24, "Nylon Guitar — cumbia guitar rhythm"),
        ],
        "lead": [
            (21, "Accordion — cumbia accordion lead"),
            (73, "Flute — Colombian cumbia gaita-style"),
        ],
        "melody": [
            (73, "Flute — cumbia gaita flute melody"),
            (21, "Accordion — accordion melody"),
        ],
        "pads": [(48, "String Ensemble")],
    },
    "tango": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — tango upright bass")],
        "chords": [
            (23, "Tango Accordion — bandoneón (essential tango)"),
            (0, "Acoustic Grand Piano — tango piano"),
        ],
        "lead": [
            (23, "Tango Accordion — bandoneón lead"),
            (40, "Violin — tango violin solo"),
        ],
        "melody": [
            (40, "Violin — tango violin melody"),
            (23, "Tango Accordion — bandoneón melody"),
        ],
        "pads": [(42, "Cello — tango cello sustain")],
    },
    "huayno": {
        "drums": [],
        "bass": [(24, "Nylon Guitar — Andean guitar bass")],
        "chords": [
            (24, "Nylon Guitar — charango-style strumming"),
            (46, "Orchestral Harp — Andean harp"),
        ],
        "lead": [(75, "Pan Flute — quintessential Andean quena/zampoña")],
        "melody": [(75, "Pan Flute — Andean pan flute melody")],
        "pads": [(48, "String Ensemble — Andean string texture")],
    },
    "afro-cuban": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — Afro-Cuban upright bass")],
        "chords": [
            (0, "Acoustic Grand Piano — montuno comping"),
            (56, "Trumpet — horn section stabs"),
        ],
        "lead": [
            (56, "Trumpet — Afro-Cuban trumpet solo"),
            (57, "Trombone — salsa trombone"),
        ],
        "melody": [
            (56, "Trumpet — Afro-Cuban horn melody"),
            (73, "Flute — charanga flute"),
        ],
        "pads": [(48, "String Ensemble — charanga strings")],
    },

    # =========================================================================
    # Hip-hop / Urban
    # =========================================================================
    "boom bap": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — boom bap bass"),
            (38, "Synth Bass 1 — sampled 808 feel"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — boom bap keys"),
            (0, "Acoustic Grand Piano — sample chop feel"),
        ],
        "lead": [(80, "Square Lead — synth stab")],
        "melody": [(80, "Square Lead")],
        "pads": [(89, "Warm Pad — dusty boom bap texture")],
    },
    "hip hop": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — hip-hop sub bass"),
            (33, "Electric Bass (Finger) — classic hip-hop bass"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — hip-hop Rhodes"),
            (0, "Acoustic Grand Piano — sampled piano"),
        ],
        "lead": [(80, "Square Lead — hip-hop synth lead")],
        "melody": [(80, "Square Lead")],
        "pads": [(88, "Pad (New Age) — hip-hop ambient pad")],
    },
    "trap": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — 808 sub bass"),
            (39, "Synth Bass 2 — digital 808"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — dark trap chords"),
            (88, "Pad (New Age) — ambient trap pad"),
        ],
        "lead": [
            (80, "Square Lead — trap synth lead"),
            (82, "Calliope Lead — high-pitched trap bell"),
        ],
        "melody": [(82, "Calliope Lead — trap bell melody")],
        "pads": [(88, "Pad (New Age) — dark ambient trap")],
    },
    "drill": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — drill 808 slide bass"),
            (39, "Synth Bass 2 — aggressive 808"),
        ],
        "chords": [
            (0, "Acoustic Grand Piano — dark drill piano chords"),
            (88, "Pad (New Age) — sinister drill pad"),
        ],
        "lead": [
            (82, "Calliope Lead — drill bell lead"),
            (80, "Square Lead — drill synth"),
        ],
        "melody": [(82, "Calliope Lead — drill bell melody")],
        "pads": [(88, "Pad (New Age) — dark drill atmosphere")],
    },
    "lofi": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — mellow lo-fi bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — lo-fi Rhodes"),
            (0, "Acoustic Grand Piano — lo-fi piano"),
        ],
        "lead": [(73, "Flute — airy lo-fi melody")],
        "melody": [(73, "Flute — lo-fi melody")],
        "pads": [(89, "Warm Pad — lo-fi warmth")],
    },
    "lo-fi": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — mellow lo-fi bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — lo-fi Rhodes"),
            (0, "Acoustic Grand Piano — lo-fi piano"),
        ],
        "lead": [(73, "Flute — airy lo-fi melody")],
        "melody": [(73, "Flute — lo-fi melody")],
        "pads": [(89, "Warm Pad — lo-fi warmth")],
    },

    # =========================================================================
    # Jazz / Soul / R&B / Funk / Gospel
    # =========================================================================
    "jazz": {
        "drums": [],
        "bass": [
            (32, "Acoustic Bass — jazz upright bass"),
            (33, "Electric Bass (Finger) — jazz fusion bass"),
        ],
        "chords": [
            (0, "Acoustic Grand Piano — jazz piano comping"),
            (26, "Electric Guitar (Jazz) — hollow-body jazz guitar"),
        ],
        "lead": [
            (66, "Tenor Sax — jazz sax solo"),
            (56, "Trumpet — jazz trumpet lead"),
        ],
        "melody": [(66, "Tenor Sax — jazz melody")],
        "pads": [(48, "String Ensemble — lush jazz strings")],
    },
    "bebop": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — bebop walking bass (fast chromatic)")],
        "chords": [(0, "Acoustic Grand Piano — bebop piano comping (Bud Powell style)")],
        "lead": [
            (65, "Alto Sax — bebop alto sax (Charlie Parker style)"),
            (56, "Trumpet — bebop trumpet (Dizzy Gillespie style)"),
        ],
        "melody": [(65, "Alto Sax — bebop alto sax")],
        "pads": [],
    },
    "ethio-jazz": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — Ethio-jazz electric bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — Ethio-jazz Rhodes"),
            (16, "Drawbar Organ — Ethio-jazz organ"),
        ],
        "lead": [
            (66, "Tenor Sax — Ethio-jazz sax (Mulatu Astatke style)"),
            (11, "Vibraphone — Ethio-jazz vibes"),
        ],
        "melody": [
            (66, "Tenor Sax — Ethio-jazz sax melody"),
            (73, "Flute — Ethiopian flute"),
        ],
        "pads": [(48, "String Ensemble")],
    },
    "neo-soul": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — neo-soul bass"),
            (35, "Fretless Bass — smooth neo-soul fretless"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — essential neo-soul Rhodes (warm, slightly overdriven)"),
            (5, "Electric Piano 2 (DX7) — crystalline neo-soul FM keys"),
        ],
        "lead": [
            (4, "Electric Piano 1 (Rhodes) — Rhodes solo"),
            (80, "Square Lead — neo-soul synth lead"),
        ],
        "melody": [(4, "Electric Piano 1 (Rhodes) — neo-soul Rhodes melody")],
        "pads": [(89, "Warm Pad — neo-soul warmth")],
    },
    "r&b": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — R&B bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — classic R&B Rhodes"),
            (7, "Clavinet — funky R&B clav"),
        ],
        "lead": [(80, "Square Lead — R&B synth lead")],
        "melody": [(80, "Square Lead — R&B melody")],
        "pads": [(89, "Warm Pad — R&B warmth")],
    },
    "soul": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — Motown soul bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — soul Rhodes"),
            (16, "Drawbar Organ — soul Hammond organ"),
        ],
        "lead": [
            (66, "Tenor Sax — soul sax lead"),
            (56, "Trumpet — soul brass"),
        ],
        "melody": [(66, "Tenor Sax — soul sax melody")],
        "pads": [(48, "String Ensemble — Motown string section")],
    },
    "funk": {
        "drums": [],
        "bass": [
            (36, "Slap Bass 1 — funky slap bass"),
            (33, "Electric Bass (Finger) — funk fingered bass"),
        ],
        "chords": [
            (7, "Clavinet — funk clav (Stevie Wonder style)"),
            (4, "Electric Piano 1 (Rhodes) — funk Rhodes"),
        ],
        "lead": [
            (61, "Brass Section — funk horn stab"),
            (80, "Square Lead — funk synth lead"),
        ],
        "melody": [(61, "Brass Section — funk horn hit")],
        "pads": [(16, "Drawbar Organ — funky organ")],
    },
    "gospel": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — gospel bass")],
        "chords": [
            (16, "Drawbar Organ — gospel Hammond organ (essential)"),
            (0, "Acoustic Grand Piano — gospel piano"),
        ],
        "lead": [
            (0, "Acoustic Grand Piano — gospel piano lead runs"),
            (16, "Drawbar Organ — gospel organ solo"),
        ],
        "melody": [(0, "Acoustic Grand Piano — gospel piano melody")],
        "pads": [
            (19, "Church Organ — sustained gospel organ"),
            (91, "Choir Pad — gospel vocal pad"),
        ],
    },
    "blues": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — blues bass")],
        "chords": [
            (27, "Electric Guitar (Clean) — blues guitar comping"),
            (0, "Acoustic Grand Piano — blues piano"),
        ],
        "lead": [
            (27, "Electric Guitar (Clean) — blues guitar lead"),
            (22, "Harmonica — blues harp"),
        ],
        "melody": [
            (22, "Harmonica — blues harp melody"),
            (27, "Electric Guitar (Clean) — blues guitar"),
        ],
        "pads": [(16, "Drawbar Organ — blues organ")],
    },
    "disco": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — disco bass groove"),
            (36, "Slap Bass 1 — disco slap bass"),
        ],
        "chords": [
            (27, "Electric Guitar (Clean) — disco guitar chop (16th-note wah)"),
            (4, "Electric Piano 1 (Rhodes) — disco Rhodes"),
        ],
        "lead": [
            (62, "Synth Brass 1 — disco brass stab"),
            (80, "Square Lead — disco synth lead"),
        ],
        "melody": [(62, "Synth Brass 1 — disco brass melody")],
        "pads": [(48, "String Ensemble — disco strings (lush ascending)")],
    },

    # =========================================================================
    # Electronic
    # =========================================================================
    "edm": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — EDM sub bass"),
            (81, "Sawtooth Lead — EDM bass growl"),
        ],
        "chords": [
            (90, "Polysynth Pad — EDM supersaw chords"),
            (62, "Synth Brass 1 — EDM brass stab"),
        ],
        "lead": [
            (81, "Sawtooth Lead — EDM main lead"),
            (80, "Square Lead — plucky EDM lead"),
        ],
        "melody": [(81, "Sawtooth Lead — EDM melody")],
        "pads": [(90, "Polysynth Pad — EDM pad wash")],
    },
    "house": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — house bass line"),
            (33, "Electric Bass (Finger) — deep house bass"),
        ],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — deep house Rhodes stabs"),
            (90, "Polysynth Pad — house chord stabs"),
        ],
        "lead": [
            (80, "Square Lead — house synth lead"),
            (62, "Synth Brass 1 — house brass stab"),
        ],
        "melody": [(80, "Square Lead — house melody")],
        "pads": [
            (89, "Warm Pad — deep house warmth"),
            (88, "Pad (New Age) — ambient house pad"),
        ],
    },
    "techno": {
        "drums": [],
        "bass": [(38, "Synth Bass 1 — techno bass (acid/sub)")],
        "chords": [
            (90, "Polysynth Pad — techno chord stab"),
            (88, "Pad (New Age) — techno ambient chord"),
        ],
        "lead": [
            (81, "Sawtooth Lead — melodic techno lead"),
            (80, "Square Lead — classic techno lead"),
        ],
        "melody": [(81, "Sawtooth Lead — melodic techno melody")],
        "pads": [
            (88, "Pad (New Age) — techno ambient pad"),
            (89, "Warm Pad — deep techno pad"),
        ],
        "arp": [(80, "Square Lead — techno arpeggio")],
    },
    "drum and bass": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — DnB reese bass"),
            (81, "Sawtooth Lead — DnB growl bass"),
        ],
        "chords": [
            (88, "Pad (New Age) — DnB atmospheric pad"),
            (90, "Polysynth Pad — liquid DnB chords"),
        ],
        "lead": [
            (80, "Square Lead — DnB synth lead"),
            (81, "Sawtooth Lead — DnB lead"),
        ],
        "melody": [(80, "Square Lead — liquid DnB melody")],
        "pads": [(88, "Pad (New Age) — DnB atmosphere")],
    },
    "dnb": {
        "drums": [],
        "bass": [
            (38, "Synth Bass 1 — DnB reese bass"),
            (81, "Sawtooth Lead — DnB growl bass"),
        ],
        "chords": [(88, "Pad (New Age) — DnB atmospheric pad")],
        "lead": [(80, "Square Lead — DnB synth lead")],
        "melody": [(80, "Square Lead — liquid DnB melody")],
        "pads": [(88, "Pad (New Age) — DnB atmosphere")],
    },
    "dubstep": {
        "drums": [],
        "bass": [
            (81, "Sawtooth Lead — dubstep wobble bass"),
            (38, "Synth Bass 1 — dubstep sub bass"),
        ],
        "chords": [(90, "Polysynth Pad — dubstep chord stab")],
        "lead": [
            (81, "Sawtooth Lead — dubstep screech lead"),
            (80, "Square Lead — dubstep synth"),
        ],
        "melody": [(80, "Square Lead — dubstep melody")],
        "pads": [(88, "Pad (New Age) — dubstep ambient intro pad")],
    },
    "synthwave": {
        "drums": [],
        "bass": [(38, "Synth Bass 1 — analog 80s synth bass")],
        "chords": [
            (90, "Polysynth Pad — big 80s supersaw chords"),
            (62, "Synth Brass 1 — 80s brass stab"),
        ],
        "lead": [
            (81, "Sawtooth Lead — soaring synthwave lead"),
            (80, "Square Lead — retro digital lead"),
        ],
        "melody": [(81, "Sawtooth Lead — synthwave melody")],
        "pads": [(89, "Warm Pad — lush 80s analog pad")],
        "arp": [(80, "Square Lead — synthwave arpeggio")],
    },
    "psytrance": {
        "drums": [],
        "bass": [(38, "Synth Bass 1 — psytrance acid bass line")],
        "chords": [(90, "Polysynth Pad — psytrance stab")],
        "lead": [
            (80, "Square Lead — psytrance squelch lead"),
            (81, "Sawtooth Lead — psytrance lead"),
        ],
        "melody": [(80, "Square Lead — psytrance melody")],
        "pads": [(99, "Atmosphere FX — psychedelic texture")],
        "arp": [(80, "Square Lead — psytrance gated arpeggio")],
    },
    "uk garage": {
        "drums": [],
        "bass": [(38, "Synth Bass 1 — UKG bouncy bass")],
        "chords": [
            (4, "Electric Piano 1 (Rhodes) — chopped UKG Rhodes"),
            (90, "Polysynth Pad — UKG vocal chop chord"),
        ],
        "lead": [(80, "Square Lead — UKG synth lead")],
        "melody": [(52, "Choir Aahs — UKG vocal chop melody")],
        "pads": [(89, "Warm Pad — UKG warm pad")],
    },
    "ambient": {
        "drums": [],
        "bass": [(38, "Synth Bass 1 — ambient sub drone")],
        "chords": [(88, "Pad (New Age) — ambient pad chord")],
        "lead": [
            (88, "Pad (New Age) — ambient melodic texture"),
            (99, "Atmosphere FX — ambient drone texture"),
        ],
        "melody": [(88, "Pad (New Age) — ambient drifting melody")],
        "pads": [
            (88, "Pad (New Age) — ambient pad"),
            (95, "Sweep Pad — ambient evolving sweep"),
        ],
        "arp": [(88, "Pad (New Age) — ambient granular arpeggio")],
    },

    # =========================================================================
    # Rock / Metal / Pop / Indie
    # =========================================================================
    "rock": {
        "drums": [],
        "bass": [(34, "Electric Bass (Pick) — rock bass")],
        "chords": [
            (29, "Overdriven Guitar — rock rhythm guitar"),
            (27, "Electric Guitar (Clean) — clean rock guitar"),
        ],
        "lead": [
            (29, "Overdriven Guitar — rock guitar solo"),
            (30, "Distortion Guitar — heavy rock lead"),
        ],
        "melody": [(29, "Overdriven Guitar — rock melody")],
        "pads": [(48, "String Ensemble — rock power ballad strings")],
    },
    "progressive rock": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — prog bass (complex time)")],
        "chords": [
            (18, "Rock Organ — prog rock organ (Rick Wakeman style)"),
            (0, "Acoustic Grand Piano — prog piano"),
        ],
        "lead": [
            (81, "Sawtooth Lead — prog synth lead (Moog-style)"),
            (29, "Overdriven Guitar — prog guitar solo"),
        ],
        "melody": [(81, "Sawtooth Lead — prog synth melody")],
        "pads": [
            (90, "Polysynth Pad — prog synth bed"),
            (88, "Pad (New Age) — prog ambient texture"),
        ],
    },
    "post-rock": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — post-rock bass (clean, reverbed)")],
        "chords": [
            (27, "Electric Guitar (Clean) — post-rock tremolo guitar (heavy reverb/delay)"),
            (49, "Slow Strings — post-rock string layer"),
        ],
        "lead": [
            (27, "Electric Guitar (Clean) — post-rock guitar with delay"),
            (29, "Overdriven Guitar — post-rock climax guitar"),
        ],
        "melody": [(27, "Electric Guitar (Clean) — post-rock arpeggiated guitar")],
        "pads": [
            (49, "Slow Strings — post-rock string pad"),
            (88, "Pad (New Age) — post-rock ambient texture"),
        ],
    },
    "metal": {
        "drums": [],
        "bass": [(34, "Electric Bass (Pick) — metal bass (tight picking)")],
        "chords": [(30, "Distortion Guitar — metal rhythm (palm-muted chugs)")],
        "lead": [(30, "Distortion Guitar — metal guitar shred solo")],
        "melody": [(30, "Distortion Guitar — metal melody")],
        "pads": [(48, "String Ensemble — symphonic metal strings")],
    },
    "pop": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — pop bass"),
            (38, "Synth Bass 1 — modern pop synth bass"),
        ],
        "chords": [
            (0, "Acoustic Grand Piano — pop piano"),
            (4, "Electric Piano 1 (Rhodes) — pop Rhodes"),
        ],
        "lead": [
            (80, "Square Lead — pop synth lead"),
            (81, "Sawtooth Lead — bright pop lead"),
        ],
        "melody": [(80, "Square Lead — pop melody")],
        "pads": [(88, "Pad (New Age) — pop ambient pad")],
    },
    "indie folk": {
        "drums": [],
        "bass": [
            (32, "Acoustic Bass — folk upright bass"),
            (33, "Electric Bass (Finger) — indie bass"),
        ],
        "chords": [
            (25, "Acoustic Guitar (Steel) — essential folk fingerpicking/strumming"),
            (24, "Nylon Guitar — folk classical guitar"),
        ],
        "lead": [
            (25, "Acoustic Guitar (Steel) — folk lead guitar"),
            (73, "Flute — folk flute"),
        ],
        "melody": [
            (25, "Acoustic Guitar (Steel) — indie folk guitar melody"),
            (40, "Violin — folk fiddle melody"),
        ],
        "pads": [
            (48, "String Ensemble — indie folk string pad"),
            (21, "Accordion — folk accordion"),
        ],
    },
    "ska": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — ska walking bass")],
        "chords": [(27, "Electric Guitar (Clean) — ska upstroke skank (offbeat chop)")],
        "lead": [
            (56, "Trumpet — ska horn section lead"),
            (57, "Trombone — ska trombone"),
        ],
        "melody": [(56, "Trumpet — ska trumpet melody")],
        "pads": [(16, "Drawbar Organ — ska organ")],
    },

    # =========================================================================
    # Latin / World Americas
    # =========================================================================
    "latin": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — Latin bass"),
            (32, "Acoustic Bass — Latin acoustic"),
        ],
        "chords": [
            (0, "Acoustic Grand Piano — Latin montuno"),
            (24, "Nylon Guitar — Latin guitar"),
        ],
        "lead": [
            (56, "Trumpet — Latin brass lead"),
            (73, "Flute — Latin flute"),
        ],
        "melody": [(56, "Trumpet — Latin horn line")],
        "pads": [(48, "String Ensemble — Latin strings")],
    },
    "new orleans": {
        "drums": [],
        "bass": [
            (58, "Tuba — New Orleans sousaphone bass"),
            (32, "Acoustic Bass — NOLA upright"),
        ],
        "chords": [(0, "Acoustic Grand Piano — New Orleans piano (Professor Longhair style)")],
        "lead": [
            (56, "Trumpet — New Orleans trumpet lead (second line)"),
            (57, "Trombone — NOLA trombone"),
        ],
        "melody": [
            (71, "Clarinet — New Orleans clarinet"),
            (56, "Trumpet — second line trumpet"),
        ],
        "pads": [(61, "Brass Section — NOLA brass ensemble")],
    },
    "bluegrass": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — bluegrass upright bass")],
        "chords": [(25, "Acoustic Guitar (Steel) — bluegrass flatpick guitar")],
        "lead": [
            (105, "Banjo — bluegrass banjo rolls (Scruggs style)"),
            (40, "Violin — bluegrass fiddle"),
        ],
        "melody": [
            (105, "Banjo — banjo melody"),
            (40, "Violin — bluegrass fiddle melody"),
        ],
        "pads": [(25, "Acoustic Guitar (Steel) — bluegrass rhythm guitar")],
    },

    # =========================================================================
    # European classical / historical
    # =========================================================================
    "classical": {
        "drums": [],
        "bass": [
            (42, "Cello — classical cello bass line"),
            (43, "Contrabass — orchestral double bass"),
        ],
        "chords": [
            (0, "Acoustic Grand Piano — classical piano"),
            (48, "String Ensemble — orchestral strings"),
        ],
        "lead": [
            (40, "Violin — classical violin solo"),
            (73, "Flute — classical flute"),
        ],
        "melody": [
            (40, "Violin — classical violin melody"),
            (73, "Flute — classical flute melody"),
        ],
        "pads": [(48, "String Ensemble — orchestral string pad")],
    },
    "baroque": {
        "drums": [],
        "bass": [(6, "Harpsichord — baroque basso continuo")],
        "chords": [(6, "Harpsichord — essential baroque keyboard")],
        "lead": [
            (40, "Violin — baroque violin solo"),
            (73, "Flute — baroque flute"),
        ],
        "melody": [
            (40, "Violin — baroque violin"),
            (73, "Flute — baroque traverso flute"),
        ],
        "pads": [(48, "String Ensemble — baroque string ensemble")],
    },
    "cinematic": {
        "drums": [],
        "bass": [
            (42, "Cello — cinematic cello bass"),
            (43, "Contrabass — orchestral sub bass"),
        ],
        "chords": [
            (48, "String Ensemble — cinematic string chords"),
            (0, "Acoustic Grand Piano — cinematic piano"),
        ],
        "lead": [
            (56, "Trumpet — heroic cinematic brass"),
            (60, "French Horn — epic cinematic horn"),
        ],
        "melody": [
            (40, "Violin — cinematic violin melody"),
            (73, "Flute — cinematic flute"),
        ],
        "pads": [
            (48, "String Ensemble — cinematic string pad"),
            (49, "Slow Strings — cinematic sustain"),
        ],
    },
    "minimalist": {
        "drums": [],
        "bass": [(0, "Acoustic Grand Piano — minimalist piano bass register")],
        "chords": [(0, "Acoustic Grand Piano — minimalist piano (Reich/Glass style)")],
        "lead": [
            (0, "Acoustic Grand Piano — minimalist piano patterns"),
            (11, "Vibraphone — minimalist mallet patterns"),
        ],
        "melody": [(0, "Acoustic Grand Piano — minimalist piano melody")],
        "pads": [(48, "String Ensemble — minimalist string sustain")],
        "arp": [(0, "Acoustic Grand Piano — minimalist phasing patterns")],
    },
    "gregorian": {
        "drums": [],
        "bass": [(19, "Church Organ — Gregorian organ drone pedal")],
        "chords": [(19, "Church Organ — Gregorian organ chords")],
        "lead": [(52, "Choir Aahs — Gregorian chant voice")],
        "melody": [(52, "Choir Aahs — Gregorian plainchant melody")],
        "pads": [
            (91, "Choir Pad — Gregorian vocal ambience"),
            (19, "Church Organ — cathedral organ sustain"),
        ],
    },

    # =========================================================================
    # European folk / regional
    # =========================================================================
    "flamenco": {
        "drums": [],
        "bass": [(24, "Nylon Guitar — flamenco bass thumb technique")],
        "chords": [(24, "Nylon Guitar — flamenco rasgueado strumming (essential)")],
        "lead": [(24, "Nylon Guitar — flamenco picado (single-note runs)")],
        "melody": [(24, "Nylon Guitar — flamenco guitar melody")],
        "pads": [(42, "Cello — flamenco cello drone")],
    },
    "klezmer": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — klezmer upright bass")],
        "chords": [
            (21, "Accordion — klezmer accordion"),
            (0, "Acoustic Grand Piano — klezmer piano"),
        ],
        "lead": [(71, "Clarinet — essential klezmer clarinet (laughing/crying ornaments)")],
        "melody": [(71, "Clarinet — klezmer clarinet melody")],
        "pads": [(40, "Violin — klezmer fiddle")],
    },
    "balkan": {
        "drums": [],
        "bass": [(58, "Tuba — Balkan brass tuba bass")],
        "chords": [(61, "Brass Section — Balkan brass ensemble")],
        "lead": [
            (56, "Trumpet — Balkan trumpet lead"),
            (71, "Clarinet — Balkan clarinet"),
        ],
        "melody": [
            (71, "Clarinet — Balkan clarinet melody"),
            (56, "Trumpet — Balkan trumpet"),
        ],
        "pads": [(21, "Accordion — Balkan accordion")],
    },
    "nordic": {
        "drums": [],
        "bass": [
            (32, "Acoustic Bass — Nordic folk upright"),
            (42, "Cello — Nordic cello drone"),
        ],
        "chords": [
            (46, "Orchestral Harp — Nordic harp"),
            (25, "Acoustic Guitar (Steel) — Nordic folk guitar"),
        ],
        "lead": [
            (73, "Flute — Nordic wooden flute"),
            (40, "Violin — Hardanger fiddle style"),
        ],
        "melody": [
            (40, "Violin — Nordic fiddle melody"),
            (73, "Flute — Nordic folk flute"),
        ],
        "pads": [
            (88, "Pad (New Age) — Nordic ambient pad"),
            (49, "Slow Strings — Nordic string drone"),
        ],
    },
    "anatolian": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — Anatolian psych bass")],
        "chords": [
            (104, "Sitar — saz/bağlama-like string instrument"),
            (27, "Electric Guitar (Clean) — Anatolian psych guitar"),
        ],
        "lead": [
            (104, "Sitar — saz/bağlama lead (Anatolian scales)"),
            (68, "Oboe — zurna-like Anatolian double reed"),
        ],
        "melody": [
            (104, "Sitar — bağlama melody"),
            (68, "Oboe — zurna melody"),
        ],
        "pads": [(88, "Pad (New Age) — psychedelic ambient pad")],
    },

    # =========================================================================
    # African
    # =========================================================================
    "afrobeats": {
        "drums": [],
        "bass": [(33, "Electric Bass (Finger) — Afrobeats bass groove")],
        "chords": [
            (27, "Electric Guitar (Clean) — Afrobeats guitar pattern (palm-muted staccato)"),
            (4, "Electric Piano 1 (Rhodes) — Afrobeats keys"),
        ],
        "lead": [
            (80, "Square Lead — Afrobeats synth lead"),
            (56, "Trumpet — Afrobeats horn accent"),
        ],
        "melody": [
            (80, "Square Lead — Afrobeats melody"),
            (27, "Electric Guitar (Clean) — Afrobeats guitar lead"),
        ],
        "pads": [(88, "Pad (New Age) — Afrobeats ambient pad")],
    },
    "west african": {
        "drums": [],
        "bass": [
            (33, "Electric Bass (Finger) — West African bass"),
            (108, "Kalimba — West African thumb piano bass"),
        ],
        "chords": [
            (108, "Kalimba — West African kalimba/balafon pattern"),
            (24, "Nylon Guitar — West African kora-like"),
        ],
        "lead": [
            (108, "Kalimba — West African balafon lead"),
            (24, "Nylon Guitar — kora-style lead"),
        ],
        "melody": [(108, "Kalimba — West African melody")],
        "pads": [(52, "Choir Aahs — West African vocal texture")],
    },
    "gnawa": {
        "drums": [],
        "bass": [(32, "Acoustic Bass — sintir/guembri-like bass (deep, buzzy)")],
        "chords": [
            (108, "Kalimba — qraqeb metallic percussion pattern"),
            (113, "Agogo — qraqeb-like metal castanets"),
        ],
        "lead": [
            (32, "Acoustic Bass — guembri lead (bass and melody combined)"),
            (52, "Choir Aahs — Gnawa call-and-response vocal"),
        ],
        "melody": [(52, "Choir Aahs — Gnawa vocal melody")],
        "pads": [(91, "Choir Pad — Gnawa trance vocal drone")],
    },

    # =========================================================================
    # Middle Eastern / South Asian / Sufi
    # =========================================================================
    "arabic": {
        "drums": [],
        "bass": [(24, "Nylon Guitar — oud-like bass register")],
        "chords": [
            (24, "Nylon Guitar — oud (essential Arabic plucked string)"),
            (104, "Sitar — Arabic kanun/qanun-like"),
        ],
        "lead": [
            (74, "Recorder — ney-like Arabic flute"),
            (68, "Oboe — mizmar/zurna-like double reed"),
        ],
        "melody": [
            (24, "Nylon Guitar — oud maqam melody"),
            (74, "Recorder — ney melody (Arabic microtonal flute)"),
        ],
        "pads": [(91, "Choir Pad — Arabic vocal texture")],
    },
    "maqam": {
        "drums": [],
        "bass": [(24, "Nylon Guitar — oud bass")],
        "chords": [(24, "Nylon Guitar — oud maqam chords")],
        "lead": [
            (74, "Recorder — ney (Arabic flute, microtonal scales)"),
            (24, "Nylon Guitar — oud taqsim (improvised maqam lead)"),
        ],
        "melody": [(24, "Nylon Guitar — oud maqam melody")],
        "pads": [(91, "Choir Pad — maqam vocal texture")],
    },
    "qawwali": {
        "drums": [],
        "bass": [(22, "Harmonica — harmonium drone (essential qawwali)")],
        "chords": [
            (22, "Harmonica — harmonium chord drone (pump organ style)"),
            (21, "Accordion — harmonium variant"),
        ],
        "lead": [
            (52, "Choir Aahs — qawwali lead vocal"),
            (22, "Harmonica — harmonium solo passage"),
        ],
        "melody": [(52, "Choir Aahs — qawwali vocal melody (Nusrat style)")],
        "pads": [(91, "Choir Pad — qawwali group vocal response")],
    },
    "sufi": {
        "drums": [],
        "bass": [(24, "Nylon Guitar — oud/tanbur drone")],
        "chords": [
            (22, "Harmonica — harmonium sustain"),
            (24, "Nylon Guitar — oud drone chords"),
        ],
        "lead": [
            (74, "Recorder — ney (Sufi reed flute, breathy, microtonal)"),
            (52, "Choir Aahs — Sufi dhikr vocal"),
        ],
        "melody": [(74, "Recorder — ney meditation melody")],
        "pads": [(91, "Choir Pad — Sufi vocal ambience")],
    },
    "hindustani": {
        "drums": [],
        "bass": [(104, "Sitar — tanpura drone (essential raga drone)")],
        "chords": [(104, "Sitar — tanpura/sitar chordal drone")],
        "lead": [
            (104, "Sitar — sitar lead (alap, jor, jhala)"),
            (73, "Flute — bansuri (North Indian bamboo flute)"),
        ],
        "melody": [
            (104, "Sitar — sitar raga melody"),
            (73, "Flute — bansuri raga melody"),
        ],
        "pads": [(47, "Timpani — tabla-like sustained drone")],
    },
    "raga": {
        "drums": [],
        "bass": [(104, "Sitar — tanpura drone")],
        "chords": [(104, "Sitar — tanpura/sitar drone chords")],
        "lead": [
            (104, "Sitar — sitar raga lead"),
            (73, "Flute — bansuri lead"),
        ],
        "melody": [(104, "Sitar — sitar melody")],
        "pads": [(47, "Timpani — tabla-like drone")],
    },

    # =========================================================================
    # East Asian / Southeast Asian / Pacific
    # =========================================================================
    "gamelan": {
        "drums": [],
        "bass": [(12, "Marimba — Balinese jegogan/calung-like bass metalophone")],
        "chords": [(11, "Vibraphone — gamelan metalophone interlocking pattern")],
        "lead": [
            (11, "Vibraphone — gamelan kantilan/gangsa (high metalophone)"),
            (13, "Xylophone — gamelan gender wayang"),
        ],
        "melody": [(11, "Vibraphone — gamelan melody (pokok)")],
        "pads": [(9, "Glockenspiel — gamelan shimmering metalophone texture")],
    },
    "japanese": {
        "drums": [],
        "bass": [(107, "Koto — koto bass register")],
        "chords": [(107, "Koto — koto arpeggiated chords")],
        "lead": [
            (77, "Shakuhachi — essential Japanese zen bamboo flute"),
            (107, "Koto — koto melody"),
        ],
        "melody": [
            (77, "Shakuhachi — shakuhachi zen melody"),
            (107, "Koto — koto melody"),
        ],
        "pads": [(88, "Pad (New Age) — zen ambient texture")],
    },
    "zen": {
        "drums": [],
        "bass": [(107, "Koto — koto bass drone")],
        "chords": [(107, "Koto — koto arpeggiated pattern")],
        "lead": [(77, "Shakuhachi — shakuhachi zen flute (breathy, meditative)")],
        "melody": [(77, "Shakuhachi — shakuhachi zen melody")],
        "pads": [
            (88, "Pad (New Age) — zen ambient pad"),
            (107, "Koto — koto harmonic wash"),
        ],
    },
    "korean": {
        "drums": [],
        "bass": [(107, "Koto — gayageum-like bass register")],
        "chords": [(107, "Koto — gayageum/geomungo chordal pattern")],
        "lead": [
            (107, "Koto — gayageum sanjo lead (virtuosic bending)"),
            (77, "Shakuhachi — daegeum-like Korean flute"),
        ],
        "melody": [(107, "Koto — gayageum melody")],
        "pads": [(52, "Choir Aahs — pansori vocal texture")],
    },
    "sanjo": {
        "drums": [],
        "bass": [(107, "Koto — gayageum bass")],
        "chords": [(107, "Koto — gayageum/geomungo")],
        "lead": [(107, "Koto — gayageum sanjo lead")],
        "melody": [(107, "Koto — gayageum sanjo melody")],
        "pads": [(52, "Choir Aahs — pansori vocal")],
    },
    "polynesian": {
        "drums": [],
        "bass": [(116, "Taiko Drum — taiko bass drum pattern")],
        "chords": [
            (108, "Kalimba — Pacific island percussion pattern"),
            (114, "Steel Drums — Pacific island steel pan"),
        ],
        "lead": [
            (75, "Pan Flute — Polynesian nose flute"),
            (78, "Whistle — Pacific island whistle"),
        ],
        "melody": [(75, "Pan Flute — Polynesian flute melody")],
        "pads": [(52, "Choir Aahs — Polynesian choral texture")],
    },
    "taiko": {
        "drums": [],
        "bass": [(116, "Taiko Drum — taiko odaiko bass")],
        "chords": [(116, "Taiko Drum — taiko ensemble pattern")],
        "lead": [
            (77, "Shakuhachi — Japanese shakuhachi accent"),
            (116, "Taiko Drum — taiko solo"),
        ],
        "melody": [(77, "Shakuhachi — shakuhachi melody")],
        "pads": [(88, "Pad (New Age) — ambient taiko reverb pad")],
    },
}


def _normalize_genre_key(text: str) -> str:
    """Normalize a genre string for matching: lowercase, strip hyphens/underscores."""
    return text.lower().strip().replace("-", " ").replace("_", " ")


def get_genre_gm_guidance(style: str, role: str) -> str:
    """Return genre-specific GM program guidance for an instrument agent.

    If the genre has specific voice recommendations, returns a formatted
    string listing recommended GM programs. Returns empty string if no
    genre-specific guidance exists.

    Matching strategy: exact key match first, then bidirectional substring
    match with hyphen/underscore normalization.
    """
    style_lower = style.lower().strip()
    style_norm = _normalize_genre_key(style)

    # Exact match on canonical key
    genre_map = GENRE_GM_GUIDANCE.get(style_lower)

    # Substring match (both directions) with normalization
    if not genre_map:
        for genre_key, gmap in GENRE_GM_GUIDANCE.items():
            key_norm = _normalize_genre_key(genre_key)
            if key_norm in style_norm or style_norm in key_norm:
                genre_map = gmap
                break

    # Fallback: try matching individual words from the style
    if not genre_map:
        style_words = style_norm.split()
        for genre_key, gmap in GENRE_GM_GUIDANCE.items():
            key_norm = _normalize_genre_key(genre_key)
            if any(key_norm == w for w in style_words):
                genre_map = gmap
                break

    if not genre_map:
        return ""

    role_lower = role.lower().strip()
    programs = genre_map.get(role_lower, [])
    if not programs:
        return ""

    lines = [f"GENRE GM VOICE GUIDANCE ({style}):", "Recommended GM programs for this role:"]
    for prog, desc in programs:
        lines.append(f"  - gmProgram={prog}: {desc}")
    lines.append("Use the FIRST option unless the track name or prompt strongly suggests another.")
    return "\n".join(lines)


def get_default_program_for_role(role: str) -> int | None:
    """
    Get a sensible default GM program for a musical role.
    
    Used by the planner/generator when creating tracks for specific roles.
    
    Args:
        role: Musical role like "bass", "chords", "melody", "drums"
        
    Returns:
        GM program number or None (for drums)
    """
    role_lower = role.lower().strip()
    
    role_to_program: dict[str, int | None] = {
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
    program: int | None  # GM program (0-127) or None for drums
    instrument_name: str    # Human-readable name
    confidence: str         # "high", "medium", "low", "none"
    is_drums: bool          # True if this should use channel 10
    
    @property
    def needs_program_change(self) -> bool:
        """Returns True if a MIDI program change should be sent."""
        return self.program is not None and not self.is_drums


def infer_gm_program_with_context(
    track_name: str | None = None,
    instrument: str | None = None,
    role: str | None = None,
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
    (range(16, 24),   "pianokeys"),              # Organ (keyboard family)
    (range(24, 32),   "guitars.fill"),            # Guitar
    (range(32, 40),   "guitars.fill"),            # Bass (guitar family)
    (range(40, 48),   "instrument.violin"),       # Strings
    (range(48, 56),   "instrument.violin"),       # Ensemble / Strings
    (range(56, 64),   "instrument.trumpet"),      # Brass
    (range(64, 72),   "instrument.saxophone"),    # Reed
    (range(72, 80),   "instrument.flute"),        # Pipe
    (range(80, 88),   "pianokeys.inverse"),       # Synth Lead
    (range(88, 96),   "pianokeys.inverse"),       # Synth Pad
    (range(96, 104),  "sparkles"),                # Synth Effects
    (range(104, 112), "globe"),                   # Ethnic
    (range(112, 120), "instrument.drum"),         # Percussive
    (range(120, 128), "sparkles"),                # Sound Effects
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
