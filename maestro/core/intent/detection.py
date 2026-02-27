"""Boolean detection functions for question, generation, vague, and affirmative/negative requests."""

from __future__ import annotations

import re

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
    "stori", "stori app", "this app", "this daw", "this program",
    "piano roll", "pianoroll", "step sequencer", "stepsequencer",
    "mixer", "mix console", "inspector", "properties panel",
    "timeline", "arrange view", "arrangement",
    "track", "tracks", "midi track", "audio track",
    "region", "regions", "midi region", "audio region",
    "midi", "midi note", "midi notes", "midi data",
    "quantize", "quantization", "swing", "groove",
    "automation", "modulation", "velocity",
    "generate", "generation", "compose", "composition",
    "record", "recording", "playback", "loop", "looping",
    "export", "bounce", "render", "mixdown",
    "effect", "effects", "plugin", "plugins", "vst",
    "compressor", "eq", "equalizer", "reverb", "delay",
)

_GENERATION_PHRASES = (
    "make a beat", "create a beat", "make beat", "create beat",
    "generate a beat", "generate beat", "build a beat", "build beat",
    "make drums", "create drums", "generate drums", "build drums",
    "make a drum", "create a drum", "generate a drum",
    "drum pattern", "drum loop", "drum groove",
    "boom bap", "trap beat", "lofi beat", "lo fi beat", "lo-fi beat",
    "house beat", "techno beat", "hip hop beat", "hiphop beat",
    "jazz drums", "rock drums", "funk drums",
    "make a bassline", "create a bassline", "write a bassline",
    "make bass", "create bass", "generate bass", "write bass",
    "bass line", "bass groove", "bass pattern",
    "make chords", "create chords", "generate chords", "write chords",
    "chord progression", "chord pattern", "jazz chords",
    "make a melody", "create a melody", "generate a melody", "write a melody",
    "make melody", "create melody", "generate melody", "write melody",
    "melodic line", "melody line",
    "compose", "compose a", "compose some",
    "generate a", "generate some", "generate an",
    "make a", "make some", "make an",
    "create a", "create some", "create an",
    "build a", "build some", "build an",
    "write a", "write some", "write an",
    "give me a beat", "give me drums", "give me bass", "give me chords",
    "i want a beat", "i want drums", "i want bass", "i want chords",
    "i need a beat", "i need drums", "i need bass", "i need chords",
    "lay down some drums", "lay down drums", "lay down a beat",
    "drop a beat", "drop some drums", "cook up a beat",
    "whip up a beat", "throw together a beat",
    "dorian scale", "pentatonic scale", "major scale", "minor scale",
    "blues scale", "chromatic scale",
)

_VAGUE_PHRASES = (
    "make it better", "fix it", "do it", "change it", "just make it",
    "make this better", "fix this", "change this", "make it good",
    "make it nice", "fix that", "change that", "do that",
    "improve it", "improve this", "improve that", "enhance it",
    "enhance this", "enhance that", "update it", "update this", "update that",
    "modify it", "modify this", "modify that", "adjust it", "adjust this",
    "tweak it", "tweak this", "tweak that", "refine it", "refine this",
    "make something", "add something", "create something", "do something",
    "fix something", "change something", "help me", "help me with this",
    "work on it", "work on this", "work on that",
)

_AFFIRMATIVE_PHRASES = (
    "yes", "yep", "yeah", "yup", "yea", "ya", "aye",
    "yes please", "yes pls", "hell yes", "hell yeah", "absolutely", "definitely",
    "for sure", "of course", "certainly", "indeed", "affirmative",
    "sure", "sure thing", "sounds good", "sounds great", "that works",
    "that sounds good", "that sounds great", "that'd be great", "that would be great",
    "perfect", "excellent", "wonderful", "fantastic",
    "ok", "okay", "k", "kk", "alright", "aight", "all right",
    "cool", "sweet", "nice", "great", "awesome", "dope", "bet",
    "go ahead", "go for it", "do it", "let's do it", "lets do it",
    "let do it", "proceed", "continue", "make it happen",
    "go on", "carry on", "keep going",
    "agreed", "i agree", "sounds like a plan", "works for me",
    "im in", "i'm in", "im down", "i'm down", "lets go", "let's go",
    "correct", "right", "exactly", "precisely", "that's right",
    "thats right", "you got it", "youve got it", "you've got it",
    "right then", "brilliant", "lovely", "cheers", "righto",
    "yass", "yaaas", "yee", "yessir", "yes sir", "yesss",
)

_NEGATIVE_PHRASES = (
    "no", "nope", "nah", "naw", "na", "nay",
    "no thanks", "no thank you", "no thx", "hell no", "definitely not",
    "absolutely not", "no way", "not at all", "never", "never mind",
    "nevermind", "forget it", "dont", "don't", "dont do it", "don't do it",
    "not right now", "not now", "maybe later", "not yet", "not this time",
    "i changed my mind", "ive changed my mind", "i've changed my mind",
    "cancel", "stop", "abort", "undo", "go back", "back",
    "scratch that", "disregard", "ignore that", "skip it", "skip that",
    "different", "something else", "try again", "not that", "not quite",
)


def _is_question(norm: str) -> bool:
    return bool(_QUESTION_START.search(norm)) or norm.endswith("?")


def _is_stori_question(norm: str) -> bool:
    return _is_question(norm) and any(k in norm for k in _STORI_KEYWORDS)


def _is_generation_request(norm: str) -> bool:
    return any(phrase in norm for phrase in _GENERATION_PHRASES)


def _is_vague(norm: str) -> bool:
    return any(v in norm for v in _VAGUE_PHRASES)


def _is_affirmative(norm: str) -> bool:
    if norm in _AFFIRMATIVE_PHRASES:
        return True
    words = norm.split()
    if len(words) <= 3 and any(a in norm for a in _AFFIRMATIVE_PHRASES):
        return True
    return False


def _is_negative(norm: str) -> bool:
    if norm in _NEGATIVE_PHRASES:
        return True
    words = norm.split()
    if len(words) <= 3 and any(n in norm for n in _NEGATIVE_PHRASES):
        return True
    return False
