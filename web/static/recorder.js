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

let state = 'idle';
let mediaStream = null;
let recorder = null;
let recordedChunks = [];
let analyser = null;
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
  const source = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  dataArray = new Uint8Array(analyser.fftSize);

  setInterval(meterTick, METER_INTERVAL_MS);
  console.log('[rocky] media ready');
  return true;
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
