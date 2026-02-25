"""STORI PROMPT pool — Americas region.

Covers: lo-fi boom bap, dark trap, funk, neo-soul, jazz, bossa nova,
New Orleans brass, indie folk, reggaeton, and future additions
(bluegrass, gospel, hip-hop, cumbia, tango, huayno, dancehall, calypso).
"""
from __future__ import annotations

from app.models.maestro_ui import PromptItem

PROMPTS_AMERICAS: list[PromptItem] = [

    # 1 ── Lo-fi boom bap ────────────────────────────────────────────────────
    PromptItem(
        id="lofi_boom_bap",
        title="Lo-fi boom bap \u00b7 Cm \u00b7 75 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: lofi hip hop \u00b7 Key: Cm \u00b7 75 BPM\nRole: drums, bass, piano, melody\nVibe: dusty x3, warm x2, melancholic",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: lofi hip hop
Key: Cm
Tempo: 75
Energy: low
Role: [drums, bass, piano, melody]
Constraints:
  bars: 24
  density: medium-sparse
Vibe: [dusty x3, warm x2, melancholic, laid-back]

Request: |
  A complete lo-fi boom bap piece in three sections. 4-bar ambient intro
  with just piano and reverb. 12-bar verse with lazy swing drums, deep
  bass on Cm-Ab-Eb-Bb, and a wistful dorian melody. 8-bar chorus where
  the melody opens up, the bass gets more melodic, and a subtle vinyl
  crackle texture enters. Like Nujabes scoring a Miyazaki rain scene.

Harmony:
  progression: [Cm7, Abmaj7, Ebmaj7, Bb7sus4]
  voicing: rootless close position
  rhythm: half-note stabs on beats 1 and 3
  extensions: 9ths throughout, Cm add 11 in chorus
  color: bittersweet — Abmaj7 is the emotional peak each cycle
  reharmonize: |
    Chorus substitutes Bb7sus4 with Bbmaj9 for resolution warmth

Melody:
  scale: C dorian
  register: mid (Bb4-G5)
  contour: |
    Intro: single repeated piano motif, C-Eb-G, descending
    Verse: descending arch, resolves up on bar 12
    Chorus: wider intervals, leaps to Bb5 on emotional peaks
  phrases:
    structure: 2-bar call, 2-bar response
    breath: 1.5 beats of silence between phrases
  density: sparse — average 1 note per beat
  ornamentation:
    - grace notes on the minor 3rd
    - occasional blue note (Db) in verse

Rhythm:
  feel: behind the beat
  swing: 56%
  ghost_notes:
    instrument: snare
    velocity: 28-40
  hi_hat: slightly open on the ands
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early — lazy pocket push

Dynamics:
  overall: pp to mf across 24 bars
  arc:
    - bars: 1-4
      level: pp
      shape: flat — intro stillness
    - bars: 5-16
      level: mp
      shape: gentle phrase swells
    - bars: 17-24
      level: mf
      shape: chorus lifts, slight crescendo to bar 22, decrescendo bar 23-24
  accent_velocity: 88
  ghost_velocity: 32
  expression_cc:
    curve: follow dynamic arc
    range: [35, 92]

Orchestration:
  drums:
    kit: boom bap
    kick: slightly late, warm thud, enters bar 5
    snare: cracked, slightly behind, enters bar 5
    hi_hat: enters bar 7, loose 8th notes
  bass:
    technique: finger style
    register: E2-G3
    articulation: legato, occasional staccato on syncopations
    entry: bar 5
  piano:
    voicing: rootless, 7th and 3rd only
    pedaling: half pedal
    right_hand: sparse single-note melody
    entry: bar 1 — owns the intro alone
  melody:
    instrument: warm synth pad, breathy
    register: Bb4-Bb5
    entry: bar 9 — enters mid-verse

Effects:
  drums:
    saturation: tape, subtle
    compression:
      type: slow attack, let transients breathe
      ratio: 3:1
  bass:
    saturation: tube, warm
    eq:
      - band: low shelf
        freq: 80hz
        gain: +2db
  piano:
    reverb: small room, 0.7s, 14ms predelay
  master:
    tape_warmth: true

Expression:
  arc: silence to resignation to quiet acceptance
  narrative: |
    The intro is 3am. Just you and the piano. Rain outside. Then the drums
    arrive like a memory of something you used to feel. By the chorus you
    are not sad anymore — you are present. Every note chosen. Nothing wasted.
    The music doesn't try to fix anything. It just stays.
  spatial_image: |
    Intro: piano alone, center, reverberant. Verse: drums back-center,
    bass upfront, piano drifts left. Chorus: melody enters right,
    everything slightly wider.
  character: Nujabes meets Ryuichi Sakamoto. Unhurried. Human.

Texture:
  density: very sparse (intro) to medium-sparse (chorus)
  register_spread: E2-Bb5
  space: silence between every phrase — let it breathe
  stereo_field:
    drums: center
    bass: center
    piano: left -15
    melody: right +20

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-4)
      intensity: pp, establishing — piano alone with reverb
    - section: verse (bars 5-16)
      variation: drums and bass enter bar 5, melody enters bar 9
    - section: chorus (bars 17-24)
      contrast: melody opens up, bass becomes melodic, vinyl texture enters
  variation_strategy: |
    Each section reveals one more layer. Intro: piano alone. Verse: rhythm
    section grounds it. Chorus: the melody finally says what the chords
    were feeling all along.

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.02
    grid: 16th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 2]
      strength: 8
    ghost_notes:
      probability: 0.08
      velocity: [28, 42]
  feel: behind the beat

MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches
    changes_per_bar: 2
  expression:
    curve: slow swell across sections
    range: [35, 92]
  pitch_bend:
    style: subtle blues bends on minor 3rds
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 30
      to: 65
      position: bars 1-24
    - cc: 11
      from: 35
      to: 92
      position: bars 1-24
  aftertouch:
    type: channel
    response: gentle — adds warmth on sustained piano tones
    use: slight filter opening
  modulation:
    instrument: melody synth pad
    depth: slow vibrato — CC 1 from 0 to 30 over phrase
    onset: delayed 2 beats
  filter:
    cutoff:
      sweep: slowly opens from 800hz to 3khz across 24 bars
      resonance: low

Automation:
  - track: Piano
    param: reverb_wet
    events:
      - beat: 0
        value: 0.5
        curve: linear
      - beat: 16
        value: 0.25
        curve: smooth
      - beat: 64
        value: 0.35
        curve: linear
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -4db
      - beat: 64
        value: 0db
        curve: smooth
""",
    ),

    # 6 ── Jazz reharmonization ──────────────────────────────────────────────
    PromptItem(
        id="jazz_reharmonization",
        title="Jazz reharmonization \u00b7 Bb \u00b7 120 BPM",
        preview="Mode: compose \u00b7 Section: bridge\nStyle: bebop jazz \u00b7 Key: Bb \u00b7 120 BPM\nRole: piano, upright bass, drums\nVibe: jazzy x2, mysterious x2, bittersweet, flowing",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: bridge
Style: bebop jazz
Key: Bb
Tempo: 120
Energy: medium
Role: [piano, upright bass, drums]
Constraints:
  bars: 24
Vibe: [jazzy x2, mysterious x2, bittersweet, flowing, daring, conversational]

Request: |
  A full jazz trio piece in three acts. 8-bar head — the melody stated
  simply and beautifully over Bbmaj7-Ebm7-Ab7-Dmaj7 with aggressive
  tritone substitutions and secondary dominants. The piano right hand
  plays the melody, left hand walks. 8-bar solo section — the melody
  dissolves into improvised bebop lines, increasingly chromatic, the
  bass takes over walking, the drums open up with snare bombs. 8-bar
  head return — the melody comes back, but reharmonized even further,
  ending on an unexpected Gmaj7 that leaves you hanging between worlds.
  Ahmad Jamal's space. Bill Evans' harmony. Oscar Peterson's fire.

Harmony:
  progression: |
    Head (1-8): [Bbmaj7, Ebm7-Ab7, Dmaj7, Gm7-C7, Fm7-Bb7, Ebmaj7, Am7-D7, Gmaj7]
    Solo (9-16): [Cm7-F7, Bbmaj7, Ebm7-Ab7, Dbmaj7, Dm7-G7, Cm7, F7alt, Bbmaj7]
    Head return (17-24): [Bbmaj7, Ebm7-Ab7, Dmaj7#11, Gm7-C7, Fm7-E7, Ebmaj7, Am7-D7, Gmaj7]
  voicing: rootless — 3rd and 7th in left hand, extensions in right
  rhythm: |
    Head: comping behind melody — syncopated, sparse.
    Solo: active comping, rhythmic variety, responding to the line.
    Head return: fuller voicings, richer, more resolved.
  extensions: 9ths, 11ths, 13ths throughout — every chord is extended
  reharmonize: |
    Head: Bbmaj7 \u2192 Ebm7-Ab7 (bII7 sub). Target Dmaj instead of IV.
    Solo: free reharmonization, chromatic ii-V chains.
    Head return: Dmaj7#11 adds Lydian brightness. Fm7 slips to E7 —
    chromatic approach to Ebmaj7. Am7-D7-Gmaj7 ending refuses Bb resolution.
  tension:
    point: bar 22
    device: Am7-D7 hangs unresolved for 2 beats
    release: Gmaj7 on bar 23 — unexpected key, earned arrival

Melody:
  scale: Bb bebop scale with chromatic passing tones
  register: mid-upper (C4-F5)
  contour: |
    Head: singing melody, descending bars 1-4, ascending bars 5-8.
    Solo: bebop lines, increasingly dense, chromatic runs.
    Head return: melody restated with ornaments, richer, more final.
  phrases:
    structure: |
      Head: 2-bar melodic statements with quarter-note breaths.
      Solo: 4-bar phrases, each more adventurous than the last.
      Head return: same 2-bar structure as head, embellished.
    breath: quarter-note rest between phrases
  density: |
    Head: medium — clear melodic statement.
    Solo: dense — 8th-note lines, triplet bursts, occasional 16th runs.
    Head return: medium, ornamental.
  ornamentation:
    - grace notes on approach tones throughout
    - blue notes on b3 and b7 in solo
    - trills on cadential notes in head return

Rhythm:
  feel: slightly ahead — bebop urgency
  subdivision: 8th-note triplet feel
  swing: 62%
  ghost_notes:
    instrument: snare
    velocity: 35-50
  hi_hat: foot hat on 2 and 4
  pushed_hits:
    - beat: 3.75
      anticipation: pickup into bar 9 (solo entrance)

Dynamics:
  overall: mp to f and back
  arc:
    - bars: 1-8
      level: mf
      shape: flat, conversational — stating the melody
    - bars: 9-12
      level: mf to f
      shape: solo builds intensity
    - bars: 13-16
      level: f
      shape: peak — drums open up, snare bombs
    - bars: 17-20
      level: mf
      shape: head returns, slightly softer, more settled
    - bars: 21-24
      level: mp
      shape: diminuendo into Gmaj7 ending — quiet wonder
  accent_velocity: 100
  ghost_velocity: 40

Orchestration:
  piano:
    head: |
      Right hand: melody, single notes, clear and singing.
      Left hand: walking bass bars 1-4, rootless comps bars 5-8.
    solo: |
      Right hand: bebop lines, increasingly chromatic.
      Left hand: comping, responding to the right hand.
    head_return: |
      Right hand: melody with turns and trills.
      Left hand: fuller voicings, 4-note rootless chords.
    pedaling: minimal — just for phrase connection, never blurring
  bass:
    technique: |
      Head: sparse, roots and 5ths on beats 1 and 3.
      Solo: full walking — each beat a different chord tone or approach.
      Head return: walking with melodic embellishments.
    register: Bb1-Bb3
    articulation: legato with slight portamento on leaps
  drums:
    head: |
      Ride: continuous 8th-note swing pattern.
      Hi-hat: foot on 2 and 4. Kick: feathered.
    solo: |
      Ride: opens up, louder. Snare: bombs on bar 13 beats 2 and 4.
      Kick: more active, responding to piano lines.
    head_return: |
      Ride: returns to head pattern. Everything settles.
      Final bar: ride bell on beat 4, then silence.

Effects:
  piano:
    reverb: small bright room, 0.5s, 6ms predelay
    eq:
      - band: presence
        freq: 3khz
        gain: +2db
  bass:
    eq:
      - band: warmth
        freq: 200hz
        gain: +3db
  drums:
    reverb: same room as piano — trio shares space
    compression:
      type: gentle, preserve dynamics
      ratio: 2:1

Expression:
  arc: statement to exploration to homecoming
  narrative: |
    The head is a question asked clearly. The solo is the search for
    an answer — going further and further from home, taking harmonic
    risks, finding beauty in the wrong notes. The head return is the
    answer, but it is not the answer you expected. It ends on Gmaj7
    instead of Bb. You went looking for home and found somewhere better.
    This is what jazz does. It makes wrong turns into right ones.
  character: Ahmad Jamal's space. Bill Evans' voicings. Oscar Peterson's
    fire in the solo. The Gmaj7 ending is pure Keith Jarrett.
  spatial_image: |
    Piano slightly left. Bass right. Drums center and behind.
    The trio shares a small bright room. You can hear them listening
    to each other.

Texture:
  density: medium (head) to dense (solo) to medium (head return)
  register_spread: Bb1-F5
  space: |
    Head: the melody owns the room, everything else listens.
    Solo: bass and drums join the conversation as equals.
    Head return: the melody comes back wiser. Space returns.

Form:
  structure: head-solo-head_return
  development:
    - section: head (bars 1-8)
      intensity: mf — melody stated simply, tritone subs surprise
    - section: solo (bars 9-16)
      variation: bebop improv, increasingly chromatic, drums open up
    - section: head_return (bars 17-24)
      contrast: melody restated with ornaments, settles to Gmaj7
  variation_strategy: |
    The head is a promise. The solo breaks that promise to see what
    happens. The head return keeps the promise differently — same
    melody, new harmony, unexpected ending. Three acts of a
    conversation between friends who finish each other's sentences.

Humanization:
  timing:
    jitter: 0.06
    late_bias: -0.01
    grid: 8th triplet
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [1, 3]
      strength: 6
    ghost_notes:
      probability: 0.1
      velocity: [32, 50]
  feel: slightly ahead — bebop urgency, always leaning forward

MidiExpressiveness:
  sustain_pedal:
    style: minimal catches — connect phrase tones only
    changes_per_bar: 4
  expression:
    curve: |
      Head: conversational mf. Solo: building to f.
      Head return: settling to mp. Final bar: pp.
    range: [45, 105]
  pitch_bend:
    style: bass slides on approach notes, piano blue-note bends
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 22
      to: 42
      position: bars 1-24
    - cc: 11
      from: 55
      to: 105
      position: bars 1-16
    - cc: 11
      from: 105
      to: 50
      position: bars 17-24
  articulation:
    legato: true
    portamento:
      time: 35
      switch: on
  aftertouch:
    type: channel
    response: adds warmth on sustained piano chords
    use: slight volume swell and filter opening
  modulation:
    instrument: upright bass
    depth: subtle vibrato — CC 1 from 0 to 20 on long notes
    onset: delayed 1 beat
  filter:
    cutoff:
      sweep: warm low-pass opens from 1.2khz to 4khz across piece
      resonance: low

Automation:
  - track: Piano
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
        curve: linear
      - beat: 48
        value: 0.4
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -2db
      - beat: 96
        value: 0db
        curve: smooth
""",
    ),

    # 7 ── Dark trap ─────────────────────────────────────────────────────────
    PromptItem(
        id="dark_trap",
        title="Dark trap \u00b7 Fm \u00b7 140 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: dark trap \u00b7 Key: Fm \u00b7 140 BPM\nRole: drums, 808, pad, melody, vocal chop\nVibe: dark x3, haunting x2, brooding, aggressive",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: dark trap
Key: Fm
Tempo: 140
Energy: medium
Role: [drums, 808, pad, melody, vocal chop]
Constraints:
  bars: 24
Vibe: [dark x3, haunting x2, brooding, aggressive, menacing]

Request: |
  A full dark trap piece in three acts. 4-bar intro — eerie pad alone,
  stacked fifths in Fm, wide and empty. Just dread. 12-bar verse — sparse
  hi-hat triplets enter with randomized velocity, the 808 slides on F1-C1,
  a melodic bell line plays the minor pentatonic with maximum space, snare
  clap only on beat 3. 8-bar hook — a pitched vocal chop enters, the 808
  pattern doubles, the bell melody becomes more insistent, and a sub-bass
  drone adds weight. The hook doesn't resolve — it just gets heavier.
  Travis Scott's atmosphere meets Metro Boomin's architecture.

Harmony:
  progression: [Fm, Db, Ab, Eb]
  voicing: stacked 5ths, no 3rds — cold and hollow
  rhythm: |
    Intro: whole-note pad swells, no rhythmic movement.
    Verse: pad continues, 808 provides bass motion.
    Hook: vocal chop adds implied melody, sub-bass drone on F0.
  extensions: none — bare power chords for menace
  color: cold, hollow, threatening — no warmth anywhere

Melody:
  scale: F minor pentatonic
  register: upper (F5-C6)
  contour: |
    Intro: no melody — just the void.
    Verse: mostly static bell line, occasional upward flick, 3-4 notes per bar.
    Hook: bell becomes more insistent, patterns tighten, vocal chop adds call.
  phrases:
    structure: |
      Verse: 1-bar phrases with 1-bar rests — menacing patience.
      Hook: 2-bar phrases, tighter, more aggressive.
  density: very sparse (verse) to medium-sparse (hook)

Rhythm:
  feel: quantized grid — trap is surgical
  subdivision: 16th-note triplet hi-hat pattern
  swing: 50%
  accent:
    pattern: triplet hi-hat with random velocity drops
    weight: heavy downbeat kick, sparse snare clap on 3
  pushed_hits:
    - beat: 3.75
      anticipation: hi-hat roll leading into hook at bar 17

Dynamics:
  overall: pp to f across 24 bars
  arc:
    - bars: 1-4
      level: pp
      shape: flat — intro dread, pad only
    - bars: 5-16
      level: mp
      shape: verse — controlled menace, flat
    - bars: 17-20
      level: mf
      shape: hook lifts, 808 doubles
    - bars: 21-24
      level: f
      shape: hook peaks, sub-bass drone enters, relentless
  accent_velocity: 115
  ghost_velocity: 25

Orchestration:
  drums:
    kick: 808-style sub kick, pitch slides F1-C1 over 500ms
    snare: trap clap — beat 3 only, enters bar 5
    hi_hat: 16th-note triplet, randomized velocity 35-90, enters bar 5
    hi_hat_hook: pattern doubles in hook, rolls on bar 16 beat 4
  808:
    pitch: F1 slide to C1 over 1 bar
    tail: 2 bars sustain
    distortion: subtle saturation for presence
    entry: bar 5
    hook_variation: pattern doubles in hook, alternating F1 and Ab0
  pad:
    voicing: stacked 5ths in Fm spread wide
    attack: slow 1.5s
    stereo: wide \u00b170
    entry: bar 1 — owns the intro
  melody:
    instrument: bell synth, crystalline, cold
    register: F5-C6
    entry: bar 7 — enters mid-verse
  vocal_chop:
    style: pitched, chopped, ghostly
    pitch: Ab4 and C5 alternating
    entry: bar 17 — hook only
  sub_bass:
    pitch: F0 drone
    entry: bar 21 — final 4 bars, adds crushing weight

Effects:
  drums:
    compression:
      type: hard limiting
      ratio: 10:1
    saturation: subtle drive on kick
  808:
    distortion: light clip for low-end presence
    eq:
      - band: sub boost
        freq: 50hz
        gain: +4db
  pad:
    reverb: large dark hall, 5s, 80% wet
    filter: lowpass at 2khz
  vocal_chop:
    reverb: dark plate, 2s, 60% wet
    pitch_correction: none — raw and ghostly

Expression:
  arc: dread to menace to crushing weight
  narrative: |
    The intro is the moment before something goes wrong. You feel it but
    you cannot name it. The verse is the thing arriving — sparse, patient,
    inevitable. The hook is the weight of it settling on your chest.
    Nothing resolves. Nothing escapes. The sub-bass at bar 21 is gravity
    itself. You don't listen to this music. You survive it.
  spatial_image: |
    Intro: pad wide, nothing else. Verse: 808 center, hi-hats scattered,
    bell melody slightly right, pad wide. Hook: vocal chop left, sub-bass
    everywhere, 808 center, bell right.
  character: Metro Boomin's architecture. Travis Scott's void. 21 Savage's patience.

Texture:
  density: very sparse (intro/verse) to medium (hook)
  register_spread: F0-C6
  space: deliberate emptiness — the trap is in the silence
  stereo_field:
    808: center
    pad: wide \u00b170
    melody: right +20
    vocal_chop: left -25
    hi_hat: scattered \u00b130

Form:
  structure: intro-verse-hook
  development:
    - section: intro (bars 1-4)
      intensity: pp — pad alone, establishing dread
    - section: verse (bars 5-16)
      variation: drums enter bar 5, melody enters bar 7, controlled menace
    - section: hook (bars 17-24)
      contrast: vocal chop enters, 808 doubles, sub-bass adds weight bar 21
  variation_strategy: |
    Each section adds weight without adding brightness. The intro is
    empty. The verse is sparse. The hook is dense but dark. The
    trajectory is always downward — deeper, heavier, darker.

Humanization:
  timing:
    jitter: 0.02
    late_bias: 0.0
    grid: 16th triplet
  velocity:
    arc: flat
    stdev: 18
    accents:
      beats: [0]
      strength: 15
    ghost_notes:
      probability: 0.12
      velocity: [22, 38]
  feel: on the grid — trap is surgical precision

MidiExpressiveness:
  pitch_bend:
    style: 808 slide — programmatic downward bend on each note start
    depth: full range 2 semitones
  cc_curves:
    - cc: 74
      from: 15
      to: 85
      position: bars 1-24
    - cc: 91
      from: 35
      to: 80
      position: bars 1-24
  expression:
    curve: rises across sections
    range: [35, 95]
  aftertouch:
    type: channel
    response: deepens pad texture on sustained notes
    use: filter opening and volume swell
  modulation:
    instrument: pad
    depth: slow dark vibrato — CC 1 from 0 to 45 across section
    onset: delayed 1 beat

Automation:
  - track: Pad
    param: filter_cutoff
    events:
      - beat: 0
        value: 1.5khz
      - beat: 64
        value: 2.5khz
        curve: smooth
  - track: 808
    param: distortion
    events:
      - beat: 64
        value: 0.15
      - beat: 96
        value: 0.35
        curve: linear
""",
    ),

    # 8 ── Bossa nova ────────────────────────────────────────────────────────
    PromptItem(
        id="bossa_nova",
        title="Bossa nova \u00b7 Em \u00b7 132 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: bossa nova \u00b7 Key: Em \u00b7 132 BPM\nRole: guitar, bass, drums, flute\nVibe: warm x3, intimate x2, nostalgic, flowing",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: bossa nova
Key: Em
Tempo: 132
Energy: low
Role: [guitar, bass, drums, flute]
Constraints:
  bars: 24
Vibe: [warm x3, intimate x2, nostalgic, flowing, bittersweet, saudade]

Request: |
  A complete bossa nova piece in three sections. 4-bar intro — nylon
  guitar alone, chord-melody style, establishing the Jobim rhythmic
  pattern (dotted-quarter, 8th, 8th) on Em7. Just the guitar and
  breath. 12-bar verse — upright bass enters walking Em7-Am7-D7-Gmaj7,
  brush drums whisper on 2 and 4, and the guitar carries both rhythm
  and melody. 8-bar bridge — a flute-style melody enters with the
  saudade line, the harmony shifts to the relative major (Cmaj7-F#7b9),
  and the piece reaches its emotional peak before settling back.
  Tom Jobim on a slow afternoon in Ipanema. No hurry. Everywhere to be.

Harmony:
  progression: |
    Intro (1-4): [Em7, Em7, Em9, Em9]
    Verse (5-16): [Em7, Am7, D7, Gmaj7, Cmaj7, F#7b9, Bm7, B7,
                    Em7, Am7, D7, Gmaj7]
    Bridge (17-24): [Cmaj7, F#7b9, Bm7, E7, Am7, D7, Gmaj7, Em7]
  voicing: guitar chord melody — thumb bass, fingers play chord + melody
  rhythm: bossa clave — 3-3-2 pattern of 8th notes
  extensions: 9ths, 11ths, 13ths — rich jazz extensions throughout
  color: warm bittersweet — major 7ths always present
  reharmonize: |
    Bridge: Cmaj7 to F#7b9 is the emotional pivot — the tritone sub
    creates yearning that the Bm7-E7 only partly resolves.

Melody:
  scale: E dorian with chromatic passing tones
  register: |
    Guitar melody: mid (G4-E5)
    Flute melody: upper (B4-E6) — enters bridge only
  contour: |
    Intro: guitar states the theme simply, single notes over chords.
    Verse: theme expands, call-and-response between guitar and bass.
    Bridge: flute takes over with wider intervals, lyrical peak on bar 21.
  phrases:
    structure: 4-bar phrases with half-bar breath
    breath: short space — bossa is conversational
  density: sparse intro, medium verse, lyrical bridge

Rhythm:
  feel: slightly behind the beat — lush, relaxed, saudade
  subdivision: 8th notes, bossa clave pattern
  swing: 53%
  accent:
    pattern: bossa clave — long-short-short feel
    weight: gentle, never accented harshly
  ghost_notes:
    instrument: brush snare
    velocity: 28-42

Dynamics:
  overall: pp to mf
  arc:
    - bars: 1-4
      level: pp
      shape: flat — guitar alone, intimate
    - bars: 5-16
      level: mp
      shape: flat, conversational — the verse grooves
    - bars: 17-20
      level: mp to mf
      shape: bridge lifts gently, flute adds presence
    - bars: 21-24
      level: mf to mp
      shape: peak at bar 21, gentle diminuendo to end
  accent_velocity: 82
  ghost_velocity: 30

Orchestration:
  guitar:
    technique: nylon string, fingerpicked
    voicing: chord-melody — bass + inner chord + melody simultaneously
    articulation: legato melody, slightly staccato inner voices
    entry: bar 1 — owns the entire piece
  bass:
    technique: finger style, upright sound
    register: E1-D2
    pattern: |
      Verse: roots on 1 and 3, chord tones on 2 and 4.
      Bridge: more melodic, fills between guitar phrases.
    entry: bar 5
  drums:
    style: brush snare on 2 and 4, wire brushes on ride
    kick: very light — beats 1 and 3 only
    entry: bar 5
  flute:
    technique: breathy, lyrical, warm
    register: B4-E6
    entry: bar 17 — bridge only
    articulation: legato, slight vibrato on sustained notes

Effects:
  guitar:
    reverb: small bright room, 0.6s, 8ms predelay
    eq:
      - band: warmth
        freq: 250hz
        gain: +2db
  bass:
    eq:
      - band: fundamental
        freq: 80hz
        gain: +3db
  flute:
    reverb: same room, 0.8s, slightly wetter
    delay: very subtle dotted 8th, 10% wet

Expression:
  arc: solitude to conversation to saudade to peace
  narrative: |
    The intro is you alone with a guitar. The verse is a friend arriving
    with a bass and a whisper of drums. The bridge is the moment you
    remember something beautiful that is already gone — the flute sings
    what you cannot say. The ending is acceptance. Not grief. Just the
    beautiful ache of knowing it happened. Saudade.
  spatial_image: |
    Intro: guitar center, intimate. Verse: guitar slightly left, bass
    right, brushes center-back. Bridge: flute enters right, guitar
    left, bass center, everything slightly wider.
  character: Tom Jobim's melody. Joao Gilberto's rhythm. Stan Getz's
    breath on the flute. Ipanema at sunset.

Texture:
  density: very sparse (intro) to medium-sparse (verse) to lyrical (bridge)
  register_spread: E1-E6
  space: the guitar carries bass AND melody — everything else listens

Form:
  structure: intro-verse-bridge
  development:
    - section: intro (bars 1-4)
      intensity: pp — guitar alone, establishing the bossa feel
    - section: verse (bars 5-16)
      variation: bass and drums enter, guitar melody expands
    - section: bridge (bars 17-24)
      contrast: flute enters, harmony shifts to relative major, emotional peak
  variation_strategy: |
    Each section adds one voice. Intro: guitar. Verse: guitar + rhythm.
    Bridge: the flute says what the guitar was feeling all along.

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.02
    grid: 8th
  velocity:
    arc: phrase
    stdev: 12
    accents:
      beats: [0, 2]
      strength: 5
    ghost_notes:
      probability: 0.06
      velocity: [26, 40]
  feel: behind the beat — bossa is never in a hurry

MidiExpressiveness:
  sustain_pedal:
    style: no sustain — nylon guitar is naturally dry
    changes_per_bar: 0
  expression:
    curve: follows dynamic arc, pp to mf to mp
    range: [40, 92]
  pitch_bend:
    style: subtle string bends on melody notes — quarter-tone only
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 18
      to: 48
      position: bars 1-24
    - cc: 11
      from: 40
      to: 92
      position: bars 1-24
    - cc: 1
      from: 0
      to: 25
      position: bars 17-24
  articulation:
    legato: true
    portamento:
      time: 25
      switch: on
  aftertouch:
    type: channel
    response: adds warmth on sustained piano and bass tones
    use: gentle volume swell
  modulation:
    instrument: guitar
    depth: subtle vibrato — CC 1 from 0 to 20 on melody notes
    onset: delayed 1.5 beats
  filter:
    cutoff:
      sweep: gentle low-pass opens from 1khz to 5khz across bridge
      resonance: low

Automation:
  - track: Guitar
    param: reverb_wet
    events:
      - beat: 0
        value: 0.15
        curve: linear
      - beat: 48
        value: 0.3
        curve: smooth
      - beat: 96
        value: 0.2
        curve: smooth
  - track: Shaker
    param: pan
    events:
      - beat: 0
        value: -0.3
      - beat: 48
        value: 0.3
        curve: smooth
      - beat: 96
        value: -0.3
        curve: smooth
""",
    ),

    # 9 ── Funk pocket ───────────────────────────────────────────────────────
    PromptItem(
        id="funk_pocket",
        title="Funk pocket \u00b7 E \u00b7 108 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: classic funk \u00b7 Key: E \u00b7 108 BPM\nRole: drums, bass, guitar, keys, horns\nVibe: groovy x3, joyful x2, driving, energetic",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: classic funk
Key: E
Tempo: 108
Energy: high
Role: [drums, bass, guitar, keys, horns]
Constraints:
  bars: 24
Vibe: [groovy x3, joyful x2, driving, energetic, bouncy, physical]

Request: |
  A full funk workout in three sections. 4-bar intro — drums and bass
  alone, establishing the pocket. Kick on 1 and 3, slap bass pops on
  the offbeats. Just the groove, no frills. 12-bar verse — scratchy
  rhythm guitar enters on the E9 chord (offbeats only), tight clavinet
  stabs on the upbeats, bass and drums locked in the pocket. Pure rhythm
  section power. 8-bar horn section — trumpet and alto sax enter with
  the head riff, call-and-response with the clavinet, the energy peaks.
  James Brown would approve. Bootsy Collins would cry.

Harmony:
  progression: [E9, A9, E9, B9]
  voicing: 9th chords throughout — root + 7th + 9th on guitar
  rhythm: |
    Intro: bass and drums only — implied harmony.
    Verse: guitar on every 16th-note offbeat (scratchy funk).
    Horn section: horn stabs on upbeats, guitar continues underneath.
  extensions: dominant 9ths — gritty and bright
  color: raw and punchy — no softness anywhere

Melody:
  scale: E mixolydian
  register: mid (E4-B4 horns)
  contour: |
    Intro/Verse: no melody — the groove IS the statement.
    Horn section: bright ascending riff bars 17-20, call-and-response
    bars 21-24 between trumpet and alto sax.
  phrases:
    structure: |
      Horn section: 2-bar horn call, 2-bar clav response, repeated.
  density: sparse verse (rhythm only), medium-high horn section

Rhythm:
  feel: right on the beat — machine-tight funk precision
  subdivision: 16th notes
  swing: 51%
  accent:
    pattern: kick on 1 and 3, snare on 2 and 4, all 16th upbeats
    weight: everything is accented — funk commits completely
  ghost_notes:
    instrument: snare
    velocity: 35-55
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early — classic funk push
    - beat: 4.75
      anticipation: 16th note early — double push into next bar

Dynamics:
  overall: mf to ff across 24 bars
  arc:
    - bars: 1-4
      level: mf
      shape: flat — drums and bass locked, establishing
    - bars: 5-16
      level: f
      shape: verse — full rhythm section, constant energy
    - bars: 17-24
      level: f to ff
      shape: horn section lifts everything, peaks bar 22
  accent_velocity: 112
  ghost_velocity: 42

Orchestration:
  drums:
    kit: acoustic funk
    kick: D-click on attack, boom on body — 1 and 3
    snare: fat backbeat 2 and 4, ghost notes throughout
    hi_hat: 16th notes, partially closed
    entry: bar 1 — owns the intro
  bass:
    technique: slap — thumb on 1 and 3, pop on every offbeat 16th
    register: E1-E2
    articulation: ultra staccato — each note a separate event
    entry: bar 1 — locked with drums from beat 1
  guitar:
    technique: muted scratch — 16th-note offbeats only, single E9 chord
    style: scratchy rhythm, no sustain, percussive
    entry: bar 5
  keys:
    instrument: clavinet
    pattern: upbeat stabs, syncopated 8th-note hits
    entry: bar 5
  horns:
    trumpet: plays the top melody, bright
    alto_sax: plays a 3rd below, punchy
    rhythm: 16th-note punches with short rests
    entry: bar 17

Effects:
  drums:
    compression:
      type: FET — fast and punchy
      ratio: 5:1
      attack: 3ms
    eq:
      - band: kick presence
        freq: 5khz
        gain: +3db
  bass:
    compression: limiting — 10:1, no gain reduction after attack
    overdrive: subtle, add harmonics
  guitar:
    distortion: light overdrive for bite
    filter: wah-adjacent bandpass at 2khz
  horns:
    reverb: tight room, 0.4s
    compression: gentle, preserve dynamics

Expression:
  arc: lock-in to lift-off
  narrative: |
    The intro is two musicians who have played together for decades
    finding the pocket. When the guitar enters at bar 5, the room
    tightens. When the horns enter at bar 17, everybody moves. Not
    because they decide to — because the music decides for them.
    This is physics, not art. The pocket is a force of nature.
  spatial_image: |
    Intro: drums center, bass center-right. Verse: guitar left,
    clav right, rhythm section center. Horn section: trumpet right,
    alto sax left, everything wider.
  character: Bootsy Collins meets Nile Rodgers. James Brown's discipline.
    Every note serves the groove. Nothing wasted. Nothing fancy.

Texture:
  density: medium (intro/verse) to medium-high (horn section)
  register_spread: E1-B5
  space: no padding — every element has a specific rhythmic job

Form:
  structure: intro-verse-horn_section
  development:
    - section: intro (bars 1-4)
      intensity: mf — drums and bass alone, the pocket
    - section: verse (bars 5-16)
      variation: guitar and clav enter, full rhythm section
    - section: horn_section (bars 17-24)
      contrast: horns enter with the riff, call-and-response, peak energy
  variation_strategy: |
    The intro proves the groove works with nothing. The verse proves
    it works with everything. The horn section proves it can fly.

Humanization:
  timing:
    jitter: 0.02
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 10
    accents:
      beats: [0, 2]
      strength: 12
    ghost_notes:
      probability: 0.15
      velocity: [32, 55]
  feel: right on the beat — funk is precision, not laziness

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [78, 120]
  pitch_bend:
    style: bass slap slides — upward quarter-tone before each note
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 12
      to: 35
      position: bars 1-24
    - cc: 11
      from: 78
      to: 120
      position: bars 1-24
  aftertouch:
    type: channel
    response: light filter opening on sustained horn notes
    use: slight brightness boost
  modulation:
    instrument: clavinet
    depth: subtle wah effect — CC 1 from 0 to 40 on held notes
    onset: immediate

Automation:
  - track: Clavinet
    param: filter_cutoff
    events:
      - beat: 0
        value: 1.2khz
        curve: linear
      - beat: 48
        value: 4khz
        curve: smooth
      - beat: 96
        value: 2khz
        curve: smooth
  - track: Drums
    param: compressor_ratio
    events:
      - beat: 0
        value: 3.0
      - beat: 96
        value: 5.0
        curve: linear
""",
    ),

    # 10 ── Neo-soul groove ──────────────────────────────────────────────────
    PromptItem(
        id="neo_soul_groove",
        title="Neo-soul groove \u00b7 Gm \u00b7 83 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: neo-soul \u00b7 Key: Gm \u00b7 83 BPM\nRole: drums, bass, keys, guitar, melody\nVibe: warm x3, intimate x2, melancholic, groovy",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: neo-soul
Key: Gm
Tempo: 83
Energy: low
Role: [drums, bass, keys, guitar, melody]
Constraints:
  bars: 24
Vibe: [warm x3, intimate x2, melancholic, groovy, laid-back, humid]

Request: |
  A full neo-soul piece in three sections. 4-bar intro — Rhodes alone,
  lazy behind-the-beat chords on Gm9, half-pedal sustain, slow tremolo.
  Just the keys and space. 12-bar verse — live-feel drums enter with
  heavy swing, a melodic bass line talks back to the melody, muted
  guitar adds texture on upbeats, and a breathy synth melody enters
  bar 9 with short sighing phrases. 8-bar bridge — the harmony shifts
  to D7alt, the melody reaches its emotional peak, the bass plays its
  most melodic line, and the Rhodes voicings get richer. Then everything
  settles back into the Gm9 fog. D'Angelo meets J Dilla meets Erykah Badu.

Harmony:
  progression: |
    Intro (1-4): [Gm9, Gm9, Gm9, Gm9]
    Verse (5-16): [Gm7, Cm7, Ebmaj7, D7alt, Gm7, Cm7, Ebmaj7, D7alt,
                    Gm9, Cm11, Ebmaj9, D7alt]
    Bridge (17-24): [Cm9, Cm9, D7#9b13, D7#9b13, Ebmaj7#11, Ebmaj7#11, Gm9, Gm9]
  voicing: rich extended — 9ths, 11ths, #5 on D7alt
  rhythm: |
    Intro: lazy Rhodes stabs — behind the beat, whole notes.
    Verse: behind-beat comping, never on downbeats.
    Bridge: fuller voicings, half-note changes, more sustained.
  extensions: Gm9, Cm11, Ebmaj9#11, D7#9b13
  color: lush and bittersweet — D7alt creates yearning that never resolves

Melody:
  scale: G dorian with chromatic approach notes
  register: mid (D4-G5)
  contour: |
    Intro: no melody — just Rhodes breathing.
    Verse: short sighing phrases that end on the 9th, entering bar 9.
    Bridge: melody opens up — wider intervals, reaches G5 on bar 19,
    then slowly descends back to D4 by bar 24.
  phrases:
    structure: |
      Verse: 2-bar phrases with full bar of breath.
      Bridge: 4-bar phrases, more sustained, more emotional.
    breath: essential — the space is the soul
  density: zero (intro), sparse (verse), lyrical (bridge)

Rhythm:
  feel: heavy behind the beat — almost drunk, perfectly imperfect
  subdivision: 16th-note feel with heavy swing
  swing: 64%
  ghost_notes:
    instrument: snare
    velocity: 22-42
  hi_hat: loose, partially open, often late

Dynamics:
  overall: pp to mf
  arc:
    - bars: 1-4
      level: pp
      shape: flat — Rhodes alone, barely there
    - bars: 5-8
      level: mp
      shape: rhythm section enters, settling in
    - bars: 9-16
      level: mp
      shape: melody enters, conversation begins
    - bars: 17-20
      level: mp to mf
      shape: bridge lifts, emotional peak bar 19
    - bars: 21-24
      level: mf to mp
      shape: settling back, returning to Gm9 fog
  accent_velocity: 85
  ghost_velocity: 25

Orchestration:
  drums:
    kit: vintage acoustic, slightly compressed, tape-saturated
    kick: hits 1 and 3, sometimes anticipates beat 3
    snare: wide fat crack on 2 and 4
    hi_hat: sloppy, human — slightly open, always slightly late
    entry: bar 5
  bass:
    technique: finger style, melodic — the bass has opinions
    register: G1-D3
    articulation: mix of legato and staccato — follows groove instinct
    entry: bar 5
    bridge: most melodic passage — the bass sings bars 17-24
  keys:
    instrument: Rhodes electric piano
    voicing: rootless, voiced in mid register
    rhythm: lazy behind-beat comping
    tremolo: subtle, slow 3Hz
    entry: bar 1 — owns the intro
  guitar:
    technique: muted Wes Montgomery style — octaves
    pattern: upbeat fills only, never on the downbeat
    entry: bar 9
  melody:
    instrument: breathy synth pad, vocal quality
    register: D4-G5
    entry: bar 9

Effects:
  drums:
    saturation: tape emulation, warm — like it was recorded in 1999
    compression:
      type: program-dependent, slow
  keys:
    tremolo: subtle, slow 3Hz
    reverb: warm plate, 1.2s
  guitar:
    reverb: small room, 0.4s
  melody:
    reverb: warm hall, 1.8s, 25% wet
    chorus: very subtle, slow

Expression:
  arc: solitude to intimacy to yearning to acceptance
  narrative: |
    The Rhodes in the intro is 2am. You are sitting by the window. The
    drums arrive like a heartbeat you had forgotten. The bass starts a
    conversation you didn't know you needed. The melody at bar 9 is the
    thing you have been trying to say. The bridge at bar 17 is the moment
    you almost say it — the D7alt reaches, the melody opens up, the
    bass sings. But you don't say it. You return to Gm9. And somehow
    that is enough. The music makes it okay to want something you
    cannot have. That is neo-soul.
  spatial_image: |
    Intro: Rhodes center, intimate, reverberant. Verse: drums back-center,
    bass slightly right, Rhodes left, guitar far right.
    Bridge: melody center, bass comes forward, everything slightly closer.
  character: D'Angelo circa Voodoo. J Dilla's swing. Erykah Badu's
    patience. Humid. Slightly imprecise. Perfectly human.

Texture:
  density: very sparse (intro) to medium-sparse (verse) to medium (bridge)
  register_spread: G1-G5
  space: |
    The bass and melody have a conversation — everything else listens.
    The space between notes is where the soul lives.

Form:
  structure: intro-verse-bridge
  development:
    - section: intro (bars 1-4)
      intensity: pp — Rhodes alone, establishing the mood
    - section: verse (bars 5-16)
      variation: rhythm section enters bar 5, melody enters bar 9
    - section: bridge (bars 17-24)
      contrast: harmony shifts, melody opens up, emotional peak bar 19
  variation_strategy: |
    The intro is loneliness. The verse is company. The bridge is
    vulnerability. Each section lets you feel more, not hear more.

Humanization:
  timing:
    jitter: 0.07
    late_bias: 0.03
    grid: 16th
  velocity:
    arc: phrase
    stdev: 18
    accents:
      beats: [1, 3]
      strength: 4
    ghost_notes:
      probability: 0.12
      velocity: [20, 40]
  feel: heavy behind the beat — everything leans back, nothing rushes

MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches on Rhodes
    changes_per_bar: 3
  expression:
    curve: follows dynamic arc, pp to mf
    range: [35, 90]
  modulation:
    instrument: melody synth
    depth: slow vibrato onset — CC 1 from 0 to 35 after attack
    onset: delayed 1.5 beats
  pitch_bend:
    style: vocal-style scoops on melody notes — approach from below
    depth: quarter to half-tone
  cc_curves:
    - cc: 91
      from: 30
      to: 62
      position: bars 1-24
    - cc: 1
      from: 0
      to: 35
      position: bars 9-24
    - cc: 11
      from: 35
      to: 90
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds warmth and depth on sustained Rhodes chords
    use: filter opening and tremolo depth
  filter:
    cutoff:
      sweep: warm low-pass on bass — opens from 600hz to 2.5khz across verse
      resonance: moderate

Automation:
  - track: Rhodes
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
        curve: linear
      - beat: 48
        value: 0.5
        curve: smooth
      - beat: 96
        value: 0.35
        curve: smooth
  - track: Guitar
    param: tremolo_rate
    events:
      - beat: 0
        value: 2.0
      - beat: 48
        value: 3.5
        curve: smooth
      - beat: 96
        value: 2.5
        curve: smooth
""",
    ),

    # 15 ── Reggaeton dembow ─────────────────────────────────────────────────
    PromptItem(
        id="reggaeton_dembow",
        title="Reggaeton dembow \u00b7 Bbm \u00b7 96 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: reggaeton \u00b7 Key: Bbm \u00b7 96 BPM\nRole: drums, bass, synth chord, perc, vocal lead\nVibe: energetic x3, driving x2, dark, bouncy",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: chorus
Style: reggaeton
Key: Bbm
Tempo: 96
Energy: high
Role: [drums, bass, synth chord, perc, vocal lead]
Constraints:
  bars: 24
Vibe: [energetic x3, driving x2, dark, bouncy, aggressive, infectious]

Request: |
  A full reggaeton piece in three sections. 4-bar intro — dembow kick
  pattern alone with a filtered bass slide, building tension. 12-bar
  verse — full dembow rhythm, hi-hat on every 16th, kick on 1 and the
  and of 2, snare clap on 3, bass slides on Bbm. Synth stabs on
  Bbm-Ebm-Gb-F. Congas and shaker add Afro-Latin flavor. 8-bar chorus —
  a vocal-style lead enters with the hook, the synth stabs double, the
  bass pattern gets more aggressive, and the energy peaks for the main
  stage. Daddy Yankee's precision meets Bad Bunny's darkness.

Harmony:
  progression: [Bbm, Ebm7, Gbmaj7, F7]
  voicing: punchy stabs — root + 5th on bass, 3rd + 7th on synth
  rhythm: |
    Intro: bass slides only, no chords.
    Verse: synth stabs on beats 2 and 4 offbeats.
    Chorus: stab rhythm doubles, every offbeat, fuller voicings.
  extensions: 7ths on Ebm and F, add 9th on Gbmaj in chorus

Melody:
  scale: Bb minor pentatonic
  register: mid (Db4-Bb4)
  contour: |
    Intro/Verse: no melody — pure rhythm and bass.
    Chorus: short repeated melodic hook, 2-bar motif, Db4-F4-Bb4 ascend.
  phrases:
    structure: 2-bar phrase repeated 4x with slight variations in chorus
  density: zero (intro/verse), medium (chorus)

Rhythm:
  feel: right on the grid — reggaeton is quantized
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: dembow — kick on 1 and 2-and, snare clap on 3
    weight: snare clap is very loud, very present

Dynamics:
  overall: mp to ff
  arc:
    - bars: 1-4
      level: mp
      shape: filtered intro, tension building
    - bars: 5-16
      level: f
      shape: full dembow, constant driving energy
    - bars: 17-24
      level: f to ff
      shape: chorus peaks, vocal lead adds presence
  accent_velocity: 118
  ghost_velocity: 48

Orchestration:
  drums:
    kick: tight sub kick, dembow pattern, entry bar 1
    snare: loud rimshot clap on 3, entry bar 5
    hi_hat: 16th notes, tight and bright, entry bar 5
  bass:
    technique: sustained electronic bass with pitch slides
    register: Bb0-Bb1
    entry: bar 1 — filtered in intro, full from bar 5
    chorus: pattern more aggressive, adds offbeat 16th slides
  synth_chord:
    instrument: detuned saw synth
    voicing: punchy stabs
    rhythm: offbeat hits, doubles in chorus
    entry: bar 5
  perc:
    conga: 8th-note pattern, Latin feel, entry bar 5
    shaker: 16th notes straight, entry bar 5
  vocal_lead:
    style: melodic hook, rhythmic, percussive delivery
    register: Db4-Bb4
    entry: bar 17 — chorus only

Effects:
  drums:
    compression: hard limiting
    saturation: light
  bass:
    distortion: subtle saturation
    eq:
      - band: sub
        freq: 60hz
        gain: +4db
  synth_chord:
    reverb: short plate, 0.5s

Expression:
  arc: tension to groove to eruption
  narrative: |
    The intro is a fuse. The dembow kicks alone, the bass slides through
    darkness. When the full beat drops at bar 5, there is no choice —
    you move. The verse is a spell, the dembow is ancient and modern at
    once. When the chorus hits at bar 17, the vocal hook turns the groove
    into a weapon. Full stadium. Full moon. Nobody is standing still.
  spatial_image: |
    Intro: kick center, bass center, filtered. Verse: kick and bass
    center, synth stabs wide, perc left and right. Chorus: vocal lead
    center, everything wider and louder.
  character: Daddy Yankee's precision. Bad Bunny's darkness. The dembow
    is a spell cast on every body in the room.

Texture:
  density: sparse (intro) to high (verse) to maximum (chorus)
  register_spread: Bb0-Bb4

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-4)
      intensity: mp — dembow kick and filtered bass only, tension
    - section: verse (bars 5-16)
      variation: full dembow, synth stabs, percussion layers
    - section: chorus (bars 17-24)
      contrast: vocal lead enters, stabs double, bass more aggressive
  variation_strategy: |
    The intro is the fuse. The verse is the fire. The chorus is the
    explosion. Each section adds presence without adding complexity.

Humanization:
  timing:
    jitter: 0.01
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 6
    accents:
      beats: [2]
      strength: 18
    ghost_notes:
      probability: 0.04
      velocity: [42, 55]
  feel: on the grid — reggaeton is surgical

MidiExpressiveness:
  pitch_bend:
    style: bass slides between chord roots
    depth: 1-2 semitones
  cc_curves:
    - cc: 91
      from: 22
      to: 58
      position: bars 1-24
    - cc: 74
      from: 40
      to: 98
      position: bars 1-24
  expression:
    curve: rises across sections
    range: [80, 122]
  aftertouch:
    type: channel
    response: adds edge on synth lead sustains
    use: filter brightness and vibrato depth
  modulation:
    instrument: synth lead
    depth: aggressive vibrato — CC 1 from 0 to 55 on hook phrases
    onset: delayed 0.5 beats
  filter:
    cutoff:
      sweep: bass filter opens from 300hz to 3khz across intro to chorus
      resonance: moderate

Automation:
  - track: Bass
    param: filter_cutoff
    events:
      - beat: 0
        value: 400hz
      - beat: 16
        value: 4khz
        curve: exp
  - track: Synth_Chord
    param: volume
    events:
      - beat: 64
        value: 0.7
      - beat: 96
        value: 1.0
        curve: linear
""",
    ),

    # 18 ── Indie folk ballad ────────────────────────────────────────────────
    PromptItem(
        id="indie_folk_ballad",
        title="Indie folk ballad \u00b7 G \u00b7 70 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: indie folk \u00b7 Key: G \u00b7 70 BPM\nRole: acoustic guitar, piano, bass, melody\nVibe: intimate x3, melancholic x2, nostalgic, peaceful",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: indie folk
Key: G
Tempo: 70
Energy: very low
Role: [acoustic guitar, piano, bass, melody]
Constraints:
  bars: 24
Vibe: [intimate x3, melancholic x2, nostalgic, peaceful, personal, fragile]

Request: |
  A full indie folk ballad in three sections. 8-bar intro — fingerpicked
  acoustic guitar alone on Gmaj9-Em9, Travis picking pattern, each note
  placed like glass. No drums. No bass. Just the guitar and silence.
  8-bar verse — piano enters with sparse 9th chords on the downbeats,
  upright bass walks underneath so quietly you almost imagine it, and
  the guitar melody expands. 8-bar bridge — a breathy vocal-style
  melody enters in the upper register, the harmony shifts to Cmaj7-Dsus2,
  the piano gets slightly more present, and the piece reaches its quiet
  emotional peak before settling back to Gmaj9. This is for 2am
  listening. Elliott Smith's intimacy. Sufjan Stevens' space. Iron & Wine's warmth.

Harmony:
  progression: |
    Intro (1-8): [Gmaj9, Gmaj9, Em9, Em9, Gmaj9, Gmaj9, Em9, Em9]
    Verse (9-16): [Gmaj9, Em9, Cmaj7, Dsus2, Gmaj9, Em9, Cmaj7, Dsus2]
    Bridge (17-24): [Cmaj7, Cmaj7, Dsus2, Dsus2, Em9, Em9, Gmaj9, Gmaj9]
  voicing: open, airy — capo 2nd fret guitar sound, piano adds 9ths
  rhythm: guitar strums 6/8 feel in 4/4, piano hits beats 1 and 3 only
  extensions: 9ths and sus2s throughout — nothing fully resolved

Melody:
  scale: G major pentatonic with added 6th
  register: upper-mid (D4-G5)
  contour: |
    Intro: guitar melody embedded in the picking pattern, simple motif.
    Verse: melody expands, slightly wider intervals, still in guitar.
    Bridge: vocal-style melody enters, arching phrases, peaks on G5
    at bar 21, then settles back to D4.
  phrases:
    structure: 2-bar phrases with 2-bar breath
    breath: essential — the space IS the ballad
  density: very sparse — 4-6 notes per 2-bar phrase

Rhythm:
  feel: floating — barely any pulse
  subdivision: 8th notes with 6/8 flow
  swing: 54%
  accent:
    pattern: soft downbeats only, no other accents

Dynamics:
  overall: ppp to mp
  arc:
    - bars: 1-8
      level: ppp
      shape: flat — guitar alone, nearly silent
    - bars: 9-12
      level: pp
      shape: piano and bass enter, barely
    - bars: 13-16
      level: pp to p
      shape: verse settles into gentle groove
    - bars: 17-20
      level: p to mp
      shape: bridge lifts, melody enters
    - bars: 21-24
      level: mp to pp
      shape: peak at bar 21, then settling back to silence
  accent_velocity: 68
  ghost_velocity: 25

Orchestration:
  guitar:
    technique: fingerpicked — thumb + 3 fingers, Travis picking pattern
    voicing: full chord shapes, some open strings
    articulation: legato, let notes ring naturally
    entry: bar 1 — owns the entire piece
  piano:
    voicing: right hand — 9th chord in upper register
    rhythm: beats 1 and 3 only, very soft
    pedaling: half pedal throughout
    entry: bar 9
  bass:
    technique: upright feel, bowed occasionally
    register: G1-G2
    articulation: mostly sustained, occasional pizzicato
    entry: bar 9 — so quiet you almost imagine it
  melody:
    instrument: breathy vocal-style synth
    register: D4-G5
    entry: bar 17 — bridge only

Effects:
  guitar:
    reverb: small room, 0.8s, 8ms predelay
  piano:
    reverb: same room, gentle
  bass:
    reverb: very subtle — barely processed
  melody:
    reverb: same room, 1s, 20% wet
    chorus: very subtle, slow

Expression:
  arc: solitude to companionship to vulnerability to peace
  narrative: |
    The guitar in the intro is you alone at 2am. Every note chosen. The
    silence between notes is where the meaning lives. When the piano and
    bass enter at bar 9, it is like someone sitting down beside you
    without speaking. The melody at bar 17 is the thing you have been
    wanting to say. It peaks at bar 21 and then — instead of resolving
    triumphantly — it settles back to Gmaj9. Acceptance. Not fixing.
    Just staying. The music doesn't try to fix anything. It just stays.
  character: Elliott Smith's intimacy. Sufjan Stevens' space. Iron & Wine's
    warmth. Nick Drake's loneliness. The quiet that holds everything.
  spatial_image: |
    Intro: guitar center, intimate. Verse: guitar slightly left, piano
    right, bass center-back. Bridge: melody enters center, everything
    slightly closer, as if the room got smaller.

Texture:
  density: very sparse throughout — even at peak, mostly silence
  register_spread: G1-G5
  space:
    principle: |
      Every silence is load-bearing. The song exists in the spaces
      between the notes as much as in the notes themselves. This is
      a song made of air. The notes are just the walls.

Form:
  structure: intro-verse-bridge
  development:
    - section: intro (bars 1-8)
      intensity: ppp — guitar alone, Travis picking, silence
    - section: verse (bars 9-16)
      variation: piano and bass enter, guitar melody expands
    - section: bridge (bars 17-24)
      contrast: vocal melody enters, emotional peak bar 21, settles back
  variation_strategy: |
    Each section adds one element and one degree of vulnerability.
    Intro: the guitar is alone. Verse: it has company. Bridge: it
    speaks. The return to Gmaj9 at the end is not defeat — it is peace.

Humanization:
  timing:
    jitter: 0.06
    late_bias: 0.02
    grid: 8th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0]
      strength: 4
    ghost_notes:
      probability: 0.03
      velocity: [18, 30]
  feel: floating — barely any grid, maximum human imperfection

MidiExpressiveness:
  sustain_pedal:
    style: half pedal catches on piano — sustain chord tones
    changes_per_bar: 3
  expression:
    curve: follows dynamic arc, ppp to mp to pp
    range: [18, 68]
  pitch_bend:
    style: vocal slides on melody — up into phrases, down at ends
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 22
      to: 52
      position: bars 1-24
    - cc: 11
      from: 18
      to: 68
      position: bars 1-24
  articulation:
    legato: true
    soft_pedal: bars 1-8
  aftertouch:
    type: channel
    response: adds warmth on sustained piano notes
    use: gentle volume swell
  modulation:
    instrument: strings (melody synth)
    depth: slow vibrato — CC 1 from 0 to 25 on sustained notes
    onset: delayed 2 beats
  filter:
    cutoff:
      sweep: subtle low-pass warms bass — from 2khz to 5khz across bridge
      resonance: low

Automation:
  - track: Vocal_Melody
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
        curve: linear
      - beat: 32
        value: 0.35
        curve: smooth
      - beat: 96
        value: 0.25
        curve: smooth
  - track: Strings
    param: volume
    events:
      - beat: 0
        value: 0.0
      - beat: 32
        value: 0.4
        curve: smooth
      - beat: 64
        value: 0.7
        curve: smooth
      - beat: 96
        value: 0.5
        curve: smooth
""",
    ),

    # 19 ── New Orleans second line ──────────────────────────────────────────
    PromptItem(
        id="second_line_brass",
        title="New Orleans second line \u00b7 F \u00b7 98 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: New Orleans brass / second line \u00b7 Key: F \u00b7 98 BPM\nRole: drums, tuba, trumpet, trombone, sax\nVibe: joyful x4, groovy x2, bouncy, energetic",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: chorus
Style: New Orleans brass second line
Key: F
Tempo: 98
Energy: high
Role: [drums, tuba, trumpet, trombone, sax]
Constraints:
  bars: 24
Vibe: [joyful x4, groovy x2, bouncy, energetic, triumphant, communal]

Request: |
  A full New Orleans second line brass band piece in three sections.
  4-bar drum intro — the snare establishes the second-line shuffle
  (syncopated 16th notes) while the bass drum lays the parade pattern.
  Just the drums calling the band together. 12-bar verse — tuba enters
  with the walking bass line on F7-Bb7-C7, trumpet plays the melody,
  trombone fills between phrases, alto sax adds responses. Full call-
  and-response between trumpet and sax. 8-bar chorus — all horns in
  unison on the hook melody, the energy peaks, the crowd is one body.
  This is a funeral march that turned into a celebration. Pure NOLA joy.

Harmony:
  progression: [F7, Bb7, F7, C7, Bb7, F7, C7, F7]
  voicing: blues seventh chords throughout
  rhythm: |
    Intro: drums only — implied harmony.
    Verse: horn stabs on upbeats, tuba on all downbeats.
    Chorus: all horns play the melody in unison, harmonized 3rds.
  extensions: dominant 7ths — nothing fancy, pure blues

Melody:
  scale: F blues scale
  register: Trumpet — C4-C6
  contour: |
    Intro: no melody — drums call the band.
    Verse: bright ascending phrases, call-and-response trumpet/sax.
    Chorus: hook melody in unison, always ending on a high F5.
  phrases:
    structure: |
      Verse: 2-bar call (trumpet), 2-bar response (sax).
      Chorus: 4-bar unison hook, repeated.
  density: medium verse, high chorus

Rhythm:
  feel: ahead of the beat — New Orleans bounce, always leaning forward
  subdivision: 16th notes
  swing: 56%
  accent:
    pattern: second-line shuffle — syncopated snare, bass drum on 1
  ghost_notes:
    instrument: snare
    velocity: 38-58
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early — second-line push

Dynamics:
  overall: mf to ff
  arc:
    - bars: 1-4
      level: mf
      shape: drums alone, calling the band
    - bars: 5-16
      level: f
      shape: full band, constant joy
    - bars: 17-24
      level: f to ff
      shape: chorus peaks, all horns unison
  accent_velocity: 115
  ghost_velocity: 42

Orchestration:
  drums:
    snare: second-line shuffle — syncopated 16th pattern
    bass_drum: on 1, 2 and 4 (parade pattern)
    cymbals: crash on downbeats, ride throughout
    entry: bar 1 — calls the band together
  tuba:
    register: F0-F2
    technique: walking bass line, every quarter note
    entry: bar 5
  trumpet:
    role: melody and high fills
    register: Bb3-F5
    articulation: bright, clear tone — no mutes
    entry: bar 5
  trombone:
    role: countermelody and riffs between trumpet phrases
    register: Bb1-F4
    articulation: slide portamento between notes
    entry: bar 5
  alto_sax:
    role: response to trumpet calls in verse, unison in chorus
    register: Bb3-Bb4
    articulation: bright, bluesy
    entry: bar 7 — enters 2 bars after trumpet for call-response

Effects:
  brass:
    reverb: outdoor street reverb — medium hall, 1.2s
    compression: gentle, preserve dynamics
  drums:
    compression: light, preserve the snare crack

Expression:
  arc: call to celebration to communion
  narrative: |
    The drums at bar 1 are a call — "come, gather, we are going to
    celebrate." When the tuba and trumpet enter at bar 5, the parade
    has begun. Every 2 bars the trumpet calls and the sax answers —
    this is a conversation between joy and more joy. By the chorus at
    bar 17, every horn plays the same melody. No more conversation.
    Just communion. Everyone on the street is family right now. The
    music doesn't know about sadness today. Follow the tuba. Let
    your feet figure it out. This is New Orleans. This is alive.
  character: Rebirth Brass Band's fire. Trombone Shorty's energy.
    The Preservation Hall Jazz Band's tradition. Sunday afternoon
    in the Trem\u00e9.
  spatial_image: |
    Intro: drums center. Verse: drums center, tuba center, trumpet
    right, trombone left, sax right. Chorus: all horns center and
    forward, drums behind, wall of brass.

Texture:
  density: medium (intro) to high (verse) to maximum (chorus)
  register_spread: F0-C6
  space: every instrument has its lane — this is disciplined joy

Form:
  structure: drum_call-verse-chorus
  development:
    - section: drum_call (bars 1-4)
      intensity: mf — drums alone, calling the band together
    - section: verse (bars 5-16)
      variation: full band, trumpet/sax call-and-response
    - section: chorus (bars 17-24)
      contrast: all horns unison, peak energy, communal joy
  variation_strategy: |
    The drum call is an invitation. The verse is a conversation.
    The chorus is a congregation. Each section adds voices until
    everyone is singing the same song.

Humanization:
  timing:
    jitter: 0.04
    late_bias: -0.01
    grid: 16th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.1
      velocity: [35, 55]
  feel: ahead of the beat — NOLA bounce leans forward

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [78, 118]
  pitch_bend:
    style: trombone slides between notes — half-tone portamento
    depth: 1 semitone
  cc_curves:
    - cc: 91
      from: 22
      to: 52
      position: bars 1-24
    - cc: 11
      from: 78
      to: 118
      position: bars 1-24
  articulation:
    portamento:
      time: 35
      switch: on
  aftertouch:
    type: channel
    response: adds brightness on high notes
    use: expression boost
  modulation:
    instrument: trumpet
    depth: warm vibrato — CC 1 from 0 to 50 on sustained melody notes
    onset: delayed 1 beat
  breath_control:
    instrument: trumpet
    mapping: filter + volume — CC 2 shapes phrase dynamics and brightness
  filter:
    cutoff:
      sweep: tuba low-pass warms from 800hz to 2khz across chorus
      resonance: low

Automation:
  - track: Brass
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
        curve: linear
      - beat: 48
        value: 0.4
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
  - track: Second_Line_Drums
    param: pan
    events:
      - beat: 0
        value: -0.2
      - beat: 48
        value: 0.2
        curve: smooth
      - beat: 96
        value: -0.2
        curve: smooth
""",
    ),

    # 33 ── Colombian cumbia ───────────────────────────────────────────────
    PromptItem(
        id="colombian_cumbia",
        title="Colombian cumbia \u00b7 C \u00b7 90 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Colombian cumbia \u00b7 Key: C \u00b7 90 BPM\nRole: accordion, gaita, tumbadora, guacharaca, bass\nVibe: tropical x3, joyful x2, groovy, earthy, celebratory",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Colombian cumbia
Key: C
Tempo: 90
Energy: medium
Role: [accordion, gaita, tumbadora, guacharaca, bass]
Constraints:
  bars: 24
Vibe: [tropical x3, joyful x2, groovy, earthy, celebratory, carnival, ancestral]

Request: |
  A full Colombian cumbia piece in three sections. 8-bar llamada —
  the gaita (GM 73 flute, representing the indigenous flute) plays a
  simple pentatonic melody alone over the guacharaca (GM 0 drums,
  channel 10, scraping shaker) establishing the cumbia shuffle rhythm.
  8-bar verse — the accordion (GM 21) enters with bright major chord
  arpeggios, the tumbadora (GM 0 drums, conga pattern) adds the
  driving beat, the bass (GM 33) walks in a 2-feel, and the gaita
  shifts to ornamented melodic runs. The cumbia groove is irresistible:
  the guacharaca scrapes on every 8th note while the tumbadora plays
  the traditional tumba'o pattern. 8-bar chorus — the accordion takes
  the melody with a rapid vallenato-style run, the gaita harmonizes a
  3rd above, the bass doubles its energy, and all percussion intensifies.
  The Caribbean coast at sunset. Candles and white dresses.
  Cumbia is prayer made dance.

Harmony:
  progression: |
    Llamada (1-8): [C, C, F, G, C, C, F, G]
    Verse (9-16): [C, F, G, C, Am, F, G, C]
    Chorus (17-24): [C, F, Am, G, C, F, G, C]
  voicing: accordion arpeggios, bright major voicings
  rhythm: 2-feel, cumbia shuffle
  extensions: none — cumbia harmony is simple and bright

Melody:
  scale: C major pentatonic
  register: gaita C4-C6, accordion C3-C6
  contour: |
    Llamada: gaita pentatonic melody, simple, calling.
    Verse: gaita ornaments, accordion arpeggios.
    Chorus: accordion vallenato runs, gaita harmonizes 3rd above.
  density: sparse (llamada) to medium-high (chorus)

Dynamics:
  overall: mp to f
  arc:
    - bars: 1-8
      level: mp
      shape: gaita and guacharaca, gentle call
    - bars: 9-16
      level: mf
      shape: full ensemble, groove established
    - bars: 17-24
      level: f
      shape: chorus, accordion leads, peak energy
  accent_velocity: 105
  ghost_velocity: 42

Rhythm:
  feel: cumbia shuffle — a circular, swaying 2-feel that is neither
    straight nor swung but something older than both
  subdivision: 8th notes
  swing: 56%
  accent:
    pattern: |
      Guacharaca: continuous scraping 8ths, accent on 1 and the and-of-2.
      Tumbadora: traditional tumba'o — open tone on the and-of-2, slap
      on 4, bass tone on 1. This is the African heart of cumbia.
      Bass: roots on 1 and 3, 5ths on 2 and 4 — the 2-feel walk.
  ghost_notes:
    instrument: tumbadora
    velocity: 32-50

Orchestration:
  accordion:
    instrument: accordion (GM 21)
    technique: |
      Verse: bright chord arpeggios.
      Chorus: vallenato-style rapid runs, lead melody.
    entry: bar 9
  gaita:
    instrument: flute (GM 73)
    technique: |
      Llamada: simple pentatonic calling melody.
      Verse: ornamental runs.
      Chorus: harmonizes 3rd above accordion.
    register: C4-C6
    entry: bar 1
  tumbadora:
    instrument: drums (GM 0, channel 10)
    technique: traditional tumba'o conga pattern
    entry: bar 9
  guacharaca:
    instrument: drums (GM 0, channel 10)
    technique: scraping 8th-note pattern, cumbia shuffle
    entry: bar 1
  bass:
    instrument: fingered bass (GM 33)
    technique: walking 2-feel, roots and 5ths
    register: C1-C3
    entry: bar 9

Effects:
  gaita:
    reverb: outdoor Caribbean night, 1s, bright natural
    eq: slight air at 6khz — the breathy attack is sacred
  accordion:
    reverb: same outdoor space, 0.8s
    compression: gentle — preserve bellows dynamics
  tumbadora:
    reverb: very short, 0.3s — congas need to be dry and present
  bass:
    eq:
      - band: low_mid
        freq: 200hz
        gain: +2db

Expression:
  arc: call to groove to celebration
  narrative: |
    The gaita at bar 1 is indigenous Colombia calling. The cumbia was
    born where three worlds met: indigenous, African, and Spanish. The
    guacharaca's shuffle is African. The gaita's melody is indigenous.
    The accordion at bar 9 is Spanish. Together they create the cumbia
    groove — a dance that is also a prayer, performed with candles in
    white dresses on the Caribbean coast. The chorus at bar 17 is the
    celebration: the accordion runs like vallenato fire, the gaita
    soars, and for a moment the three worlds are one world.
  character: Lucho Bermudez's elegance. Tot\u00f3 la Momposina's roots.
    The Festival de la Leyenda Vallenata. Barranquilla carnival.

Texture:
  density: sparse (llamada) to medium-high (chorus)
  register_spread: C1-C6
  space:
    principle: |
      Cumbia breathes. The guacharaca is the constant — a rhythmic
      river that everything else floats on. The gaita is the sky.
      The accordion is the sun. The tumbadora is the earth. Even at
      peak density in the chorus, each instrument has room because
      each occupies a different element.

Form:
  structure: llamada-verse-chorus
  development:
    - section: llamada (bars 1-8)
      intensity: mp — gaita and guacharaca, indigenous call
    - section: verse (bars 9-16)
      variation: full ensemble, cumbia groove established
    - section: chorus (bars 17-24)
      contrast: accordion leads, peak energy, celebration

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.06
      velocity: [35, 52]
  feel: cumbia shuffle — relaxed, tropical, behind the beat

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [62, 108]
  cc_curves:
    - cc: 91
      from: 22
      to: 48
      position: bars 1-24
    - cc: 11
      from: 62
      to: 108
      position: bars 1-24
  pitch_bend:
    style: gaita ornamental slides
    depth: quarter-tone
  aftertouch:
    type: channel
    response: adds bellows expression on accordion sustains
    use: volume swell and brightness
  modulation:
    instrument: gaita
    depth: breathy vibrato — CC 1 from 0 to 40 on sustained melody notes
    onset: delayed 1 beat
  breath_control:
    instrument: gaita
    mapping: filter + volume — CC 2 shapes airflow dynamics and tone color
  filter:
    cutoff:
      sweep: bass low-pass opens from 500hz to 2khz across verse to chorus
      resonance: low

Automation:
  - track: Gaita
    param: reverb_wet
    events:
      - beat: 0
        value: 0.25
        curve: linear
      - beat: 48
        value: 0.45
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
  - track: Guacharaca
    param: pan
    events:
      - beat: 0
        value: 0.3
      - beat: 48
        value: -0.3
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
""",
    ),

    # 34 ── Argentine tango nuevo ──────────────────────────────────────────
    PromptItem(
        id="tango_nuevo",
        title="Tango nuevo \u00b7 Dm \u00b7 66 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: tango nuevo \u00b7 Key: Dm \u00b7 66 BPM\nRole: bandoneon, piano, violin, bass\nVibe: passionate x3, melancholic x2, dramatic, tense, Buenos Aires",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: tango nuevo
Key: Dm
Tempo: 66
Energy: medium
Role: [bandoneon, piano, violin, bass]
Constraints:
  bars: 24
Vibe: [passionate x3, melancholic x2, dramatic, tense, Buenos Aires, nocturnal, elegant]

Request: |
  A tango nuevo piece in three sections, Piazzolla-inspired. 8-bar
  intro — the bandoneon (GM 23 tango accordion) alone, playing a slow
  rubato melody in Dm with the characteristic tango breathing (bellows
  accents), minor 2nd intervals creating tension. The piano (GM 0)
  enters at bar 5 with sparse marcato chords. 8-bar milonga — the
  violin (GM 40) enters with a passionate countermelody, the bass
  (GM 32) drives with the milonga rhythm (habanera-derived: dotted
  8th + 16th + quarter + quarter), the piano shifts to the marcato
  staccato pattern, and the bandoneon plays rhythmic stabs between
  melodic phrases. Full tango orquesta energy. 8-bar variación —
  the violin takes the lead with a virtuosic solo, the bandoneon
  sustains dramatic chords, the piano plays a fugato bass line, and
  at bar 23 all four instruments converge on a unison D4 with a
  dramatic ritardando into the final Dm chord. Buenos Aires at
  midnight. Smoke and candlelight. The dance continues.

Harmony:
  progression: |
    Intro (1-8): [Dm, Dm, Gm, A7, Dm, Gm, A7, Dm]
    Milonga (9-16): [Dm, Gm, A7, Dm, Bb, Gm, A7, Dm]
    Variacion (17-24): [Dm, Gm, C, F, Bb, Gm, A7, Dm]
  voicing: close-voiced minor chords, dramatic suspensions
  rhythm: |
    Intro: rubato, bandoneon breathing.
    Milonga: habanera-derived groove.
    Variacion: driving, fugato bass.
  extensions: dom7 on A, maj7 on Bb and F for color

Melody:
  scale: D harmonic minor (D-E-F-G-A-Bb-C#-D)
  register: bandoneon D3-D5, violin G3-E6
  contour: |
    Intro: rubato, minor 2nds, breathing bellows accents.
    Milonga: bandoneon rhythmic stabs, violin countermelody.
    Variacion: violin virtuosic solo, dramatic ascent.
  density: sparse (intro) to dense (variación)
  ornamentation:
    - bandoneon bellows accents (sfz)
    - violin portamento between phrases
    - piano marcato staccato

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-4
      level: pp
      shape: bandoneon alone, rubato
    - bars: 5-8
      level: mp
      shape: piano enters, tension builds
    - bars: 9-16
      level: f
      shape: milonga, full orquesta, driving
    - bars: 17-22
      level: f to ff
      shape: variación, violin solo peaks
    - bars: 23-24
      level: ff to fff
      shape: convergence, ritardando, final Dm chord
  accent_velocity: 118
  ghost_velocity: 35

Rhythm:
  feel: |
    Intro: free, rubato — the bandoneon's bellows dictate time, not
    a metronome. Phrases accelerate and decelerate like breath.
    Milonga: habanera-derived pattern — dotted 8th + 16th + quarter +
    quarter. This is the DNA of tango: a limping walk that is somehow
    the most elegant rhythm on earth.
    Variaci\u00f3n: fugato drive, the habanera pattern intensifies, then
    the ritardando at bar 23 suspends time completely.
  subdivision: 8th notes with habanera dotted feel
  swing: 52%
  accent:
    pattern: |
      The marcato: piano staccato on every beat, accenting 1 and 3.
      The s\u00edncopa: bass anticipates beat 1 by a 16th — this tiny
      anticipation is the secret engine of tango's forward motion.
      Bandoneon stabs land on offbeats, creating rhythmic argument
      with the piano's on-beat marcato.

Orchestration:
  bandoneon:
    instrument: tango accordion (GM 23)
    technique: |
      Intro: rubato melody, bellows breathing.
      Milonga: rhythmic stabs.
      Variacion: dramatic sustained chords.
    entry: bar 1
  piano:
    instrument: acoustic grand (GM 0)
    technique: |
      Intro: sparse marcato chords.
      Milonga: marcato staccato pattern.
      Variacion: fugato bass line.
    entry: bar 5
  violin:
    instrument: violin (GM 40)
    technique: |
      Milonga: passionate countermelody.
      Variacion: virtuosic solo, portamento.
    register: G3-E6
    entry: bar 9
  bass:
    instrument: acoustic bass (GM 32)
    technique: milonga rhythm — habanera-derived pattern
    register: D1-D3
    entry: bar 9

Effects:
  bandoneon:
    reverb: Buenos Aires milonga hall, 1.5s
    compression: none — the bellows dynamics ARE the expression
  violin:
    reverb: same hall, slightly wet — portamento trails
  piano:
    reverb: same hall, dry and present — marcato needs attack
  bass:
    reverb: tight, 0.4s — the habanera must be felt, not blurred

Expression:
  arc: solitude to passion to drama to convergence
  narrative: |
    The bandoneon at bar 1 breathes. You hear the bellows — the
    instrument is literally inhaling and exhaling. Each minor 2nd is a
    knife edge of tension. When the piano enters at bar 5, the tango
    begins: two instruments, already arguing, already in love. The
    milonga at bar 9 is Buenos Aires at its most alive — the violin
    enters with a countermelody that is both embrace and resistance,
    the bass drives the habanera rhythm that makes hips move, and the
    bandoneon shifts from melody to percussive stabs. The variaci\u00f3n
    at bar 17 is Piazzolla's revolution: the violin plays like it is
    singing its last song, the piano's fugato is Bach transported to
    San Telmo, and when all four voices converge on D4 at bar 23
    with a dramatic ritardando — that is the tango. Two becoming one.
    Then separating. Then the final chord. Smoke. Candlelight. Midnight.
  character: Piazzolla's revolution. Pugliese's darkness. Gardel's
    melody. A milonga in San Telmo. The Riachuelo at dawn.

Texture:
  density: sparse (intro) to dense (variaci\u00f3n) to singular (final chord)
  register_spread: D1-E6
  space:
    principle: |
      Tango is a conversation between two people who cannot decide
      whether they are fighting or making love. The bandoneon and
      piano argue rhythmically — stabs vs. marcato, offbeat vs. on.
      The violin and bass argue melodically — ascending countermelody
      vs. descending habanera. The spaces between their phrases are
      where the dance happens. The ritardando at bar 23 is the moment
      before the kiss. The final chord is the kiss.

Form:
  structure: intro-milonga-variacion
  development:
    - section: intro (bars 1-8)
      intensity: pp — bandoneon alone, then piano, rubato
    - section: milonga (bars 9-16)
      variation: full orquesta, driving groove, passionate
    - section: variacion (bars 17-24)
      contrast: violin solo, fugato, convergence, dramatic end

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.015
    grid: 8th
  velocity:
    arc: phrase
    stdev: 18
    accents:
      beats: [0]
      strength: 16
    ghost_notes:
      probability: 0.03
      velocity: [28, 42]
  feel: tango rubato — dramatic timing, never metronomic

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [22, 125]
  pitch_bend:
    style: violin portamento, bandoneon bellows bends
    depth: half-tone
  cc_curves:
    - cc: 91
      from: 25
      to: 52
      position: bars 1-24
    - cc: 11
      from: 22
      to: 125
      position: bars 1-24
    - cc: 1
      from: 25
      to: 70
      position: bars 1-24
  articulation:
    legato: true
    portamento:
      time: 30
      switch: on
  aftertouch:
    type: channel
    response: adds dramatic weight on sustained piano chords
    use: volume swell and filter darkening
  modulation:
    instrument: violin
    depth: passionate vibrato — CC 1 from 25 to 70 on sustained phrases
    onset: immediate
  breath_control:
    instrument: bandoneon
    mapping: bellows expression — CC 2 controls dynamics and tonal color
  filter:
    cutoff:
      sweep: bass darkens from 2khz to 800hz in variación, then opens
      resonance: moderate

Automation:
  - track: Strings
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
        curve: linear
      - beat: 32
        value: 0.45
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
  - track: Bandoneon
    param: expression
    events:
      - beat: 0
        value: 0.5
      - beat: 32
        value: 0.8
        curve: smooth
      - beat: 64
        value: 1.0
        curve: smooth
      - beat: 88
        value: 0.6
        curve: smooth
""",
    ),

    # 35 ── Andean huayno ──────────────────────────────────────────────────
    PromptItem(
        id="andean_huayno",
        title="Andean huayno \u00b7 Em \u00b7 100 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Andean huayno \u00b7 Key: Em \u00b7 100 BPM\nRole: quena, charango, bombo, zampo\u00f1a\nVibe: spiritual x3, earthy x2, melancholic, high-altitude, ancestral",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Andean huayno
Key: Em
Tempo: 100
Energy: medium
Role: [quena, charango, bombo, zampona]
Constraints:
  bars: 24
Vibe: [spiritual x3, earthy x2, melancholic, high-altitude, ancestral, Andes, prayer]

Request: |
  An Andean huayno piece in three sections. 8-bar intro — the quena
  (GM 74 recorder/flute) alone plays a melody in E minor pentatonic
  (E-G-A-B-D), each note sustained and breathy, echoing off mountain
  walls. 8-bar verse — the charango (GM 105 banjo, representing the
  small Andean stringed instrument) enters with rapid tremolo strumming
  on Em-Am-B7, the bombo (GM 116 taiko, representing the large Andean
  drum) establishes the huayno bounce rhythm (strong beat 1, syncopated
  beat 2), and the quena plays ornamental runs. 8-bar chorus — the
  zampo\u00f1a (GM 75 pan flute) enters with a harmony a 3rd above the
  quena, both playing the melody together, the charango intensifies,
  and the bombo drives. The two flutes together are the sound of the
  Andes. Machu Picchu at dawn. The condor is flying. The earth is
  singing.

Harmony:
  progression: |
    Intro (1-8): [Em, Em, Am, Am, B7, B7, Em, Em]
    Verse (9-16): [Em, Am, B7, Em, Em, Am, B7, Em]
    Chorus (17-24): [Em, Am, B7, Em, C, Am, B7, Em]
  voicing: charango tremolo on full chords
  rhythm: huayno bounce — strong 1, syncopated 2

Melody:
  scale: E minor pentatonic (E-G-A-B-D)
  register: quena E4-E6, zampo\u00f1a E4-E6
  contour: |
    Intro: sustained quena melody, echoing.
    Verse: ornamental quena runs, charango tremolo.
    Chorus: quena and zampo\u00f1a in 3rds, together.
  density: sparse (intro) to medium (chorus)

Dynamics:
  overall: pp to f
  arc:
    - bars: 1-8
      level: pp to mp
      shape: quena alone, mountain silence
    - bars: 9-16
      level: mf
      shape: full ensemble, huayno groove
    - bars: 17-24
      level: f
      shape: dual flutes, peak, spiritual
  accent_velocity: 98
  ghost_velocity: 38

Rhythm:
  feel: huayno bounce — a characteristic syncopation on beat 2 that
    gives the rhythm its earthy, walking quality. People who live at
    4,000 meters walk differently. This rhythm is that walk.
  subdivision: 8th notes
  swing: 54%
  accent:
    pattern: |
      Bombo: strong hit on 1 (the foot lands), syncopated hit on the
      and-of-2 (the weight shifts). This is the huayno DNA — the
      bounce between these two hits is the dance.
      Charango: tremolo strumming fills the space between bombo hits,
      creating a shimmering blanket of sound.

Orchestration:
  quena:
    instrument: recorder (GM 74)
    technique: breathy sustained notes (intro), ornamental runs (verse)
    register: E4-E6
    entry: bar 1
  charango:
    instrument: banjo (GM 105)
    technique: rapid tremolo strumming, full chords
    entry: bar 9
  bombo:
    instrument: taiko drum (GM 116)
    technique: huayno bounce — strong beat 1, syncopated beat 2
    entry: bar 9
  zampona:
    instrument: pan flute (GM 75)
    technique: harmony 3rd above quena, breathy
    register: E4-E6
    entry: bar 17

Effects:
  quena:
    reverb: mountain valley, very long natural decay, 4s — the echo
      off stone walls is part of the instrument's sound
    eq: no processing — the breath noise is the soul of the quena
  charango:
    reverb: same valley, shorter 1.2s — tremolo needs clarity
  bombo:
    reverb: short, 0.5s — earth sounds, not sky sounds
  zampona:
    reverb: same valley as quena, 4s — the two flutes share the echo

Expression:
  arc: solitude to community to prayer
  narrative: |
    The quena at bar 1 is a voice from 4,000 meters. The air is thin
    and the notes carry farther than they should. Each phrase echoes
    off mountain walls that have been there for millions of years.
    When the charango and bombo enter at bar 9, the mountain has a
    heartbeat — the huayno bounce is the walk of people who live at
    altitude, steady and syncopated. The zampo\u00f1a at bar 17 joins the
    quena in 3rds and the sound becomes prayer — two flutes, two
    breaths, one melody. Machu Picchu. The condor. The earth. The sky
    is very close here.
  character: Los Kjarkas's soul. Inti-Illimani's fire. Victor Jara's
    heart. The Altiplano. Pachamama. Music older than memory.

Texture:
  density: very sparse (intro) to medium (chorus)
  register_spread: E1-E6
  space:
    principle: |
      At 4,000 meters, sound travels differently. The thin air makes
      each note carry farther and last longer. The quena's breath
      echoes off stone that is millions of years old. The charango's
      tremolo fills space the way sunlight fills a valley. The bombo
      is the earth itself. When the zampo\u00f1a joins the quena in 3rds,
      two breaths become one prayer, and the mountain listens.

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-8)
      intensity: pp — quena alone, mountain silence
    - section: verse (bars 9-16)
      variation: charango and bombo enter, huayno groove
    - section: chorus (bars 17-24)
      contrast: zampo\u00f1a joins quena in 3rds, spiritual peak

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 12
    accents:
      beats: [0]
      strength: 8
    ghost_notes:
      probability: 0.04
      velocity: [30, 45]
  feel: huayno bounce — earthy, rooted, high-altitude breathing

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [28, 100]
  pitch_bend:
    style: quena breath bends — approaching notes from below
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 35
      to: 58
      position: bars 1-24
    - cc: 11
      from: 28
      to: 100
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds resonance on charango tremolo sustains
    use: brightness and volume swell
  modulation:
    instrument: quena
    depth: breathy vibrato — CC 1 from 0 to 45 on sustained notes
    onset: delayed 1 beat
  breath_control:
    instrument: quena
    mapping: airflow dynamics — CC 2 controls volume and breathiness
  filter:
    cutoff:
      sweep: bass low-pass opens from 400hz to 1.8khz across verse to chorus
      resonance: low

Automation:
  - track: Quena
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
        curve: linear
      - beat: 32
        value: 0.5
        curve: smooth
      - beat: 96
        value: 0.35
        curve: smooth
  - track: Charango
    param: pan
    events:
      - beat: 0
        value: 0.2
      - beat: 48
        value: -0.2
        curve: smooth
      - beat: 96
        value: 0.2
        curve: smooth
""",
    ),

    # 36 ── Jamaican dancehall ─────────────────────────────────────────────
    PromptItem(
        id="jamaican_dancehall",
        title="Jamaican dancehall \u00b7 F#m \u00b7 90 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Jamaican dancehall \u00b7 Key: F#m \u00b7 90 BPM\nRole: drums, bass, synth, organ bubble\nVibe: dark x3, bouncy x2, driving, bass-heavy, yard",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Jamaican dancehall
Key: F#m
Tempo: 90
Energy: high
Role: [drums, bass, synth, organ bubble]
Constraints:
  bars: 24
Vibe: [dark x3, bouncy x2, driving, bass-heavy, yard, midnight, Kingston]

Request: |
  A Jamaican dancehall riddim in three sections. 8-bar intro — the
  bass (GM 38 synth bass) enters alone with a heavy sub-bass pattern
  in F#m, pitch-sliding between notes, the digital riddim kick pattern
  (one-drop on beat 3), and a filtered organ bubble (GM 16 drawbar
  organ) playing offbeat skanks. 8-bar verse — the full drum pattern
  arrives (kick on 3, snare rim on 2 and 4, hi-hat 16ths), a dark
  synth stab (GM 81 square lead) plays minor chord hits on F#m-Bm-C#7,
  and the bass pattern doubles in complexity with 16th-note fills
  between the main hits. The dancehall bounce. 8-bar chorus — the
  synth melody enters with a dark minor hook, the organ bubble gets
  louder and wider, the bass hits its heaviest sub drops, and the
  riddim reaches peak energy. Kingston at midnight. Bass you feel in
  your chest. The yard is alive.

Harmony:
  progression: |
    Intro (1-8): [F#m, F#m, Bm, C#7, F#m, F#m, Bm, C#7]
    Verse (9-16): [F#m, Bm, C#7, F#m, F#m, Bm, C#7, F#m]
    Chorus (17-24): [F#m, Bm, E, C#7, F#m, Bm, C#7, F#m]
  voicing: synth stabs, minor chords, dark
  rhythm: one-drop (kick on 3), organ offbeat skanks

Melody:
  scale: F# minor pentatonic
  register: synth F#4-F#5
  contour: |
    Intro/Verse: no melody — pure riddim.
    Chorus: dark minor hook, short repeated motif.
  density: zero (intro/verse) to medium (chorus)

Dynamics:
  overall: f to ff
  arc:
    - bars: 1-8
      level: f
      shape: bass and organ intro, filtered
    - bars: 9-16
      level: f
      shape: full riddim, constant groove
    - bars: 17-24
      level: f to ff
      shape: chorus melody, peak bass drops
  accent_velocity: 112
  ghost_velocity: 45

Rhythm:
  feel: one-drop riddim — the kick is absent from beat 1, which is
    the defining inversion of dancehall. Beat 3 is where the kick
    lands, creating a gravitational center that is always in the
    wrong place and always in the right place.
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      One-drop: kick on beat 3 only. The ABSENCE of kick on 1 is the
      groove — it creates a vacuum that the bass fills with sub energy.
      Snare rim: on 2 and 4, tight and percussive.
      Hi-hat: continuous 16ths, mechanical, relentless.
      Organ bubble: offbeat skanks on every and — the reggae DNA that
      dancehall inherited and never abandoned.

Orchestration:
  drums:
    kick: one-drop on beat 3
    snare: rim shot on 2 and 4
    hi_hat: 16th notes, tight
    entry: bar 1 (kick only), bar 9 (full kit)
  bass:
    instrument: synth bass (GM 38)
    technique: heavy sub-bass, pitch slides between notes
    register: F#0-F#2
    entry: bar 1
  synth:
    instrument: square lead (GM 81)
    technique: |
      Verse: dark minor chord stabs.
      Chorus: minor hook melody.
    entry: bar 9 (stabs), bar 17 (melody)
  organ_bubble:
    instrument: drawbar organ (GM 16)
    technique: offbeat skanks, filtered in intro, opens later
    entry: bar 1

Effects:
  drums:
    compression: hard limiting — dancehall drums are punched through
    saturation: subtle digital edge
  bass:
    eq:
      - band: sub
        freq: 40hz
        gain: +6db
      - band: mid
        freq: 800hz
        gain: -4db
    distortion: very subtle — warmth, not grit
  synth:
    reverb: short plate, 0.3s — stabs need to be dry and aggressive
  organ_bubble:
    filter: lowpass in intro (1khz), opens to full bandwidth by verse
    reverb: medium room, 0.6s — the bubble needs space to breathe

Expression:
  arc: tension to groove to bass eruption
  narrative: |
    The bass at bar 1 is the first thing you feel — not hear, feel.
    F#m sub-bass that lives in your ribcage. The one-drop kick on
    beat 3 is the heartbeat of Jamaica. The organ bubble is Skatalites
    DNA, the offbeat skank that invented reggae and lives on in
    dancehall. When the full riddim hits at bar 9, the yard comes
    alive. The chorus at bar 17 adds the hook, but the hook is almost
    beside the point — the riddim is the point. The bass drops. The
    concrete vibrates. Kingston at midnight.
  character: Dave Kelly's riddims. Steely & Clevie's bass science.
    Shabba Ranks's energy. Vybz Kartel's darkness. The yard.

Texture:
  density: medium throughout — dancehall is never cluttered, every
    element earns its place in the frequency spectrum
  register_spread: F#0-F#5
  space:
    principle: |
      Dancehall production is subtractive. The one-drop leaves beat 1
      empty — that emptiness is the groove. The bass occupies 30-80hz
      and nothing else goes there. The organ bubble lives in the mids.
      The synth stabs live in the upper mids. The hi-hat is the ceiling.
      Each element owns its frequency band absolutely. The result is
      maximum impact with minimum clutter. Less is more. The riddim
      is the point.

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-8)
      intensity: f — bass and organ, filtered, tension
    - section: verse (bars 9-16)
      variation: full riddim, synth stabs, groove
    - section: chorus (bars 17-24)
      contrast: melody enters, bass heaviest, peak

Humanization:
  timing:
    jitter: 0.015
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 8
    accents:
      beats: [2]
      strength: 14
    ghost_notes:
      probability: 0.04
      velocity: [38, 52]
  feel: on the grid — dancehall riddims are precise

MidiExpressiveness:
  expression:
    curve: constant
    range: [85, 115]
  pitch_bend:
    style: bass pitch slides between notes
    depth: 2-4 semitones
  cc_curves:
    - cc: 91
      from: 18
      to: 38
      position: bars 1-24
    - cc: 74
      from: 35
      to: 85
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds grit on synth stab sustains
    use: filter edge and distortion depth
  modulation:
    instrument: pad
    depth: dark vibrato — CC 1 from 0 to 35 on ambient pad
    onset: delayed 1 beat

Automation:
  - track: Bass
    param: filter_cutoff
    events:
      - beat: 0
        value: 400hz
        curve: linear
      - beat: 32
        value: 2khz
        curve: exp
      - beat: 64
        value: 3khz
        curve: smooth
  - track: Organ
    param: tremolo_rate
    events:
      - beat: 0
        value: 0.3
      - beat: 64
        value: 0.5
        curve: smooth
  - track: Master
    param: highpass
    events:
      - beat: 0
        value: 200hz
      - beat: 32
        value: 20hz
        curve: exp
      - beat: 96
        value: 20hz
""",
    ),

    # 37 ── Trinidad calypso/soca ──────────────────────────────────────────
    PromptItem(
        id="trinidad_calypso_soca",
        title="Trinidad calypso/soca \u00b7 Bb \u00b7 128 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: soca / calypso \u00b7 Key: Bb \u00b7 128 BPM\nRole: steel drums, bass, brass, drums\nVibe: joyful x4, bright x2, carnival, sunshine, dancing",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: soca calypso
Key: Bb
Tempo: 128
Energy: high
Role: [steel drums, bass, brass, drums]
Constraints:
  bars: 24
Vibe: [joyful x4, bright x2, carnival, sunshine, dancing, Trinidad, road march]

Request: |
  A Trinidad soca/calypso piece in three sections. 8-bar intro — the
  steel drums (GM 114 steel drums) alone play a bright melody in Bb
  major, the characteristic ringing metallic tone of the pan, a
  4-bar phrase repeated with slight variation. Pure sunshine. 8-bar
  verse — the bass (GM 33) enters with the soca engine room pattern
  (driving 8th notes), the brass section (GM 61 brass section) adds
  rhythmic stabs, and the drums play the soca beat (kick on every
  beat, snare on 2 and 4, hi-hat driving 16ths). The steel drums
  shift to comping on upbeats. 8-bar chorus — the brass takes the
  melody with a bright fanfare, the steel drums play rapid 16th-note
  runs, the bass adds fills, and the energy peaks for the road march.
  Port of Spain at carnival. J'ouvert morning. Wine and wave.

Harmony:
  progression: |
    Intro (1-8): [Bb, Eb, F, Bb, Bb, Eb, F, Bb]
    Verse (9-16): [Bb, Eb, F, Bb, Gm, Eb, F, Bb]
    Chorus (17-24): [Bb, Eb, Cm, F, Bb, Eb, F, Bb]
  voicing: bright major chords, steel drum resonance
  rhythm: soca engine room — driving 8ths on bass

Melody:
  scale: Bb major
  register: steel drums Bb4-Bb6, brass Bb3-Bb5
  contour: |
    Intro: steel drum melody, bright arches.
    Verse: steel drums comp, brass stabs.
    Chorus: brass fanfare melody, steel drum 16th runs.
  density: medium (intro) to high (chorus)

Dynamics:
  overall: mf to ff
  arc:
    - bars: 1-8
      level: mf
      shape: steel drums alone, bright
    - bars: 9-16
      level: f
      shape: full soca band, driving
    - bars: 17-24
      level: f to ff
      shape: chorus, brass fanfare, road march peak
  accent_velocity: 112
  ghost_velocity: 48

Rhythm:
  feel: soca engine room — four-on-the-floor kick at carnival tempo,
    relentless, built to move 100,000 people through streets for 8
    hours straight. The engine room is the heartbeat of Trinidad.
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      Kick: every beat, four-on-the-floor — the march never stops.
      Snare: 2 and 4, crisp, cutting through the brass.
      Hi-hat: continuous 16ths, driving — the urgency that makes soca
      different from reggae. Soca wants to go faster. Always faster.
      Bass: driving 8th notes, the engine room's engine — roots and
      5ths alternating, locked to the kick, pushing forward.
      Steel drums: upbeat comps (verse), 16th runs (chorus).

Orchestration:
  steel_drums:
    instrument: steel drums (GM 114)
    technique: |
      Intro: lead melody, ringing.
      Verse: comping on upbeats.
      Chorus: rapid 16th-note runs.
    register: Bb4-Bb6
    entry: bar 1
  bass:
    instrument: fingered bass (GM 33)
    technique: soca engine room — driving 8th notes
    register: Bb1-Bb3
    entry: bar 9
  brass:
    instrument: brass section (GM 61)
    technique: |
      Verse: rhythmic stabs on upbeats.
      Chorus: bright fanfare melody.
    register: Bb3-Bb5
    entry: bar 9
  drums:
    technique: soca beat — kick every beat, snare 2 and 4, hi-hat 16ths
    entry: bar 9

Effects:
  steel_drums:
    reverb: outdoor stage, 0.8s, bright
    eq: presence at 3khz — the ring of the pan is sacred
  brass:
    reverb: same outdoor, 0.6s
    compression: gentle — preserve the fanfare's natural dynamics
  drums:
    compression: tight soca punch — the kick must cut through brass
  bass:
    eq:
      - band: low_mid
        freq: 250hz
        gain: +2db

Expression:
  arc: sunshine to groove to celebration
  narrative: |
    The steel drums at bar 1 are Port of Spain at J'ouvert morning — the
    sun is barely up and the pans are already ringing. The melody is pure
    major-key joy, each note a metallic sunbeam. When the soca engine
    room kicks in at bar 9, the road march begins: bass driving 8th
    notes like a heartbeat that refuses to slow down, brass stabs that
    are both funky and triumphant, drums that make standing still
    impossible. The chorus at bar 17 is the road march at its peak —
    brass fanfare, steel drum runs, the sound of 100,000 people
    wining and waving through the streets of Port of Spain. The
    Greatest Show on Earth.
  character: Lord Kitchener's melodies. Mighty Sparrow's wit. Machel
    Montano's energy. The Panorama competition. Carnival Monday and
    Tuesday. Sunrise in the Savannah.

Texture:
  density: medium (intro) to high (chorus)
  register_spread: Bb1-Bb6
  space:
    principle: |
      Soca is bright. Every frequency band is occupied by something
      shining. The steel drums ring in the highs. The brass punches
      the upper mids. The bass drives the lows. The drums fill
      everything between. There are no dark corners in this music.
      Carnival is daylight. The road march is joy made mandatory.

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-8)
      intensity: mf — steel drums alone, bright melody
    - section: verse (bars 9-16)
      variation: full soca band, engine room groove
    - section: chorus (bars 17-24)
      contrast: brass fanfare, steel drum runs, road march peak

Humanization:
  timing:
    jitter: 0.02
    late_bias: -0.005
    grid: 16th
  velocity:
    arc: flat
    stdev: 10
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.04
      velocity: [42, 55]
  feel: ahead of the beat — soca leans forward, carnival urgency

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [72, 115]
  cc_curves:
    - cc: 91
      from: 20
      to: 42
      position: bars 1-24
    - cc: 11
      from: 72
      to: 115
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds shimmer on sustained steel pan notes
    use: brightness and resonance
  modulation:
    instrument: steel drums
    depth: subtle tremolo vibrato — CC 1 from 0 to 30 on ringing notes
    onset: delayed 0.5 beats
  filter:
    cutoff:
      sweep: bass low-pass opens from 600hz to 2.5khz across verse to chorus
      resonance: low

Automation:
  - track: Steel_Drums
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
        curve: linear
      - beat: 32
        value: 0.5
        curve: smooth
      - beat: 96
        value: 0.35
        curve: smooth
  - track: Shaker
    param: pan
    events:
      - beat: 0
        value: 0.3
      - beat: 48
        value: -0.3
        curve: smooth
      - beat: 96
        value: 0.3
        curve: smooth
""",
    ),

    # 41 ── Appalachian bluegrass ──────────────────────────────────────────
    PromptItem(
        id="appalachian_bluegrass",
        title="Appalachian bluegrass \u00b7 G \u00b7 145 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: bluegrass \u00b7 Key: G \u00b7 145 BPM\nRole: banjo, fiddle, mandolin, upright bass\nVibe: driving x3, joyful x2, earthy, virtuosic, Appalachian",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: bluegrass
Key: G
Tempo: 145
Energy: high
Role: [banjo, fiddle, mandolin, upright bass]
Constraints:
  bars: 24
Vibe: [driving x3, joyful x2, earthy, virtuosic, Appalachian, front-porch, mountain]

Request: |
  An Appalachian bluegrass piece in three sections. 8-bar intro — the
  banjo (GM 105) alone plays a Scruggs-style 3-finger roll in G, the
  characteristic cascading 16th notes of bluegrass, thumb-index-middle
  pattern creating arpeggiated chords. 8-bar verse — the fiddle (GM 110
  fiddle) enters with a driving melody, the mandolin (GM 105 banjo,
  higher octave) chops on beats 2 and 4 (the bluegrass backbeat), and
  the upright bass (GM 32) walks a boom-chuck pattern. The banjo shifts
  to backup rolls. Classic bluegrass drive. 8-bar breakdown — each
  instrument takes 4 bars: first the banjo plays a virtuosic Scruggs
  roll solo (bars 17-20), then the fiddle takes 4 bars of double-stop
  and single-note runs (bars 21-24). Maximum flatpicking energy. The
  front porch of a cabin in the Blue Ridge Mountains. Moonshine and
  firelight.

Harmony:
  progression: |
    Intro (1-8): [G, G, C, D, G, G, C, D]
    Verse (9-16): [G, C, D, G, Em, C, D, G]
    Breakdown (17-24): [G, C, D, G, G, C, D, G]
  voicing: open string voicings, banjo rolls
  rhythm: boom-chuck bass pattern, mandolin chop on 2 and 4

Melody:
  scale: G major with bluegrass inflections (blue 3rd on Bb)
  register: banjo G3-G5, fiddle G3-E6
  contour: |
    Intro: banjo roll pattern, arpeggiated.
    Verse: fiddle melody, driving 8ths with blue notes.
    Breakdown: virtuosic solos, 16th-note runs.
  density: medium (intro/verse) to very dense (breakdown)

Dynamics:
  overall: f to ff
  arc:
    - bars: 1-8
      level: f
      shape: banjo alone, rolling, bright
    - bars: 9-16
      level: f
      shape: full band, driving groove
    - bars: 17-24
      level: f to ff
      shape: breakdown solos, virtuosic peak
  accent_velocity: 112
  ghost_velocity: 48

Rhythm:
  feel: bluegrass drive — slightly ahead of the beat, always pushing
    forward. The mandolin chop on 2 and 4 is the bluegrass equivalent
    of a snare drum, the sharpest backbeat in acoustic music.
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      Mandolin chop: beats 2 and 4, muted percussive strum — the
      backbeat that makes bluegrass drive. Without this, it is folk.
      With it, it is fire.
      Upright bass: boom-chuck — root on 1 and 3, 5th on 2 and 4.
      The bass walks in lockstep with the mandolin chop.
      Banjo roll: continuous cascading 16th-note arpeggios, the
      Scruggs 3-finger pattern — thumb-index-middle, a perpetual
      waterfall of notes that is the sound of bluegrass itself.

Orchestration:
  banjo:
    instrument: banjo (GM 105)
    technique: |
      Intro: Scruggs 3-finger roll.
      Verse: backup rolls.
      Breakdown (17-20): virtuosic solo roll.
    register: G3-G5
    entry: bar 1
  fiddle:
    instrument: fiddle (GM 110)
    technique: |
      Verse: driving melody, blue notes.
      Breakdown (21-24): double-stops, 16th runs.
    register: G3-E6
    entry: bar 9
  mandolin:
    instrument: banjo (GM 105), higher octave
    technique: chop on beats 2 and 4 — bluegrass backbeat
    entry: bar 9
  upright_bass:
    instrument: acoustic bass (GM 32)
    technique: boom-chuck — root on 1 and 3, 5th on 2 and 4
    register: G1-G3
    entry: bar 9

Effects:
  banjo:
    reverb: none — the banjo is naturally bright and present. Adding
      reverb would blur the roll. The cascading notes need air between
      them. Dry is sacred.
    eq: slight presence boost at 3khz for the head ring
  fiddle:
    reverb: very short, 0.3s — enough room for the rosin sound,
      not enough to soften the attack
  mandolin:
    reverb: none — the chop must be as dry and percussive as a snare
  upright_bass:
    reverb: subtle, 0.3s — woody warmth, never blurred
    eq: gentle warmth at 120hz

Expression:
  arc: roll to drive to fire
  narrative: |
    The banjo at bar 1 is the sound of the Appalachian Mountains — the
    Scruggs roll cascading like a mountain stream, each note ringing
    into the next. When the full band enters at bar 9, it is a front
    porch at dusk: the fiddle sings, the mandolin chops the backbeat,
    the upright bass walks, and the banjo rolls underneath everything.
    The breakdown at bar 17 is where bluegrass shows its teeth — the
    banjo solo is pure virtuosity, 16th notes flying, and the fiddle
    solo that follows is fire and rosin. Earl Scruggs picking on
    a porch. Bill Monroe stomping his foot. The mountains are old
    and the music is older.
  character: Earl Scruggs's banjo. Bill Monroe's mandolin. Doc Watson's
    flatpick. The Blue Ridge Mountains. Moonshine. Firelight.

Texture:
  density: medium (intro/verse) to very dense (breakdown)
  register_spread: G1-E6
  space:
    principle: |
      Bluegrass is acoustic music that hits as hard as electric.
      The banjo roll is a continuous cascade — 16th notes with no
      gaps. But each note is a separate pluck, a separate attack,
      and between them is a microsecond of silence that gives the
      roll its definition. The mandolin chop is the sharpest sound
      in the band — muted strings slapped on 2 and 4. The upright
      bass is the deepest. The fiddle is the wildest. Together they
      cover G1 to E6 without any amplification. The porch is the
      concert hall. The mountains are the acoustics.

Form:
  structure: intro-verse-breakdown
  development:
    - section: intro (bars 1-8)
      intensity: f — banjo alone, Scruggs roll
    - section: verse (bars 9-16)
      variation: full band, driving bluegrass groove
    - section: breakdown (bars 17-24)
      contrast: banjo solo (17-20), fiddle solo (21-24), virtuosic

Humanization:
  timing:
    jitter: 0.03
    late_bias: -0.005
    grid: 16th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.05
      velocity: [42, 58]
  feel: slightly ahead — bluegrass leans forward, driving

MidiExpressiveness:
  expression:
    curve: constant bright
    range: [82, 118]
  pitch_bend:
    style: fiddle slides, banjo hammer-on bends
    depth: quarter-tone to half-tone
  cc_curves:
    - cc: 91
      from: 18
      to: 38
      position: bars 1-24
    - cc: 11
      from: 82
      to: 118
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds brightness on mandolin tremolo sustains
    use: resonance and volume swell
  modulation:
    instrument: fiddle
    depth: Appalachian vibrato — CC 1 from 0 to 40 on sustained notes
    onset: delayed 0.5 beats
  filter:
    cutoff:
      sweep: bass low-pass opens from 800hz to 3khz across verse to breakdown
      resonance: low

Automation:
  - track: Fiddle
    param: reverb_wet
    events:
      - beat: 0
        value: 0.15
        curve: linear
      - beat: 32
        value: 0.3
        curve: smooth
      - beat: 96
        value: 0.2
        curve: smooth
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -1db
      - beat: 96
        value: 0db
        curve: smooth
""",
    ),

    # 42 ── Gospel choir ───────────────────────────────────────────────────
    PromptItem(
        id="gospel_choir",
        title="Gospel choir \u00b7 Ab \u00b7 72 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: gospel \u00b7 Key: Ab \u00b7 72 BPM\nRole: choir, church organ, piano, bass, drums\nVibe: uplifting x4, spiritual x2, powerful, communal, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: gospel
Key: Ab
Tempo: 72
Energy: medium
Role: [choir, church organ, piano, bass, drums]
Constraints:
  bars: 32
Vibe: [uplifting x4, spiritual x2, powerful, communal, sacred, Sunday morning, glory]

Request: |
  A full gospel piece in four sections. 8-bar invocation — the church
  organ (GM 19) alone, playing sustained Ab major chords with the
  Leslie speaker effect, a slow hymn-like progression (Ab-Db-Eb-Ab),
  the sound of Sunday morning before the choir arrives. 8-bar verse —
  the choir (GM 52 choir aahs) enters softly with a simple melody,
  the piano (GM 0) adds gospel fills (grace notes, rolled chords),
  and the bass (GM 32) walks in half notes. The choir builds from
  solo to 4-part harmony. 8-bar chorus — the full band arrives: drums
  play a gospel shuffle, the piano shifts to driving gospel chords,
  the choir sings in full 4-part harmony (soprano, alto, tenor, bass),
  and the organ swells with the Leslie on fast. The music lifts.
  8-bar altar call — the choir reaches maximum intensity, the lead
  soprano soars to Ab5, the organ sustains a massive Ab chord, the
  drums build to a crash, and at bar 31 everything drops to the
  organ alone holding Ab for 2 bars. The silence after is prayer.

Harmony:
  progression: |
    Invocation (1-8): [Ab, Db, Eb, Ab, Fm, Db, Eb, Ab]
    Verse (9-16): [Ab, Fm, Db, Eb, Ab, Fm, Bbm7, Eb7]
    Chorus (17-24): [Ab, Db, Eb, Cm, Fm, Db, Eb7, Ab]
    Altar (25-32): [Ab, Db, Eb, Ab, Fm, Db, Eb, Ab]
  voicing: |
    Organ: full 4-note chords, thick.
    Piano: gospel voicings — 7ths, 9ths, grace notes.
    Choir: 4-part SATB harmony.
  rhythm: hymn feel (invocation), gospel shuffle (chorus/altar)
  extensions: 7ths and 9ths on piano, sus4 resolutions

Melody:
  scale: Ab major with gospel inflections (blue 3rd Cb, blue 7th Gb)
  register: choir soprano Ab4-Ab5, lead to Ab5
  contour: |
    Invocation: organ hymn, no melody.
    Verse: simple choir melody, building from solo to harmony.
    Chorus: full 4-part harmony, soaring soprano.
    Altar: maximum intensity, lead soprano peaks Ab5.
  density: sparse (invocation) to maximum (altar call)

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-8
      level: pp to mp
      shape: organ alone, Sunday morning
    - bars: 9-12
      level: mp
      shape: choir enters softly
    - bars: 13-16
      level: mf
      shape: choir builds to 4-part
    - bars: 17-24
      level: f
      shape: full band, chorus, lifting
    - bars: 25-30
      level: f to fff
      shape: altar call, maximum intensity
    - bars: 31-32
      level: fff to pp
      shape: drops to organ alone, prayer, silence
  accent_velocity: 115
  ghost_velocity: 35

Rhythm:
  feel: |
    Invocation: rubato hymn, no fixed meter — the organ breathes, the
    congregation sways.
    Verse/Chorus: gospel shuffle — a triplet-based 12/8 feel in 4/4,
    the swing that is the heartbeat of the Black church. The shuffle
    is not a technique, it is a feeling. It is behind the beat because
    glory takes its time.
    Altar call: the shuffle intensifies, the drummer pushes, the piano
    drives harder, until bar 31 when everything stops.
  subdivision: triplet 8ths (gospel shuffle)
  swing: 68%
  accent:
    pattern: |
      Drums: shuffle pattern, kick on 1 and 3, snare on 2 and 4
      with a triplet swing. The hi-hat plays the triplet subdivision.
      Piano: gospel chords land on 1 with grace-note rolls that
      anticipate by a 32nd — this is testimony, not accompaniment.
      Choir: phrases breathe together, accents on the word.

Orchestration:
  choir:
    instrument: choir aahs (GM 52)
    technique: |
      Verse: simple melody, building from solo to 4-part.
      Chorus: full SATB harmony.
      Altar: maximum, lead soprano soars to Ab5.
    register: bass Ab2-Ab3, tenor Eb3-Eb4, alto Ab3-Eb4, soprano Ab4-Ab5
    entry: bar 9
  church_organ:
    instrument: church organ (GM 19)
    technique: |
      Invocation: sustained hymn chords, Leslie slow.
      Chorus: swelling, Leslie fast.
      Altar: massive Ab chord, sustained.
    entry: bar 1
  piano:
    instrument: acoustic grand (GM 0)
    technique: |
      Verse: gospel fills, grace notes, rolled chords.
      Chorus: driving gospel chords, rhythmic.
    entry: bar 9
  bass:
    instrument: acoustic bass (GM 32)
    technique: walking half notes (verse), gospel groove (chorus)
    register: Ab1-Ab3
    entry: bar 9
  drums:
    technique: gospel shuffle (chorus onward)
    entry: bar 17

Effects:
  organ:
    leslie: slow (invocation), fast (chorus/altar) — the Leslie
      rotating speaker is not an effect, it is the organ's breath
    reverb: stone church, 3.5s — the reverb of a real church, where
      every chord hangs in the air like incense
  choir:
    reverb: same church, 60% wet — voices must surround, the
      congregation is enveloped, the sound comes from everywhere
  piano:
    reverb: same church, drier, more present — the piano testifies,
      it needs to be close, intimate, personal

Expression:
  arc: silence to prayer to praise to glory to silence
  narrative: |
    The organ at bar 1 is the church before the people arrive. The
    Leslie speaker rotates slowly. The chords are simple — Ab, Db, Eb —
    because the truth is simple. When the choir enters at bar 9, it is
    one voice, then two, then four — the congregation gathering. The
    piano adds gospel fills: grace notes that are not decoration but
    testimony. The chorus at bar 17 is the moment the Spirit arrives —
    the drums establish the shuffle, the choir sings in full harmony,
    and the music lifts off the ground. The altar call at bar 25 is
    everything: the lead soprano on Ab5 is not singing — she is
    testifying, and the choir answers with every voice they have.
    At bar 31, everything stops except the organ holding Ab. The
    silence after is the most important sound. That silence is prayer.
  character: Mahalia Jackson's power. Kirk Franklin's joy. The Clark
    Sisters' harmony. Rev. James Cleveland's preaching. Sunday
    morning in Harlem. The Black church. Where it all began.

Texture:
  density: very sparse (invocation) to maximum (altar call) to silence
  register_spread: Ab1-Ab5
  space:
    principle: |
      Gospel music is about vertical space. The organ fills the bottom
      and middle. The piano fills the cracks. The choir fills the top.
      As the piece progresses, the space fills from bottom to top like
      a church filling with congregation. At the altar call, every
      register is occupied — bass Ab2, tenor Eb3, alto Ab3, soprano
      Ab5 — and the organ and piano fill everything between. Then at
      bar 31, it all empties. One organ chord. Silence. The silence is
      the most dense texture of all because it is full of everything
      that came before.

Form:
  structure: invocation-verse-chorus-altar_call
  development:
    - section: invocation (bars 1-8)
      intensity: pp — organ alone, hymn, Leslie slow
    - section: verse (bars 9-16)
      variation: choir enters, builds from solo to 4-part
    - section: chorus (bars 17-24)
      contrast: full band, gospel shuffle, lifting
    - section: altar_call (bars 25-32)
      variation: maximum intensity, soprano peaks, drops to organ

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.015
    grid: 8th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.04
      velocity: [28, 45]
  feel: behind the beat — gospel drags slightly, soulful weight

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [22, 127]
  cc_curves:
    - cc: 91
      from: 38
      to: 68
      position: bars 1-32
    - cc: 11
      from: 22
      to: 127
      position: bars 1-32
    - cc: 1
      from: 20
      to: 55
      position: bars 9-32
  pitch_bend:
    style: vocal slides into notes — gospel ornamentation
    depth: quarter-tone to half-tone
  articulation:
    legato: true
  aftertouch:
    type: channel
    response: adds depth on sustained organ chords
    use: Leslie speed and volume swell
  modulation:
    instrument: church organ
    depth: Leslie tremolo — CC 1 from 20 to 55 across crescendo sections
    onset: immediate
  filter:
    cutoff:
      sweep: bass low-pass opens from 500hz to 2khz across invocation to altar
      resonance: low

Automation:
  - track: Choir
    param: reverb_wet
    events:
      - beat: 0
        value: 0.35
        curve: linear
      - beat: 64
        value: 0.55
        curve: smooth
      - beat: 128
        value: 0.4
        curve: smooth
  - track: Organ
    param: volume
    events:
      - beat: 0
        value: 0.6
      - beat: 64
        value: 0.85
        curve: smooth
      - beat: 120
        value: 1.0
        curve: smooth
      - beat: 124
        value: 0.5
        curve: smooth
""",
    ),

    # 49 ── Full hip-hop song ──────────────────────────────────────────────
    PromptItem(
        id="full_hiphop_song",
        title="Hip-hop full song \u00b7 Fm \u00b7 88 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: hip-hop / boom bap \u00b7 Key: Fm \u00b7 88 BPM\nRole: drums, bass, rhodes, strings, vocal lead\nVibe: soulful x3, hard x2, cinematic, storytelling, street",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: hip-hop boom bap
Key: Fm
Tempo: 88
Energy: medium
Role: [drums, bass, rhodes, strings, vocal lead]
Constraints:
  bars: 32
Vibe: [soulful x3, hard x2, cinematic, storytelling, street, raw, Nas, Kendrick]

Request: |
  A full hip-hop song in four sections. 8-bar intro — a soul sample-
  style Rhodes (GM 4) plays a melancholic chord progression
  (Fm7-Bbm7-Cm7-Dbmaj7) with vinyl crackle texture and gentle tremolo.
  Strings (GM 49) add a cinematic sustain underneath. No drums, no bass.
  Setting the scene. 8-bar verse 1 — the boom bap drums arrive: hard
  kick (boom-bap pattern, kick on 1 and 3-and), snappy snare on 2 and
  4, hi-hat 16ths with open hat on the and-of-2. The bass (GM 33) adds
  a deep Fm sub pattern. The Rhodes continues comping. The vocal lead
  (GM 85 synth voice) enters with a rhythmic melodic hook. 8-bar
  chorus — the strings swell, the Rhodes shifts to sustained chords,
  a second vocal melody enters an octave higher, the bass doubles its
  pattern, and the drums add a crash cymbal on beat 1 of every 2 bars.
  The hook: "we rise." 8-bar bridge — everything drops except the
  Rhodes and a single sustained string note. A piano (GM 0) enters
  with sparse gospel-influenced fills. The vocal lead plays its most
  emotional phrase. Then the drums slam back in at bar 29 with a fill
  into a final 4-bar hook section that combines chorus and bridge
  elements. The story is told. The beat lives.

Harmony:
  progression: |
    Intro (1-8): [Fm7, Bbm7, Cm7, Dbmaj7, Fm7, Bbm7, Cm7, Dbmaj7]
    Verse (9-16): [Fm7, Bbm7, Cm7, Dbmaj7, Fm7, Bbm7, Cm7, Fm7]
    Chorus (17-24): [Dbmaj7, Cm7, Bbm7, Fm7, Dbmaj7, Cm7, Bbm7, Fm7]
    Bridge (25-32): [Fm7, Fm7, Bbm7, Bbm7, Dbmaj7, Cm7, Fm7, Fm7]
  voicing: Rhodes — close-voiced 7th chords, soul register
  rhythm: Rhodes on all beats, bass on boom-bap pattern
  extensions: 7ths throughout, maj7 on Db for warmth

Melody:
  scale: F minor pentatonic with blue notes (Cb, Gb)
  register: vocal lead F3-F5
  contour: |
    Intro: no vocal — Rhodes melody embedded in chord voicings.
    Verse: rhythmic vocal hook, speech-like, Fm pentatonic.
    Chorus: melodic hook, ascending, soulful.
    Bridge: most emotional phrase, wide intervals, peaks F5.
  phrases:
    structure: 2-bar phrases with 2-bar response
  density: zero (intro), rhythmic (verse), melodic (chorus/bridge)

Dynamics:
  overall: mp to f to pp to f
  arc:
    - bars: 1-8
      level: mp
      shape: intro, Rhodes and strings, setting scene
    - bars: 9-16
      level: f
      shape: verse, full boom bap, driving
    - bars: 17-24
      level: f
      shape: chorus, strings swell, hook
    - bars: 25-28
      level: pp
      shape: bridge, stripped, emotional
    - bars: 29-32
      level: f
      shape: drums slam back, final hook
  accent_velocity: 112
  ghost_velocity: 40

Rhythm:
  feel: boom bap — the kick-snare pattern that is the heartbeat of
    New York hip-hop. Not a loop. Not a sample. A living, breathing
    groove that J Dilla taught us could be drunk and perfect at the
    same time.
  subdivision: 16th notes
  swing: 58%
  accent:
    pattern: |
      Kick: beats 1 and the-and-of-3 — the boom. The kick is the
      weight, the gravity, the ground under the MC's feet.
      Snare: beats 2 and 4 — the bap. Hard, snappy, the crack of
      the backbeat that makes heads nod. This is the clock that
      boom bap time is measured by.
      Hi-hat: continuous 16ths, open hat on the and-of-2 — the city,
      the constant. The open hat is the breath.
  ghost_notes:
    instrument: hi-hat
    velocity: 35-52

Orchestration:
  drums:
    kick: boom-bap — beats 1 and 3-and
    snare: hard snappy on 2 and 4
    hi_hat: 16ths, open hat on and-of-2
    entry: "bar 9, exit: bar 25, re-entry: bar 29"
  bass:
    instrument: fingered bass (GM 33)
    technique: deep Fm sub pattern, boom-bap locked to kick
    register: F0-F2
    entry: bar 9
  rhodes:
    instrument: electric piano (GM 4)
    technique: |
      Intro: melody chords, tremolo, vinyl crackle.
      Verse: comping.
      Bridge: sustained, sparse.
    entry: bar 1
  strings:
    instrument: string ensemble (GM 49)
    technique: |
      Intro: cinematic sustain.
      Chorus: swell, dramatic.
      Bridge: single sustained note.
    entry: bar 1
  vocal_lead:
    instrument: synth voice (GM 85)
    technique: |
      Verse: rhythmic melodic hook.
      Chorus: soulful ascending hook.
      Bridge: emotional peak, wide intervals.
    entry: bar 9

Effects:
  rhodes:
    tremolo: gentle, soul texture
    reverb: medium plate, 1s
    texture: vinyl crackle throughout — the crackle is the patina
      of time, the sound of a record that has been loved
  drums:
    compression: hard hip-hop compression — NY boom bap drums are
      the most compressed sound in music. Every hit is maximum impact.
    saturation: subtle vinyl warmth
  strings:
    reverb: large hall, 2.5s — cinematic distance, the strings are
      the backdrop, not the foreground
  bass:
    eq: sub boost at 50hz — you feel this in your sternum

Expression:
  arc: scene-setting to storytelling to hook to vulnerability to triumph
  narrative: |
    The Rhodes at bar 1 sets the scene — a soul sample floating in
    vinyl crackle, strings underneath giving it cinematic weight. This
    is the moment before the story begins. When the boom bap drops at
    bar 9, the story starts: the kick and snare are the footsteps, the
    hi-hat is the city, the bass is the ground. The vocal hook at the
    chorus (bar 17) is the truth the story was building toward —
    ascending, soulful, something you remember. The bridge at bar 25
    strips everything away: Rhodes, a single string note, the most
    emotional vocal phrase. Vulnerability. Then bar 29: the drums
    slam back and the final hook combines everything. The story is
    told. The beat lives. Nas's lyricism. Kendrick's cinematic scope.
    J Dilla's soul.
  character: Nas's Illmatic. Kendrick's TPAB. J Dilla's Donuts.
    Madlib's production. The Roots' live drums. Hip-hop as cinema.

Texture:
  density: sparse (intro) to full (verse/chorus) to naked (bridge) to full
  register_spread: F0-F5
  space:
    principle: |
      Hip-hop production is about layers and absence. The intro is
      two layers: Rhodes and strings. The verse adds three more: kick,
      snare, bass. The chorus adds strings swell and vocal hook. Each
      layer occupies its own frequency band with surgical precision.
      The bridge strips to two layers again — Rhodes and one string
      note — and the sudden emptiness after 16 bars of density is
      the most powerful moment. Then bar 29 brings everything back.
      The architecture of presence and absence IS the story.

Form:
  structure: intro-verse-chorus-bridge
  development:
    - section: intro (bars 1-8)
      intensity: mp — Rhodes and strings, scene-setting
    - section: verse (bars 9-16)
      variation: boom bap drums, bass, vocal hook
    - section: chorus (bars 17-24)
      contrast: strings swell, melodic hook, peak
    - section: bridge (bars 25-32)
      variation: stripped, emotional, drums return bar 29, final hook

Humanization:
  timing:
    jitter: 0.035
    late_bias: 0.01
    grid: 16th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [1, 3]
      strength: 12
    ghost_notes:
      probability: 0.06
      velocity: [32, 50]
  feel: behind the beat — boom bap drags, soulful weight

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [55, 115]
  cc_curves:
    - cc: 91
      from: 25
      to: 55
      position: bars 1-32
    - cc: 11
      from: 55
      to: 115
      position: bars 1-32
  pitch_bend:
    style: Rhodes pitch wobble, vocal slides
    depth: quarter-tone
  aftertouch:
    type: channel
    response: adds warmth on sustained Rhodes and vocal lead
    use: filter opening and tremolo depth
  modulation:
    instrument: strings
    depth: cinematic vibrato — CC 1 from 0 to 40 on sustained pads
    onset: delayed 2 beats
  filter:
    cutoff:
      sweep: bass low-pass opens from 400hz to 2.5khz across verse to chorus
      resonance: moderate

Automation:
  - track: Master
    param: highpass
    events:
      - beat: 0
        value: 80hz
      - beat: 32
        value: 30hz
        curve: smooth
      - beat: 96
        value: 30hz
      - beat: 112
        value: 120hz
        curve: smooth
      - beat: 128
        value: 30hz
        curve: smooth
  - track: Melody
    param: reverb_wet
    events:
      - beat: 0
        value: 0.15
      - beat: 64
        value: 0.35
        curve: smooth
      - beat: 96
        value: 0.2
        curve: smooth
      - beat: 128
        value: 0.3
        curve: smooth
  - track: Rhodes
    param: tremolo_depth
    events:
      - beat: 0
        value: 0.4
      - beat: 32
        value: 0.2
        curve: linear
  - track: Strings
    param: volume
    events:
      - beat: 64
        value: 0.5
      - beat: 96
        value: 1.0
        curve: linear
      - beat: 96
        value: 1.0
      - beat: 100
        value: 0.2
        curve: linear
""",
    ),

]
