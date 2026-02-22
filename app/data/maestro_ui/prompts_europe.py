"""STORI PROMPT pool — Europe region.

Covers: melodic techno, liquid D&B, minimal deep house, synthwave,
post-rock, classical string quartet, psytrance, Nordic ambient folk,
flamenco fusion, UK garage, and future additions (klezmer, baroque,
Balkan brass).
"""

from app.models.maestro_ui import PromptItem

PROMPTS_EUROPE: list[PromptItem] = [

    # 2 ── Melodic techno drop ───────────────────────────────────────────────
    PromptItem(
        id="melodic_techno_drop",
        title="Melodic techno drop \u00b7 Am \u00b7 128 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: melodic techno \u00b7 Key: Am \u00b7 128 BPM\nRole: kick, bass, lead, pads, perc\nVibe: hypnotic x3, driving x2, euphoric",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: drop
Style: melodic techno
Key: Am
Tempo: 128
Energy: high
Role: [kick, bass, lead, pads, perc]
Constraints:
  bars: 32
  density: high
Vibe: [hypnotic x3, driving x2, euphoric, tense, relentless]

Request: |
  A complete melodic techno journey in three acts. 8-bar filtered buildup
  where only the kick and a distant pad exist, the filter slowly opening.
  16-bar full drop — punishing four-on-the-floor kick, deep Reese bass,
  soaring minor-key supersaw lead on A-C-E-G, and lush evolving pads.
  8-bar breakdown where the kick drops out, the lead goes solo over a
  reverb wash, then a one-bar riser slams back into the drop energy.
  The kind of track that makes a warehouse full of people lose their minds.

Harmony:
  progression: [Am, F, C, G]
  voicing: open, stacked 5ths on pads
  rhythm: |
    Buildup: whole-note pad swells, muted and distant
    Drop: full chord extensions, pad underneath lead
    Breakdown: suspended 4ths, no resolution until bar 32
  extensions: 9ths on pads, bare 5ths on bass
  tension:
    point: bar 24
    device: all harmony drops out, single suspended A note
    release: beat 1 bar 25 — breakdown begins
  reharmonize: |
    Breakdown bars 25-31 use Am9sus4 — refuses to resolve.
    Bar 32 beat 4: rising chromatic line A-Bb-B-C slams back to Am.

Melody:
  scale: A natural minor
  register: upper (A4-E6)
  contour: |
    Buildup: no melody — just filtered textures
    Drop bars 9-16: ascending sequence, 4-bar motif
    Drop bars 17-24: descending inversion with variation
    Breakdown: lead solo, free and lyrical over reverb wash
  phrases:
    structure: 4-bar motif, repeated with variation
    breath: no breath in drop — continuous 8th-note motion
  density: dense in drop, sparse in breakdown
  ornamentation:
    - pitch bend up 1 semitone on every 4th note
    - trill on bar 23 beat 3 before breakdown

Rhythm:
  feel: straight 16th grid, mechanical precision
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: four-on-the-floor kick with 16th-note hi-hat
    weight: strong downbeat emphasis
  polyrhythm:
    perc: 3-against-4 cross rhythm on rimshot throughout drop

Dynamics:
  overall: mp to fff across 32 bars
  arc:
    - bars: 1-8
      level: mp to mf
      shape: filtered buildup, exponential rise
    - bars: 9-12
      level: f
      shape: instant full energy — the drop hits
    - bars: 17-24
      level: f to fff
      shape: exponential build to peak
    - bars: 25-31
      level: mp
      shape: breakdown — everything drops, intimate
    - bars: 32
      level: fff
      shape: riser hits, instant energy return
  accent_velocity: 120
  ghost_velocity: 55
  expression_cc:
    curve: follows dynamic arc
    range: [50, 127]

Orchestration:
  kick:
    style: punchy sub kick, 909 character
    pitch: F1, slight upward pitch bend
    tail: tight, 200ms
    entry: bar 1 — present throughout buildup and drop
    exit: bar 25 — drops out for breakdown
    re_entry: bar 32 beat 1 — slams back
  bass:
    technique: Reese — detuned sawtooth x2, slight modulation
    register: A1-A2
    articulation: sustained, sidechain pumping from kick
    entry: bar 9 — arrives with the drop
  lead:
    technique: supersaw, 7 voices, 12 cents detune
    filter: resonant lowpass, cutoff sweeps open over bars 9-16
    entry: bar 9 — the drop's emotional core
    breakdown: bars 25-31, solo and exposed, long reverb tail
  pads:
    voicing: wide stereo, slow attack 800ms
    filter: gentle highpass at 300hz
    entry: bar 3 — fades in during buildup, very filtered
  perc:
    rimshot: 3-against-4 pattern, enters bar 13
    clap: layered with snare on 2 and 4, bars 9-24

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
  arc: anticipation to ecstasy to intimacy to rebirth
  narrative: |
    The buildup is a promise. Eight bars of nothing but a kick and a
    whisper of harmony — the crowd knows what is coming. When the drop
    hits at bar 9, it is not surprise. It is inevitability. The lead
    cuts through the noise like a searchlight. By bar 17 you are no
    longer thinking. By bar 24 the peak arrives. Then — silence. The
    breakdown strips everything away. Just the lead, alone, singing
    over infinite reverb. And then bar 32. The kick returns. The crowd
    erupts. You are home.
  spatial_image: |
    Buildup: kick center, pad distant and wide. Drop: bass center,
    lead slightly right, pads wide ±70, perc scattered.
    Breakdown: lead center and intimate, reverb everywhere.
  character: Afterlife label at 3am. Tale Of Us energy. Collective release.

Texture:
  density: sparse (buildup) to dense (drop) to exposed (breakdown)
  register_spread: A1-E6
  layering:
    strategy: |
      Buildup: kick owns the space, pad is atmosphere.
      Drop: kick and bass own low end, lead cuts through mid, pads fill high.
      Breakdown: lead alone, reverb fills everything else.
  stereo_field:
    kick: center
    bass: center
    lead: right +15 (drop), center (breakdown)
    pads: wide ±70
    perc: scattered ±40

Form:
  structure: buildup-drop-breakdown-drop_return
  development:
    - section: buildup (bars 1-8)
      intensity: mp rising, filtered — kick and distant pad only
    - section: drop (bars 9-24)
      variation: |
        Bars 9-16: full drop, ascending lead motif.
        Bars 17-24: intensifying, descending inversion, polyrhythm enters.
    - section: breakdown (bars 25-31)
      contrast: kick drops out, lead goes solo over reverb wash
    - section: drop_return (bar 32)
      variation: one-bar riser, kick and full energy slam back
  variation_strategy: |
    The buildup teases. The drop delivers. The breakdown makes you
    remember what it felt like to want. The return makes you forget
    you ever stopped.

Humanization:
  timing:
    jitter: 0.01
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 6
    accents:
      beats: [0]
      strength: 12
    ghost_notes:
      probability: 0.03
      velocity: [50, 65]
  feel: on the grid — techno is precise

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: strong vibrato — CC 1 value 60-90
    onset: immediate in drop, delayed 2 beats in breakdown
  filter:
    cutoff:
      sweep: |
        Buildup bars 1-8: 200hz to 2khz (slow opening)
        Drop bars 9-16: 2khz to 8khz (full open)
        Breakdown bars 25-31: 8khz to 1khz (closing)
      resonance: moderate buildup, high in breakdown
  cc_curves:
    - cc: 74
      from: 15
      to: 110
      position: bars 1-16
    - cc: 74
      from: 110
      to: 40
      position: bars 25-31
    - cc: 91
      from: 20
      to: 90
      position: bars 1-24
    - cc: 91
      from: 90
      to: 60
      position: bars 25-31
    - cc: 1
      from: 0
      to: 90
      position: bars 9-24
  aftertouch:
    type: channel
    response: adds filter opening and volume swell on lead synth
    use: filter cutoff + amplitude — harder pressure brightens and lifts the lead
  pitch_bend:
    range: +-2 semitones
    style: upward bends on phrase peaks in drop
    depth: half-tone

Automation:
  - track: Lead
    param: filter_cutoff
    events:
      - beat: 0
        value: 200hz
      - beat: 32
        value: 2khz
        curve: exp
      - beat: 64
        value: 8khz
        curve: exp
      - beat: 96
        value: 1khz
        curve: smooth
  - track: Pads
    param: reverb_wet
    events:
      - beat: 0
        value: 0.8
      - beat: 32
        value: 0.3
        curve: smooth
      - beat: 96
        value: 0.9
        curve: linear
  - track: Master
    param: highpass
    events:
      - beat: 0
        value: 400hz
      - beat: 32
        value: 20hz
        curve: exp
      - beat: 96
        value: 200hz
        curve: linear
      - beat: 128
        value: 20hz
""",
    ),

    # 11 ── Liquid drum & bass ───────────────────────────────────────────────
    PromptItem(
        id="drum_and_bass",
        title="Liquid drum & bass \u00b7 Dm \u00b7 174 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: liquid drum & bass \u00b7 Key: Dm \u00b7 174 BPM\nRole: drums, reese bass, pad, melody\nVibe: flowing x2, melancholic x2, driving, energetic",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: drop
Style: liquid drum and bass
Key: Dm
Tempo: 174
Energy: high
Role: [drums, reese bass, pad, melody]
Constraints:
  bars: 24
Vibe: [flowing x2, melancholic x2, driving, energetic, atmospheric, beautiful]

Request: |
  A full liquid D&B piece in three sections. 8-bar intro — lush pad alone
  on Dm9, filter slowly opening, setting the emotional tone. Then a single
  snare hit on bar 8 beat 4 announces the drop. 8-bar drop — classic
  Amen-break drums at 174 BPM (half-time feel 87), thick Reese bass on D1,
  the pad continues, and a wistful vocal-style lead melody enters. 8-bar
  second drop — the melody ascends, the drums add rolls, a counter-melody
  enters, and the emotional intensity peaks. Fast but emotional — the
  liquid DnB signature. Hospital Records at sunrise.

Harmony:
  progression: [Dm9, Bbmaj9, Fmaj7, Cm7]
  voicing: open spread, lots of air
  rhythm: |
    Intro: whole-note pad swells only.
    Drop 1: pad sustains, bass drives the bottom.
    Drop 2: pad opens up, harmony becomes more luminous.
  extensions: 9ths throughout, add 11th on Fmaj7 in drop 2

Melody:
  scale: D natural minor
  register: upper-mid (F4-A5)
  contour: |
    Intro: no melody — just the pad breathing.
    Drop 1: lyrical stepwise melody, vocal feel, 4-bar phrases.
    Drop 2: melody ascends, wider intervals, peaks on A5 at bar 20.
  phrases:
    structure: 4-bar phrases with 2-beat breath
  density: zero (intro), medium (drop 1), high (drop 2)

Rhythm:
  feel: locked on grid — DnB precision
  subdivision: 8th notes at 174 BPM (sounds like quarters at 87)
  swing: 52%
  accent:
    pattern: Amen-style — kick on 1, snare on 2 and 3, rolling 16ths
  ghost_notes:
    instrument: snare roll
    velocity: 28-52

Dynamics:
  overall: pp to ff
  arc:
    - bars: 1-8
      level: pp to p
      shape: slow swell — pad filter opening
    - bars: 9-12
      level: f
      shape: instant — the drop hits
    - bars: 13-16
      level: f
      shape: sustained, melody emotional
    - bars: 17-20
      level: f to ff
      shape: second drop intensifies
    - bars: 21-24
      level: ff
      shape: peak energy, relentless beauty
  accent_velocity: 115
  ghost_velocity: 35

Orchestration:
  drums:
    style: Amen break inspired — chopped and rolled
    kick: 1 and 3, tight sub kick
    snare: 2 and 4 with rolls leading into each bar
    hi_hat: 16th notes, alternating velocity
    entry: bar 9 (single snare hit bar 8 beat 4 as announcement)
    drop_2: add extra rolls and crash cymbals from bar 17
  reese_bass:
    technique: detuned sawtooth, heavy frequency modulation
    register: D1-D2
    modulation: slow filter sweep, resonance at 60%
    entry: bar 9
  pad:
    style: warm analog string pad
    attack: slow 1.5s
    stereo: very wide \u00b165
    entry: bar 1 — owns the intro
  melody:
    instrument: vocal-style synth, warm and breathy
    register: F4-A5
    entry: bar 11 — enters mid-drop 1
  counter_melody:
    instrument: piano, sparse single notes
    register: C5-D6
    entry: bar 17 — drop 2 only

Effects:
  drums:
    compression: ultra-fast FET, heavy limiting
    saturation: subtle
  reese_bass:
    filter: resonant lowpass, slow LFO at 0.3Hz
    distortion: light saturation
  pad:
    reverb: hall, 4s decay
  melody:
    reverb: plate, 2s, 30% wet
    delay: dotted 8th, 15% wet

Expression:
  arc: stillness to momentum to catharsis
  narrative: |
    The intro is standing on a hill at 5am watching the horizon. The
    pad is the sky before sunrise. When the drop hits at bar 9, you
    are running — not away from anything, toward something. The melody
    at bar 11 is what you are running toward. By drop 2, the counter-
    melody arrives and you realize someone is running with you. You are
    not alone. The music doesn't stop for feelings — it carries them.
  spatial_image: |
    Intro: pad wide and everywhere. Drop 1: drums center, bass center,
    pad wide, melody slightly left. Drop 2: counter-melody right,
    everything slightly wider.
  character: Hospital Records at sunrise. London Elektricity's heart.
    Calibre's depth.

Texture:
  density: sparse (intro) to medium-high (drop 1) to dense (drop 2)
  register_spread: D1-D6
  stereo_field:
    drums: center
    bass: center
    pad: wide \u00b165
    melody: left -10
    counter_melody: right +20

Form:
  structure: intro-drop-drop_2
  development:
    - section: intro (bars 1-8)
      intensity: pp — pad alone, filter opening, single snare announce bar 8
    - section: drop (bars 9-16)
      variation: drums and bass enter, melody enters bar 11
    - section: drop_2 (bars 17-24)
      contrast: drums intensify, counter-melody enters, emotional peak
  variation_strategy: |
    The intro is the breath before the run. Drop 1 is the run. Drop 2
    is the reason you are running. Each section adds emotion, not just
    energy.

Humanization:
  timing:
    jitter: 0.02
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 8
    accents:
      beats: [0, 2]
      strength: 12
    ghost_notes:
      probability: 0.08
      velocity: [25, 48]
  feel: on the grid — DnB is precise

MidiExpressiveness:
  modulation:
    instrument: reese bass
    depth: heavy filter modulation — CC 1 value 55-100
    onset: immediate
  cc_curves:
    - cc: 74
      from: 20
      to: 100
      position: bars 1-24
    - cc: 91
      from: 25
      to: 72
      position: bars 1-24
    - cc: 1
      from: 55
      to: 100
      position: bars 9-24
  aftertouch:
    type: channel
    response: opens pad filter for warmth on sustained chords
    use: filter cutoff — pressure reveals upper harmonics on the pad
  pitch_bend:
    style: bass filter resonance slides
    depth: 1 semitone

Automation:
  - track: Pad
    param: filter_cutoff
    events:
      - beat: 0
        value: 400hz
      - beat: 32
        value: 4khz
        curve: exp
  - track: Melody
    param: reverb_wet
    events:
      - beat: 40
        value: 0.2
      - beat: 64
        value: 0.35
        curve: smooth
""",
    ),

    # 12 ── Minimal deep house ───────────────────────────────────────────────
    PromptItem(
        id="minimal_deep_house",
        title="Minimal deep house \u00b7 Am \u00b7 122 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: minimal deep house \u00b7 Key: Am \u00b7 122 BPM\nRole: kick, bass, chord stab, perc, texture\nVibe: hypnotic x3, atmospheric x2, minimal, groovy",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: drop
Style: minimal deep house
Key: Am
Tempo: 122
Energy: medium
Role: [kick, bass, chord stab, perc, texture]
Constraints:
  bars: 24
Vibe: [hypnotic x3, atmospheric x2, minimal, groovy, dark, patient]

Request: |
  A full minimal deep house piece in three sections. 8-bar intro — kick
  alone, four-on-the-floor, filtered. The sub bass fades in across bars
  5-8. Just the heartbeat. 8-bar main groove — the Am7 chord stab enters
  on the upbeat of beat 2 (every other bar), shuffled hi-hat triplets,
  rim-click on beat 3. Minimal and patient. 8-bar evolution — a granular
  texture enters, the filter opens slightly, the chord stab rhythm
  doubles, and a subtle percussive loop adds polyrhythmic tension. The
  groove evolves without ever breaking. 3am in Berlin. The believers
  are left.

Harmony:
  progression: [Am7]
  voicing: just the 7th — A + G, no 3rd, no 5th
  rhythm: |
    Intro: none — kick and sub only.
    Main: single stab on upbeat of beat 2, every other bar.
    Evolution: stab rhythm doubles — every bar, adds subtle 3rd (C).
  extensions: bare 7th main groove, Am9 in evolution

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
    perc: 3-against-4 on hi-hat against kick throughout
    evolution: additional 5-against-4 percussive loop from bar 17

Dynamics:
  overall: mp to f
  arc:
    - bars: 1-4
      level: mp
      shape: kick alone, filtered
    - bars: 5-8
      level: mp to mf
      shape: sub bass fades in
    - bars: 9-16
      level: mf
      shape: main groove, hypnotic stasis
    - bars: 17-24
      level: mf to f
      shape: evolution — filter opens, texture enters
  accent_velocity: 115
  ghost_velocity: 38

Orchestration:
  kick:
    style: deep sub kick, 909 character
    pitch: A0, very short attack
    tail: 400ms
    entry: bar 1 — the only element from the start
  bass:
    technique: sustained sine wave sub
    register: A0-A1
    articulation: held whole notes with subtle pitch variation
    entry: bar 5 — fades in across 4 bars
  chord_stab:
    instrument: vintage organ
    voicing: bare 7th, staccato
    rhythm: |
      Main: offbeat hit, bars 9/11/13/15 only.
      Evolution: every bar from bar 17, adds C for Am9.
    entry: bar 9
  perc:
    hi_hat: shuffled triplet, 16th notes, entry bar 9
    rim: on beat 3, every bar, entry bar 9
    loop: percussive 5-against-4 pattern, entry bar 17
  texture:
    style: granular, metallic, sparse
    entry: bar 17

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
    reverb: dark cave, 2.5s
  texture:
    reverb: infinite, 70% wet
    filter: bandpass at 2khz, narrow

Expression:
  arc: heartbeat to hypnosis to evolution
  narrative: |
    Bar 1 is a heartbeat. Nothing else. By bar 9 you have forgotten
    you are listening to music — you are just moving. By bar 17 the
    groove has evolved without you noticing. A texture has arrived. A
    rhythm has shifted. Time stopped and you did not notice when.
    The music disappears and you are just moving. The kick is a
    heartbeat. The bass is gravity. You are inside it now.
  spatial_image: |
    Intro: kick center, nothing else. Main: kick center, bass center,
    stab slightly right, perc scattered. Evolution: texture fills the
    edges, everything slightly wider.
  character: Ricardo Villalobos' patience. The Berghain sound system.
    3am for the believers.

Texture:
  density: very sparse (intro) to sparse (main) to medium-sparse (evolution)
  register_spread: A0-G3
  space: enormous — almost nothing is happening and it is everything

Form:
  structure: intro-main_groove-evolution
  development:
    - section: intro (bars 1-8)
      intensity: mp — kick alone, sub bass fades in
    - section: main_groove (bars 9-16)
      variation: chord stab, hi-hat, rim enter — hypnotic stasis
    - section: evolution (bars 17-24)
      contrast: texture enters, stab doubles, polyrhythmic loop, filter opens
  variation_strategy: |
    The groove never breaks. It only evolves. The intro strips everything
    to the essential. The main groove is perfection through reduction.
    The evolution proves that even perfection can grow.

Humanization:
  timing:
    jitter: 0.01
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 5
    accents:
      beats: [0]
      strength: 10
    ghost_notes:
      probability: 0.04
      velocity: [35, 48]
  feel: on the grid — minimal house is mechanical meditation

MidiExpressiveness:
  cc_curves:
    - cc: 74
      from: 12
      to: 65
      position: bars 1-24
    - cc: 91
      from: 18
      to: 52
      position: bars 1-24
  expression:
    curve: slow rise across sections
    range: [75, 112]
  aftertouch:
    type: channel
    response: adds velocity-mapped warmth on Rhodes stabs
    use: subtle filter opening — pressure warms the stab tone
  modulation:
    instrument: pad
    depth: subtle vibrato — CC 1 value 15-35
    onset: delayed 4 beats, bloom section only
  pitch_bend:
    style: none — minimal house is precise

Automation:
  - track: Kick
    param: highpass
    events:
      - beat: 0
        value: 200hz
      - beat: 32
        value: 20hz
        curve: exp
  - track: Bass
    param: volume
    events:
      - beat: 16
        value: 0.0
      - beat: 32
        value: 1.0
        curve: smooth
  - track: Chord_Stab
    param: filter_cutoff
    events:
      - beat: 64
        value: 1.5khz
      - beat: 96
        value: 3khz
        curve: smooth
""",
    ),

    # 13 ── Synthwave night drive ────────────────────────────────────────────
    PromptItem(
        id="synthwave_night_drive",
        title="Synthwave night drive \u00b7 Cm \u00b7 118 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: synthwave / retrowave \u00b7 Key: Cm \u00b7 118 BPM\nRole: lead, bass, pads, drums, arp\nVibe: nostalgic x3, cinematic x2, driving, dark",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: synthwave retrowave
Key: Cm
Tempo: 118
Energy: medium
Role: [lead, bass, pads, drums, arp]
Constraints:
  bars: 24
Vibe: [nostalgic x3, cinematic x2, driving, dark, mysterious, yearning]

Request: |
  A full synthwave night drive in three sections. 8-bar intro — pads
  alone, wide and luminous, Cm with major 7ths on Ab and Eb creating
  bittersweet beauty. A 16th-note arp enters bar 5, quietly pulsing.
  8-bar verse — drums enter with four-on-the-floor kick and gated
  snare, pulsing bass on C1 follows the kick, the arp continues, and
  the saw lead enters with the melody at bar 13. 8-bar chorus — the
  lead opens up, the melody ascends, the pads swell, and the arp
  pattern shifts. Pure neon and chrome nostalgia. The Midnight meets
  Kavinsky. Driving at night toward somewhere you have never been.

Harmony:
  progression: [Cm, Abmaj7, Ebmaj7, Bb7]
  voicing: open 5ths on bass, full extensions on pads
  rhythm: |
    Intro: pad swells on whole notes, arp enters bar 5.
    Verse: bass follows kick, pads sustain, lead melody.
    Chorus: everything wider, pads swell, arp shifts.
  extensions: major 7ths on Ab and Eb — cinematic luminosity over minor
  reharmonize: |
    Chorus bar 21: Bb7 becomes Bbmaj9 for unexpected warmth before
    returning to Cm. The hope in the darkness.

Melody:
  scale: C natural minor
  register: upper (G4-C6)
  contour: |
    Intro: no melody — just pads and arp atmosphere.
    Verse: ascending line bars 13-16, clear melodic statement.
    Chorus: wider intervals, reaches C6 on bar 21, then descending
    resolution. The melody becomes what you were searching for.
  phrases:
    structure: 2-bar melodic statements
  density: zero (intro), medium (verse), high (chorus)

Rhythm:
  feel: straight, metronomic — machines don't swing
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: four-on-the-floor kick, gated snare on 2 and 4

Dynamics:
  overall: pp to f
  arc:
    - bars: 1-4
      level: pp
      shape: flat — pads alone
    - bars: 5-8
      level: pp to p
      shape: arp enters, gentle build
    - bars: 9-12
      level: mf
      shape: drums and bass enter, driving
    - bars: 13-16
      level: mf
      shape: lead enters, melody stated
    - bars: 17-20
      level: mf to f
      shape: chorus lifts, pads swell
    - bars: 21-24
      level: f
      shape: peak on bar 21, gentle resolution
  accent_velocity: 105
  ghost_velocity: 42

Orchestration:
  lead:
    instrument: sawtooth synth, 3 voices detuned \u00b18 cents
    filter: resonant lowpass, slowly opening
    portamento: slight — 40ms glide between notes
    entry: bar 13
  bass:
    technique: pulsed — follows kick, single note per beat
    register: C1-C2
    articulation: staccato, tight
    entry: bar 9
  pads:
    instrument: warm analog pad, slow attack 700ms
    voicing: full chord spread \u00b12 octaves
    stereo: ultra wide \u00b180
    entry: bar 1 — owns the intro
  drums:
    kick: 808-style, tight
    snare: gated reverb — 1980s aesthetic
    hi_hat: 16th notes, closed
    entry: bar 9
  arp:
    pattern: chord tones, 16th notes, ascending
    filter: resonant lowpass, slowly opening
    entry: bar 5

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
  arp:
    delay: dotted 8th, 20% wet
    reverb: same hall as pads, 30% wet

Expression:
  arc: solitude to motion to arrival
  narrative: |
    The pads in the intro are the night sky through your windshield. The
    arp at bar 5 is the road markings passing under your wheels. When the
    drums enter at bar 9, you are driving. When the melody enters at bar
    13, you know where you are going. The chorus is arriving — not at a
    place, but at a feeling. The Bbmaj9 at bar 21 is the moment you
    realize you were never lost. You were always heading here.
  spatial_image: |
    Intro: pads ultra-wide, arp center-left. Verse: drums center, bass
    center, lead slightly right, pads wide. Chorus: everything wider,
    lead center, pads \u00b180.
  character: The Midnight's warmth. Kavinsky's drive. 1985 through
    the lens of 2025. Chrome and neon and longing.

Texture:
  density: sparse (intro) to medium (verse) to full (chorus)
  register_spread: C1-C6
  stereo_field:
    lead: right +15 (verse), center (chorus)
    bass: center
    pads: \u00b180
    arp: left -20
    drums: center

Form:
  structure: intro-verse-chorus
  development:
    - section: intro (bars 1-8)
      intensity: pp — pads alone, arp enters bar 5
    - section: verse (bars 9-16)
      variation: drums/bass enter bar 9, lead enters bar 13
    - section: chorus (bars 17-24)
      contrast: melody opens up, pads swell, Bbmaj9 surprise bar 21
  variation_strategy: |
    The intro is atmosphere. The verse is motion. The chorus is meaning.
    Each section adds purpose, not just energy.

Humanization:
  timing:
    jitter: 0.01
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: section
    stdev: 6
    accents:
      beats: [0]
      strength: 8
    ghost_notes:
      probability: 0.03
      velocity: [38, 50]
  feel: on the grid — synthwave is machines dreaming

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: slow vibrato — CC 1 from 0 to 40 over 4 bars
    onset: bars 13-24 only
  filter:
    cutoff:
      sweep: closed to open across all sections
      resonance: moderate
  cc_curves:
    - cc: 74
      from: 20
      to: 100
      position: bars 1-24
    - cc: 91
      from: 35
      to: 75
      position: bars 1-24
    - cc: 1
      from: 0
      to: 40
      position: bars 13-24
  pitch_bend:
    style: vibrato-style narrow bends on lead phrase peaks
    depth: quarter-tone
  aftertouch:
    type: channel
    response: sweeps lead synth filter on sustained notes
    use: filter cutoff — pressure opens the lowpass for neon brightness
  articulation:
    portamento:
      time: 40
      switch: on

Automation:
  - track: Pads
    param: filter_cutoff
    events:
      - beat: 0
        value: 1khz
      - beat: 64
        value: 5khz
        curve: smooth
  - track: Lead
    param: chorus_depth
    events:
      - beat: 48
        value: 0.2
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
""",
    ),

    # 14 ── Post-rock crescendo ──────────────────────────────────────────────
    PromptItem(
        id="post_rock_crescendo",
        title="Post-rock crescendo \u00b7 Em \u00b7 98 BPM",
        preview="Mode: compose \u00b7 Section: buildup\nStyle: post-rock \u00b7 Key: Em \u00b7 98 BPM\nRole: guitars, bass, drums, keys\nVibe: cinematic x3, tense x2, intense, atmospheric",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: buildup
Style: post-rock
Key: Em
Tempo: 98
Energy: building
Role: [guitars, bass, drums, keys]
Constraints:
  bars: 32
Vibe: [cinematic x3, tense x2, intense, atmospheric, triumphant, cathartic]

Request: |
  A full post-rock crescendo in four acts. 8-bar whisper — clean
  fingerpicked guitar and shimmering keys, pp, Em-G-D-Bm, each note
  placed like a prayer. 8-bar gathering — drums enter with brushes,
  bass quietly doubles the guitar an octave below, a second guitar
  adds harmonics. 8-bar storm — the clean guitars crunch into distortion,
  the drums switch to full kit with crashes, the bass gets heavy, power
  chords replace arpeggios. One beat of complete silence on bar 24 beat 4.
  8-bar catharsis — full wall of sound, two distorted guitars in unison,
  crashing cymbals, thunderous bass, keys soaring. fff. The dam has broken.
  Explosions in the Sky meets Mogwai meets Godspeed You! Black Emperor.

Harmony:
  progression: [Em, G, D, Bm]
  voicing: |
    Whisper: open 5ths, delicate.
    Gathering: 9ths added, slightly richer.
    Storm: full power chords, thick.
    Catharsis: power chords doubled in octaves, maximum width.
  rhythm: |
    Whisper: half-note arpeggios, gentle.
    Gathering: quarter-note arpeggios, building.
    Storm: driving 8th-note power chords.
    Catharsis: relentless 8th-note power chords, crashes on every 1 and 3.
  extensions: 9ths on Em and G whisper/gathering, bare 5ths storm/catharsis

Melody:
  scale: E natural minor
  register: upper guitar (B3-E5)
  contour: |
    Whisper: single arpeggiated guitar, simple and clear.
    Gathering: melody expands, second guitar adds upper harmonics.
    Storm: melody fragments into power chord riffs.
    Catharsis: guitars in unison, triumphant ascending line to E5.
  phrases:
    structure: 4-bar melodic arch, repeated and building
  density: very sparse (whisper) to relentless (catharsis)

Rhythm:
  feel: straight, building momentum throughout
  subdivision: |
    Whisper: quarter notes. Gathering: 8th notes emerge.
    Storm: driving 8th notes. Catharsis: continuous 8th notes.
  swing: 50%
  accent:
    pattern: |
      Whisper: minimal. Gathering: beats 1 and 3.
      Storm: every beat. Catharsis: crashes on 1 and 3.

Dynamics:
  overall: pp to fff over 32 bars
  arc:
    - bars: 1-8
      level: pp
      shape: flat — whisper, barely there
    - bars: 9-12
      level: p
      shape: gathering starts, drums enter
    - bars: 13-16
      level: p to mp
      shape: second guitar adds harmonics
    - bars: 17-20
      level: mf to f
      shape: storm hits, distortion enters
    - bars: 21-24
      level: f to ff
      shape: storm intensifies, silence on bar 24 beat 4
    - bars: 25-28
      level: fff
      shape: catharsis — instant full power
    - bars: 29-32
      level: fff
      shape: sustained, triumphant, final ascending line
  accent_velocity: 127
  ghost_velocity: 22

Orchestration:
  guitars:
    whisper: clean fingerpicked arpeggios — single guitar, reverb
    gathering: add second guitar with upper harmonics, still clean
    storm: both guitars crunch into heavy overdrive, power chords
    catharsis: two distorted guitars in unison, thick chorus, maximum width
  bass:
    whisper: absent
    gathering: enters bar 9, octave below guitar, very quiet
    storm: heavy picked, locked with kick, distortion
    catharsis: thunderous, driving 8th notes
  drums:
    whisper: absent
    gathering: brushes only — very quiet, bars 9-16
    storm: full kit, crash on bar 17 beat 1, driving
    catharsis: relentless, crashes on 1 and 3, floor tom rolls
  keys:
    whisper: shimmering pad, very quiet, adds air
    gathering: slightly louder, adds 9th extensions
    storm: sustained chords, adding harmonic weight
    catharsis: soaring above the guitars, cathedral reverb

Effects:
  guitars:
    whisper_gathering:
      reverb: large hall, 3s
    storm_catharsis:
      distortion: heavy — dual-channel amp sim
      chorus: wide, 3 voices
      delay: dotted 8th, 25% wet
      reverb: large hall, 2.5s
  bass:
    storm_catharsis:
      distortion: heavy overdrive
      compression: FET, fast
  keys:
    reverb: cathedral, 5s, 60% wet — the keys exist in a larger space

Expression:
  arc: prayer to gathering to storm to catharsis
  narrative: |
    The whisper is a secret you tell yourself at 3am. The gathering is
    the courage to say it out loud. The storm is everything you have been
    holding. The silence on bar 24 beat 4 — one single beat of nothing —
    is the moment before you let go. And then the catharsis. Bar 25 is
    the dam breaking. Everything you have held in, finally coming out.
    Not anger. Not sadness. Relief. Freedom. The crash on bar 25 beat 1
    is not violence. It is the sound of becoming whole.
  tension_points:
    - bar: 17
      device: distortion engages — clean to crunch in one beat
    - bar: 24
      device: full silence on beat 4 — one beat of absolute nothing
    - bar: 25
      device: full band eruption, crash cymbal, fff, wall of sound
  spatial_image: |
    Whisper: guitar center, keys wide. Gathering: guitar left, guitar 2
    right, bass center, drums back. Storm: everything wider, louder.
    Catharsis: wall of sound, everything everywhere, no space left unfilled.
  character: Explosions in the Sky's emotion. Mogwai's weight.
    Godspeed You! Black Emperor's architecture. The dam breaking.

Texture:
  density: pp whisper to fff wall of sound over 32 bars
  register_spread: E1-E5

Form:
  structure: whisper-gathering-storm-catharsis
  development:
    - section: whisper (bars 1-8)
      intensity: pp — clean guitar and keys alone
    - section: gathering (bars 9-16)
      variation: drums (brushes), bass, second guitar enter
    - section: storm (bars 17-24)
      contrast: distortion engages, power chords, full drums, silence bar 24 beat 4
    - section: catharsis (bars 25-32)
      variation: fff wall of sound, two guitars unison, keys soaring, relentless
  variation_strategy: |
    Four acts of a single emotion. The whisper is the seed. The gathering
    is the root. The storm is the growth. The catharsis is the bloom.
    The silence on bar 24 is the moment the bud opens.

Humanization:
  timing:
    jitter: 0.03
    late_bias: 0.005
    grid: 8th
  velocity:
    arc: section
    stdev: 12
    accents:
      beats: [0, 2]
      strength: 14
    ghost_notes:
      probability: 0.05
      velocity: [18, 30]
  feel: straight but human — post-rock breathes

MidiExpressiveness:
  expression:
    curve: exponential rise pp to fff
    range: [18, 127]
  cc_curves:
    - cc: 91
      from: 18
      to: 75
      position: bars 1-32
    - cc: 74
      from: 25
      to: 95
      position: bars 17-32
    - cc: 11
      from: 18
      to: 127
      position: bars 1-32
  modulation:
    instrument: guitars
    depth: vibrato on sustained notes storm/catharsis — CC 1 from 0 to 65
    onset: bars 17-32 only
  aftertouch:
    type: channel
    response: increases tremolo intensity on sustained guitar chords
    use: vibrato depth — pressure adds shimmer in storm and catharsis sections
  filter:
    cutoff:
      sweep: |
        Whisper bars 1-8: fully open, clean signal
        Storm bars 17-24: slowly closing lowpass on guitar wash
        Catharsis bars 25-32: wide open, everything unleashed
      resonance: low whisper/gathering, moderate storm/catharsis
  pitch_bend:
    style: guitar bends on phrase peaks — up 1 semitone
    depth: 1 semitone

Automation:
  - track: Guitars
    param: distortion
    events:
      - beat: 0
        value: 0.0
      - beat: 64
        value: 0.0
      - beat: 65
        value: 0.8
      - beat: 96
        value: 1.0
        curve: linear
  - track: Keys
    param: reverb_wet
    events:
      - beat: 0
        value: 0.4
      - beat: 96
        value: 0.7
        curve: smooth
  - track: Master
    param: volume
    events:
      - beat: 92
        value: 1.0
      - beat: 95
        value: 0.0
      - beat: 96
        value: 1.0
""",
    ),

    # 16 ── Classical string quartet ─────────────────────────────────────────
    PromptItem(
        id="classical_string_quartet",
        title="String quartet \u00b7 G major \u00b7 76 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: classical chamber \u00b7 Key: G \u00b7 76 BPM\nRole: violin I, violin II, viola, cello\nVibe: intimate x3, bittersweet x2, peaceful, flowing",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: classical chamber
Key: G
Tempo: 76
Energy: medium
Role: [violin I, violin II, viola, cello]
Constraints:
  bars: 32
Vibe: [intimate x3, bittersweet x2, peaceful, flowing, noble, tender]

Request: |
  A full string quartet movement in four sections. 8-bar exposition —
  Violin I states the main theme over Gmaj7-Em7, an arching melody with
  chromatic passing tones, while the cello provides a walking quarter-
  note bass line. Violin II and Viola sustain long inner voices. 8-bar
  development — the theme fragments: Violin II takes the first 4 notes
  of the theme in canon at 2 beats delay, Viola inverts the contour,
  the harmony darkens through Cmaj7-D7sus4, and tension builds toward a
  suspended D7sus4 held for a full bar. 8-bar recapitulation — the main
  theme returns in Violin I but transposed up a 4th, now brighter and
  more assured, while the cello arpeggiates. The D7 resolves to Gmaj7
  and the theme is stated with ornamental turns and trills. 8-bar coda
  — all four voices play in unison on the opening motif, then gradually
  thin out: Violin II drops out, then Viola, until Violin I and cello
  sustain a final open G octave. Late Haydn meets early Beethoven.
  Every note has a reason. Every silence is chosen.

Harmony:
  progression: |
    Exposition (1-8): [Gmaj7, Gmaj7, Em7, Em7, Gmaj7, Em7, Cmaj7, D7]
    Development (9-16): [Cmaj7, Am7, D7sus4, D7sus4, Em7, C, D7sus4, D7]
    Recap (17-24): [Gmaj7, Em7, Cmaj7, D7, Gmaj7, Em7, D7, Gmaj7]
    Coda (25-32): [Gmaj7, Gmaj7, Em7, Em7, Cmaj7, D7, Gmaj7, Gmaj7]
  voicing: four-voice SATB texture throughout
  rhythm: cello on all quarter notes, inner voices half notes, melody free
  extensions: 7ths throughout, 9th on D7sus4
  tension:
    point: bars 11-12
    device: D7sus4 suspension held for 2 full bars in development
    release: D7 resolving to Em at bar 13

Melody:
  scale: G major with chromatic passing tones
  register: Violin I — D4-D6
  contour: |
    Exposition: arch form — rises bars 1-4, peaks bar 5, descends bars 6-8.
    Development: theme fragments into imitation, intervals widen, tension.
    Recap: theme returns transposed up a 4th, brighter, with ornaments.
    Coda: unison motif, gradual thinning, final open octave.
  phrases:
    structure: antecedent 4 bars, consequent 4 bars
    breath: half-beat at phrase boundary
  density: medium — quarter and 8th notes, occasional 16th runs
  ornamentation:
    - turns on cadential notes (recap)
    - trills on bar 20 peak
    - mordents in development

Rhythm:
  feel: classical pulse — even, expressive
  subdivision: 8th notes
  swing: 50%
  accent:
    pattern: downbeat emphasis, secondary on beat 3

Dynamics:
  overall: pp to mf to pp
  arc:
    - bars: 1-8
      level: mp
      shape: gentle arch, phrase swells on bars 4-5
    - bars: 9-12
      level: mp to mf
      shape: building tension through development
    - bars: 13-16
      level: mf
      shape: peak of emotional intensity
    - bars: 17-24
      level: mf to mp
      shape: recap settles, assured and warm
    - bars: 25-28
      level: mp
      shape: unison statement, together
    - bars: 29-32
      level: mp to pp
      shape: voices drop away, final open G, silence
  accent_velocity: 92
  ghost_velocity: 42

Orchestration:
  violin_I:
    role: melody — owns the theme in exposition and recap
    articulation: legato with slight bow pressure variation
    vibrato: continuous, moderate depth
    entry: bar 1
  violin_II:
    role: |
      Exposition: inner voice, long notes. Development: takes theme
      fragment in canon at 2 beats delay. Coda: unison then drops out
      at bar 29.
    register: G3-G5
    articulation: slightly lighter than Violin I
    entry: bar 1
  viola:
    role: |
      Exposition: inner voice. Development: inverts the theme contour.
      Coda: unison then drops out at bar 30.
    register: C3-C5
    articulation: warm, supportive
    entry: bar 1
  cello:
    role: bass line throughout, arpeggiates in recap
    register: C2-C4
    technique: walking quarter notes (exposition), arpeggios (recap)
    entry: bar 1

Effects:
  strings:
    reverb: intimate concert hall, 1.8s, 12ms predelay
    compression: program-dependent, very gentle

Expression:
  arc: question to struggle to answer to silence
  narrative: |
    The exposition asks a question in G major — gentle, searching, honest.
    The development takes that question apart: fragments it, inverts it,
    suspends it on D7sus4 for two agonizing bars. The recap answers: the
    same theme, but higher, brighter, with ornaments earned through the
    struggle. And the coda — all four voices play the opening motif
    together, one last time, and then, one by one, they stop. Until only
    Violin I and cello hold an open G. The question was never the point.
    Being together was.
  character: Haydn's clarity, Beethoven's emotional depth, Schubert's
    tenderness. A 32-bar world.

Texture:
  density: medium — four-voice counterpoint
  register_spread: C2-D6
  layering:
    strategy: cello anchors bass, violas fill middle, violins carry line
  space: |
    Every voice is always heard, none dominate. In the coda, the texture
    thins deliberately — each departure is felt.

Form:
  structure: exposition-development-recap-coda
  development:
    - section: exposition (bars 1-8)
      intensity: mp — theme stated, gentle arch
    - section: development (bars 9-16)
      variation: theme fragmented, canon, inversion, D7sus4 tension
    - section: recap (bars 17-24)
      contrast: theme returns transposed up, ornaments, resolution
    - section: coda (bars 25-32)
      variation: unison motif, voices thin, open G octave, silence
  variation_strategy: |
    Exposition plants. Development uproots. Recap replants with new
    understanding. Coda lets go.

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 12
    accents:
      beats: [0, 2]
      strength: 6
    ghost_notes:
      probability: 0.02
      velocity: [32, 48]
  feel: classical pulse — human, never metronomic, always breathing

MidiExpressiveness:
  sustain_pedal:
    style: no sustain pedal — strings don't use damper
    changes_per_bar: 0
  expression:
    curve: phrase swells — follows melodic contour
    range: [45, 102]
  modulation:
    instrument: all strings
    depth: vibrato — CC 1 constant 45-65
    onset: immediate
  cc_curves:
    - cc: 91
      from: 28
      to: 58
      position: bars 1-32
    - cc: 1
      from: 42
      to: 68
      position: bars 1-32
    - cc: 11
      from: 45
      to: 102
      position: bars 1-32
  aftertouch:
    type: channel
    response: deepens vibrato depth on sustained string notes
    use: vibrato intensity — pressure adds expressive warmth to long tones
  filter:
    cutoff:
      sweep: |
        Exposition bars 1-8: fully open, natural string tone
        Development bars 9-16: subtle brightness increase on cello
        Coda bars 25-32: gentle rolloff, warmth as voices thin
      resonance: low throughout — classical clarity
  pitch_bend:
    style: classical string intonation — very subtle vibrato pitch variation
    depth: microtonal
  articulation:
    legato: true
    portamento:
      time: 20
      switch: on

Automation:
  - track: Violin_I
    param: volume
    events:
      - beat: 0
        value: 0.6
      - beat: 32
        value: 0.7
        curve: smooth
      - beat: 64
        value: 0.75
        curve: smooth
      - beat: 96
        value: 0.7
      - beat: 128
        value: 0.3
        curve: smooth
  - track: Cello
    param: reverb_wet
    events:
      - beat: 0
        value: 0.25
      - beat: 64
        value: 0.35
        curve: smooth
      - beat: 96
        value: 0.4
        curve: smooth
  - track: Master
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
      - beat: 96
        value: 0.35
        curve: smooth
      - beat: 128
        value: 0.45
        curve: smooth
""",
    ),

    # 17 ── Hypnotic psytrance ───────────────────────────────────────────────
    PromptItem(
        id="hypnotic_psytrance",
        title="Hypnotic psytrance \u00b7 Am \u00b7 145 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: psytrance \u00b7 Key: Am \u00b7 145 BPM\nRole: kick, bass, lead, atmosphere\nVibe: hypnotic x4, tense x2, intense, driving",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: drop
Style: psytrance
Key: Am
Tempo: 145
Energy: extreme
Role: [kick, bass, lead, atmosphere]
Constraints:
  bars: 32
Vibe: [hypnotic x4, tense x2, intense, driving, mysterious, alien, mechanical]

Request: |
  A full psytrance journey in four sections. 8-bar buildup — the kick
  enters alone, four-on-the-floor at 145 BPM, while a filtered bass
  line begins to pulse beneath it, sub-only, and a distant atmospheric
  pad drones on A. The filter rises slowly. 8-bar drop_1 — the bass
  filter opens fully, distorted sawtooth 16th-note gating, the lead
  enters with a 3-note ascending motif against 4 beats creating a
  phase-shifting illusion. Am-G-F-E. Maximum psychedelic energy.
  8-bar evolution — the lead motif inverts and descends, the bass
  pattern shifts to triplets creating cross-rhythm against the 4/4
  kick, the atmosphere introduces a new texture (metallic bell),
  the filter sweeps downward. 8-bar peak — everything converges:
  the lead motif combines ascending and descending, the bass returns
  to 16ths but an octave higher, the atmosphere adds white noise
  risers, and the piece reaches maximum density before cutting to
  silence on the last beat. You are inside the machine. The machine
  is inside you.

Harmony:
  progression: [Am, G, F, E]
  voicing: power chords on bass, open 5ths on lead
  rhythm: |
    Buildup: bass on quarter notes, filtered.
    Drop 1: bass on every 16th (machine-gun), lead on 8ths.
    Evolution: bass on triplets, lead on 8ths.
    Peak: bass on 16ths (octave higher), lead combines motifs.
  extensions: none — bare and aggressive

Melody:
  scale: A phrygian with chromatic alterations
  register: upper (E5-A6)
  contour: |
    Buildup: no melody — just kick and filtered bass.
    Drop 1: short ascending motif, 3 notes against 4 beats, phase shift.
    Evolution: inverted descending motif, new intervals.
    Peak: combined motif — ascending then descending, maximally complex.
  phrases:
    structure: 4-bar motif cycles, each section varies the motif
  density: sparse (buildup) to very dense (peak)

Rhythm:
  feel: mechanical precision — inhuman, relentless
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: kick on every quarter note, bass on every 16th
  polyrhythm:
    lead: 3-note motif against 4 beats creates phase shifting effect
    evolution_bass: triplets against 4/4 kick in evolution section

Dynamics:
  overall: f to fff
  arc:
    - bars: 1-8
      level: f
      shape: building — filter opens, tension rises
    - bars: 9-16
      level: ff
      shape: drop 1 — full energy
    - bars: 17-24
      level: ff
      shape: evolution — cross-rhythms, complexity
    - bars: 25-31
      level: fff
      shape: peak — maximum density
    - bar: 32
      level: silence
      shape: cut — everything stops on beat 4
  accent_velocity: 127
  ghost_velocity: 55

Orchestration:
  kick:
    style: punchy psytrance kick, 909 with sub
    pitch: F0, very short
    entry: bar 1
  bass:
    technique: distorted sawtooth, 16th-note gating
    register: A0-A1 (drop), A1-A2 (peak)
    filter: heavy resonant sweep — closed in buildup, open in drop
    distortion: heavy drive, clipping
    entry: bar 1 — filtered sub only, opens at bar 9
  lead:
    technique: FM synthesis, 3-operator
    filter: bandpass, sweeping
    modulation: pitch modulation +-2 semitones at 4Hz
    entry: bar 9
  atmosphere:
    style: drone pad in buildup, metallic bell texture in evolution
    entry: bar 1 (drone), bar 17 (bell)

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
  arc: hypnosis to madness to enlightenment to void
  narrative: |
    The buildup is a door. You step through at bar 9 and you are inside
    the machine. The pattern repeats but it is never the same twice.
    The evolution at bar 17 introduces cross-rhythms that make your body
    disagree with itself — triplets against 4/4, the phase-shifting lead
    now descending instead of ascending. Your sense of time dissolves.
    The peak at bar 25 is everything at once — maximum density, maximum
    complexity, maximum volume. And then on beat 4 of bar 32: silence.
    Complete. Sudden. The machine stops. You are still spinning.
  character: Shpongle's psychedelia. Infected Mushroom's aggression.
    Hallucinogen's alien precision. The Goa sun at 6am.

Texture:
  density: sparse (buildup) to maximum (peak) to zero (final beat)
  register_spread: A0-A6
  space: |
    Buildup has space — kick and filtered bass only. Drop fills the
    spectrum. Evolution adds metallic textures in the highs. Peak is
    total saturation. The final silence is deafening.

Form:
  structure: buildup-drop_1-evolution-peak
  development:
    - section: buildup (bars 1-8)
      intensity: f — kick and filtered bass, tension rising
    - section: drop_1 (bars 9-16)
      variation: bass filter opens, lead enters, phase-shift motif
    - section: evolution (bars 17-24)
      contrast: lead inverts, bass goes triplet, bell texture enters
    - section: peak (bars 25-32)
      variation: everything converges, maximum density, cuts to silence
  variation_strategy: |
    Each section transforms the same material. The 3-note motif is the
    DNA — ascending, then inverted, then combined. The bass is the
    heartbeat — 16ths, then triplets, then 16ths an octave higher.
    Complexity increases until it collapses.

Humanization:
  timing:
    jitter: 0.005
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 4
    accents:
      beats: [0]
      strength: 12
    ghost_notes:
      probability: 0.02
      velocity: [48, 60]
  feel: mechanical — inhuman precision is the aesthetic

MidiExpressiveness:
  modulation:
    instrument: lead
    depth: heavy pitch modulation — CC 1 value 80-120
    onset: immediate at bar 9
  cc_curves:
    - cc: 74
      from: 25
      to: 120
      position: bars 1-16
    - cc: 74
      from: 120
      to: 35
      position: bars 17-24
    - cc: 74
      from: 35
      to: 127
      position: bars 25-32
    - cc: 91
      from: 35
      to: 85
      position: bars 1-32
    - cc: 1
      from: 80
      to: 120
      position: bars 9-32
  aftertouch:
    type: channel
    response: drives acid bass filter and resonance simultaneously
    use: filter cutoff + resonance — pressure creates squelchy acid sweeps
  pitch_bend:
    range: +-2 semitones
    style: rapid psychedelic pitch sweeps on lead
    depth: full range

Automation:
  - track: Bass
    param: filter_cutoff
    events:
      - beat: 0
        value: 200hz
      - beat: 32
        value: 8khz
        curve: exp
      - beat: 64
        value: 8khz
      - beat: 96
        value: 300hz
        curve: log
  - track: Bass
    param: filter_cutoff
    events:
      - beat: 96
        value: 300hz
      - beat: 128
        value: 12khz
        curve: exp
  - track: Atmosphere
    param: volume
    events:
      - beat: 64
        value: 0.0
      - beat: 68
        value: 0.6
        curve: linear
""",
    ),

    # 20 ── Nordic ambient folk ──────────────────────────────────────────────
    PromptItem(
        id="nordic_ambient_folk",
        title="Nordic ambient folk \u00b7 Em \u00b7 63 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: Nordic ambient folk \u00b7 Key: Em \u00b7 63 BPM\nRole: folk strings, piano, vocals, drone\nVibe: atmospheric x3, melancholic x2, intimate, dreamy",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: intro
Style: Nordic ambient folk
Key: Em
Tempo: 63
Energy: very low
Role: [folk strings, piano, vocals, drone]
Constraints:
  bars: 24
Vibe: [atmospheric x3, melancholic x2, intimate, dreamy, peaceful, ancient, still]

Request: |
  A full Nordic folk piece in three sections. 8-bar stillness — a deep
  open-string drone on E0 alone, so low it is almost felt rather than
  heard. After 4 bars, a lone cello enters with a slow dorian melody,
  sul tasto, near the fingerboard, the softest possible tone. Like
  light through frozen glass. 8-bar gathering — piano adds sparse
  whole-note chords in Em-C-G-Dsus2, open voicings with only root and
  5th (no 3rds), wordless vocal hums enter at bar 9 (mezzo soprano,
  low register, mm and ah sounds), and the cello melody becomes slightly
  more present. 8-bar bloom — all voices reach their quiet peak. The
  cello plays with wider intervals, the piano adds occasional 8th-note
  figures, the vocals move from hum to open ah, and at bar 21 the
  piece reaches mp — the loudest it will ever be. Then it settles
  back to ppp over the final 3 bars. The drone never stops. Like
  Hauschka meets Nils Frahm on a winter evening. Snow falling on
  still water. No wind.

Harmony:
  progression: |
    Stillness (1-8): [Em, Em, Em, Em, Em, C, G, Dsus2]
    Gathering (9-16): [Em, C, G, Dsus2, Em, C, G, Dsus2]
    Bloom (17-24): [Em, Em, C, C, G, G, Dsus2, Em]
  voicing: open — only root and 5th, no 3rds
  rhythm: whole-note sustains, changing only on bar downbeats
  extensions: sus2s — open, unresolved, Scandinavian

Melody:
  scale: E dorian
  register: Cello — D3-A4
  contour: |
    Stillness: slow descending spiral, barely moving, one note every
    2-3 beats. Never resolving.
    Gathering: melody gains shape, intervals of 3rds and 4ths, still
    sparse but with direction.
    Bloom: wider intervals (5ths, 6ths), more present, peaks at bar
    21 with a sustained A4, then descends back to E3.
  phrases:
    structure: free, non-metric — phrases breathe organically
    breath: long — each phrase 3-4 bars
  density: very sparse throughout — one note every 2-3 beats

Rhythm:
  feel: free, non-metric — pulse is felt not counted
  subdivision: quarter notes
  swing: 50%

Dynamics:
  overall: ppp to mp to ppp
  arc:
    - bars: 1-4
      level: ppp
      shape: drone alone — barely audible
    - bars: 5-8
      level: ppp
      shape: cello enters, still nearly silent
    - bars: 9-12
      level: ppp to pp
      shape: piano and vocals enter, gentle gathering
    - bars: 13-16
      level: pp
      shape: settled, present, quiet companionship
    - bars: 17-20
      level: pp to mp
      shape: bloom begins, intervals widen, brightness
    - bars: 21-24
      level: mp to ppp
      shape: peak at bar 21, then 3-bar descent to silence
  accent_velocity: 62
  ghost_velocity: 18

Orchestration:
  folk_strings:
    instrument: solo cello
    technique: long bowing, sul tasto (near fingerboard) — softest tone
    vibrato: slow, narrow, delayed onset (begins bar 9)
    entry: bar 5
  piano:
    voicing: right hand only — open 5ths in mid register
    articulation: extremely soft, barely struck
    pedaling: full sustain throughout
    entry: bar 9
  vocals:
    style: wordless, low hum — mm sounds (gathering), open ah (bloom)
    register: E3-E4 (mezzo soprano, mid register)
    entry: bar 9
  drone:
    pitch: E0
    technique: pure low string resonance — continuous throughout
    entry: bar 1

Effects:
  strings:
    reverb: large stone church, 5s decay, 40ms predelay
    filter: gentle lowpass at 4khz
  piano:
    reverb: same church space
  vocals:
    reverb: same space, 70% wet
    filter: gentle air at 8khz
  drone:
    reverb: same church, very long tail
    filter: lowpass at 200hz — felt more than heard

Expression:
  arc: silence to gentle presence to quiet peak to silence
  narrative: |
    The drone at bar 1 is the world before you arrived. It has always
    been there. When the cello enters at bar 5, it is the first breath
    of something alive — tentative, fragile, not sure it wants to be
    heard. The piano and vocals at bar 9 are companionship arriving
    without announcement. By bar 17, there is enough trust for the
    bloom — wider intervals, open vowels, the closest thing to joy
    this music allows. The peak at bar 21 is mp — the loudest this
    piece will ever be. And then it remembers what it is: silence
    with landmarks. The final 3 bars return to ppp. The drone
    continues. It was there before. It will be there after.
  character: Nils Frahm's piano. Hauschka's prepared strings.
    Hildur Gu\u00f0nad\u00f3ttir's cello. A winter evening in Iceland.
    The Northern Lights as sound.
  spatial_image: |
    Everything diffuse and wide — no clear center, just space. The
    drone is everywhere. The cello is slightly left. The piano slightly
    right. The vocals are above. You are inside the church.

Texture:
  density: nearly silent to sparse to nearly silent
  register_spread: E0-A4
  space:
    principle: |
      This music is mostly silence. The notes are just landmarks in a
      vast white expanse. Every silence between notes is load-bearing.
      The piece exists in the spaces as much as in the sounds.

Form:
  structure: stillness-gathering-bloom
  development:
    - section: stillness (bars 1-8)
      intensity: ppp — drone alone, cello enters bar 5, nearly silent
    - section: gathering (bars 9-16)
      variation: piano and vocals enter, quiet companionship
    - section: bloom (bars 17-24)
      contrast: wider intervals, open vowels, peak at bar 21, return
  variation_strategy: |
    Each section adds one degree of presence. Stillness: sound exists.
    Gathering: it has company. Bloom: it opens, briefly, then returns
    to what it was. The arc is a single breath.

Humanization:
  timing:
    jitter: 0.08
    late_bias: 0.03
    grid: quarter
  velocity:
    arc: phrase
    stdev: 8
    accents:
      beats: [0]
      strength: 2
    ghost_notes:
      probability: 0.01
      velocity: [12, 22]
  feel: free, non-metric — maximum human imperfection, no grid

MidiExpressiveness:
  expression:
    curve: follows dynamic arc exactly, ppp to mp to ppp
    range: [12, 68]
  modulation:
    instrument: cello
    depth: slow vibrato — CC 1 from 0 to 30, onset delayed to bar 9
    onset: bars 9-24 only
  cc_curves:
    - cc: 91
      from: 48
      to: 82
      position: bars 1-24
    - cc: 1
      from: 0
      to: 32
      position: bars 9-24
    - cc: 11
      from: 12
      to: 68
      position: bars 1-24
  sustain_pedal:
    style: full sustain throughout on piano
    changes_per_bar: 0
  aftertouch:
    type: channel
    response: adds volume swell on kantele and nyckelharpa sustained tones
    use: amplitude — pressure creates gentle dynamic swells on folk strings
  pitch_bend:
    style: subtle cello intonation — microtonal
    depth: microtonal

Automation:
  - track: Folk_Strings
    param: reverb_wet
    events:
      - beat: 0
        value: 0.4
      - beat: 32
        value: 0.5
        curve: smooth
      - beat: 64
        value: 0.6
        curve: smooth
      - beat: 80
        value: 0.7
        curve: smooth
      - beat: 96
        value: 0.5
        curve: smooth
  - track: Piano
    param: delay_feedback
    events:
      - beat: 32
        value: 0.1
      - beat: 64
        value: 0.25
        curve: smooth
      - beat: 96
        value: 0.15
        curve: smooth
  - track: Master
    param: reverb_wet
    events:
      - beat: 0
        value: 0.4
      - beat: 80
        value: 0.55
        curve: smooth
      - beat: 96
        value: 0.4
        curve: smooth
""",
    ),

    # 21 ── Flamenco fusion ──────────────────────────────────────────────────
    PromptItem(
        id="flamenco_fusion",
        title="Flamenco fusion \u00b7 Am phrygian \u00b7 176 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: flamenco fusion \u00b7 Key: Am phrygian \u00b7 176 BPM\nRole: guitar, cajon, bass, pad, palmas\nVibe: intense x3, tense x2, driving, mysterious",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: flamenco fusion
Key: A phrygian
Tempo: 176
Energy: high
Role: [guitar, cajon, bass, pad, palmas]
Constraints:
  bars: 24
Vibe: [intense x3, tense x2, driving, mysterious, dark, passionate, ancient]

Request: |
  A full flamenco fusion piece in three sections. 8-bar llamada
  (opening call) — the guitar alone plays a slow falseta in A phrygian,
  rubato-like 16th-note runs, ornamental and free. The cajon enters at
  bar 5 with soft ghost notes, establishing the compas. No bass, no pad.
  Just guitar and the whisper of hands. 8-bar verse — the full ensemble
  enters: cajon plays the compas accent pattern (3,6,8,10,12), the
  electronic bass adds deep sub weight under the Andalusian cadence
  (Am-G-F-E), palmas (handclaps) double the accent pattern, and the
  dark atmospheric pad sustains open 5ths. The guitar alternates between
  picado runs and tremolo on sustained notes. 8-bar rasgueado climax —
  the guitar explodes into rasgueado strumming on the E major chord
  (Phrygian surprise), the cajon hits fortissimo, the bass distortion
  increases, and the piece builds to maximum intensity. The final bar
  is a single struck E major chord with all voices, held and ringing.
  Duende — the dark spirit of flamenco. Ancient fire, electronic depth.

Harmony:
  progression: |
    Llamada (1-8): [Am, Am, Am, G, F, F, E, E]
    Verse (9-16): [Am, G, F, E, Am, G, F, E]
    Rasgueado (17-24): [Am, Am, F, E, Am, Am, E, E]
  voicing: Andalusian cadence — descending in phrygian mode
  rhythm: flamenco rhythmic cycle (compas) — 12-beat cycle
  extensions: E major chord (not minor) — Phrygian harmonic surprise

Melody:
  scale: A phrygian
  register: Guitar — E3-E6
  contour: |
    Llamada: rubato falseta, ascending runs, ornamental, free.
    Verse: alternating picado runs (fast, linear) and tremolo (sustained).
    Rasgueado: rhythm becomes melody — the strum pattern IS the phrase.
  phrases:
    structure: 4-bar falseta, repeated with variations
  density: sparse (llamada) to very dense (rasgueado)
  ornamentation:
    - rasgueado on E chord (climax)
    - picado runs on A phrygian scale (verse)
    - tremolo on sustained notes (verse)
    - hammer-ons and pull-offs throughout

Rhythm:
  feel: flamenco compas — 12-beat cycle with complex accent patterns
  subdivision: 16th notes, triplet feel
  swing: 53%
  accent:
    pattern: |
      Compas: 12-beat cycle accented on 3, 6, 8, 10, 12.
      Llamada: free, rubato-like.
      Rasgueado: every beat accented, maximum intensity.
  ghost_notes:
    instrument: cajon
    velocity: 28-62

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-4
      level: pp to mp
      shape: guitar alone, falseta, rubato
    - bars: 5-8
      level: mp
      shape: cajon enters, compas established
    - bars: 9-12
      level: mf
      shape: full ensemble, driving
    - bars: 13-16
      level: f
      shape: building toward climax
    - bars: 17-20
      level: f to ff
      shape: rasgueado begins, intensity rising
    - bars: 21-23
      level: ff to fff
      shape: maximum intensity, everything erupting
    - bar: 24
      level: fff (held chord)
      shape: single struck E major, ringing into silence
  accent_velocity: 122
  ghost_velocity: 32

Orchestration:
  guitar:
    technique: |
      Llamada: classical falseta, picado runs, rubato.
      Verse: alternating picado and tremolo.
      Rasgueado: explosive strumming, percussive attacks.
    articulation: extremely fast, percussive attacks on strums
    entry: bar 1
  cajon:
    technique: |
      Llamada: soft ghost notes only (bar 5-8).
      Verse: full compas — bass tone on 1/7, slap on 3/8.
      Rasgueado: fortissimo, every accent doubled.
    ghost_notes: constant 16th-note ghost pattern
    entry: bar 5
  bass:
    technique: picked electric, deep sub
    register: A0-A2
    articulation: sustained, follows guitar roots
    entry: bar 9
  pad:
    instrument: dark analog pad
    voicing: open 5ths, phrygian atmosphere
    attack: slow 1s
    entry: bar 9
  palmas:
    style: handclaps doubling compas accent pattern
    entry: bar 9

Effects:
  guitar:
    reverb: small stone room, 0.8s
    compression: very gentle — preserve transients
  bass:
    distortion: subtle saturation (verse), heavier in rasgueado
    eq:
      - band: sub
        freq: 80hz
        gain: +3db
  pad:
    reverb: large dark hall, 4s

Expression:
  arc: solitary call to communal fire to eruption
  narrative: |
    The llamada is a call from the darkness. The guitar is alone,
    searching through A phrygian for something it cannot name. When
    the cajon whispers at bar 5, the call has been heard. The verse
    at bar 9 is the answer — the full ensemble enters, and the
    Andalusian cadence descends like ancient stairs into something
    deeper. The rasgueado at bar 17 is not played. It erupts. Duende —
    the dark mysterious spirit of flamenco — arrives uninvited. The
    music comes from somewhere below rational thought. The final struck
    E major chord at bar 24 hangs in the air like a question that
    doesn't want an answer. It just wants to ring.
  character: Paco de Luc\u00eda's fire. Camar\u00f3n's anguish. Rosalia's
    modernity. The caves of Sacromonte at midnight.
  tension_points:
    - bar: 16
      device: verse peaks, full compas at f, transition bar
    - bar: 21
      device: rasgueado at maximum, ff approaching fff
    - bar: 24
      device: held E major chord, ringing into silence

Texture:
  density: sparse (llamada) to very dense (rasgueado) to single chord
  register_spread: A0-E6
  space: |
    Llamada: mostly space, guitar and silence. Verse: structured, each
    instrument in its lane. Rasgueado: no space, total density. Final
    bar: one chord, all the space in the world.

Form:
  structure: llamada-verse-rasgueado
  development:
    - section: llamada (bars 1-8)
      intensity: pp to mp — guitar alone, then cajon ghost notes
    - section: verse (bars 9-16)
      variation: full ensemble, Andalusian cadence, compas pattern
    - section: rasgueado (bars 17-24)
      contrast: explosive strumming, maximum intensity, final held chord
  variation_strategy: |
    The llamada is a solitary call. The verse is the community answering.
    The rasgueado is duende arriving. Each section doubles the density
    and the emotional stakes.

Humanization:
  timing:
    jitter: 0.05
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: phrase
    stdev: 18
    accents:
      beats: [2, 5, 7, 9, 11]
      strength: 16
    ghost_notes:
      probability: 0.12
      velocity: [25, 58]
  feel: flamenco compas — human fire, never metronomic

MidiExpressiveness:
  expression:
    curve: builds from pp to fff
    range: [35, 127]
  pitch_bend:
    style: guitar bends on ornamental notes — half-tone to whole-tone
    depth: 1 semitone
  cc_curves:
    - cc: 91
      from: 18
      to: 62
      position: bars 1-24
    - cc: 74
      from: 35
      to: 105
      position: bars 1-24
    - cc: 11
      from: 35
      to: 127
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds sustain on sustained guitar tones
    use: tremolo expression
  modulation:
    instrument: guitar
    depth: vibrato on sustained bends — CC 1 value 40-80
    onset: delayed 1 beat on long notes, immediate on bends
  filter:
    cutoff:
      sweep: |
        Llamada bars 1-8: open, natural guitar tone
        Verse bars 9-16: bass filter gradually opening from 400hz to 2khz
        Rasgueado bars 17-24: fully open, raw and aggressive
      resonance: low on guitar, moderate on bass
  articulation:
    portamento:
      time: 20
      switch: on

Automation:
  - track: Bass
    param: distortion
    events:
      - beat: 32
        value: 0.2
      - beat: 64
        value: 0.4
      - beat: 96
        value: 0.8
        curve: exp
""",
    ),

    # 22 ── UK garage steppers ───────────────────────────────────────────────
    PromptItem(
        id="uk_garage_steppers",
        title="UK garage steppers \u00b7 Dbm \u00b7 130 BPM",
        preview="Mode: compose \u00b7 Section: drop\nStyle: UK garage / 2-step \u00b7 Key: Dbm \u00b7 130 BPM\nRole: drums, bass, vocal chop, synth pad\nVibe: groovy x3, energetic x2, dark, atmospheric",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: drop
Style: UK garage 2-step
Key: Dbm
Tempo: 130
Energy: high
Role: [drums, bass, vocal chop, synth pad]
Constraints:
  bars: 24
Vibe: [groovy x3, energetic x2, dark, atmospheric, flowing, soulful, midnight]

Request: |
  A full UK garage piece in three sections. 8-bar intro — a warm synth
  pad alone plays Dbm7-Abmaj7, close-voiced 7th chords, dark and
  soulful, with a gentle vinyl crackle texture. At bar 5, a pitched
  vocal chop enters with a 2-note pattern (Eb4-Ab4), time-stretched
  and dreamy. 8-bar drop — the iconic 2-step kick pattern arrives
  (kick on 1 and 2-and, then 3 and 4-and), tight rimshot snare on
  2 and 4, 16th-note partially-open hi-hats, rolling bass following
  the kick syncopation exactly. The vocal chop doubles and the pad
  stabs shift to upbeats. Craig David era, updated. 8-bar evolution —
  the bass pattern gets more complex (adds 16th-note fills between
  kick hits), the vocal chop adds a third pitch (Db5), the hi-hat
  pattern opens up, and the pad introduces a new chord (Bbm7-Gbmaj7).
  The piece reaches its peak of sophistication at bar 20, then the
  drums drop out for bars 23-24, leaving only the pad and vocal chop
  to close — the way it began. 3am in a sweaty London club.

Harmony:
  progression: |
    Intro (1-8): [Dbm7, Dbm7, Abmaj7, Abmaj7, Dbm7, Dbm7, Abmaj7, Abmaj7]
    Drop (9-16): [Dbm7, Abmaj7, Bbm7, Gbmaj7, Dbm7, Abmaj7, Bbm7, Gbmaj7]
    Evolution (17-24): [Bbm7, Gbmaj7, Dbm7, Abmaj7, Bbm7, Gbmaj7, Dbm7, Dbm7]
  voicing: close voiced 7th chords, keyboard register
  rhythm: |
    Intro: pad sustains, whole notes.
    Drop: pad stabs on upbeats — classic 2-step rhythm.
    Evolution: pad adds passing chords, slightly more active.
  extensions: 7ths throughout — soulful and warm

Melody:
  scale: Db minor pentatonic
  register: vocal chop — Eb4-Db5
  contour: |
    Intro: 2-note pattern (Eb4-Ab4), dreamy, time-stretched.
    Drop: same pattern doubled, more rhythmic.
    Evolution: 3-note pattern adds Db5, melodic development.
  phrases:
    structure: 2-bar hook, repeated with variation each section
  density: sparse (intro) to medium (drop) to medium-high (evolution)

Rhythm:
  feel: ahead of the beat — 2-step urgency
  subdivision: 16th notes
  swing: 52%
  accent:
    pattern: 2-step kick — not four-on-the-floor, skipping
  ghost_notes:
    instrument: snare
    velocity: 32-52

Dynamics:
  overall: mp to f to mp
  arc:
    - bars: 1-4
      level: mp
      shape: pad alone, atmospheric
    - bars: 5-8
      level: mp
      shape: vocal chop enters, still gentle
    - bars: 9-16
      level: f
      shape: full 2-step drop, constant groove
    - bars: 17-20
      level: f
      shape: evolution peak, maximum sophistication
    - bars: 21-22
      level: f to mp
      shape: beginning to unwind
    - bars: 23-24
      level: mp
      shape: drums drop out, pad and vocal chop only, circular return
  accent_velocity: 108
  ghost_velocity: 40

Orchestration:
  drums:
    kick: 2-step pattern — beats 1, 2-and, 3, 4-and
    snare: tight rimshot on 2 and 4
    hi_hat: 16th notes, partially open, opens further in evolution
    entry: "bar 9, exit: bar 22 (drums drop out bars 23-24)"
  bass:
    technique: pulsed electronic bass
    register: Db1-Db2
    articulation: |
      Drop: follows kick rhythm exactly, same syncopation.
      Evolution: adds 16th-note fills between kick hits.
    entry: bar 9
  vocal_chop:
    pitch: |
      Intro/Drop: Eb4 and Ab4 alternating.
      Evolution: adds Db5, 3-note melodic development.
    style: pitched, chopped, time-stretched
    rhythm: syncopated, every bar
    entry: bar 5
  synth_pad:
    voicing: close-voiced 7th chords
    style: warm, dark, soulful
    entry: bar 1 — owns the entire piece, present throughout

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
    texture: subtle vinyl crackle

Expression:
  arc: atmosphere to groove to sophistication to atmosphere
  narrative: |
    The pad at bar 1 is the club before the track drops — warm, dark,
    expectant. The vocal chop at bar 5 is the DJ teasing. When the
    2-step kick arrives at bar 9, everything makes sense — the bass
    follows the kick like a shadow, the hi-hats shimmer, the pad stabs
    land on every upbeat. This is the heartbeat of South London at 3am.
    The evolution at bar 17 is sophistication: the bass adds fills, the
    vocal chop finds a third note, the harmony moves to Bbm7-Gbmaj7.
    And then, at bar 23, the drums stop. The pad and vocal chop are
    alone again. Where we started. The circle closes. Everything is
    slow-motion and fast at the same time. This is what cool sounds like.
  character: MJ Cole's elegance. Craig David's soul. El-B's bass
    science. Todd Edwards' vocal chops. Wookie's darkness.
  spatial_image: |
    Intro: pad wide, vocal chop center-right. Drop: drums center, bass
    center, vocal chop right +20, pad wide +-50. Evolution: everything
    slightly closer, more intimate. Outro: pad wide, vocal chop
    center-right — circular.

Texture:
  density: sparse (intro) to medium-high (evolution) to sparse (outro)
  register_spread: Db1-Db5
  stereo_field:
    drums: center
    bass: center
    vocal_chop: right +20
    pad: +-50

Form:
  structure: intro-drop-evolution
  development:
    - section: intro (bars 1-8)
      intensity: mp — pad and vocal chop, atmospheric setup
    - section: drop (bars 9-16)
      variation: full 2-step kit, bass, groove established
    - section: evolution (bars 17-24)
      contrast: bass fills, 3-note vocal, new chords, drums exit bar 22
  variation_strategy: |
    Intro sets the mood. Drop delivers the groove. Evolution sophisticates
    it. The drum exit at bar 22 creates circularity — the piece ends
    where it began, pad and vocal chop alone. The club is still here.
    You are still here. The night is not over.

Humanization:
  timing:
    jitter: 0.02
    late_bias: -0.005
    grid: 16th
  velocity:
    arc: phrase
    stdev: 10
    accents:
      beats: [0, 2]
      strength: 8
    ghost_notes:
      probability: 0.06
      velocity: [30, 50]
  feel: ahead of the beat — 2-step urgency, always leaning forward

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [68, 112]
  cc_curves:
    - cc: 91
      from: 28
      to: 62
      position: bars 1-24
    - cc: 74
      from: 38
      to: 78
      position: bars 1-24
    - cc: 11
      from: 68
      to: 112
      position: bars 1-24
  aftertouch:
    type: channel
    response: opens stab synth filter and adds volume swell
    use: filter cutoff + amplitude — pressure brightens and lifts the stab
  modulation:
    instrument: synth pad
    depth: subtle vibrato — CC 1 value 20-45
    onset: delayed 2 beats on sustained pad chords
  pitch_bend:
    style: bass slides between kicks — subtle
    depth: quarter-tone

Automation:
  - track: Vocal_Chop
    param: reverb_wet
    events:
      - beat: 0
        value: 0.5
      - beat: 32
        value: 0.2
        curve: linear
      - beat: 88
        value: 0.5
        curve: linear
  - track: Drums
    param: volume
    events:
      - beat: 84
        value: 1.0
      - beat: 88
        value: 0.0
        curve: linear
""",
    ),

    # 32 ── Anatolian psychedelic rock ─────────────────────────────────────
    PromptItem(
        id="anatolian_psych_rock",
        title="Anatolian psych rock \u00b7 Em \u00b7 125 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Anatolian psychedelic rock \u00b7 Key: Em \u00b7 125 BPM\nRole: rock organ, electric saz, bass, drums, strings\nVibe: psychedelic x3, driving x2, mystical, fuzzy, Turkish",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Anatolian psychedelic rock
Key: Em
Tempo: 125
Energy: high
Role: [rock organ, electric saz, bass, drums, strings]
Constraints:
  bars: 24
Vibe: [psychedelic x3, driving x2, mystical, fuzzy, Turkish, 1970s, garage]

Request: |
  A 1970s Anatolian psych rock piece in three sections. 8-bar riff —
  the electric saz (GM 105 banjo with fuzz) plays a hypnotic riff in
  E phrygian (E-F-G#-A-B-C-D), the augmented 2nd between F and G#
  giving it a distinctly Turkish psychedelic feel. The rock organ
  (GM 18) adds sustained chords. The drums play a straight rock beat.
  8-bar verse — the bass enters with a heavy driving pattern following
  the saz riff, strings (GM 49) add a lush Turkish-style countermelody,
  the organ shifts to a Leslie speaker tremolo effect. The energy builds.
  8-bar solo — the saz takes a distorted, wah-inflected solo over the
  riff, bending notes in the phrygian scale, the organ and strings
  sustain, and the drums intensify. Peak 1970s Istanbul garage rock
  energy. Erkin Koray meets Baris Manco. The Bosphorus at sunset.

Harmony:
  progression: |
    Riff (1-8): [Em, F, G#m, Am, Em, F, G#m, Em]
    Verse (9-16): [Em, F, Am, G#m, Em, F, Am, Em]
    Solo (17-24): [Em, F, G#m, Am, Em, F, G#m, Em]
  voicing: power chords on saz, full chords on organ
  rhythm: riff-based — saz drives, organ sustains
  extensions: none — raw, garage rock aesthetic

Melody:
  scale: E phrygian dominant (E-F-G#-A-B-C-D) — augmented 2nd F-G#
  register: saz E3-E6, strings E3-E5
  contour: |
    Riff: hypnotic 2-bar pattern, repeating.
    Verse: strings countermelody ascending while saz riffs.
    Solo: saz bends and wah effects, peak virtuosity.
  phrases:
    structure: 2-bar riff pattern, varied across sections
  density: medium (riff) to dense (solo)

Dynamics:
  overall: f to fff
  arc:
    - bars: 1-8
      level: f
      shape: riff established, driving
    - bars: 9-16
      level: f to ff
      shape: verse builds, bass and strings add weight
    - bars: 17-24
      level: ff to fff
      shape: solo peaks, maximum garage energy
  accent_velocity: 115
  ghost_velocity: 48

Rhythm:
  feel: driving rock beat with phrygian gravity — the augmented 2nd
    interval (F to G#) pulls the rhythm into a circular, hypnotic
    pattern. The saz riff repeats every 2 bars, each repetition adding
    weight. This is not Western rock time — it is Anatolian time,
    where the downbeat is heavier and the riff cycle is a meditation.
  subdivision: 8th notes
  swing: 50%
  accent:
    pattern: |
      Drums: straight rock beat — kick on 1 and 3, snare on 2 and 4.
      But the saz riff accents the phrygian intervals (F and G#) which
      create a gravitational pull that makes the straight beat feel
      modal and hypnotic. The augmented 2nd is the secret.
      Bass: locked to the saz riff, doubling at the octave below.

Orchestration:
  electric_saz:
    instrument: banjo (GM 105) — representing saz with fuzz distortion
    technique: |
      Riff: hypnotic phrygian pattern, fuzz distortion.
      Solo: bends, wah effects, maximum expression.
    register: E3-E6
    entry: bar 1
  rock_organ:
    instrument: rock organ (GM 18)
    technique: sustained chords, Leslie tremolo from bar 9
    entry: bar 1
  bass:
    instrument: electric bass (GM 33)
    technique: heavy driving pattern following saz riff
    register: E1-E3
    entry: bar 9
  drums:
    technique: straight rock beat, intensifying through sections
    entry: bar 1
  strings:
    instrument: string ensemble (GM 49)
    technique: Turkish-style countermelody, lush and dramatic
    register: E3-E5
    entry: bar 9

Effects:
  saz:
    distortion: heavy fuzz — the sound of a 3,000-year-old instrument
      fed through a Big Muff. Civilization-spanning overdrive.
    wah: solo section, sweep at 2Hz — the wah pedal bends the
      phrygian intervals into something alien and ancient
    reverb: garage room, 0.8s — raw and close
  organ:
    leslie: rotating speaker at medium speed — the Doppler effect
      adds a psychedelic shimmer that makes the organ breathe
    reverb: same garage room
  drums:
    compression: moderate rock compression — tight, punchy
  strings:
    reverb: medium hall, 1.5s — the strings exist in a different
      space than the garage instruments, creating the Ottoman/
      psychedelic tension

Expression:
  arc: riff to drive to ecstasy
  narrative: |
    Istanbul, 1974. The saz has been electrified and fed through a fuzz
    pedal. The augmented 2nd of phrygian dominant — the interval that
    makes Turkish music Turkish — is now distorted, amplified, and
    bouncing off the walls of a garage. Erkin Koray is bending notes
    that no rock guitarist in London or San Francisco has ever bent.
    The organ swirls through a Leslie speaker. The strings add drama
    that is simultaneously psychedelic and Ottoman. The solo at bar 17
    is the Bosphorus at sunset — ancient and electric, sacred and
    dangerous, East and West at the exact same moment.
  character: Erkin Koray's madness. Baris Manco's style. Selda Bagcan's
    rebellion. 3 H\u00fcr-El's garage energy. The sound of Anatolia
    plugged in and turned up.

Texture:
  density: medium (riff) to dense (verse) to maximum (solo)
  register_spread: E1-E6
  space:
    principle: |
      Two sonic worlds collide. The garage: saz, organ, drums — raw,
      close, distorted, present. The hall: strings — lush, distant,
      cinematic. The genius of Anatolian psych rock is that these
      two worlds coexist without merging. The saz riff is in your
      face. The strings are behind your head. The organ's Leslie
      speaker creates a rotating bridge between them. In the solo
      section, the wah pedal makes the saz occupy both worlds at
      once — the sweep between low and high frequency is the saz
      traveling from the garage to the Ottoman court and back.

Form:
  structure: riff-verse-solo
  development:
    - section: riff (bars 1-8)
      intensity: f — saz and organ establish the hypnotic pattern
    - section: verse (bars 9-16)
      variation: bass and strings enter, energy builds, Leslie organ
    - section: solo (bars 17-24)
      contrast: saz solo with fuzz and wah, peak energy

Humanization:
  timing:
    jitter: 0.03
    late_bias: 0.0
    grid: 8th
  velocity:
    arc: phrase
    stdev: 14
    accents:
      beats: [0, 2]
      strength: 12
    ghost_notes:
      probability: 0.05
      velocity: [40, 58]
  feel: driving rock — slightly loose, garage energy

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [78, 125]
  pitch_bend:
    style: saz bends — half-tone to whole-tone on phrygian notes
    depth: 1-2 semitones
  cc_curves:
    - cc: 91
      from: 20
      to: 48
      position: bars 1-24
    - cc: 74
      from: 45
      to: 100
      position: bars 1-24
    - cc: 11
      from: 78
      to: 125
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds vibrato depth on saz/baglama sustained notes
    use: vibrato — pressure deepens the microtonal shimmer of the saz
  modulation:
    instrument: electric saz
    depth: wah-like sweep — CC 1 value 50-100
    onset: immediate in solo section, delayed 2 beats in riff/verse
  filter:
    cutoff:
      sweep: |
        Riff bars 1-8: bass filter at 800hz, dark and fuzzy
        Verse bars 9-16: bass opens to 2khz, weight emerges
        Solo bars 17-24: bass fully open, fuzz filter saturated
      resonance: moderate on bass, high on saz wah

Automation:
  - track: Electric_Saz
    param: distortion
    events:
      - beat: 0
        value: 0.6
      - beat: 64
        value: 0.9
        curve: linear
""",
    ),

    # 38 ── Klezmer wedding ────────────────────────────────────────────────
    PromptItem(
        id="klezmer_wedding",
        title="Klezmer wedding \u00b7 Dm \u00b7 140 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: klezmer \u00b7 Key: Dm \u00b7 140 BPM\nRole: clarinet, violin, accordion, bass, drums\nVibe: joyful x3, frantic x2, bittersweet, dancing, celebration",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: klezmer
Key: Dm
Tempo: 140
Energy: high
Role: [clarinet, violin, accordion, bass, drums]
Constraints:
  bars: 24
Vibe: [joyful x3, frantic x2, bittersweet, dancing, celebration, wedding, alive]

Request: |
  A full klezmer wedding piece in three sections. 8-bar doina (free
  introduction) — the clarinet (GM 71) alone plays a free, rubato
  melody in D Ahava Rabbah (D-Eb-F#-G-A-Bb-C-D, same as Hijaz), with
  krekhts (sobbing bends) and trills. Bittersweet, like laughter
  through tears. 8-bar freilach (dance) — the full band explodes: the
  violin (GM 40) joins with a rapid dance melody, the accordion (GM 21)
  pumps rhythmic chords, the bass walks in 2-feel, and the drums drive
  a freilach dance rhythm (fast 2/4, heavy on beat 2). Everyone is
  dancing. 8-bar sher (circle dance) — the tempo feels even more urgent,
  the clarinet and violin play in call-and-response, the accordion
  shifts to a pumping 8th-note pattern, and the piece builds to a
  frantic peak. The hora circle spins faster. Joy that knows sorrow.
  Sorrow that dances.

Harmony:
  progression: |
    Doina (1-8): Dm free — Ahava Rabbah mode, no strict changes.
    Freilach (9-16): [Dm, Gm, A7, Dm, Dm, Gm, A7, Dm]
    Sher (17-24): [Dm, Gm, C, F, Dm, Gm, A7, Dm]
  voicing: accordion pumps full chords, bass on roots
  rhythm: |
    Doina: free. Freilach: 2-feel. Sher: driving 2/4.
  extensions: dom7 on A for tension before Dm resolution

Melody:
  scale: D Ahava Rabbah (D-Eb-F#-G-A-Bb-C-D)
  register: clarinet D4-D6, violin G3-E6
  contour: |
    Doina: free, wailing, krekhts bends, trills.
    Freilach: rapid dance melody, 8th and 16th notes.
    Sher: call-response clarinet/violin, frantic peak.
  phrases:
    structure: |
      Doina: free, breath-length.
      Freilach: 4-bar dance phrases.
      Sher: 2-bar call, 2-bar response.
  density: sparse (doina) to very dense (sher)
  ornamentation:
    - krekhts (sobbing bend — pitch drops then returns)
    - trills on sustained notes
    - grace notes on dance phrases

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-8
      level: pp to mf
      shape: doina, clarinet alone, bittersweet
    - bars: 9-16
      level: f
      shape: freilach, full band, joyful dance
    - bars: 17-24
      level: f to fff
      shape: sher, frantic peak, maximum celebration
  accent_velocity: 118
  ghost_velocity: 38

Rhythm:
  feel: |
    Doina: free, rubato — no meter.
    Freilach: fast 2/4, heavy on beat 2.
    Sher: even faster 2/4, frantic.
  subdivision: 8th notes (freilach), 16th notes (sher)
  swing: 55%
  accent:
    pattern: |
      Freilach: heavy on beat 2.
      Sher: every beat, driving.
  ghost_notes:
    instrument: drums
    velocity: 32-50

Orchestration:
  clarinet:
    instrument: clarinet (GM 71)
    technique: |
      Doina: free, krekhts, trills, rubato.
      Freilach: dance melody with ornaments.
      Sher: call phrases, soaring.
    register: D4-D6
    entry: bar 1
  violin:
    instrument: violin (GM 40)
    technique: |
      Freilach: rapid dance melody, double-stops.
      Sher: response to clarinet calls, virtuosic.
    register: G3-E6
    entry: bar 9
  accordion:
    instrument: accordion (GM 21)
    technique: |
      Freilach: rhythmic chord pumps, 2-feel.
      Sher: 8th-note pumping, driving.
    entry: bar 9
  bass:
    instrument: acoustic bass (GM 32)
    technique: walking 2-feel, strong beats 1 and 2
    register: D1-D3
    entry: bar 9
  drums:
    technique: freilach rhythm — 2/4, heavy on beat 2
    entry: bar 9

Effects:
  clarinet:
    reverb: wedding hall, 1.5s
  violin:
    reverb: same hall
  accordion:
    compression: gentle, preserve bellows dynamics

Expression:
  arc: sorrow to joy to ecstasy
  narrative: |
    The doina is klezmer's secret: before you can dance, you must weep.
    The clarinet at bar 1 is a human voice — the krekhts is literally a
    sob turned into music. But when the freilach explodes at bar 9,
    the sorrow becomes the fuel for joy. This is the Jewish genius:
    dancing because of the tears, not despite them. The sher at bar 17
    is the hora circle spinning faster and faster — the clarinet calls,
    the violin responds, the accordion pumps, and everyone in the
    room is one body. Joy that knows sorrow. Sorrow that dances.
    L'chaim.
  character: Naftule Brandwein's fire. Dave Tarras's soul. Giora
    Feidman's breath. A wedding in Odessa. A wedding in Brooklyn.
    The same wedding everywhere.

Texture:
  density: sparse (doina) to very dense (sher)
  register_spread: D1-E6
  space: |
    Doina: all space, clarinet and silence. Freilach: structured joy,
    everyone in their lane. Sher: no space, every voice at full volume.

Form:
  structure: doina-freilach-sher
  development:
    - section: doina (bars 1-8)
      intensity: pp — clarinet alone, free, bittersweet
    - section: freilach (bars 9-16)
      variation: full band, dance rhythm, joyful
    - section: sher (bars 17-24)
      contrast: frantic peak, call-response, maximum celebration

Humanization:
  timing:
    jitter: 0.04
    late_bias: -0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 18
    accents:
      beats: [1]
      strength: 14
    ghost_notes:
      probability: 0.06
      velocity: [32, 50]
  feel: ahead of the beat in freilach — dancers lean forward

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [28, 127]
  pitch_bend:
    style: clarinet krekhts — sobbing pitch drops, 1-2 semitones
    depth: 1-2 semitones
  cc_curves:
    - cc: 91
      from: 25
      to: 55
      position: bars 1-24
    - cc: 11
      from: 28
      to: 127
      position: bars 1-24
    - cc: 1
      from: 20
      to: 65
      position: bars 1-24
  aftertouch:
    type: channel
    response: simulates accordion bellows pressure dynamics
    use: volume + filter — pressure controls bellows intensity and brightness
  modulation:
    instrument: clarinet
    depth: expressive klezmer vibrato — CC 1 value 40-85
    onset: immediate on sustained notes, wider in sher section
  breath_control:
    instrument: clarinet
    mapping: filter cutoff + volume — CC 2 shapes clarinet dynamics naturally
  filter:
    cutoff:
      sweep: |
        Doina bars 1-8: open, natural clarinet tone
        Freilach bars 9-16: bass filter opens from 600hz to 2khz
        Sher bars 17-24: fully open, everything raw and present
      resonance: low on clarinet, moderate on bass
  articulation:
    legato: true
    portamento:
      time: 25
      switch: on

Automation:
  - track: Clarinet
    param: reverb_wet
    events:
      - beat: 0
        value: 0.4
      - beat: 32
        value: 0.3
        curve: smooth
      - beat: 64
        value: 0.25
        curve: smooth
      - beat: 96
        value: 0.2
        curve: linear
  - track: Accordion
    param: volume
    events:
      - beat: 32
        value: 0.6
      - beat: 64
        value: 0.8
        curve: smooth
      - beat: 96
        value: 1.0
        curve: exp
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.5
      - beat: 32
        value: 0.7
        curve: smooth
      - beat: 96
        value: 1.0
        curve: exp
""",
    ),

    # 39 ── Baroque suite ──────────────────────────────────────────────────
    PromptItem(
        id="baroque_suite",
        title="Baroque suite \u00b7 D major \u00b7 108 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Baroque dance suite \u00b7 Key: D \u00b7 108 BPM\nRole: harpsichord, oboe, English horn, cello\nVibe: elegant x3, stately x2, ornamental, noble, bright",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Baroque dance suite
Key: D
Tempo: 108
Energy: medium
Role: [harpsichord, oboe, english horn, cello]
Constraints:
  bars: 24
  time_signature: 3/4
Vibe: [elegant x3, stately x2, ornamental, noble, bright, courtly, sparkling]

Request: |
  A Baroque dance suite movement in 3/4 time, in three sections.
  8-bar gavotte — the harpsichord (GM 6) plays a bright D major
  figured bass with ornamental right-hand runs, the cello (GM 42)
  provides a walking bass line. Stately, courtly, elegant. 8-bar
  musette — the oboe (GM 68) enters with a pastoral melody over a
  drone bass (cello holds open D), the harpsichord thins to simple
  accompaniment, and the English horn (GM 69) adds a warm counter-
  melody a third below the oboe. The texture is lighter, more
  pastoral. 8-bar gavotte return — the full gavotte returns with
  all four voices: harpsichord figured bass, oboe melody, English
  horn countermelody, cello walking bass. The harpsichord adds
  trills and mordents on cadential notes. Bach's court. Versailles.
  Candlelight on polished floors.

Harmony:
  progression: |
    Gavotte (1-8): [D, A, Bm, F#m, G, D, A7, D]
    Musette (9-16): [D, D, G, D, D, D, A, D] — over drone bass
    Return (17-24): [D, A, Bm, F#m, G, D, A7, D]
  voicing: figured bass — harpsichord realizes continuo
  rhythm: 3/4, dance feel — strong beat 1, lighter 2 and 3
  extensions: some 7ths on dominant (A7), suspensions

Melody:
  scale: D major
  register: oboe D4-D6, English horn A3-A5
  contour: |
    Gavotte: harpsichord right hand, ornamental, stepwise with leaps.
    Musette: oboe pastoral melody, gentle arches.
    Return: oboe melody with harpsichord ornaments added.
  phrases:
    structure: 4-bar antecedent, 4-bar consequent
  density: medium — 8th and 16th notes with ornaments
  ornamentation:
    - trills on cadential notes
    - mordents on beat 1
    - turns on long notes

Dynamics:
  overall: mf throughout (Baroque terraced dynamics)
  arc:
    - bars: 1-8
      level: mf
      shape: gavotte, stately
    - bars: 9-16
      level: mp
      shape: musette, lighter, pastoral
    - bars: 17-24
      level: mf
      shape: gavotte return, full texture
  accent_velocity: 88
  ghost_velocity: 48

Rhythm:
  feel: gavotte — a moderate duple dance in cut time, with the
    characteristic upbeat start (half-bar anacrusis). Each phrase
    begins on beat 3, giving the dance its elegant forward motion.
    The musette shifts to a pastoral lilt over the cello drone.
  subdivision: 8th notes with ornamental 16ths and 32nds
  swing: 50%
  accent:
    pattern: |
      Gavotte: upbeat start — phrases begin on beat 3, accent on 1
      of the next bar. This anacrusis creates the stately forward
      lean that makes the gavotte a walking dance, not a standing one.
      Musette: gentler accents, the drone smooths everything.
      Harpsichord: terraced dynamics (no crescendo), ornamental
      accents on trills and mordents.

Orchestration:
  harpsichord:
    instrument: harpsichord (GM 6)
    technique: |
      Gavotte: figured bass, ornamental right hand.
      Musette: simple accompaniment.
      Return: full figured bass with trills and mordents.
    entry: bar 1
  oboe:
    instrument: oboe (GM 68)
    technique: pastoral melody, clean articulation
    register: D4-D6
    entry: bar 9
  english_horn:
    instrument: English horn (GM 69)
    technique: warm countermelody, a third below oboe
    register: A3-A5
    entry: bar 9
  cello:
    instrument: cello (GM 42)
    technique: |
      Gavotte: walking bass, quarter notes.
      Musette: drone on open D.
      Return: walking bass.
    register: D2-D4
    entry: bar 1

Effects:
  all:
    reverb: palace hall, 2s, 15ms predelay — the resonance of stone
      and crystal. Baroque music was composed for specific rooms.
      This is the room.
    compression: none — Baroque dynamics are terraced, not continuous.
      The harpsichord cannot crescendo. Dynamics are structural,
      achieved by adding or removing voices, not by getting louder.

Expression:
  arc: stately to pastoral to triumphant return
  narrative: |
    The gavotte at bar 1 is the court: harpsichord sparkling, cello
    walking, every note a gesture of elegance. The musette at bar 9
    is the garden: the oboe's pastoral melody floats over the drone
    like birdsong over a fountain. The English horn adds warmth below,
    the way sunlight warms the back of your neck. The return at bar 17
    brings the court back, but now the garden is in the room — oboe
    and English horn join the harpsichord and cello, and the full
    texture is both stately and warm. Trills and mordents sparkle
    like candlelight on polished floors. Bach's court. Handel's garden.
    Telemann's grace.
  character: Bach's precision. Handel's melody. Telemann's charm.
    Rameau's elegance. The sound of the 18th century at its most refined.

Texture:
  density: medium (gavotte) to transparent (musette) to full (return)
  register_spread: D2-D6
  space:
    principle: |
      Baroque texture is contrapuntal — each voice is independent,
      and the beauty is in how they interweave. The harpsichord's
      right hand and the cello's walking bass create a frame. The
      oboe and English horn at bar 9 add a middle layer. In the
      return at bar 17, four independent voices create a polyphonic
      tapestry where you can follow any single thread and hear a
      complete melody. The space between voices is as composed as
      the voices themselves. This is the art of counterpoint: the
      architecture of simultaneous melodies.

Form:
  structure: gavotte-musette-gavotte_return
  development:
    - section: gavotte (bars 1-8)
      intensity: mf — harpsichord and cello, stately court dance
    - section: musette (bars 9-16)
      variation: oboe and English horn enter, pastoral, drone bass
    - section: gavotte_return (bars 17-24)
      contrast: all four voices, full texture, ornamental

Humanization:
  timing:
    jitter: 0.025
    late_bias: 0.0
    grid: 8th
  velocity:
    arc: phrase
    stdev: 8
    accents:
      beats: [0]
      strength: 8
    ghost_notes:
      probability: 0.02
      velocity: [42, 55]
  feel: Baroque pulse — even, elegant, not metronomic but measured

MidiExpressiveness:
  expression:
    curve: terraced — mf, mp, mf
    range: [58, 92]
  cc_curves:
    - cc: 91
      from: 32
      to: 52
      position: bars 1-24
    - cc: 11
      from: 58
      to: 92
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds subtle dynamic emphasis on harpsichord ornamental notes
    use: velocity variation — pressure creates nuanced terraced dynamics
  modulation:
    instrument: oboe
    depth: Baroque vibrato — CC 1 value 25-50, narrow and controlled
    onset: delayed 1 beat on sustained notes only
  breath_control:
    instrument: oboe
    mapping: filter cutoff + volume — CC 2 shapes oboe phrasing and dynamics
  filter:
    cutoff:
      sweep: |
        Gavotte bars 1-8: open, bright harpsichord tone
        Musette bars 9-16: cello drone slightly warmer, gentle rolloff
        Return bars 17-24: fully open, all voices clear and present
      resonance: low throughout — Baroque clarity
  articulation:
    legato: true

Automation:
  - track: Oboe
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
      - beat: 24
        value: 0.3
        curve: smooth
      - beat: 48
        value: 0.35
        curve: smooth
      - beat: 72
        value: 0.3
        curve: smooth
  - track: Cello
    param: volume
    events:
      - beat: 0
        value: 0.7
      - beat: 24
        value: 0.5
        curve: smooth
      - beat: 48
        value: 0.7
        curve: smooth
  - track: Master
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
      - beat: 48
        value: 0.35
        curve: smooth
      - beat: 72
        value: 0.3
        curve: smooth
""",
    ),

    # 40 ── Balkan brass ───────────────────────────────────────────────────
    PromptItem(
        id="balkan_brass",
        title="Balkan brass \u00b7 Gm \u00b7 160 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Balkan brass / \u010do\u010dek \u00b7 Key: Gm \u00b7 160 BPM\nRole: trumpet, trombone, tuba, snare, bass drum\nVibe: frantic x3, joyful x2, wild, virtuosic, celebration",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Balkan brass cocek
Key: Gm
Tempo: 160
Energy: extreme
Role: [trumpet, trombone, tuba, snare, bass drum]
Constraints:
  bars: 24
  time_signature: 7/8
Vibe: [frantic x3, joyful x2, wild, virtuosic, celebration, brass, fire]

Request: |
  A Balkan brass band piece in 7/8 time (2+2+3 grouping), in three
  sections. 8-bar march — the tuba (GM 58) establishes the 7/8 oom-pah
  pattern (bass on 1, chord on 3, bass on 5), the bass drum (GM 0)
  and snare establish the asymmetric groove, and a single trumpet
  (GM 56) plays a simple melody in Gm. 8-bar cocek (dance) — the
  full brass section erupts: two trumpets play a rapid melody in 3rds,
  the trombone (GM 57) adds a driving countermelody, the tuba walks
  through the chord changes, and the drumming intensifies with
  paradiddle fills. 8-bar fire — maximum virtuosity: the trumpets
  play 16th-note runs in the Hijaz-derived scale (G-Ab-B-C-D-Eb-F#),
  the trombone glisses between notes, the tuba doubles its walking
  pattern, and the piece builds to a frantic climax. Guca Trumpet
  Festival energy. Wedding celebration. The brass is on fire.

Harmony:
  progression: |
    March (1-8): [Gm, Cm, D7, Gm, Gm, Cm, D7, Gm]
    Cocek (9-16): [Gm, Cm, D7, Gm, Eb, Cm, D7, Gm]
    Fire (17-24): [Gm, Cm, D7, Gm, Eb, Cm, D7, Gm]
  voicing: brass band — close harmony, trumpets in 3rds
  rhythm: 7/8 (2+2+3) oom-pah pattern
  extensions: dom7 on D for tension

Melody:
  scale: G Hijaz-derived (G-Ab-B-C-D-Eb-F#)
  register: trumpets G4-G6, trombone G2-G4
  contour: |
    March: simple melody, 8th notes.
    Cocek: rapid melody in 3rds, 16th notes.
    Fire: virtuosic runs, maximum speed.
  phrases:
    structure: 2-bar phrases, accelerating density
  density: medium (march) to very dense (fire)

Rhythm:
  feel: 7/8 — asymmetric, driving, addictive
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      7/8 grouping: 2+2+3. Accents on beats 1, 3, 5.
      Tuba: bass on 1 and 5, chord on 3.

Dynamics:
  overall: f to fff
  arc:
    - bars: 1-8
      level: f
      shape: march, driving, established
    - bars: 9-16
      level: ff
      shape: cocek, full brass, dance energy
    - bars: 17-24
      level: ff to fff
      shape: fire, maximum virtuosity and intensity
  accent_velocity: 122
  ghost_velocity: 48

Orchestration:
  trumpet:
    instrument: trumpet (GM 56)
    technique: |
      March: simple melody. Cocek: melody in 3rds.
      Fire: virtuosic 16th-note runs.
    register: G4-G6
    entry: bar 1
  trombone:
    instrument: trombone (GM 57)
    technique: |
      Cocek: driving countermelody.
      Fire: glissandi between notes, dramatic.
    register: G2-G4
    entry: bar 9
  tuba:
    instrument: tuba (GM 58)
    technique: 7/8 oom-pah walking bass, anchors everything
    register: G0-G2
    entry: bar 1
  snare:
    instrument: drums (GM 0, channel 10)
    technique: 7/8 pattern with paradiddle fills in cocek/fire
    entry: bar 1
  bass_drum:
    instrument: drums (GM 0, channel 10)
    technique: on beats 1 and 5 of 7/8
    entry: bar 1

Effects:
  brass:
    reverb: outdoor festival, 0.8s
    compression: light — preserve transient attacks
  drums:
    compression: moderate

Expression:
  arc: march to dance to fire
  narrative: |
    The 7/8 groove at bar 1 is the heartbeat of the Balkans — 2+2+3,
    asymmetric, addictive, impossible to resist. The tuba's oom-pah in
    odd meter is the genius of this tradition: a waltz that limps and
    that limp is the groove. When the full brass erupts at bar 9, the
    cocek dance begins — trumpets in 3rds, trombone driving, everyone
    moving in 7. The fire at bar 17 is Guca: the trumpet festival where
    brass bands compete to see who can play fastest, loudest, most
    virtuosically. The 16th-note runs in the Hijaz-derived scale are
    both Turkish and Romani, both ancient and electric. The trombone
    glisses are laughter. The tuba never stops. The party never stops.
  character: Boban Markovic's fire. Fanfare Ciocarlia's speed. Gu\u010da
    Festival's madness. A Serbian wedding at 3am. The brass is on fire.

Texture:
  density: medium (march) to dense (cocek) to maximum (fire)
  register_spread: G0-G6
  space:
    principle: |
      Balkan brass is additive. The march establishes the frame: tuba
      on the bottom, trumpet on top, drums in between. The co\u010dek adds
      trombone in the middle register, and the trumpets double in 3rds.
      The fire section fills every register with virtuosic runs. But
      even at maximum density, the 7/8 asymmetry creates natural
      breathing space — the 2+2+3 grouping means the 3-pulse group
      always has slightly more room. The tuba's oom-pah in odd meter
      is the foundation. The brass stacks above it like layers of a
      wedding cake that someone set on fire.

Form:
  structure: march-cocek-fire
  development:
    - section: march (bars 1-8)
      intensity: f — tuba/drums/trumpet, 7/8 established
    - section: cocek (bars 9-16)
      variation: full brass, dance melody in 3rds, trombone joins
    - section: fire (bars 17-24)
      contrast: virtuosic runs, maximum speed, frantic climax

Humanization:
  timing:
    jitter: 0.03
    late_bias: -0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0, 2, 4]
      strength: 14
    ghost_notes:
      probability: 0.05
      velocity: [42, 58]
  feel: ahead of the beat — Balkan brass leans forward, relentless

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [82, 127]
  pitch_bend:
    style: trombone glissandi, trumpet bends on Hijaz intervals
    depth: 1-2 semitones
  cc_curves:
    - cc: 91
      from: 18
      to: 42
      position: bars 1-24
    - cc: 11
      from: 82
      to: 127
      position: bars 1-24
  aftertouch:
    type: channel
    response: adds bellows pressure dynamics on accordion chords
    use: volume + brightness — pressure intensifies the accordion pump
  modulation:
    instrument: trumpet
    depth: brass vibrato — CC 1 value 35-70
    onset: delayed 1 beat on sustained notes, immediate in fire section
  breath_control:
    instrument: trumpet
    mapping: filter cutoff + volume — CC 2 controls brass dynamics and air
  filter:
    cutoff:
      sweep: |
        March bars 1-8: bass at 1khz, warm and round
        Cocek bars 9-16: bass opens to 3khz, presence emerges
        Fire bars 17-24: fully open, raw brass power
      resonance: low on brass, moderate on bass

Automation:
  - track: Trumpet
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
      - beat: 28
        value: 0.25
        curve: smooth
      - beat: 56
        value: 0.2
        curve: smooth
      - beat: 84
        value: 0.15
        curve: linear
  - track: Snare
    param: pan
    events:
      - beat: 0
        value: -0.1
      - beat: 56
        value: 0.1
        curve: smooth
      - beat: 84
        value: -0.1
        curve: smooth
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.7
      - beat: 56
        value: 0.85
        curve: smooth
      - beat: 84
        value: 1.0
        curve: exp
""",
    ),

]
