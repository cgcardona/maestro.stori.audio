"""Static seed data for the Maestro Default UI endpoints.

Content here is returned verbatim by the API. When we add a CMS or
per-user personalisation, these become the *fallback* defaults.

PROMPT_POOL — 22 curated STORI PROMPT examples spanning a wide sonic field.
Each item uses the full spec breadth: Mode, Section, Style, Key, Tempo,
Role, Vibe, Request, Harmony, Melody, Rhythm, Dynamics, Orchestration,
Effects, Expression, Texture, MidiExpressiveness.
"""

from app.models.maestro_ui import (
    PromptItem,
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
# STORI PROMPT pool — 22 curated examples, randomly sampled 4-at-a-time
# ---------------------------------------------------------------------------

PROMPT_POOL: list[PromptItem] = [

    # 1 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="lofi_boom_bap",
        title="Lo-fi boom bap \u00b7 Cm \u00b7 75 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: lofi hip hop \u00b7 Key: Cm \u00b7 75 BPM\nRole: drums, bass, piano, melody\nVibe: dusty x3, warm x2, melancholic",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: lofi hip hop
Key: Cm
Tempo: 75
Role: [drums, bass, piano, melody]
Constraints:
  bars: 8
  density: medium-sparse
Vibe: [dusty x3, warm x2, melancholic, laid-back]

Request: |
  Lazy boom bap verse — loose swing, deep bass anchoring Cm-Ab-Eb-Bb,
  lo-fi chord stabs, and a wistful piano melody with plenty of space.
  Like Nujabes playing for the empty room at 3am.

Harmony:
  progression: [Cm7, Abmaj7, Ebmaj7, Bb7sus4]
  voicing: rootless close position
  rhythm: half-note stabs on beats 1 and 3
  extensions: 9ths throughout
  color: bittersweet — Abmaj7 is the emotional peak each bar

Melody:
  scale: C dorian
  register: mid (Bb4-G5)
  contour: descending arch, resolves up on bar 8
  phrases:
    structure: 2-bar call, 2-bar response
    breath: 1.5 beats of silence between phrases
  density: sparse — average 1 note per beat

Rhythm:
  feel: behind the beat
  swing: 56%
  ghost_notes:
    instrument: snare
    velocity: 28-40
  hi_hat: slightly open on the ands

Dynamics:
  overall: mp throughout
  arc:
    - bars: 1-4
      level: mp
      shape: flat
    - bars: 5-8
      level: mp to mf
      shape: gentle swell
  accent_velocity: 88
  ghost_velocity: 32

Orchestration:
  drums:
    kit: boom bap
    kick: slightly late, warm thud
    snare: cracked, slightly behind
  bass:
    technique: finger style
    register: E2-G3
    articulation: legato, occasional staccato on syncopations
  piano:
    voicing: rootless, 7th and 3rd only
    pedaling: half pedal
    right_hand: sparse single-note melody

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

Expression:
  arc: resignation to quiet acceptance
  narrative: |
    Late night. Alone but not lonely. The city outside has gone quiet.
    Every note chosen. Nothing wasted.
  spatial_image: drums back-center, bass upfront, piano left, melody right

Texture:
  density: medium-sparse
  register_spread: E2-G5
  space: silence between every phrase — let it breathe

MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches
    changes_per_bar: 2
  expression:
    curve: flat mp, slight swell bars 5-6
    range: [48, 92]
  pitch_bend:
    style: subtle blues bends on minor 3rds
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 30
      to: 65
      position: bars 1-8
""",
    ),

    # 2 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="melodic_techno_drop",
        title="Melodic techno drop \u00b7 Am \u00b7 128 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: melodic techno \u00b7 Key: Am \u00b7 128 BPM\nRole: kick, bass, lead, pads, perc\nVibe: hypnotic x3, driving x2, euphoric",
        full_prompt="""STORI PROMPT
Mode: compose
Section: drop
Style: melodic techno
Key: Am
Tempo: 128
Role: [kick, bass, lead, pads, perc]
Constraints:
  bars: 16
  density: high
Vibe: [hypnotic x3, driving x2, euphoric, tense]

Request: |
  Full melodic techno drop — punishing four-on-the-floor kick, deep
  Reese bass, soaring minor-key lead sequence, and lush evolving pads.
  The kind of drop that makes a warehouse full of people lose their minds.
  Main lead motif: A-C-E-G, 8th notes with pitch mod and filter sweep.

Harmony:
  progression: [Am, F, C, G]
  voicing: open, stacked 5ths on pads
  rhythm: whole-note pad swells, no chord hits
  extensions: 9ths on pads, bare 5ths on bass
  tension:
    point: bar 14
    device: suspended 4th on lead, held 2 beats
    release: beat 1 bar 15

Melody:
  scale: A natural minor
  register: upper (A4-E6)
  contour: ascending sequence bars 1-8, descending inversion bars 9-16
  phrases:
    structure: 4-bar motif, repeated with variation
    breath: no breath — continuous 8th-note motion
  density: dense — 8th notes throughout
  ornamentation:
    - pitch bend up 1 semitone on every 4th note

Rhythm:
  feel: straight 16th grid, mechanical precision
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: four-on-the-floor kick with 16th-note hi-hat
    weight: strong downbeat emphasis
  polyrhythm:
    perc: 3-against-4 cross rhythm on rimshot

Dynamics:
  overall: forte throughout drop
  arc:
    - bars: 1-2
      level: f
      shape: instant full energy
    - bars: 13-16
      level: f to fff
      shape: exponential build to breakdown
  accent_velocity: 120
  expression_cc:
    curve: constant high
    range: [90, 127]
  automation:
    - param: filter_cutoff
      from: 800hz
      to: 6khz
      position: bars 1-8
      curve: Exp
    - param: reverb_wet
      from: 0.1
      to: 0.4
      position: bars 9-16
      curve: Linear

Orchestration:
  kick:
    style: punchy sub kick, 909 character
    pitch: F1, slight upward pitch bend
    tail: tight, 200ms
  bass:
    technique: Reese — detuned sawtooth x2, slight modulation
    register: A1-A2
    articulation: sustained, sidechain pumping from kick
  lead:
    technique: supersaw, 7 voices, 12 cents detune
    filter: resonant lowpass, cutoff sweeps open over 8 bars
  pads:
    voicing: wide stereo, slow attack 800ms
    filter: gentle highpass at 300hz

Effects:
  kick:
    compression:
      type: FET, ultra-fast
      ratio: 8:1
      attack: 0.5ms
  bass:
    distortion: saturation, light clipping
    eq:
      - band: lowpass
        freq: 200hz
  lead:
    reverb: large hall, 2.5s, 30ms predelay
    delay: 3/16 ping-pong, 20% wet
    chorus: subtle ensemble, 0.3 depth
  pads:
    reverb: plate, 4s, wide
    filter: slow phaser sweep

Expression:
  arc: tension building to explosive release
  narrative: |
    The crowd is already gone. This is pure momentum. The melody cuts
    through the noise like a searchlight. Follow it or get left behind.
  spatial_image: kick center, bass center, lead slightly right, pads wide ±70

Texture:
  density: dense
  register_spread: A1-E6
  layering:
    strategy: kick and bass own low end, lead cuts through mid, pads fill high
  stereo_field:
    kick: center
    bass: center
    lead: right +15
    pads: wide ±70

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: strong vibrato — CC 1 value 60-90
    onset: immediate
  filter:
    cutoff:
      sweep: low to high bars 1-8
      resonance: moderate
  cc_curves:
    - cc: 91
      from: 20
      to: 90
      position: bars 1-16
    - cc: 74
      from: 30
      to: 110
      position: bars 1-8
  pitch_bend:
    range: +-2 semitones
    style: upward bends on phrase peaks
    depth: half-tone
""",
    ),

    # 3 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="cinematic_buildup",
        title="Cinematic orchestral buildup \u00b7 Dm \u00b7 88 BPM",
        preview="Mode: compose \u00b7 Section: buildup\nStyle: cinematic orchestral \u00b7 Key: Dm \u00b7 88 BPM\nRole: strings, brass, timpani, choir pad\nVibe: cinematic x3, tense x2, triumphant",
        full_prompt="""STORI PROMPT
Mode: compose
Section: buildup
Style: cinematic orchestral
Key: Dm
Tempo: 88
Role: [strings, brass, timpani, choir pad]
Constraints:
  bars: 16
  density: orchestral
Vibe: [cinematic x3, tense x2, triumphant, epic]

Request: |
  Epic orchestral buildup — starts with low string tremolo and solo horn
  melody, adds layers every 4 bars until the full orchestra erupts in bar
  13. Timpani rolls through the final 4 bars. Dm-Bb-F-C with a surprise
  Gmaj pivot in bar 14 that turns the pain into triumph.

Harmony:
  progression: [Dm, Bbmaj7, Fmaj7, Csus4, Dm, Bb, F, Gmaj]
  voicing: thick orchestral doublings
  rhythm: whole notes bars 1-8, half notes bars 9-12, quarter-note hits bars 13-16
  extensions: major 7ths on Bb and F for luminous quality
  tension:
    point: bar 12
    device: Csus4 unresolved for 2 bars
    release: Gmaj pivot beat 1 bar 14 — unexpected brightness

Melody:
  scale: D natural minor, shift to D major from bar 14
  register: mid-high (A4-A5 strings, D4-A4 brass)
  contour: ascending stepwise bars 1-8, large leaps bars 9-12, triumphant peak bar 13
  phrases:
    structure: 4-bar statements, each louder and thicker
  density: sparse bars 1-4, dense from bar 9

Rhythm:
  feel: straight, majestic
  subdivision: quarter-note feel, half-note harmonic rhythm
  accent:
    pattern: downbeat accents only bars 1-8, every beat bars 13-16
  pushed_hits:
    - beat: 3.5
      anticipation: quarter-note pickup into bar 13

Dynamics:
  overall: ppp to fff over 16 bars
  arc:
    - bars: 1-4
      level: ppp
      shape: flat
    - bars: 5-8
      level: ppp to mp
      shape: linear crescendo
    - bars: 9-12
      level: mp to f
      shape: exponential
    - bars: 13-16
      level: fff
      shape: flat with accents
  accent_velocity: 127
  ghost_velocity: 20
  expression_cc:
    curve: match dynamic arc
    range: [20, 127]

Orchestration:
  strings:
    bars_1_4: low tremolo, violins divisi — pp
    bars_5_8: add violas with countermelody — mp
    bars_9_16: full section, tutti — forte
    articulation: tremolo bars 1-8, arco bars 9-16, col legno accent bar 13
    vibrato: delayed onset bars 1-4, full from bar 5
  brass:
    bars_1_4: solo horn melody — haunting
    bars_5_12: add two horns doubling
    bars_13_16: full brass tutti — trumpets, trombones, tuba
  timpani:
    bars_13_16: full roll with crescendo
    accent: downbeat quarter-note hits alternating with roll
  choir_pad:
    style: wordless aahs, slow attack
    bars: 9-16

Effects:
  strings:
    reverb: large concert hall, 3.5s decay, 20ms predelay
    compression:
      type: gentle, program-dependent
      ratio: 2:1
  brass:
    reverb: same hall bus
    eq:
      - band: high shelf
        freq: 8khz
        gain: +2db
  timpani:
    compression:
      type: FET, fast
      ratio: 5:1
    saturation: subtle tube warmth

Expression:
  arc: dread to defiance to triumph
  narrative: |
    The moment before everything changes. Darkness builds. Then — against
    all expectation — the G major chord arrives. The impossible made real.
    Not relief. Triumph.
  tension_points:
    - bar: 10
      device: suspended chord, dissonant brass cluster
    - bar: 13
      device: full-force entry of all voices simultaneously
  spatial_image: |
    Strings: wide stereo, back of stage. Brass: center, slightly forward.
    Timpani: center, front. Choir: diffuse, reverberant, everywhere.

Texture:
  density: sparse to orchestral over 16 bars
  register_spread: C2 timpani to A5 violins
  layering:
    strategy: add one instrument family every 4 bars
  space: bars 1-4 are almost silence — the void before the storm

MidiExpressiveness:
  modulation:
    instrument: strings
    depth: medium vibrato — CC 1 from 0 to 50 over 8 bars
    onset: delayed 1 beat from note attack
  expression:
    curve: match dynamic arc — exponential rise
    range: [20, 127]
  cc_curves:
    - cc: 91
      from: 40
      to: 80
      position: bars 1-16
    - cc: 1
      from: 0
      to: 50
      position: bars 1-8
  pitch_bend:
    style: none — classical convention
  aftertouch:
    type: channel
    response: gentle swell on sustained notes
    use: expression boost on peaks
""",
    ),

    # 4 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="afrobeats_groove",
        title="Afrobeats pocket \u00b7 Gb \u00b7 102 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: Afrobeats \u00b7 Key: Gb \u00b7 102 BPM\nRole: drums, bass, keys, perc, melody\nVibe: joyful x3, groovy x2, warm, bouncy",
        full_prompt="""STORI PROMPT
Mode: compose
Section: chorus
Style: Afrobeats
Key: Gb
Tempo: 102
Role: [drums, bass, keys, perc, melody]
Constraints:
  bars: 8
  density: medium-high
Vibe: [joyful x3, groovy x2, warm, bouncy, uplifting]

Request: |
  Irresistible Afrobeats chorus — bright Rhodes-style keys on the
  Gb-Eb-Bb-F progression, slapping bass that pops on every 2 and 4,
  three-layer percussion (shaker, talking drum, conga), and a vocal-style
  whistle melody that makes everyone move. Skank guitars on the offbeats.

Harmony:
  progression: [Gbmaj7, Ebm7, Bbm7, Fm7]
  voicing: bright, open — root + 3rd + 7th
  rhythm: stabbed 8th notes on beats 2-and and 4-and
  extensions: 9ths on Gbmaj7, 11th on Ebm7
  color: warm and luminous — always landing on a bright chord

Melody:
  scale: Gb major pentatonic
  register: upper-mid (Db5-Gb5)
  contour: short phrases that peak and return — call-and-response structure
  phrases:
    structure: 1-bar call, 1-bar answer, 4x
    breath: end of each phrase — half-beat rest
  density: medium — strong 8th-note feel, syncopated

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

Dynamics:
  overall: mf to f throughout
  arc:
    - bars: 1-4
      level: mf
      shape: flat, grooving
    - bars: 5-8
      level: f
      shape: slight push into final bar
  accent_velocity: 100
  ghost_velocity: 48
  expression_cc:
    curve: constant mf-f
    range: [70, 110]

Orchestration:
  drums:
    kit: acoustic with bright snare
    kick: on 1 and 3, double kick on beat 4-and
    hi_hat: 8th-note, partially open
    snare: rimshot on 2 and 4
  bass:
    technique: slap — pop on 2 and 4, thumb on 1 and 3
    register: Gb1-Gb2
    articulation: staccato pops, legato thumbs
  keys:
    instrument: Rhodes-style electric piano
    voicing: 2-note stabs, bright register
  perc:
    layer_1: shaker — 16th-note straight
    layer_2: talking drum — syncopated 3-pattern
    layer_3: conga — steady 8th triplet feel

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

Expression:
  arc: pure joy — no darkness, no tension
  narrative: |
    This is the moment the crowd locks in. Hips start moving. Smiles appear.
    The music doesn't ask for your attention — it just takes it.
  spatial_image: drums center, bass center upfront, keys left, melody right, perc wide

Texture:
  density: medium-high
  register_spread: Gb1-Gb5
  stereo_field:
    drums: center
    bass: center
    keys: left -20
    melody: right +25
    perc_shaker: wide ±40
    perc_talking_drum: right +15

MidiExpressiveness:
  expression:
    curve: constant warm mf
    range: [72, 108]
  cc_curves:
    - cc: 91
      from: 25
      to: 55
      position: bars 1-8
    - cc: 10
      from: 40
      to: 60
      position: bars 1-8
  pitch_bend:
    style: vocal-style scoops on melody peak notes
    depth: quarter-tone to half-tone
  aftertouch:
    type: channel
    response: subtle — adds shimmer on sustained chord tones
    use: slight brightness boost
""",
    ),

    # 5 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="ambient_drone",
        title="Ambient drone \u00b7 D \u00b7 58 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: ambient / drone \u00b7 Key: D \u00b7 58 BPM\nRole: pads, arp, sub drone, texture\nVibe: dreamy x3, atmospheric x2, minimal, peaceful",
        full_prompt="""STORI PROMPT
Mode: compose
Section: intro
Style: ambient drone
Key: D
Tempo: 58
Role: [pads, arp, sub drone, texture]
Constraints:
  bars: 16
  density: sparse
Vibe: [dreamy x3, atmospheric x2, minimal, peaceful, distant]

Request: |
  Slowly evolving ambient intro — sustained D major pad with long slow
  attack, a gentle pentatonic arp drifting in and out of focus, a deep
  sub drone on D1, and granular texture that sounds like light through
  frosted glass. No drums. No pulse. Just space and time.

Harmony:
  progression: [Dmaj9, Asus2, Gmaj7, Dmaj7]
  voicing: open, stacked 4ths and 5ths
  rhythm: held whole notes, no rhythmic attacks
  extensions: 9ths and maj7ths throughout
  color: luminous, unresolved — always floating

Melody:
  scale: D major pentatonic
  register: upper (A4-D6)
  contour: slowly drifting up and down with no clear phrase structure
  phrases:
    structure: free — no metric grid
    breath: long silences — notes every 2-4 beats on average
  density: very sparse — 1 note every 3 beats average

Rhythm:
  feel: floating — no clear pulse
  subdivision: free, no grid
  swing: 50%

Dynamics:
  overall: pp to mp over 16 bars
  arc:
    - bars: 1-8
      level: pp
      shape: flat
    - bars: 9-16
      level: pp to mp
      shape: exponential slow swell
  accent_velocity: 60
  ghost_velocity: 20
  automation:
    - param: reverb_wet
      from: 0.3
      to: 0.7
      position: bars 8-16
      curve: Smooth
    - param: filter_cutoff
      from: 600hz
      to: 2.5khz
      position: bars 1-16
      curve: Log

Orchestration:
  pads:
    instrument: warm analog pad, slow attack 2s, slow release 3s
    voicing: open Dmaj9 spread over 3 octaves
    stereo: wide ±60
  arp:
    pattern: D-F#-A-D pentatonic, 8th triplets, random gate
    filter: resonant lowpass, slowly opening
    reverb: very long 5s decay
  sub_drone:
    pitch: D1
    technique: pure sine, extremely slow attack 4s
    level: barely audible — felt more than heard
  texture:
    style: granular — stretched recordings of piano harmonics
    pitch: random ±2 semitones
    density: sparse clouds

Effects:
  pads:
    reverb: huge hall, 6s decay, 50ms predelay, 65% wet
    chorus: very slow, subtle pitch modulation
  arp:
    reverb: same hall bus, 80% wet
    delay: dotted quarter, 40% wet, high feedback (8 repeats)
  texture:
    reverb: infinite — frozen reverb pad
    filter: gentle lowpass at 3khz

Expression:
  arc: pure stillness — no narrative arc
  narrative: |
    The moment between sleep and waking. Light through curtains. A memory
    of a place you have never been but somehow know. Time is not linear here.
  spatial_image: everything wide and diffuse — no center except the sub drone

Texture:
  density: sparse
  register_spread: D1-D6
  layering:
    strategy: each layer occupies its own frequency band with no overlap
  space:
    principle: |
      The silence between notes is the music. Never fill the space.
      Let sounds decay naturally. Let the room breathe.

MidiExpressiveness:
  expression:
    curve: slow swell pp to mp over 16 bars
    range: [25, 75]
  modulation:
    instrument: pads
    depth: very slow pitch shimmer — CC 1 value 0-15
    onset: bars 8-16 only
  cc_curves:
    - cc: 91
      from: 60
      to: 85
      position: bars 1-16
    - cc: 74
      from: 20
      to: 65
      position: bars 4-16
    - cc: 1
      from: 0
      to: 15
      position: bars 8-16
  sustain_pedal:
    style: full sustain throughout
    changes_per_bar: 0
""",
    ),

    # 6 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="jazz_reharmonization",
        title="Jazz reharmonization \u00b7 Bb \u00b7 120 BPM",
        preview="Mode: compose \u00b7 Section: bridge\nStyle: bebop jazz \u00b7 Key: Bb \u00b7 120 BPM\nRole: piano, bass, drums\nVibe: jazzy x2, mysterious x2, bittersweet, flowing",
        full_prompt="""STORI PROMPT
Mode: compose
Section: bridge
Style: bebop jazz
Key: Bb
Tempo: 120
Role: [piano, bass, drums]
Constraints:
  bars: 8
Vibe: [jazzy x2, mysterious x2, bittersweet, flowing]

Request: |
  Jazz trio bridge with aggressive reharmonization — take the I-IV-V in Bb
  and replace every change with a tritone substitution or secondary dominant.
  Walking bass from the pianist's left hand. Piano comps with bright rootless
  voicings. Drums ride cymbal throughout with subtle snare bombs on bar 7.
  This is the moment of harmonic surprise before the head returns.

Harmony:
  progression: [Bbmaj7, Ebm7-Ab7, Dmaj7, Gm7-C7, Fm7-Bb7, Ebmaj7, Am7-D7, Gmaj7]
  voicing: rootless — 3rd and 7th in left hand, extensions in right
  rhythm: comping — syncopated quarter and 8th-note stabs
  extensions: 9ths, 11ths, 13ths throughout
  reharmonize: |
    Bar 1-2: Bbmaj7 → Ebm7-Ab7 (bII7 substitution)
    Bar 3-4: target D major instead of IV — tritone pivot
    Bar 5-6: ii-V-I in Eb with chromatic approach
    Bar 7-8: Am7-D7-Gmaj7 — Gmaj ends the bridge, forces re-entry into head
  tension:
    point: bar 6
    device: augmented 6th chord on beat 3
    release: beat 1 bar 7

Melody:
  scale: Bb bebop scale with chromatic passing tones
  register: mid-upper (C4-F5)
  contour: descending line bar 1-4, ascending resolution bar 5-8
  phrases:
    structure: 2-bar bebop lines with eighth-note motion
    breath: quarter-note rest between phrases
  density: dense — 8th-note lines, occasional triplet burst
  ornamentation:
    - grace notes on approach tones
    - blue notes on b3 and b7

Rhythm:
  feel: slightly ahead — bebop urgency
  subdivision: 8th-note triplet feel
  swing: 62%
  ghost_notes:
    instrument: snare
    velocity: 35-50
  hi_hat: foot hat on 2 and 4

Dynamics:
  overall: mf throughout, accent peaks at f
  accent_velocity: 100
  ghost_velocity: 40

Orchestration:
  piano:
    comping: rootless voicings, syncopated 8th-note rhythm
    right_hand: single-note bebop line
    left_hand: walking bass lines bars 1-4, comp bars 5-8
    pedaling: minimal — just for phrase connection
  bass:
    technique: arco walking — each beat a different chord tone or approach
    register: Bb1-Bb3
    articulation: legato with slight portamento on leaps
  drums:
    ride: continuous 8th-note swing ride cymbal
    snare: bombs on bar 7 beats 2 and 4 — loud
    kick: light, feathered

Effects:
  piano:
    reverb: small bright room, 0.5s
    eq:
      - band: presence
        freq: 3khz
        gain: +2db
  bass:
    eq:
      - band: warmth
        freq: 200hz
        gain: +3db

Expression:
  arc: harmonic surprise to joyful release
  narrative: |
    The moment when the harmony goes somewhere nobody expected and somehow
    it is exactly right. Like a wrong turn that leads somewhere beautiful.
  character: Ahmad Jamal's speed with Bill Evans' harmonic daring

Texture:
  density: medium
  register_spread: Bb1-F5
  space: the walking bass defines the bottom, piano owns mid, no masking

MidiExpressiveness:
  sustain_pedal:
    style: minimal catches — connect phrase tones only
    changes_per_bar: 4
  expression:
    curve: constant conversational mf
    range: [60, 100]
  pitch_bend:
    style: bass slides on approach notes, quarter-tone
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 25
      to: 40
      position: bars 1-8
  articulation:
    legato: true
    portamento:
      time: 40
      switch: on
""",
    ),

    # 7 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="dark_trap",
        title="Dark trap \u00b7 Fm \u00b7 140 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: dark trap \u00b7 Key: Fm \u00b7 140 BPM\nRole: drums, 808, pad, melody\nVibe: dark x3, haunting x2, brooding, aggressive",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: dark trap
Key: Fm
Tempo: 140
Role: [drums, 808, pad, melody]
Constraints:
  bars: 8
Vibe: [dark x3, haunting x2, brooding, aggressive]

Request: |
  Dark trap verse — sparse hi-hat triplets, a booming 808 sliding on F1-C1,
  eerie stacked-fifths pad in Fm, and a melodic bell line playing the minor
  pentatonic. Snare clap on 3 only. Lots of space and menace.

Harmony:
  progression: [Fm, Db, Ab, Eb]
  voicing: stacked 5ths, no 3rds — cold and hollow
  rhythm: whole-note pads, no rhythmic movement
  extensions: none — bare power chords for menace
  color: cold, hollow, threatening

Melody:
  scale: F minor pentatonic
  register: upper (F5-C6)
  contour: mostly static, occasional upward flick
  phrases:
    structure: 1-bar phrases with 1-bar rests
  density: very sparse — 3-4 notes per bar

Rhythm:
  feel: quantized grid
  subdivision: 16th-note triplet hi-hat pattern
  swing: 50%
  accent:
    pattern: triplet hi-hat with random velocity drops
    weight: heavy downbeat kick, sparse snare clap bar 3

Dynamics:
  overall: mp to f
  accent_velocity: 115
  ghost_velocity: 25
  automation:
    - param: filter_cutoff
      from: 200hz
      to: 1.2khz
      position: bars 1-8
      curve: Smooth

Orchestration:
  drums:
    kick: 808-style sub kick, pitch slides F1-C1 over 500ms
    snare: trap clap — bar 3 only
    hi_hat: 16th-note triplet, randomized velocity 35-90
  808:
    pitch: F1 slide to C1 over 1 bar
    tail: 2 bars sustain
    distortion: subtle saturation for presence
  pad:
    voicing: stacked 5ths in Fm spread wide
    attack: slow 1.5s
    stereo: wide ±70

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

Expression:
  arc: cold menace throughout
  narrative: |
    The sound of a night that could go wrong. Watching and waiting.
    Every silence is as heavy as every note.
  spatial_image: 808 center, kick center, pad wide, melody slightly right

Texture:
  density: sparse
  register_spread: F1-C6
  space: deliberate emptiness — the trap is in the silence

MidiExpressiveness:
  pitch_bend:
    style: 808 slide — programmatic downward bend on each note start
    depth: full range 2 semitones
  cc_curves:
    - cc: 74
      from: 20
      to: 80
      position: bars 1-8
    - cc: 91
      from: 40
      to: 80
      position: bars 1-8
  expression:
    curve: constant brooding mp
    range: [45, 85]
""",
    ),

    # 8 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="bossa_nova",
        title="Bossa nova \u00b7 Em \u00b7 132 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: bossa nova \u00b7 Key: Em \u00b7 132 BPM\nRole: guitar, bass, drums, melody\nVibe: warm x3, intimate x2, nostalgic, flowing",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: bossa nova
Key: Em
Tempo: 132
Role: [guitar, bass, drums, melody]
Constraints:
  bars: 8
Vibe: [warm x3, intimate x2, nostalgic, flowing, bittersweet]

Request: |
  Classic bossa nova feel — nylon guitar with the distinctive Jobim
  rhythmic pattern (dotted-quarter, 8th, 8th), walking bass on Em7-Am7-D7-Gmaj7,
  light brush snare on 2 and 4, and a saudade-drenched flute-style melody.
  Like a slow afternoon in Rio.

Harmony:
  progression: [Em7, Am7, D7, Gmaj7, Cmaj7, F#7b9, Bm7, B7]
  voicing: guitar chord melody — thumb bass, fingers play chord + melody together
  rhythm: bossa clave — 3-3-2 pattern of 8th notes
  extensions: 9ths, 11ths, 13ths — rich jazz extensions throughout
  color: warm bittersweet — major 7ths always present

Melody:
  scale: E dorian with chromatic passing tones
  register: mid (G4-E5)
  contour: mostly stepwise, occasional leap to 9th
  phrases:
    structure: 4-bar phrases with half-bar breath
    breath: short space — bossa is conversational
  density: medium — one note per beat with occasional rests

Rhythm:
  feel: slightly behind the beat — lush, relaxed
  subdivision: 8th notes, bossa clave pattern
  swing: 53%
  accent:
    pattern: bossa clave — long-short-short feel
    weight: gentle, never accented harshly
  ghost_notes:
    instrument: brush snare
    velocity: 30-45

Dynamics:
  overall: mf throughout — conversation, not performance
  accent_velocity: 85
  ghost_velocity: 32

Orchestration:
  guitar:
    technique: nylon string, fingerpicked
    voicing: chord-melody — bass + inner chord + melody simultaneously
    articulation: legato melody, slightly staccato inner voices
  bass:
    technique: finger style, upright sound
    register: E1-D2
    pattern: roots on 1 and 3, chord tones on 2 and 4
  drums:
    style: brush snare on 2 and 4, wire brushes on ride
    kick: very light — beats 1 and 3 only

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

Expression:
  arc: warm nostalgia — saudade
  narrative: |
    A memory of something perfect that is already gone. Not grief —
    just the beautiful ache of knowing it happened.
  character: Tom Jobim on a slow afternoon. No hurry. Everywhere to be.

Texture:
  density: medium-sparse
  register_spread: E1-E5
  space: the guitar carries bass AND melody — the drums are whispers

MidiExpressiveness:
  sustain_pedal:
    style: no sustain — nylon guitar is naturally dry
    changes_per_bar: 0
  expression:
    curve: constant warm mf
    range: [62, 95]
  pitch_bend:
    style: subtle string bends on melody notes — quarter-tone only
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 20
      to: 45
      position: bars 1-8
  articulation:
    legato: true
    portamento:
      time: 25
      switch: on
""",
    ),

    # 9 ──────────────────────────────────────────────────────────────────────
    PromptItem(
        id="funk_pocket",
        title="Funk pocket \u00b7 E \u00b7 108 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: classic funk \u00b7 Key: E \u00b7 108 BPM\nRole: drums, bass, guitar, keys, horns\nVibe: groovy x3, joyful x2, driving, energetic",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: classic funk
Key: E
Tempo: 108
Role: [drums, bass, guitar, keys, horns]
Constraints:
  bars: 8
Vibe: [groovy x3, joyful x2, driving, energetic, bouncy]

Request: |
  Irresistible funk pocket — slap bass locked with the kick on 1 and 3,
  scratchy rhythm guitar on the E9 chord (offbeats), tight clav stabs on
  the upbeats, horn line (trumpet + alto sax) playing the head riff in bar
  5-8. Everything in the pocket. No wasted notes. James Brown would approve.

Harmony:
  progression: [E9, A9, E9, B9]
  voicing: 9th chords throughout — root + 7th + 9th on guitar
  rhythm: guitar on every 16th-note offbeat (scratchy funk)
  extensions: dominant 9ths — gritty and bright
  color: raw and punchy — no softness

Melody:
  scale: E mixolydian
  register: mid (E4-B4 horns)
  contour: call-and-response horn riff bars 5-8
  phrases:
    structure: 2-bar horn riff with 2-bar silence
  density: sparse in bars 1-4, medium in bars 5-8

Rhythm:
  feel: right on the beat — machine-tight
  subdivision: 16th notes
  swing: 51%
  accent:
    pattern: kick on 1 and 3, snare on 2 and 4, all 16th upbeats
    weight: everything is accented — funk has no ghosts
  ghost_notes:
    instrument: snare
    velocity: 35-55
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early — classic funk push

Dynamics:
  overall: f throughout — punch and power
  accent_velocity: 110
  ghost_velocity: 42

Orchestration:
  drums:
    kit: acoustic funk
    kick: D-click on attack, boom on body — 1 and 3
    snare: fat backbeat 2 and 4, ghost notes throughout
    hi_hat: 16th notes, partially closed
  bass:
    technique: slap — thumb on 1 and 3, pop on every offbeat 16th
    register: E1-E2
    articulation: ultra staccato — each note a separate event
  guitar:
    technique: muted scratch — 16th-note offbeats only, single E9 chord
    style: scratchy rhythm, no sustain
  keys:
    instrument: clavinet
    pattern: upbeat stabs, syncopated 8th-note hits
  horns:
    trumpet: plays the top melody
    alto_sax: plays a 3rd below
    rhythm: 16th-note punches with short rests

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

Expression:
  arc: pure groove energy — no emotional journey
  narrative: |
    The moment when everybody in the room starts moving at the same time
    without deciding to. Pure physical music. The pocket is the feeling.
  character: Bootsy Collins meets Nile Rodgers. Every note serves the groove.

Texture:
  density: medium-high
  register_spread: E1-B4
  space: no padding — every element has a specific rhythmic job

MidiExpressiveness:
  expression:
    curve: constant punchy f
    range: [88, 118]
  pitch_bend:
    style: bass slap slides — upward quarter-tone before each note
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 15
      to: 30
      position: bars 1-8
  aftertouch:
    type: channel
    response: light filter opening on sustained horn notes
    use: slight brightness boost
""",
    ),

    # 10 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="neo_soul_groove",
        title="Neo-soul groove \u00b7 Gm \u00b7 83 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: neo-soul \u00b7 Key: Gm \u00b7 83 BPM\nRole: drums, bass, keys, guitar, melody\nVibe: warm x3, intimate x2, melancholic, groovy",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: neo-soul
Key: Gm
Tempo: 83
Role: [drums, bass, keys, guitar, melody]
Constraints:
  bars: 8
Vibe: [warm x3, intimate x2, melancholic, groovy, laid-back]

Request: |
  Soulful neo-soul verse — live-feel drums with heavy swing, a melodic
  bass line that talks back to the melody, Rhodes chords on the Gm7-Cm7-Eb-D7alt
  progression, a muted guitar adding texture on upbeats, and a breathy
  synth melody with plenty of space. D'Angelo meets J Dilla.

Harmony:
  progression: [Gm7, Cm7, Ebmaj7, D7alt]
  voicing: rich extended — 9ths, 11ths, #5 on D7alt
  rhythm: lazy Rhodes stabs — behind the beat, never on downbeats
  extensions: Gm9, Cm11, Ebmaj9#11, D7#9b13
  color: lush and bittersweet — D7alt creates yearning

Melody:
  scale: G dorian with chromatic approach notes
  register: mid (D4-G5)
  contour: short sighing phrases that end on the 9th
  phrases:
    structure: 2-bar phrases with full bar of breath
    breath: essential — the space is the soul
  density: sparse — maximum 4 notes per bar

Rhythm:
  feel: heavy behind the beat — almost drunk
  subdivision: 16th-note feel with heavy swing
  swing: 64%
  ghost_notes:
    instrument: snare
    velocity: 25-45
  hi_hat: loose, partially open, often late

Dynamics:
  overall: mp throughout
  accent_velocity: 88
  ghost_velocity: 28

Orchestration:
  drums:
    kit: vintage acoustic, slightly compressed
    kick: hits 1 and 3, sometimes anticipates beat 3
    snare: wide fat crack on 2 and 4
    hi_hat: sloppy, human — slightly open
  bass:
    technique: finger style, melodic
    register: G1-D3
    articulation: mix of legato and staccato — follows groove instinct
  keys:
    instrument: Rhodes electric piano
    voicing: rootless, voiced in mid register
    rhythm: lazy behind-beat comping
  guitar:
    technique: muted Wes Montgomery style — octaves
    pattern: upbeat fills only, never on the downbeat

Effects:
  drums:
    saturation: tape emulation, warm
    compression:
      type: program-dependent, slow
  keys:
    tremolo: subtle, slow 3Hz
    reverb: warm plate, 1.2s
  guitar:
    reverb: small room, 0.4s

Expression:
  arc: yearning without resolution
  narrative: |
    2am, sitting by the window. You know what you want. It is not here.
    The music makes it feel okay to want it anyway.
  character: D'Angelo circa Voodoo. Humid. Slightly imprecise. Perfectly human.

Texture:
  density: medium-sparse
  register_spread: G1-G5
  space: the bass and melody have a conversation — everything else listens

MidiExpressiveness:
  sustain_pedal:
    style: half-pedal catches on Rhodes
    changes_per_bar: 3
  expression:
    curve: constant intimate mp
    range: [50, 88]
  modulation:
    instrument: melody synth
    depth: slow vibrato onset — CC 1 from 0 to 35 after attack
    onset: delayed 1.5 beats
  pitch_bend:
    style: vocal-style scoops on melody notes — approach from below
    depth: quarter to half-tone
  cc_curves:
    - cc: 91
      from: 35
      to: 65
      position: bars 1-8
    - cc: 1
      from: 0
      to: 35
      position: bars 1-8
""",
    ),

    # 11 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="drum_and_bass",
        title="Liquid drum & bass \u00b7 Dm \u00b7 174 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: liquid drum & bass \u00b7 Key: Dm \u00b7 174 BPM\nRole: drums, reese bass, pad, melody\nVibe: flowing x2, melancholic x2, driving, energetic",
        full_prompt="""STORI PROMPT
Mode: compose
Section: drop
Style: liquid drum and bass
Key: Dm
Tempo: 174
Role: [drums, reese bass, pad, melody]
Constraints:
  bars: 8
Vibe: [flowing x2, melancholic x2, driving, energetic, atmospheric]

Request: |
  Liquid D&B drop — classic Amen-break-inspired drum pattern at 174 BPM
  (half-time feel for 87 BPM vibe), thick Reese bass on D1 with heavy
  modulation, lush pad in Dm9, and a wistful lead melody over the
  Dm-Bb-F-C progression. Fast but emotional — the liquid DnB signature.

Harmony:
  progression: [Dm9, Bbmaj9, Fmaj7, Cm7]
  voicing: open spread, lots of air
  rhythm: whole-note pad swells, no rhythmic hits
  extensions: 9ths throughout

Melody:
  scale: D natural minor
  register: upper-mid (F4-A5)
  contour: lyrical, stepwise — vocal feel
  phrases:
    structure: 4-bar phrases with 2-beat breath
  density: medium — 8th-note feel at half-time (equivalent to quarter notes)

Rhythm:
  feel: locked on grid — DnB precision
  subdivision: 8th notes at 174 BPM (sounds like quarters at 87)
  swing: 52%
  accent:
    pattern: Amen-style — kick on 1, snare on 2 and 3, rolling 16ths
  ghost_notes:
    instrument: snare roll
    velocity: 30-55

Dynamics:
  overall: f throughout
  accent_velocity: 112
  ghost_velocity: 38
  automation:
    - param: filter_cutoff
      from: 400hz
      to: 3khz
      position: bars 1-4
      curve: Exp

Orchestration:
  drums:
    style: Amen break inspired — chopped and rolled
    kick: 1 and 3, tight sub kick
    snare: 2 and 4 with rolls leading into each bar
    hi_hat: 16th notes, alternating velocity
  reese_bass:
    technique: detuned sawtooth, heavy frequency modulation
    register: D1-D2
    modulation: slow filter sweep, resonance at 60%
  pad:
    style: warm analog string pad
    attack: slow 1s
    stereo: very wide ±65

Effects:
  drums:
    compression: ultra-fast FET, heavy limiting
    saturation: subtle
  reese_bass:
    filter: resonant lowpass, slow LFO at 0.3Hz
    distortion: light saturation
  pad:
    reverb: hall, 3s decay

Expression:
  arc: melancholic momentum — beautiful but relentless
  narrative: |
    Running at full speed through a rainstorm. Exhilarating. Exhausting.
    The music doesn't stop for feelings — it carries them.

Texture:
  density: medium-high
  register_spread: D1-A5
  stereo_field:
    drums: center
    bass: center
    pad: wide ±65
    melody: slightly left -10

MidiExpressiveness:
  modulation:
    instrument: reese bass
    depth: heavy filter modulation — CC 1 value 60-100
    onset: immediate
  filter:
    cutoff:
      sweep: low to high bars 1-4
      resonance: strong
  cc_curves:
    - cc: 74
      from: 30
      to: 100
      position: bars 1-4
    - cc: 91
      from: 30
      to: 70
      position: bars 1-8
    - cc: 1
      from: 60
      to: 100
      position: bars 1-8
  pitch_bend:
    style: bass filter resonance slides
    depth: 1 semitone
""",
    ),

    # 12 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="minimal_deep_house",
        title="Minimal deep house \u00b7 Am \u00b7 122 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: minimal deep house \u00b7 Key: Am \u00b7 122 BPM\nRole: kick, bass, chord stab, perc\nVibe: hypnotic x3, atmospheric x2, minimal, groovy",
        full_prompt="""STORI PROMPT
Mode: compose
Section: drop
Style: minimal deep house
Key: Am
Tempo: 122
Role: [kick, bass, chord stab, perc]
Constraints:
  bars: 8
Vibe: [hypnotic x3, atmospheric x2, minimal, groovy, dark]

Request: |
  Late-night minimal deep house — four-on-the-floor kick with a deep warm
  sub bass on A1, one Am7 chord stab on the upbeat of beat 2, shuffled
  hi-hat triplets, and a rim-click cross-stick on beat 3. Minimal. Patient.
  The kind of track that works at 3am when the crowd has thinned and only
  the believers are left.

Harmony:
  progression: [Am7]
  voicing: just the 7th — A + G, no 3rd, no 5th
  rhythm: single stab on upbeat of beat 2, every other bar
  extensions: bare 7th chord for maximum tension

Melody:
  scale: A minor
  register: none — no melody in minimal house
  density: zero — silence is the melody

Rhythm:
  feel: mechanical, locked to grid
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: four-on-the-floor kick, rim on 3, shuffled hi-hat triplets
    weight: kick is king — everything else serves it
  polyrhythm:
    perc: 3-against-4 on hi-hat against kick

Dynamics:
  overall: constant f — house doesn't breathe
  accent_velocity: 115
  ghost_velocity: 40
  automation:
    - param: filter_cutoff
      from: 200hz
      to: 1khz
      position: bars 1-8
      curve: Linear

Orchestration:
  kick:
    style: deep sub kick, 909 character
    pitch: A0, very short attack
    tail: 400ms
  bass:
    technique: sustained sine wave sub
    register: A0-A1
    articulation: held whole notes with subtle pitch variation
  chord_stab:
    instrument: vintage organ
    voicing: bare 7th, staccato
    rhythm: offbeat hit, bars 1/3/5/7 only
  perc:
    hi_hat: shuffled triplet, 16th notes
    rim: on beat 3, every bar

Effects:
  kick:
    compression:
      type: hard limit
      ratio: infinity:1
    eq:
      - band: sub
        freq: 60hz
        gain: +3db
  bass:
    filter: resonant lowpass, very slow LFO
  chord_stab:
    reverb: dark cave, 2s

Expression:
  arc: hypnotic stasis — no arc, no narrative
  narrative: |
    The music disappears and you are just moving. Time stops. The kick
    is a heartbeat. The bass is gravity. You are inside it now.

Texture:
  density: sparse
  register_spread: A0-G3
  space: enormous — almost nothing is happening and it is everything

MidiExpressiveness:
  cc_curves:
    - cc: 74
      from: 15
      to: 60
      position: bars 1-8
    - cc: 91
      from: 20
      to: 50
      position: bars 1-8
  expression:
    curve: constant hypnotic f
    range: [85, 110]
  pitch_bend:
    style: none — minimal house is precise
""",
    ),

    # 13 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="synthwave_night_drive",
        title="Synthwave night drive \u00b7 Cm \u00b7 118 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: synthwave / retrowave \u00b7 Key: Cm \u00b7 118 BPM\nRole: lead, bass, pads, drums, arp\nVibe: nostalgic x3, cinematic x2, driving, dark",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: synthwave retrowave
Key: Cm
Tempo: 118
Role: [lead, bass, pads, drums, arp]
Constraints:
  bars: 8
Vibe: [nostalgic x3, cinematic x2, driving, dark, mysterious]

Request: |
  80s synthwave verse — bright saw lead playing the Cm-Ab-Eb-Bb melody,
  pulsing bass on C1 following the kick pattern, wide lush pads underneath,
  analog-style drum machine (808-adjacent), and a 16th-note arp on the
  chord tones. Gated reverb on the snare. Pure neon and chrome nostalgia.

Harmony:
  progression: [Cm, Abmaj7, Ebmaj7, Bb7]
  voicing: open 5ths on bass, full extensions on pads
  rhythm: pad swells on whole notes, bass follows kick
  extensions: major 7ths on Ab and Eb — cinematic luminosity over minor

Melody:
  scale: C natural minor
  register: upper (G4-C6)
  contour: ascending line bars 1-4, falling resolution bars 5-8
  phrases:
    structure: 2-bar melodic statements
  density: medium — quarter and 8th notes

Rhythm:
  feel: straight, metronomic — machines don't swing
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: four-on-the-floor kick, gated snare on 2 and 4

Dynamics:
  overall: mf to f
  accent_velocity: 105
  ghost_velocity: 45
  automation:
    - param: filter_cutoff
      from: 500hz
      to: 4khz
      position: bars 1-8
      curve: Log

Orchestration:
  lead:
    instrument: sawtooth synth, 3 voices detuned ±8 cents
    filter: resonant lowpass, slowly opening
    portamento: slight — 40ms glide between notes
  bass:
    technique: pulsed — follows kick, single note per beat
    register: C1-C2
    articulation: staccato, tight
  pads:
    instrument: warm analog pad, slow attack 700ms
    voicing: full chord spread ±2 octaves
    stereo: ultra wide ±80
  drums:
    kick: 808-style, tight
    snare: gated reverb — 1980s aesthetic
    hi_hat: 16th notes, closed

Effects:
  lead:
    chorus: 3-voice, 0.4 depth, wide stereo
    delay: quarter-note, 25% wet, stereo ping-pong
    reverb: medium hall, 1.5s
  pads:
    reverb: large hall, 4s, very wet (60%)
    phaser: slow, gentle sweep
  drums:
    snare:
      reverb: gated — gate closes at 0.3s
    compression: FET, fast

Expression:
  arc: yearning nostalgia — the memory of the future
  narrative: |
    Driving at night. The city recedes in the mirror. The music is the
    only thing that exists. You are going somewhere you have never been
    and somehow it feels like coming home.
  spatial_image: lead slightly right, pads ultra-wide, drums center, bass center

Texture:
  density: medium
  register_spread: C1-C6
  stereo_field:
    lead: right +15
    bass: center
    pads: ±80
    arp: left -20
    drums: center

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: slow vibrato — CC 1 from 0 to 40 over 4 bars
    onset: bars 5-8 only
  filter:
    cutoff:
      sweep: closed to open bars 1-8
      resonance: moderate
  cc_curves:
    - cc: 74
      from: 30
      to: 100
      position: bars 1-8
    - cc: 91
      from: 40
      to: 75
      position: bars 1-8
    - cc: 1
      from: 0
      to: 40
      position: bars 5-8
  pitch_bend:
    style: vibrato-style narrow bends on lead phrase peaks
    depth: quarter-tone
  articulation:
    portamento:
      time: 40
      switch: on
""",
    ),

    # 14 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="post_rock_crescendo",
        title="Post-rock crescendo \u00b7 Em \u00b7 98 BPM",
        preview="Mode: compose \u00b7 Section: buildup\nStyle: post-rock \u00b7 Key: Em \u00b7 98 BPM\nRole: guitars, bass, drums, keys\nVibe: cinematic x3, tense x2, intense, atmospheric",
        full_prompt="""STORI PROMPT
Mode: compose
Section: buildup
Style: post-rock
Key: Em
Tempo: 98
Role: [guitars, bass, drums, keys]
Constraints:
  bars: 16
Vibe: [cinematic x3, tense x2, intense, atmospheric, triumphant]

Request: |
  Post-rock crescendo — 16 bars building from quiet picked guitar and
  keys to a full wall of distorted guitars, crashing cymbals, and
  thunderous bass. Starts at pp, ends at fff. Em-G-D-Bm progression.
  The guitars start clean and arpeggio, then crunch when the drums enter
  fully at bar 9. This is Explosions in the Sky meets Mogwai.

Harmony:
  progression: [Em, G, D, Bm]
  voicing: bars 1-8 open 5ths, bars 9-16 full power chords
  rhythm: half-note strums bars 1-8, driving 8th-note power chords bars 9-16
  extensions: 9ths on Em and G bars 1-8

Melody:
  scale: E natural minor
  register: upper guitar (B3-E5)
  contour: bars 1-8 single arpeggiated guitar, bars 9-16 guitar unison
  phrases:
    structure: 4-bar melodic arch, repeated and building
  density: bars 1-8 sparse arpeggios, bars 9-16 continuous 8th notes

Rhythm:
  feel: straight, building momentum
  subdivision: 8th notes from bar 9
  swing: 50%
  accent:
    pattern: bars 1-8 minimal, bars 9-16 four-on-the-floor with crashes

Dynamics:
  overall: pp to fff over 16 bars
  arc:
    - bars: 1-4
      level: pp
      shape: flat
    - bars: 5-8
      level: p to mp
      shape: linear
    - bars: 9-12
      level: mf to f
      shape: exponential
    - bars: 13-16
      level: fff
      shape: flat and relentless
  accent_velocity: 127
  ghost_velocity: 25

Orchestration:
  guitars:
    bars_1_8: clean, fingerpicked arpeggios — single guitar
    bars_9_16: two distorted guitars in unison, thick chorus
    distortion: bars 9-16 only — heavy overdrive
  bass:
    bars_1_8: octave below guitar, very quiet
    bars_9_16: heavy picked, locked with kick
  drums:
    bars_1_4: brushes only — very quiet
    bars_5_8: light sticks entry — kick and snare
    bars_9_16: full kit, crash cymbal on bar 9 beat 1

Effects:
  guitars:
    bars_9_16:
      distortion: heavy — dual-channel amp sim
      chorus: wide, 3 voices
      delay: dotted 8th, 30% wet
    reverb: large hall, 2.5s
  keys:
    reverb: hall, 3s, very wet

Expression:
  arc: quiet yearning to cathartic release
  narrative: |
    The dam breaking. Everything you have held in finally coming out.
    Not anger — relief. The crash at bar 9 is not violence. It is freedom.
  tension_points:
    - bar: 8
      device: full silence on beat 4 — one beat of nothing
    - bar: 9
      device: full band entry with crash cymbal — the release

Texture:
  density: pp to orchestral over 16 bars
  register_spread: E1-E5

MidiExpressiveness:
  expression:
    curve: exponential rise pp to fff
    range: [20, 127]
  cc_curves:
    - cc: 91
      from: 20
      to: 70
      position: bars 1-16
    - cc: 74
      from: 30
      to: 90
      position: bars 9-16
  modulation:
    instrument: guitars
    depth: vibrato on sustained notes bars 9-16 — CC 1 from 0 to 60
    onset: bars 9-16 only
  pitch_bend:
    style: guitar bends on phrase peaks — up 1 semitone
    depth: 1 semitone
""",
    ),

    # 15 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="reggaeton_dembow",
        title="Reggaeton dembow \u00b7 Bbm \u00b7 96 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: reggaeton \u00b7 Key: Bbm \u00b7 96 BPM\nRole: drums, bass, synth chord, perc\nVibe: energetic x3, driving x2, dark, bouncy",
        full_prompt="""STORI PROMPT
Mode: compose
Section: chorus
Style: reggaeton
Key: Bbm
Tempo: 96
Role: [drums, bass, synth chord, perc]
Constraints:
  bars: 8
Vibe: [energetic x3, driving x2, dark, bouncy, aggressive]

Request: |
  Hard reggaeton chorus with the dembow rhythm — hi-hat on every 16th,
  kick on 1 and the and of 2, snare clap on 3, bass slides on Bbm. Synth
  chord stabs on Bbm-Ebm-Gb-F. Congas and shaker adding Afro-Latin flavor.
  This is for the main stage, full energy.

Harmony:
  progression: [Bbm, Ebm7, Gbmaj7, F7]
  voicing: punchy stabs — root + 5th on bass, 3rd + 7th on synth
  rhythm: synth stabs on beats 2 and 4 offbeats — classic reggaeton
  extensions: 7ths on Ebm and F for tension

Melody:
  scale: Bb minor pentatonic
  register: mid (Db4-Bb4)
  contour: short repeated melodic hook, 2-bar motif
  phrases:
    structure: 2-bar phrase repeated 4x with slight variations
  density: medium — one main note per beat with ornaments

Rhythm:
  feel: right on the grid — reggaeton is quantized
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: dembow — kick on 1 and 2-and, snare clap on 3
    weight: snare clap is very loud, very present

Dynamics:
  overall: f throughout
  accent_velocity: 118
  ghost_velocity: 50

Orchestration:
  drums:
    kick: tight sub kick, dembow pattern
    snare: loud rimshot clap on 3
    hi_hat: 16th notes, tight and bright
  bass:
    technique: sustained electronic bass
    register: Bb0-Bb1
    pitch: slides between chord roots
  synth_chord:
    instrument: detuned saw synth
    voicing: punchy stabs
    rhythm: offbeat hits
  perc:
    conga: 8th-note pattern, Latin feel
    shaker: 16th notes straight

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
  arc: constant high energy — no dynamic arc
  narrative: |
    Full stadium energy. Nobody is standing still. The dembow is a spell.
  spatial_image: kick and bass center, synth stabs wide, perc left and right

Texture:
  density: high
  register_spread: Bb0-Bb4

MidiExpressiveness:
  pitch_bend:
    style: bass slides between chord roots
    depth: 1-2 semitones
  cc_curves:
    - cc: 91
      from: 25
      to: 55
      position: bars 1-8
    - cc: 74
      from: 50
      to: 95
      position: bars 1-8
  expression:
    curve: constant hard f
    range: [95, 120]
""",
    ),

    # 16 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="classical_string_quartet",
        title="String quartet \u00b7 G major \u00b7 76 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: classical chamber \u00b7 Key: G \u00b7 76 BPM\nRole: violin I, violin II, viola, cello\nVibe: intimate x3, bittersweet x2, peaceful, flowing",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: classical chamber
Key: G
Tempo: 76
Role: [violin I, violin II, viola, cello]
Constraints:
  bars: 8
Vibe: [intimate x3, bittersweet x2, peaceful, flowing]

Request: |
  String quartet movement — melodic Violin I over Gmaj-Em-Cmaj-D7
  with imitative counterpoint from Violin II and Viola answering in
  canon at 2 beats delay. Cello provides the harmonic foundation with
  a walking bass line. Late Haydn meets early Beethoven. Expressive but
  restrained — every note has a reason.

Harmony:
  progression: [Gmaj7, Em7, Cmaj7, D7sus4, D7]
  voicing: four-voice SATB texture throughout
  rhythm: cello on all quarter notes, inner voices half notes, melody free
  extensions: 7ths throughout, 9th on D7sus4
  tension:
    point: bar 6
    device: D7sus4 suspension held 1 full bar
    release: D7 resolving to G on bar 7

Melody:
  scale: G major with chromatic passing tones
  register: Violin I — D4-D6
  contour: arch form — rises bars 1-4, peaks bar 5, descends bars 6-8
  phrases:
    structure: antecedent 4 bars, consequent 4 bars
    breath: half-beat at phrase boundary
  density: medium — quarter and 8th notes, occasional 16th runs
  ornamentation:
    - turns on cadential notes
    - trills on bar 4 peak

Rhythm:
  feel: classical pulse — even, expressive
  subdivision: 8th notes
  swing: 50%
  accent:
    pattern: downbeat emphasis, secondary on beat 3

Dynamics:
  overall: mp to mf
  arc:
    - bars: 1-4
      level: mp
      shape: flat with phrase swells
    - bars: 5-6
      level: mf
      shape: peak
    - bars: 7-8
      level: mp
      shape: diminuendo
  accent_velocity: 92
  ghost_velocity: 45

Orchestration:
  violin_I:
    role: melody
    articulation: legato with slight bow pressure variation
    vibrato: continuous, moderate depth
  violin_II:
    role: inner voice + canon answer to Violin I at 2 beats
    register: G3-G5
    articulation: slightly lighter than Violin I
  viola:
    role: inner voice, fills harmony
    register: C3-C5
    articulation: warm, supportive
  cello:
    role: bass line
    register: C2-C4
    technique: sustained quarter notes, legato

Effects:
  strings:
    reverb: intimate concert hall, 1.8s, 12ms predelay
    compression: program-dependent, very gentle

Expression:
  arc: contemplation through uncertainty to resolution
  narrative: |
    A question asked in music. The answer takes all 8 bars to arrive.
    When it does — D7 resolving to G — it feels earned.
  character: Haydn's clarity, Beethoven's emotional depth

Texture:
  density: medium — four-voice counterpoint
  register_spread: C2-D6
  layering:
    strategy: cello anchors bass, violas fill middle, violins carry line
  space: classical texture — every voice is always heard, none dominate

MidiExpressiveness:
  sustain_pedal:
    style: no sustain pedal — strings don't use damper
    changes_per_bar: 0
  expression:
    curve: phrase swells — follows melodic contour
    range: [55, 100]
  modulation:
    instrument: all strings
    depth: vibrato — CC 1 constant 45-65
    onset: immediate
  cc_curves:
    - cc: 91
      from: 30
      to: 55
      position: bars 1-8
    - cc: 1
      from: 45
      to: 65
      position: bars 1-8
  pitch_bend:
    style: classical string intonation — very subtle vibrato pitch variation
    depth: microtonal
  articulation:
    legato: true
    portamento:
      time: 20
      switch: on
""",
    ),

    # 17 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="hypnotic_psytrance",
        title="Hypnotic psytrance \u00b7 Am \u00b7 145 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: psytrance \u00b7 Key: Am \u00b7 145 BPM\nRole: kick, bass, lead, atmosphere\nVibe: hypnotic x4, tense x2, intense, driving",
        full_prompt="""STORI PROMPT
Mode: compose
Section: drop
Style: psytrance
Key: Am
Tempo: 145
Role: [kick, bass, lead, atmosphere]
Constraints:
  bars: 16
Vibe: [hypnotic x4, tense x2, intense, driving, mysterious]

Request: |
  Hypnotic psytrance — pounding 145 BPM four-on-the-floor kick, a thick
  rolling bass line in Am with heavy distortion and filter sweeps, a
  psychedelic lead sequence using Am-G-F-E with glitching pitch modulation,
  and a wide atmospheric pad. The lead should feel slightly mechanical and
  slightly alien. Repeating 4-bar motif with variations on every repetition.

Harmony:
  progression: [Am, G, F, E]
  voicing: power chords on bass, open 5ths on lead
  rhythm: bass on every 16th note (machine-gun pattern), lead on 8th notes
  extensions: none — bare and aggressive

Melody:
  scale: A phrygian with chromatic alterations
  register: upper (E5-A6)
  contour: short ascending motif, repeating with pitch variations each cycle
  phrases:
    structure: 4-bar motif, 4 variations over 16 bars
  density: dense — continuous 16th-note pattern

Rhythm:
  feel: mechanical precision
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: kick on every quarter note, bass on every 16th
  polyrhythm:
    lead: 3-note motif against 4 beats creates phase shifting effect

Dynamics:
  overall: fff throughout
  accent_velocity: 127
  ghost_velocity: 60
  automation:
    - param: filter_cutoff
      from: 300hz
      to: 8khz
      position: bars 1-8
      curve: Exp
    - param: filter_cutoff
      from: 8khz
      to: 300hz
      position: bars 9-16
      curve: Log

Orchestration:
  kick:
    style: punchy psytrance kick, 909 with sub
    pitch: F0, very short
  bass:
    technique: distorted sawtooth, 16th-note gating
    register: A0-A1
    filter: heavy resonant sweep
    distortion: heavy drive, clipping
  lead:
    technique: FM synthesis, 3-operator
    filter: bandpass, sweeping
    modulation: pitch modulation ±2 semitones at 4Hz

Effects:
  kick:
    compression: hard limiter
  bass:
    distortion: very heavy
    filter: resonant lowpass, fast LFO at 2Hz
  lead:
    delay: 1/16 note, 30% wet, self-oscillating
    reverb: medium plate, 1s
  atmosphere:
    reverb: huge — 8s decay

Expression:
  arc: relentless hypnotic pressure — no release
  narrative: |
    You are inside the machine now. The machine is inside you. The pattern
    repeats but it is never the same twice. You will figure out why later.

Texture:
  density: very dense
  register_spread: A0-A6
  space: none — total saturation is the aesthetic

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: heavy pitch modulation — CC 1 value 80-120
    onset: immediate
  filter:
    cutoff:
      sweep: complex — up bars 1-8, down bars 9-16
      resonance: very high
  cc_curves:
    - cc: 74
      from: 30
      to: 120
      position: bars 1-8
    - cc: 74
      from: 120
      to: 30
      position: bars 9-16
    - cc: 91
      from: 40
      to: 80
      position: bars 1-16
    - cc: 1
      from: 80
      to: 120
      position: bars 1-16
  pitch_bend:
    range: +-2 semitones
    style: rapid psychedelic pitch sweeps on lead
    depth: full range
""",
    ),

    # 18 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="indie_folk_ballad",
        title="Indie folk ballad \u00b7 G \u00b7 70 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: indie folk \u00b7 Key: G \u00b7 70 BPM\nRole: acoustic guitar, piano, bass, melody\nVibe: intimate x3, melancholic x2, nostalgic, peaceful",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: indie folk
Key: G
Tempo: 70
Role: [acoustic guitar, piano, bass, melody]
Constraints:
  bars: 8
Vibe: [intimate x3, melancholic x2, nostalgic, peaceful, personal]

Request: |
  Quiet indie folk verse — fingerpicked acoustic guitar on G-Em-C-D,
  a sparse piano adding 9th chords on the downbeats, upright bass walking
  quietly underneath, and a breathy lead vocal-style melody in the upper
  register. This is for 2am listening. No drums. Maximum space.

Harmony:
  progression: [Gmaj9, Em9, Cmaj7, Dsus2]
  voicing: open, airy — capo 2nd fret guitar sound, piano adds 9ths
  rhythm: guitar strums 6/8 feel in 4/4, piano hits beats 1 and 3 only
  extensions: 9ths and sus2s throughout — nothing fully resolved

Melody:
  scale: G major pentatonic with added 6th
  register: upper-mid (D4-G5)
  contour: gently arching phrases, always ending slightly unresolved
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
  overall: pp to mp
  arc:
    - bars: 1-4
      level: pp
      shape: flat
    - bars: 5-8
      level: p to mp
      shape: gentle swell
  accent_velocity: 72
  ghost_velocity: 30

Orchestration:
  guitar:
    technique: fingerpicked — thumb + 3 fingers, Travis picking pattern
    voicing: full chord shapes, some open strings
    articulation: legato, let notes ring naturally
  piano:
    voicing: right hand — 9th chord in upper register
    rhythm: beats 1 and 3 only, very soft
    pedaling: half pedal throughout
  bass:
    technique: upright feel, bowed occasionally
    register: G1-G2
    articulation: mostly sustained, occasional pizzicato

Effects:
  guitar:
    reverb: small room, 0.8s, 8ms predelay
  piano:
    reverb: same room, gentle
  bass:
    reverb: very subtle

Expression:
  arc: quiet grief becoming quiet acceptance
  narrative: |
    Sitting with something you cannot change. Not fighting it anymore.
    Just being with it. The music doesn't try to fix anything.
    It just stays.
  character: Elliott Smith's intimacy. Sufjan Stevens' space.

Texture:
  density: very sparse
  register_spread: G1-G5
  space:
    principle: |
      Every silence is load-bearing. The song exists in the spaces
      between the notes as much as in the notes themselves.

MidiExpressiveness:
  sustain_pedal:
    style: half pedal catches on piano — sustain chord tones
    changes_per_bar: 3
  expression:
    curve: slow swell pp to mp over 8 bars
    range: [28, 72]
  pitch_bend:
    style: vocal slides on melody — up into phrases, down at ends
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 25
      to: 50
      position: bars 1-8
    - cc: 11
      from: 28
      to: 72
      position: bars 1-8
  articulation:
    legato: true
    soft_pedal: bars 1-4
""",
    ),

    # 19 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="second_line_brass",
        title="New Orleans second line \u00b7 F \u00b7 98 BPM",
        preview="Mode: compose \u00b7 Section: chorus\nStyle: New Orleans brass / second line \u00b7 Key: F \u00b7 98 BPM\nRole: drums, tuba, trumpet, trombone, sax\nVibe: joyful x4, groovy x2, bouncy, energetic",
        full_prompt="""STORI PROMPT
Mode: compose
Section: chorus
Style: New Orleans brass second line
Key: F
Tempo: 98
Role: [drums, tuba, trumpet, trombone, sax]
Constraints:
  bars: 8
Vibe: [joyful x4, groovy x2, bouncy, energetic, triumphant]

Request: |
  New Orleans second line brass band chorus — the snare is doing the
  second-line shuffle (syncopated 16th notes), tuba on the bass line,
  trumpet playing the melody, trombone doing countermelody fills,
  alto sax adding call-and-response. F7-Bb7-C7 blues changes. Pure joy.

Harmony:
  progression: [F7, Bb7, F7, C7, Bb7, F7, C7, F7]
  voicing: blues seventh chords throughout
  rhythm: horn stabs on upbeats, tuba on all downbeats
  extensions: dominant 7ths — nothing fancy, pure blues

Melody:
  scale: F blues scale
  register: Trumpet — C4-C6
  contour: bright, ascending phrases — always ending on a high note
  phrases:
    structure: 2-bar call, 2-bar response between trumpet and sax
  density: medium — clear melodic statements with space

Rhythm:
  feel: ahead of the beat — New Orleans bounce
  subdivision: 16th notes
  swing: 56%
  accent:
    pattern: second-line shuffle — syncopated snare, bass drum on 1
  ghost_notes:
    instrument: snare
    velocity: 40-60
  pushed_hits:
    - beat: 2.75
      anticipation: 16th note early

Dynamics:
  overall: f to ff
  accent_velocity: 112
  ghost_velocity: 45

Orchestration:
  drums:
    snare: second-line shuffle — syncopated 16th pattern
    bass_drum: on 1, 2 and 4 (parade pattern)
    cymbals: crash on downbeats, ride throughout
  tuba:
    register: F0-F2
    technique: walking bass line, every quarter note
  trumpet:
    role: melody and high fills
    register: Bb3-F5
    articulation: bright, clear tone — no mutes
  trombone:
    role: countermelody and riffs between trumpet phrases
    register: Bb1-F4
    articulation: slide portamento between notes
  alto_sax:
    role: response to trumpet calls
    register: Bb3-Bb4
    articulation: bright, bluesy

Effects:
  brass:
    reverb: outdoor street reverb — medium hall, 1.2s
    compression: gentle, preserve dynamics

Expression:
  arc: pure celebration — nothing else
  narrative: |
    Everyone on the street is family right now. The music doesn't know
    about sadness today. Follow the tuba. Let your feet figure it out.
  spatial_image: drums center, tuba center, trumpet right, trombone left, sax right

Texture:
  density: high — full brass band
  register_spread: F0-C6
  space: every instrument has its lane — this is disciplined joy

MidiExpressiveness:
  expression:
    curve: constant bright f
    range: [88, 115]
  pitch_bend:
    style: trombone slides between notes — half-tone portamento
    depth: 1 semitone
  cc_curves:
    - cc: 91
      from: 25
      to: 50
      position: bars 1-8
  articulation:
    portamento:
      time: 35
      switch: on
  aftertouch:
    type: channel
    response: adds brightness on high notes
    use: expression boost
""",
    ),

    # 20 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="nordic_ambient_folk",
        title="Nordic ambient folk \u00b7 Em \u00b7 63 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: Nordic ambient folk \u00b7 Key: Em \u00b7 63 BPM\nRole: folk strings, piano, vocals, drone\nVibe: atmospheric x3, melancholic x2, intimate, dreamy",
        full_prompt="""STORI PROMPT
Mode: compose
Section: intro
Style: Nordic ambient folk
Key: Em
Tempo: 63
Role: [folk strings, piano, vocals, drone]
Constraints:
  bars: 12
Vibe: [atmospheric x3, melancholic x2, intimate, dreamy, peaceful]

Request: |
  Haunting Nordic folk intro — a lone cello playing a slow dorian melody
  over an open-string drone, piano adding sparse whole-note chords in
  Em-C-G-D, wordless vocal hums entering in bar 5, and a deep open-string
  drone on E0. Like Hauschka meets Nils Frahm on a winter evening.

Harmony:
  progression: [Em, C, G, Dsus2]
  voicing: open — only root and 5th, no 3rds
  rhythm: whole-note sustains, changing only on bar downbeats
  extensions: sus2s — open, unresolved, Scandinavian

Melody:
  scale: E dorian
  register: Cello — D3-A4
  contour: slow descending spiral, never quite resolving
  phrases:
    structure: free, non-metric — phrases breathe organically
    breath: long — each phrase 3-4 bars
  density: very sparse — one note every 2-3 beats

Rhythm:
  feel: free, non-metric — pulse is felt not counted
  subdivision: quarter notes
  swing: 50%

Dynamics:
  overall: ppp to mp over 12 bars
  arc:
    - bars: 1-4
      level: ppp
      shape: flat — barely audible
    - bars: 5-8
      level: pp
      shape: gradual entry of vocals
    - bars: 9-12
      level: mp
      shape: gentle peak
  accent_velocity: 65
  ghost_velocity: 20

Orchestration:
  folk_strings:
    instrument: solo cello
    technique: long bowing, sul tasto (near fingerboard) — softer tone
    vibrato: slow, narrow, delayed onset
  piano:
    voicing: right hand only — open 5ths in mid register
    articulation: extremely soft, barely struck
    pedaling: full sustain throughout
  vocals:
    style: wordless, low hum — mm and ah sounds
    entry: bar 5
    register: E3-E4 (mezzo soprano, mid register)
  drone:
    pitch: E0
    technique: pure low string resonance

Effects:
  strings:
    reverb: large stone church, 5s decay, 40ms predelay
    filter: gentle lowpass at 4khz
  piano:
    reverb: same church space
  vocals:
    reverb: same space, 70% wet
    filter: gentle air at 8khz

Expression:
  arc: silence to gentle presence — the sound of winter light
  narrative: |
    Snow falling on still water. No wind. The world has slowed to the
    pace of breath. Something ancient is watching without judgment.
  spatial_image: everything diffuse and wide — no clear center, just space

Texture:
  density: very sparse to sparse
  register_spread: E0-A4
  space:
    principle: |
      The silence between each note is as important as the note itself.
      This music is mostly silence. The notes are just landmarks.

MidiExpressiveness:
  expression:
    curve: slow swell ppp to mp
    range: [15, 65]
  modulation:
    instrument: cello
    depth: slow vibrato — CC 1 from 0 to 30 over 8 bars
    onset: bars 5-12 only
  cc_curves:
    - cc: 91
      from: 50
      to: 80
      position: bars 1-12
    - cc: 1
      from: 0
      to: 30
      position: bars 5-12
  sustain_pedal:
    style: full sustain throughout on piano
    changes_per_bar: 0
  pitch_bend:
    style: subtle cello intonation — microtonal
    depth: microtonal
""",
    ),

    # 21 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="flamenco_fusion",
        title="Flamenco fusion \u00b7 Am phrygian \u00b7 176 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: flamenco fusion \u00b7 Key: Am phrygian \u00b7 176 BPM\nRole: guitar, cajon, bass, pad\nVibe: intense x3, tense x2, driving, mysterious",
        full_prompt="""STORI PROMPT
Mode: compose
Section: verse
Style: flamenco fusion
Key: A phrygian
Tempo: 176
Role: [guitar, cajon, bass, pad]
Constraints:
  bars: 8
Vibe: [intense x3, tense x2, driving, mysterious, dark]

Request: |
  Flamenco fusion verse — rapid Phrygian guitar runs in the Andalusian
  cadence (Am-G-F-E), cajon with heavy palmas-style ghost notes, a deep
  electronic bass adding modern weight under the roots, and a dark
  atmospheric pad. The guitar plays rasgueado strumming on the E chord.
  Ancient meets modern. Flamenco fire, electronic depth.

Harmony:
  progression: [Am, G, F, E]
  voicing: Andalusian cadence — descending in phrygian mode
  rhythm: flamenco rhythmic cycle (compas) — 12-beat cycle
  extensions: E major chord (not minor) — Phrygian harmonic surprise

Melody:
  scale: A phrygian
  register: Guitar — E3-E6
  contour: rapid ascending runs, falling with ornaments
  phrases:
    structure: 4-bar falseta, repeated with variations
  density: very dense — 16th and 32nd note runs
  ornamentation:
    - rasgueado on E chord
    - picado runs on A phrygian scale
    - tremolo on sustained notes

Rhythm:
  feel: flamenco compas — 12-beat cycle with complex accent patterns
  subdivision: 16th notes, triplet feel
  swing: 53%
  accent:
    pattern: flamenco 12-beat cycle (1,2,3,4,5,6,7,8,9,10,11,12) accented on 3,6,8,10,12
  ghost_notes:
    instrument: cajon
    velocity: 30-65

Dynamics:
  overall: mf to fff
  arc:
    - bars: 1-2
      level: mf
      shape: flat
    - bars: 3-6
      level: f
      shape: building
    - bars: 7-8
      level: fff
      shape: explosive rasgueado climax

Orchestration:
  guitar:
    technique: classical + flamenco — picado runs, rasgueado strums, tremolo
    articulation: extremely fast, percussive attacks on strums
  cajon:
    technique: hands — bass tone on 1 and 7, slap tone on 3 and 8
    ghost_notes: constant 16th-note ghost pattern
  bass:
    technique: picked electric, deep sub
    register: A0-A2
    articulation: sustained, follows guitar roots
  pad:
    instrument: dark analog pad
    voicing: open 5ths, phrygian atmosphere
    attack: slow 1s

Effects:
  guitar:
    reverb: small stone room, 0.8s
    compression: very gentle — preserve transients
  bass:
    distortion: subtle saturation
    eq:
      - band: sub
        freq: 80hz
        gain: +3db
  pad:
    reverb: large dark hall, 4s

Expression:
  arc: controlled tension erupting into release
  narrative: |
    Duende — the dark mysterious spirit of flamenco. The music comes from
    somewhere below rational thought. The rasgueado at bar 7 is not played.
    It erupts.
  tension_points:
    - bar: 6
      device: held E major chord, suspended in time
    - bar: 7
      device: full rasgueado eruption

Texture:
  density: medium to very dense
  register_spread: A0-E6
  space: bars 1-2 have breathing room, bars 7-8 have none

MidiExpressiveness:
  expression:
    curve: builds from mf to fff
    range: [70, 127]
  pitch_bend:
    style: guitar bends on ornamental notes — half-tone to whole-tone
    depth: 1 semitone
  cc_curves:
    - cc: 91
      from: 20
      to: 60
      position: bars 1-8
    - cc: 74
      from: 40
      to: 100
      position: bars 1-8
  aftertouch:
    type: channel
    response: adds sustain on sustained guitar tones
    use: tremolo expression
  articulation:
    portamento:
      time: 20
      switch: on
""",
    ),

    # 22 ─────────────────────────────────────────────────────────────────────
    PromptItem(
        id="uk_garage_steppers",
        title="UK garage steppers \u00b7 Dbm \u00b7 130 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: UK garage / 2-step \u00b7 Key: Dbm \u00b7 130 BPM\nRole: drums, bass, vocal chop, synth pad\nVibe: groovy x3, energetic x2, dark, atmospheric",
        full_prompt="""STORI PROMPT
Mode: compose
Section: drop
Style: UK garage 2-step
Key: Dbm
Tempo: 130
Role: [drums, bass, vocal chop, synth pad]
Constraints:
  bars: 8
Vibe: [groovy x3, energetic x2, dark, atmospheric, flowing]

Request: |
  UK garage 2-step drop — the iconic skipping kick pattern (kick on 1 and
  the and of 2, then 3 and 4-and), rolling bass on Dbm following the kick,
  a pitched vocal chop playing the minor third (E), and warm synth pad chords
  on Dbm-Ab-Bbm-Gb. Dark and soulful. Craig David era energy, updated.

Harmony:
  progression: [Dbm7, Abmaj7, Bbm7, Gbmaj7]
  voicing: close voiced 7th chords, keyboard register
  rhythm: pad stabs on upbeats — classic 2-step rhythm
  extensions: 7ths throughout — soulful and warm

Melody:
  scale: Db minor pentatonic
  register: vocal chop — Eb4-Ab4
  contour: repetitive — same 2-note pattern repeated with pitch variation
  phrases:
    structure: 2-bar hook, repeated 4x with variation
  density: medium — vocal chops every bar

Rhythm:
  feel: ahead of the beat — 2-step urgency
  subdivision: 16th notes
  swing: 52%
  accent:
    pattern: 2-step kick — not four-on-the-floor, skipping
  ghost_notes:
    instrument: snare
    velocity: 35-55

Dynamics:
  overall: f throughout
  accent_velocity: 108
  ghost_velocity: 42

Orchestration:
  drums:
    kick: 2-step pattern — beats 1, 2-and, 3, 4-and
    snare: tight rimshot on 2 and 4
    hi_hat: 16th notes, partially open
  bass:
    technique: pulsed electronic bass
    register: Db1-Db2
    articulation: follows kick rhythm exactly, same syncopation
  vocal_chop:
    pitch: Eb4 and Ab4 alternating
    style: pitched, chopped, time-stretched
    rhythm: syncopated, every bar

Effects:
  drums:
    compression: UK garage punch — tight
    saturation: subtle vinyl warmth
  bass:
    filter: resonant, slight LFO wobble at 0.5Hz
  vocal_chop:
    reverb: medium room, 0.8s
    pitch_correction: tight
  pad:
    reverb: dark hall, 2.5s

Expression:
  arc: effortless cool — no dramatic build
  narrative: |
    3am in a sweaty London club. The bass is in your chest. Everything
    is slow-motion and fast at the same time. This is what cool sounds like.
  spatial_image: drums center, bass center, vocal chop right +20, pad wide ±50

Texture:
  density: medium-high
  register_spread: Db1-Ab4
  stereo_field:
    drums: center
    bass: center
    vocal_chop: right +20
    pad: ±50

MidiExpressiveness:
  expression:
    curve: constant cool f
    range: [82, 110]
  cc_curves:
    - cc: 91
      from: 30
      to: 60
      position: bars 1-8
    - cc: 74
      from: 40
      to: 75
      position: bars 1-8
  pitch_bend:
    style: bass slides between kicks — subtle
    depth: quarter-tone
  filter:
    cutoff:
      sweep: gentle open over 4 bars
      resonance: moderate
""",
    ),
]

ALL_PROMPT_IDS: set[str] = {p.id for p in PROMPT_POOL}


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
