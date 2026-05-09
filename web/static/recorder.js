// recorder.js — owns the realtime path: mic, camera, recording, POST, playback.
//
// Primary interaction: the TALK button (click to start, click to submit).
// VAD silence auto-submit is a secondary path (1.2s of quiet while recording).
//
// State machine:
//   idle -> ready -> recording -> submitting -> thinking -> speaking -> ready
//
// The button is ALWAYS enabled. If you click while state==idle, we lazily
// acquire mic+camera. This means even if the auto-init failed (permission
// not yet granted, page just opened), the first click triggers the prompt.

const videoEl = document.getElementById('video');
const audioEl = document.getElementById('audio');
const statusEl = document.getElementById('status');
const talkBtn = document.getElementById('talk-btn');
const meterEl = document.getElementById('meter');
const vizCanvas = document.getElementById('viz');
const vizCtx = vizCanvas ? vizCanvas.getContext('2d') : null;

let state = 'idle';
let mediaStream = null;
let recorder = null;
let recordedChunks = [];
let analyser = null;          // mic analyser (RMS, VAD, viz during recording)
let ttsAnalyser = null;       // TTS audio analyser (viz during speaking)
let dataArray = null;
let belowSince = 0;
let aboveSince = 0;
let recordingStart = 0;

// Mode: 'open' (hands-free auto-start + auto-submit) | 'ptt' (TALK button only)
const MODE_KEY = 'rocky.mode';
let mode = localStorage.getItem(MODE_KEY) || 'open';

const SILENCE_AUTO_SUBMIT_MS = 1000;  // stop after 1s of quiet
const SPEECH_START_MS = 250;          // start after 250ms of sound
const MIN_RECORDING_MS = 500;
const SPEECH_THRESHOLD = 0.02;        // RMS threshold; raised slightly so
                                      // ambient noise doesn't auto-trigger
const METER_INTERVAL_MS = 50;

function setStatus(s) {
  state = s;
  statusEl.textContent = s;
  statusEl.className = 'pill status-' + s;
  if (s === 'recording') {
    talkBtn.classList.add('recording');
    talkBtn.textContent = 'STOP & SEND';
  } else {
    talkBtn.classList.remove('recording');
    if (s === 'ready' || s === 'idle') {
      talkBtn.textContent = 'TALK';
    } else {
      talkBtn.textContent = s.toUpperCase();
    }
  }
  // Button is enabled in idle/ready/recording so the user can always click.
  talkBtn.disabled = !(s === 'idle' || s === 'ready' || s === 'recording');
  console.log('[rocky] state=', s);
}

async function ensureMedia() {
  if (mediaStream) return true;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
    });
  } catch (e) {
    document.getElementById('transcript').innerHTML =
      '<div class="empty" style="color:#f87">Camera/mic permission denied — fix in browser settings and reload.</div>';
    console.error('[rocky] getUserMedia failed', e);
    return false;
  }
  videoEl.srcObject = mediaStream;

  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

  // Mic analyser — drives RMS for VAD and the viz during recording.
  const micSource = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  micSource.connect(analyser);
  dataArray = new Uint8Array(analyser.fftSize);

  // TTS analyser — taps Rocky's audio output so the viz reacts to it.
  // createMediaElementSource removes the element's default routing, so we
  // explicitly reconnect to destination to keep audible playback.
  try {
    const ttsSource = audioCtx.createMediaElementSource(audioEl);
    ttsAnalyser = audioCtx.createAnalyser();
    ttsAnalyser.fftSize = 256;
    ttsSource.connect(ttsAnalyser);
    ttsSource.connect(audioCtx.destination);
  } catch (e) {
    console.warn('[rocky] could not attach TTS analyser', e);
  }

  setInterval(meterTick, METER_INTERVAL_MS);
  startViz();
  console.log('[rocky] media ready');
  return true;
}

// ---------------- visualizer ----------------

const STATE_COLORS = {
  idle:       [108, 122, 150],
  ready:      [90,  216, 255],
  recording:  [255, 193, 90 ],
  submitting: [255, 90,  216],
  thinking:   [255, 90,  216],
  speaking:   [95,  255, 177],
};

function fitCanvas() {
  if (!vizCanvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = vizCanvas.offsetWidth, h = vizCanvas.offsetHeight;
  if (vizCanvas.width !== w * dpr || vizCanvas.height !== h * dpr) {
    vizCanvas.width = w * dpr;
    vizCanvas.height = h * dpr;
    vizCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
}

function startViz() {
  if (!vizCanvas) return;
  window.addEventListener('resize', fitCanvas);
  fitCanvas();
  requestAnimationFrame(drawViz);
}

const _vizFreq = new Uint8Array(128);

function drawViz() {
  if (!vizCanvas || !vizCtx) return;
  fitCanvas();
  const w = vizCanvas.offsetWidth, h = vizCanvas.offsetHeight;
  vizCtx.clearRect(0, 0, w, h);

  const cx = w / 2, cy = h / 2;
  const baseR = Math.min(w, h) * 0.18;
  const t = performance.now() / 1000;

  // Choose data source for this frame.
  let source = null;
  if (state === 'recording' && analyser) source = analyser;
  else if (state === 'speaking' && ttsAnalyser) source = ttsAnalyser;

  const bins = 64;
  const data = new Array(bins);
  if (source) {
    const raw = new Uint8Array(source.frequencyBinCount);
    source.getByteFrequencyData(raw);
    // Take the lower half (where the energy lives for voice).
    for (let i = 0; i < bins; i++) {
      data[i] = raw[Math.floor(i * raw.length * 0.6 / bins)] / 255;
    }
  } else {
    // Synthesized soft wobble — keeps the viz alive when no audio source.
    for (let i = 0; i < bins; i++) {
      const noise = Math.sin(t * 1.4 + i * 0.31) * 0.5
                  + Math.sin(t * 0.7 + i * 0.13) * 0.3 + 0.5;
      data[i] = Math.max(0, Math.min(1, noise * 0.35));
    }
  }

  const [r, g, b] = STATE_COLORS[state] || STATE_COLORS.ready;
  const stroke = `rgba(${r},${g},${b},0.95)`;
  const glow   = `rgba(${r},${g},${b},0.6)`;
  const fade   = `rgba(${r},${g},${b},0)`;

  // Center radial glow (always present, breathes slowly)
  const breath = 1 + 0.04 * Math.sin(t * 1.6);
  const grad = vizCtx.createRadialGradient(cx, cy, 0, cx, cy, baseR * 1.1 * breath);
  grad.addColorStop(0, `rgba(${r},${g},${b},0.55)`);
  grad.addColorStop(0.5, `rgba(${r},${g},${b},0.18)`);
  grad.addColorStop(1, fade);
  vizCtx.fillStyle = grad;
  vizCtx.beginPath();
  vizCtx.arc(cx, cy, baseR * 1.1 * breath, 0, Math.PI * 2);
  vizCtx.fill();

  // Inner solid core
  vizCtx.fillStyle = `rgba(${r},${g},${b},0.28)`;
  vizCtx.beginPath();
  vizCtx.arc(cx, cy, baseR * 0.55, 0, Math.PI * 2);
  vizCtx.fill();

  // Spinning thinking arcs
  if (state === 'thinking' || state === 'submitting') {
    const arcs = 3;
    for (let a = 0; a < arcs; a++) {
      const start = (t * 1.2 + a * (Math.PI * 2 / arcs)) % (Math.PI * 2);
      vizCtx.strokeStyle = stroke;
      vizCtx.lineWidth = 3;
      vizCtx.shadowBlur = 16;
      vizCtx.shadowColor = glow;
      vizCtx.beginPath();
      vizCtx.arc(cx, cy, baseR * 1.4, start, start + 0.6);
      vizCtx.stroke();
    }
    vizCtx.shadowBlur = 0;
  }

  // Circular FFT bars
  const rotate = state === 'thinking' ? t * 0.5 : 0;
  vizCtx.shadowBlur = 10;
  vizCtx.shadowColor = glow;
  vizCtx.lineCap = 'round';
  vizCtx.lineWidth = 2.5;
  vizCtx.strokeStyle = stroke;
  for (let i = 0; i < bins; i++) {
    const angle = (i / bins) * Math.PI * 2 + rotate - Math.PI / 2;
    const v = data[i];
    const inner = baseR * 0.95;
    const outer = inner + Math.max(2, v * baseR * 1.4);
    const x1 = cx + Math.cos(angle) * inner;
    const y1 = cy + Math.sin(angle) * inner;
    const x2 = cx + Math.cos(angle) * outer;
    const y2 = cy + Math.sin(angle) * outer;
    vizCtx.beginPath();
    vizCtx.moveTo(x1, y1);
    vizCtx.lineTo(x2, y2);
    vizCtx.stroke();
  }
  vizCtx.shadowBlur = 0;

  // Outer expanding ring during recording / speaking
  if (state === 'recording' || state === 'speaking') {
    const phase = (t % 1.2) / 1.2;
    const ringR = baseR * (1.4 + phase * 0.9);
    vizCtx.strokeStyle = `rgba(${r},${g},${b},${(1 - phase).toFixed(3)})`;
    vizCtx.lineWidth = 1.5;
    vizCtx.beginPath();
    vizCtx.arc(cx, cy, ringR, 0, Math.PI * 2);
    vizCtx.stroke();
  }

  requestAnimationFrame(drawViz);
}

function setMode(m) {
  mode = m;
  localStorage.setItem(MODE_KEY, m);
  document.querySelectorAll('.mode-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === m);
  });
  // Reset VAD timers so a stale aboveSince doesn't immediately fire when
  // switching back to open-mic.
  aboveSince = 0;
  belowSince = 0;
  console.log('[rocky] mode=', m);
}

async function init() {
  setStatus('idle');
  talkBtn.addEventListener('click', onTalkClick);
  // Wire the mode toggle and apply persisted preference.
  document.querySelectorAll('.mode-btn').forEach((btn) => {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  });
  setMode(mode);
  // Try to acquire media eagerly; if the user hasn't granted yet, this
  // triggers the browser permission prompt. If it succeeds, we go to ready.
  if (await ensureMedia()) {
    setStatus('ready');
  }
}

function rms() {
  analyser.getByteTimeDomainData(dataArray);
  let sum = 0;
  for (let i = 0; i < dataArray.length; i++) {
    const v = (dataArray[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / dataArray.length);
}

function meterTick() {
  if (!analyser) return;
  const level = rms();
  const pct = Math.min(100, Math.round(level * 500));
  meterEl.style.height = pct + '%';

  const now = performance.now();

  // Auto features only apply in open-mic mode. Push-to-talk relies entirely
  // on the TALK button.
  if (mode !== 'open') return;

  if (state === 'ready') {
    // Auto-start recording when the user starts talking.
    if (level > SPEECH_THRESHOLD) {
      aboveSince ||= now;
      if (now - aboveSince > SPEECH_START_MS) {
        startRecording();
      }
    } else {
      aboveSince = 0;
    }
  } else if (state === 'recording') {
    // Auto-submit on sustained silence.
    if (level < SPEECH_THRESHOLD) {
      belowSince ||= now;
      if (now - belowSince > SILENCE_AUTO_SUBMIT_MS &&
          now - recordingStart > MIN_RECORDING_MS) {
        stopRecordingAndSubmit();
      }
    } else {
      belowSince = 0;
    }
  }
  // While speaking/thinking/submitting we deliberately don't VAD —
  // Rocky's own voice through the speaker would self-trigger.
}

async function onTalkClick() {
  console.log('[rocky] TALK clicked, state=', state);
  if (state === 'recording') {
    await stopRecordingAndSubmit();
    return;
  }
  // Anything else: try to start. Lazily acquire media if we don't have it.
  if (!mediaStream) {
    const ok = await ensureMedia();
    if (!ok) return;
  }
  startRecording();
}

function startRecording() {
  if (!mediaStream) {
    console.error('[rocky] no mediaStream');
    return;
  }
  recordedChunks = [];

  // Build an audio-only MediaStream. Some browsers reject start() if the
  // MediaRecorder is given a stream containing video tracks but configured
  // with an audio-only mimeType.
  const audioTracks = mediaStream.getAudioTracks();
  if (audioTracks.length === 0) {
    console.error('[rocky] no audio tracks in mediaStream');
    setStatus('ready');
    return;
  }
  const audioOnly = new MediaStream(audioTracks);

  // Pick a MIME type the browser supports.
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/mp4;codecs=mp4a.40.2',
    'audio/mpeg',
    '',  // browser default
  ];
  let mime = '';
  for (const m of candidates) {
    if (m === '' || MediaRecorder.isTypeSupported(m)) { mime = m; break; }
  }
  console.log('[rocky] starting MediaRecorder with mime=', JSON.stringify(mime));

  try {
    recorder = new MediaRecorder(audioOnly, mime ? { mimeType: mime } : undefined);
  } catch (e) {
    console.error('[rocky] MediaRecorder construct failed', e);
    setStatus('ready');
    return;
  }
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
  };
  recorder.onerror = (e) => {
    console.error('[rocky] MediaRecorder error', e);
    setStatus('ready');
  };
  try {
    recorder.start();
  } catch (e) {
    console.error('[rocky] MediaRecorder.start failed', e);
    setStatus('ready');
    return;
  }
  recordingStart = performance.now();
  belowSince = 0;
  aboveSince = 0;
  setStatus('recording');
}

async function stopRecordingAndSubmit() {
  if (!recorder || recorder.state === 'inactive') {
    setStatus('ready');
    return;
  }
  setStatus('submitting');

  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });

  const audioBlob = new Blob(recordedChunks, { type: recorder.mimeType });
  console.log(`[rocky] recorded: ${audioBlob.size} bytes, type=${recorder.mimeType}`);

  if (audioBlob.size < 1000) {
    console.warn('[rocky] recording too small — ignoring');
    setStatus('ready');
    return;
  }

  const imageBlob = await snapshotFrame();

  setStatus('thinking');

  const form = new FormData();
  form.append('audio', audioBlob, 'audio.webm');
  if (imageBlob) form.append('image', imageBlob, 'frame.jpg');

  let response;
  try {
    response = await fetch('/turn', { method: 'POST', body: form });
  } catch (e) {
    console.error('[rocky] POST /turn failed', e);
    setStatus('ready');
    return;
  }
  if (!response.ok) {
    console.error('[rocky] POST /turn returned', response.status);
    setStatus('ready');
    return;
  }

  const transcript = response.headers.get('X-Transcript') || '';
  const reply = response.headers.get('X-Reply') || '';
  console.log(`[rocky] heard: ${JSON.stringify(transcript)}`);
  console.log(`[rocky] reply: ${JSON.stringify(reply)}`);
  if (window.rocky) window.rocky.setTranscript(transcript, reply);

  const mp3 = await response.blob();
  const url = URL.createObjectURL(mp3);
  audioEl.src = url;
  setStatus('speaking');
  try {
    await audioEl.play();
  } catch (e) {
    console.error('[rocky] audio play failed', e);
    setStatus('ready');
  }
}

async function snapshotFrame() {
  const w = videoEl.videoWidth, h = videoEl.videoHeight;
  if (!w || !h) return null;
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  canvas.getContext('2d').drawImage(videoEl, 0, 0, w, h);
  return await new Promise((resolve) =>
    canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.7)
  );
}

audioEl.addEventListener('ended', () => {
  setStatus('ready');
});

init();
