(() => {
  const nativeFetch = window.fetch.bind(window);
  const storageKey = 'MAI:pending-chat-job';
  const pollDelayMs = 1500;
  let currentSubmissionJobId = null;
  let recovering = false;
  let liveProgressWrap = null;
  let historySyncing = false;
  let renderedHistorySignature = null;

  // MAI currently exposes one continuing conversation per account. Keeping the
  // session id stable lets PC and phone restore the same server-side history.
  if (typeof state === 'object' && state) state.sessionId = 'default';

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

  function captureToolResultScroll(details) {
    if (!details) return [];
    return Array.from(details.querySelectorAll('.tool-result'), result => ({
      top: result.scrollTop,
      left: result.scrollLeft,
    }));
  }

  function restoreToolResultScroll(details, positions) {
    if (!details || !positions.length) return;
    const results = details.querySelectorAll('.tool-result');
    results.forEach((result, index) => {
      const position = positions[index];
      if (!position) return;
      result.scrollTop = position.top;
      result.scrollLeft = position.left;
    });
  }

  function renderLiveToolProgress(tools) {
    if (!liveProgressWrap || !Array.isArray(tools) || !tools.length) return;

    let details = liveProgressWrap.querySelector('[data-live-tool-progress="true"]');
    const wasOpen = details?.open ?? false;
    const resultScrollPositions = captureToolResultScroll(details);
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
        if (typeof containToolResultScroll === 'function') containToolResultScroll(result);
        entry.appendChild(result);
      }
      details.appendChild(entry);
    });
    restoreToolResultScroll(details, resultScrollPositions);
    if (typeof scrollToBottom === 'function') scrollToBottom();
  }

  function syncSingleSendControl() {
    const sendButton = document.getElementById('send-btn');
    const input = document.getElementById('msg-input');
    if (!sendButton || !input) return;

    sendButton.style.color = '';
    if (!currentSubmissionJobId) {
      if (typeof syncSendButton === 'function') syncSendButton();
      return;
    }

    const hasMessage = Boolean(input.value.trim());
    sendButton.textContent = hasMessage ? '전송' : '중단';
    sendButton.classList.toggle('stop-mode', !hasMessage);
    sendButton.disabled = false;
  }

  function setCurrentJob(jobId) {
    currentSubmissionJobId = jobId;
    if (!jobId) clearLiveProgress();
    syncSingleSendControl();
  }

  function showExpiredLogin() {
    sessionStorage.removeItem('MAI:auth-token');
    if (typeof state === 'object' && state) state.token = null;
    renderedHistorySignature = null;
    if (typeof showLogin === 'function') {
      showLogin('다른 기기에서 새로 로그인했거나 세션이 만료되었습니다. 다시 로그인해주세요.');
    }
  }

  function clearVisibleConversation() {
    const host = document.getElementById('messages');
    if (!host) return;
    host.replaceChildren();
    const empty = document.createElement('div');
    empty.id = 'empty-state';
    const big = document.createElement('div');
    big.className = 'big';
    big.textContent = 'MAI';
    const hint = document.createElement('div');
    hint.textContent = '메시지를 입력하세요.';
    empty.append(big, hint);
    host.appendChild(empty);
    renderedHistorySignature = null;
  }

  function renderPersistentHistory(messages) {
    const host = document.getElementById('messages');
    if (!host || typeof addMessage !== 'function') return;
    const normalized = Array.isArray(messages) ? messages : [];
    const signature = JSON.stringify([state?.userId || null, normalized]);
    if (signature === renderedHistorySignature) return;

    host.replaceChildren();
    if (!normalized.length) {
      const empty = document.createElement('div');
      empty.id = 'empty-state';
      const big = document.createElement('div');
      big.className = 'big';
      big.textContent = 'MAI';
      const hint = document.createElement('div');
      hint.textContent = '메시지를 입력하세요.';
      empty.append(big, hint);
      host.appendChild(empty);
    } else {
      normalized.forEach(message => {
        if (message?.role === 'user') addMessage('user', String(message.content || ''));
        if (message?.role === 'assistant') addMessage('mai', String(message.content || ''));
      });
    }
    renderedHistorySignature = signature;
    if (typeof scrollToBottom === 'function') scrollToBottom('auto', true);
  }

  async function syncPersistentHistory() {
    if (historySyncing || currentSubmissionJobId || !state?.token) return;
    historySyncing = true;
    try {
      const response = await nativeFetch(`/session/${encodeURIComponent(state.sessionId || 'default')}`, {
        headers: headersWithAuth(),
        cache: 'no-store',
      });
      if (response.status === 401) {
        showExpiredLogin();
        return;
      }
      if (!response.ok) return;
      const data = await response.json().catch(() => ({}));
      renderPersistentHistory(Array.isArray(data.messages) ? data.messages : []);
    } finally {
      historySyncing = false;
    }
  }

  async function cancelJob(jobId) {
    if (!jobId) return false;
    try {
      const response = await nativeFetch(`/chat/jobs/${encodeURIComponent(jobId)}`, {
        method: 'DELETE',
        headers: headersWithAuth(),
      });
      return response.ok;
    } catch (_) {
      return false;
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

    const jobId = data.job_id;
    setLiveProgressWrap(latestMaiWrap());
    setCurrentJob(jobId);
    localStorage.setItem(storageKey, JSON.stringify({job_id: jobId, created_at: Date.now()}));
    try {
      return await pollJob(jobId);
    } finally {
      if (currentSubmissionJobId === jobId) setCurrentJob(null);
    }
  }

  window.fetch = function(input, init = {}) {
    const url = typeof input === 'string' ? input : input?.url;
    const method = String(init?.method || 'GET').toUpperCase();
    if (url === '/chat' && method === 'POST') return submitDetachedChat(init);
    if (url === '/login' && method === 'POST') {
      return nativeFetch(input, init).then(response => {
        if (response.ok) {
          clearVisibleConversation();
          setTimeout(recoverPendingJob, 0);
        }
        return response;
      });
    }
    return nativeFetch(input, init);
  };

  async function discoverActiveJob() {
    const response = await nativeFetch('/chat/jobs/active', {
      headers: headersWithAuth(),
      cache: 'no-store',
    });
    if (response.status === 401) {
      showExpiredLogin();
      return null;
    }
    if (!response.ok) return null;
    const data = await response.json().catch(() => ({}));
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    return jobs.length ? jobs[0] : null;
  }

  async function resolveRecoveryJobId(saved) {
    if (saved?.job_id) {
      try {
        const response = await nativeFetch(`/chat/jobs/${encodeURIComponent(saved.job_id)}`, {
          headers: headersWithAuth(),
          cache: 'no-store',
        });
        if (response.status === 401) {
          showExpiredLogin();
          return null;
        }
        if (response.status !== 404) return saved.job_id;
        localStorage.removeItem(storageKey);
      } catch (_) {
        return saved.job_id;
      }
    }

    const active = await discoverActiveJob();
    if (!active?.job_id) return null;
    localStorage.setItem(storageKey, JSON.stringify({job_id: active.job_id, created_at: Date.now()}));
    return active.job_id;
  }

  async function recoverPendingJob() {
    if (recovering || currentSubmissionJobId) return;

    recovering = true;
    let pending = null;
    let recoveredJobId = null;
    try {
      if (!state?.token) return;
      const auth = await nativeFetch('/me', {headers: headersWithAuth(), cache: 'no-store'});
      if (auth.status === 401) {
        showExpiredLogin();
        return;
      }
      if (!auth.ok) return;

      await syncPersistentHistory();

      let saved = null;
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        try {
          saved = JSON.parse(raw);
        } catch (_) {
          localStorage.removeItem(storageKey);
        }
      }

      recoveredJobId = await resolveRecoveryJobId(saved);
      if (!recoveredJobId) return;

      setCurrentJob(recoveredJobId);
      if (typeof addThinking === 'function') pending = addThinking();
      setLiveProgressWrap(pending?.wrap || latestMaiWrap());
      const response = await pollJob(recoveredJobId);
      const data = await response.json().catch(() => ({}));

      if (response.status === 401) {
        pending?.row?.remove();
        showExpiredLogin();
        return;
      }

      if (response.ok) {
        if (pending) {
          pending.bubble.innerHTML = typeof renderMarkdown === 'function' ? renderMarkdown(data.answer) : String(data.answer || '');
          if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
        } else if (typeof addMessage === 'function') {
          addMessage('mai', data.answer || '');
        }
        renderedHistorySignature = null;
      } else if (pending) {
        pending.bubble.classList.add('error');
        const prefix = data.error_type ? `${data.error_type}: ` : '';
        pending.bubble.textContent = `오류: ${prefix}${data.detail || '대화 작업을 복구하지 못했습니다.'}`;
        if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
      }
    } finally {
      if (recoveredJobId && currentSubmissionJobId === recoveredJobId) setCurrentJob(null);
      recovering = false;
      if (!currentSubmissionJobId) void syncPersistentHistory();
      if (typeof scrollToBottom === 'function') scrollToBottom();
    }
  }

  const chatForm = document.getElementById('chat-form');
  const messageInput = document.getElementById('msg-input');
  if (chatForm) {
    chatForm.addEventListener('submit', () => {
      const jobId = currentSubmissionJobId;
      if (jobId) void cancelJob(jobId);
    });
  }
  if (messageInput) {
    messageInput.addEventListener('input', syncSingleSendControl);
  }

  window.addEventListener('pageshow', recoverPendingJob);
  window.addEventListener('focus', recoverPendingJob);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') recoverPendingJob();
  });
  setInterval(recoverPendingJob, 3000);
  recoverPendingJob();
})();
