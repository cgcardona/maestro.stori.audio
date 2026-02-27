# Stori Neural MIDI Generation Roadmap

**Version:** 2.2  
**Last Updated:** 2026-02-02  
**Status:** Phase 1 In Progress — HuggingFace Cloud Integration Complete

---

## Implementation Status

### Completed (2026-02-02)

| Component | File | Status |
|-----------|------|--------|
| **EmotionVector Schema** | `app/core/emotion_vector.py` | ✅ Complete |
| **REMI Tokenizer** | `app/services/neural/tokenizer.py` | ✅ Complete |
| **Neural Melody Generator** | `app/services/neural/melody_generator.py` | ✅ Complete (mock backend) |
| **HuggingFace Cloud Backend** | `app/services/neural/huggingface_melody.py` | ✅ Complete |
| **Text2MIDI Backend** | `app/services/neural/text2midi_backend.py` | ✅ Complete |
| **Drop-in Backend** | `app/services/backends/melody_neural.py` | ✅ Complete |
| **MVP Test Suite** | `tests/test_neural_mvp.py` | ✅ 45 tests passing |

### Cloud Models Available

| Model | Access Method | Best For | Status |
|-------|---------------|----------|--------|
| **text2midi** (amaai-lab) | Gradio Spaces API | Text → MIDI, high quality | ✅ Recommended |
| `skytnt/midi-model` | HF Inference API | General purpose | ⚠️ Deprecated |
| `asigalov61/Giant-Music-Transformer` | HF Inference API | Multi-instrument | ⚠️ Deprecated |

> **Note**: The HuggingFace Inference API models have been deprecated. Use `Text2MidiBackend` via Gradio Spaces API instead.

### What's Working Now

```python
# Option 1: Text2MIDI via Gradio Spaces (RECOMMENDED - best quality)
from maestro.services.neural import Text2MidiBackend
from maestro.core.emotion_vector import EmotionVector

backend = Text2MidiBackend()
result = await backend.generate(
    bars=8,
    tempo=120,
    key="Am",
    emotion_vector=EmotionVector(energy=0.8, valence=-0.2, tension=0.4),
    style="jazz",
    instrument="piano",
)
# → Converts emotion to natural language description:
#   "A balanced and expressive jazz piece featuring piano.
#    Set in Am minor with a 4/4 time signature, moving at Allegro tempo..."
# → Calls amaai-lab/text2midi Space API
# → Returns parsed MIDI notes

# Option 2: Mock backend for testing (no API needed)
from maestro.services.neural import NeuralMelodyGenerator

generator = NeuralMelodyGenerator()  # Uses MockNeuralMelodyBackend
result = await generator.generate(
    bars=8,
    tempo=120,
    key="Am",
    chords=["Am", "F", "C", "G"] * 2,
    emotion_vector=EmotionVector(energy=0.8, valence=0.3, tension=0.4)
)
```

### Emotion → HuggingFace Parameter Mapping

| Emotion Axis | HF Parameter | Effect |
|--------------|--------------|--------|
| `energy` + `tension` | `temperature` | Higher → more variation (0.5-1.4) |
| `intimacy` | `top_p` | Higher intimacy → lower top_p (more focused) |
| `motion` | `max_tokens` | Higher → more notes generated |
| All axes | Post-processing | Velocity/register adjusted to match emotion |

### Immediate Next Steps

| Task | Priority | Effort | Status |
|------|----------|--------|--------|
| ~~Evaluate MuseCoco locally~~ | ~~P0~~ | ~~1-2 days~~ | ⏭️ Skipped (using cloud) |
| ~~Evaluate AMT locally~~ | ~~P0~~ | ~~1-2 days~~ | ⏭️ Skipped (using cloud) |
| ~~Cloud model integration~~ | ~~P0~~ | ~~2-3 days~~ | ✅ Complete |
| ~~Test with real HuggingFace API~~ | ~~P0~~ | ~~0.5 days~~ | ✅ Verified (text2midi works!) |
| ~~Wire into existing API~~ | ~~P0~~ | ~~1 day~~ | ✅ Complete (text2midi is primary backend) |
| ~~A/B test vs rule-based~~ | ~~P1~~ | ~~1 day~~ | ⏭️ Skipped (all-in on neural) |
| Deploy and test end-to-end | P0 | 0.5 days | 🔄 Next |
| Add more style presets | P1 | 1 day | ⏳ Pending |

### Resolved Questions

1. ~~**Model choice**: MuseCoco vs Anticipatory Music Transformer~~ → **Using text2midi via Gradio Spaces API** (best quality text→MIDI)
2. ~~**Compute**: Where will inference run?~~ → **HuggingFace Spaces** (free GPU, ~60-90s per generation)
3. ~~**Latency target**~~ → **~60-90 seconds per generation** (acceptable for high quality output)

### GPU Quota Notes

The free tier HuggingFace Spaces has daily GPU quotas:
- ~120-300 seconds per day for anonymous users
- More quota with a free HuggingFace account
- Unlimited with HuggingFace Pro ($9/mo) or dedicated Inference Endpoints

For production, consider:
1. **HuggingFace Pro account** ($9/mo) for higher quotas
2. **Dedicated Inference Endpoint** for unlimited, faster generation
3. **Self-hosting text2midi** on your own GPU (model is Apache 2.0 licensed)

---

## Vision: From 1 to 1000

The goal is to create a system where anyone can express any musical desire in natural language and receive world-class, emotionally resonant MIDI output that can be rendered, edited, and refined in a DAW.

**Why MIDI over Audio:**
- **Separation**: Each instrument is its own track
- **Editability**: Users can tweak any note
- **Flexibility**: Render with any sounds/instruments
- **Speed**: Generating tokens is faster than waveforms
- **Creative Control**: The DAW can do what DAWs do best

**Core Principle:** MIDI-first is non-negotiable. We generate symbolic music, not waveforms.

---

## Current State Assessment

### What the Current System Does

The existing rule-based system generates "structured randomness" - notes that follow patterns but lack musical intent:

**Drums** (`drum_ir_renderer.py`):
- 4-5 hardcoded groove templates (`trap_straight`, `boom_bap_swing`, etc.)
- Same kick patterns every time: `[0.0, 2.0, 3.5]` for trap, `[0.0, 2.5]` for boom bap
- Random velocity within ranges
- No awareness of the song's emotional journey

**Bass** (`bass_ir_renderer.py`):
- Only plays root and fifth of chords
- Random selection between the two
- No melodic bass lines, no passing tones, no chromaticism

**Melody** (`melody_ir_renderer.py`):
- Random notes from scale with basic contour
- No motifs, no call-and-response, no development
- No awareness of emotional arc

### What's Missing

| Element | Current State | What's Needed |
|---------|---------------|---------------|
| Chord Progressions | Just a "key" parameter | Real progressions with emotional weight |
| Song Structure | Flat, no sections | Intro/Verse/Chorus/Bridge/Outro |
| Dynamics | Everything same intensity | Energy that breathes and builds |
| Musical Narrative | None | Tension → Release |
| Melodic Development | Random notes | Motifs that develop |
| Genre DNA | 5 hardcoded templates | Deep style understanding |
| Emotional Control | Descriptive strings | Explicit numeric vectors |

### The Quality Scale

| Level | Description | How It's Made |
|-------|-------------|---------------|
| **1-10** | Random but structured | Rule-based, hardcoded patterns (CURRENT) |
| **20-40** | Competent but generic | Rich rules + chord progressions + structure |
| **50-70** | Surprisingly good | Neural models for note generation |
| **80-90** | Professionally usable | Fine-tuned neural + sophisticated conditioning |
| **100** | Gives people chills | End-to-end learned, emotionally intelligent |
| **1000** | Transcendent | Natural language → music that moves souls |

---

## What 1000 Looks Like

### Example Interaction

**User says:** 
> "Create a bittersweet indie folk song about leaving home, with fingerpicked guitar, warm bass, subtle drums, and a melody that soars in the chorus"

**System generates:**
- 4 separate MIDI tracks (guitar, bass, drums, vocals/melody)
- Proper song structure:
  - Intro (4 bars, sparse guitar)
  - Verse 1 (8 bars, intimate)
  - Pre-Chorus (4 bars, building)
  - Chorus (8 bars, soaring)
  - Verse 2 (8 bars, slightly fuller)
  - Chorus (8 bars)
  - Bridge (8 bars, different emotional color)
  - Final Chorus (8 bars, biggest)
  - Outro (4 bars, return to intimacy)
- Chord progression that supports the emotion (Am → F → C → G with vi-IV-I-V feel)
- Melody with a **motif** that develops, rises in the chorus, falls in the verse
- Guitar that fingerpicks arpeggiated patterns matching the chords
- Bass that breathes with the song (sparse in verse, fuller in chorus)
- Drums that build from brushes in verse to full kit in chorus

**User then says:**
> "Make the bridge more intense"

**System adjusts:**
- Mutates emotional vector: `tension: 0.4 → 0.7`, `energy: 0.5 → 0.7`
- Identifies affected dimensions: rhythm density, melodic register, harmonic tension
- Regenerates only the bridge section, preserving motif usage
- Validates cross-section coherence with adjacent sections

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     USER (Natural Language)                      │
│        "bittersweet indie folk about leaving home"              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  MAESTRO LLM (Claude/GPT-4)                    │
│  Understands intent, emotion, genre, generates:                 │
│  • Song structure (sections as generation units)                │
│  • Emotional vectors per section (energy, valence, tension...)  │
│  • Chord progression per section                                │
│  • Motif definitions (first-class IR objects)                   │
│  • Instrumentation with section-specific behavior               │
│  • Style/genre embedding                                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              RICH MUSICAL SPECIFICATION (RMS)                    │
│  See detailed schema below                                       │
│  Key: Sections are hard generation boundaries                   │
│  Key: Motifs are first-class objects, not descriptions          │
│  Key: Emotional vectors are numeric, not strings                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              SECTION-LOCAL GENERATION ENGINE                     │
│                                                                  │
│  For each section:                                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Load section spec (emotion, chords, motifs, entry/exit)│   │
│  │ 2. Generate drums (conditioned on all)                    │   │
│  │ 3. Generate bass (conditioned on drums + chords)          │   │
│  │ 4. Generate harmony (conditioned on drums + bass)         │   │
│  │ 5. Generate melody (conditioned on all + motif refs)      │   │
│  │ 6. Score all dimensions                                   │   │
│  │ 7. Regenerate weakest dimension if below threshold        │   │
│  │ 8. Validate exit state matches next section entry         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Models: MelodyModel, HarmonyModel, RhythmModel                 │
│  Each conditioned on: emotion_vector + chords + motifs + context│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    POST-PROCESSING                               │
│  • Humanization (groove, microtiming, velocity curves)          │
│  • Cross-section smoothing (boundary coherence)                 │
│  • Expression (dynamics, articulation)                          │
│  • Final critic pass (reject if PKR-proxy fails)                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MULTI-TRACK MIDI                             │
│  Each track separate, editable, ready for DAW rendering         │
│  Delivered to frontend via existing tool call system            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Core Intermediate Representations

### 1. Emotional Vector Schema

Emotion is not a string. It is a **continuous, multi-dimensional control signal** that conditions generation, guides refinement, and enables precise user commands.

#### Schema Definition

```json
{
  "emotion_vector": {
    "energy": 0.0,      // 0.0 = stillness, 1.0 = explosive
    "valence": 0.0,     // -1.0 = dark/sad, +1.0 = bright/joyful
    "tension": 0.0,     // 0.0 = resolved/relaxed, 1.0 = unresolved/anxious
    "intimacy": 0.0,    // 0.0 = distant/epic, 1.0 = close/personal
    "motion": 0.0       // 0.0 = static/sustained, 1.0 = driving/rhythmic
  }
}
```

#### Axis Definitions and Musical Mappings

| Axis | Range | Low Value Musical Effect | High Value Musical Effect |
|------|-------|--------------------------|---------------------------|
| **energy** | 0.0–1.0 | Sparse arrangement, soft dynamics, slow harmonic rhythm | Full arrangement, loud dynamics, fast activity |
| **valence** | -1.0–+1.0 | Minor mode, low register, descending lines, slower tempo | Major mode, high register, ascending lines, brighter timbre |
| **tension** | 0.0–1.0 | Consonant harmony, stable rhythm, resolved phrases | Dissonance, syncopation, suspended chords, chromatic motion |
| **intimacy** | 0.0–1.0 | Wide stereo, reverb, distant feel, ensemble sound | Mono/close, dry, solo instrument focus, personal |
| **motion** | 0.0–1.0 | Long notes, pads, rubato feel, minimal drums | Short notes, arpeggios, strict grid, active drums |

#### How Axes Map to Generation Parameters

```python
# Example: emotion_vector → generation constraints
def emotion_to_constraints(ev: EmotionVector) -> GenerationConstraints:
    return GenerationConstraints(
        # Rhythm
        drum_density=lerp(0.2, 1.0, ev.energy * ev.motion),
        subdivision=16 if ev.motion > 0.6 else 8,
        swing_amount=lerp(0.0, 0.3, 1.0 - ev.tension),
        
        # Melody
        register_center=lerp(48, 72, (ev.valence + 1) / 2),
        register_spread=lerp(6, 18, ev.energy),
        rest_density=lerp(0.4, 0.1, ev.motion),
        leap_probability=lerp(0.1, 0.4, ev.tension),
        
        # Harmony
        chord_extensions=ev.tension > 0.5,
        borrowed_chord_probability=lerp(0.0, 0.3, ev.tension),
        harmonic_rhythm_bars=lerp(2, 0.5, ev.energy),
        
        # Dynamics
        velocity_floor=lerp(40, 80, ev.energy),
        velocity_ceiling=lerp(80, 120, ev.energy),
        dynamic_range=lerp(10, 40, 1.0 - ev.intimacy),
    )
```

#### LLM Output Format

The LLM generates emotion vectors per section:

```json
{
  "sections": [
    {
      "name": "verse_1",
      "emotion_vector": {
        "energy": 0.3,
        "valence": -0.2,
        "tension": 0.2,
        "intimacy": 0.8,
        "motion": 0.4
      }
    },
    {
      "name": "chorus",
      "emotion_vector": {
        "energy": 0.8,
        "valence": 0.3,
        "tension": 0.4,
        "intimacy": 0.5,
        "motion": 0.7
      }
    }
  ]
}
```

#### Refinement Command Mappings

Natural language refinement commands mutate specific axes:

| User Command | Axis Mutations |
|--------------|----------------|
| "Make it sadder" | `valence -= 0.3` |
| "More intense" | `energy += 0.2`, `tension += 0.2` |
| "Calmer" | `energy -= 0.3`, `tension -= 0.2`, `motion -= 0.2` |
| "More intimate" | `intimacy += 0.3`, `energy -= 0.1` |
| "More driving" | `motion += 0.3`, `energy += 0.1` |
| "Brighter" | `valence += 0.3` |
| "More tension" | `tension += 0.3` |
| "Resolve it" | `tension -= 0.4`, `valence += 0.1` |

The LLM interprets ambiguous commands and outputs the delta:

```json
{
  "refinement": {
    "target_section": "bridge",
    "emotion_delta": {
      "energy": 0.2,
      "tension": 0.3
    },
    "regenerate_dimensions": ["rhythm", "melody"]
  }
}
```

---

### 2. Motif IR (First-Class Object)

Motifs are not descriptions. They are **structured musical objects** that can be referenced, transformed, and tracked across sections.

#### Motif Schema

```json
{
  "motif_id": "main_theme",
  "interval_pattern": [0, -2, -1, -3],
  "rhythm_pattern": ["quarter", "eighth", "eighth", "half"],
  "duration_beats": 3.0,
  "register": "mid",
  "emotional_role": "nostalgic",
  "anchor_scale_degree": 5,
  "allowed_transformations": [
    "transpose",
    "register_shift",
    "rhythmic_expand",
    "rhythmic_compress",
    "invert",
    "retrograde",
    "intensify",
    "simplify"
  ]
}
```

#### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `motif_id` | string | Unique identifier for referencing |
| `interval_pattern` | int[] | Semitone intervals from first note (0 = root of motif) |
| `rhythm_pattern` | string[] | Duration of each note: "whole", "half", "quarter", "eighth", "sixteenth", "triplet" |
| `duration_beats` | float | Total length in beats |
| `register` | enum | "low", "mid_low", "mid", "mid_high", "high" |
| `emotional_role` | string | Semantic tag for the motif's function |
| `anchor_scale_degree` | int | Which scale degree the motif typically starts on (1-7) |
| `allowed_transformations` | string[] | Which transformations preserve the motif's identity |

#### Transformation Definitions

| Transformation | Effect | When to Use |
|----------------|--------|-------------|
| `transpose` | Shift to different pitch level | Following chord changes |
| `register_shift` | Move up/down an octave | Energy changes |
| `rhythmic_expand` | Double all durations | Lower motion sections |
| `rhythmic_compress` | Halve all durations | Higher motion sections |
| `invert` | Flip intervals (up becomes down) | Development, variation |
| `retrograde` | Reverse note order | Bridge, development |
| `intensify` | Add passing tones, shorten rests | Building energy |
| `simplify` | Remove ornaments, lengthen notes | Intimate sections |

#### Section-Motif References

Each section explicitly declares which motifs it uses and how:

```json
{
  "section": "chorus",
  "motif_usage": [
    {
      "motif_id": "main_theme",
      "occurrences": [
        {"beat": 0, "transformation": "register_shift", "params": {"octave": 1}},
        {"beat": 8, "transformation": "intensify"}
      ]
    },
    {
      "motif_id": "answer_phrase",
      "occurrences": [
        {"beat": 4, "transformation": "transpose", "params": {"semitones": 5}}
      ]
    }
  ]
}
```

#### Motif Generation Pipeline

```
1. LLM generates 1-3 motif definitions based on emotional arc
2. Motif IR is validated (intervals reasonable, rhythm coherent)
3. Each section receives motif references
4. Melody generator uses motifs as hard constraints:
   - Must include specified motif at specified beat
   - May fill around motifs with coherent material
   - Transformation parameters are applied before rendering
5. Critic validates motif presence and recognizability
```

---

### 3. Section Specification (Generation Unit)

Sections are the **hard boundaries** for generation. We do not generate full songs in one pass.

#### Section Schema

```json
{
  "section_id": "verse_1",
  "section_type": "verse",
  "bars": 8,
  "tempo": 108,
  "time_signature": "4/4",
  "key": "C major",
  
  "emotion_vector": {
    "energy": 0.3,
    "valence": -0.2,
    "tension": 0.2,
    "intimacy": 0.8,
    "motion": 0.4
  },
  
  "chords": [
    {"bar": 0, "chord": "Am7"},
    {"bar": 2, "chord": "F"},
    {"bar": 4, "chord": "C"},
    {"bar": 6, "chord": "G"}
  ],
  
  "motif_usage": [
    {"motif_id": "main_theme", "beat": 0, "transformation": null}
  ],
  
  "entry_state": {
    "energy": 0.2,
    "last_chord": "G",
    "melodic_register": "mid",
    "drum_active": false
  },
  
  "exit_state": {
    "energy": 0.35,
    "last_chord": "G",
    "melodic_register": "mid",
    "drum_active": true
  },
  
  "track_behaviors": {
    "drums": {"active": true, "enter_bar": 4, "style": "brushes"},
    "bass": {"active": true, "density": "sparse"},
    "guitar": {"active": true, "pattern": "fingerpicked"},
    "melody": {"active": true, "priority": "motif_statement"}
  }
}
```

#### Entry/Exit State Matching

Cross-section coherence comes from **state matching**, not free continuation:

```python
def validate_section_transition(section_a: Section, section_b: Section) -> bool:
    """Validate that section_a.exit_state is compatible with section_b.entry_state"""
    
    # Energy should not jump more than 0.3 without a break section
    if abs(section_a.exit_state.energy - section_b.entry_state.energy) > 0.3:
        if section_b.section_type not in ("break", "intro", "outro"):
            return False
    
    # Harmonic connection: exit chord should lead to entry chord
    if not is_valid_progression(section_a.exit_state.last_chord, 
                                 section_b.chords[0].chord):
        return False
    
    # Melodic register should be within an octave
    if register_distance(section_a.exit_state.melodic_register,
                         section_b.entry_state.melodic_register) > 12:
        return False
    
    return True
```

#### Why Section-Local Generation

| Problem with Full-Song Generation | Section-Local Solution |
|-----------------------------------|------------------------|
| Long-range coherence fails | Each section is bounded, tractable |
| One bad section ruins everything | Regenerate only the weak section |
| Motifs get lost over time | Motifs are explicitly placed per section |
| Energy arc drifts | Entry/exit states enforce arc |
| Memory/context limits | Fixed context window per section |

---

## Rich Musical Specification (RMS) — Complete Schema

This is the full output schema from the Maestro LLM:

```json
{
  "rms_version": "2.0",
  "title": "Leaving Home",
  "genre": "indie_folk",
  "tempo": 108,
  "key": "C major",
  "time_signature": "4/4",
  
  "global_emotion": {
    "arc_description": "bittersweet journey from intimacy to catharsis",
    "primary_valence": -0.1,
    "energy_range": [0.2, 0.85]
  },
  
  "motifs": [
    {
      "motif_id": "main_theme",
      "interval_pattern": [0, -2, -1, -3],
      "rhythm_pattern": ["quarter", "eighth", "eighth", "half"],
      "duration_beats": 3.0,
      "register": "mid",
      "emotional_role": "nostalgic",
      "anchor_scale_degree": 5,
      "allowed_transformations": ["transpose", "register_shift", "intensify"]
    },
    {
      "motif_id": "answer_phrase",
      "interval_pattern": [0, 2, 4, 2],
      "rhythm_pattern": ["eighth", "eighth", "quarter", "quarter"],
      "duration_beats": 2.0,
      "register": "mid_high",
      "emotional_role": "hopeful",
      "anchor_scale_degree": 1,
      "allowed_transformations": ["transpose", "rhythmic_expand"]
    }
  ],
  
  "sections": [
    {
      "section_id": "intro",
      "section_type": "intro",
      "bars": 4,
      "emotion_vector": {
        "energy": 0.2,
        "valence": -0.1,
        "tension": 0.1,
        "intimacy": 0.9,
        "motion": 0.2
      },
      "chords": [
        {"bar": 0, "chord": "Am"},
        {"bar": 2, "chord": "F"}
      ],
      "motif_usage": [],
      "entry_state": {"energy": 0.0, "last_chord": null, "melodic_register": null, "drum_active": false},
      "exit_state": {"energy": 0.2, "last_chord": "F", "melodic_register": "mid", "drum_active": false},
      "track_behaviors": {
        "drums": {"active": false},
        "bass": {"active": false},
        "guitar": {"active": true, "pattern": "sparse_fingerpick"},
        "melody": {"active": false}
      }
    },
    {
      "section_id": "verse_1",
      "section_type": "verse",
      "bars": 8,
      "emotion_vector": {
        "energy": 0.3,
        "valence": -0.2,
        "tension": 0.2,
        "intimacy": 0.8,
        "motion": 0.4
      },
      "chords": [
        {"bar": 0, "chord": "Am"},
        {"bar": 2, "chord": "F"},
        {"bar": 4, "chord": "C"},
        {"bar": 6, "chord": "G"}
      ],
      "motif_usage": [
        {"motif_id": "main_theme", "beat": 0, "transformation": null}
      ],
      "entry_state": {"energy": 0.2, "last_chord": "F", "melodic_register": "mid", "drum_active": false},
      "exit_state": {"energy": 0.35, "last_chord": "G", "melodic_register": "mid", "drum_active": false},
      "track_behaviors": {
        "drums": {"active": false},
        "bass": {"active": true, "density": "sparse", "enter_bar": 2},
        "guitar": {"active": true, "pattern": "fingerpicked_arpeggios"},
        "melody": {"active": true, "priority": "motif_statement"}
      }
    },
    {
      "section_id": "chorus",
      "section_type": "chorus",
      "bars": 8,
      "emotion_vector": {
        "energy": 0.8,
        "valence": 0.3,
        "tension": 0.4,
        "intimacy": 0.5,
        "motion": 0.7
      },
      "chords": [
        {"bar": 0, "chord": "C"},
        {"bar": 2, "chord": "G"},
        {"bar": 4, "chord": "Am"},
        {"bar": 6, "chord": "F"}
      ],
      "motif_usage": [
        {"motif_id": "main_theme", "beat": 0, "transformation": "register_shift", "params": {"octave": 1}},
        {"motif_id": "answer_phrase", "beat": 12, "transformation": null}
      ],
      "entry_state": {"energy": 0.5, "last_chord": "Am", "melodic_register": "mid", "drum_active": true},
      "exit_state": {"energy": 0.75, "last_chord": "F", "melodic_register": "mid_high", "drum_active": true},
      "track_behaviors": {
        "drums": {"active": true, "style": "full_kit", "pattern": "driving"},
        "bass": {"active": true, "density": "full"},
        "guitar": {"active": true, "pattern": "strummed"},
        "melody": {"active": true, "priority": "soaring"}
      }
    }
  ],
  
  "tracks": [
    {
      "track_id": "guitar",
      "name": "Acoustic Guitar",
      "role": "chords",
      "instrument": "acoustic_guitar_steel",
      "register": "mid"
    },
    {
      "track_id": "bass",
      "name": "Bass",
      "role": "bass",
      "instrument": "acoustic_bass",
      "register": "low"
    },
    {
      "track_id": "drums",
      "name": "Drums",
      "role": "drums",
      "instrument": "drum_kit_acoustic",
      "register": null
    },
    {
      "track_id": "melody",
      "name": "Lead Melody",
      "role": "melody",
      "instrument": "piano",
      "register": "mid_high"
    }
  ]
}
```

---

## Key Technologies

### 1. MIDI Tokenization

MIDI must be converted to a format transformers can learn. The best approaches:

#### REMI (Revamped MIDI)
Encodes musical events as tokens:
```
BAR | POSITION_0 | TEMPO_120 | CHORD_Am | 
PITCH_60 | DURATION_480 | VELOCITY_80 |
POSITION_480 | PITCH_64 | DURATION_240 | VELOCITY_75 |
...
```

**Pros:** Explicit structure, handles timing well  
**Cons:** Long sequences

#### Compound Word
Groups related tokens into single "words":
```
[BAR, POS_0, PITCH_60, DUR_480, VEL_80]  # Single token
```

**Pros:** More efficient, faster generation  
**Cons:** Larger vocabulary

#### Octuple (Multi-track)
Separate streams per track with cross-attention:
```
Track 1: [tokens...]
Track 2: [tokens...]
...
```

**Pros:** Native multi-track support  
**Cons:** Complex architecture

**Recommendation:** Start with REMI for simplicity, move to Compound Word for efficiency.

---

### 2. Pre-trained Music Models

Don't train from scratch. Build on existing work:

| Model | What It Does | Availability | Best For |
|-------|--------------|--------------|----------|
| **MusicVAE** | Latent space for melodies | Open source | Melody interpolation, style |
| **Pop Music Transformer** | Song-level structure | Paper + code | Pop music with sections |
| **Anticipatory Music Transformer** | Infilling, continuation | Open source | Editing, completion |
| **MuseCoco** | Text → symbolic music | Open source | Text conditioning |
| **MidiGPT** | GPT-style MIDI generation | Emerging | General purpose |

#### Recommended Starting Point: MuseCoco or Anticipatory Music Transformer

**MuseCoco:**
- Designed for text-to-music
- Understands descriptions like "happy", "energetic", "minor key"
- Can condition on multiple attributes

**Anticipatory Music Transformer:**
- Excellent for infilling (generate middle given beginning and end)
- Good for iterative refinement
- Strong coherence

---

### 3. The LLM as Conductor

The LLM (Claude/GPT-4) handles:
- **Intent understanding**: What does "bittersweet" mean musically?
- **Emotion vector generation**: Convert descriptions to numeric axes
- **Motif design**: Create coherent melodic seeds
- **Structural planning**: How should the song unfold?
- **Refinement interpretation**: "Make it sadder" → axis mutations

The LLM outputs the Rich Musical Specification (RMS) shown above.

---

### 4. Conditioning Mechanisms

The music models are conditioned on multiple signals:

| Condition | Type | Implementation |
|-----------|------|----------------|
| **Emotion Vector** | 5D float | Concatenated to input embeddings |
| **Chords** | Embedding per beat | Chord2Vec or learned embeddings |
| **Section Type** | Categorical | Learned section embeddings |
| **Motif Constraint** | Token sequence | Forced decoding at specified beats |
| **Other Tracks** | Token sequence | Cross-attention from generated tracks |
| **Entry/Exit State** | Structured | Initial hidden state conditioning |

---

### 5. Multi-Track Coherence

**The Core Challenge:** How do you get multiple AI-generated tracks to sound like they were written together?

#### Sequential Generation (Phase 1 Approach)

```
For each section:
  1. Generate drums (conditioned on emotion + structure)
  2. Generate bass (conditioned on drums + chords + emotion)
  3. Generate harmony (conditioned on drums + bass + chords)
  4. Generate melody (conditioned on all + motifs)
```

**Pros:** Each part can fully condition on previous  
**Cons:** Early parts can't adapt to later parts

#### Iterative Dimension Refinement (Phase 2 Enhancement)

See detailed refinement loop below.

**Recommendation:** Start with sequential generation, add iterative refinement for quality.

---

## Iterative Refinement Loop

### Philosophy

Refinement should mirror **real producer behavior**:
1. Listen to the output
2. Identify the weakest element
3. Fix just that element
4. Re-evaluate
5. Repeat until satisfied

We do not regenerate everything. We do not retrain models. We surgically fix dimensions.

### Refinement Dimensions

| Dimension | What It Controls | Regeneration Scope |
|-----------|------------------|-------------------|
| **rhythm** | Drum patterns, note timing, groove | Drum track for section |
| **harmony** | Chord voicings, bass line | Harmony + bass tracks for section |
| **melody** | Note pitches, motif usage | Melody track for section |
| **expression** | Dynamics, articulation, humanization | Post-processing pass |
| **density** | Note count, rest frequency | All tracks for section |

### Refinement Algorithm

```python
def refine_section(
    section: Section,
    generated_tracks: dict[str, MidiTrack],
    max_iterations: int = 3
) -> dict[str, MidiTrack]:
    """
    Iterative dimension-targeted refinement.
    """
    for iteration in range(max_iterations):
        # Score all dimensions
        scores = score_all_dimensions(section, generated_tracks)
        
        # Find weakest dimension
        weakest_dim = min(scores, key=scores.get)
        weakest_score = scores[weakest_dim]
        
        # Check if acceptable
        if weakest_score >= DIMENSION_THRESHOLDS[weakest_dim]:
            break  # All dimensions pass
        
        # Regenerate only the weak dimension
        if weakest_dim == "rhythm":
            generated_tracks["drums"] = regenerate_drums(
                section,
                context=generated_tracks,
                emotion_vector=section.emotion_vector
            )
        elif weakest_dim == "melody":
            generated_tracks["melody"] = regenerate_melody(
                section,
                context=generated_tracks,
                motifs=section.motif_usage,
                preserve_motif_placements=True  # Keep motifs, fix fill
            )
        elif weakest_dim == "harmony":
            generated_tracks["bass"] = regenerate_bass(section, context=generated_tracks)
            generated_tracks["chords"] = regenerate_chords(section, context=generated_tracks)
        elif weakest_dim == "expression":
            generated_tracks = apply_humanization(generated_tracks, section.emotion_vector)
        elif weakest_dim == "density":
            # Adjust density and regenerate all
            adjusted_constraints = adjust_density(section.emotion_vector, scores["density"])
            generated_tracks = regenerate_all(section, constraints=adjusted_constraints)
    
    return generated_tracks
```

### Scoring Functions per Dimension

```python
def score_all_dimensions(section: Section, tracks: dict) -> dict[str, float]:
    return {
        "rhythm": score_rhythm(
            tracks["drums"],
            section.emotion_vector,
            expected_density=emotion_to_rhythm_density(section.emotion_vector)
        ),
        "melody": score_melody(
            tracks["melody"],
            section.motif_usage,
            section.emotion_vector,
            section.chords
        ),
        "harmony": score_harmony(
            tracks["bass"],
            tracks.get("chords"),
            section.chords,
            section.emotion_vector
        ),
        "expression": score_expression(
            tracks,
            section.emotion_vector,
            expected_dynamics=emotion_to_dynamics(section.emotion_vector)
        ),
        "density": score_density(
            tracks,
            section.emotion_vector,
            expected_note_count=emotion_to_density(section.emotion_vector, section.bars)
        ),
    }
```

### Motif Preservation During Refinement

When regenerating melody, motifs are **locked**:

```python
def regenerate_melody(section, context, motifs, preserve_motif_placements=True):
    if preserve_motif_placements:
        # Extract current motif instances
        locked_regions = []
        for usage in motifs:
            motif = get_motif(usage.motif_id)
            start_beat = usage.beat
            end_beat = start_beat + motif.duration_beats
            locked_regions.append((start_beat, end_beat))
        
        # Generate with locked regions
        return melody_model.generate(
            section=section,
            context=context,
            locked_regions=locked_regions,
            infill_only=True  # Only generate between/around motifs
        )
    else:
        return melody_model.generate(section=section, context=context)
```

---

## Success Metrics

### North Star: Producer Keep Rate (PKR)

> **Producer Keep Rate (PKR):** "Would a human keep this section and continue working with it?"

This is the metric that matters. Everything else is a proxy.

#### PKR Definition

```
PKR = (Sections kept without full regeneration) / (Total sections generated)
```

A section is "kept" if the user:
- Makes no changes, OR
- Makes only minor edits (< 20% of notes changed), OR
- Uses refinement commands but doesn't regenerate

#### Measurable PKR Proxies

| Proxy Metric | What It Measures | Target |
|--------------|------------------|--------|
| **Section Regeneration Rate** | % of sections regenerated from scratch | < 20% |
| **Time to First Edit** | How long before user touches MIDI | > 30 seconds (listening) |
| **Edit Density** | Notes edited / notes generated | < 10% |
| **Refinement Success Rate** | % of refinement commands that resolve issue | > 80% |
| **Session Abandonment** | % of sessions with no exported MIDI | < 30% |
| **Section Delete Rate** | % of sections deleted (vs refined) | < 10% |

#### PKR Calibration

| PKR | Quality Level | User Experience |
|-----|---------------|-----------------|
| < 30% | Unusable | "This is frustrating, I'll write it myself" |
| 30-50% | Rough draft | "It's a starting point, but needs a lot of work" |
| 50-70% | Usable | "This is pretty good, I can work with this" |
| 70-85% | Good | "Wow, this mostly works, just minor tweaks" |
| > 85% | Excellent | "This is better than I would have written" |

### Secondary Metrics

| Metric | Baseline (Current) | Target (Phase 2) | Target (Final) |
|--------|-------------------|------------------|----------------|
| PKR (defined above) | N/A | 50% | 80% |
| Human preference vs rule-based | 50% | 75% | 95% |
| Melodic coherence (human 1-10) | 3 | 6 | 8+ |
| Genre authenticity (human 1-10) | 2 | 5 | 8+ |
| Multi-track coherence (human 1-10) | N/A | 5 | 8+ |
| Motif recognizability | N/A | 70% | 90% |
| Emotional accuracy (rated match) | N/A | 60% | 85% |
| Generation time (8-bar section) | <1s | <5s | <3s |

---

## Implementation Roadmap

### Phase 1: Foundation (2-4 weeks) — IN PROGRESS

**Status:** ~60% Complete

**Goals:**
- ✅ MIDI tokenization working
- ⏳ Dataset pipeline established
- ⏳ Evaluation framework with PKR proxies
- ✅ Emotional vector schema implemented

**Tasks:**

1. **Implement REMI tokenization** ✅ COMPLETE
   - ✅ MIDI → tokens → MIDI roundtrip (`app/services/neural/tokenizer.py`)
   - ⏳ Test on Lakh MIDI dataset samples

2. **Dataset curation** ⏳ NOT STARTED
   - ⏳ Download Lakh MIDI Dataset
   - ⏳ Filter for quality and genre
   - ⏳ Create genre-specific subsets (pop, folk, jazz, electronic)
   - ⏳ Add emotion labels (valence, energy) via audio analysis of paired recordings

3. **Implement core schemas** 🔄 PARTIAL
   - ✅ `EmotionVector` dataclass with validation (`app/core/emotion_vector.py`)
   - ⏳ `MotifIR` dataclass with transformation methods
   - ⏳ `SectionSpec` dataclass with entry/exit state
   - ✅ JSON serialization for LLM I/O

4. **Evaluation framework** ⏳ NOT STARTED
   - ⏳ Pitch class entropy (melodic interest)
   - ⏳ Rhythmic consistency
   - ⏳ Chord-tone alignment
   - ⏳ PKR proxy instrumentation hooks
   - ⏳ Human evaluation protocol

**Deliverables:**
- ✅ `MidiTokenizer` class with encode/decode
- 🔄 `EmotionVector` (done), `MotifIR`, `SectionSpec` schemas (pending)
- ⏳ Curated dataset with emotion metadata
- ⏳ Evaluation scripts with PKR proxy collection

**Bonus Completed (Ahead of Schedule):**
- ✅ `NeuralMelodyGenerator` with mock backend (`app/services/neural/melody_generator.py`)
- ✅ `MelodyNeuralBackend` drop-in replacement (`app/services/backends/melody_neural.py`)
- ✅ Emotion presets and refinement command mappings
- ✅ `emotion_to_constraints()` mapping function
- ✅ 25 passing tests (`tests/test_neural_mvp.py`)

---

### Phase 2: Core Models (4-8 weeks)

**Goals:**
- Single-track generation working
- Conditioning on emotion vectors + chords
- Motif-constrained melody generation
- Section-local generation enforced

**Tasks:**

1. **Fine-tune base model**
   - Start with MuseCoco or similar
   - Add emotion vector conditioning (5D input)
   - Fine-tune on curated genre datasets
   - Validate section-local generation (no cross-section bleeding)

2. **Build conditioning system**
   - Emotion vector → generation constraints mapping
   - Chord embeddings (Chord2Vec or learned)
   - Section type embeddings
   - Entry/exit state conditioning

3. **Motif-constrained generation**
   - Implement forced decoding for motif placement
   - Transformation application (transpose, invert, etc.)
   - Infill generation around locked motif regions

4. **Single-track quality**
   - Melody generation that respects motifs + emotion
   - Drum patterns with emotion-appropriate density/groove
   - Bass lines that follow harmony + emotion

**Deliverables:**
- `MelodyGenerator`, `DrumGenerator`, `BassGenerator` models
- Emotion conditioning interface
- Motif constraint system
- Quality metrics showing improvement over rule-based

---

### Phase 3: Multi-Track Coordination (3-4 weeks)

**Goals:**
- Tracks work together
- Sequential generation pipeline
- Iterative dimension refinement
- Cross-section coherence via state matching

**Tasks:**

1. **Sequential generation pipeline**
   - Drums first (from section spec + emotion)
   - Bass conditioned on drums + chords + emotion
   - Harmony (if separate) conditioned on drums + bass
   - Melody conditioned on all + motifs

2. **Iterative refinement loop**
   - Implement dimension scoring functions
   - Implement targeted regeneration per dimension
   - Motif preservation during melody regeneration
   - Bounded iteration (max 3 passes)

3. **Cross-section coherence**
   - Implement entry/exit state validation
   - Generate transition measures if needed
   - Score cross-section smoothness

4. **Integration with existing system**
   - Replace rule-based renderers with neural generators
   - Keep DAW tool integration (tracks, regions, effects)
   - Maintain API compatibility

**Deliverables:**
- `SectionGenerator` orchestrating per-section generation
- `DimensionRefiner` implementing refinement loop
- `CoherenceValidator` for cross-section checks
- Integration with Stori backend

---

### Phase 4: LLM Integration (2-4 weeks)

**Goals:**
- Natural language → RMS (Rich Musical Specification)
- Refinement commands → emotion vector mutations
- Motif generation from descriptions

**Tasks:**

1. **Enhanced LLM planning**
   - Prompt engineering for RMS output
   - Emotion vector generation from natural language
   - Motif IR generation from melodic descriptions
   - Genre-specific prompting

2. **Refinement command parsing**
   - "Make it sadder" → emotion delta extraction
   - "More intense in the bridge" → section targeting
   - "The melody should soar more" → dimension targeting
   - Mutation validation (bounds, coherence)

3. **Two-way communication**
   - LLM can request clarification on ambiguous commands
   - Model can report generation issues for LLM to address
   - Feedback loop for iterative refinement

**Deliverables:**
- LLM prompts for RMS generation
- Refinement command parser
- Full natural language → MIDI pipeline

---

### Phase 5: Polish & Humanization (2-4 weeks)

**Goals:**
- Output sounds human, not robotic
- Expression matches emotion vectors
- PKR targets achieved

**Tasks:**

1. **Humanization layer**
   - Apply microtiming (groove engine concepts, emotion-aware)
   - Velocity curves matching emotion dynamics
   - Articulation (legato in intimate, staccato in driving)

2. **Dynamic processing**
   - Per-section energy matching to emotion vector
   - Crescendos/decrescendos at section boundaries
   - Accent patterns from motion axis

3. **Cross-section smoothing**
   - Transition handling (fills, sustains)
   - Tempo/feel consistency
   - Motif thread continuity

4. **Quality validation**
   - A/B testing with human evaluators
   - PKR measurement in beta users
   - Genre expert review
   - Iteration on weak points

**Deliverables:**
- `HumanizationProcessor` (emotion-aware)
- Expression system
- PKR benchmarks
- Production-ready pipeline

---

## What Existing Code Is Still Valuable

| Component | Keep/Modify/Replace | Rationale |
|-----------|---------------------|-----------|
| DAW tool integration | **Keep** | Tracks, regions, effects system works |
| Entity registry | **Keep** | Still needed for MIDI output to frontend |
| API routes | **Modify** | Same interface, different generation backend |
| LLM planning | **Modify** | Enhance to output RMS schema |
| Groove engine concepts | **Keep** | Use for humanization post-processing |
| Rule-based renderers | **Replace** | This is what we're upgrading |
| Critic scoring | **Modify** | Add emotion-aware, motif-aware metrics |

---

## Resources & References

### Datasets
- **Lakh MIDI Dataset**: 176,000+ MIDI files, multi-genre
- **MAESTRO**: High-quality piano MIDI from performances
- **Groove MIDI Dataset**: Expressive drum performances
- **FMA**: Audio + metadata (for emotion labels via audio analysis)

### Papers
- *Music Transformer* (Huang et al., 2018) - Foundation for neural music generation
- *Pop Music Transformer* (Huang & Yang, 2020) - Song-level structure
- *Anticipatory Music Transformer* (Thickstun et al., 2023) - Infilling and continuation
- *MuseCoco* (Lu et al., 2023) - Text-to-symbolic music
- *Compound Word Transformer* (Hsiao et al., 2021) - Efficient tokenization
- *Music Emotion Recognition* (Yang & Chen, 2012) - Valence-arousal in music

### Code Repositories
- MuseCoco: https://github.com/microsoft/muzic/tree/main/musecoco
- Anticipatory Music Transformer: https://github.com/jthickstun/anticipation

---

## Open Research Questions

1. **Multi-track coherence**: How to make independently generated tracks sound like a band playing together? (Addressed via sequential generation + iterative refinement)

2. **Long-range structure**: How to maintain musical coherence over a 3-minute song? (Addressed via section-local generation + motif threading)

3. **Emotional mapping**: How to reliably translate "bittersweet" into musical parameters? (Addressed via explicit emotion vectors)

4. **Genre boundaries**: How to generate authentic style without overfitting to clichés?

5. **Real-time interaction**: Can we generate fast enough for interactive composition?

6. **Motif recognizability**: How to ensure transformed motifs remain recognizable to listeners?

7. **Cross-cultural emotion**: Do emotion vectors map to musical parameters consistently across genres/cultures?

---

## Architectural Constraints (Non-Negotiables)

These constraints must be preserved across all implementation phases:

1. **MIDI-first**: We generate symbolic music, not audio
2. **Section-local generation**: No unconstrained full-song generation
3. **Sequential generation as Phase 1**: Don't jump to joint generation prematurely
4. **Existing DAW tool interfaces**: Frontend integration must not break
5. **Iterative refinement**: Not batch retraining or full regeneration
6. **Explainable conditioning**: Emotion vectors, not black-box embeddings
7. **Motif threading**: Musical coherence through explicit structure, not hope
8. **Rule-based humanization preserved**: Groove engine concepts remain valuable

---

## The Meta-Layer: Musical Reasoning Engine (Future Phase)

> *This section describes a capability layer that builds on top of the generation system. It is not required for Phases 1-5, but should inform architectural decisions from the start.*

### The Insight

The current architecture knows **what** to generate.

The next level is knowing **why** something was generated.

This is the difference between:
- A tool that makes music
- A system that **understands** music and can explain it back to you

### Musical Intent Trace

Every generation decision should produce an optional **rationale trace** — a structured explanation of why that decision was made.

#### Schema

```json
{
  "trace_id": "uuid",
  "timestamp": "2026-02-02T10:31:00Z",
  "decision_type": "melody_register_shift",
  "target": {
    "section_id": "chorus",
    "track": "melody",
    "beat_range": [0, 8]
  },
  "decision": "Raised melody register by one octave",
  "because": [
    {
      "factor": "emotion_vector.energy",
      "value_change": "0.3 → 0.8",
      "weight": 0.4
    },
    {
      "factor": "section_type",
      "value": "chorus",
      "implication": "chorus typically has higher melodic register than verse",
      "weight": 0.3
    },
    {
      "factor": "motif.allowed_transformations",
      "value": "includes 'register_shift'",
      "implication": "transformation is permitted for this motif",
      "weight": 0.2
    },
    {
      "factor": "entry_state.melodic_register",
      "value": "mid",
      "implication": "previous section ended mid-register, upward movement creates lift",
      "weight": 0.1
    }
  ],
  "alternatives_considered": [
    {
      "decision": "Keep register same, increase note density instead",
      "rejected_because": "motion axis already high (0.7), density increase would over-saturate"
    }
  ],
  "confidence": 0.85,
  "musical_principle": "Choruses typically occupy higher register than verses to create emotional lift and differentiation"
}
```

#### Trace Types

| Decision Type | What It Explains |
|---------------|------------------|
| `chord_selection` | Why this chord was chosen at this point |
| `melody_register_shift` | Why melody moved to different octave |
| `motif_transformation` | Why motif was transformed this way |
| `instrument_entry` | Why instrument entered at this bar |
| `density_change` | Why note density increased/decreased |
| `rhythm_pattern` | Why this drum pattern was selected |
| `harmonic_tension` | Why tension was added/resolved here |
| `dynamic_curve` | Why velocity follows this shape |
| `section_transition` | Why sections connect this way |
| `refinement_target` | Why this dimension was identified as weak |

### Why This Matters

#### 1. Debugging

When something sounds wrong, the trace tells you exactly which decision led there:

```
User: "The chorus feels flat"
System: [Analyzes trace]
→ "The melody register was not raised because motion axis (0.9) 
   triggered density increase instead. The melody is busy but 
   not elevated. Suggest: reduce motion to 0.6, regenerate melody."
```

#### 2. Trust

Users (especially professional producers) don't trust black boxes. Traces build confidence:

```
"I raised the melody in the chorus because choruses typically 
occupy higher register (musical principle), your energy vector 
jumped from 0.3 to 0.8 (your intent), and the main_theme motif 
allows register_shift (structural permission)."
```

#### 3. Education

Stori becomes a music theory teacher:

```
User: "Why did you use that chord?"
System: "I used Dm7 here because:
- The progression was moving IV → V → I
- Dm7 is the ii chord, which commonly precedes V (G)
- Your tension axis was 0.6, and ii-V creates smooth tension
- This is called a 'ii-V-I turnaround' — one of the most 
  common progressions in Western harmony."
```

#### 4. Agent Collaboration

When multiple agents (or future AI collaborators) work together, traces are the shared language:

```json
{
  "from_agent": "harmony_agent",
  "to_agent": "melody_agent",
  "message": "I placed a sus4 on bar 7 beat 3. Tension axis is 0.7. 
              Suggest resolving to chord tone on bar 8 beat 1.",
  "trace_ref": "trace_id_12345"
}
```

#### 5. Musical Diff

When the user says "make it sadder" and we regenerate, we can explain exactly what changed and why:

```
## Musical Diff: chorus (before → after)

### Emotion Vector Changes
- valence: 0.3 → -0.1 (user requested "sadder")
- energy: 0.8 → 0.7 (co-adjusted: sadness often correlates with less energy)

### Resulting Musical Changes
1. Melody register: mid_high → mid
   - Because: lower register correlates with negative valence
   
2. Chord voicing: open → close
   - Because: close voicings feel more intimate/melancholy
   
3. Drum pattern: driving → sparse
   - Because: energy reduction from 0.8 → 0.7

### Preserved Elements
- Motif usage: main_theme at beat 0 (unchanged)
- Chord progression: C-G-Am-F (unchanged — harmonic structure stable)
- Section duration: 8 bars (unchanged)
```

#### 6. Teaching Interns / New Team Members

The trace corpus becomes training data for understanding the system:

```
"Here are 500 examples of why the system chose to raise 
melodic register. Notice how energy > 0.6 AND section_type 
= chorus appears in 78% of cases..."
```

### Implementation Approach

#### Phase 1-5: Prepare the Foundation

During core implementation, ensure every decision point can **emit** a trace:

```python
class DecisionPoint:
    """A point where a musical decision is made."""
    
    def __init__(self, decision_type: str, target: dict):
        self.decision_type = decision_type
        self.target = target
        self.factors: list[Factor] = []
        self.alternatives: list[Alternative] = []
    
    def add_factor(self, name: str, value: Any, implication: str, weight: float):
        self.factors.append(Factor(name, value, implication, weight))
    
    def add_alternative(self, decision: str, rejected_because: str):
        self.alternatives.append(Alternative(decision, rejected_because))
    
    def emit_trace(self) -> MusicalIntentTrace:
        """Generate the trace object. Can be no-op in production if traces disabled."""
        return MusicalIntentTrace(
            decision_type=self.decision_type,
            target=self.target,
            factors=self.factors,
            alternatives=self.alternatives,
            confidence=self._compute_confidence(),
            musical_principle=self._lookup_principle()
        )
```

The traces can be:
- **Disabled** in production for performance
- **Enabled** for debugging, education mode, or agent collaboration
- **Stored** for corpus analysis and model improvement

#### Future Phase: Reasoning Engine

Once traces are flowing, build the reasoning layer:

1. **Trace Storage** — Persist traces for analysis
2. **Trace Query** — "Why did the chorus sound different from the verse?"
3. **Trace-Based Refinement** — "Undo the register shift but keep the density change"
4. **Trace-to-Natural-Language** — Generate human-readable explanations
5. **Trace-Based Learning** — Use trace patterns to improve future decisions

### The Vision

This is how Stori becomes:

> Not just a DAW you talk to — but a **DAW that explains music back to you**.

A producer asks: *"Why does this work?"*

Stori answers: *"Here's exactly why, and here's the music theory behind it."*

A student asks: *"How do I make a chorus feel bigger?"*

Stori answers: *"Here are the 7 techniques I used, ranked by impact, with examples from your own project."*

An AI collaborator asks: *"What was your intent here?"*

Stori answers: *"Here's my trace. Continue from this reasoning."*

That's legacy-level.

---

## Next Steps

### Completed ✅
1. ~~**Implement EmotionVector schema**~~ - ✅ `app/core/emotion_vector.py`
2. ~~**Set up MIDI tokenization**~~ - ✅ `app/services/neural/tokenizer.py`
3. ~~**Prototype emotion-conditioned generation**~~ - ✅ Mock backend working

### Immediate (This Week)
4. **Evaluate MuseCoco and Anticipatory Music Transformer** - Run examples, assess quality, choose model
5. **Integrate real model backend** - Replace `MockNeuralMelodyBackend` with actual model
6. **Wire into existing API** - Enable A/B testing neural vs rule-based

### Next Sprint
7. **Implement MotifIR schema** - First-class motif objects
8. **Implement SectionSpec schema** - Section-local generation boundaries
9. **Curate initial dataset** - Genre-specific subsets with emotion labels
10. **Instrument PKR proxy collection** - Add telemetry hooks for measurement

### Future
11. **Design DecisionPoint interface** - Prepare trace emission points for future Reasoning Engine
12. **Multi-track coherence** - Sequential generation with cross-track conditioning

---

## Change Log

| Version | Date | Changes |
|---------|------|---------|
| 2.1 | 2026-02-02 | Added implementation status, updated Phase 1 progress |
| 2.0 | 2026-02-02 | Added EmotionVector, MotifIR, PKR metrics, Reasoning Engine vision |
| 1.0 | 2026-02-02 | Initial roadmap |

---

*This document represents the strategic vision for Stori's neural MIDI generation system. The goal is to create something that doesn't exist yet: natural language to emotionally resonant, multi-track MIDI that gives people chills.*

*Version 2.1 reflects MVP scaffolding complete: EmotionVector, REMI tokenizer, and mock neural backend are implemented and tested.*
