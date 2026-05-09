// recorder.js — owns the realtime path: mic, camera, recording, POST, playback.
//
// Primary interaction: the TALK button (click to start, click to submit).
// VAD (silence-based auto-submit) is a secondary path — it auto-submits if
// you go quiet for 1.2s while recording.
//
// State machine:
//   idle -> ready -> recording -> submitting -> thinking -> speaking -> ready

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
let recordingStart = 0;

const SILENCE_AUTO_SUBMIT_MS = 1200;  // generous — easy to keep talking
const MIN_RECORDING_MS = 400;         // ignore accidental click-click
const SPEECH_THRESHOLD = 0.015;       // simple fixed threshold
const METER_INTERVAL_MS = 50;

function setStatus(s) {
  state = s;
  statusEl.textContent = s;
  statusEl.className = 'status status-' + s;
  // Talk button mirrors state
  if (s === 'ready') {
    talkBtn.disabled = false;
    talkBtn.classList.remove('recording');
    talkBtn.textContent = 'TALK';
  } else if (s === 'recording') {
    talkBtn.disabled = false;
    talkBtn.classList.add('recording');
    talkBtn.textContent = 'STOP & SEND';
  } else {
    talkBtn.disabled = true;
    talkBtn.classList.remove('recording');
    talkBtn.textContent = s.toUpperCase();
  }
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
    console.error('getUserMedia failed', e);
    return;
  }

  videoEl.srcObject = mediaStream;

  // Web Audio analyser for the live mic-level meter + silence detection
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  dataArray = new Uint8Array(analyser.fftSize);

  setStatus('ready');
  setInterval(meterTick, METER_INTERVAL_MS);

  talkBtn.addEventListener('click', onTalkClick);

  console.log('recorder.js ready — click TALK to begin.');
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
  // Map ~0..0.2 to 0..100% width
  const pct = Math.min(100, Math.round(level * 500));
  meterEl.style.width = pct + '%';
  meterEl.style.background = level > SPEECH_THRESHOLD ? '#f93' : '#5fd';

  // Auto-submit on silence while recording
  if (state === 'recording') {
    const now = performance.now();
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
}

function onTalkClick() {
  if (state === 'ready') {
    startRecording();
  } else if (state === 'recording') {
    stopRecordingAndSubmit();
  }
}

function startRecording() {
  recordedChunks = [];
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
    .find((m) => MediaRecorder.isTypeSupported(m)) || '';
  recorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined);
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
  };
  recorder.start();
  recordingStart = performance.now();
  belowSince = 0;
  setStatus('recording');
}

async function stopRecordingAndSubmit() {
  if (!recorder || recorder.state === 'inactive') return;
  setStatus('submitting');

  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });

  const audioBlob = new Blob(recordedChunks, { type: recorder.mimeType });
  console.log(`recording: ${audioBlob.size} bytes, type=${recorder.mimeType}`);

  if (audioBlob.size < 1000) {
    console.warn('recording too small — ignoring');
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
    console.error('POST /turn failed', e);
    setStatus('ready');
    return;
  }
  if (!response.ok) {
    console.error('POST /turn returned', response.status);
    setStatus('ready');
    return;
  }

  const transcript = response.headers.get('X-Transcript') || '';
  const reply = response.headers.get('X-Reply') || '';
  console.log(`heard: ${JSON.stringify(transcript)}`);
  if (window.rocky) window.rocky.setTranscript(transcript, reply);

  const mp3 = await response.blob();
  const url = URL.createObjectURL(mp3);
  audioEl.src = url;
  setStatus('speaking');
  try {
    await audioEl.play();
  } catch (e) {
    console.error('audio play failed', e);
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
