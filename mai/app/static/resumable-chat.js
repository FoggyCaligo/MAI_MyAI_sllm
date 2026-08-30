(() => {
  const nativeFetch = window.fetch.bind(window);
  const storageKey = 'MAI:pending-chat-job';
  const pollDelayMs = 1500;
  let currentSubmissionJobId = null;
  let recovering = false;
  let cancelButton = null;

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

  async function recoverPendingJob() {
    if (recovering || currentSubmissionJobId) return;
    const raw = localStorage.getItem(storageKey);
    if (!raw) return;

    let saved;
    try {
      saved = JSON.parse(raw);
    } catch (_) {
      localStorage.removeItem(storageKey);
      return;
    }
    if (!saved?.job_id) {
      localStorage.removeItem(storageKey);
      return;
    }

    recovering = true;
    let pending = null;
    try {
      if (!state?.token) return;
      const auth = await nativeFetch('/me', {headers: headersWithAuth(), cache: 'no-store'});
      if (auth.status === 401) return;
      if (!auth.ok) return;

      setCurrentJob(saved.job_id);
      if (typeof addThinking === 'function') pending = addThinking();
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
