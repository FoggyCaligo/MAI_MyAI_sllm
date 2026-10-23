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

  function renderLiveThinking(thinking, verificationRejections = []) {
    if (!liveProgressWrap) return;
    let panel = liveProgressWrap.querySelector('[data-live-thinking="true"]');
    const sections = [];
    const text = typeof thinking === 'string' ? thinking.trim() : '';
    if (text) sections.push(text);
    if (Array.isArray(verificationRejections)) {
      verificationRejections.forEach(rejection => {
        const feedback = typeof rejection?.feedback === 'string' ? rejection.feedback.trim() : '';
        if (!feedback) return;
        const round = Number.isInteger(rejection?.model_round) ? ` · round ${rejection.model_round}` : '';
        sections.push(`REJECTED${round}\n${feedback}`);
      });
    }
    if (!sections.length) {
      panel?.remove();
      return;
    }
    if (!panel) {
      panel = document.createElement('details');
      panel.className = 'tool-log';
      panel.dataset.liveThinking = 'true';
      panel.open = true;
      const summary = document.createElement('summary');
      summary.textContent = 'Think · 진행 중';
      const body = document.createElement('div');
      body.className = 'tool-result';
      body.style.whiteSpace = 'pre-wrap';
      body.style.overflowWrap = 'anywhere';
      panel.append(summary, body);
      liveProgressWrap.appendChild(panel);
    }
    panel.querySelector('.tool-result').textContent = sections.join('\n\n');
    if (typeof scrollToBottom === 'function') scrollToBottom();
  }

  function preserveFailedThinking() {
    if (!liveProgressWrap) return;
    const panel = liveProgressWrap.querySelector('[data-live-thinking="true"]');
    if (!panel) return;
    delete panel.dataset.liveThinking;
    panel.querySelector('summary').textContent = 'Think · 오류 전 기록';
    panel.open = false;
  }

  function renderLiveToolProgress(tools) {
    if (!liveProgressWrap || !Array.isArray(tools) || !tools.length) return;

    let panel = liveProgressWrap.querySelector('[data-live-tool-progress="true"]');
    const existingEntries = panel?.querySelector('.tool-log-entries');
    const scrollTop = existingEntries?.scrollTop ?? 0;
    const openEntries = existingEntries
      ? Array.from(existingEntries.querySelectorAll('details.tool-entry'), entry => entry.open)
      : [];
    const wasOpen = panel?.open ?? true;

    if (!panel) {
      panel = document.createElement('details');
      panel.className = 'tool-log';
      panel.dataset.liveToolProgress = 'true';
      liveProgressWrap.appendChild(panel);
    }

    const summary = document.createElement('summary');
    summary.textContent = `tool log · ${tools.length} call${tools.length === 1 ? '' : 's'} · running`;
    const entries = document.createElement('div');
    entries.className = 'tool-log-entries';

    tools.forEach((tool, index) => {
      const entry = document.createElement('details');
      entry.className = 'tool-entry';
      entry.open = openEntries[index] ?? false;
      const head = document.createElement('summary');
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
      entries.appendChild(entry);
    });

    panel.replaceChildren(summary, entries);
    panel.open = wasOpen;
    entries.scrollTop = scrollTop;
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

    const hasMessage = Boolean(input.value.trim()) || Boolean(document.getElementById('clipboard-previews')?.children.length);
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
        if (message?.role === 'assistant') {
          const metadata = message?.metadata;
          const isError = metadata?.kind === 'error';
          const rendered = addMessage('mai', isError ? '' : String(message.content || ''));
          if (isError) {
            rendered.bubble.classList.add('error');
            const prefix = metadata.error_type ? `${metadata.error_type}: ` : '';
            rendered.bubble.textContent = `오류: ${prefix}${String(message.content || '')}`;
          }
          if (metadata && typeof renderToolLog === 'function') {
            renderToolLog(
              rendered.wrap,
              Array.isArray(metadata.tools) ? metadata.tools : [],
              Number(metadata.model_rounds) || 0,
              String(metadata.model || 'unknown'),
            );
          }
        }
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
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        return jsonResponse({
          error_type: error instanceof Error ? error.name : 'NetworkError',
          detail: detail || '대화 작업 상태 조회 중 연결이 끊겼습니다.',
        }, 503);
      }

      if (res.status === 401) return res;
      if (res.status === 404) {
        localStorage.removeItem(storageKey);
        return jsonResponse({detail: '저장된 대화 작업을 찾을 수 없습니다.'}, 410);
      }
      if (!res.ok) return res;

      const data = await res.json();
      renderLiveThinking(data.thinking, data.verification_rejections);
      renderLiveToolProgress(Array.isArray(data.tools) ? data.tools : []);
      if (data.status === 'completed') {
        localStorage.removeItem(storageKey);
        return jsonResponse(data.response || {}, 200);
      }
      if (data.status === 'failed') {
        preserveFailedThinking();
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
        document.dispatchEvent(new CustomEvent('mai:answer-complete', {detail: {text: data.answer || ''}}));
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
