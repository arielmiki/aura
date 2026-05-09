// app.js — page state + WebSocket subscription.
// recorder.js owns mic/camera/POST.

const memoriesEl   = document.getElementById('memories');
const memCountEl   = document.getElementById('memory-count');
const transcriptEl = document.getElementById('transcript');

let isFirstTurn = true;

function renderMemories(entries, justAddedId) {
  memCountEl.textContent = entries.length;
  if (entries.length === 0) {
    memoriesEl.innerHTML = '<div class="empty-mem">no memories yet</div>';
    return;
  }
  memoriesEl.innerHTML = '';
  // Most recent first
  for (const e of [...entries].reverse()) {
    const div = document.createElement('div');
    div.className = 'memory' + (e.id === justAddedId ? ' new' : '');
    if (e.has_image) {
      const img = document.createElement('img');
      img.className = 'memory-thumb';
      img.src = '/memory/image/' + e.id;
      img.alt = '';
      img.loading = 'lazy';
      div.appendChild(img);
    }
    const fact = document.createElement('div');
    fact.className = 'memory-fact';
    fact.textContent = e.fact;
    div.appendChild(fact);
    memoriesEl.appendChild(div);
  }
}

function appendTurn(youText, rockyText) {
  if (isFirstTurn) {
    transcriptEl.innerHTML = '';
    isFirstTurn = false;
  }
  const turn = document.createElement('div');
  turn.className = 'turn';
  if (youText) {
    const a = document.createElement('div');
    a.className = 'you';
    a.innerHTML = '<span class="label">YOU</span>' + escapeHtml(youText);
    turn.appendChild(a);
  }
  if (rockyText) {
    const b = document.createElement('div');
    b.className = 'rocky';
    b.innerHTML = '<span class="label">ROCKY</span>' + escapeHtml(rockyText);
    turn.appendChild(b);
  }
  transcriptEl.appendChild(turn);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---------------- Adaptive patterns panel ----------------

const adaptWordsEl   = document.getElementById('adapt-words');
const adaptShorterEl = document.getElementById('adapt-shorter');
const adaptTopicsEl  = document.getElementById('adapt-topics');
const adaptPulseEl   = document.getElementById('adapt-pulse');

function renderPatterns(state, animate) {
  if (!state) return;
  const wordsTxt = (state.max_reply_words ?? 8) + 'w';
  const shorterTxt = (state.shorter_count ?? 0) + '×';
  const topicEntries = Object.entries(state.topic_counts || {})
    .filter(([, c]) => c >= 2)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([w]) => w);
  const topicsTxt = topicEntries.length ? topicEntries.join(', ') : '—';

  flashIfChanged(adaptWordsEl, wordsTxt, animate);
  flashIfChanged(adaptShorterEl, shorterTxt, animate);
  flashIfChanged(adaptTopicsEl, topicsTxt, animate);
}

function flashIfChanged(el, value, animate) {
  if (el.textContent !== value) {
    el.textContent = value;
    if (animate) {
      el.classList.remove('changed');
      void el.offsetWidth;  // restart animation
      el.classList.add('changed');
    }
  }
}

function pulseAdaptCard() {
  if (!adaptPulseEl) return;
  adaptPulseEl.classList.remove('flash');
  void adaptPulseEl.offsetWidth;
  adaptPulseEl.classList.add('flash');
}

// ---------------- Adaption Labs corpus card ----------------

const corpusStatusEl = document.getElementById('corpus-status');
const corpusTurnsEl  = document.getElementById('corpus-turns');
const corpusRowsEl   = document.getElementById('corpus-rows');
const corpusIdEl     = document.getElementById('corpus-id');
const adaptBtn       = document.getElementById('adapt-btn');

let lastTurnCount = 0;

function setCorpusStatus(status) {
  if (!corpusStatusEl) return;
  corpusStatusEl.textContent = status || 'idle';
  corpusStatusEl.className = 'corpus-status ' + (status || 'idle');
  // Disable the button while a run is mid-flight to avoid duplicate uploads.
  if (adaptBtn) {
    const inFlight = status === 'uploading' || status === 'running';
    adaptBtn.disabled = inFlight;
    adaptBtn.textContent = inFlight ? status.toUpperCase() + '…' : 'ADAPT NOW';
  }
}

function renderCorpus(state, turnCount) {
  if (!state) return;
  setCorpusStatus(state.status);
  if (turnCount !== undefined) {
    lastTurnCount = turnCount;
    corpusTurnsEl.textContent = String(turnCount);
  }
  corpusRowsEl.textContent = state.row_count
    ? `${state.row_count} rows`
    : '—';
  corpusIdEl.textContent = state.dataset_id
    ? state.dataset_id.slice(0, 12) + '…'
    : '—';
}

if (adaptBtn) {
  adaptBtn.addEventListener('click', async () => {
    setCorpusStatus('uploading');
    try {
      const r = await fetch('/api/adapt', { method: 'POST' });
      const j = await r.json();
      if (j.error) {
        console.error('[adapt] error:', j.error);
        setCorpusStatus('failed');
      } else if (j.state) {
        renderCorpus(j.state);
      }
    } catch (e) {
      console.error('[adapt] request failed', e);
      setCorpusStatus('failed');
    }
  });
}

// While running, poll status every 4 s so the UI updates without a refresh.
setInterval(async () => {
  if (corpusStatusEl?.textContent !== 'running' &&
      corpusStatusEl?.textContent !== 'uploading') return;
  try {
    const r = await fetch('/api/adapt/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.state) renderCorpus(j.state);
  } catch (_) {}
}, 4000);

// Expose for recorder.js (keeps the existing call signature)
window.rocky = {
  setTranscript: (you, rocky) => {
    appendTurn(you, rocky);
    // Increment the corpus turn count locally so the UI updates immediately.
    if (corpusTurnsEl) {
      lastTurnCount += 1;
      corpusTurnsEl.textContent = String(lastTurnCount);
    }
  },
};

const ws = new WebSocket(
  (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws'
);

ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  if (data.type === 'snapshot') {
    renderMemories(data.entries);
    renderPatterns(data.patterns, false);
    renderCorpus(data.adapt, data.turn_count);
    if (data.adapt_configured === false && adaptBtn) {
      adaptBtn.disabled = true;
      adaptBtn.textContent = 'NOT CONFIGURED';
    }
  } else if (data.type === 'adapt_status') {
    renderCorpus(data.state);
  } else if (data.type === 'memory_added') {
    fetch('/api/memories').then(r => r.json()).then(j => {
      renderMemories(j.entries, data.entry.id);
    });
  } else if (data.type === 'memory_compacted') {
    fetch('/api/memories').then(r => r.json()).then(j => renderMemories(j.entries));
  } else if (data.type === 'pattern_updated') {
    renderPatterns(data.state, true);
    pulseAdaptCard();
  }
};
