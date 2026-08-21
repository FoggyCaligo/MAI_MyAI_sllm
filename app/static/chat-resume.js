(() => {
  const nativeFetch = window.fetch.bind(window);
  const storageKey = "mk4.pendingChatJob";
  const pollDelayMs = 1500;
  let currentSubmissionJobId = null;
  let recovering = false;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function pollJob(jobId) {
    while (true) {
      let res;
      try {
        res = await nativeFetch(`/chat/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      } catch (_) {
        await sleep(pollDelayMs);
        continue;
      }
      if (res.status === 401) return res;
      if (res.status === 404) {
        localStorage.removeItem(storageKey);
        return new Response(JSON.stringify({ detail: "저장된 대화 작업을 찾을 수 없습니다." }), {
          status: 410,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (!res.ok) return res;
      const data = await res.json();
      if (data.status === "completed") {
        localStorage.removeItem(storageKey);
        return new Response(JSON.stringify(data.response || {}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (data.status === "failed") {
        localStorage.removeItem(storageKey);
        return new Response(JSON.stringify(data.response || { detail: data.error || "대화 작업 실패" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      }
      await sleep(pollDelayMs);
    }
  }

  async function submitDetachedChat(init) {
    const submit = await nativeFetch("/chat/jobs", init);
    if (!submit.ok) return submit;
    const data = await submit.json();
    if (!data.job_id) {
      return new Response(JSON.stringify({ detail: "chat job id가 반환되지 않았습니다." }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
    currentSubmissionJobId = data.job_id;
    localStorage.setItem(storageKey, JSON.stringify({ job_id: data.job_id, created_at: Date.now() }));
    try {
      return await pollJob(data.job_id);
    } finally {
      currentSubmissionJobId = null;
    }
  }

  window.fetch = function(input, init = {}) {
    const url = typeof input === "string" ? input : input?.url;
    const method = String(init?.method || "GET").toUpperCase();
    if (url === "/chat" && method === "POST") {
      return submitDetachedChat(init);
    }
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
    try {
      const auth = await nativeFetch("/auth/status", { cache: "no-store" });
      if (!auth.ok) return;
      const loader = typeof window.appendLoader === "function" ? window.appendLoader() : null;
      const response = await pollJob(saved.job_id);
      if (loader?.remove) loader.remove();
      if (response.status === 401) return;
      const rawResponse = await response.text();
      let data = {};
      try { data = rawResponse ? JSON.parse(rawResponse) : {}; }
      catch (_) { data = { detail: rawResponse || response.statusText }; }
      if (response.ok && typeof window.appendAssistantBubble === "function") {
        window.appendAssistantBubble(data);
      } else if (typeof window.appendErrorBubble === "function") {
        window.appendErrorBubble(data.detail || "대화 작업을 복구하지 못했습니다.");
      }
    } finally {
      recovering = false;
    }
  }

  window.addEventListener("pageshow", recoverPendingJob);
  window.addEventListener("focus", recoverPendingJob);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") recoverPendingJob();
  });
  setInterval(recoverPendingJob, 3000);
  recoverPendingJob();
})();
