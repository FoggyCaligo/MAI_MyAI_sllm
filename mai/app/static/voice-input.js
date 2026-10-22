(() => {
  const mic = document.getElementById('mic-btn');
  const enroll = document.getElementById('voice-enroll-btn');
  const status = document.getElementById('upload-status');
  const input = document.getElementById('msg-input');
  let active = null;
  let voiceMode = false;

  function setVoiceMode(enabled) {
    voiceMode = enabled;
    mic.classList.toggle('active', enabled);
    mic.setAttribute('aria-pressed', String(enabled));
    mic.title = enabled ? '음성 모드 끄기' : '음성 입력 시작';
    mic.setAttribute('aria-label', mic.title);
    if (!enabled && 'speechSynthesis' in window) window.speechSynthesis.cancel();
  }

  function spokenText(markdown) {
    if (typeof renderMarkdown !== 'function') {
      throw new Error('TTS requires the chat Markdown renderer');
    }
    const container = document.createElement('div');
    container.innerHTML = renderMarkdown(markdown);
    // Read the displayed prose, not Markdown markup or blocks of source code.
    container.querySelectorAll('pre, code, script, style').forEach(node => node.remove());
    container.querySelectorAll('p, h1, h2, h3, h4, li, tr').forEach(node => node.appendChild(document.createTextNode('\n')));
    return container.textContent
      .replace(/[\p{S}]/gu, ' ')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n[\s\n]*/g, '. ')
      .trim();
  }

  function resumeAfterAnswer() {
    if (!voiceMode || active || mic.disabled) return;
    void start('transcribe');
  }

  document.addEventListener('mai:answer-complete', event => {
    if (!voiceMode || !event.detail?.text) return;
    if (!('speechSynthesis' in window)) {
      setStatus('이 브라우저는 음성 합성(TTS)을 지원하지 않아.', true);
      return;
    }
    const synthesizer = window.speechSynthesis;
    synthesizer.cancel();
    const message = spokenText(event.detail.text);
    if (!message) { resumeAfterAnswer(); return; }
    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = 'ko-KR';
    utterance.rate = 1;
    utterance.onend = () => resumeAfterAnswer();
    utterance.onerror = eventError => {
      if (eventError.error !== 'canceled') setStatus('TTS 재생 오류: ' + eventError.error, true);
    };
    synthesizer.speak(utterance);
  });


  function setStatus(message, failed = false) {
    status.textContent = message;
    status.classList.toggle('failed', failed);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {...options, headers: authHeaders(options.headers || {})});
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
      throw new Error(`Voice API returned HTTP ${response.status} with non-JSON content (${contentType || 'no content type'}). Check the MAI server terminal traceback.`);
    }
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(`Voice API HTTP ${response.status}: ${JSON.stringify(payload)}`);
      error.status = response.status;
      error.code = payload?.detail?.code ?? null;
      throw error;
    }
    return payload;
  }

  function toWave(samples, sampleRate) {
    const output = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(output);
    const word = (offset, value) => {
      for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
    };
    word(0, 'RIFF');
    view.setUint32(4, output.byteLength - 8, true);
    word(8, 'WAVE');
    word(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    word(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    samples.forEach((sample, index) => {
      const clamped = Math.max(-1, Math.min(1, sample));
      view.setInt16(44 + index * 2, clamped < 0 ? clamped * 32768 : clamped * 32767, true);
    });
    return new Blob([output], {type: 'audio/wav'});
  }

  async function checkSpeechEnd(capture) {
    capture.checking = true;
    try {
      const all = new Float32Array(capture.chunks.reduce((n, c) => n + c.length, 0));
      let offset = 0;
      for (const chunk of capture.chunks) { all.set(chunk, offset); offset += chunk.length; }
      const recentLength = Math.min(all.length, capture.sampleRate * 8);
      const recent = all.subarray(all.length - recentLength);
      const windowStart = (all.length - recentLength) / capture.sampleRate;
      const body = new FormData();
      body.append('file', toWave(recent, capture.sampleRate), 'activity.wav');
      const result = await request('/voice/activity', {method: 'POST', body});
      if (active !== capture) return;
      if (result.speech_end_seconds !== null) {
        const end = windowStart + result.speech_end_seconds;
        capture.lastSpeechEnd = Math.max(capture.lastSpeechEnd ?? 0, end);
      }
      const elapsed = capture.context.currentTime - capture.began;
      if (capture.lastSpeechEnd !== null && elapsed - capture.lastSpeechEnd >= capture.silenceSeconds) {
        void finish();
      }
    } catch (error) {
      if (active === capture) {
        void finish(true);
        setStatus('발화 구간 검사 오류: ' + error.message, true);
      }
    } finally {
      capture.checking = false;
    }
  }

  async function finish(discard = false) {
    if (!active) return;
    const capture = active;
    active = null;
    if (capture.mode === 'enroll') setVoiceMode(false);
    capture.processor.disconnect();
    capture.source.disconnect();
    capture.stream.getTracks().forEach(track => track.stop());
    await capture.context.close();
    if (discard) return;
    if (!capture.chunks.length || capture.speechSeconds < capture.minSpeechSeconds) {
      setStatus('음성이 충분하지 않아 인식을 중단했어.', true);
      return;
    }
    const data = new Float32Array(capture.chunks.reduce((size, chunk) => size + chunk.length, 0));
    let offset = 0;
    for (const chunk of capture.chunks) { data.set(chunk, offset); offset += chunk.length; }
    const body = new FormData();
    body.append('file', toWave(data, capture.sampleRate), 'voice.wav');
    mic.disabled = true;
    let retryAfterMismatch = false;
    try {
      setStatus(capture.mode === 'enroll' ? '목소리 특징 등록 중…' : '화자 확인 및 음성 인식 중…');
      const path = capture.mode === 'enroll' ? '/voice/enroll' : '/voice/transcribe';
      const result = await request(path, {method: 'POST', body});
      if (capture.mode === 'enroll') {
        enroll.hidden = true;
        setStatus('목소리 등록 완료. 마이크 버튼으로 음성 입력할 수 있어.');
      } else {
        const recognized = result.text.trim();
        if (!recognized) throw new Error('음성 인식 결과가 비어 있어.');
        const form = document.getElementById('chat-form');
        const send = document.getElementById('send-btn');
        if (!voiceMode || input.value.trim() || send.classList.contains('stop-mode')) {
          setStatus('자동 전송을 건너뛰었어. 음성모드가 꺼졌거나 작성 중인 메시지 또는 진행 중인 대화가 있어.', true);
          return;
        }
        input.value = recognized;
        input.dispatchEvent(new Event('input'));
        if (send.disabled) throw new Error('전송 버튼이 비활성화되어 자동 전송할 수 없어.');
        form.requestSubmit();
        setStatus('인식된 메시지 전송을 요청했어.');
      }
    } catch (error) {
      setStatus(`음성 처리 실패: ${error.message}`, true);
      retryAfterMismatch = capture.mode === 'transcribe' &&
        error.status === 403 && error.code === 'speaker_mismatch';
    } finally {
      mic.disabled = false;
    }
    if (retryAfterMismatch && voiceMode) {
      void start('transcribe');
    }
  }

  async function start(mode) {
    mic.disabled = true;
    try {
      const config = await request('/voice/config');
      if (mode === 'transcribe' && !config.enrolled) {
        enroll.hidden = false;
        setStatus('먼저 목소리 등록 버튼으로 본인 음성을 등록해 줘.', true);
        return;
      }
      if (mode === 'enroll' && config.enrolled) {
        setStatus('이미 목소리가 등록돼 있어. 재등록은 현재 지원하지 않아.', true);
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const sink = context.createGain();
      sink.gain.value = 0;
      source.connect(processor);
      processor.connect(sink);
      sink.connect(context.destination);
      const capture = {
        mode, stream, context, source, processor, chunks: [],
        sampleRate: context.sampleRate, speechSeconds: 0,
        lastSpeechEnd: null, lastCheck: context.currentTime, checking: false, began: context.currentTime,
        silenceSeconds: config.silence_seconds,
        minSpeechSeconds: mode === "enroll" ? 2 : config.min_speech_seconds
      };
      active = capture;
      processor.onaudioprocess = event => {
        if (active !== capture) return;
        const pcm = event.inputBuffer.getChannelData(0);
        const chunk = new Float32Array(pcm);
        capture.chunks.push(chunk);
        let sum = 0;
        for (let i = 0; i < chunk.length; i += 1) sum += chunk[i] * chunk[i];
        const speech = Math.sqrt(sum / chunk.length) > 0.012;
        const now = context.currentTime;
        if (speech) capture.speechSeconds += chunk.length / capture.sampleRate;
        if (now - capture.began >= 120) {
          void finish();
        } else if (now - capture.lastCheck >= 1 && !capture.checking) {
          capture.lastCheck = now;
          void checkSpeechEnd(capture);
        }
      };
      if (mode === 'transcribe') setVoiceMode(true);
      setStatus(mode === 'enroll' ? '목소리 등록 녹음 중 (최소 3초 말해 줘)' : '듣고 있어. 마지막 말이 끝난 뒤 설정된 시간이 지나면 종료돼.');
    } catch (error) {
      setStatus(`마이크 시작 실패: ${error.message}`, true);
    } finally {
      mic.disabled = false;
    }
  }

  mic.addEventListener('click', () => {
    if (active) { void finish(); setVoiceMode(false); return; }
    if (voiceMode) { setVoiceMode(false); return; }
    void start('transcribe');
  });
  enroll.addEventListener('click', () => {
    if (active) { void finish(); return; }
    void start('enroll');
  });
  if (!navigator.mediaDevices?.getUserMedia) {
    mic.disabled = true;
    setStatus('이 브라우저에서는 HTTPS 또는 localhost 환경의 마이크가 필요해.', true);
  }
})();
