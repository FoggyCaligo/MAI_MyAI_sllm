(() => {
  const nativeFetch = window.fetch.bind(window);
  const storageKey = 'MAI:pending-chat-job';
  const pollDelayMs = 1500;
  let currentSubmissionJobId = null;
  let recovering = false;
  // A foreground form submission owns its loading row before the server
  // has returned a job_id. Track every overlapping submission separately.
  let foregroundSubmissions = 0;
  let foregroundSubmissionEpoch = 0;

  function hasForegroundChat() {
    return foregroundSubmissions > 0 ||
      (typeof activeChat !== 'undefined' && Boolean(activeChat));
  }
  let liveProgressWrap = null;
  let historySyncing = false;
  let historyEpoch = 0;
  let resettingConversation = false;
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
    const bubble = liveProgressWrap.querySelector('.bubble');
    if (!bubble) return;

    let placeholder = bubble.querySelector('[data-live-thinking="true"]');
    const text = typeof thinking === 'string' ? thinking.trim() : '';
    const feedback = Array.isArray(verificationRejections)
      ? verificationRejections.map(item => {
          const message = typeof item?.feedback === 'string' ? item.feedback.trim() : '';
          if (!message) return '';
          const round = Number.isInteger(item?.model_round) ? ` · round ${item.model_round}` : '';
          return `REJECTED${round}\n${message}`;
        }).filter(Boolean) : [];
    const thinkingText = [text, ...feedback].filter(Boolean).join('\n\n');
    if (!thinkingText) {
      placeholder?.remove();
      return;
    }
    if (!placeholder) {
      placeholder = document.createElement('details');
      placeholder.dataset.liveThinking = 'true';
      placeholder.open = true;
      placeholder.style.marginTop = '8px';
      placeholder.style.color = 'var(--text-dim)';
      placeholder.style.opacity = '0.78';
      placeholder.style.fontSize = '13px';
      const heading = document.createElement('summary');
      heading.textContent = 'Thinking · generating';
      const body = document.createElement('div');
      body.dataset.thinkingBody = 'true';
      body.style.marginTop = '8px';
      body.style.lineHeight = '1.55';
      body.style.whiteSpace = 'pre-wrap';
      body.style.overflowWrap = 'anywhere';
      placeholder.append(heading, body);
      bubble.appendChild(placeholder);
    }
    // Only the dedicated thinking field is visible before completion.
    // Streamed content and provisional model turns are never answers.
    placeholder.querySelector('[data-thinking-body="true"]').textContent = thinkingText;
    if (typeof scrollToBottom === 'function') scrollToBottom();
  }

  async function settlePreviousPresentation(pending) {
    // A final answer may have reached the server before the UI's next poll.
    // Check the authoritative job status before labelling it interrupted.
    const jobId = currentSubmissionJobId;
    if (!jobId || !pending) return false;
    try {
      const response = await nativeFetch(`/chat/jobs/${encodeURIComponent(jobId)}`, {
        headers: headersWithAuth(), cache: 'no-store',
      });
      if (!response.ok) return false;
      const job = await response.json();
      if (job.status !== 'completed' || typeof job.response?.answer !== 'string') {
        return false;
      }
      const result = job.response;
      clearLiveProgress();
      pending.bubble.innerHTML = typeof renderMarkdown === 'function'
        ? renderMarkdown(result.answer) : result.answer;
      if (typeof setMessageTime === 'function') setMessageTime(pending);
      if (typeof renderToolLog === 'function') {
        renderToolLog(pending.wrap, result.tools || [], result.model_rounds || 0, result.model || 'unknown');
      }
      localStorage.removeItem(storageKey);
      return true;
    } catch (_) {
      // If the authoritative status is unavailable, never fake completion.
      return false;
    }
  }
  window.maiSettlePreviousPresentation = settlePreviousPresentation;

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
      entry.name = 'mai-tool-log-exclusive';
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
    if (typeof setMaiIconButton === 'function') setMaiIconButton(sendButton, hasMessage ? 'send' : 'square', hasMessage ? 'Send' : 'Stop');
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
      showLogin('Session expired or signed in elsewhere. Please sign in again.');
    }
  }

  function clearVisibleConversation() {
    const host = document.getElementById('messages');
    if (!host) return;
    if (typeof releaseImageObjectUrls === 'function') releaseImageObjectUrls();
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
    renderedHistorySignature = null;
  }

  function renderPersistentHistory(messages) {
    const host = document.getElementById('messages');
    if (!host || typeof addMessage !== 'function') return;
    const normalized = Array.isArray(messages) ? messages : [];
    const signature = JSON.stringify([state?.userId || null, normalized]);
    if (signature === renderedHistorySignature) return;

    if (typeof releaseImageObjectUrls === 'function') releaseImageObjectUrls();
    host.replaceChildren();
    if (!normalized.length) {
      const empty = document.createElement('div');
      empty.id = 'empty-state';
      const big = document.createElement('div');
      big.className = 'big';
      big.textContent = 'MAI';
      const hint = document.createElement('div');
      hint.textContent = 'Start a conversation';
      empty.append(big, hint);
      host.appendChild(empty);
    } else {
      normalized.forEach(message => {
        if (message?.role === 'user') addMessage('user', String(message.content || ''), Array.isArray(message?.metadata?.attachments) ? message.metadata.attachments : [],message?.created_at);
        if (message?.role === 'assistant') {
          const metadata = message?.metadata;
          const isError = metadata?.kind === 'error';
          const rendered = addMessage('mai', isError ? '' : String(message.content || ''), [], message?.created_at);
          if (isError) {
            rendered.bubble.classList.add('error');
            const prefix = metadata.error_type ? `${metadata.error_type}: ` : '';
            if (typeof metadata?.unverified_answer === 'string' && typeof renderUnverifiedAnswer === 'function') {
              renderUnverifiedAnswer(rendered.bubble, metadata);
            } else {
              rendered.bubble.textContent = `Error: ${prefix}${String(message.content || '')}`;
            }
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
    if (historySyncing || resettingConversation || currentSubmissionJobId || hasForegroundChat() || !state?.token) return;
    historySyncing = true;
    const epoch = historyEpoch;
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
      // The history request might have started before the user submitted
      // text/voice. Never replace a foreground loading row with stale history.
      if (epoch !== historyEpoch || resettingConversation ||
          currentSubmissionJobId || hasForegroundChat()) return;
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
          detail: detail || 'Connection lost while checking chat status.',
        }, 503);
      }

      if (res.status === 401) return res;
      if (res.status === 404) {
        localStorage.removeItem(storageKey);
        return jsonResponse({detail: 'Chat job not found.'}, 410);
      }
      if (!res.ok) return res;

      const data = await res.json();
      // A cancelled job's slow polling response must not overwrite the newer
      // prompt's thinking/tool log or clear the newer job from localStorage.
      if (currentSubmissionJobId && currentSubmissionJobId !== jobId) {
        return jsonResponse({detail: 'Request replaced by a new message.'}, 499);
      }
      if (data.status === 'pending' || data.status === 'running' || data.status === 'cancelling') {
        renderLiveThinking(data.thinking, data.verification_rejections);
        renderLiveToolProgress(Array.isArray(data.tools) ? data.tools : []);
      }
      if (data.status === 'completed') {
        localStorage.removeItem(storageKey);
        return jsonResponse(data.response || {}, 200);
      }
      if (data.status === 'failed') {
        localStorage.removeItem(storageKey);
        const payload = data.response || {detail: data.error || 'Chat job failed'};
        return jsonResponse(payload, Number(payload.status_code) || 500);
      }
      if (data.status === 'cancelled') {
        localStorage.removeItem(storageKey);
        return jsonResponse(data.response || {detail: 'Request cancelled.'}, 499);
      }
      await sleep(pollDelayMs);
    }
  }

  async function submitDetachedChat(init) {
    // Aborting the original /chat fetch must cancel the detached server job,
    // not just stop browser polling. The job-create POST must stay connected
    // long enough to obtain its id even if the user presses Stop mid-submit.
    const signal = init?.signal;
    if (signal?.aborted) return jsonResponse({detail: 'Request cancelled.'}, 499);
    // A replaced prompt must not race with the cancelled request's model
    // stream and the single-resident llama.cpp gateway. Wait for the old
    // task to release its server-side locks before posting the new one.
    const precedingJobId = currentSubmissionJobId;
    if (precedingJobId) {
      const cancelled = await cancelJob(precedingJobId);
      if (!cancelled) {
        return jsonResponse({detail: 'Could not confirm cancellation. New request not started.'}, 503);
      }
      if (currentSubmissionJobId === precedingJobId) setCurrentJob(null);
    }
    const submit = await nativeFetch('/chat/jobs', {...init, signal: undefined});
    if (!submit.ok) return submit;
    const data = await submit.json();
    if (!data.job_id) return jsonResponse({detail: 'Missing chat job ID.'}, 500);

    const jobId = data.job_id;
    let cancellation = null;
    const cancelOnAbort = () => {
      // Keep a single cancellation request even if Stop races job creation.
      if (!cancellation) cancellation = cancelJob(jobId);
    };
    signal?.addEventListener('abort', cancelOnAbort, {once: true});
    if (signal?.aborted) cancelOnAbort();
    const foregroundWrap = latestMaiWrap();
    const foregroundRow = foregroundWrap?.closest('.msg-row.mai');
    if (foregroundRow) foregroundRow.dataset.chatJobId = jobId;
    setLiveProgressWrap(foregroundWrap);
    setCurrentJob(jobId);
    localStorage.setItem(storageKey, JSON.stringify({job_id: jobId, created_at: Date.now()}));
    try {
      if (cancellation) {
        const cancelled = await cancellation;
        if (cancelled) localStorage.removeItem(storageKey);
        return jsonResponse({detail: cancelled ? 'Request cancelled.' : 'Server cancellation failed.'},
          cancelled ? 499 : 503);
      }
      return await pollJob(jobId);
    } finally {
      signal?.removeEventListener('abort', cancelOnAbort);
      if (currentSubmissionJobId === jobId) setCurrentJob(null);
    }
  }

  window.fetch = function(input, init = {}) {
    const url = typeof input === 'string' ? input : input?.url;
    const method = String(init?.method || 'GET').toUpperCase();
    if (url === '/chat' && method === 'POST') {
      // Voice recognition uses chat-form.requestSubmit(), just like text:
      // both create one foreground loading row in the form handler.
      // Suppress recovery from the instant submission begins, including
      // the period before POST /chat/jobs returns its ID.
      foregroundSubmissionEpoch += 1;
      foregroundSubmissions += 1;
      return submitDetachedChat(init).finally(() => {
        foregroundSubmissions -= 1;
      });
    }
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
    if (recovering || resettingConversation || currentSubmissionJobId || hasForegroundChat()) return;

    recovering = true;
    const recoveryEpoch = historyEpoch;
    const recoverySubmissionEpoch = foregroundSubmissionEpoch;
    const foregroundStarted = () => (
      resettingConversation || recoveryEpoch !== historyEpoch ||
      recoverySubmissionEpoch !== foregroundSubmissionEpoch || hasForegroundChat()
    );
    let pending = null;
    let createdRecoveryPending = false;
    let recoveredJobId = null;
    try {
      if (!state?.token) return;
      const auth = await nativeFetch('/me', {headers: headersWithAuth(), cache: 'no-store'});
      if (auth.status === 401) {
        showExpiredLogin();
        return;
      }
      if (!auth.ok || foregroundStarted()) return;

      await syncPersistentHistory();
      if (foregroundStarted()) return;

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
      if (!recoveredJobId || foregroundStarted() || currentSubmissionJobId) return;

      // Reuse the single visible pending MAI row, if one already exists.
      // This is a presentation guard; the normal foreground submit path
      // always owns its row and never enters recovery.
      const loadingRow = Array.from(document.querySelectorAll('.msg-row.mai'))
        .find(row => row.dataset.chatJobId === recoveredJobId &&
          row.querySelector('.loader') && row.isConnected);
      if (loadingRow) {
        const wrap = loadingRow.querySelector('.content-wrap');
        const bubble = wrap?.querySelector('.bubble');
        if (wrap && bubble) pending = {row: loadingRow, wrap, bubble};
      }
      if (!pending && typeof addThinking === 'function') {
        pending = addThinking();
        createdRecoveryPending = Boolean(pending);
      }
      if (pending?.row) pending.row.dataset.chatJobId = recoveredJobId;
      setCurrentJob(recoveredJobId);
      setLiveProgressWrap(pending?.wrap || latestMaiWrap());
      const response = await pollJob(recoveredJobId);
      const data = await response.json().catch(() => ({}));

      if (response.status === 401) {
        pending?.row?.remove();
        showExpiredLogin();
        return;
      }

      if (foregroundStarted() || currentSubmissionJobId !== recoveredJobId) {
        // The recovery lost ownership to a foreground form submission.
        // Remove only the placeholder that this recovery created.
        if (createdRecoveryPending) pending?.row?.remove();
        return;
      }
      if (response.ok) {
        if (pending) {
          pending.bubble.innerHTML = typeof renderMarkdown === 'function' ? renderMarkdown(data.answer) : String(data.answer || '');
          if (typeof setMessageTime === 'function') setMessageTime(pending);
          if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
        } else if (typeof addMessage === 'function') {
          addMessage('mai', data.answer || '');
        }
        document.dispatchEvent(new CustomEvent('mai:answer-complete', {detail: {text: data.answer || ''}}));
        renderedHistorySignature = null;
      } else if (pending) {
        pending.bubble.classList.add('error');
        const prefix = data.error_type ? `${data.error_type}: ` : '';
        if (typeof data.unverified_answer === 'string' && typeof renderUnverifiedAnswer === 'function') {
          renderUnverifiedAnswer(pending.bubble, data);
        } else {
          pending.bubble.textContent = `Error: ${prefix}${data.detail || 'Could not recover chat job.'}`;
        }
        if (typeof renderToolLog === 'function') renderToolLog(pending.wrap, data.tools || [], data.model_rounds || 0, data.model || 'unknown');
      }
    } finally {
      if (recoveredJobId && currentSubmissionJobId === recoveredJobId) setCurrentJob(null);
      recovering = false;
      if (!foregroundStarted() && !currentSubmissionJobId) void syncPersistentHistory();
      if (typeof scrollToBottom === 'function') scrollToBottom();
    }
  }

  window.maiResetConversation = async function() {
    if (resettingConversation) throw new Error('Chat reset already in progress.');
    resettingConversation = true;
    historyEpoch += 1;
    try {
      const activeResponse = await nativeFetch('/chat/jobs/active', {
        headers: headersWithAuth(), cache: 'no-store',
      });
      if (!activeResponse.ok) throw new Error('Could not check active chat.');
      const active = await activeResponse.json();
      for (const job of active.jobs) {
        const cancelled = await nativeFetch(`/chat/jobs/${encodeURIComponent(job.job_id)}`, {
          method: 'DELETE', headers: headersWithAuth(),
        });
        if (!cancelled.ok) throw new Error('Failed to cancel active job.');
      }
      const result = await nativeFetch(`/session/${encodeURIComponent(state.sessionId || 'default')}`, {
        method: 'DELETE', headers: headersWithAuth(),
      });
      if (!result.ok) throw new Error('Failed to reset chat history.');
      localStorage.removeItem(storageKey);
      clearVisibleConversation();
    } finally {
      resettingConversation = false;
    }
  };

  const chatForm = document.getElementById('chat-form');
  const messageInput = document.getElementById('msg-input');
  // Cancellation is awaited in submitDetachedChat, not fired-and-forgotten
  // from a form event listener. This keeps replacement requests ordered.
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
