// recorder.js — owns the realtime path: mic, camera, VAD, recording, POST, playback.
//
// State machine:
//   idle -> listening -> recording -> submitting -> thinking -> speaking -> listening
//
// VAD: simple RMS threshold on the mic stream. Threshold is calibrated as
// (noise floor over first 2s) * 3. Below threshold for 800ms continuously =
// end of turn. Above threshold for 200ms = start of turn.

const videoEl = document.getElementById('video');
const audioEl = document.getElementById('audio');
const statusEl = document.getElementById('status');

let state = 'idle';
let mediaStream = null;
let recorder = null;
let recordedChunks = [];
let analyser = null;
let dataArray = null;
let noiseFloor = 0.01;        // updated during calibration
let speechThreshold = 0.05;   // = noiseFloor * 3 (post calibration)
let belowSince = 0;
let aboveSince = 0;
let calibrationDone = false;
let calibrationSamples = [];
let calibrationStart = 0;

const SILENCE_MS = 800;
const SPEECH_START_MS = 200;
const FRAME_INTERVAL_MS = 50;  // VAD poll cadence

function setStatus(s) {
  state = s;
  statusEl.textContent = s;
  statusEl.className = 'status status-' + s;
}

async function init() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
    });
  } catch (e) {
    setStatus('idle');
    document.getElementById('transcript').innerHTML =
      '<div class="you" style="color:#f55">Camera/mic permission denied — reload and allow.</div>';
    return;
  }

  videoEl.srcObject = mediaStream;

  // Web Audio analyser for VAD
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  dataArray = new Uint8Array(analyser.fftSize);

  setStatus('listening');
  calibrationStart = performance.now();
  setInterval(vadTick, FRAME_INTERVAL_MS);
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

function vadTick() {
  // Skip VAD while not in a state where we care about audio level
  if (state === 'submitting' || state === 'speaking' ||
      state === 'thinking') return;

  const level = rms();
  const now = performance.now();

  // Calibration phase: collect samples for the first 2 seconds.
  if (!calibrationDone) {
    calibrationSamples.push(level);
    if (now - calibrationStart > 2000) {
      const sorted = [...calibrationSamples].sort((a, b) => a - b);
      // 80th percentile of "quiet" ~= noise floor
      noiseFloor = sorted[Math.floor(sorted.length * 0.8)] || 0.01;
      speechThreshold = Math.max(noiseFloor * 3, 0.02);
      calibrationDone = true;
      console.log(`VAD calibrated: noiseFloor=${noiseFloor.toFixed(4)} threshold=${speechThreshold.toFixed(4)}`);
    }
    return;
  }

  if (state === 'listening') {
    if (level >= speechThreshold) {
      aboveSince ||= now;
      if (now - aboveSince > SPEECH_START_MS) {
        startRecording();
      }
    } else {
      aboveSince = 0;
    }
  } else if (state === 'recording') {
    if (level < speechThreshold) {
      belowSince ||= now;
      if (now - belowSince > SILENCE_MS) {
        stopRecordingAndSubmit();
      }
    } else {
      belowSince = 0;
    }
  }
}

function startRecording() {
  recordedChunks = [];
  // Pick the first MIME type the browser supports
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
    .find((m) => MediaRecorder.isTypeSupported(m)) || '';
  recorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined);
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
  };
  recorder.start();
  setStatus('recording');
  belowSince = 0;
}

async function stopRecordingAndSubmit() {
  if (!recorder || recorder.state === 'inactive') return;
  setStatus('submitting');

  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });

  const audioBlob = new Blob(recordedChunks, { type: recorder.mimeType });
  const imageBlob = await snapshotFrame();

  setStatus('thinking');

  const form = new FormData();
  form.append('audio', audioBlob, 'audio.webm');
  if (imageBlob) form.append('image', imageBlob, 'frame.jpg');

  let response;
  try {
    response = await fetch('/turn', { method: 'POST', body: form });
  } catch (e) {
    console.error('POST /turn failed', e);
    setStatus('listening');
    return;
  }
  if (!response.ok) {
    console.error('POST /turn returned', response.status);
    setStatus('listening');
    return;
  }

  // Show transcript + reply (sent as headers; ascii-only)
  const transcript = response.headers.get('X-Transcript') || '';
  const reply = response.headers.get('X-Reply') || '';
  if (window.rocky) window.rocky.setTranscript(transcript, reply);

  const mp3 = await response.blob();
  const url = URL.createObjectURL(mp3);
  audioEl.src = url;
  setStatus('speaking');
  await audioEl.play();
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
  setStatus('listening');
  belowSince = 0;
  aboveSince = 0;
});

init();
