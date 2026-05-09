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
    div.className = 'memory' + (e.id === justAddedId ? ' new' : '') +
                    (e.has_image ? '' : ' no-image');
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

    const xBtn = document.createElement('button');
    xBtn.className = 'memory-x';
    xBtn.textContent = '×';
    xBtn.title = 'Forget this memory';
    xBtn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try {
        await fetch('/api/memories/' + e.id, { method: 'DELETE' });
        // The WebSocket memory_removed event will refresh the list.
      } catch (err) {
        console.error('[rocky] forget failed', err);
      }
    });
    div.appendChild(xBtn);

    if (e.has_image) {
      div.addEventListener('click', () => openMemory(e));
      div.title = 'Click to view full image';
    }
    memoriesEl.appendChild(div);
  }
}

const clearMemBtn = document.getElementById('clear-mem-btn');
if (clearMemBtn) {
  clearMemBtn.addEventListener('click', async () => {
    if (!confirm('Forget all memories? This cannot be undone.')) return;
    try {
      await fetch('/api/memories', { method: 'DELETE' });
    } catch (e) {
      console.error('[rocky] clear failed', e);
    }
  });
}

function appendTurn(youText, rockyText, speakerId) {
  if (isFirstTurn) {
    transcriptEl.innerHTML = '';
    isFirstTurn = false;
  }
  const speakerLabel = (speakerId || 'rocky').toUpperCase();
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
    b.innerHTML = '<span class="label">' + escapeHtml(speakerLabel) + '</span>' + escapeHtml(rockyText);
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

// ---------------- Character picker ----------------

const charPickerEl = document.getElementById('char-picker');
const avatarWrap = document.getElementById('avatar-wrap');
let currentCharacterId = 'rocky';

async function loadCharacters() {
  if (!charPickerEl) return;
  try {
    const r = await fetch('/api/characters');
    const j = await r.json();
    charPickerEl.innerHTML = '';
    currentCharacterId = j.active || 'rocky';
    if (avatarWrap) avatarWrap.dataset.character = currentCharacterId;
    for (const c of (j.characters || [])) {
      const btn = document.createElement('button');
      btn.className = 'char-btn' + (c.id === currentCharacterId ? ' active' : '');
      btn.dataset.char = c.id;
      btn.textContent = c.name.toUpperCase();
      btn.title = c.description;
      btn.addEventListener('click', () => switchCharacter(c.id));
      charPickerEl.appendChild(btn);
    }
  } catch (e) {
    console.error('[chars] failed to load', e);
  }
}

async function switchCharacter(id) {
  if (id === currentCharacterId) return;
  try {
    const r = await fetch(`/api/characters/${id}`, { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      currentCharacterId = id;
      if (avatarWrap) avatarWrap.dataset.character = id;
      document.querySelectorAll('.char-btn').forEach((b) =>
        b.classList.toggle('active', b.dataset.char === id));
      console.log('[chars] switched to', id);
    }
  } catch (e) {
    console.error('[chars] switch failed', e);
  }
}

loadCharacters();

// ---------------- Adaption Labs corpus mini ----------------
// Auto-runs on the server every 5 turns; UI just shows status.

const corpusStatusEl = document.getElementById('corpus-status');
const corpusTurnsEl  = document.getElementById('corpus-turns');

let lastTurnCount = 0;

function setCorpusStatus(status) {
  if (!corpusStatusEl) return;
  corpusStatusEl.textContent = status || 'idle';
  corpusStatusEl.className = 'corpus-status ' + (status || 'idle');
}

function renderCorpus(state, turnCount) {
  if (!state) return;
  setCorpusStatus(state.status);
  if (turnCount !== undefined) {
    lastTurnCount = turnCount;
    corpusTurnsEl.textContent = String(turnCount);
  }
}

// While a run is in flight, poll for completion so the UI flips to "completed".
setInterval(async () => {
  const s = corpusStatusEl?.textContent;
  if (s !== 'running' && s !== 'uploading') return;
  try {
    const r = await fetch('/api/adapt/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.state) renderCorpus(j.state);
  } catch (_) {}
}, 5000);

// Manual ADAPT NOW button in the header.
const corpusBtn = document.getElementById('corpus-btn');
if (corpusBtn) {
  corpusBtn.addEventListener('click', async () => {
    const s = corpusStatusEl?.textContent;
    if (s === 'uploading' || s === 'running') return;  // already in flight
    setCorpusStatus('uploading');
    try {
      const r = await fetch('/api/adapt', { method: 'POST' });
      const j = await r.json();
      if (j.state) renderCorpus(j.state);
      else if (j.error) {
        console.error('[adapt]', j.error);
        setCorpusStatus('failed');
      }
    } catch (e) {
      console.error('[adapt]', e);
      setCorpusStatus('failed');
    }
  });
}

// ---------------- Memory lightbox ----------------

const lightboxEl   = document.getElementById('lightbox');
const lightboxImg  = document.getElementById('lightbox-img');
const lightboxFact = document.getElementById('lightbox-fact');
const lightboxWhen = document.getElementById('lightbox-when');

function openMemory(entry) {
  if (!entry) return;
  lightboxImg.src = entry.has_image ? '/memory/image/' + entry.id : '';
  lightboxImg.style.display = entry.has_image ? '' : 'none';
  lightboxFact.textContent = entry.fact;
  const ts = new Date((entry.saved_at || 0) * 1000);
  lightboxWhen.textContent = isNaN(ts.getTime())
    ? ''
    : 'saved ' + ts.toLocaleString();
  lightboxEl.hidden = false;
}

function closeLightbox() { lightboxEl.hidden = true; }

lightboxEl.querySelectorAll('[data-close]').forEach((el) =>
  el.addEventListener('click', closeLightbox));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !lightboxEl.hidden) closeLightbox();
});

// Expose for recorder.js — supports both the old (you, rocky) and the new
// (you, rocky, speakerId) signatures.
window.rocky = {
  setTranscript: (you, rocky, speakerId) => {
    appendTurn(you, rocky, speakerId);
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
  } else if (data.type === 'memory_removed' || data.type === 'memory_compacted'
             || data.type === 'memory_cleared') {
    fetch('/api/memories').then(r => r.json()).then(j => renderMemories(j.entries));
  } else if (data.type === 'pattern_updated') {
    renderPatterns(data.state, true);
    pulseAdaptCard();
  }
};
