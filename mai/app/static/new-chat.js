(() => {
  const rememberedUserIdKey = 'MAI:last-login-id';
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
    hint.textContent = 'Start a conversation';
    empty.append(big, hint);
    host.appendChild(empty);
  }

  async function startNewChat(button) {
    if (!state?.token) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.title = 'Starting new chat…';
    try {
      if (typeof window.maiResetConversation !== 'function') {
        throw new Error('대화 초기화 기능이 준비되지 않았습니다.');
      }
      await window.maiResetConversation();
      document.getElementById('msg-input')?.focus();
    } catch (error) {
      window.alert(`New chat failed: ${error.message}`);
    } finally {
      button.removeAttribute('aria-busy');
      button.title = 'New chat (keeps long-term memory)';
      button.disabled = false;
    }
  }

  function bindNewChatButton() {
    const button = document.getElementById('new-chat-btn');
    if (!button || button.dataset.bound === 'true') return;
    button.dataset.bound = 'true';
    if (typeof setMaiIconButton === 'function') setMaiIconButton(button, 'message-plus', 'New chat (keeps long-term memory)');
    button.addEventListener('click', () => void startNewChat(button));
  }

  restoreRememberedUserId();
  bindNewChatButton();
  window.addEventListener('pageshow', () => {
    restoreRememberedUserId();
    bindNewChatButton();
  });

})();
