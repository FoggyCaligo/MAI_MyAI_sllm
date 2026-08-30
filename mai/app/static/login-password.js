(() => {
  const previousFetch = window.fetch.bind(window);
  const rememberedUserIdKey = 'MAI:last-login-id';

  function restoreRememberedUserId() {
    const loginIdInput = document.getElementById('login-id');
    if (!loginIdInput || loginIdInput.value) return;
    const remembered = localStorage.getItem(rememberedUserIdKey);
    if (remembered) loginIdInput.value = remembered;
  }

  restoreRememberedUserId();
  window.addEventListener('pageshow', restoreRememberedUserId);

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
