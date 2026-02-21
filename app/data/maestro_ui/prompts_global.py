"""STORI PROMPT pool — Global region (Africa, Middle East, Asia, Oceania).

Covers: Afrobeats, and future additions (West African polyrhythmic,
Ethio-jazz, Gnawa, North Indian raga, Balinese gamelan, Japanese zen,
Korean sanjo, Qawwali, Arabic maqam, Anatolian psych rock,
Polynesian/Taiko).
"""

from app.models.maestro_ui import PromptItem

PROMPTS_GLOBAL: list[PromptItem] = [

    # 4 ── Afrobeats pocket ──────────────────────────────────────────────────
    PromptItem(
        id="afrobeats_groove",
        title="Afrobeats pocket \u00b7 Gb \u00b7 102 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: Afrobeats \u00b7 Key: Gb \u00b7 102 BPM\nRole: drums, bass, keys, perc, melody, guitar\nVibe: joyful x3, groovy x2, warm, bouncy",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: chorus
Style: Afrobeats
Key: Gb
Tempo: 102
Energy: medium-high
Role: [drums, bass, keys, perc, melody, guitar]
Constraints:
  bars: 24
  density: medium-high
Vibe: [joyful x3, groovy x2, warm, bouncy, uplifting, infectious]

Request: |
  A full Afrobeats journey in three sections. 4-bar intro with just
  shaker and talking drum establishing the polyrhythmic foundation, then
  a gentle Rhodes chord enters. 12-bar verse where the full rhythm section
  locks in — slapping bass pops on 2 and 4, skank guitar on offbeats,
  three-layer percussion (shaker, talking drum, conga), and the whistle
  melody teases in bars 9-12. 8-bar chorus where the melody opens up
  into full call-and-response, the bass gets more melodic, and the energy
  lifts the whole room. Wizkid meets Fela. Lagos at golden hour.

Harmony:
  progression: [Gbmaj7, Ebm7, Bbm7, Fm7]
  voicing: bright, open — root + 3rd + 7th
  rhythm: |
    Intro: single Rhodes chord, sustained whole notes.
    Verse: stabbed 8th notes on beats 2-and and 4-and.
    Chorus: full chord stabs, guitar joins on offbeats.
  extensions: 9ths on Gbmaj7, 11th on Ebm7, 13th on Fm7 in chorus
  color: warm and luminous — always landing on a bright chord
  reharmonize: |
    Chorus adds Dbmaj7 as passing chord between Bbm7 and Fm7 for
    extra warmth. The borrowed brightness lifts the whole section.

Melody:
  scale: Gb major pentatonic
  register: upper-mid (Db5-Gb5)
  contour: |
    Intro: no melody — just percussion and one chord.
    Verse: short teasing phrases, hinting at the hook.
    Chorus: full call-and-response — 1-bar call peaks on Gb5,
    1-bar answer drops to Db5. Irresistible.
  phrases:
    structure: |
      Verse: 2-bar motifs, casual and conversational.
      Chorus: 1-bar call, 1-bar answer, repeated 4x.
    breath: end of each phrase — half-beat rest
  density: sparse intro, medium verse, high chorus
  ornamentation:
    - vocal-style ornaments on chorus peaks
    - grace notes on pentatonic 4th

Rhythm:
  feel: slightly ahead of the beat — bright and eager
  subdivision: 16th-note feel
  swing: 52%
  accent:
    pattern: skank on every 8th-note offbeat
    weight: bright, snappy
  ghost_notes:
    instrument: snare
    velocity: 40-55
  polyrhythm:
    perc: talking drum plays 3-against-4 against conga pattern
    shaker: straight 16ths — the anchor the polyrhythm locks to

Dynamics:
  overall: mp to f across 24 bars
  arc:
    - bars: 1-4
      level: mp
      shape: flat — percussion only, establishing
    - bars: 5-12
      level: mf
      shape: flat, grooving — full rhythm section
    - bars: 13-16
      level: mf to f
      shape: chorus lifts energy
    - bars: 17-24
      level: f
      shape: sustained joy, slight push into final bar
  accent_velocity: 100
  ghost_velocity: 48
  expression_cc:
    curve: follows section energy
    range: [60, 115]

Orchestration:
  drums:
    kit: acoustic with bright snare
    kick: on 1 and 3, double kick on beat 4-and, enters bar 5
    hi_hat: 8th-note, partially open, enters bar 5
    snare: rimshot on 2 and 4, enters bar 5
  bass:
    technique: slap — pop on 2 and 4, thumb on 1 and 3
    register: Gb1-Gb2
    articulation: staccato pops verse, more melodic and legato in chorus
    entry: bar 5
  keys:
    instrument: Rhodes-style electric piano
    voicing: |
      Intro: sustained whole-note chord, gentle.
      Verse: 2-note stabs, bright register.
      Chorus: full 4-note voicings, doubled octave.
    entry: bar 3 — eases in during intro
  guitar:
    technique: skank — muted strums on offbeats
    voicing: single chord shape, percussive
    entry: bar 13 — chorus only
  perc:
    layer_1: shaker — 16th-note straight, enters bar 1
    layer_2: talking drum — syncopated 3-pattern, enters bar 1
    layer_3: conga — steady 8th triplet feel, enters bar 5
  melody:
    instrument: whistle-style synth, vocal quality
    register: Db5-Gb5
    entry: bar 9 — teases in verse, full in chorus

Effects:
  drums:
    compression:
      type: parallel, punchy
      ratio: 4:1
  bass:
    eq:
      - band: high mid boost
        freq: 1.5khz
        gain: +3db
    compression: fast attack, tight
  keys:
    reverb: bright plate, 0.8s
    chorus: subtle ensemble
  guitar:
    eq:
      - band: high shelf
        freq: 6khz
        gain: +2db

Expression:
  arc: invitation to groove to collective joy
  narrative: |
    The shaker starts and the talking drum answers — a conversation before
    the party begins. When the bass enters at bar 5, hips start moving
    before brains decide to. The melody teases in the verse, promising
    something. Then the chorus delivers. By bar 17, everyone in the room
    is dancing. Nobody decided to. The music decided for them.
  spatial_image: |
    Intro: percussion scattered wide, Rhodes center.
    Verse: drums center, bass upfront, keys left, perc wide.
    Chorus: melody right, guitar left, everything wider.
  character: Wizkid's magnetism. Fela's rhythm. Lagos at golden hour.

Texture:
  density: sparse (intro) to medium (verse) to high (chorus)
  register_spread: Gb1-Gb5
  stereo_field:
    drums: center
    bass: center
    keys: left -20
    melody: right +25
    guitar: left -30
    perc_shaker: wide \u00b140
    perc_talking_drum: right +15
    perc_conga: left -25

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-4)
      intensity: mp — percussion and Rhodes only, establishing groove
    - section: verse (bars 5-12)
      variation: full rhythm section, melody teases bars 9-12
    - section: chorus (bars 13-24)
      contrast: melody full call-and-response, guitar enters, bass melodic
  variation_strategy: |
    The intro is a promise. The verse is a groove. The chorus is a
    celebration. Each section adds layers — never subtracts. The
    polyrhythm is present from bar 1 and never leaves.

Humanization:
  timing:
    jitter: 0.04
    late_bias: -0.01
    grid: 16th
  velocity:
    arc: phrase
    stdev: 12
    accents:
      beats: [1, 3]
      strength: 10
    ghost_notes:
      probability: 0.06
      velocity: [38, 55]
  feel: slightly ahead — Afrobeats pushes forward, never drags

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [60, 115]
  cc_curves:
    - cc: 91
      from: 25
      to: 60
      position: bars 1-24
    - cc: 10
      from: 40
      to: 60
      position: bars 1-24
    - cc: 11
      from: 60
      to: 115
      position: bars 1-24
  pitch_bend:
    style: vocal-style scoops on melody peak notes
    depth: quarter-tone to half-tone
  aftertouch:
    type: channel
    response: subtle — adds shimmer on sustained chord tones
    use: slight brightness boost

Automation:
  - track: Keys
    param: chorus_depth
    events:
      - beat: 0
        value: 0.1
      - beat: 48
        value: 0.3
        curve: smooth
  - track: Bass
    param: eq_presence
    events:
      - beat: 48
        value: +1db
      - beat: 96
        value: +3db
        curve: linear
""",
    ),

    # 23 ── West African polyrhythmic ──────────────────────────────────────
    PromptItem(
        id="west_african_polyrhythm",
        title="West African polyrhythm \u00b7 C \u00b7 110 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: West African polyrhythmic \u00b7 Key: C \u00b7 110 BPM\nRole: djembe, dundun, balafon, kora, shekere\nVibe: joyful x3, communal x2, hypnotic, earthy, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: West African polyrhythmic
Key: C
Tempo: 110
Energy: high
Role: [djembe, dundun, balafon, kora, shekere]
Constraints:
  bars: 24
  time_signature: 12/8
Vibe: [joyful x3, communal x2, hypnotic, earthy, sacred, ancient, alive]

Request: |
  A full West African polyrhythmic piece in three sections, felt in 12/8
  compound time. 8-bar foundation — the dundun (bass drum) establishes
  the timeline pattern in a steady dotted-quarter pulse, while the
  shekere shakes continuous 8th-note triplets. At bar 5, the djembe
  enters with a 4-against-3 cross-rhythm — playing in groups of 4
  against the compound triple feel, creating the classic West African
  polyrhythmic tension. 8-bar weaving — the balafon (GM 12 marimba)
  enters with an ostinato pattern in C pentatonic, accenting every 3rd
  8th note against the 12/8 pulse. The kora (GM 46 harp) adds a
  descending melodic line over the top, its thumb pattern providing a
  bass countermelody. All five voices are independent rhythmic streams
  that interlock like gears. 8-bar communion — a 6-against-4 cross-
  rhythm emerges: the djembe plays groups of 6 against the dundun's
  groups of 4, the balafon shifts to call-and-response with the kora,
  and at bar 21, all voices converge on the downbeat of every 2 bars —
  moments of rhythmic unity before separating again. The piece peaks
  at this convergence. Community as music. Music as community.

Harmony:
  progression: |
    Foundation (1-8): C pentatonic drone — no chord changes, C root.
    Weaving (9-16): [C, F, C, G, C, F, C, G] — simple roots.
    Communion (17-24): [C, F, Am, G, C, F, Am, C] — slightly richer.
  voicing: open — pentatonic, no 7ths, very sparse harmony
  rhythm: harmony changes on dotted-quarter pulse in 12/8
  extensions: none — West African harmony is melodic, not chordal

Melody:
  scale: C major pentatonic (C-D-E-G-A)
  register: balafon C4-C6, kora G2-G5
  contour: |
    Foundation: no melody — pure rhythm.
    Weaving: balafon ostinato (repeating 3-note pattern), kora descending
    melody with thumb bass countermelody.
    Communion: call-and-response between balafon and kora, converging
    on downbeats.
  phrases:
    structure: 2-bar cyclic patterns, varied on each repetition
  density: medium — multiple streams but each one sparse

Rhythm:
  feel: compound triple — 12/8 felt as 4 groups of 3
  subdivision: 8th-note triplets
  swing: 67%
  accent:
    pattern: |
      Dundun: dotted-quarter pulse (beats 1, 4, 7, 10 of 12).
      Djembe: 4-against-3 (foundation), 6-against-4 (communion).
      Shekere: continuous 8th triplets, accent on beat 1.
  polyrhythm:
    cross_rhythm_1: djembe plays groups of 4 against 12/8 (4:3)
    cross_rhythm_2: balafon accents every 3rd 8th (3:4 against dundun)
    cross_rhythm_3: djembe plays groups of 6 against dundun's 4 (6:4) in communion

Dynamics:
  overall: mf to f
  arc:
    - bars: 1-4
      level: mf
      shape: dundun and shekere alone, foundation
    - bars: 5-8
      level: mf to f
      shape: djembe enters, cross-rhythm tension
    - bars: 9-16
      level: f
      shape: full ensemble, interlocking weave
    - bars: 17-20
      level: f
      shape: 6:4 cross-rhythm, maximum complexity
    - bars: 21-24
      level: f
      shape: convergence moments on downbeats, community
  accent_velocity: 110
  ghost_velocity: 42

Orchestration:
  djembe:
    instrument: drums (GM 0, channel 10)
    technique: |
      Three sounds: bass (open center hit), tone (edge), slap (rim).
      Foundation: 4-against-3 cross-rhythm with tone and slap.
      Communion: 6-against-4 with all three sounds.
    entry: bar 5
  dundun:
    instrument: drums (GM 0, channel 10)
    technique: bass drum with bell — dotted-quarter timeline
    entry: bar 1
  balafon:
    instrument: marimba (GM 12)
    technique: |
      Weaving: 3-note ostinato (C-E-G) accenting every 3rd 8th.
      Communion: call phrases answered by kora.
    register: C4-C6
    entry: bar 9
  kora:
    instrument: harp (GM 46)
    technique: |
      Weaving: descending melody with thumb bass (C2-C3 range).
      Communion: response to balafon calls.
    register: melody G3-G5, thumb bass C2-C3
    entry: bar 9
  shekere:
    instrument: drums (GM 0, channel 10)
    technique: continuous 8th-note triplet shaking
    entry: bar 1

Effects:
  all:
    reverb: outdoor village, short natural reverb, 0.6s
    compression: none — dynamic range is sacred
  balafon:
    eq: slight presence boost at 2khz
  kora:
    reverb: slightly more (0.8s), creates depth

Expression:
  arc: foundation to complexity to convergence
  narrative: |
    The dundun at bar 1 is the heartbeat — the timeline that everything
    else relates to. When the djembe enters at bar 5 with its 4-against-3
    cross-rhythm, the mathematics of joy begins. You feel the pull —
    your body wants to follow the dundun, your hands want to follow the
    djembe. This is the genius of West African music: multiple truths
    coexisting. The balafon and kora at bar 9 add melodic streams to
    the rhythmic ones — five independent voices, each telling its own
    story, all of them true. The communion at bar 17 introduces 6:4,
    the most complex cross-rhythm, and yet at bar 21, all five voices
    land together on the downbeat. That convergence is not resolution —
    it is recognition. We are separate. We are together. Both are true.
  character: Mamady Keita's djembe mastery. Toumani Diabat\u00e9's kora.
    The griot tradition. The village circle. Ubuntu — I am because we are.

Texture:
  density: medium — five voices but each sparse, interlocking
  register_spread: C2-C6
  space: each voice occupies its own rhythmic lane — density comes
    from layering, not from any single voice being dense

Form:
  structure: foundation-weaving-communion
  development:
    - section: foundation (bars 1-8)
      intensity: mf — dundun/shekere timeline, djembe cross-rhythm
    - section: weaving (bars 9-16)
      variation: balafon and kora enter, five interlocking voices
    - section: communion (bars 17-24)
      contrast: 6:4 cross-rhythm, convergence moments, community peak
  variation_strategy: |
    Each section adds one layer of rhythmic complexity AND one moment
    of unity. Foundation: one rhythm. Weaving: five rhythms. Communion:
    five rhythms that periodically become one.

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.0
    grid: 8th
  velocity:
    arc: cyclic
    stdev: 16
    accents:
      beats: [0, 3, 6, 9]
      strength: 12
    ghost_notes:
      probability: 0.08
      velocity: [35, 55]
  feel: compound triple — each 8th triplet slightly different,
    maximum human feel, the groove breathes

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [72, 112]
  cc_curves:
    - cc: 91
      from: 18
      to: 42
      position: bars 1-24
    - cc: 11
      from: 72
      to: 112
      position: bars 1-24
  pitch_bend:
    style: kora slides between melody notes
    depth: quarter-tone
""",
    ),

    # 24 ── Ethio-jazz ─────────────────────────────────────────────────────
    PromptItem(
        id="ethio_jazz",
        title="Ethio-jazz \u00b7 C minor \u00b7 92 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Ethio-jazz \u00b7 Key: Cm \u00b7 92 BPM\nRole: vibraphone, organ, alto sax, bass, drums\nVibe: mysterious x3, groovy x2, melancholic, hypnotic",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Ethio-jazz
Key: Cm
Tempo: 92
Energy: medium
Role: [vibraphone, organ, alto sax, bass, drums]
Constraints:
  bars: 24
  time_signature: 6/8
Vibe: [mysterious x3, groovy x2, melancholic, hypnotic, golden, ancient]

Request: |
  A full Ethio-jazz piece in three sections, in the style of Mulatu
  Astatke. 8-bar intro — vibraphone (GM 11) alone plays a descending
  melody in C minor pentatonic with the Ethiopian \u00e9ch\u00e8le scale (flat 2nd,
  flat 6th), the motorized vibrato giving each note a shimmering
  quality. 8-bar groove — drums enter with a 6/8 East African shuffle,
  the organ (GM 19) lays down sustained minor chords (Cm-Fm-Abmaj7-G7),
  the bass walks quarter notes in the cello register, and the vibes
  shift to comping. 8-bar solo — alto sax (GM 66) takes the melody,
  playing long bluesy phrases with bends and cries, while the vibraphone
  adds bell-like fills between phrases. The band settles into a deep
  pocket. Addis Ababa, 1972. The golden age of Ethiopian music.

Harmony:
  progression: |
    Intro (1-8): [Cm, Cm, Fm, Fm, Abmaj7, Abmaj7, G7, G7]
    Groove (9-16): [Cm, Fm, Abmaj7, G7, Cm, Fm, Abmaj7, G7]
    Solo (17-24): [Cm, Fm, Abmaj7, G7, Cm, Fm, G7, Cm]
  voicing: |
    Organ: close-voiced minor chords, mid register.
    Vibes: open, bell-like, wide intervals.
  rhythm: organ sustains, vibes on upbeats when comping
  extensions: maj7 on Ab, dom7 on G — tension and release

Melody:
  scale: C minor with flat 2 (Db) and flat 6 (Ab) — Ethiopian scale
  register: vibes C4-C6, sax Bb3-F5
  contour: |
    Intro: descending melody, minor 2nds and minor 3rds, shimmering.
    Groove: vibes comp, no lead melody.
    Solo: long bluesy sax phrases, bends on Eb and G, cries on high F5.
  phrases:
    structure: 4-bar phrases with 2-bar breath
  density: sparse (intro), medium (groove), melodic (solo)

Rhythm:
  feel: 6/8 East African shuffle — compound duple
  subdivision: 8th-note triplets
  swing: 62%
  accent:
    pattern: |
      Drums: kick on 1 and 4 (of 6), snare on 4, hi-hat 8th triplets.
      Bass: quarter notes, walking.

Dynamics:
  overall: mp to f
  arc:
    - bars: 1-8
      level: mp
      shape: vibes alone, contemplative
    - bars: 9-16
      level: mf
      shape: full band, groove established
    - bars: 17-20
      level: mf to f
      shape: sax solo builds
    - bars: 21-24
      level: f to mf
      shape: solo peaks and settles
  accent_velocity: 98
  ghost_velocity: 38

Orchestration:
  vibraphone:
    instrument: vibraphone (GM 11)
    technique: |
      Intro: lead melody, motorized vibrato, sustain pedal.
      Groove: comping on upbeats, bell-like fills.
      Solo: fills between sax phrases.
    entry: bar 1
  organ:
    instrument: church organ (GM 19)
    technique: sustained minor chords, Leslie speaker effect
    entry: bar 9
  alto_sax:
    instrument: alto sax (GM 66)
    technique: long bluesy phrases, bends, cries, breathy tone
    register: Bb3-F5
    entry: bar 17
  bass:
    instrument: acoustic bass (GM 32)
    technique: walking quarter notes, deep pocket
    register: C1-C3
    entry: bar 9
  drums:
    technique: 6/8 East African shuffle, brushes
    entry: bar 9

Effects:
  vibraphone:
    reverb: warm studio, 1.2s
    tremolo: motorized vibrato at 5Hz
  organ:
    leslie: slow speed, rotating speaker effect
    reverb: same studio
  alto_sax:
    reverb: medium room, 1s
    compression: gentle — preserve dynamics
  bass:
    reverb: subtle, 0.4s

Expression:
  arc: solitary beauty to groove to soulful cry
  narrative: |
    The vibraphone in the intro is Addis Ababa waking up — the flat 2nd
    and flat 6th of the Ethiopian scale give every phrase an ache that
    Western ears cannot quite place. It is not minor. It is not major.
    It is something older. When the band enters at bar 9, the 6/8 shuffle
    creates a groove that is African and jazzy simultaneously — Mulatu's
    genius was knowing that these traditions were always the same tradition.
    The alto sax at bar 17 speaks in bends and cries — bluesy but with
    Ethiopian intervals. The phrase at bar 21 peaks on a high F5 that
    could be joy or grief. In this music, there is no difference.
  character: Mulatu Astatke's cosmic genius. Getatchew Mekurya's sax fire.
    The Eth\u00ecopiques series. 1972 Addis. Gold and smoke and music.

Texture:
  density: sparse (intro) to medium (groove/solo)
  register_spread: C1-C6

Form:
  structure: intro-groove-solo
  development:
    - section: intro (bars 1-8)
      intensity: mp — vibes alone, Ethiopian melody
    - section: groove (bars 9-16)
      variation: full band enters, 6/8 shuffle, organ chords
    - section: solo (bars 17-24)
      contrast: alto sax takes melody, vibes fill, emotional peak
  variation_strategy: |
    Intro establishes the scale and the mood. Groove establishes the
    pocket. Solo releases the emotion. Each section adds one voice
    and one degree of feeling.

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.015
    grid: 8th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 3]
      strength: 8
    ghost_notes:
      probability: 0.06
      velocity: [30, 48]
  feel: behind the beat — Ethiopian jazz drags slightly, luxurious

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [55, 105]
  modulation:
    instrument: vibraphone
    depth: motorized vibrato — CC 1 value 55-75
    onset: immediate
  cc_curves:
    - cc: 91
      from: 28
      to: 55
      position: bars 1-24
    - cc: 1
      from: 55
      to: 75
      position: bars 1-24
    - cc: 11
      from: 55
      to: 105
      position: bars 1-24
  pitch_bend:
    style: sax bends on bluesy notes — Eb and G
    depth: half-tone to whole-tone
  articulation:
    legato: true
""",
    ),

    # 25 ── Gnawa trance ceremony ──────────────────────────────────────────
    PromptItem(
        id="gnawa_trance",
        title="Gnawa trance ceremony \u00b7 Gm \u00b7 78 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Gnawa trance \u00b7 Key: Gm \u00b7 78 BPM\nRole: guembri, qraqeb, voice, bass drone\nVibe: hypnotic x4, spiritual x3, ancient, trance",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Gnawa trance
Key: Gm
Tempo: 78
Energy: medium
Role: [guembri, qraqeb, voice, bass drone]
Constraints:
  bars: 32
Vibe: [hypnotic x4, spiritual x3, ancient, trance, healing, deep, eternal]

Request: |
  A full Gnawa trance ceremony piece in four sections. 8-bar invocation —
  a deep bass drone on G0 alone, the guembri (GM 33 fingered bass) enters
  at bar 3 with a repeating 2-bar bass ostinato in Gm, pentatonic, each
  note heavy and deliberate. 8-bar gathering — the qraqeb (GM 0 drums,
  channel 10, metal castanets) enter with a hypnotic triplet pattern,
  steady and relentless, the guembri pattern becomes slightly more
  ornamented. 8-bar trance — a vocal line (GM 52 choir aahs) enters with
  a call-and-response pattern, the voice calls on G4-Bb4-C5, an unseen
  chorus responds on G3-Bb3. The qraqeb intensify. The guembri adds
  grace notes. The trance deepens. 8-bar peak — all elements reach
  maximum density: the guembri plays continuous 8th notes, the qraqeb
  double-time, the vocals sustain long held notes, and the bass drone
  swells. At bar 29, everything drops to the guembri and drone alone
  for 4 bars — the ceremony completes. Essaouira. Midnight. The spirits
  have been called. The healing has begun.

Harmony:
  progression: |
    Invocation (1-8): Gm drone — no changes.
    Gathering (9-16): [Gm, Gm, Cm, Cm, Gm, Gm, Dm, Gm]
    Trance (17-24): [Gm, Cm, Gm, Dm, Gm, Cm, Gm, Gm]
    Peak (25-32): [Gm, Cm, Dm, Gm, Gm, Gm, Gm, Gm]
  voicing: open — root and 5th only, very sparse
  rhythm: guembri ostinato drives harmony, 2-bar cycle
  extensions: none — Gnawa music is modal, not chordal

Melody:
  scale: G minor pentatonic (G-Bb-C-D-F)
  register: guembri G1-G3, voice G3-C5
  contour: |
    Invocation: guembri ostinato, repeating 2-bar pattern.
    Gathering: ostinato gains grace notes, slight ornamentation.
    Trance: vocal call G4-Bb4-C5, response G3-Bb3.
    Peak: guembri 8th notes, vocals sustain long tones.
  phrases:
    structure: 2-bar cyclic ostinato, varied per section
  density: sparse (invocation) to medium-dense (peak)

Rhythm:
  feel: hypnotic pulse — relentless, trance-inducing
  subdivision: 8th-note triplets
  swing: 65%
  accent:
    pattern: |
      Qraqeb: triplet pattern, accent on 1 of each group.
      Guembri: downbeat emphasis with offbeat ghost notes.

Dynamics:
  overall: pp to f to pp
  arc:
    - bars: 1-8
      level: pp to mp
      shape: drone, guembri enters bar 3
    - bars: 9-16
      level: mp to mf
      shape: qraqeb enter, gathering energy
    - bars: 17-24
      level: mf to f
      shape: vocals enter, trance deepening
    - bars: 25-28
      level: f
      shape: peak density, maximum trance
    - bars: 29-32
      level: f to pp
      shape: drops to guembri and drone, ceremony completes
  accent_velocity: 95
  ghost_velocity: 35

Orchestration:
  guembri:
    instrument: fingered bass (GM 33)
    technique: |
      2-bar repeating ostinato in Gm pentatonic.
      Invocation: sparse, deliberate. Gathering: grace notes added.
      Trance: more ornamented. Peak: continuous 8th notes.
    register: G1-G3
    entry: bar 3
  qraqeb:
    instrument: drums (GM 0, channel 10)
    technique: metal castanet triplet pattern, relentless
    entry: bar 9
  voice:
    instrument: choir aahs (GM 52)
    technique: |
      Call: G4-Bb4-C5, single voice.
      Response: G3-Bb3, lower register (implied chorus).
      Peak: sustained long tones.
    register: G3-C5
    entry: bar 17
  bass_drone:
    instrument: synth bass or low strings
    technique: sustained G0, continuous
    entry: bar 1

Effects:
  guembri:
    reverb: stone room, 1.5s, dry and close
    eq: boost at 120hz for weight
  qraqeb:
    reverb: same room, metallic brightness preserved
  voice:
    reverb: large hall, 3s, 50% wet — voices from far away
  drone:
    reverb: very long tail, 6s, deep and omnipresent

Expression:
  arc: silence to invocation to trance to return
  narrative: |
    The drone at bar 1 is the earth itself. The guembri at bar 3 is the
    maalem (master musician) beginning the ceremony. Each note is placed
    with the weight of centuries. When the qraqeb enter at bar 9, their
    metallic triplets are the pulse of the trance — steady, relentless,
    hypnotic. You stop counting. The vocals at bar 17 are the spirits
    being called — the call rises, the response descends, and between
    them is a space where something happens that music theory cannot
    explain. The peak at bar 25 is the deepest trance — maximum density,
    minimum consciousness. And then at bar 29, everything falls away
    except the guembri and the drone. The ceremony is complete. The
    healing has begun. Essaouira. Midnight. Stars.
  character: Maalem Mahmoud Guinea's depth. Hassan Hakmoun's fire.
    The Gnawa tradition of Morocco. Music as medicine. Sound as spirit.

Texture:
  density: very sparse (invocation) to dense (peak) to sparse (return)
  register_spread: G0-C5

Form:
  structure: invocation-gathering-trance-peak
  development:
    - section: invocation (bars 1-8)
      intensity: pp — drone and guembri alone
    - section: gathering (bars 9-16)
      variation: qraqeb enter, energy gathering
    - section: trance (bars 17-24)
      contrast: vocals enter, call-and-response, deepening
    - section: peak (bars 25-32)
      variation: maximum density bars 25-28, return to sparseness 29-32
  variation_strategy: |
    Each section adds one voice and one degree of trance depth.
    The return at bar 29 is not a fade — it is a completion.

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.02
    grid: 8th
  velocity:
    arc: cyclic
    stdev: 14
    accents:
      beats: [0]
      strength: 8
    ghost_notes:
      probability: 0.08
      velocity: [28, 45]
  feel: hypnotic — slightly behind the beat, trance-inducing

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [25, 100]
  cc_curves:
    - cc: 91
      from: 32
      to: 65
      position: bars 1-32
    - cc: 11
      from: 25
      to: 100
      position: bars 1-32
  pitch_bend:
    style: guembri slides between notes
    depth: quarter-tone to half-tone
""",
    ),

    # 26 ── North Indian raga Yaman ────────────────────────────────────────
    PromptItem(
        id="raga_yaman",
        title="Raga Yaman \u00b7 C Lydian \u00b7 72 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Hindustani classical \u00b7 Key: C Lydian \u00b7 72 BPM\nRole: sitar, tabla, tanpura drone\nVibe: meditative x3, luminous x2, devotional, flowing, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Hindustani classical
Key: C Lydian
Tempo: 72
Energy: low
Role: [sitar, tabla, tanpura drone]
Constraints:
  bars: 32
Vibe: [meditative x3, luminous x2, devotional, flowing, sacred, vast, evening]

Request: |
  A North Indian raga piece based on Raga Yaman (Lydian mode with
  raised 4th) in four sections. 8-bar alap — the tanpura drone (GM 49
  string ensemble) establishes Sa-Pa (C-G) in a continuous shimmering
  loop, and the sitar (GM 104) enters at bar 3 with a free, unmetered
  exploration of the raga: touching Ni (B), descending through Dha Pa
  Ma Ga Re Sa (A-G-F#-E-D-C). No tabla. No pulse. Pure raga. 8-bar
  jor — the sitar establishes a gentle rhythmic pulse, ascending
  through the raga's characteristic phrases (N R G — B-D-E ascending,
  then M P D N S — F#-G-A-B-C descending). The tanpura continues.
  Still no tabla. 8-bar gat — the tabla (GM 116 taiko drum) enters
  with a teental (16-beat cycle, 4+4+4+4), the sitar plays a composed
  melody (bandish) over the taal, and the interplay between sitar
  and tabla creates rhythmic dialogue. 8-bar jhala — maximum speed:
  the sitar plays rapid strumming patterns on the drone strings,
  the tabla intensifies, and the piece builds to a climactic tihai
  (a rhythmic pattern repeated 3 times that lands on sam/beat 1).
  Evening raga. The sun is setting. The music is prayer.

Harmony:
  progression: |
    All sections: C Lydian drone (C-G tanpura). Raga Yaman:
    Sa Re Ga Ma(tivra) Pa Dha Ni Sa = C D E F# G A B C.
    No chord changes — raga is melodic, not harmonic.
  voicing: tanpura drone — Sa and Pa (C and G) continuous
  rhythm: no harmonic rhythm — drone is constant
  extensions: none — raga system does not use chords

Melody:
  scale: C Lydian (Raga Yaman) — C D E F# G A B C
  register: sitar C3-C6
  contour: |
    Alap: free, descending — N D P M G R S (B A G F# E D C).
    Jor: ascending phrases — N R G (B D E), then descending.
    Gat: composed melody (bandish), balanced phrases.
    Jhala: rapid strumming, rhythmic climax, tihai pattern.
  phrases:
    structure: |
      Alap: free, no phrase structure.
      Jor: 4-bar phrases, gradual rhythmic establishment.
      Gat: 4-bar phrases within teental cycle.
      Jhala: 2-bar phrases accelerating to tihai.
  density: very sparse (alap) to very dense (jhala)
  ornamentation:
    - meend (slides between notes)
    - gamak (oscillation between adjacent notes)
    - murki (quick grace note clusters)
    - kan (approach from above or below)

Rhythm:
  feel: |
    Alap: free, no meter.
    Jor: gentle pulse, no taal.
    Gat: teental (16-beat cycle, 4+4+4+4).
    Jhala: fast teental, driving.
  subdivision: 8th notes (jor), 16th notes (gat/jhala)
  swing: 50%
  accent:
    pattern: |
      Teental: sam (beat 1) strongest, khali (beat 9) empty.
      Jhala: every beat accented, driving.

Dynamics:
  overall: ppp to ff
  arc:
    - bars: 1-8
      level: ppp to pp
      shape: alap — barely audible, devotional whisper
    - bars: 9-16
      level: pp to mp
      shape: jor — pulse emerges, gradual building
    - bars: 17-24
      level: mf
      shape: gat — tabla enters, composed melody
    - bars: 25-30
      level: f to ff
      shape: jhala — rapid strumming, building to climax
    - bars: 31-32
      level: ff (tihai) to p
      shape: tihai lands on sam, final held Sa
  accent_velocity: 105
  ghost_velocity: 30

Orchestration:
  sitar:
    instrument: sitar (GM 104)
    technique: |
      Alap: slow meend slides, free exploration.
      Jor: gentle picking, establishing pulse.
      Gat: composed melody, interplay with tabla.
      Jhala: rapid chikari string strumming.
    register: C3-C6
    entry: bar 3
  tabla:
    instrument: taiko drum (GM 116)
    technique: |
      Gat: teental (16-beat cycle) — Na Dhin Dhin Na patterns.
      Jhala: intensifying, driving, tihai at end.
    entry: bar 17
  tanpura:
    instrument: string ensemble (GM 49)
    technique: continuous Sa-Pa (C-G) drone, shimmering
    entry: bar 1 — never stops

Effects:
  sitar:
    reverb: medium hall, 1.5s
    sympathetic_strings: natural resonance (implied by sustain)
  tabla:
    reverb: close, 0.6s — tight and present
  tanpura:
    reverb: large hall, 4s — enveloping
    chorus: very slow, shimmering

Expression:
  arc: silence to meditation to dialogue to ecstasy
  narrative: |
    The tanpura at bar 1 is the universe humming. Sa and Pa — the tonic
    and the fifth — the most ancient interval. When the sitar enters at
    bar 3, it is a human voice joining the cosmic hum. The alap explores
    Raga Yaman note by note, each one a question asked of the evening.
    The jor at bar 9 is the first stirring of rhythm — not imposed but
    emerging from the melody itself. The gat at bar 17 is dialogue: the
    tabla asks, the sitar answers, both within the architecture of
    teental. And the jhala at bar 25 is ecstasy — the sitar's chikari
    strings ringing in rapid fire, the tabla driving, and the final
    tihai at bar 31 landing on sam like a prayer answered. The silence
    after is part of the music.
  character: Ravi Shankar's divinity. Vilayat Khan's lyricism. Nikhil
    Banerjee's depth. The evening raga. The sun setting over the Ganges.
    Music as yoga — union with the divine.

Texture:
  density: very sparse (alap) to very dense (jhala) to silence
  register_spread: C0 (tanpura)-C6 (sitar)

Form:
  structure: alap-jor-gat-jhala
  development:
    - section: alap (bars 1-8)
      intensity: ppp — tanpura drone and free sitar exploration
    - section: jor (bars 9-16)
      variation: pulse emerges, sitar phrases gain rhythm
    - section: gat (bars 17-24)
      contrast: tabla enters, composed melody, rhythmic dialogue
    - section: jhala (bars 25-32)
      variation: rapid strumming, climax, tihai lands on sam
  variation_strategy: |
    The traditional structure of a raga performance. Each section adds
    one dimension: alap = melody alone, jor = melody + pulse, gat =
    melody + pulse + taal, jhala = melody + pulse + taal + fire.

Humanization:
  timing:
    jitter: 0.07
    late_bias: 0.02
    grid: 8th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0]
      strength: 8
    ghost_notes:
      probability: 0.04
      velocity: [22, 38]
  feel: |
    Alap: completely free, no grid. Jor: emerging pulse.
    Gat: human teental. Jhala: fast but human, never mechanical.

MidiExpressiveness:
  expression:
    curve: follows dynamic arc exactly
    range: [15, 108]
  pitch_bend:
    style: sitar meend slides — long glissandi between notes
    depth: 2-3 semitones
  cc_curves:
    - cc: 91
      from: 35
      to: 55
      position: bars 1-32
    - cc: 1
      from: 20
      to: 70
      position: bars 1-32
    - cc: 11
      from: 15
      to: 108
      position: bars 1-32
  articulation:
    legato: true
    portamento:
      time: 40
      switch: on
""",
    ),

    # 27 ── Balinese gamelan ───────────────────────────────────────────────
    PromptItem(
        id="balinese_gamelan",
        title="Balinese gamelan \u00b7 D pelog \u00b7 88 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Balinese gamelan \u00b7 Key: D \u00b7 88 BPM\nRole: gangsa, jegogan, reyong, kendang, gong\nVibe: shimmering x3, hypnotic x2, sacred, interlocking, bright",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Balinese gamelan
Key: D
Tempo: 88
Energy: medium
Role: [gangsa, jegogan, reyong, kendang, gong]
Constraints:
  bars: 24
Vibe: [shimmering x3, hypnotic x2, sacred, interlocking, bright, metallic, alive]

Request: |
  A Balinese gamelan piece in three sections featuring kotekan
  (interlocking parts). 8-bar pokok (core melody) — the jegogan
  (GM 14 tubular bells, low register) plays a slow 8-note core melody
  in the pelog scale (D-E-F-Ab-Bb, 5-tone), one note per bar, while
  the gong (GM 14 tubular bells, lower octave) marks the cycle at
  bar 1 and bar 8. 8-bar kotekan — two gangsa parts (GM 13 xylophone)
  enter with interlocking patterns: gangsa 1 plays on-beats, gangsa 2
  plays off-beats, together creating a continuous 16th-note stream
  that is impossible for one player alone. This is the magic of
  gamelan — the music exists between the players. The reyong (GM 12
  marimba) adds a melodic ostinato above. 8-bar ngubeng-ngoret — the
  kotekan intensifies with the ngoret (moving) pattern where the
  interlocking parts shift in pitch, the kendang (GM 116 taiko) enters
  with the drum pattern that controls tempo and dynamics, and the
  piece reaches its shimmering peak before the gong marks the final
  cycle. Ubud. Sunset. The temple fills with bronze and gold.

Harmony:
  progression: |
    All sections: D pelog scale (D-E-F-Ab-Bb).
    No chord changes — gamelan is melodic and textural.
    Jegogan core melody: D-E-F-Ab-Bb-Ab-F-E (8-bar cycle).
  voicing: unison and octave doublings — no chords in Western sense
  rhythm: core melody in whole notes, elaborations in 16ths
  extensions: none — pelog is a 5-tone system, non-Western tuning

Melody:
  scale: D pelog (D-E-F-Ab-Bb) — 5-tone, non-equal temperament
  register: jegogan D2-D3, gangsa D4-D6, reyong D5-D6
  contour: |
    Pokok: jegogan plays core melody, one note per bar.
    Kotekan: interlocking 16th-note patterns, gangsa 1 and 2.
    Ngubeng-ngoret: kotekan shifts pitch, intensifies.
  phrases:
    structure: 8-bar cycle (one gongan)
  density: sparse (pokok) to very dense (kotekan/ngubeng)

Rhythm:
  feel: precise interlocking — kotekan requires exact timing
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      Gong: marks start and end of 8-bar cycle.
      Jegogan: one note per bar (whole notes).
      Gangsa kotekan: alternating 16ths — 1 plays on-beats, 2 plays off.
      Kendang: controls dynamics and tempo in final section.

Dynamics:
  overall: mp to ff
  arc:
    - bars: 1-8
      level: mp to mf
      shape: jegogan and gong, core melody, majestic
    - bars: 9-16
      level: mf to f
      shape: kotekan enters, shimmering interlocking
    - bars: 17-24
      level: f to ff
      shape: ngubeng-ngoret, kendang drives, peak shimmer
  accent_velocity: 112
  ghost_velocity: 45

Orchestration:
  gangsa:
    instrument: xylophone (GM 13) — two parts
    technique: |
      Kotekan: gangsa 1 plays on-beats, gangsa 2 plays off-beats.
      Together they create continuous 16th notes.
      Ngubeng-ngoret: patterns shift in pitch (ngoret = moving).
    register: D4-D6
    entry: bar 9
  jegogan:
    instrument: tubular bells (GM 14)
    technique: core melody, one note per bar, deep and resonant
    register: D2-D3
    entry: bar 1
  reyong:
    instrument: marimba (GM 12)
    technique: melodic ostinato, bright and high, 4-note pattern
    register: D5-D6
    entry: bar 9
  kendang:
    instrument: taiko drum (GM 116)
    technique: drum patterns that control ensemble tempo/dynamics
    entry: bar 17
  gong:
    instrument: tubular bells (GM 14), low octave
    technique: marks cycle — hit on bar 1 and bar 8 of each 8-bar cycle
    register: D1
    entry: bar 1

Effects:
  metallophones:
    reverb: open-air temple, 2.5s, bright
    eq: presence boost at 3khz for shimmer
  kendang:
    reverb: tight, 0.4s
    compression: gentle

Expression:
  arc: majesty to shimmer to ecstasy
  narrative: |
    The gong at bar 1 is a door opening. The jegogan's core melody is
    ancient — each note one bar long, each bar a world. When the kotekan
    enters at bar 9, the air fills with bronze — two players, neither
    playing a complete melody, together creating something neither could
    alone. This is gamelan's deepest teaching: the music exists between
    the players. The ngubeng-ngoret at bar 17 is the kotekan in flight —
    patterns shifting, the kendang driving, bronze and wood and skin
    all shimmering together. The gong at bar 24 closes the cycle. The
    temple is full. The sun has set. The music continues after the
    sound stops.
  character: The gamelan of Ubud. Pelog tuning's otherworldly intervals.
    I Wayan Suweca's teaching. Walter Spies hearing gamelan for the
    first time. Colin McPhee's Music in Bali. Sound as architecture.

Texture:
  density: sparse (pokok) to very dense (kotekan)
  register_spread: D1-D6
  space: |
    Pokok: mostly space, jegogan notes ring into silence.
    Kotekan: no space — continuous 16th notes fill the air.
    The contrast between these two densities is the piece.

Form:
  structure: pokok-kotekan-ngubeng_ngoret
  development:
    - section: pokok (bars 1-8)
      intensity: mp — jegogan core melody and gong, majestic space
    - section: kotekan (bars 9-16)
      variation: interlocking gangsa, reyong ostinato, shimmering
    - section: ngubeng_ngoret (bars 17-24)
      contrast: kotekan intensifies, kendang enters, maximum shimmer
  variation_strategy: |
    Pokok is the skeleton. Kotekan is the flesh. Ngubeng-ngoret is
    the spirit. Each section doubles the density while maintaining
    the same 8-bar core melody underneath.

Humanization:
  timing:
    jitter: 0.015
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 8
    accents:
      beats: [0]
      strength: 14
    ghost_notes:
      probability: 0.03
      velocity: [40, 55]
  feel: precise — kotekan requires tight interlocking, minimal jitter

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [60, 115]
  cc_curves:
    - cc: 91
      from: 38
      to: 65
      position: bars 1-24
    - cc: 11
      from: 60
      to: 115
      position: bars 1-24
""",
    ),

    # 28 ── Japanese zen ───────────────────────────────────────────────────
    PromptItem(
        id="japanese_zen",
        title="Japanese zen \u00b7 D minor \u00b7 50 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: Japanese traditional / zen \u00b7 Key: Dm \u00b7 50 BPM\nRole: shakuhachi, koto, silence\nVibe: meditative x4, still x3, sacred, empty, vast",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: intro
Style: Japanese traditional zen
Key: Dm
Tempo: 50
Energy: very low
Role: [shakuhachi, koto]
Constraints:
  bars: 24
Vibe: [meditative x4, still x3, sacred, empty, vast, ancient, breath]

Request: |
  A Japanese zen meditation piece in three sections, the sparsest thing
  in the entire catalog. 8-bar breath — the shakuhachi (GM 77) alone,
  playing a single sustained D4 with breath noise, muraiki (bamboo
  overtone technique), slowly bending up a quarter-tone and back. One
  note. One breath. 8 bars. The silence between breaths is longer than
  the note. 8-bar stones — the koto (GM 107) enters with single plucked
  notes in D minor pentatonic (D-F-G-A-C), one note every 2-4 beats,
  each one placed like a stone in a zen garden. The shakuhachi plays a
  second phrase — D4 to A4, a slow ascending 5th, then back down. Still
  mostly silence. 8-bar water — both instruments play together, the
  koto adding a very gentle ostinato (2 notes per bar) while the
  shakuhachi plays its most extended phrase yet — D4-F4-G4-A4-C5-D5,
  ascending the full pentatonic scale over 4 bars, then a single held
  D5 for the final 4 bars. The piece ends on sustained silence. The
  koto's last note rings until it disappears. Ma — the Japanese concept
  of meaningful emptiness. The music is the silence.

Harmony:
  progression: |
    All sections: D minor pentatonic (D-F-G-A-C). No chord changes.
    The koto's notes imply D minor but never state a chord.
  voicing: single notes only — never more than 2 notes sounding
  rhythm: no harmonic rhythm — notes placed freely
  extensions: none

Melody:
  scale: D minor pentatonic (D-F-G-A-C)
  register: shakuhachi D4-D5, koto D3-D5
  contour: |
    Breath: single D4, bent up quarter-tone and back.
    Stones: shakuhachi D4 to A4, slow ascending 5th. Koto single notes.
    Water: shakuhachi ascending pentatonic D4-D5. Koto 2-note ostinato.
  phrases:
    structure: free — each phrase is one breath
    breath: the silence between phrases is longer than the phrases
  density: the sparsest piece in the catalog — mostly silence

Rhythm:
  feel: free, no meter — ma (meaningful emptiness) is the pulse
  subdivision: none — notes are placed by breath, not by grid
  swing: 50%

Dynamics:
  overall: ppp to pp to ppp
  arc:
    - bars: 1-8
      level: ppp
      shape: shakuhachi alone, one note, mostly silence
    - bars: 9-12
      level: ppp
      shape: koto enters, single plucked notes, still nearly silent
    - bars: 13-16
      level: ppp to pp
      shape: shakuhachi second phrase, slightly more present
    - bars: 17-20
      level: pp
      shape: both instruments, the most active this piece gets
    - bars: 21-24
      level: pp to silence
      shape: held D5 fading, koto last note ringing into nothing
  accent_velocity: 52
  ghost_velocity: 15

Orchestration:
  shakuhachi:
    instrument: shakuhachi (GM 77)
    technique: |
      Breath: sustained tone with breath noise, muraiki overtones.
      Stones: slow ascending 5th (D4-A4).
      Water: full pentatonic ascent D4-D5, final held note.
    register: D4-D5
    articulation: maximum breath noise, honkyoku (meditative) style
    entry: bar 1
  koto:
    instrument: koto (GM 107)
    technique: |
      Stones: single plucked notes, one every 2-4 beats.
      Water: gentle 2-note ostinato.
    register: D3-D5
    articulation: each note struck and allowed to ring completely
    entry: bar 9

Effects:
  shakuhachi:
    reverb: empty wooden room, 3s decay, 30ms predelay
    eq: no processing — natural breath preserved
  koto:
    reverb: same room
    eq: slight presence at 4khz for string clarity

Expression:
  arc: breath to presence to ascent to emptiness
  narrative: |
    The shakuhachi at bar 1 is not a musical instrument. It is a
    meditation tool. The single D4 is not a note — it is a breath
    made audible. The silence that follows is not a rest — it is ma,
    the emptiness that gives form its meaning. The koto at bar 9
    places notes like stones in a garden — each one chosen, each
    position considered, each surrounded by vast space. The water
    section at bar 17 is the closest this piece comes to motion —
    the shakuhachi ascending the pentatonic scale like water finding
    its path downhill. The final held D5 is not an ending. It is a
    return to what was always there. The silence after the last koto
    note is the most important part of the piece.
  character: Watazumido's shakuhachi. The silence of a Kyoto temple
    garden. Wabi-sabi — the beauty of imperfection and impermanence.
    Music as zazen — sitting meditation. Ichi-go ichi-e — this
    moment, once, never again.

Texture:
  density: nearly nothing — this piece is 70% silence
  register_spread: D3-D5
  space:
    principle: |
      Ma — the Japanese concept of negative space. The silence is not
      absence. It is the thing itself. Every note interrupts the silence
      and must justify its existence. Most notes in most music would
      not survive this test. These do.

Form:
  structure: breath-stones-water
  development:
    - section: breath (bars 1-8)
      intensity: ppp — shakuhachi alone, one note, mostly silence
    - section: stones (bars 9-16)
      variation: koto enters, shakuhachi second phrase, still sparse
    - section: water (bars 17-24)
      contrast: both instruments, ascending melody, fade to silence
  variation_strategy: |
    Each section adds one degree of presence. Breath: one sound.
    Stones: two sounds. Water: a melody. Then silence. The entire
    piece could fit in the space between two notes of any other
    song in this catalog. That is the point.

Humanization:
  timing:
    jitter: 0.1
    late_bias: 0.04
    grid: quarter
  velocity:
    arc: phrase
    stdev: 6
    accents:
      beats: [0]
      strength: 2
    ghost_notes:
      probability: 0.0
      velocity: [10, 18]
  feel: completely free — no grid, each note placed by breath

MidiExpressiveness:
  expression:
    curve: follows dynamic arc, ppp to pp to silence
    range: [8, 48]
  pitch_bend:
    style: shakuhachi quarter-tone bends — muraiki technique
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 42
      to: 68
      position: bars 1-24
    - cc: 11
      from: 8
      to: 48
      position: bars 1-24
    - cc: 1
      from: 0
      to: 25
      position: bars 9-24
  articulation:
    legato: true
""",
    ),

    # 29 ── Korean pansori/sanjo fusion ────────────────────────────────────
    PromptItem(
        id="korean_pansori_sanjo",
        title="Korean sanjo fusion \u00b7 Am \u00b7 66-132 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Korean sanjo / pansori \u00b7 Key: Am \u00b7 66 BPM\nRole: gayageum, janggu, haegeum\nVibe: dramatic x3, intense x2, melancholic, virtuosic, soul-rending",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Korean sanjo pansori fusion
Key: Am
Tempo: 66
Energy: medium
Role: [gayageum, janggu, haegeum]
Constraints:
  bars: 24
Vibe: [dramatic x3, intense x2, melancholic, virtuosic, soul-rending, ancient, han]

Request: |
  A Korean sanjo-inspired piece in three sections, expressing han
  (the untranslatable Korean concept of deep sorrow, longing, and
  beauty intertwined). 8-bar jinyangjo (slow movement) — the gayageum
  (GM 107 koto, representing the 12-string zither) alone, playing a
  slow melody in the gyemyeonjo mode (similar to minor pentatonic:
  A-C-D-E-G), with heavy vibrato (nonghyeon) and slides between notes.
  Each phrase is a cry. The janggu (GM 116 taiko, representing the
  hourglass drum) enters at bar 5 with a slow jinyangjo rhythmic cycle.
  8-bar jungmori (medium tempo) — the tempo feels doubled in energy,
  the gayageum plays faster phrases with more ornamental slides and
  vibratos, the janggu drives with the jungmori pattern (12/4 feel),
  and the haegeum (GM 110 fiddle, representing the 2-string bowed
  fiddle) enters with a soaring countermelody. 8-bar hwimori (fast
  finale) — maximum speed and intensity, the gayageum plays virtuosic
  cascading runs, the janggu drives relentlessly, the haegeum soars
  to its highest register, and at bar 23 all three instruments converge
  on a unison A4 before a final held silence. Han expressed.
  Han released. Han remains.

Harmony:
  progression: |
    All sections: A gyemyeonjo (A-C-D-E-G, similar to minor pentatonic).
    No chord changes — Korean traditional music is melodic.
  voicing: single melodic lines, unison, octave doublings
  rhythm: no harmonic rhythm — modal framework
  extensions: none

Melody:
  scale: A gyemyeonjo (A-C-D-E-G) — Korean minor pentatonic
  register: gayageum A2-A5, haegeum A3-A6
  contour: |
    Jinyangjo: slow descending phrases, heavy vibrato on each note,
    slides between A and C, between D and E. Each note cries.
    Jungmori: faster, wider intervals, gayageum ornamental runs,
    haegeum countermelody ascending.
    Hwimori: cascading runs, virtuosic, gayageum and haegeum converge.
  phrases:
    structure: |
      Jinyangjo: free, breath-length phrases.
      Jungmori: 2-bar phrases, call-response between gayageum/haegeum.
      Hwimori: 1-bar phrases, accelerating.
  density: sparse (jinyangjo) to very dense (hwimori)
  ornamentation:
    - nonghyeon (heavy vibrato — pressing the string deeply)
    - chuseong (upward slide)
    - toesong (downward slide)
    - jeonseong (ornamental trill)

Rhythm:
  feel: |
    Jinyangjo: very slow, free, rubato.
    Jungmori: moderate, 12/4 feel.
    Hwimori: fast, driving, relentless.
  subdivision: 8th notes (jinyangjo), 16th notes (hwimori)
  swing: 55%
  accent:
    pattern: janggu controls dynamics — strong and weak beats alternate

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-4
      level: pp
      shape: gayageum alone, slow, intimate
    - bars: 5-8
      level: pp to mp
      shape: janggu enters, rhythmic structure emerges
    - bars: 9-16
      level: mf to f
      shape: haegeum enters, energy doubles, drive
    - bars: 17-22
      level: f to fff
      shape: hwimori, maximum virtuosity and intensity
    - bars: 23-24
      level: fff to silence
      shape: unison A4, final held silence
  accent_velocity: 118
  ghost_velocity: 30

Orchestration:
  gayageum:
    instrument: koto (GM 107)
    technique: |
      Jinyangjo: slow, heavy nonghyeon vibrato, slides.
      Jungmori: faster phrases, ornamental runs.
      Hwimori: virtuosic cascading runs, maximum speed.
    register: A2-A5
    entry: bar 1
  janggu:
    instrument: taiko drum (GM 116)
    technique: |
      Jinyangjo: slow rhythmic cycle, sparse.
      Jungmori: 12/4 pattern, medium drive.
      Hwimori: fast, relentless, driving.
    entry: bar 5
  haegeum:
    instrument: fiddle (GM 110)
    technique: |
      Jungmori: soaring countermelody, wide vibrato.
      Hwimori: highest register, convergence with gayageum.
    register: A3-A6
    entry: bar 9

Effects:
  gayageum:
    reverb: wooden hall, 1.5s
  haegeum:
    reverb: same space, slightly more wet
  janggu:
    reverb: tight, 0.5s

Expression:
  arc: sorrow to intensity to catharsis to silence
  narrative: |
    Han cannot be translated. It is sorrow that has been refined by
    centuries into something beautiful. The gayageum at bar 1 expresses
    it directly — each note pressed so deeply into the string that it
    cries. The nonghyeon vibrato is not ornament — it is the sound of
    a heart that has been pressed deeply. The janggu at bar 5 gives
    the sorrow structure. The haegeum at bar 9 gives it flight — a
    bowed voice soaring above the plucked one. The hwimori at bar 17
    is han released — maximum intensity, maximum virtuosity, the
    gayageum cascading, the haegeum soaring, the janggu relentless.
    The unison A4 at bar 23 is all three voices becoming one. And then
    silence. Han expressed. Han released. Han remains.
  character: Kim Juk-pa's gayageum mastery. Ahn Sook-sun's pansori
    voice. The sanjo tradition. The sound of Korean soul.

Texture:
  density: very sparse (jinyangjo) to very dense (hwimori)
  register_spread: A2-A6

Form:
  structure: jinyangjo-jungmori-hwimori
  development:
    - section: jinyangjo (bars 1-8)
      intensity: pp — slow, gayageum alone then janggu, intimate sorrow
    - section: jungmori (bars 9-16)
      variation: haegeum enters, tempo doubles in feel, call-response
    - section: hwimori (bars 17-24)
      contrast: maximum speed, virtuosic, convergence, silence
  variation_strategy: |
    The three traditional sanjo movements: slow, medium, fast. Each
    section doubles the tempo in feel and the emotional intensity.
    The structure is a journey from silence to catharsis to silence.

Humanization:
  timing:
    jitter: 0.08
    late_bias: 0.02
    grid: 8th
  velocity:
    arc: phrase
    stdev: 20
    accents:
      beats: [0]
      strength: 14
    ghost_notes:
      probability: 0.05
      velocity: [22, 38]
  feel: deeply human — every note pressed and released with intention

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [20, 127]
  pitch_bend:
    style: nonghyeon deep vibrato, chuseong/toesong slides
    depth: 1-3 semitones (deep presses)
  cc_curves:
    - cc: 91
      from: 25
      to: 52
      position: bars 1-24
    - cc: 1
      from: 30
      to: 90
      position: bars 1-24
    - cc: 11
      from: 20
      to: 127
      position: bars 1-24
  articulation:
    legato: true
    portamento:
      time: 45
      switch: on
""",
    ),

    # 30 ── Qawwali devotional ─────────────────────────────────────────────
    PromptItem(
        id="qawwali_devotional",
        title="Qawwali devotional \u00b7 Bbm \u00b7 84 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Qawwali / Sufi devotional \u00b7 Key: Bbm \u00b7 84 BPM\nRole: harmonium, choir, tabla, clapping\nVibe: ecstatic x3, devotional x3, building, communal, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Qawwali Sufi devotional
Key: Bbm
Tempo: 84
Energy: medium
Role: [harmonium, choir, tabla, clapping]
Constraints:
  bars: 32
Vibe: [ecstatic x3, devotional x3, building, communal, sacred, fire, surrender]

Request: |
  A full Qawwali devotional piece in four sections, building from
  intimate prayer to ecstatic trance. 8-bar prayer — the harmonium
  (GM 20) alone, playing sustained drone chords on Bbm with the
  bellows breathing audibly, a simple 4-note melody (Bb-Db-Eb-F)
  repeated with slight variation. 8-bar gathering — the tabla (GM 116
  taiko) enters with a slow tintal pattern, the harmonium melody
  becomes more ornamented, and a solo voice (GM 52 choir aahs) enters
  with a devotional melody, simple and sincere. 8-bar building — group
  clapping (GM 0 drums, channel 10) enters on every beat, the choir
  adds a response to the solo voice (call-and-response), the tabla
  intensifies, and the harmonium adds drone bass notes. The energy
  doubles. 8-bar ecstasy — everything reaches maximum: the solo
  voice soars to its highest register, the choir responds with full
  force, the clapping doubles to 8th notes, the tabla drives
  relentlessly, and the harmonium sustains a continuous drone. The
  repetition becomes trance. Nusrat Fateh Ali Khan's fire. The
  dargah at night. Devotion as music. Music as devotion.

Harmony:
  progression: |
    Prayer (1-8): [Bbm, Bbm, Ebm, Bbm, Bbm, Ebm, Fm, Bbm]
    Gathering (9-16): [Bbm, Ebm, Fm, Bbm, Bbm, Ebm, Fm, Bbm]
    Building (17-24): [Bbm, Ebm, Fm, Bbm, Bbm, Ebm, Fm, Bbm]
    Ecstasy (25-32): [Bbm, Ebm, Fm, Bbm, Bbm, Bbm, Bbm, Bbm]
  voicing: harmonium drone — sustained root and 5th, melody on top
  rhythm: harmonium sustains, melody on beats 1 and 3
  extensions: none — Qawwali harmony is simple, power is in repetition

Melody:
  scale: Bb minor (Bb-C-Db-Eb-F-Gb-Ab)
  register: solo voice Bb3-Bb5, choir Bb2-Bb4
  contour: |
    Prayer: simple 4-note motif (Bb-Db-Eb-F), repeated.
    Gathering: melody ornamented, longer phrases.
    Building: call (solo) and response (choir), energy rising.
    Ecstasy: solo voice climbs to Bb5, maximum intensity.
  phrases:
    structure: 2-bar call, 2-bar response (from gathering onward)
  density: sparse (prayer) to very dense (ecstasy)

Rhythm:
  feel: |
    Prayer: free, no meter.
    Gathering: slow tintal.
    Building: tintal with clapping.
    Ecstasy: driving, relentless, trance.
  subdivision: quarter notes (prayer), 8th notes (ecstasy)
  swing: 52%
  accent:
    pattern: clapping on every beat, doubles in ecstasy

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-8
      level: pp to mp
      shape: harmonium alone, intimate prayer
    - bars: 9-16
      level: mp to mf
      shape: tabla and solo voice enter, gathering
    - bars: 17-24
      level: mf to f
      shape: clapping and choir enter, building
    - bars: 25-30
      level: f to fff
      shape: ecstasy — maximum intensity, trance
    - bars: 31-32
      level: fff
      shape: peak sustained, no resolution — the trance continues
  accent_velocity: 120
  ghost_velocity: 35

Orchestration:
  harmonium:
    instrument: reed organ (GM 20)
    technique: |
      Drone: sustained Bb root and F 5th throughout.
      Melody: right hand plays motif, increasingly ornamented.
    entry: bar 1
  choir:
    instrument: choir aahs (GM 52)
    technique: |
      Solo voice (bars 9-16): devotional melody, sincere.
      Call-response (bars 17+): solo calls, choir responds.
      Ecstasy: solo soars, choir at full force.
    register: solo Bb3-Bb5, choir Bb2-Bb4
    entry: bar 9 (solo), bar 17 (full choir response)
  tabla:
    instrument: taiko drum (GM 116)
    technique: |
      Gathering: slow tintal pattern.
      Building: intensifying.
      Ecstasy: relentless driving.
    entry: bar 9
  clapping:
    instrument: drums (GM 0, channel 10)
    technique: |
      Building: every beat, quarter notes.
      Ecstasy: doubles to 8th notes.
    entry: bar 17

Effects:
  harmonium:
    reverb: stone dargah (shrine), 3s
  choir:
    reverb: same space, 60% wet — voices surround
  tabla:
    reverb: tight, 0.6s — present and driving
  clapping:
    reverb: same space as choir

Expression:
  arc: prayer to gathering to building to ecstasy
  narrative: |
    The harmonium at bar 1 breathes. You can hear the bellows. The
    4-note melody is not art — it is prayer, repeated because the
    divine is reached through repetition, not through novelty. The
    tabla at bar 9 gives the prayer a heartbeat. The solo voice is
    sincere — this is not performance, it is devotion. When the
    clapping and choir enter at bar 17, the prayer becomes communal.
    One voice calls, many respond. The energy doubles. And at bar 25,
    ecstasy: the solo voice soars to Bb5, the choir responds with
    everything, the clapping is continuous 8th notes, the tabla
    drives without mercy. This is Qawwali — devotion intensified to
    the point of transcendence. Nusrat Fateh Ali Khan knew: if you
    repeat something enough times with enough devotion, it stops being
    music and becomes a door. The piece does not resolve at bar 32.
    The trance continues beyond the music.
  character: Nusrat Fateh Ali Khan's ocean. Abida Parveen's depth.
    The Sabri Brothers' fire. Thursday night at the dargah.
    Rumi's poetry set to sound.

Texture:
  density: sparse (prayer) to maximum (ecstasy)
  register_spread: Bb0-Bb5

Form:
  structure: prayer-gathering-building-ecstasy
  development:
    - section: prayer (bars 1-8)
      intensity: pp — harmonium alone, intimate, breathing
    - section: gathering (bars 9-16)
      variation: tabla and solo voice, rhythm established
    - section: building (bars 17-24)
      contrast: clapping and choir, call-response, energy doubles
    - section: ecstasy (bars 25-32)
      variation: maximum intensity, trance, no resolution
  variation_strategy: |
    Each section doubles the energy while keeping the same melody.
    The power is in repetition, not variation. The 4-note motif at
    bar 1 is the same motif at bar 25 — but by bar 25, the entire
    community is singing it and the divine has entered the room.

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 18
    accents:
      beats: [0, 2]
      strength: 12
    ghost_notes:
      probability: 0.04
      velocity: [28, 45]
  feel: communal — slightly loose, human, the groove of many bodies

MidiExpressiveness:
  expression:
    curve: follows dynamic arc exactly
    range: [25, 127]
  cc_curves:
    - cc: 91
      from: 35
      to: 62
      position: bars 1-32
    - cc: 11
      from: 25
      to: 127
      position: bars 1-32
  pitch_bend:
    style: vocal ornaments — slides into notes from below
    depth: half-tone
  articulation:
    legato: true
""",
    ),

    # 31 ── Arabic maqam hijaz ─────────────────────────────────────────────
    PromptItem(
        id="arabic_maqam_hijaz",
        title="Arabic maqam Hijaz \u00b7 D Hijaz \u00b7 85 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Arabic maqam \u00b7 Key: D Hijaz \u00b7 85 BPM\nRole: oud, ney, qanun, riq, bass drone\nVibe: mysterious x3, passionate x2, ancient, ornamental, yearning",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Arabic maqam
Key: D Hijaz
Tempo: 85
Energy: medium
Role: [oud, ney, qanun, riq, bass drone]
Constraints:
  bars: 24
Vibe: [mysterious x3, passionate x2, ancient, ornamental, yearning, golden, nocturnal]

Request: |
  A full Arabic maqam piece in Maqam Hijaz (D-Eb-F#-G-A-Bb-C-D) in
  three sections. 8-bar taqsim — the oud (GM 105 banjo, representing
  the short-necked lute) plays a solo taqsim (free improvisation) in
  Maqam Hijaz, exploring the characteristic augmented 2nd between Eb
  and F# that gives Hijaz its yearning quality. Free, unmetered,
  ornamental. A bass drone (low D) sustains beneath. 8-bar groove —
  the riq (GM 0 drums, channel 10, representing the tambourine) enters
  with a saidi rhythm (DUM-DUM-tek-DUM-tek), the ney (GM 73 flute)
  plays a second melody weaving around the oud's phrases, the qanun
  (GM 15 dulcimer) adds shimmering arpeggiated fills, and the oud
  shifts to rhythmic strumming. 8-bar tarab — all instruments reach
  the state of tarab (musical ecstasy): the ney plays its most
  passionate phrase, the oud and qanun play in heterophony (the same
  melody with individual ornaments), the riq drives, and the piece
  peaks on a long held F# (the most emotionally charged note in Hijaz)
  before resolving to D. The desert at night. A thousand and one nights.

Harmony:
  progression: |
    Taqsim (1-8): D Hijaz drone — no changes, free exploration.
    Groove (9-16): [D, Gm, A7, D, D, Gm, A7, D] — implied.
    Tarab (17-24): [D, Gm, A7, D, D, Gm, A7, D] — heterophony.
  voicing: unison and heterophony — same melody, individual ornaments
  rhythm: drone in taqsim, saidi rhythm in groove/tarab
  extensions: none — maqam system is melodic

Melody:
  scale: D Hijaz (D-Eb-F#-G-A-Bb-C-D) — augmented 2nd Eb-F#
  register: oud D3-D5, ney D4-D6, qanun D4-D6
  contour: |
    Taqsim: free descending and ascending exploration of Hijaz.
    Groove: oud rhythmic, ney melodic, qanun ornamental.
    Tarab: heterophony — all play same melody with individual ornaments.
    Peak: long held F# (emotional apex), resolution to D.
  phrases:
    structure: |
      Taqsim: free, breath-length.
      Groove: 2-bar phrases with ornaments.
      Tarab: 4-bar phrases building to peak.
  density: sparse (taqsim) to dense (tarab)
  ornamentation:
    - trill (shakl) on sustained notes
    - slide (glissando) between adjacent maqam degrees
    - mordent ornaments on phrase endings

Rhythm:
  feel: |
    Taqsim: free, no meter.
    Groove: saidi rhythm (DUM-DUM-tek-DUM-tek).
    Tarab: saidi intensifying.
  subdivision: 16th notes (groove/tarab)
  swing: 54%
  accent:
    pattern: saidi — two bass hits followed by high-pitched tek

Dynamics:
  overall: pp to ff
  arc:
    - bars: 1-8
      level: pp to mp
      shape: taqsim, oud alone, contemplative
    - bars: 9-16
      level: mf
      shape: full ensemble, groove established
    - bars: 17-22
      level: mf to ff
      shape: tarab building, heterophony intensifying
    - bars: 23-24
      level: ff to mp
      shape: peak on F#, resolution to D
  accent_velocity: 108
  ghost_velocity: 35

Orchestration:
  oud:
    instrument: banjo (GM 105)
    technique: |
      Taqsim: free improvisation, ornamental.
      Groove: rhythmic strumming.
      Tarab: heterophonic melody with ornaments.
    register: D3-D5
    entry: bar 1
  ney:
    instrument: flute (GM 73)
    technique: breathy, ornamental, long phrases with slides
    register: D4-D6
    entry: bar 9
  qanun:
    instrument: dulcimer (GM 15)
    technique: shimmering arpeggios, rapid ornamental fills
    register: D4-D6
    entry: bar 9
  riq:
    instrument: drums (GM 0, channel 10)
    technique: saidi rhythm, finger rolls on skin
    entry: bar 9
  bass_drone:
    instrument: low strings or synth
    technique: sustained D1, continuous
    entry: bar 1

Effects:
  oud:
    reverb: stone courtyard, 1.8s
  ney:
    reverb: same space, more wet — breathy and far
  qanun:
    reverb: same space, bright and shimmering
  riq:
    reverb: tight, 0.4s

Expression:
  arc: contemplation to groove to ecstasy to resolution
  narrative: |
    The augmented 2nd between Eb and F# is the soul of Maqam Hijaz —
    an interval that yearns. The oud's taqsim at bar 1 explores this
    yearning freely, each phrase a question. When the riq enters at
    bar 9 with the saidi rhythm, the questions find a pulse. The ney
    and qanun join, and the three melodic voices begin to interweave.
    The tarab at bar 17 is the state Arabic musicians live for — when
    the music transcends performance and becomes shared ecstasy. All
    three instruments play the same melody but each with their own
    ornaments — heterophony, not harmony, not unison, but the beautiful
    space between. The peak on F# at bar 22 is the moment of maximum
    yearning. The resolution to D at bar 24 is not satisfaction. It is
    acceptance. The desert at night. The stars are the same stars that
    shone over Baghdad a thousand years ago.
  character: Munir Bachir's oud. The Whirling Dervishes of Konya.
    Um Kulthum's tarab. A thousand and one nights.

Texture:
  density: sparse (taqsim) to rich heterophony (tarab)
  register_spread: D1-D6

Form:
  structure: taqsim-groove-tarab
  development:
    - section: taqsim (bars 1-8)
      intensity: pp — oud alone, free exploration of Hijaz
    - section: groove (bars 9-16)
      variation: riq, ney, qanun enter, saidi rhythm established
    - section: tarab (bars 17-24)
      contrast: heterophony, peak on F#, resolution to D
  variation_strategy: |
    Taqsim introduces the maqam. Groove gives it a body. Tarab
    gives it a soul. The progression is from solo to ensemble to
    communion — the same arc as Qawwali but through Arabic aesthetics.

Humanization:
  timing:
    jitter: 0.06
    late_bias: 0.015
    grid: 16th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0, 2]
      strength: 10
    ghost_notes:
      probability: 0.06
      velocity: [28, 48]
  feel: Arabic groove — slightly behind the beat, ornamental, breathing

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [25, 110]
  pitch_bend:
    style: |
      Oud slides between Hijaz degrees, emphasis on Eb-F# augmented 2nd.
      Ney breath bends — approaching notes from below.
    depth: half-tone to whole-tone
  cc_curves:
    - cc: 91
      from: 30
      to: 58
      position: bars 1-24
    - cc: 11
      from: 25
      to: 110
      position: bars 1-24
    - cc: 1
      from: 25
      to: 65
      position: bars 1-24
  articulation:
    legato: true
    portamento:
      time: 35
      switch: on
""",
    ),

]
