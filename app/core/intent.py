"""
Intent routing for Stori Composer.

Routes user prompts to the appropriate execution path (THINKING/EDITING/COMPOSING).

Uses centralized configuration from intent_config.py for:
- Intent → Tool allowlist mapping
- Intent → SSE State routing
- Intent → Execution policy

Entrypoints:
    # Sync: pattern-only (fast)
    result = get_intent_result(prompt, project_context)
    
    # Async: patterns + LLM fallback (comprehensive)
    result = await get_intent_result_with_llm(prompt, project_context, llm)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.intent_config import (
    Intent,
    SSEState,
    IntentConfig,
    IdiomMatch,
    get_intent_config,
    get_allowed_tools_for_intent,
    match_producer_idiom,
    INTENT_CONFIGS,
)
from app.core.tools import ALL_TOOLS, build_tool_registry, ToolKind
from app.core.prompts import intent_classification_prompt, INTENT_CLASSIFICATION_SYSTEM

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slots:
    """Extracted slots from user prompt."""
    action: Optional[str] = None
    target_type: Optional[str] = None
    target_name: Optional[str] = None
    amount: Optional[float] = None
    amount_unit: Optional[str] = None
    direction: Optional[str] = None
    value_str: Optional[str] = None
    idiom_match: Optional[IdiomMatch] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    sse_state: SSEState
    confidence: float
    slots: Slots
    tools: list[dict[str, Any]]
    allowed_tool_names: set[str]
    tool_choice: str | dict | None
    force_stop_after: bool
    requires_planner: bool = False
    reasons: tuple[str, ...] = ()
    
    @property
    def needs_llm_fallback(self) -> bool:
        """Check if this result should trigger LLM classification."""
        return self.intent == Intent.UNKNOWN and self.confidence < 0.5


# =============================================================================
# Text Normalization
# =============================================================================

_FILLER = {
    # Politeness
    "please", "pls", "plz", "please can you", "please could you",
    "thank you", "thanks", "thx", "ty",
    # Greetings/attention getters
    "hey", "hi", "hello", "yo", "sup", "wassup", "whats up", "what's up",
    # Hedges/uncertainty
    "umm", "uh", "uhh", "um", "hmm", "well", "so", "like", "kinda", "sorta",
    "maybe", "perhaps", "i think", "i guess", "probably",
    # Polite requests
    "can you", "could you", "would you", "will you", "can u", "could u", "would u",
    "i want you to", "i need you to", "i'd like you to", "id like you to",
    "would you mind", "could you please", "can you please",
    # Intensifiers (often removable)
    "just", "really", "very", "quite", "pretty", "super", "totally",
    # Conversational fillers
    "you know", "i mean", "basically", "actually", "literally",
}


def normalize(text: str) -> str:
    """Normalize text for pattern matching."""
    t = text.strip().lower()
    t = t.replace(""", '"').replace(""", '"').replace("'", "'")
    t = re.sub(r"\s+", " ", t)
    for f in sorted(_FILLER, key=len, reverse=True):
        t = t.replace(f, "")
    return re.sub(r"\s+", " ", t).strip()


def _extract_quoted(text: str) -> Optional[str]:
    """Extract quoted string from text."""
    m = re.search(r'"([^"]+)"', text)
    if m:
        return m.group(1).strip()
    m = re.search(r"'([^']+)'", text)
    if m:
        return m.group(1).strip()
    return None


def _num(x: str) -> Optional[float]:
    """Parse number from string."""
    try:
        return float(x)
    except Exception:
        return None


# =============================================================================
# Pattern Rules
# =============================================================================

@dataclass(frozen=True)
class Rule:
    """A pattern-based intent rule."""
    name: str
    intent: Intent
    pattern: re.Pattern
    confidence: float
    slot_extractor: Optional[str] = None  # Name of slot extraction function


RULES: list[Rule] = [
    # Transport
    Rule("play", Intent.PLAY, re.compile(r"^(play|start|begin)$"), 0.99),
    Rule("stop", Intent.STOP, re.compile(r"^(stop|pause)$"), 0.99),
    
    # UI
    Rule("show_panel", Intent.UI_SHOW_PANEL,
         re.compile(r"^(show|hide|open|close) (the )?(mixer|inspector|piano ?roll|step ?sequencer)\b"), 0.92),
    Rule("zoom_pct", Intent.UI_SET_ZOOM,
         re.compile(r"^set (the )?zoom( to)? (?P<pct>\d+)%$"), 0.9),
    Rule("zoom_dir", Intent.UI_SET_ZOOM,
         re.compile(r"^zoom (in|out)$"), 0.95),
    
    # Project
    Rule("tempo", Intent.PROJECT_SET_TEMPO,
         re.compile(r"^(set|change) (the )?(tempo|bpm)( to)? (?P<bpm>\d{2,3})(\.\d+)?$|^(tempo|bpm) (?P<bpm2>\d{2,3})(\.\d+)?$"), 0.93,
         "extract_tempo"),
    Rule("key", Intent.PROJECT_SET_KEY,
         re.compile(r"^(set|change) (the )?key( to)? (?P<key>[A-Ga-g][#b]?m?)"), 0.90),
    
    # Track
    Rule("add_track", Intent.TRACK_ADD,
         re.compile(r"^(add|create) (a )?(new )?(midi )?(\w+(\s+\w+)*\s+)?(track|drum track|bass track|piano track|guitar track|melody track)\b"), 0.82),
    Rule("rename_track", Intent.TRACK_RENAME,
         re.compile(r"^(rename|name) (the )?(.+? )?(track)?\b"), 0.75),
    Rule("mute_track", Intent.TRACK_MUTE,
         re.compile(r"^(mute|unmute) (the )?(.+? )?(track)?\b"), 0.85),
    Rule("solo_track", Intent.TRACK_SOLO,
         re.compile(r"^(solo|unsolo) (the )?(.+? )?(track)?\b"), 0.85),
    Rule("set_volume", Intent.TRACK_SET_VOLUME,
         re.compile(r"^set (the )?(.+? )?(volume|level)( to)? (?P<vol>-?\d+(\.\d+)?)\s*(db)?\b"), 0.85,
         "extract_volume"),
    Rule("set_pan", Intent.TRACK_SET_PAN,
         re.compile(r"^(pan|set pan)( (the )?(.+? )?(track)?)?( to)? (?P<pan>-?\d+|left|right|center)\b"), 0.85),
    Rule("set_icon", Intent.TRACK_SET_ICON,
         re.compile(r"^set (the )?.*icon\b"), 0.75),
    Rule("set_color", Intent.TRACK_SET_COLOR,
         re.compile(r"^set (the )?.*color\b"), 0.75),
    
    # Effects
    Rule("add_effect", Intent.FX_ADD_INSERT,
         re.compile(r"^add (a )?(compressor|eq|reverb|delay|chorus|flanger|phaser|distortion|limiter|gate)( to)?"), 0.80),
    
    # Notes
    Rule("quantize", Intent.NOTES_QUANTIZE,
         re.compile(r"^quantize"), 0.85),
    Rule("swing", Intent.NOTES_SWING,
         re.compile(r"^(add )?swing"), 0.85),
]


def _extract_slots(rule: Rule, m: re.Match, raw: str, norm: str) -> Slots:
    """Extract slots based on rule and match."""
    if rule.intent == Intent.PROJECT_SET_TEMPO:
        bpm = m.group("bpm") or m.groupdict().get("bpm2")
        return Slots(
            target_type="project",
            action="set_tempo",
            amount=_num(bpm) if bpm else None,
            amount_unit="bpm",
            value_str=bpm,
        )
    
    if rule.intent == Intent.UI_SET_ZOOM:
        pct = m.groupdict().get("pct")
        if pct:
            return Slots(target_type="ui", action="zoom", amount=_num(pct), amount_unit="percent", value_str=pct)
        d = norm.split()[-1]
        return Slots(target_type="ui", action="zoom", direction=d, value_str=raw)
    
    if rule.intent == Intent.TRACK_ADD:
        return Slots(target_type="track", action="add", value_str=raw)
    
    if rule.intent == Intent.UI_SHOW_PANEL:
        panel = m.group(3)
        verb = m.group(1)
        visible = verb in ("show", "open")
        return Slots(
            target_type="panel",
            target_name=panel.replace(" ", "_"),
            action="show",
            extras={"visible": visible},
            value_str=raw,
        )
    
    if rule.intent == Intent.TRACK_SET_VOLUME:
        vol = m.groupdict().get("vol")
        return Slots(target_type="track", action="set_volume", amount=_num(vol) if vol else None, amount_unit="dB", value_str=raw)
    
    if rule.intent == Intent.TRACK_SET_PAN:
        pan = m.groupdict().get("pan")
        return Slots(target_type="track", action="set_pan", value_str=pan)
    
    return Slots(value_str=raw)


# =============================================================================
# Question Detection
# =============================================================================

_QUESTION_START = re.compile(
    r"^(what( is| are| does| do| was| were| will| would| can| should|'s|s)|"
    r"how( do i| to| can| does| do| did| is| are| was| were| will| would| should)|"
    r"where( is| are| can i| can you| do i| do you| does| did| was| were| will)|"
    r"why( is| are| does| do| did| was| were| will| would| should|'s|s)|"
    r"when( is| are| should| do| does| did| was| were| will| would)|"
    r"who( is| are| does| do| did| was| were| can| should)|"
    r"which( is| are| one| ones| do| does)|"
    r"can (i|you|we|it)|could (i|you|we|it)|"
    r"should (i|you|we|it)|would (i|you|we|it)|"
    r"will (i|you|we|it|this)|shall (i|you|we)|"
    r"is (there|this|that|it)|are (there|these|those)|"
    r"do (i|you|we|they)|does (it|this|that)|"
    r"did (i|you|we|they|it))"
)

_STORI_KEYWORDS = (
    # App name
    "stori", "stori app", "this app", "this daw", "this program",
    # UI components
    "piano roll", "pianoroll", "step sequencer", "stepsequencer",
    "mixer", "mix console", "inspector", "properties panel",
    "timeline", "arrange view", "arrangement",
    # Core concepts
    "track", "tracks", "midi track", "audio track",
    "region", "regions", "midi region", "audio region",
    "midi", "midi note", "midi notes", "midi data",
    "quantize", "quantization", "swing", "groove",
    "automation", "modulation", "velocity",
    # Actions/features
    "generate", "generation", "compose", "composition",
    "record", "recording", "playback", "loop", "looping",
    "export", "bounce", "render", "mixdown",
    # Effects/processing
    "effect", "effects", "plugin", "plugins", "vst",
    "compressor", "eq", "equalizer", "reverb", "delay",
)


def _is_question(norm: str) -> bool:
    """Check if text looks like a question."""
    return bool(_QUESTION_START.search(norm)) or norm.endswith("?")


def _is_stori_question(norm: str) -> bool:
    """Check if question is about Stori specifically."""
    return _is_question(norm) and any(k in norm for k in _STORI_KEYWORDS)


# =============================================================================
# Generation Detection
# =============================================================================

_GENERATION_PHRASES = (
    # Beat/drum creation
    "make a beat", "create a beat", "make beat", "create beat",
    "generate a beat", "generate beat", "build a beat", "build beat",
    "make drums", "create drums", "generate drums", "build drums",
    "make a drum", "create a drum", "generate a drum",
    "drum pattern", "drum loop", "drum groove",
    # Specific genres/styles
    "boom bap", "trap beat", "lofi beat", "lo fi beat", "lo-fi beat",
    "house beat", "techno beat", "hip hop beat", "hiphop beat",
    "jazz drums", "rock drums", "funk drums",
    # Basslines
    "make a bassline", "create a bassline", "write a bassline",
    "make bass", "create bass", "generate bass", "write bass",
    "bass line", "bass groove", "bass pattern",
    # Chords
    "make chords", "create chords", "generate chords", "write chords",
    "chord progression", "chord pattern", "jazz chords",
    # Melody
    "make a melody", "create a melody", "generate a melody", "write a melody",
    "make melody", "create melody", "generate melody", "write melody",
    "melodic line", "melody line",
    # General composition verbs
    "compose", "compose a", "compose some",
    "generate a", "generate some", "generate an",
    "make a", "make some", "make an",
    "create a", "create some", "create an",
    "build a", "build some", "build an",
    "write a", "write some", "write an",
    # Requests
    "give me a beat", "give me drums", "give me bass", "give me chords",
    "i want a beat", "i want drums", "i want bass", "i want chords",
    "i need a beat", "i need drums", "i need bass", "i need chords",
    # Colloquial
    "lay down some drums", "lay down drums", "lay down a beat",
    "drop a beat", "drop some drums", "cook up a beat",
    "whip up a beat", "throw together a beat",
    # Scales/patterns
    "dorian scale", "pentatonic scale", "major scale", "minor scale",
    "blues scale", "chromatic scale",
)


def _is_generation_request(norm: str) -> bool:
    """Check if text is a music generation request."""
    return any(phrase in norm for phrase in _GENERATION_PHRASES)


# =============================================================================
# Vague Request Detection
# =============================================================================

_VAGUE_PHRASES = (
    # Generic actions without clear target
    "make it better", "fix it", "do it", "change it", "just make it",
    "make this better", "fix this", "change this", "make it good",
    "make it nice", "fix that", "change that", "do that",
    "improve it", "improve this", "improve that", "enhance it",
    "enhance this", "enhance that", "update it", "update this", "update that",
    "modify it", "modify this", "modify that", "adjust it", "adjust this",
    "tweak it", "tweak this", "tweak that", "refine it", "refine this",
    # Too general
    "make something", "add something", "create something", "do something",
    "fix something", "change something", "help me", "help me with this",
    "work on it", "work on this", "work on that",
)

# Affirmative responses that need conversation context
_AFFIRMATIVE_PHRASES = (
    # Basic affirmatives
    "yes", "yep", "yeah", "yup", "yea", "ya", "aye",
    # Enthusiastic
    "yes please", "yes pls", "hell yes", "hell yeah", "absolutely", "definitely",
    "for sure", "of course", "certainly", "indeed", "affirmative",
    # Polite/formal
    "sure", "sure thing", "sounds good", "sounds great", "that works",
    "that sounds good", "that sounds great", "that'd be great", "that would be great",
    "perfect", "excellent", "wonderful", "fantastic",
    # Casual/informal
    "ok", "okay", "k", "kk", "alright", "aight", "all right",
    "cool", "sweet", "nice", "great", "awesome", "dope", "bet",
    # Action-oriented
    "go ahead", "go for it", "do it", "let's do it", "lets do it",
    "let do it", "proceed", "continue", "make it happen",
    "go on", "carry on", "keep going",
    # Agreement
    "agreed", "i agree", "sounds like a plan", "works for me",
    "im in", "i'm in", "im down", "i'm down", "lets go", "let's go",
    # Confirmation
    "correct", "right", "exactly", "precisely", "that's right",
    "thats right", "you got it", "youve got it", "you've got it",
    # British/regional
    "right then", "brilliant", "lovely", "cheers", "righto",
    # Short enthusiastic
    "yass", "yaaas", "yee", "yessir", "yes sir", "yesss",
)


def _is_vague(norm: str) -> bool:
    """Check if request is too vague to act on."""
    return any(v in norm for v in _VAGUE_PHRASES)


# Negative responses (rejections)
_NEGATIVE_PHRASES = (
    # Basic negatives
    "no", "nope", "nah", "naw", "na", "nay",
    # Strong negatives
    "no thanks", "no thank you", "no thx", "hell no", "definitely not",
    "absolutely not", "no way", "not at all", "never", "never mind",
    "nevermind", "forget it", "dont", "don't", "dont do it", "don't do it",
    # Polite declines
    "not right now", "not now", "maybe later", "not yet", "not this time",
    "i changed my mind", "ive changed my mind", "i've changed my mind",
    # Cancellations
    "cancel", "stop", "abort", "undo", "go back", "back",
    "scratch that", "disregard", "ignore that", "skip it", "skip that",
    # Alternative requests
    "different", "something else", "try again", "not that", "not quite",
)


def _is_affirmative(norm: str) -> bool:
    """Check if request is an affirmative confirmation."""
    # Short responses that are just affirmative
    if norm in _AFFIRMATIVE_PHRASES:
        return True
    # Affirmative with minimal words (e.g., "yes please", "sure thing")
    words = norm.split()
    if len(words) <= 3 and any(affirm in norm for affirm in _AFFIRMATIVE_PHRASES):
        return True
    return False


def _is_negative(norm: str) -> bool:
    """Check if request is a negative rejection."""
    # Short responses that are just negative
    if norm in _NEGATIVE_PHRASES:
        return True
    # Negative with minimal words
    words = norm.split()
    if len(words) <= 3 and any(neg in norm for neg in _NEGATIVE_PHRASES):
        return True
    return False


# =============================================================================
# Intent Result Builder
# =============================================================================

def _build_result(
    intent: Intent,
    confidence: float,
    slots: Slots,
    reasons: tuple[str, ...],
) -> IntentResult:
    """Build IntentResult from intent using centralized config."""
    config = get_intent_config(intent)
    
    return IntentResult(
        intent=intent,
        sse_state=config.sse_state,
        confidence=confidence,
        slots=slots,
        tools=ALL_TOOLS,
        allowed_tool_names=set(config.allowed_tools),
        tool_choice=config.tool_choice,
        force_stop_after=config.force_stop_after,
        requires_planner=config.requires_planner,
        reasons=reasons,
    )


def _clarify(raw: str, reason: str) -> IntentResult:
    """Return a clarification-needed result."""
    return _build_result(
        Intent.NEEDS_CLARIFICATION,
        confidence=0.6,
        slots=Slots(value_str=raw),
        reasons=(f"clarify:{reason}",),
    )


# =============================================================================
# Main Entrypoint (sync - pattern only)
# =============================================================================

def get_intent_result(prompt: str, project_context: Optional[dict[str, Any]] = None) -> IntentResult:
    """
    Synchronous intent routing using patterns only.
    
    For comprehensive routing with LLM fallback, use get_intent_result_with_llm().
    """
    raw = prompt
    norm = normalize(prompt)
    
    # 1) Questions route to thinking
    if _is_question(norm):
        intent = Intent.ASK_STORI_DOCS if _is_stori_question(norm) else Intent.ASK_GENERAL
        return _build_result(intent, 0.75, Slots(value_str=raw), ("question",))
    
    # 2) High precision pattern rules
    for rule in RULES:
        m = rule.pattern.search(norm)
        if m:
            slots = _extract_slots(rule, m, raw, norm)
            return _build_result(rule.intent, rule.confidence, slots, (f"rule:{rule.name}",))
    
    # 3) Vague requests -> clarify (before idiom check)
    if _is_vague(norm):
        return _clarify(raw, "vague")
    
    # 4) Producer idioms with polarity
    idiom = match_producer_idiom(norm)
    if idiom:
        slots = Slots(
            value_str=raw,
            idiom_match=idiom,
            direction=idiom.direction,
            extras={"target": idiom.target, "matched_phrase": idiom.phrase},
        )
        return _build_result(idiom.intent, 0.85, slots, (f"idiom:{idiom.phrase}",))
    
    # 5) "Add...to..." pattern - adding to existing entities (EDITING not COMPOSING)
    # This must come BEFORE generation check to prevent "add a major scale to X"
    # from being classified as GENERATE_MUSIC
    if ("add" in norm or "insert" in norm or "write" in norm) and " to " in norm:
        # User is adding to existing track/region, not generating new music
        # Examples: "add a scale to the guitar track", "add notes to the region"
        return _build_result(
            Intent.NOTES_ADD,
            confidence=0.82,
            slots=Slots(value_str=raw, action="add", target_type="notes"),
            reasons=("add_to_existing",),
        )
    
    # 6) Generation requests (only if not "add...to" pattern)
    if _is_generation_request(norm):
        return _build_result(
            Intent.GENERATE_MUSIC,
            confidence=0.80,
            slots=Slots(value_str=raw),
            reasons=("generation_phrase",),
        )
    
    # 7) Unknown - may need LLM fallback
    return _build_result(
        Intent.UNKNOWN,
        confidence=0.25,
        slots=Slots(value_str=raw),
        reasons=("no_match",),
    )


# =============================================================================
# LLM Classification (async fallback)
# =============================================================================

async def classify_with_llm(prompt: str, llm) -> tuple[str, float]:
    """Use LLM to classify intent when patterns fail."""
    try:
        response = await llm.chat(
            system=INTENT_CLASSIFICATION_SYSTEM,
            user=intent_classification_prompt(prompt),
            tools=[],
            tool_choice="none",
            context={},
        )
        
        category = (response.content or "other").strip().lower()
        
        category_map = {
            "transport": "transport",
            "track_edit": "track",
            "track": "track",
            "region_edit": "region",
            "region": "region",
            "effects": "effects",
            "mix_vibe": "mix",
            "mix": "mix",
            "generation": "generation",
            "question": "question",
            "clarify": "clarify",
            "other": "other",
        }
        
        normalized = category_map.get(category, "other")
        confidence = 0.75 if normalized != "other" else 0.4
        
        logger.info(f"🤖 LLM classified '{prompt[:50]}...' as '{normalized}' (conf: {confidence})")
        return normalized, confidence
        
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")
        return "other", 0.3


def _category_to_result(category: str, confidence: float, raw: str, norm: str) -> IntentResult:
    """Convert LLM category to IntentResult."""
    slots = Slots(value_str=raw)
    
    intent_map = {
        "transport": Intent.PLAY,
        "track": Intent.TRACK_ADD,
        "region": Intent.REGION_ADD,
        "effects": Intent.FX_ADD_INSERT,
        "mix": Intent.MIX_ENERGY,
        "generation": Intent.GENERATE_MUSIC,
        "question": Intent.ASK_STORI_DOCS if _is_stori_question(norm) else Intent.ASK_GENERAL,
        "clarify": Intent.NEEDS_CLARIFICATION,
        "other": Intent.UNKNOWN,
    }
    
    intent = intent_map.get(category, Intent.UNKNOWN)
    return _build_result(intent, confidence, slots, (f"llm:{category}",))


async def get_intent_result_with_llm(
    prompt: str,
    project_context: Optional[dict[str, Any]] = None,
    llm=None,
    conversation_history: Optional[list[dict[str, Any]]] = None,
) -> IntentResult:
    """
    Comprehensive intent routing with LLM fallback.
    
    Flow:
    1. Check for affirmative responses with conversation context
    2. Try pattern-based routing (fast)
    3. If UNKNOWN with low confidence, use LLM classification
    4. Convert classification to IntentResult
    """
    norm = normalize(prompt)
    conversation_history = conversation_history or []
    
    # Special handling for affirmative responses - inherit previous intent
    if _is_affirmative(norm) and conversation_history:
        # Look for the last assistant message that might have asked for confirmation
        for msg in reversed(conversation_history):
            if msg.get("role") == "assistant":
                content = msg.get("content", "").lower()
                # Check if assistant asked a question or suggested something
                if "?" in content or any(word in content for word in ["would you like", "should i", "want me to"]):
                    # This is likely a confirmation - route to COMPOSING to enable tools
                    logger.info(f"🔄 Detected affirmative response to previous question, routing to COMPOSING")
                    return _build_result(
                        Intent.GENERATE_MUSIC,
                        confidence=0.85,
                        slots=Slots(value_str=prompt),
                        reasons=("affirmative_confirmation",),
                    )
                break
    
    result = get_intent_result(prompt, project_context)
    
    if not result.needs_llm_fallback or llm is None:
        return result
    
    logger.info(f"🤖 Pattern routing returned UNKNOWN, using LLM fallback")
    
    category, confidence = await classify_with_llm(prompt, llm)
    
    return _category_to_result(category, confidence, prompt, norm)


# =============================================================================
# Re-export SSEState and Intent for backward compatibility
# =============================================================================

# These are now in intent_config but re-exported here for existing imports
__all__ = [
    "Intent",
    "SSEState",
    "IntentResult",
    "Slots",
    "get_intent_result",
    "get_intent_result_with_llm",
    "normalize",
]
