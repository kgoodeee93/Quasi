const kpiGrid = document.getElementById('kpi-grid');
const summaryText = document.getElementById('summary-text');
const eventList = document.getElementById('event-list');
const focusList = document.getElementById('focus-list');
const approvalList = document.getElementById('approval-list');
const connectionPill = document.getElementById('connection-pill');
const connectionNote = document.getElementById('connection-note');
const answerBox = document.getElementById('answer-box');

// Event titles and locations are written by whoever sends you an invite, so never insert them as HTML.
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function toneClass(tone) {
  return ['blue', 'green', 'amber'].includes(tone) ? tone : 'blue';
}

function renderKpis(items) {
  kpiGrid.innerHTML = items.map((item) => `
    <article class="kpi-card ${toneClass(item.tone)}">
      <div class="label">${esc(item.label)}</div>
      <div class="value">${esc(item.value)}</div>
    </article>
  `).join('');
}

function renderEvents(events) {
  eventList.innerHTML = events.map((event) => `
    <div class="event-row">
      <div>
        <strong>${esc(event.title)}</strong>
        <small>${esc(event.location)}</small>
      </div>
      <div style="text-align: right;">
        <span class="badge">${esc(event.type)}</span><br />
        <small>${esc(event.time)}</small>
      </div>
    </div>
  `).join('');
}

function renderFocus(items) {
  focusList.innerHTML = items.map((item) => `<li>${esc(item)}</li>`).join('');
}

function renderApprovals(items) {
  approvalList.innerHTML = items.map((item) => `
    <div class="approval-item">
      <div>
        <strong>${esc(item.title)}</strong>
        <small>${esc(item.context)}</small>
      </div>
      <span class="badge">${esc(item.status)}</span>
    </div>
  `).join('');
}

async function loadOverview() {
  const res = await fetch('/api/overview');
  if (!res.ok) {
    summaryText.textContent = `Couldn't load the overview (${res.status}).`;
    return;
  }
  const data = await res.json();

  renderKpis(data.kpis || []);
  summaryText.textContent = data.summary || '';
  renderEvents(data.events || []);
  renderFocus(data.focus || []);
  renderApprovals(data.approvals || []);

  if (data.connected) {
    connectionPill.textContent = 'Connected';
    connectionPill.className = 'connection-pill connected';
    connectionNote.textContent = 'Live Google Calendar data is active.';
  } else {
    connectionPill.textContent = 'Demo mode';
    connectionPill.className = 'connection-pill disconnected';
    connectionNote.textContent = 'Connect your Google account to load live data.';
  }
}

async function submitQuestion(question) {
  answerBox.textContent = 'Thinking...';

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      answerBox.textContent = data.detail
        || `Quasi hit an error (${res.status}). Check the server log; is ANTHROPIC_API_KEY set in .env?`;
      return;
    }
    answerBox.textContent = data.answer || 'No answer returned.';
  } catch (err) {
    answerBox.textContent = `Couldn't reach Quasi: ${err.message}`;
  }
}

document.getElementById('ask-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = document.getElementById('question-input');
  const q = input.value.trim();
  if (!q) return;
  await submitQuestion(q);
  input.value = '';
});

document.querySelectorAll('[data-question]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = document.getElementById('question-input');
    input.value = button.dataset.question;
    submitQuestion(button.dataset.question);
  });
});

document.getElementById('connect-btn').addEventListener('click', () => {
  answerBox.textContent = 'Google Calendar connection is handled from the backend setup flow. Use the CLI once: python -m quasi connect google';
});

loadOverview();

const now = new Date();
document.getElementById('today-title').textContent = `Operations dashboard • ${now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
