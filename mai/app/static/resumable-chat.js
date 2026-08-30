(() => {
  const nativeFetch = window.fetch.bind(window);
  const storageKey = 'MAI:pending-chat-job';
  const pollDelayMs = 1500;
  let currentSubmissionJobId = null;
  let recovering = false;
  let cancelButton = null;
  let liveProgressWrap = null;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function headersWithAuth(extra = {}) {
    if (typeof authHeaders === 'function') return authHeaders(extra);
    return extra;
  }

  function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload ?? {}), {
      status,
      headers: {'Content-Type': 'application/json'},
    });
  }

  function latestMaiWrap() {
    const wraps = document.querySelectorAll('.msg-row.mai .content-wrap');
    return wraps.length ? wraps[wraps.length - 1] : null;
  }

  function setLiveProgressWrap(wrap) {
    clearLiveProgress();
    liveProgressWrap = wrap || null;
  }

  function clearLiveProgress() {
    if (liveProgressWrap) {
      liveProgressWrap.querySelector('[data-live-tool-progress="true"]')?.remove();
      liveProgressWrap.querySelector('[data-live-thinking="true"]')?.remove();
    }
    liveProgressWrap = null;
  }

  function renderLiveThinking(thinking) {
    if (!liveProgressWrap) return;
    const bubble = liveProgressWrap.querySelector('.bubble');
    if (!bubble) return;

    let placeholder = bubble.querySelector('[data-live-thinking="true"]');
    const text = typeof thinking === 'string' ? thinking.trim() : '';
    if (!text) {
      placeholder?.remove();
      return;
    }

    if (!placeholder) {
      placeholder = document.createElement('div');
      placeholder.dataset.liveThinking = 'true';
      placeholder.style.marginTop = '8px';
      placeholder.style.color = 'var(--text-dim)';
      placeholder.style.opacity = '0.78';
      placeholder.style.fontSize = '13px';
      placeholder.style.lineHeight = '1.55';
      placeholder.style.whiteSpace = 'pre-wrap';
      placeholder.style.overflowWrap = 'anywhere';
      bubble.appendChild(placeholder);
    }
    placeholder.textContent = text;
    if (typeof scrollToBottom === 'function') scrollToBottom();
  }

  function renderLiveToolProgress(tools) {
    if (!liveProgressWrap || !Array.isArray(tools) || !tools.length) return;

    let details = liveProgressWrap.querySelector('[data-live-tool-progress="true"]');
    const wasOpen = details?.open ?? false;
    if (!details) {
      details = document.createElement('details');
      details.className = 'tool-log';
      details.dataset.liveToolProgress = 'true';
      liveProgressWrap.appendChild(details);
    }

    details.replaceChildren();
    details.open = wasOpen;
    const summary = document.createElement('summary');
    summary.textContent = `tool log · ${tools.length} call${tools.length === 1 ? '' : 's'} · running`;
    details.appendChild(summary);

    tools.forEach(tool => {
      const entry = document.createElement('div');
      entry.className = 'tool-entry';
      const head = document.createElement('div');
      head.className = 'tool-head';
      const name = document.createElement('span');
      name.textContent = tool.name;
      const status = document.createElement('span');
      status.textContent = tool.ok ? 'ok' : (tool.error_type || 'failed');
      if (!tool.ok) status.className = 'tool-failed';
      head.append(name, status);

      const args = document.createElement('div');
      args.className = 'tool-args';
      args.textContent = JSON.stringify(tool.arguments, null, 2);
      entry.append(head, args);

      if (tool.result) {
        const result = document.createElement('div');
        result.className = 'tool-result';
        result.textContent = tool.result;
        entry.appendChild(result);
      }
      details.appendChild(entry);
    });
    if (typeof scrollToBottom === 'function') scrollToBottom();
  }

  function ensureCancelButton() {
    if (cancelButton) return cancelButton;
    const sendButton = document.getElementById('send-btn');
    const parent = sendButton?.parentElement;
    if (!parent) return null;

    cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.textContent = '중단';
    cancelButton.hidden = true;
    cancelButton.style.border = '1px solid var(--border)';
    cancelButton.style.borderRadius = '8px';
    cancelButton.style.padding = '7px 12px';
    cancelButton.style.background = 'transparent';
    cancelButton.style.color = 'var(--danger)';
    cancelButton.style.cursor = 'pointer';
    cancelButton.addEventListener('click', cancelCurrentJob);
    parent.insertBefore(cancelButton, sendButton);
    return cancelButton;
  }

  function setCurrentJob(jobId) {
    currentSubmissionJobId = jobId;
    const button = ensureCancelButton();
    if (!button) return;
    button.hidden = !jobId;
    button.disabled = false;
    button.textContent = '중단';
    if (!jobId) clearLiveProgress();
  }

  async function cancelCurrentJob() {
    const jobId = currentSubmissionJobId;
    if (!jobId) return;
    const button = ensureCancelButton();
    if (button) {
      button.disabled = true;
      button.textContent = '중단 중…';
    }
    try {
      const response = await nativeFetch(`/chat/jobs/${encodeURIComponent(jobId)}`, {
        method: 'DELETE',
        headers: headersWithAuth(),
      });
      if (!response.ok && button) {
        button.disabled = false;
        button.textContent = '중단';
      }
    } catch (_) {
      if (button) {
        button.disabled = false;
        button.textContent = '중단';
      }
    }
  }

  async function pollJob(jobId) {
    while (true) {
      let res;
      try {
        res = await nativeFetch(`/chat/jobs/${encodeURIComponent(jobId)}`, {
          headers: headersWithAuth(),
          cache: 'no-store',
        });
      } catch (_) {
        await sleep(pollDelayMs);
        continue;
      }

      if (res.status === 401) return res;
      if (res.status === 404) {
        localStorage.removeItem(storageKey);
        return jsonResponse({detail: '저장된 대화 작업을 찾을 수 없습니다.'}, 410);
      }
      if (!res.ok) return res;

      const data = await res.json();
      renderLiveThinking(data.thinking);
      renderLiveToolProgress(Array.isArray(data.tools) ? data.tools : []);
      if (data.status === 'completed') {
        localStorage.removeItem(storageKey);
        return jsonResponse(data.response || {}, 200);
      }
      if (data.status === 'failed') {
        localStorage.removeItem(storageKey);
        const payload = data.response || {detail: data.error || '대화 작업 실패'};
        return jsonResponse(payload, Number(payload.status_code) || 500);
      }
      if (data.status === 'cancelled') {
        localStorage.removeItem(storageKey);
        return jsonResponse(data.response || {detail: '요청이 취소되었습니다.'}, 499);
      }
      await sleep(pollDelayMs);
    }
  }

  async function submitDetachedChat(init) {
    const submit = await nativeFetch('/chat/jobs', init);
    if (!submit.ok) return submit;
    const data = await submit.json();
    if (!data.job_id) return jsonResponse({detail: 'chat job id가 반환되지 않았습니다.'}, 500);

    setLiveProgressWrap(latestMaiWrap());
    setCurrentJob(data.job_id);
    localStorage.setItem(storageKey, JSON.stringify({job_id: data.job_id, created_at: Date.now()}));
    try {
      return await pollJob(data.job_id);
    } finally {
      setCurrentJob(null);
    }
  }

  window.fetch = function(input, init = {}) {
    const url = typeof input === 'string' ? input : input?.url;
    const method = String(init?.method || 'GET').toUpperCase();
    if (url === '/chat' && method === 'POST') return submitDetachedChat(init);
    return nativeFetch(input, init);
  };

  async function discoverActiveJob() {
    const response = await nativeFetch('/chat/jobs/active', {
      headers: headersWithAuth(),
      cache: 'no-store',
    });
    if (!response.ok) return null;
    const data = await response.json().catch(() => ({}));
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    return jobs.length ? jobs[0] : null;
  }

  async function recoverPendingJob() {
    if (recovering || currentSubmissionJobId) return;

    recovering = true;
    let pending = null;
    try {
      if (!state?.token) return;
      const auth = await nativeFetch('/me', {headers: headersWithAuth(), cache: 'no-store'});
      if (auth.status === 401) return;
      if (!auth.ok) return;

      let saved = null;
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        try {
          saved = JSON.parse(raw);
        } catch (_) {
          localStorage.removeItem(storageKey);
        }
      }

      if (!saved?.job_id) {
        const active = await discoverActiveJob();
        if (!active?.job_id) return;
        saved = {job_id: active.job_id, created_at: Date.now()};
        localStorage.setItem(storageKey, JSON.stringify(saved));
      }

      setCurrentJob(saved.job_id);
      if (typeof addThinking === 'function') pending = addThinking();
      setLiveProgressWrap(pending?.wrap || latestMaiWrap());
      const response = await pollJob(saved.job_id);
      const data = await response.json().catch(() => ({}));

      if (response.status === 401) {
        pending?.row?.remove();
        if (typeof showLogin === 'function') showLogin(data.detail || '세션 만료');
        return;
      }

      if (response.ok) {
        if (pending) {
          pending.bubble.innerHTML = typeof renderMarkdown === 'function' ? renderMarkdown(data.answer) : String(data.answer || '');
          if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
        } else if (typeof addMessage === 'function') {
          addMessage('mai', data.answer || '');
        }
      } else if (pending) {
        pending.bubble.classList.add('error');
        const prefix = data.error_type ? `${data.error_type}: ` : '';
        pending.bubble.textContent = `오류: ${prefix}${data.detail || '대화 작업을 복구하지 못했습니다.'}`;
        if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
      }
    } finally {
      setCurrentJob(null);
      recovering = false;
      if (typeof scrollToBottom === 'function') scrollToBottom();
    }
  }

  ensureCancelButton();
  window.addEventListener('pageshow', recoverPendingJob);
  window.addEventListener('focus', recoverPendingJob);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') recoverPendingJob();
  });
  setInterval(recoverPendingJob, 3000);
  recoverPendingJob();
})();
