/**
 * audio-player.js — MuseHub advanced audio player component.
 *
 * Wraps the vendored WaveSurfer implementation
 * (/musehub/static/vendor/wavesurfer.min.js) to provide:
 *
 *   - Waveform visualization via canvas with seek-on-click
 *   - A/B loop region (Shift+drag on waveform, or programmatic)
 *   - Playback speed control: 0.5x, 0.75x, 1x, 1.25x, 1.5x, 2x
 *   - Time display: current MM:SS / total MM:SS
 *   - Volume slider
 *   - Keyboard shortcuts: Space=play/pause, L=clear loop, ArrowLeft/Right=seek 5s
 *
 * Usage:
 *   const player = AudioPlayer.init({
 *     waveformEl: document.getElementById('waveform'),
 *     playBtnEl:  document.getElementById('play-btn'),
 *     timeCurEl:  document.getElementById('time-cur'),
 *     timeDurEl:  document.getElementById('time-dur'),
 *     speedSelEl: document.getElementById('speed-sel'),
 *     loopBtnEl:  document.getElementById('loop-btn'),
 *     loopInfoEl: document.getElementById('loop-info'),
 *   });
 *   player.load(url, title);
 *
 * The module exposes AudioPlayer as a global. It depends on WaveSurfer being
 * loaded first (via the vendor script tag).
 */
(function (global) {
  'use strict';

  var SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];

  function fmtTime(secs) {
    if (!isFinite(secs) || secs < 0) return '0:00';
    var m = Math.floor(secs / 60);
    var s = Math.floor(secs % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  /**
   * AudioPlayer — stateful wrapper around a WaveSurfer instance.
   *
   * Do not construct directly; use AudioPlayer.init(opts).
   *
   * @param {object} opts - DOM element references and configuration.
   */
  function AudioPlayer(opts) {
    this._ws = null;
    this._opts = opts;
    this._speedIdx = 2; /* default 1.0x */
    this._loopActive = false;
  }

  /**
   * Create and return an AudioPlayer, wiring up all UI event listeners.
   *
   * @param {object} opts
   * @param {HTMLElement} opts.waveformEl   Container for the waveform canvas.
   * @param {HTMLElement} opts.playBtnEl    Play/pause button.
   * @param {HTMLElement} opts.timeCurEl    Current-time display span.
   * @param {HTMLElement} opts.timeDurEl    Duration display span.
   * @param {HTMLElement} [opts.speedSelEl] Speed <select> element.
   * @param {HTMLElement} [opts.loopBtnEl]  Clear-loop button.
   * @param {HTMLElement} [opts.loopInfoEl] Loop-region info display.
   * @param {HTMLElement} [opts.volSliderEl] Volume range input.
   * @returns {AudioPlayer}
   */
  AudioPlayer.init = function (opts) {
    var player = new AudioPlayer(opts);
    player._setup();
    return player;
  };

  AudioPlayer.prototype._setup = function () {
    var self = this;
    var opts = this._opts;

    /* ── WaveSurfer ──────────────────────────────────────────────── */

    this._ws = WaveSurfer.create({
      container: opts.waveformEl,
      waveColor: '#4a5568',
      progressColor: '#1f6feb',
      cursorColor: '#58a6ff',
      height: 80,
      barWidth: 2,
      barGap: 1,
    });

    this._ws.on('ready', function () {
      var dur = self._ws.getDuration();
      if (opts.timeDurEl) opts.timeDurEl.textContent = fmtTime(dur);
      if (opts.playBtnEl) opts.playBtnEl.disabled = false;
    });

    this._ws.on('play', function () {
      if (opts.playBtnEl) opts.playBtnEl.innerHTML = '&#9646;&#9646;';
    });

    this._ws.on('pause', function () {
      if (opts.playBtnEl) opts.playBtnEl.innerHTML = '&#9654;';
    });

    this._ws.on('finish', function () {
      if (opts.playBtnEl) opts.playBtnEl.innerHTML = '&#9654;';
    });

    this._ws.on('timeupdate', function (t) {
      if (opts.timeCurEl) opts.timeCurEl.textContent = fmtTime(t);
    });

    this._ws.on('region-update', function (region) {
      self._loopActive = true;
      if (opts.loopInfoEl) {
        opts.loopInfoEl.textContent =
          'Loop: ' + fmtTime(region.start) + ' – ' + fmtTime(region.end);
        opts.loopInfoEl.style.display = '';
      }
      if (opts.loopBtnEl) opts.loopBtnEl.style.display = '';
    });

    this._ws.on('region-clear', function () {
      self._loopActive = false;
      if (opts.loopInfoEl) opts.loopInfoEl.style.display = 'none';
      if (opts.loopBtnEl) opts.loopBtnEl.style.display = 'none';
    });

    this._ws.on('error', function (msg) {
      if (opts.waveformEl) {
        var errEl = document.createElement('p');
        errEl.style.cssText = 'color:#f85149;padding:16px;margin:0;';
        errEl.textContent = '\u274C Audio unavailable: ' + msg;
        opts.waveformEl.appendChild(errEl);
      }
    });

    /* ── Play/Pause button ───────────────────────────────────────── */

    if (opts.playBtnEl) {
      opts.playBtnEl.disabled = true;
      opts.playBtnEl.addEventListener('click', function () {
        self._ws.playPause();
      });
    }

    /* ── Speed selector ──────────────────────────────────────────── */

    if (opts.speedSelEl) {
      SPEEDS.forEach(function (s, idx) {
        var opt = document.createElement('option');
        opt.value = String(s);
        opt.textContent = s + 'x';
        if (idx === 2) opt.selected = true;
        opts.speedSelEl.appendChild(opt);
      });
      opts.speedSelEl.addEventListener('change', function () {
        self._ws.setPlaybackRate(parseFloat(this.value));
      });
    }

    /* ── Clear-loop button ───────────────────────────────────────── */

    if (opts.loopBtnEl) {
      opts.loopBtnEl.style.display = 'none';
      opts.loopBtnEl.addEventListener('click', function () {
        self._ws.clearRegion();
      });
    }

    if (opts.loopInfoEl) opts.loopInfoEl.style.display = 'none';

    /* ── Volume slider ───────────────────────────────────────────── */

    if (opts.volSliderEl) {
      opts.volSliderEl.addEventListener('input', function () {
        self._ws.setVolume(parseFloat(this.value));
      });
    }

    /* ── Keyboard shortcuts ──────────────────────────────────────── */

    document.addEventListener('keydown', function (e) {
      /* Ignore when focus is in a form element */
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.code === 'Space') {
        e.preventDefault();
        self._ws.playPause();
      } else if (e.code === 'KeyL') {
        self._ws.clearRegion();
      } else if (e.code === 'ArrowLeft') {
        var t = Math.max(0, self._ws.getCurrentTime() - 5);
        var d = self._ws.getDuration();
        if (d > 0) self._ws.seekTo(t / d);
      } else if (e.code === 'ArrowRight') {
        var tc = self._ws.getCurrentTime() + 5;
        var dc = self._ws.getDuration();
        if (dc > 0) self._ws.seekTo(Math.min(1, tc / dc));
      }
    });
  };

  /**
   * Load an audio URL into the player.
   *
   * @param {string} url - Absolute or relative URL of the audio file.
   */
  AudioPlayer.prototype.load = function (url) {
    if (this._opts.playBtnEl) this._opts.playBtnEl.disabled = true;
    if (this._opts.timeCurEl) this._opts.timeCurEl.textContent = '0:00';
    if (this._opts.timeDurEl) this._opts.timeDurEl.textContent = '0:00';
    this._ws.load(url);
  };

  /**
   * Destroy the player and release all resources.
   */
  AudioPlayer.prototype.destroy = function () {
    if (this._ws) {
      this._ws.destroy();
      this._ws = null;
    }
  };

  global.AudioPlayer = AudioPlayer;
})(typeof window !== 'undefined' ? window : this);
