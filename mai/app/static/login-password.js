(() => {
  const previousFetch = window.fetch.bind(window);

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
    if (response.ok && passwordInput) passwordInput.value = '';
    return response;
  };
})();
