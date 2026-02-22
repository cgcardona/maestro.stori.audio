"""STORI PROMPT pool — Cinematic & Experimental.

Covers: cinematic orchestral buildup, ambient drone, and future
additions (Sufi ney meditation, Gregorian chant ambient, progressive
rock 5/4, Afro-Cuban rumba, minimalist phasing, through-composed
cinematic score).
"""

from app.models.maestro_ui import PromptItem

PROMPTS_CINEMATIC: list[PromptItem] = [

    # 3 ── Cinematic orchestral buildup ──────────────────────────────────────
    PromptItem(
        id="cinematic_buildup",
        title="Cinematic orchestral buildup \u00b7 Dm \u00b7 88 BPM",
        preview="Mode: compose \u00b7 Section: buildup\nStyle: cinematic orchestral \u00b7 Key: Dm \u00b7 88 BPM\nRole: strings, brass, timpani, choir pad\nVibe: cinematic x3, tense x2, triumphant",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: buildup
Style: cinematic orchestral
Key: Dm
Tempo: 88
Energy: building
Role: [strings, brass, timpani, choir pad]
Constraints:
  bars: 32
  density: orchestral
Vibe: [cinematic x3, tense x2, triumphant, epic, heartbreaking]

Request: |
  A four-act cinematic orchestral journey. 8 bars of near-silence — low
  string tremolo and a solo French horn melody, ppp, the void before
  creation. 8 bars of emergence — violas enter with countermelody, harp
  arpeggios, the harmony grows. 8 bars of conflict — full strings, brass
  cluster dissonance, timpani war drums, Csus4 refusing to resolve. Then
  8 bars of triumph — the surprise Gmaj pivot in bar 25 turns D minor
  into D major. Full orchestra. Choir. Timpani cannon shots. The
  impossible made real. Hans Zimmer meets Beethoven's Ninth.

Harmony:
  progression: |
    Void (1-8): [Dm, Dm, Bbmaj7, Bbmaj7, Fmaj7, Fmaj7, Csus4, Csus4]
    Emergence (9-16): [Dm, Bbmaj7, Fmaj7, Am7, Dm, Bb, F, Csus4]
    Conflict (17-24): [Dm, Dm/C, Bbmaj7, A7sus4, Dm, Bb, Csus4, Csus4]
    Triumph (25-32): [Gmaj, G/B, Cmaj7, D, Gmaj, Em, Cmaj7, Dmaj]
  voicing: |
    Void: unison low strings, solo horn.
    Emergence: 2-voice, then 3-voice divisi.
    Conflict: thick clusters, dissonant brass.
    Triumph: full orchestral doublings, every register.
  rhythm: |
    Bars 1-8: whole notes, no rhythmic attacks.
    Bars 9-16: half notes, harp arpeggios fill.
    Bars 17-24: quarter-note pulse, driving.
    Bars 25-32: powerful quarter-note hits, brass fanfare rhythm.
  extensions: major 7ths on Bb and F, 9th on Csus4
  tension:
    point: bar 24
    device: Csus4 held for 2 full bars, brass dissonance piles up
    release: Gmaj pivot beat 1 bar 25 — the sky breaks open

Melody:
  scale: D natural minor bars 1-24, D major from bar 25
  register: |
    Void: French horn D3-A4
    Emergence: violins A4-D6, violas countermelody C4-G5
    Conflict: full range, brass and strings in octaves
    Triumph: peak at A5, then descending resolution to D5
  contour: |
    Void: solo horn, ascending stepwise, lonely and searching.
    Emergence: strings pick up the horn melody, expand it upward.
    Conflict: fragmented, interrupted by brass stabs and timpani.
    Triumph: long triumphant line, rising to A5, then resolving
    stepwise down to D5 in the final bar. Earned. Complete.
  phrases:
    structure: 4-bar statements, each louder and thicker
  density: sparse bars 1-8, medium bars 9-16, dense bars 17-32
  ornamentation:
    - trills on cadential notes in triumph section
    - grace notes on horn melody in void section

Rhythm:
  feel: straight, majestic
  subdivision: |
    Bars 1-16: quarter-note feel, half-note harmonic rhythm
    Bars 17-32: quarter-note pulse, then 8th-note drive in bar 29-32
  accent:
    pattern: |
      Bars 1-8: downbeats only. Bars 9-16: beats 1 and 3.
      Bars 17-24: every beat, building weight.
      Bars 25-32: every beat, full accent, cymbal crashes on 1 and 3.
  pushed_hits:
    - beat: 3.5
      anticipation: quarter-note pickup into bar 25 — the pivot moment

Dynamics:
  overall: ppp to fff over 32 bars
  arc:
    - bars: 1-8
      level: ppp
      shape: flat — the void
    - bars: 9-12
      level: ppp to p
      shape: linear
    - bars: 13-16
      level: p to mp
      shape: linear, strings growing
    - bars: 17-20
      level: mp to f
      shape: exponential — conflict intensifies
    - bars: 21-24
      level: f to ff
      shape: exponential — timpani enters, brass clusters
    - bars: 25-28
      level: fff
      shape: instant — the Gmaj pivot unleashes everything
    - bars: 29-32
      level: fff
      shape: sustained power, slight decrescendo in final bar to D5 resolve
  accent_velocity: 127
  ghost_velocity: 20
  expression_cc:
    curve: match dynamic arc — exponential rise
    range: [15, 127]

Orchestration:
  strings:
    bars_1_8: low tremolo, violins I divisi — ppp, sul tasto
    bars_9_16: add violas with countermelody, violins II enter — mp
    bars_17_24: full section tutti, arco — forte, aggressive bowing
    bars_25_32: full section fff, violins in octaves, triumphant
    articulation: |
      Tremolo bars 1-8. Sustained arco bars 9-16.
      Marcato bars 17-24. Grandioso bars 25-32.
      Col legno accent bar 25 beat 1.
    vibrato: delayed onset bars 1-8, full from bar 9, wide and intense from bar 25
  brass:
    bars_1_8: solo French horn melody — haunting, lonely
    bars_9_16: add second horn doubling at octave
    bars_17_24: full brass — trumpets, trombones, tuba, cluster dissonance
    bars_25_32: brass fanfare — triumphant, major key, bright and open
  timpani:
    bars_17_24: war drums — quarter-note pulse, crescendo roll bars 23-24
    bars_25_32: cannon shots on beats 1 and 3, rolls between
    accent: powerful downbeat hits, alternating with sustained rolls
  choir_pad:
    style: wordless aahs, slow attack
    bars_17_24: enter pp, building
    bars_25_32: full choir, bright vowels, triumphant
  harp:
    bars_9_16: gentle arpeggios, Dm and Bbmaj7 chord tones
    bars_25_32: glissando on Gmaj at bar 25 beat 1

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
  choir:
    reverb: cathedral, 5s, 40ms predelay, 60% wet
  harp:
    reverb: same hall bus, 30% wet

Expression:
  arc: void to emergence to conflict to triumph
  narrative: |
    Four acts of a story told without words. The void is loneliness — a
    single horn playing to no one. The emergence is hope — strings join,
    tentatively, then with conviction. The conflict is everything that
    stands between you and what you need — the brass fights the strings,
    the timpani hammers, the Csus4 refuses to give in. And then bar 25.
    G major. The key you never expected. The sky breaks open. The choir
    sings what the horn was trying to say all along. Not relief. Not
    happiness. Triumph. The thing you fought for, earned.
  tension_points:
    - bar: 17
      device: timpani enters with war-drum quarter notes
    - bar: 22
      device: brass cluster dissonance, Csus4 piling up
    - bar: 24
      device: full silence on beat 4 — one beat of absolute nothing
    - bar: 25
      device: Gmaj pivot — full orchestra, choir, harp glissando, timpani cannon
  spatial_image: |
    Void: horn center, tremolo strings distant and wide.
    Emergence: strings spread, harp left, horn center.
    Conflict: timpani front-center, brass center-forward, strings wide.
    Triumph: everything — choir above, timpani below, brass forward,
    strings everywhere. The entire hall is sound.
  character: Hans Zimmer's scale. John Williams' melody. Beethoven's soul.

Texture:
  density: ppp void to fff orchestral over 32 bars
  register_spread: C2 timpani to A5 violins
  layering:
    strategy: |
      Add one instrument family every 8 bars:
      1-8: strings + solo horn.
      9-16: + violas, harp.
      17-24: + full brass, timpani, choir begins.
      25-32: everything, full power.
  space: |
    Bars 1-8 are mostly silence — the void before creation.
    Bars 25-32 are total saturation — every register filled.

Form:
  structure: void-emergence-conflict-triumph
  development:
    - section: void (bars 1-8)
      intensity: ppp — solo horn, low string tremolo, near-silence
    - section: emergence (bars 9-16)
      variation: violas countermelody, harp arpeggios, harmony grows
    - section: conflict (bars 17-24)
      contrast: full brass, timpani war drums, Csus4 refuses to resolve
    - section: triumph (bars 25-32)
      variation: Gmaj pivot, full orchestra + choir + harp glissando
  variation_strategy: |
    Each section doubles the forces and emotional intensity.
    The Gmaj pivot in bar 25 is the structural keystone — everything
    before it builds toward this moment. Everything after it celebrates.

Humanization:
  timing:
    jitter: 0.02
    late_bias: 0.005
    grid: 16th
  velocity:
    arc: section
    stdev: 10
    accents:
      beats: [0]
      strength: 15
    ghost_notes:
      probability: 0.02
      velocity: [18, 30]
  feel: classical rubato — slight give and take around the beat

MidiExpressiveness:
  modulation:
    instrument: strings
    depth: |
      Void: no vibrato — CC 1 at 0.
      Emergence: slow vibrato onset — CC 1 from 0 to 35.
      Conflict: full vibrato — CC 1 at 50.
      Triumph: wide intense vibrato — CC 1 at 65-80.
    onset: delayed 1 beat from note attack
  expression:
    curve: match dynamic arc — exponential rise
    range: [15, 127]
  cc_curves:
    - cc: 11
      from: 15
      to: 127
      position: bars 1-32
    - cc: 91
      from: 40
      to: 85
      position: bars 1-32
    - cc: 1
      from: 0
      to: 80
      position: bars 1-32
  pitch_bend:
    style: none — classical convention, no bends
  aftertouch:
    type: channel
    response: gentle swell on sustained notes
    use: expression boost on peaks, especially bar 25 onward
  breath_control:
    instrument: French horns
    mapping: dynamics and air noise — CC 2 controls tone from covered muted (0) to bright open bell (127)
  filter:
    cutoff:
      sweep: strings brightness swell — dark sul tasto (bar 1) to full brilliance (bar 25)
      resonance: low

Automation:
  - track: Strings
    param: reverb_wet
    events:
      - beat: 0
        value: 0.5
      - beat: 64
        value: 0.35
        curve: smooth
      - beat: 96
        value: 0.6
        curve: linear
  - track: Choir
    param: volume
    events:
      - beat: 64
        value: 0.2
      - beat: 96
        value: 1.0
        curve: exp
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -6db
      - beat: 96
        value: 0db
        curve: smooth
""",
    ),

    # 5 ── Ambient drone ─────────────────────────────────────────────────────
    PromptItem(
        id="ambient_drone",
        title="Ambient drone \u00b7 D \u00b7 58 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: ambient / drone \u00b7 Key: D \u00b7 58 BPM\nRole: pads, arp, sub drone, texture, piano\nVibe: dreamy x3, atmospheric x2, minimal, peaceful",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: intro
Style: ambient drone
Key: D
Tempo: 58
Energy: very low
Role: [pads, arp, sub drone, texture, piano]
Constraints:
  bars: 32
  density: sparse
Vibe: [dreamy x3, atmospheric x2, minimal, peaceful, distant, transcendent]

Request: |
  A 32-bar ambient meditation in four phases. 8 bars of pure stillness —
  only the sub drone on D1, barely audible, and the faintest granular
  texture like dust in light. 8 bars of awakening — a warm analog pad
  fades in with an open Dmaj9, painfully slow attack. 8 bars of drift —
  a pentatonic arp enters, random-gated, drifting in and out of focus,
  and a single prepared piano note rings once every 4 bars. 8 bars of
  dissolve — the arp thins, the pad opens up, the filter slowly closes,
  everything returns to the drone. You end where you began, but changed.
  Brian Eno's Music For Airports meets Stars of the Lid. Time is not
  linear here.

Harmony:
  progression: |
    Stillness (1-8): [D drone — no harmony, just fundamental]
    Awakening (9-16): [Dmaj9, Dmaj9, Asus2, Asus2, Gmaj7, Gmaj7, Dmaj7, Dmaj7]
    Drift (17-24): [Dmaj9, Asus2, Gmaj7, Dmaj7]
    Dissolve (25-32): [Dmaj9, Dmaj9, Dmaj9, Dmaj9, D drone, D drone, D drone, D drone]
  voicing: open, stacked 4ths and 5ths — never close voiced
  rhythm: held whole notes, no rhythmic attacks
  extensions: 9ths and maj7ths throughout
  color: luminous, unresolved — always floating, never arriving

Melody:
  scale: D major pentatonic
  register: upper (A4-D6)
  contour: |
    Stillness: no melody — only the drone exists.
    Awakening: no melody — just the pad breathing.
    Drift: arp provides melody fragments — D, F#, A, D, random order.
    Dissolve: arp thins to single notes every 4 beats, then silence.
  phrases:
    structure: free — no metric grid, no phrase boundaries
    breath: long silences — notes every 2-4 beats on average
  density: zero (stillness) to very sparse (drift) to zero (dissolve)

Rhythm:
  feel: floating — no clear pulse at any point
  subdivision: free, no grid
  swing: 50%

Dynamics:
  overall: ppp to mp and back to ppp
  arc:
    - bars: 1-8
      level: ppp
      shape: flat — almost silence
    - bars: 9-16
      level: ppp to pp
      shape: glacial swell
    - bars: 17-20
      level: pp to mp
      shape: arp brings gentle presence
    - bars: 21-24
      level: mp
      shape: peak — the most present the piece ever gets
    - bars: 25-28
      level: mp to pp
      shape: dissolving
    - bars: 29-32
      level: pp to ppp
      shape: returning to the drone, almost silence
  accent_velocity: 55
  ghost_velocity: 15

Orchestration:
  sub_drone:
    pitch: D1
    technique: pure sine, extremely slow attack 4s
    level: barely audible — felt more than heard
    presence: bars 1-32 — the only constant
  pads:
    instrument: warm analog pad, slow attack 3s, slow release 4s
    voicing: open Dmaj9 spread over 3 octaves
    stereo: wide \u00b165
    entry: bar 9 — fades in across 4 bars
    exit: bar 29 — fades out across 4 bars
  arp:
    pattern: D-F#-A-D pentatonic, random gate, random order
    filter: resonant lowpass, slowly opening bars 17-24
    reverb: very long 6s decay
    entry: bar 17
    exit: bar 28 — thins and disappears
  texture:
    style: granular — stretched recordings of piano harmonics
    pitch: random \u00b12 semitones
    density: sparse clouds, present bars 1-8 and 25-32
  piano:
    instrument: prepared piano, single high note (D5)
    articulation: struck very softly, let ring with long sustain
    occurrences: bar 17 beat 1, bar 21 beat 1 — only two strikes

Effects:
  pads:
    reverb: huge hall, 7s decay, 50ms predelay, 70% wet
    chorus: very slow, subtle pitch modulation, 0.05 depth
  arp:
    reverb: same hall bus, 85% wet
    delay: dotted quarter, 45% wet, high feedback (10 repeats)
  texture:
    reverb: infinite — frozen reverb pad
    filter: gentle lowpass at 2.5khz
  piano:
    reverb: same hall, 90% wet — the note dissolves into space
  sub_drone:
    filter: lowpass at 120hz — only the fundamental

Expression:
  arc: stillness to presence to memory to stillness
  narrative: |
    You are alone in a cathedral made of light. The drone is the building.
    The pad is the light coming through glass. The arp is a thought you
    almost had. The piano is a memory — it strikes twice and you spend
    the rest of the piece wondering if you imagined it. By the dissolve
    you are not sure if the music is still playing or if the silence has
    always sounded like this. Time is not linear here. It never was.
  spatial_image: |
    Everything wide and diffuse. Sub drone: center and below.
    Pad: everywhere. Arp: scattered, no fixed position.
    Texture: above and behind. Piano: center, distant.
  character: Brian Eno's patience. Stars of the Lid's warmth. Silence
    as an instrument.

Texture:
  density: almost nothing (stillness/dissolve) to very sparse (drift)
  register_spread: D1-D6
  layering:
    strategy: each layer occupies its own frequency band with no overlap
  space:
    principle: |
      The silence between notes is the music. Never fill the space.
      Let sounds decay for as long as they need to. This piece is
      90% silence. The 10% that is sound exists only to make you
      aware of the silence around it.

Form:
  structure: stillness-awakening-drift-dissolve
  development:
    - section: stillness (bars 1-8)
      intensity: ppp — sub drone and granular dust only
    - section: awakening (bars 9-16)
      variation: pad fades in, harmony emerges from the drone
    - section: drift (bars 17-24)
      contrast: arp enters, piano strikes twice, peak presence
    - section: dissolve (bars 25-32)
      variation: arp thins, pad fades, return to drone and silence
  variation_strategy: |
    A palindrome. You arrive and you depart. The middle is the most
    present the piece ever gets, and even then it is barely there.
    The form is a breath: inhale, hold, exhale.

Humanization:
  timing:
    jitter: 0.08
    late_bias: 0.03
    grid: free
  velocity:
    arc: section
    stdev: 8
    accents:
      beats: []
      strength: 0
    ghost_notes:
      probability: 0.0
      velocity: [10, 20]
  feel: free — no grid, no pulse, no expectation

MidiExpressiveness:
  expression:
    curve: palindrome swell ppp-mp-ppp over 32 bars
    range: [15, 72]
  modulation:
    instrument: pads
    depth: very slow pitch shimmer — CC 1 value 0-12
    onset: bars 12-28 only
  cc_curves:
    - cc: 91
      from: 55
      to: 88
      position: bars 1-24
    - cc: 91
      from: 88
      to: 55
      position: bars 25-32
    - cc: 74
      from: 15
      to: 60
      position: bars 9-24
    - cc: 74
      from: 60
      to: 15
      position: bars 25-32
    - cc: 1
      from: 0
      to: 12
      position: bars 12-28
  sustain_pedal:
    style: full sustain throughout — everything rings
    changes_per_bar: 0
  aftertouch:
    type: channel
    response: slow filter opening on pad — pressure widens low-pass cutoff
    use: gradual brightness on sustained pad tones, peak warmth at bar 16

Automation:
  - track: Pads
    param: reverb_wet
    events:
      - beat: 32
        value: 0.4
      - beat: 64
        value: 0.7
        curve: smooth
      - beat: 96
        value: 0.9
        curve: smooth
      - beat: 128
        value: 0.4
        curve: smooth
  - track: Arp
    param: filter_cutoff
    events:
      - beat: 64
        value: 800hz
      - beat: 80
        value: 3khz
        curve: log
      - beat: 112
        value: 400hz
        curve: smooth
  - track: Master
    param: lowpass
    events:
      - beat: 0
        value: 2khz
      - beat: 64
        value: 6khz
        curve: smooth
      - beat: 112
        value: 1.5khz
        curve: smooth
""",
    ),

    # 43 ── Polynesian/Taiko fusion ────────────────────────────────────────
    PromptItem(
        id="polynesian_taiko_fusion",
        title="Polynesian/Taiko fusion \u00b7 Am \u00b7 80 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Polynesian / Taiko fusion \u00b7 Key: Am \u00b7 80 BPM\nRole: taiko, pan flute, log drums, choir\nVibe: ceremonial x3, powerful x2, ancient, oceanic, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Polynesian Taiko fusion
Key: Am
Tempo: 80
Energy: high
Role: [taiko, pan flute, log drums, choir]
Constraints:
  bars: 24
  time_signature: 5/4
Vibe: [ceremonial x3, powerful x2, ancient, oceanic, sacred, warrior, primal]

Request: |
  A Polynesian/Japanese fusion piece in 5/4 time, in three sections.
  8-bar haka call — the taiko (GM 116) alone, massive bass drums in
  5/4 (3+2 grouping), each hit reverberating. At bar 5, log drums
  (GM 115 woodblock) add a polyrhythmic counter-pattern. Ceremonial,
  primal, warrior energy. 8-bar ocean — the pan flute (GM 75) enters
  with a Polynesian melody in A minor pentatonic, long sustained
  notes like wind over the Pacific. The taiko continues but softens.
  The log drums create a flowing 8th-note pattern. 8-bar convergence —
  the choir (GM 52) enters with a haka-style chant on the root (A),
  rhythmic and powerful, doubling the taiko pattern. The pan flute
  soars. All elements converge on the downbeat of bar 21 in a massive
  unison hit, then the final 4 bars build to the biggest taiko hit
  of the piece. The ocean and the mountain meet. The warrior and the
  wave. Ancient Polynesia meets ancient Japan.

Harmony:
  progression: |
    All sections: Am pentatonic drone. Minimal harmony — power is
    rhythmic, not harmonic.
  voicing: unison, octave doublings, drone
  rhythm: 5/4 (3+2), polyrhythmic layering

Melody:
  scale: A minor pentatonic (A-C-D-E-G)
  register: pan flute A4-A6
  contour: |
    Haka: no melody — pure rhythm.
    Ocean: pan flute sustained notes, wide intervals.
    Convergence: choir chant on root, pan flute soars above.
  density: sparse (haka/ocean) to powerful (convergence)

Dynamics:
  overall: f to fff
  arc:
    - bars: 1-8
      level: f
      shape: taiko alone, then log drums, ceremonial
    - bars: 9-16
      level: mf to f
      shape: pan flute enters, ocean, softer taiko
    - bars: 17-20
      level: f to ff
      shape: choir enters, convergence begins
    - bars: 21-24
      level: ff to fff
      shape: unison hit at bar 21, build to massive finale
  accent_velocity: 125
  ghost_velocity: 45

Rhythm:
  feel: 5/4 ceremonial — 3+2 grouping, where the 3-pulse is the
    warrior's stamp and the 2-pulse is the wave receding. This is
    not Western odd meter. This is the oldest time signature on
    earth — the asymmetric heartbeat of Pacific ritual.
  subdivision: 8th notes
  swing: 50%
  accent:
    pattern: |
      Taiko: massive hits on beat 1 and beat 4 (the start of the
      3-group and the start of the 2-group). Each hit is an event.
      The space between hits is as composed as the hits themselves.
      Log drums: polyrhythmic counter-pattern — flowing 8ths that
      create a 3-against-2 tension with the taiko's 3+2 grouping.
      This is the rhythmic DNA of both Polynesian and Japanese
      ceremonial drumming — layers of pattern that interlock.
      Choir: rhythmic unison with taiko at convergence, the haka
      stomp amplified by voice.

Orchestration:
  taiko:
    instrument: taiko drum (GM 116)
    technique: |
      Haka: massive hits in 5/4, ceremonial.
      Ocean: continues softer, steady pulse.
      Convergence: full force, choir doubles pattern.
    entry: bar 1
  pan_flute:
    instrument: pan flute (GM 75)
    technique: Polynesian melody, long sustained notes, breathy
    register: A4-A6
    entry: bar 9
  log_drums:
    instrument: woodblock (GM 115)
    technique: polyrhythmic counter-pattern, flowing 8ths
    entry: bar 5
  choir:
    instrument: choir aahs (GM 52)
    technique: haka-style rhythmic chant on root (A)
    entry: bar 17

Effects:
  taiko:
    reverb: outdoor valley, 3s — the reverberations off volcanic
      cliffs. Each hit echoes for seconds. The echo is part of the
      composition.
    eq: deep sub presence at 60hz — you feel taiko before you hear it
  pan_flute:
    reverb: ocean cavern, 2.5s — the breathy tone floats and diffuses
    eq: gentle air at 8khz for the breath sound
  log_drums:
    reverb: shorter, 0.8s — the polyrhythmic pattern needs definition
  choir:
    reverb: same valley as taiko, 3s — voices and drums in the same
      massive natural space

Expression:
  arc: warrior to ocean to convergence
  narrative: |
    The taiko at bar 1 is the mountain — each hit a declaration of
    existence, reverberating across the valley. The 5/4 meter is
    asymmetric and ancient, a heartbeat that limps because it is older
    than symmetry. When the pan flute enters at bar 9, it is the ocean —
    the Pacific, endless, carrying songs between islands. The choir at
    bar 17 is the haka — the warrior dance, rhythmic and primal, voices
    as percussion. The convergence at bar 21 is the moment the mountain
    meets the ocean: taiko and choir and pan flute and log drums all
    hitting the downbeat together. Ancient Polynesia meets ancient Japan.
    The warrior and the wave. They were always the same.
  character: Taiko drumming of Kodo. Polynesian navigation chants.
    The haka. The Pacific Ocean as a concert hall.

Texture:
  density: sparse (haka) to medium (ocean) to massive (convergence)
  register_spread: A0-A6
  space:
    principle: |
      This piece is about physical space. The taiko occupies the sub
      frequencies — you feel it in your bones. The log drums occupy
      the upper-mid register — the woody click is sharp and defined.
      The pan flute floats in the highs — breathy, ethereal, sky.
      Between these three, there is enormous empty space. That
      emptiness is the Pacific Ocean. The choir at bar 17 fills it —
      the haka chant is the human voice claiming the space between
      earth and sky, between mountain and ocean. The convergence at
      bar 21 is every register occupied at maximum force. The
      emptiness becomes fullness. The silence becomes thunder.

Form:
  structure: haka_call-ocean-convergence
  development:
    - section: haka_call (bars 1-8)
      intensity: f — taiko and log drums, 5/4 ceremonial
    - section: ocean (bars 9-16)
      variation: pan flute enters, ocean melody, taiko softens
    - section: convergence (bars 17-24)
      contrast: choir chant, unison hit bar 21, massive finale

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.0
    grid: 8th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0, 3]
      strength: 18
    ghost_notes:
      probability: 0.03
      velocity: [38, 52]
  feel: ceremonial — each hit placed with intention, 5/4 groove

MidiExpressiveness:
  expression:
    curve: rises to massive finale
    range: [72, 127]
  cc_curves:
    - cc: 91
      from: 28
      to: 55
      position: bars 1-24
    - cc: 11
      from: 72
      to: 127
      position: bars 1-24
  aftertouch:
    type: channel
    response: resonance swell on mallet instruments — pressure deepens tone body
    use: sustain intensity on log drums, adds presence to pan flute held notes
  modulation:
    instrument: pan flute
    depth: gentle breath vibrato — CC 1 value 0-30
    onset: delayed 2 beats from note attack
  breath_control:
    instrument: pan flute
    mapping: air pressure and breathy tone — CC 2 controls breath noise mix from pure (0) to airy (90)
  filter:
    cutoff:
      sweep: taiko low-end rumble — sub filter opens from 60hz to 200hz across convergence section
      resonance: moderate

Automation:
  - track: Pan Flute
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
      - beat: 60
        value: 0.65
        curve: smooth
      - beat: 120
        value: 0.8
        curve: linear
  - track: Taiko
    param: volume
    events:
      - beat: 0
        value: 0.85
      - beat: 40
        value: 0.55
        curve: smooth
      - beat: 80
        value: 1.0
        curve: exp
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -3db
      - beat: 120
        value: +2db
        curve: smooth
""",
    ),

    # 44 ── Sufi ney meditation ────────────────────────────────────────────
    PromptItem(
        id="sufi_ney_meditation",
        title="Sufi ney meditation \u00b7 Dm \u00b7 60 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: Sufi meditation \u00b7 Key: Dm \u00b7 60 BPM\nRole: ney flute, frame drum, drone\nVibe: meditative x4, spiritual x3, whirling, trance, sacred",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: intro
Style: Sufi meditation
Key: Dm
Tempo: 60
Energy: low
Role: [ney flute, frame drum, drone]
Constraints:
  bars: 24
Vibe: [meditative x4, spiritual x3, whirling, trance, sacred, Rumi, dervish]

Request: |
  A Sufi ney meditation piece in three sections. 8-bar breath — the
  ney flute (GM 73) alone, playing a single sustained D4 with maximum
  breath noise, the sound of reed and air. The note bends up a
  quarter-tone, wavers, settles. A second phrase: D4 to A4, a slow
  5th, then back. The drone (low D sustained pad) enters at bar 5.
  8-bar whirl — a frame drum (GM 0, channel 10) enters with the Sufi
  rhythmic cycle, a gentle 6/8 pattern that mimics the rotation of
  the whirling dervish. The ney plays longer phrases now, D-F-G-A-C,
  the Dm pentatonic, each note a breath, each silence a prayer.
  8-bar trance — the ney reaches its most passionate phrase, ascending
  to D5 and sustaining, the frame drum intensifies slightly, the drone
  swells, and the piece reaches a state of gentle ecstasy before
  settling back to the single sustained D4 of the opening. The circle
  closes. The whirling continues. Rumi's poetry as sound.

Harmony:
  progression: |
    All sections: Dm drone. No harmonic changes.
  voicing: drone on D, ney melody above
  extensions: none — Sufi music is modal and melodic

Melody:
  scale: D minor pentatonic (D-F-G-A-C)
  register: ney D4-D5
  contour: |
    Breath: single D4 with bends, then D4-A4 5th.
    Whirl: longer phrases, ascending through pentatonic.
    Trance: peaks D5, sustained, then returns to D4.
  density: very sparse throughout

Dynamics:
  overall: ppp to mp to ppp
  arc:
    - bars: 1-8
      level: ppp
      shape: ney alone, breath, drone enters bar 5
    - bars: 9-16
      level: pp to mp
      shape: frame drum, longer phrases
    - bars: 17-20
      level: mp
      shape: peak, ney sustains D5
    - bars: 21-24
      level: mp to ppp
      shape: returns to D4, settling, silence
  accent_velocity: 58
  ghost_velocity: 18

Rhythm:
  feel: |
    Breath (1-8): free, unmeasured. The ney's phrases are dictated by
    the breath of the player, not by a metronome. Time is circular.
    Whirl (9-16): the frame drum introduces a gentle 6/8 cycle — the
    Sufi usul (rhythmic mode) that mimics the revolution of the
    whirling dervish. One rotation per bar. The ney floats above this
    cycle, sometimes aligned, sometimes free.
    Trance (17-24): the 6/8 cycle continues but the ney's phrasing
    lengthens until it transcends the meter entirely.
  subdivision: triplet 8ths (6/8 feel)
  swing: 50%
  accent:
    pattern: |
      Frame drum: the Sufi usul — a circular 6/8 pattern with
      emphasis on beat 1 (dum) and beat 4 (tek). The pattern
      repeats like the dervish's rotation. The accent is not
      percussive but gravitational — you feel the turn.

Orchestration:
  ney:
    instrument: flute (GM 73)
    technique: maximum breath noise, slow phrases, quarter-tone bends
    register: D4-D5
    entry: bar 1
  frame_drum:
    instrument: drums (GM 0, channel 10)
    technique: Sufi 6/8 cycle, gentle, whirling rhythm
    entry: bar 9
  drone:
    instrument: sustained pad
    technique: D1 continuous, swelling in trance section
    entry: bar 5

Effects:
  ney:
    reverb: stone chamber, 4s, very wet — the ney was played in
      tekkes (Sufi lodges), stone rooms where the sound reverberates
      for seconds. The reverb is the room. The room is the meditation.
    eq: no processing — the breath noise IS the instrument. The ratio
      of air to tone is the expressive parameter.
  frame_drum:
    reverb: same stone chamber, shorter 2s — the drum is closer
    eq: gentle warmth at 150hz for the skin resonance
  drone:
    reverb: infinite sustain, no attack — the drone emerges from
      silence and returns to silence

Expression:
  arc: breath to whirl to trance to breath
  narrative: |
    The ney is said to cry because it was cut from the reed bed and
    longs to return. Every note is that longing. The single D4 at
    bar 1 is the first breath of prayer — not a musical statement
    but a spiritual one. The drone at bar 5 is the earth holding the
    prayer. The frame drum at bar 9 begins the whirl — the dervish
    turns, and the rhythm mimics the rotation, steady, circular,
    trance-inducing. The ney's ascent to D5 at bar 17 is the closest
    this music comes to ecstasy — not the ecstasy of volume but of
    presence. And then the return to D4 at bar 21. The circle closes.
    The whirling continues after the music stops. It never stopped.
    Rumi: "What you seek is seeking you."
  character: The ney of Konya. The Mevlevi whirling dervishes.
    Rumi's poetry. Shams of Tabriz. The sound of seeking.

Texture:
  density: extremely sparse throughout — this is music of absence
  register_spread: D1-D5
  space:
    principle: |
      Sufi music is the sound of space. The drone occupies the very
      bottom — D1, felt more than heard. The ney occupies a narrow
      band — D4 to D5, one octave, the human voice range. Between
      them: nothing. That nothing is the meditation. The frame drum
      enters the middle register but barely — it is felt as pulse,
      not heard as percussion. Each ney phrase is surrounded by
      silence, and the silence is not empty — it is the space
      between breaths, the space between rotations of the dervish,
      the space where God is.

Form:
  structure: breath-whirl-trance
  development:
    - section: breath (bars 1-8)
      intensity: ppp — ney alone, drone enters bar 5
    - section: whirl (bars 9-16)
      variation: frame drum enters, longer ney phrases
    - section: trance (bars 17-24)
      contrast: ney peaks D5, returns to D4, circular

Humanization:
  timing:
    jitter: 0.1
    late_bias: 0.03
    grid: quarter
  velocity:
    arc: phrase
    stdev: 8
    accents:
      beats: [0]
      strength: 3
    ghost_notes:
      probability: 0.01
      velocity: [12, 22]
  feel: completely free — no grid, each note a breath

MidiExpressiveness:
  expression:
    curve: follows dynamic arc
    range: [10, 62]
  pitch_bend:
    style: ney quarter-tone bends, breath-driven wavering
    depth: quarter-tone
  cc_curves:
    - cc: 91
      from: 42
      to: 72
      position: bars 1-24
    - cc: 11
      from: 10
      to: 62
      position: bars 1-24
  articulation:
    legato: true
  aftertouch:
    type: channel
    response: vibrato depth on ney — pressure intensifies ornamental wavering
    use: expressive swells on sustained tones, especially trance section peaks
  modulation:
    instrument: ney flute
    depth: ornamental vibrato — CC 1 value 0-45, subtle in breath section, full in trance
    onset: delayed 1 beat from note attack
  breath_control:
    instrument: ney flute
    mapping: primary expression — CC 2 controls air volume and reed noise from whisper (0) to full cry (127)
  filter:
    cutoff:
      sweep: drone brightness — slow low-pass opening from 200hz to 2khz over 24 bars
      resonance: low

Automation:
  - track: Ney Flute
    param: reverb_wet
    events:
      - beat: 0
        value: 0.4
      - beat: 32
        value: 0.55
        curve: smooth
      - beat: 64
        value: 0.7
        curve: smooth
      - beat: 96
        value: 0.5
        curve: smooth
  - track: Frame Drum
    param: delay_feedback
    events:
      - beat: 32
        value: 0.15
      - beat: 64
        value: 0.35
        curve: smooth
      - beat: 96
        value: 0.2
        curve: linear
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.6
      - beat: 48
        value: 0.85
        curve: smooth
      - beat: 96
        value: 0.5
        curve: smooth
""",
    ),

    # 45 ── Gregorian chant ambient ────────────────────────────────────────
    PromptItem(
        id="gregorian_chant_ambient",
        title="Gregorian chant ambient \u00b7 D dorian \u00b7 52 BPM",
        preview="Mode: compose \u00b7 Section: intro\nStyle: Gregorian chant ambient \u00b7 Key: D dorian \u00b7 52 BPM\nRole: choir, drone, bells\nVibe: sacred x4, ancient x3, still, stone, eternal",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: intro
Style: Gregorian chant ambient
Key: D dorian
Tempo: 52
Energy: very low
Role: [choir, drone, bells]
Constraints:
  bars: 24
Vibe: [sacred x4, ancient x3, still, stone, eternal, monastic, candlelight]

Request: |
  A Gregorian chant ambient piece in three sections. 8-bar silence —
  a deep drone on D0 (sustained pad or low organ), barely audible,
  the sound of stone walls. At bar 5, tubular bells (GM 14) strike
  a single D3 that rings for 4 bars. Nothing else. 8-bar chant — the
  choir (GM 52) enters with a monophonic Gregorian melody in D dorian
  (D-E-F-G-A-B-C-D), unison male voices, one note per beat, no
  harmony, no rhythm — just the melody moving stepwise through the
  mode. Neumatic style: 2-3 notes per syllable. 8-bar resonance — the
  choir adds a second voice a 5th above (A), creating the earliest
  form of Western harmony (organum). The bell strikes again at bar 17.
  The drone swells slightly. At bar 22, the choir sustains a unison D3
  that fades into the drone. The stone walls absorb the sound. The
  candles flicker. The prayer continues in silence.

Harmony:
  progression: |
    All sections: D dorian drone. No harmonic changes.
    Resonance: parallel organum at the 5th (D and A).
  voicing: monophonic (chant), parallel 5ths (organum)
  rhythm: no meter — chant follows text rhythm

Melody:
  scale: D dorian (D-E-F-G-A-B-C-D)
  register: choir D3-D4 (bass/baritone)
  contour: |
    Silence: no melody — drone and bell only.
    Chant: stepwise motion, neumatic, one note per beat.
    Resonance: same melody with parallel 5th above.
  density: the sparsest vocal music in Western history

Dynamics:
  overall: ppp to pp to ppp
  arc:
    - bars: 1-4
      level: ppp
      shape: drone alone, barely audible
    - bars: 5-8
      level: ppp
      shape: bell strikes, rings, silence
    - bars: 9-16
      level: pp
      shape: choir chant, monophonic
    - bars: 17-20
      level: pp
      shape: organum, parallel 5ths, bell strikes
    - bars: 21-24
      level: pp to ppp
      shape: choir sustains D3, fades into drone
  accent_velocity: 48
  ghost_velocity: 15

Rhythm:
  feel: unmeasured — Gregorian chant has no bar lines, no time
    signature, no meter. The rhythm follows the Latin text, the
    natural stress of syllables, the breath of the monks. The 52 BPM
    tempo marking is a guide for the drone and bell spacing only.
    The choir is free.
  subdivision: none — each note is its own duration
  swing: n/a
  accent:
    pattern: |
      There are no rhythmic accents in the modern sense. The chant's
      emphasis comes from the text — stressed syllables receive
      slightly longer notes (tenors) and slightly louder singing.
      Neumatic groups of 2-3 notes per syllable create natural
      phrase shapes. The bell strikes are the only rhythmic events
      — D3 at bar 5 and bar 17, each left to ring into silence.

Orchestration:
  choir:
    instrument: choir aahs (GM 52)
    technique: |
      Chant: monophonic, unison male voices.
      Resonance: parallel organum at 5th.
    register: D3-A4
    entry: bar 9
  drone:
    instrument: sustained pad or low organ
    technique: continuous D0, barely audible
    entry: bar 1
  bells:
    instrument: tubular bells (GM 14)
    technique: single strikes, D3, left to ring
    entry: bar 5 (first strike), bar 17 (second strike)

Effects:
  choir:
    reverb: Romanesque abbey, 6s+ decay — stone walls, barrel vault,
      no soft surfaces. This reverb is not an effect. It is the
      architecture. Gregorian chant was composed FOR this reverb.
      The monks chose notes that would reinforce the room's natural
      resonance. The chant and the building are one instrument.
  drone:
    reverb: same abbey, infinite — the drone IS the room tone
  bells:
    reverb: same abbey, maximum decay — the bell's overtones
      multiply in the stone space, ringing for 10+ seconds. Each
      harmonic finds its own resonant frequency in the stone.
    eq: no processing — the bell's natural overtone series is sacred

Expression:
  arc: silence to prayer to harmony to silence
  narrative: |
    The drone at bar 1 is the abbey itself — the resonant frequency
    of stone walls that have been absorbing prayer for a thousand years.
    The bell at bar 5 is the call to prayer — a single D3 that rings
    and rings, the overtones multiplying in the stone space. The choir
    at bar 9 is the prayer itself — monophonic, unison, the oldest
    surviving music in Western civilization. Each note moves stepwise
    because the monks believed that leaps were prideful. The organum
    at bar 17 is the birth of Western harmony — a second voice a 5th
    above, the simplest possible harmony, and yet after 8 bars of
    monophony it sounds like the heavens opening. The final sustained
    D3 at bar 22 fades into the drone. The prayer continues in
    silence. The candles flicker. The stone remembers.
  character: The monks of Solesmes. Hildegard von Bingen. The abbey
    at Cluny. A Romanesque church at vespers. The oldest music in
    the West. The stone is singing.

Texture:
  density: approaching zero — this may be the sparsest music ever composed
  register_spread: D0-A4
  space:
    principle: |
      Gregorian chant is 90% silence. The drone is barely audible.
      The bell strikes twice in 24 bars. The choir sings one note at
      a time. The monks occupy D3-D4, a single octave. Above them:
      nothing. Below them: the drone in the sub-basement of hearing.
      The space between the drone and the choir — three octaves of
      emptiness — is the nave of the church. The organum at bar 17
      adds a single voice at A4, one 5th above, and the effect is
      as dramatic as a full orchestra because the ear has been
      calibrated to silence. In this music, a single interval is
      an event. A 5th is a revelation.

Form:
  structure: silence-chant-resonance
  development:
    - section: silence (bars 1-8)
      intensity: ppp — drone and bell only, stone walls
    - section: chant (bars 9-16)
      variation: monophonic choir, D dorian, unison
    - section: resonance (bars 17-24)
      contrast: organum at 5th, bell strikes, fades to drone

Humanization:
  timing:
    jitter: 0.08
    late_bias: 0.02
    grid: quarter
  velocity:
    arc: phrase
    stdev: 5
    accents:
      beats: [0]
      strength: 2
    ghost_notes:
      probability: 0.0
      velocity: [10, 18]
  feel: completely free — chant follows text, not grid

MidiExpressiveness:
  expression:
    curve: follows dynamic arc, ppp to pp to ppp
    range: [10, 45]
  cc_curves:
    - cc: 91
      from: 55
      to: 85
      position: bars 1-24
    - cc: 11
      from: 10
      to: 45
      position: bars 1-24
  aftertouch:
    type: channel
    response: volume swell on choir — pressure deepens sustained vowels
    use: subtle dynamic emphasis on chant phrases, organum 5ths in resonance section
  modulation:
    instrument: choir
    depth: gentle vibrato — CC 1 value 0-20, monastic restraint
    onset: delayed 2 beats from note attack
  filter:
    cutoff:
      sweep: drone pad brightness — very slow low-pass opening from 150hz to 1.2khz
      resonance: low

Automation:
  - track: Choir
    param: reverb_wet
    events:
      - beat: 0
        value: 0.65
      - beat: 32
        value: 0.7
        curve: smooth
      - beat: 64
        value: 0.8
        curve: smooth
      - beat: 96
        value: 0.75
        curve: smooth
  - track: Drone
    param: volume
    events:
      - beat: 0
        value: 0.3
      - beat: 48
        value: 0.5
        curve: smooth
      - beat: 80
        value: 0.45
        curve: smooth
      - beat: 96
        value: 0.25
        curve: smooth
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -4db
      - beat: 96
        value: -2db
        curve: smooth
""",
    ),

    # 46 ── Progressive rock 5/4 ──────────────────────────────────────────
    PromptItem(
        id="prog_rock_5_4",
        title="Progressive rock 5/4 \u00b7 Em \u00b7 138 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: progressive rock \u00b7 Key: Em \u00b7 138 BPM\nRole: marimba, vibraphone, bass, drums, mellotron\nVibe: complex x3, driving x2, cerebral, virtuosic, angular",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: progressive rock
Key: Em
Tempo: 138
Energy: high
Role: [marimba, vibraphone, bass, drums, mellotron]
Constraints:
  bars: 24
  time_signature: 5/4
Vibe: [complex x3, driving x2, cerebral, virtuosic, angular, mathematical, fire]

Request: |
  A progressive rock piece in 5/4 time (3+2 grouping), in three
  sections. 8-bar riff — the marimba (GM 12) and vibraphone (GM 11)
  play an interlocking riff in Em, the marimba on beats 1-2-3, the
  vibes on beats 4-5, creating a composite melody. The bass (GM 33)
  doubles the marimba in the low register. The drums play a complex
  5/4 pattern. King Crimson meets Tortoise. 8-bar development — the
  mellotron (GM 89 warm pad, representing the tape-loop keyboard)
  enters with sustained chords, the interlocking pattern inverts
  (vibes on 1-2-3, marimba on 4-5), the bass adds chromatic passing
  tones, and the drums play a polyrhythmic 4-against-5 hi-hat pattern.
  8-bar climax — all voices converge: the marimba and vibes play in
  unison, the mellotron swells to maximum, the bass plays a driving
  8th-note pattern, and at bar 21 the drums shift to a double-time
  feel within 5/4. Maximum complexity, maximum energy.

Harmony:
  progression: |
    Riff (1-8): [Em, G, Am, Em, Cmaj7, Bm, Em, Em]
    Development (9-16): [Cmaj7, Bm, Am, G, Em, Dm, Em, Em]
    Climax (17-24): [Em, G, Cmaj7, Bm, Am, Dm, Bm, Em]
  voicing: |
    Interlocking marimba/vibes. Mellotron: thick 3-note chords.
  rhythm: 5/4 (3+2), interlocking patterns
  extensions: maj7 on C, minor on all others

Melody:
  scale: E natural minor with chromatic passing tones
  register: marimba E3-E5, vibes E4-E6
  contour: |
    Riff: interlocking — marimba 3 beats, vibes 2 beats.
    Development: inversion — vibes 3 beats, marimba 2 beats.
    Climax: unison, driving, peak.
  density: medium (riff) to very dense (climax)

Dynamics:
  overall: mf to fff
  arc:
    - bars: 1-8
      level: mf to f
      shape: riff established, interlocking
    - bars: 9-16
      level: f
      shape: development, mellotron, inversion
    - bars: 17-24
      level: f to fff
      shape: climax, unison, double-time drums
  accent_velocity: 118
  ghost_velocity: 48

Rhythm:
  feel: 5/4 (3+2) — the 3-group is where the melodic weight lands,
    the 2-group is the breath between. The asymmetry creates a
    perpetual sense of forward motion because Western ears expect
    resolution on a power-of-2 beat that never comes.
  subdivision: 16th notes
  swing: 50%
  accent:
    pattern: |
      The interlocking pattern is the rhythm: marimba on beats 1-2-3,
      vibes on beats 4-5. The accents shift with the inversion at
      bar 9. The genius is that the composite pattern — marimba + vibes
      together — creates a continuous 5-beat cycle where the accent
      rotates to a different beat each bar. The 4-against-5 hi-hat in
      the development layers a 4-beat cycle over the 5-beat meter,
      creating an LCM cycle of 20 beats (4 bars) before both patterns
      realign. This is mathematics as groove.

Orchestration:
  marimba:
    instrument: marimba (GM 12)
    technique: |
      Riff: beats 1-2-3 of 5/4.
      Development: beats 4-5 (inverted).
      Climax: unison with vibes.
    register: E3-E5
    entry: bar 1
  vibraphone:
    instrument: vibraphone (GM 11)
    technique: |
      Riff: beats 4-5 of 5/4.
      Development: beats 1-2-3 (inverted).
      Climax: unison with marimba.
    register: E4-E6
    entry: bar 1
  bass:
    instrument: fingered bass (GM 33)
    technique: |
      Riff: doubles marimba. Development: chromatic passing tones.
      Climax: driving 8th notes.
    register: E1-E3
    entry: bar 1
  drums:
    technique: |
      Riff: complex 5/4. Development: 4-against-5 hi-hat.
      Climax: double-time feel within 5/4.
    entry: bar 1
  mellotron:
    instrument: warm pad (GM 89)
    technique: sustained thick chords, swelling
    entry: bar 9

Effects:
  marimba:
    reverb: medium room, 0.8s — dry enough for interlocking clarity
    eq: gentle presence at 2khz for the mallet attack
  vibraphone:
    reverb: same room, 1s — slightly longer for the metal sustain
    motor: off (riff/development), slow vibrato (climax) — the motor
      adds warmth when the vibes join the marimba in unison
  mellotron:
    reverb: large hall, 2s — the mellotron sits behind the percussion
      instruments, adding analog haze
    eq: rolled off above 8khz — the tape hiss is part of the charm
  drums:
    compression: moderate — tight enough for interlocking precision,
      loose enough for dynamic range
  bass:
    eq: clean, slight mid scoop at 400hz for the chromatic runs

Expression:
  arc: mathematical beauty to complexity to convergence
  narrative: |
    The interlocking riff at bar 1 is a puzzle — marimba and vibes
    each playing half the melody, the composite more than either part.
    This is the prog rock tradition at its best: intellectual fire.
    The development at bar 9 inverts the pattern — what was on beats
    1-2-3 is now on 4-5, and vice versa — and the mellotron adds a
    layer of analog warmth to the mathematical precision. The 4-against-5
    hi-hat is a polyrhythmic brain teaser. The climax at bar 17 resolves
    everything: the marimba and vibes play in unison for the first time,
    the bass drives, and the drums go double-time. The puzzle is solved.
    The solution is fire.
  character: King Crimson's discipline. Tortoise's patience. Steve
    Reich's process. Robert Fripp's guitar (on keyboards). The
    mathematics of ecstasy.

Texture:
  density: medium (riff) to complex (development) to maximum (climax)
  register_spread: E1-E6
  space:
    principle: |
      The interlocking pattern creates texture through absence — the
      marimba rests when the vibes play, and vice versa. The composite
      is continuous but each instrument has gaps. These gaps are the
      breathing room that makes 5/4 feel organic despite its
      mathematical precision. The mellotron at bar 9 fills the
      harmonic space between the attacks, the way fog fills a valley.
      In the climax, the gaps close: marimba and vibes play in
      unison, the bass drives continuously, the drums double-time.
      The texture shifts from pointillist to solid. The puzzle
      becomes a wall. The wall becomes a wave.

Form:
  structure: riff-development-climax
  development:
    - section: riff (bars 1-8)
      intensity: mf — interlocking marimba/vibes, 5/4 established
    - section: development (bars 9-16)
      variation: inversion, mellotron, 4-against-5 polyrhythm
    - section: climax (bars 17-24)
      contrast: unison, double-time drums, maximum

Humanization:
  timing:
    jitter: 0.02
    late_bias: 0.0
    grid: 16th
  velocity:
    arc: flat
    stdev: 10
    accents:
      beats: [0, 3]
      strength: 12
    ghost_notes:
      probability: 0.04
      velocity: [42, 58]
  feel: precise — prog rock demands tight interlocking

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [72, 125]
  modulation:
    instrument: vibraphone
    depth: motorized vibrato — CC 1 value 50-70
    onset: immediate
  cc_curves:
    - cc: 91
      from: 22
      to: 48
      position: bars 1-24
    - cc: 1
      from: 50
      to: 70
      position: bars 1-24
    - cc: 11
      from: 72
      to: 125
      position: bars 1-24
  aftertouch:
    type: channel
    response: filter opening on mellotron — pressure brightens tape-loop timbre
    use: timbral intensity on sustained mellotron chords, adds edge in climax
  filter:
    cutoff:
      sweep: bass fuzz sweep — filter opens from 400hz to 3khz across climax section
      resonance: moderate

Automation:
  - track: Bass
    param: filter_cutoff
    events:
      - beat: 0
        value: 600hz
      - beat: 80
        value: 1.2khz
        curve: smooth
      - beat: 120
        value: 3khz
        curve: exp
  - track: Vibraphone
    param: delay_feedback
    events:
      - beat: 0
        value: 0.1
      - beat: 40
        value: 0.25
        curve: smooth
      - beat: 80
        value: 0.4
        curve: smooth
      - beat: 120
        value: 0.15
        curve: linear
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.75
      - beat: 80
        value: 0.9
        curve: smooth
      - beat: 120
        value: 1.0
        curve: exp
""",
    ),

    # 47 ── Afro-Cuban rumba 6/8 ──────────────────────────────────────────
    PromptItem(
        id="afro_cuban_rumba",
        title="Afro-Cuban rumba \u00b7 C \u00b7 105 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: Afro-Cuban rumba \u00b7 Key: C \u00b7 105 BPM\nRole: congas, claves, tres guitar, bass, pan flute\nVibe: groovy x3, communal x2, earthy, sacred, Havana",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: Afro-Cuban rumba
Key: C
Tempo: 105
Energy: medium
Role: [congas, claves, tres guitar, bass, pan flute]
Constraints:
  bars: 24
  time_signature: 6/8
Vibe: [groovy x3, communal x2, earthy, sacred, Havana, Caribbean, son]

Request: |
  An Afro-Cuban rumba piece in 6/8 (son clave feel), in three
  sections. 8-bar clave — the son clave (GM 0 drums, channel 10)
  establishes the 3-2 clave pattern, the congas (GM 0 drums) enter
  with the tumbao pattern (open tone, slap, bass tone), and the
  rhythm section is just percussion. The clave is the law. 8-bar
  montuno — the tres guitar (GM 105 banjo, representing the Cuban
  3-course guitar) enters with a repeating montuno pattern (arpeggiated
  chords in the clave rhythm), the bass (GM 33) plays a tumbao bass
  line locked to the conga pattern, and the clave-conga-tres groove
  becomes irresistible. 8-bar descarga — the pan flute (GM 76)
  enters with an improvisatory melody over the groove, call-and-
  response with the congas, the tres intensifies, and the piece
  builds to a communal peak. Havana at midnight. The solar
  (courtyard) is full. The rumba has begun.

Harmony:
  progression: |
    Clave (1-8): C drone — no harmony, pure rhythm.
    Montuno (9-16): [C, F, G, C, Am, F, G, C]
    Descarga (17-24): [C, F, G, C, Am, Dm, G, C]
  voicing: tres montuno — arpeggiated chords, bright
  rhythm: 3-2 son clave, tumbao bass, conga pattern

Melody:
  scale: C major with blue notes (Eb, Bb)
  register: pan flute C4-C6
  contour: |
    Clave: no melody — pure rhythm.
    Montuno: tres arpeggiated pattern.
    Descarga: pan flute improvisation, bluesy, soaring.
  density: pure rhythm (clave), medium (montuno), melodic (descarga)

Dynamics:
  overall: mf to f
  arc:
    - bars: 1-8
      level: mf
      shape: clave and congas, rhythmic foundation
    - bars: 9-16
      level: mf to f
      shape: tres and bass, montuno groove
    - bars: 17-24
      level: f
      shape: descarga, pan flute soars, communal peak
  accent_velocity: 108
  ghost_velocity: 42

Rhythm:
  feel: 6/8 with 3-2 son clave — the mother rhythm of Cuba. The clave
    is not a pattern, it is a law. Everything in the ensemble exists
    in relationship to the clave. Notes that fall with the clave are
    stable. Notes that fall against it create tension. This
    tension-and-release IS Cuban music.
  subdivision: 8th notes (triplet feel in 6/8)
  swing: 58%
  accent:
    pattern: |
      Son clave (3-2): three hits in the first bar, two in the second.
      The 3-side is rhythmically dense, the 2-side is open. This
      asymmetry is the engine of everything.
      Congas tumbao: the open tone lands on the and-of-2, the slap
      on 4, the bass tone on 1. Three voices, three timbres, one
      conversation.
      Bass tumbao: the signature anticipated downbeat — the bass
      plays the root a 16th BEFORE beat 1, not on it. This tiny
      anticipation is the lean-forward that makes Cuban music move.
  ghost_notes:
    instrument: congas
    velocity: 35-52

Orchestration:
  congas:
    instrument: drums (GM 0, channel 10)
    technique: tumbao — open tone, slap, bass tone
    entry: bar 1
  claves:
    instrument: drums (GM 0, channel 10)
    technique: 3-2 son clave pattern — the rhythmic law
    entry: bar 1
  tres:
    instrument: banjo (GM 105)
    technique: montuno arpeggiated pattern, locked to clave
    entry: bar 9
  bass:
    instrument: fingered bass (GM 33)
    technique: tumbao bass line — anticipated downbeats
    register: C1-C3
    entry: bar 9
  pan_flute:
    instrument: pan flute (GM 76)
    technique: improvisatory melody, call-response with congas
    register: C4-C6
    entry: bar 17

Effects:
  congas:
    reverb: outdoor courtyard (solar), short 0.5s — the congas must
      be dry and present, each voice (open, slap, bass) distinct
    compression: none — the dynamic contrast between open tone, slap,
      and bass is the instrument's expression
  claves:
    reverb: same courtyard, even drier — the clave is the sharpest
      sound in the ensemble, a wooden click that cuts through everything
  tres:
    reverb: same courtyard, 0.6s — the metallic ring of the tres
      needs a touch of room to sing
  bass:
    reverb: minimal, 0.3s — the anticipated downbeat must be felt,
      not blurred
  pan_flute:
    reverb: courtyard, 1s — the melodic improvisation floats above
      the rhythm section with more space

Expression:
  arc: rhythm to groove to celebration
  narrative: |
    The clave at bar 1 is the law — in Afro-Cuban music, everything
    relates to the clave pattern. The congas speak in three voices:
    open tone, slap, bass. Together they create a conversation that
    has been happening since the Yoruba met the Spanish in Cuba. The
    tres at bar 9 adds the montuno — a repeating arpeggiated pattern
    that locks to the clave like a key in a lock. The bass anticipates
    every downbeat because that is how Cuban music breathes — always
    leaning forward. The descarga at bar 17 is the jam — the pan flute
    calls, the congas respond, and the solar fills with dancers. Havana.
    Midnight. The rumba has begun.
  character: Los Mu\u00f1equitos de Matanzas's conga mastery. Arsenio
    Rodr\u00edguez's tres. Buena Vista Social Club's soul. The solar.
    Havana at midnight. The Yoruba and the Spanish, still dancing.

Texture:
  density: rhythmic (clave) to medium (montuno) to full (descarga)
  register_spread: C1-C6
  space:
    principle: |
      Cuban music is built in layers, each layer related to the clave.
      The clave is the skeleton. The congas are the muscles. The tres
      is the nervous system. The bass is the heartbeat. The pan flute
      is the voice. Each enters in order, and each occupies a specific
      rhythmic and spectral space. The congas are mid-frequency
      percussion. The clave is high-frequency click. The tres is
      bright metallic upper-mids. The bass is deep. The pan flute
      floats above everything. Even at full density in the descarga,
      the clave is always audible because nothing else occupies its
      frequency or rhythmic position. The law is always heard.

Form:
  structure: clave-montuno-descarga
  development:
    - section: clave (bars 1-8)
      intensity: mf — percussion only, rhythmic foundation
    - section: montuno (bars 9-16)
      variation: tres and bass enter, groove established
    - section: descarga (bars 17-24)
      contrast: pan flute improvisation, communal peak

Humanization:
  timing:
    jitter: 0.035
    late_bias: -0.01
    grid: 8th
  velocity:
    arc: cyclic
    stdev: 14
    accents:
      beats: [0, 3]
      strength: 10
    ghost_notes:
      probability: 0.08
      velocity: [35, 52]
  feel: slightly ahead — Cuban music leans forward, anticipation

MidiExpressiveness:
  expression:
    curve: rises across sections
    range: [68, 110]
  cc_curves:
    - cc: 91
      from: 20
      to: 42
      position: bars 1-24
    - cc: 11
      from: 68
      to: 110
      position: bars 1-24
  aftertouch:
    type: channel
    response: velocity-mapped warmth on tres guitar — pressure rounds tone
    use: timbral softening on montuno chords, adds body to pan flute sustains
  modulation:
    instrument: pan flute
    depth: slow vibrato — CC 1 value 0-35, enters during descarga
    onset: delayed 1 beat from note attack
  breath_control:
    instrument: pan flute
    mapping: air flow and breathy color — CC 2 controls breath noise from focused (0) to diffuse (100)
  filter:
    cutoff:
      sweep: bass tumbao — subtle low-pass from 800hz to 2khz across montuno and descarga
      resonance: low

Automation:
  - track: Pan Flute
    param: reverb_wet
    events:
      - beat: 0
        value: 0.25
      - beat: 36
        value: 0.45
        curve: smooth
      - beat: 72
        value: 0.6
        curve: linear
  - track: Congas
    param: pan
    events:
      - beat: 0
        value: -0.15
      - beat: 24
        value: 0.15
        curve: smooth
      - beat: 48
        value: -0.1
        curve: smooth
      - beat: 72
        value: 0.0
        curve: smooth
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.7
      - beat: 48
        value: 0.85
        curve: smooth
      - beat: 72
        value: 0.95
        curve: exp
""",
    ),

    # 48 ── Minimalist phasing ─────────────────────────────────────────────
    PromptItem(
        id="minimalist_phasing",
        title="Minimalist phasing \u00b7 C \u00b7 120 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: minimalist / phasing \u00b7 Key: C \u00b7 120 BPM\nRole: marimba 1, marimba 2, vibraphone\nVibe: hypnotic x4, precise x2, evolving, mathematical, meditative",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: minimalist phasing
Key: C
Tempo: 120
Energy: medium
Role: [marimba 1, marimba 2, vibraphone]
Constraints:
  bars: 32
Vibe: [hypnotic x4, precise x2, evolving, mathematical, meditative, process, pattern]

Request: |
  A Steve Reich-inspired minimalist phasing piece in four sections.
  8-bar unison — two marimbas (GM 12) play the same 12-note pattern
  in C major (C-E-G-D-F-A-E-G-B-D-F-C) in exact unison, 8th notes,
  steady, precise. The pattern repeats. Exact copies. 8-bar phase 1 —
  marimba 2 begins to pull slightly ahead (one 16th note per bar),
  creating interference patterns, new melodies emerging from the overlap.
  The listener hears melodies that neither player is playing. This is
  phasing. 8-bar phase 2 — marimba 2 is now significantly ahead,
  the interference patterns shift, a vibraphone (GM 11) enters with
  a sustained resultant pattern — playing only the notes that emerge
  from the phasing. New harmonies appear. 8-bar convergence — marimba 2
  completes the phase cycle and returns to unison with marimba 1. The
  vibraphone sustains a final C-E-G arpeggio. What was one became two
  became infinite became one again.

Harmony:
  progression: |
    All sections: C major implied by the 12-note pattern.
    No chord changes — harmony emerges from the phasing process.
  voicing: single notes, unison, phasing creates accidental harmony
  rhythm: constant 8th notes, phasing creates rhythmic complexity

Melody:
  scale: C major
  register: marimba C4-B4 (one octave pattern), vibes C5-G5
  contour: |
    The 12-note pattern: C-E-G-D-F-A-E-G-B-D-F-C.
    Repeated continuously. Melody emerges from phasing overlap.
  phrases:
    structure: 12-note repeating pattern, no phrase structure
  density: constant medium — 8th notes throughout

Dynamics:
  overall: mf throughout (steady state)
  arc:
    - bars: 1-8
      level: mf
      shape: unison, steady
    - bars: 9-16
      level: mf
      shape: phase 1 — same dynamics, changing texture
    - bars: 17-24
      level: mf
      shape: phase 2 — vibraphone adds presence
    - bars: 25-32
      level: mf
      shape: convergence, return to unison
  accent_velocity: 88
  ghost_velocity: 52

Rhythm:
  feel: constant 8th notes — metronomic, machine-precise. The beauty
    of minimalist phasing is that the rhythm never changes. The PATTERN
    never changes. What changes is the phase relationship between two
    identical patterns, and that tiny shift creates an infinite
    kaleidoscope of emergent rhythm.
  subdivision: 8th notes
  swing: 50%
  accent:
    pattern: |
      The 12-note pattern (C-E-G-D-F-A-E-G-B-D-F-C) creates its
      own internal accent structure: the repeated notes (E appears
      twice, G appears twice, D appears twice, F appears twice)
      create natural emphasis points. When the phasing begins, these
      emphasis points shift against each other, creating new accents
      that no one is playing. The accents are emergent. The accents
      are the composition.

Orchestration:
  marimba_1:
    instrument: marimba (GM 12)
    technique: steady 8th-note pattern, never varies
    register: C4-B4
    entry: bar 1
  marimba_2:
    instrument: marimba (GM 12)
    technique: |
      Unison (1-8): exact copy of marimba 1.
      Phase 1 (9-16): gradually pulls ahead, 1/16th per bar.
      Phase 2 (17-24): significantly ahead, maximum phase.
      Convergence (25-32): returns to unison.
    register: C4-B4
    entry: bar 1
  vibraphone:
    instrument: vibraphone (GM 11)
    technique: plays resultant pattern — emergent melodies from phasing
    register: C5-G5
    entry: bar 17

Effects:
  marimba_1:
    reverb: medium room, 1s — enough sustain for the notes to
      overlap slightly, creating the shimmering quality
    pan: center-left — slight stereo separation from marimba 2
  marimba_2:
    reverb: same room, 1s — identical to marimba 1
    pan: center-right — the stereo separation makes the phasing
      audible as spatial movement
  vibraphone:
    reverb: same room, 1.5s — slightly wetter, the vibes sit above
      the marimbas in the soundfield
    motor: slow vibrato — the motor adds warmth to the resultant
      pattern, distinguishing it from the source marimbas

Expression:
  arc: unity to divergence to emergence to unity
  narrative: |
    Two marimbas. The same pattern. Exact unison. At bar 9, marimba 2
    begins to move ahead — imperceptibly at first, then undeniably.
    What was one clean pattern becomes a shimmering cloud of notes.
    Your brain hears melodies that no one is playing — phantom patterns
    emerging from the overlap. This is Steve Reich's discovery: the
    process creates the music. The composer sets the rules and steps
    back. At bar 17, the vibraphone makes the phantom melodies real,
    playing only the notes that the phasing reveals. At bar 25,
    marimba 2 returns to unison. What was one became two became
    infinite became one again. The circle is the most beautiful shape.
  character: Steve Reich's Music for 18 Musicians. Terry Riley's In C.
    Philip Glass's patterns. The process is the composition. The music
    plays itself.

Texture:
  density: constant medium — the note density never changes, but the
    perceived complexity transforms from simple to infinite to simple
  register_spread: C4-G5
  space:
    principle: |
      Minimalist phasing is about perceptual space, not frequency
      space. The two marimbas occupy the exact same register (C4-B4).
      In unison, they sound like one instrument. As the phase shifts,
      they sound like a cloud of instruments — the brain cannot
      resolve two copies of the same pattern at different offsets
      into a single image, so it invents new patterns to explain
      what it hears. These phantom patterns are the texture. The
      vibraphone at bar 17 occupies C5-G5, one octave above,
      making the phantom melodies explicit. The texture is not in
      the notes. The texture is in the listener's mind.

Form:
  structure: unison-phase_1-phase_2-convergence
  development:
    - section: unison (bars 1-8)
      intensity: mf — two marimbas in exact unison
    - section: phase_1 (bars 9-16)
      variation: marimba 2 begins phasing ahead
    - section: phase_2 (bars 17-24)
      contrast: maximum phase, vibraphone enters with resultant
    - section: convergence (bars 25-32)
      variation: return to unison, circle closes

Humanization:
  timing:
    jitter: 0.01
    late_bias: 0.0
    grid: 8th
  velocity:
    arc: flat
    stdev: 5
    accents:
      beats: [0]
      strength: 4
    ghost_notes:
      probability: 0.01
      velocity: [48, 58]
  feel: precise — phasing requires exact timing to work

MidiExpressiveness:
  expression:
    curve: constant
    range: [72, 88]
  modulation:
    instrument: vibraphone
    depth: gentle vibrato — CC 1 value 40-55
    onset: bar 17
  cc_curves:
    - cc: 91
      from: 30
      to: 48
      position: bars 1-32
    - cc: 1
      from: 40
      to: 55
      position: bars 17-32
  aftertouch:
    type: channel
    response: resonance swell on marimba — pressure adds sustain ring and body
    use: tonal warmth on held notes, emergent harmonic emphasis during phasing
  filter:
    cutoff:
      sweep: marimba gradual brightness — low-pass opens from 1khz to 4khz over 32 bars
      resonance: low

Automation:
  - track: Marimba 1
    param: reverb_wet
    events:
      - beat: 0
        value: 0.2
      - beat: 64
        value: 0.35
        curve: smooth
      - beat: 128
        value: 0.45
        curve: smooth
  - track: Vibraphone
    param: volume
    events:
      - beat: 64
        value: 0.0
      - beat: 68
        value: 0.4
        curve: smooth
      - beat: 96
        value: 0.6
        curve: smooth
      - beat: 128
        value: 0.7
        curve: linear
  - track: Master
    param: high_shelf
    events:
      - beat: 0
        value: -2db
      - beat: 128
        value: 0db
        curve: smooth
""",
    ),

    # 50 ── Through-composed cinematic score ───────────────────────────────
    PromptItem(
        id="cinematic_through_composed",
        title="Cinematic score \u00b7 Cm \u00b7 68 BPM",
        preview="Mode: compose \u00b7 Section: verse\nStyle: cinematic score \u00b7 Key: Cm \u00b7 68 BPM\nRole: strings, piano, brass, choir, timpani\nVibe: epic x3, dramatic x2, cinematic, emotional, vast",
        full_prompt="""\
STORI PROMPT
Mode: compose
Section: verse
Style: cinematic score through-composed
Key: Cm
Tempo: 68
Energy: medium
Role: [strings, piano, brass, choir, timpani]
Constraints:
  bars: 32
Vibe: [epic x3, dramatic x2, cinematic, emotional, vast, narrative, Hans Zimmer]

Request: |
  A through-composed cinematic score in four sections — no repeating
  sections, each one a new chapter. 8-bar tension — the piece opens
  with sustained low strings (GM 42 cello, GM 43 contrabass) on a Cm
  pedal, pianissimo, the piano (GM 0) adds a slow descending motif
  (C-Bb-Ab-G), and timpani (GM 47) rolls underneath. Something is
  wrong but we don't know what yet. 8-bar revelation — the strings
  shift to tremolo, the piano motif modulates unexpectedly to Ebm,
  the brass (GM 61) enters with a dark fanfare, and the choir (GM 52)
  adds sustained low notes. The revelation: the threat is real.
  8-bar resolution — the key shifts to Ab major (relative major),
  the strings play a soaring melody, the piano plays arpeggiated
  hope, the brass shifts from menace to heroism, and the choir sings
  the main theme. The hero rises. 8-bar transcendence — the full
  orchestra converges: strings, brass, choir, piano, timpani all
  playing the main theme in Cm, but now Cm sounds triumphant — the
  minor key has been recontextualized through the journey. The final
  bar is a massive Cm chord, all voices, held until silence.

Harmony:
  progression: |
    Tension (1-8): [Cm, Cm, Fm, Cm, Abmaj7, Fm, G7, Cm]
    Revelation (9-16): [Ebm, Ebm, Abm, Bb7, Ebm, Abm, Bb7, Ebm]
    Resolution (17-24): [Ab, Db, Eb, Ab, Fm, Db, Eb, Ab]
    Transcendence (25-32): [Cm, Fm, G7, Cm, Ab, Fm, G7, Cm]
  voicing: |
    Tension: low sustained pedals.
    Revelation: tremolo strings, dark brass.
    Resolution: soaring strings, arpeggiated piano.
    Transcendence: full orchestra, maximum voicing.
  rhythm: free, cinematic — no strict rhythm, follows narrative
  extensions: maj7 on Ab, dom7 on G and Bb for dramatic tension

Melody:
  scale: C natural minor → Eb minor → Ab major → C minor
  register: piano C3-C6, strings C2-C6, choir C3-C5
  contour: |
    Tension: descending piano motif (C-Bb-Ab-G), dark.
    Revelation: brass fanfare, dark and angular.
    Resolution: strings soaring melody, wide ascending intervals.
    Transcendence: main theme in all voices, Cm triumphant.
  phrases:
    structure: through-composed — no repeated phrases
  density: sparse (tension) to maximum (transcendence)

Dynamics:
  overall: pp to fff
  arc:
    - bars: 1-8
      level: pp to mp
      shape: tension, low strings, piano motif, timpani roll
    - bars: 9-12
      level: mp to f
      shape: revelation, tremolo strings, brass fanfare
    - bars: 13-16
      level: f
      shape: revelation peaks, choir enters
    - bars: 17-24
      level: f to mf
      shape: resolution, Ab major, hope, soaring
    - bars: 25-30
      level: mf to fff
      shape: transcendence, full orchestra, building
    - bars: 31-32
      level: fff
      shape: final Cm chord, held, silence
  accent_velocity: 120
  ghost_velocity: 35

Rhythm:
  feel: through-composed — no rhythmic pattern repeats. Each section
    has its own temporal character, matched to its narrative purpose.
  subdivision: varies by section
  swing: 50%
  accent:
    pattern: |
      Tension: no rhythmic accents. The timpani roll is a continuous
      texture, the strings are sustained, the piano motif is slow
      and even. Dread has no beat.
      Revelation: tremolo strings create a 32nd-note undulation, the
      brass fanfare introduces rhythmic punctuation for the first time.
      Resolution: arpeggiated piano creates flowing 8th or 16th notes,
      the strings' soaring melody introduces long-note phrasing.
      Transcendence: timpani hits on beats 1 and 3 establish a march
      feel for the first time — the hero has arrived and now there
      IS a pulse.

Orchestration:
  strings:
    instrument: string ensemble (GM 49), cello (GM 42), contrabass (GM 43)
    technique: |
      Tension: sustained low pedal.
      Revelation: tremolo.
      Resolution: soaring melody, legato.
      Transcendence: full section, main theme.
    entry: bar 1
  piano:
    instrument: acoustic grand (GM 0)
    technique: |
      Tension: slow descending motif.
      Resolution: arpeggiated hope.
      Transcendence: full chords.
    entry: bar 1
  brass:
    instrument: brass section (GM 61)
    technique: |
      Revelation: dark fanfare.
      Resolution: heroic transformation.
      Transcendence: main theme, full force.
    entry: bar 9
  choir:
    instrument: choir aahs (GM 52)
    technique: |
      Revelation: sustained low notes.
      Resolution: main theme melody.
      Transcendence: full voice, maximum.
    entry: bar 13
  timpani:
    instrument: timpani (GM 47)
    technique: |
      Tension: roll underneath. Transcendence: driving hits.
    entry: bar 1

Effects:
  strings:
    reverb: large scoring stage, 2.5s
  brass:
    reverb: same stage, slightly drier
  choir:
    reverb: same stage, 50% wet
  timpani:
    compression: moderate, preserve impact

Expression:
  arc: tension → revelation → resolution → transcendence
  narrative: |
    The opening at bar 1 is dread — low strings, descending piano motif,
    timpani rumbling. Something is coming but we can't see it yet.
    The revelation at bar 9 shows us: tremolo strings, the brass fanfare
    in Ebm is the threat made real, the choir's low notes are the
    weight of what must be faced. The modulation to Ab major at bar 17
    is the turn — the same piano that played the descending motif of
    dread now plays arpeggiated hope, and the strings soar with a
    melody that earns its beauty through what came before. The
    transcendence at bar 25 is the full truth: the key returns to Cm
    but now the minor sounds triumphant — not because the darkness was
    defeated, but because it was faced. The final chord at bar 32 is
    Cm, held by every voice in the orchestra, and when it fades to
    silence, the silence is earned. Hans Zimmer's scale. John Williams's
    heart. Ennio Morricone's soul.
  character: Hans Zimmer's Interstellar. John Williams's Schindler's
    List. Ennio Morricone's The Mission. Howard Shore's Lord of the
    Rings. The cinema is dark. The music is light.

Texture:
  density: sparse (tension) to maximum (transcendence)
  register_spread: C0-C6
  space:
    principle: |
      Cinematic scoring is about the strategic management of density.
      Tension (bars 1-8): two layers — low strings pedal, piano motif.
      Maximum emptiness. The timpani roll is felt as vibration, not
      heard as notes. The emptiness IS the dread.
      Revelation (bars 9-16): five layers — tremolo strings, brass
      fanfare, choir, piano, timpani. The sudden density is the
      revelation itself — sound replacing silence, threat made audible.
      Resolution (bars 17-24): the density relaxes slightly — the
      brass transforms from menace to heroism, the strings soar above,
      the piano arpeggios float below. There is space to breathe.
      There is hope.
      Transcendence (bars 25-32): every register occupied by every
      section. The full orchestra at maximum. But it doesn't feel
      dense — it feels unified. Twenty voices playing one theme.
      The many become one. The final Cm chord hangs in the air.
      Then silence. The silence is the earned texture of everything
      that came before it.

Form:
  structure: tension-revelation-resolution-transcendence
  development:
    - section: tension (bars 1-8)
      intensity: pp — low strings, piano motif, timpani
    - section: revelation (bars 9-16)
      variation: tremolo, Ebm, brass fanfare, choir low
    - section: resolution (bars 17-24)
      contrast: Ab major, soaring strings, piano arpeggios, hope
    - section: transcendence (bars 25-32)
      variation: Cm triumphant, full orchestra, main theme, final chord
  variation_strategy: |
    Through-composed — no section repeats. Each is a chapter in a story.
    Tension plants the question. Revelation shows the obstacle.
    Resolution shows the path. Transcendence walks it.

Humanization:
  timing:
    jitter: 0.04
    late_bias: 0.01
    grid: 8th
  velocity:
    arc: phrase
    stdev: 16
    accents:
      beats: [0]
      strength: 10
    ghost_notes:
      probability: 0.03
      velocity: [28, 42]
  feel: cinematic — rubato, dramatic, every gesture intentional

MidiExpressiveness:
  expression:
    curve: follows dramatic arc exactly
    range: [18, 127]
  cc_curves:
    - cc: 91
      from: 32
      to: 68
      position: bars 1-32
    - cc: 11
      from: 18
      to: 127
      position: bars 1-32
    - cc: 1
      from: 20
      to: 70
      position: bars 1-32
  pitch_bend:
    style: strings legato slides, brass fanfare bends
    depth: quarter-tone to half-tone
  articulation:
    legato: true
    portamento:
      time: 30
      switch: on
  aftertouch:
    type: channel
    response: vibrato depth on strings — pressure intensifies expressive wavering
    use: emotional swells on sustained string phrases, peak intensity in transcendence
  modulation:
    instrument: strings
    depth: delayed vibrato onset — CC 1 value 0-65, none in tension, full in transcendence
    onset: delayed 2 beats from note attack
  breath_control:
    instrument: brass (French horns)
    mapping: dynamics and tone color — CC 2 controls from dark covered (0) to bright heroic bell (127)
  filter:
    cutoff:
      sweep: brass muted to open — filter widens from 500hz to 4khz across resolution and transcendence
      resonance: moderate

Automation:
  - track: Strings
    param: reverb_wet
    events:
      - beat: 0
        value: 0.35
      - beat: 32
        value: 0.4
        curve: smooth
      - beat: 64
        value: 0.55
        curve: smooth
      - beat: 96
        value: 0.65
        curve: smooth
      - beat: 128
        value: 0.7
        curve: linear
  - track: Piano
    param: reverb_wet
    events:
      - beat: 0
        value: 0.3
      - beat: 64
        value: 0.45
        curve: smooth
      - beat: 128
        value: 0.5
        curve: smooth
  - track: Master
    param: volume
    events:
      - beat: 0
        value: 0.55
      - beat: 32
        value: 0.65
        curve: smooth
      - beat: 64
        value: 0.8
        curve: smooth
      - beat: 96
        value: 0.95
        curve: exp
      - beat: 128
        value: 1.0
        curve: linear
""",
    ),

]
