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
    hint.textContent = '메시지를 입력하세요.';
    empty.append(big, hint);
    host.appendChild(empty);
  }

  async function startNewChat(button) {
    if (!state?.token) return;
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = '초기화 중…';
    try {
      if (typeof window.maiResetConversation !== 'function') {
        throw new Error('대화 초기화 기능이 준비되지 않았습니다.');
      }
      await window.maiResetConversation();
      document.getElementById('msg-input')?.focus();
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

})();
