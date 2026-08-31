(() => {
  const previousFetch = window.fetch.bind(window);
  const rememberedUserIdKey = 'MAI:last-login-id';
  const pendingChatJobKey = 'MAI:pending-chat-job';

  function restoreRememberedUserId() {
    const loginIdInput = document.getElementById('login-id');
    if (!loginIdInput || loginIdInput.value) return;
    const remembered = localStorage.getItem(rememberedUserIdKey);
    if (remembered) loginIdInput.value = remembered;
  }

  function authHeadersForUi(extra = {}) {
    if (typeof authHeaders === 'function') return authHeaders(extra);
    return extra;
  }

  function resetVisibleConversation() {
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
  }

  async function cancelActiveChatJobs() {
    const response = await previousFetch('/chat/jobs/active', {
      headers: authHeadersForUi(),
      cache: 'no-store',
    });
    if (response.status === 401) throw new Error('로그인 세션이 만료되었습니다.');
    if (!response.ok) throw new Error('진행 중인 대화 확인에 실패했습니다.');

    const data = await response.json().catch(() => ({}));
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    for (const job of jobs) {
      if (!job?.job_id) continue;
      const cancelled = await previousFetch(`/chat/jobs/${encodeURIComponent(job.job_id)}`, {
        method: 'DELETE',
        headers: authHeadersForUi(),
      });
      if (!cancelled.ok && cancelled.status !== 404) {
        throw new Error('진행 중인 대화를 중단하지 못했습니다.');
      }
    }
    localStorage.removeItem(pendingChatJobKey);
  }

  async function startNewChat(button) {
    if (!state?.token) return;
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = '초기화 중…';
    try {
      await cancelActiveChatJobs();
      const sessionId = state.sessionId || 'default';
      const response = await previousFetch(`/session/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
        headers: authHeadersForUi(),
      });
      if (response.status === 401) throw new Error('로그인 세션이 만료되었습니다.');
      if (!response.ok) throw new Error('최근 대화 문맥을 지우지 못했습니다.');
      resetVisibleConversation();
      const input = document.getElementById('msg-input');
      if (input) input.focus();
    } catch (error) {
      window.alert(`새 채팅 시작 실패: ${error.message}`);
    } finally {
      button.textContent = originalText;
      button.disabled = false;
    }
  }

  function bindNewChatButton() {
    const button = document.getElementById('new-chat-btn');
    if (!button || button.dataset.bound === 'true') return;
    button.dataset.bound = 'true';
    button.addEventListener('click', () => void startNewChat(button));
  }

  restoreRememberedUserId();
  bindNewChatButton();
  window.addEventListener('pageshow', () => {
    restoreRememberedUserId();
    bindNewChatButton();
  });

  window.fetch = async function(input, init = {}) {
    const url = typeof input === 'string' ? input : input?.url;
    const method = String(init?.method || 'GET').toUpperCase();
    if (url !== '/login' || method !== 'POST') {
      return previousFetch(input, init);
    }

    const passwordInput = document.getElementById('login-pw');
    const password = passwordInput?.value ?? '';
    let payload;
    try {
      payload = JSON.parse(String(init.body || '{}'));
    } catch (_) {
      return previousFetch(input, init);
    }
    const response = await previousFetch(input, {
      ...init,
      body: JSON.stringify({...payload, user_pw: password}),
    });
    if (response.ok) {
      const userId = typeof payload.user_id === 'string' ? payload.user_id.trim() : '';
      if (userId) localStorage.setItem(rememberedUserIdKey, userId);
      if (passwordInput) passwordInput.value = '';
    }
    return response;
  };
})();
