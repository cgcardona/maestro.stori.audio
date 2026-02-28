/**
 * piano-roll.js — Canvas-based MIDI piano roll renderer for Muse Hub.
 *
 * Renders a MidiParseResult (from /objects/{id}/parse-midi) into an interactive
 * piano roll.  Features:
 *   - Piano keyboard on the left Y-axis (pitch labels)
 *   - Beat grid on the X-axis with configurable beat-line density
 *   - Per-track colour coding using the Muse Hub design token palette
 *   - Velocity mapped to rectangle opacity (soft notes appear lighter)
 *   - Zoom: horizontal (beats per screen) and vertical (pixels per pitch row)
 *   - Pan: click-drag on the canvas
 *   - Hover tooltip: pitch name, velocity, beat position, duration
 *
 * Usage:
 *   PianoRoll.render(midiParseResult, containerElement, options);
 *
 * Options:
 *   selectedTrack  {number}  -1 = all tracks, 0+ = single track index
 */
(function (global) {
  'use strict';

  // ── Design system track colours ──────────────────────────────────────────
  var TRACK_COLORS = [
    '#58a6ff',  // blue
    '#3fb950',  // green
    '#f0883e',  // orange
    '#bc8cff',  // purple
    '#ff7b72',  // red
    '#79c0ff',  // light blue
    '#56d364',  // light green
    '#ffa657',  // light orange
    '#d2a8ff',  // light purple
    '#ffa198',  // light red
  ];

  // ── MIDI pitch helpers ────────────────────────────────────────────────────
  var NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];

  function pitchToName(pitch) {
    var octave = Math.floor(pitch / 12) - 1;
    return NOTE_NAMES[pitch % 12] + octave;
  }

  function isBlackKey(pitch) {
    var pc = pitch % 12;
    return pc === 1 || pc === 3 || pc === 6 || pc === 8 || pc === 10;
  }

  // ── Keyboard geometry ─────────────────────────────────────────────────────
  var KEY_WIDTH = 36;          // piano key strip width (left margin)
  var MIN_PITCH = 21;          // A0
  var MAX_PITCH = 108;         // C8

  // ── Render entry point ────────────────────────────────────────────────────

  /**
   * Render a MidiParseResult into `container`.
   *
   * @param {Object} midi        MidiParseResult from /parse-midi
   * @param {Element} container  DOM element to render into
   * @param {Object} opts        Optional config { selectedTrack }
   */
  function render(midi, container, opts) {
    opts = opts || {};
    var selectedTrack = (opts.selectedTrack !== undefined) ? opts.selectedTrack : -1;

    // Collect all notes from selected tracks
    var tracks = midi.tracks || [];
    var allNotes = [];
    tracks.forEach(function (t) {
      if (selectedTrack === -1 || t.track_id === selectedTrack) {
        (t.notes || []).forEach(function (n) { allNotes.push(n); });
      }
    });

    // Determine pitch range from notes (+2 semitone padding)
    var pitchMin = MIN_PITCH;
    var pitchMax = MAX_PITCH;
    if (allNotes.length > 0) {
      pitchMin = Math.max(MIN_PITCH, allNotes.reduce(function (m, n) { return Math.min(m, n.pitch); }, 127) - 2);
      pitchMax = Math.min(MAX_PITCH, allNotes.reduce(function (m, n) { return Math.max(m, n.pitch); }, 0) + 2);
    }
    var pitchRange = pitchMax - pitchMin + 1;

    var totalBeats = midi.total_beats || 32;
    var tempoBpm   = midi.tempo_bpm || 120;
    var timeSig    = midi.time_signature || '4/4';

    // Build HTML wrapper with controls
    container.innerHTML = pianoRollHtml(midi, tracks, selectedTrack, totalBeats, tempoBpm, timeSig);

    var canvas  = container.querySelector('#piano-canvas');
    var outer   = container.querySelector('#piano-roll-outer');
    var tooltip = document.getElementById('tooltip') || container.querySelector('#tooltip');

    if (!canvas || !outer) return;

    // ── State ──────────────────────────────────────────────────────────────
    var zoomX       = 60;    // px per beat
    var zoomY       = 14;    // px per pitch row
    var panX        = 0;     // horizontal pan offset in beats
    var panY        = 0;     // vertical pan offset in pitch rows
    var isPanning   = false;
    var lastMouseX  = 0;
    var lastMouseY  = 0;
    var dpr         = window.devicePixelRatio || 1;

    function outerW() { return outer.clientWidth || 800; }
    function outerH() { return Math.min(Math.max(pitchRange * zoomY + 40, 200), 600); }

    function resize() {
      var w = outerW();
      var h = outerH();
      outer.style.height = h + 'px';
      canvas.width  = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width  = w + 'px';
      canvas.style.height = h + 'px';
    }

    function draw() {
      var w  = outerW();
      var h  = outerH();
      var ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      var rollW = w - KEY_WIDTH;
      var rollH = h - 20;   // header strip height

      // ── Background ───────────────────────────────────────────────────────
      ctx.fillStyle = '#0d1117';
      ctx.fillRect(0, 0, w, h);

      // ── Pitch row backgrounds (alternating + black-key shading) ──────────
      for (var p = pitchMin; p <= pitchMax; p++) {
        var y = pitchToY(p, h);
        ctx.fillStyle = isBlackKey(p) ? '#131820' : '#0d1117';
        ctx.fillRect(KEY_WIDTH, y, rollW, zoomY);

        // C markers
        if (p % 12 === 0) {
          ctx.fillStyle = '#1f2937';
          ctx.fillRect(KEY_WIDTH, y, rollW, 1);
        }
      }

      // ── Beat grid ────────────────────────────────────────────────────────
      var beatsPerScreen = rollW / zoomX;
      var beatStart = Math.floor(panX);
      var beatEnd   = Math.ceil(panX + beatsPerScreen + 1);
      var beatStep  = zoomX < 8 ? 8 : zoomX < 20 ? 4 : zoomX < 40 ? 2 : 1;

      for (var b = beatStart; b <= beatEnd; b += beatStep) {
        var bx = beatToX(b, rollW);
        var isMeasure = b % 4 === 0;
        ctx.strokeStyle = isMeasure ? '#30363d' : '#1a2030';
        ctx.lineWidth = isMeasure ? 1 : 0.5;
        ctx.beginPath();
        ctx.moveTo(bx, 20);
        ctx.lineTo(bx, h);
        ctx.stroke();

        // Beat label
        if (isMeasure && bx >= KEY_WIDTH) {
          ctx.fillStyle = '#8b949e';
          ctx.font = '9px monospace';
          ctx.fillText(b, bx + 2, 14);
        }
      }

      // ── Notes ─────────────────────────────────────────────────────────────
      allNotes.forEach(function (n) {
        var x1 = beatToX(n.start_beat, rollW);
        var x2 = beatToX(n.start_beat + n.duration_beats, rollW);
        var ny = pitchToY(n.pitch, h);
        var nw = Math.max(x2 - x1 - 1, 2);
        var nh = Math.max(zoomY - 1, 3);

        if (x2 < KEY_WIDTH || x1 > w) return;  // off-screen cull

        var trackColor = TRACK_COLORS[n.track_id % TRACK_COLORS.length];
        var alpha = 0.4 + (n.velocity / 127) * 0.6;

        ctx.globalAlpha = alpha;
        ctx.fillStyle = trackColor;
        ctx.fillRect(Math.max(x1, KEY_WIDTH), ny + 1, nw, nh);

        // Bright top edge
        ctx.globalAlpha = alpha * 0.8;
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(Math.max(x1, KEY_WIDTH), ny + 1, nw, 1);

        ctx.globalAlpha = 1;
      });

      // ── Piano keyboard strip ───────────────────────────────────────────────
      for (var pk = pitchMin; pk <= pitchMax; pk++) {
        var pky = pitchToY(pk, h);
        var black = isBlackKey(pk);
        ctx.fillStyle = black ? '#1a1a1a' : '#e6edf3';
        ctx.fillRect(0, pky + 1, black ? KEY_WIDTH * 0.65 : KEY_WIDTH - 1, Math.max(zoomY - 1, 2));

        // Note name label on C notes and every octave boundary
        if (!black && pk % 12 === 0) {
          ctx.fillStyle = '#58a6ff';
          ctx.font = '9px monospace';
          ctx.fillText(pitchToName(pk), 2, pky + zoomY - 2);
        }
      }

      // ── Header bar ────────────────────────────────────────────────────────
      ctx.fillStyle = '#161b22';
      ctx.fillRect(KEY_WIDTH, 0, rollW, 20);
      ctx.fillStyle = '#0d1117';
      ctx.fillRect(0, 0, KEY_WIDTH, 20);

      // Tempo / time sig info
      ctx.fillStyle = '#8b949e';
      ctx.font = '10px monospace';
      ctx.fillText(tempoBpm.toFixed(1) + ' BPM  ' + timeSig, KEY_WIDTH + 6, 13);
    }

    function pitchToY(pitch, h) {
      var row = (pitchMax - pitch) - panY;
      return 20 + row * zoomY;
    }

    function beatToX(beat, rollW) {
      return KEY_WIDTH + (beat - panX) * zoomX;
    }

    // ── Controls ──────────────────────────────────────────────────────────
    var zoomXInput = container.querySelector('#zoom-x');
    var zoomYInput = container.querySelector('#zoom-y');
    var trackSel   = container.querySelector('#track-sel');

    if (zoomXInput) {
      zoomXInput.addEventListener('input', function () {
        zoomX = parseInt(this.value, 10);
        resize();
        draw();
      });
    }
    if (zoomYInput) {
      zoomYInput.addEventListener('input', function () {
        zoomY = parseInt(this.value, 10);
        resize();
        draw();
      });
    }
    if (trackSel) {
      trackSel.addEventListener('change', function () {
        selectedTrack = parseInt(this.value, 10);
        allNotes = [];
        tracks.forEach(function (t) {
          if (selectedTrack === -1 || t.track_id === selectedTrack) {
            (t.notes || []).forEach(function (n) { allNotes.push(n); });
          }
        });
        // Recompute pitch range
        if (allNotes.length > 0) {
          pitchMin = Math.max(MIN_PITCH, allNotes.reduce(function (m, n) { return Math.min(m, n.pitch); }, 127) - 2);
          pitchMax = Math.min(MAX_PITCH, allNotes.reduce(function (m, n) { return Math.max(m, n.pitch); }, 0) + 2);
          pitchRange = pitchMax - pitchMin + 1;
        }
        resize();
        draw();
      });
    }

    // ── Pan ──────────────────────────────────────────────────────────────────
    canvas.addEventListener('mousedown', function (e) {
      isPanning  = true;
      lastMouseX = e.clientX;
      lastMouseY = e.clientY;
      outer.classList.add('panning');
    });

    window.addEventListener('mousemove', function (e) {
      if (isPanning) {
        var dx = e.clientX - lastMouseX;
        var dy = e.clientY - lastMouseY;
        panX = Math.max(0, panX - dx / zoomX);
        panY = Math.max(0, panY - dy / zoomY);
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        draw();
      }
      // Tooltip
      if (!isPanning) showTooltip(e);
    });

    window.addEventListener('mouseup', function () {
      isPanning = false;
      outer.classList.remove('panning');
    });

    canvas.addEventListener('mouseleave', function () {
      if (tooltip) tooltip.style.display = 'none';
    });

    function showTooltip(e) {
      if (!tooltip || !canvas) return;
      var rect = canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var my = e.clientY - rect.top;
      var rollW = outerW() - KEY_WIDTH;
      var h = outerH();

      if (mx < KEY_WIDTH || my < 20) { tooltip.style.display = 'none'; return; }

      var beat  = panX + (mx - KEY_WIDTH) / zoomX;
      var pitch = pitchMax - Math.floor((my - 20) / zoomY) - Math.round(panY);

      // Find note under cursor
      var hit = null;
      for (var i = 0; i < allNotes.length; i++) {
        var n = allNotes[i];
        if (n.pitch === pitch && n.start_beat <= beat && (n.start_beat + n.duration_beats) >= beat) {
          hit = n;
          break;
        }
      }

      if (!hit) { tooltip.style.display = 'none'; return; }

      tooltip.innerHTML =
        '<strong>' + pitchToName(hit.pitch) + '</strong> (MIDI ' + hit.pitch + ')<br>' +
        'Beat: ' + hit.start_beat.toFixed(2) + '<br>' +
        'Duration: ' + hit.duration_beats.toFixed(2) + ' beats<br>' +
        'Velocity: ' + hit.velocity + '<br>' +
        'Track: ' + hit.track_id + ' / Ch ' + hit.channel;
      tooltip.style.display = 'block';
      tooltip.style.left = (e.clientX + 14) + 'px';
      tooltip.style.top  = (e.clientY - 10) + 'px';
    }

    // ── Window resize ─────────────────────────────────────────────────────
    window.addEventListener('resize', function () { resize(); draw(); });

    // ── Initial render ────────────────────────────────────────────────────
    resize();
    draw();
  }

  // ── HTML scaffold builder ─────────────────────────────────────────────────

  function pianoRollHtml(midi, tracks, selectedTrack, totalBeats, tempoBpm, timeSig) {
    var trackOpts = '<option value="-1">All tracks</option>' +
      tracks.map(function (t) {
        return '<option value="' + t.track_id + '">' +
          escHtml(t.name || ('Track ' + t.track_id)) + ' (' + (t.notes || []).length + ' notes)</option>';
      }).join('');

    var legendItems = tracks.map(function (t, i) {
      var color = TRACK_COLORS[t.track_id % TRACK_COLORS.length];
      return '<div class="track-legend-item">' +
        '<div class="track-legend-swatch" style="background:' + color + '"></div>' +
        escHtml(t.name || ('Track ' + t.track_id)) +
        '</div>';
    }).join('');

    return '<div class="piano-roll-wrapper">' +
      '<div class="piano-roll-controls">' +
        '<label>Track: <select id="track-sel">' + trackOpts + '</select></label>' +
        '<label>H-Zoom: <input type="range" id="zoom-x" min="4" max="200" value="60" style="width:80px"></label>' +
        '<label>V-Zoom: <input type="range" id="zoom-y" min="4" max="40" value="14" style="width:60px"></label>' +
        '<span style="font-size:12px;color:#8b949e;margin-left:auto">' +
          totalBeats.toFixed(1) + ' beats &bull; ' +
          tempoBpm.toFixed(1) + ' BPM &bull; ' +
          escHtml(timeSig) +
        '</span>' +
      '</div>' +
      '<div id="piano-roll-outer"><canvas id="piano-canvas"></canvas></div>' +
      '<div class="track-legend">' + legendItems + '</div>' +
    '</div>';
  }

  function escHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Export ────────────────────────────────────────────────────────────────
  global.PianoRoll = { render: render };

}(window));
