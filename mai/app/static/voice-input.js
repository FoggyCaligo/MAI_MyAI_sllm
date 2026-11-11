(() => {
  const mic = document.getElementById('mic-btn');
  const speaker = document.getElementById('tts-toggle-btn');
  const voiceSelect = document.getElementById('tts-voice-select');
  const voiceStorageKey = 'mai.tts.voiceURI';
  let selectedVoiceURI = '';
  try { selectedVoiceURI = localStorage.getItem(voiceStorageKey) || ''; } catch (_) { /* Storage may be disabled. */ }

  function refreshVoices() {
    if (!voiceSelect) return;
    const synth = window.speechSynthesis;
    if (!synth || typeof synth.getVoices !== 'function') {
      voiceSelect.disabled = true;
      return;
    }
    const allVoices = synth.getVoices();
    const koreanVoices = allVoices.filter(voice => /^ko(?:-|$)/i.test(voice.lang));
    const voices = koreanVoices.length ? koreanVoices : allVoices;
    voiceSelect.replaceChildren(new Option('Default', ''));
    for (const voice of voices) {
      voiceSelect.add(new Option(voice.name + ' (' + voice.lang + ')', voice.voiceURI));
    }
    // Keep the saved preference even if the browser loads voices asynchronously.
    voiceSelect.value = voices.some(voice => voice.voiceURI === selectedVoiceURI) ? selectedVoiceURI : '';
    voiceSelect.disabled = voices.length === 0;
    voiceSelect.title = voices.length ? 'Voice' : 'No available voice';
  }

  if (voiceSelect) {
    voiceSelect.addEventListener('change', () => {
      selectedVoiceURI = voiceSelect.value;
      try {
        if (selectedVoiceURI) localStorage.setItem(voiceStorageKey, selectedVoiceURI);
        else localStorage.removeItem(voiceStorageKey);
      } catch (_) { /* Selection still applies for this page. */ }
    });
    refreshVoices();
    if ('speechSynthesis' in window) {
      window.speechSynthesis.addEventListener?.('voiceschanged', refreshVoices);
    }
  }
  const status = document.getElementById('upload-status');
  const input = document.getElementById('msg-input');
  let active = null;
  let voiceMode = false;
  let ttsEnabled = true;
  let currentUtterance = null;
  let starting = false;
  let modeRevision = 0;
  let webRecognition = null;

  function stopTts() {
    currentUtterance = null;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }

  function setVoiceMode(enabled) {
    voiceMode = enabled;
    mic.classList.toggle('active', enabled);
    mic.setAttribute('aria-pressed', String(enabled));
    mic.title = enabled ? 'Stop listening' : 'Start listening';
    mic.setAttribute('aria-label', mic.title);
    if (!enabled) {
      modeRevision += 1;
      stopTts();
      if (webRecognition) {
        const recognition = webRecognition;
        webRecognition = null;
        recognition.onresult = null;
        recognition.onend = null;
        recognition.onerror = null;
        recognition.abort();
      }
    }
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


  function submitRecognizedText(recognized) {
    if (!recognized.trim()) throw new Error('Empty speech recognition result.');
    const form = document.getElementById('chat-form');
    const send = document.getElementById('send-btn');
    if (!voiceMode || input.value.trim() || send.classList.contains('stop-mode')) {
      setStatus('Auto-send skipped: voice mode off, draft in progress, or chat busy.', true);
      return false;
    }
    // Speech recognition only fills the normal text composer. It never
    // creates a chat job or loading bubble itself: the same form submit
    // handler used by the Send button owns the entire chat lifecycle.
    input.value = recognized.trim();
    input.dispatchEvent(new Event('input'));
    if (send.disabled) throw new Error('Send is disabled; cannot auto-submit.');
    form.requestSubmit(send);
    setStatus('Sending recognized message…');
    return true;
  }

  function startWebSpeech(config, revision) {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) throw new Error('Web Speech is unavailable in this browser.');
    if (!Number.isFinite(config.silence_seconds) || config.silence_seconds < 1 || config.silence_seconds > 120) {
      throw new Error('Invalid VOICE_SILENCE_SECONDS setting.');
    }
    const recognition = new Recognition();
    recognition.lang = 'ko-KR';
    recognition.continuous = true;
    recognition.interimResults = true;
    let finalText = '';
    let silenceTimer = null;
    let recognitionError = null;
    const isCurrent = () => voiceMode && revision === modeRevision && webRecognition === recognition;
    const clearSilence = () => {
      if (silenceTimer !== null) clearTimeout(silenceTimer);
      silenceTimer = null;
    };
    recognition.onresult = event => {
      if (!isCurrent()) return;
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0]?.transcript || '';
        if (result.isFinal) finalText += text + ' ';
        else interim += text;
      }
      setStatus(interim ? 'Recognizing: ' + interim : 'Listening · waiting for pause…');
      clearSilence();
      if (finalText.trim()) {
        silenceTimer = setTimeout(() => {
          if (isCurrent()) recognition.stop();
        }, config.silence_seconds * 1000);
      }
    };
    recognition.onerror = event => {
      if (!isCurrent()) return;
      recognitionError = event.error;
      if (event.error !== 'no-speech') {
        clearSilence();
        setStatus('Browser speech error: ' + event.error, true);
        setVoiceMode(false);
      }
    };
    recognition.onend = () => {
      clearSilence();
      if (webRecognition === recognition) webRecognition = null;
      if (!voiceMode || revision !== modeRevision) return;
      if (recognitionError && recognitionError !== 'no-speech') return;
      if (!finalText.trim()) {
        void start();
        return;
      }
      try {
        submitRecognizedText(finalText);
      } catch (error) {
        setStatus('Browser speech failed: ' + error.message, true);
        setVoiceMode(false);
      }
    };
    webRecognition = recognition;
    try {
      recognition.start();
    } catch (error) {
      webRecognition = null;
      throw error;
    }
    setStatus('Listening · sends after the configured pause');
  }

  function resumeAfterAnswer() {
    if (!voiceMode || active || webRecognition || starting || currentUtterance) return;
    void start();
  }

  speaker.addEventListener('click', () => {
    ttsEnabled = !ttsEnabled;
    speaker.classList.toggle('muted', !ttsEnabled);
    speaker.setAttribute('aria-pressed', String(ttsEnabled));
    speaker.title = ttsEnabled ? 'Mute voice output' : 'Enable voice output';
    speaker.setAttribute('aria-label', speaker.title);
    if (!ttsEnabled && currentUtterance) {
      stopTts();
      resumeAfterAnswer();
    }
  });

  document.addEventListener('mai:answer-complete', event => {
    if (!voiceMode) return;
    if (!ttsEnabled || !event.detail?.text) { resumeAfterAnswer(); return; }
    if (!('speechSynthesis' in window)) {
      setStatus('Speech synthesis unavailable in this browser.', true);
      return;
    }
    const message = spokenText(event.detail.text);
    if (!message) { resumeAfterAnswer(); return; }
    stopTts();
    const utterance = new SpeechSynthesisUtterance(message);
    currentUtterance = utterance;
    utterance.lang = 'ko-KR';
    utterance.rate = 1;
    if (selectedVoiceURI) {
      const selectedVoice = window.speechSynthesis.getVoices().find(voice => voice.voiceURI === selectedVoiceURI);
      if (selectedVoice) {
        utterance.voice = selectedVoice;
        utterance.lang = selectedVoice.lang;
      }
    }
    utterance.onend = () => {
      if (currentUtterance !== utterance) return;
      currentUtterance = null;
      resumeAfterAnswer();
    };
    utterance.onerror = eventError => {
      if (currentUtterance !== utterance) return;
      currentUtterance = null;
      if (eventError.error !== 'canceled') setStatus('TTS error: ' + eventError.error, true);
    };
    window.speechSynthesis.speak(utterance);
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
      // Older MAI servers returned a plain string for empty Whisper
      // output. Treat that exact legacy response as recoverable too.
      error.code = payload?.detail?.code ??
        (response.status === 422 && payload?.detail === 'no speech recognized'
          ? 'insufficient_speech' : null);
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
        // A transient VAD/API error is not a user request to exit voice
        // mode. Keep collecting audio, and let the 120-second rotation
        // restart the recorder if no speech boundary can be resolved.
        setStatus('Voice activity unavailable · still listening…', true);
      }
    } finally {
      capture.checking = false;
    }
  }

  async function finish(discard = false) {
    if (!active) return;
    const capture = active;
    const revision = modeRevision;
    active = null;
    capture.processor.disconnect();
    capture.source.disconnect();
    capture.stream.getTracks().forEach(track => track.stop());
    await capture.context.close();
    if (discard || !voiceMode || revision !== modeRevision) return;
    if (!capture.chunks.length) {
      setStatus('No audio captured · listening again…', true);
      if (voiceMode && revision === modeRevision) await start();
      return;
    }
    const data = new Float32Array(capture.chunks.reduce((size, chunk) => size + chunk.length, 0));
    let offset = 0;
    for (const chunk of capture.chunks) { data.set(chunk, offset); offset += chunk.length; }
    const body = new FormData();
    body.append('file', toWave(data, capture.sampleRate), 'voice.wav');
    let retryAfterRecoverableVoiceFailure = false;
    try {
      setStatus('Transcribing…');
      const result = await request('/voice/transcribe', {method: 'POST', body});
      if (voiceMode && revision === modeRevision) submitRecognizedText(result.text);
    } catch (error) {
      retryAfterRecoverableVoiceFailure =
        error.status === 422 && error.code === 'insufficient_speech';
      if (voiceMode && revision === modeRevision) {
        setStatus(
          retryAfterRecoverableVoiceFailure
            ? 'No speech recognized · listening again…'
            : `Speech processing failed: ${error.message}`,
          !retryAfterRecoverableVoiceFailure,
        );
      }
    } finally {
      mic.disabled = false;
    }
    if (retryAfterRecoverableVoiceFailure && voiceMode && revision === modeRevision) {
      await start();
    }
  }

  async function start() {
    if (starting || active || webRecognition || !voiceMode) return;
    const revision = modeRevision;
    starting = true;
    try {
      const config = await request('/voice/config');
      if (!['local', 'web_speech'].includes(config.stt_provider)) {
        throw new Error('Invalid server STT provider.');
      }
      if (config.stt_provider === 'web_speech') {
        if (voiceMode && revision === modeRevision) startWebSpeech(config, revision);
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      if (!voiceMode || revision !== modeRevision) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const sink = context.createGain();
      sink.gain.value = 0;
      source.connect(processor);
      processor.connect(sink);
      sink.connect(context.destination);
      const capture = {
        stream, context, source, processor, chunks: [],
        sampleRate: context.sampleRate,
        lastSpeechEnd: null, lastCheck: context.currentTime, checking: false, began: context.currentTime,
        silenceSeconds: config.silence_seconds
      };
      active = capture;
      processor.onaudioprocess = event => {
        if (active !== capture) return;
        const pcm = event.inputBuffer.getChannelData(0);
        const chunk = new Float32Array(pcm);
        capture.chunks.push(chunk);
        const now = context.currentTime;
        if (now - capture.began >= 120) {
          // A long quiet period is not a voice-mode stop. Rotate the
          // recording rather than uploading 120 seconds of silence to STT.
          if (capture.lastSpeechEnd === null) {
            void finish(true).then(() => {
              if (voiceMode && revision === modeRevision) void start();
            });
          } else {
            void finish();
          }
        } else if (now - capture.lastCheck >= 1 && !capture.checking) {
          capture.lastCheck = now;
          void checkSpeechEnd(capture);
        }
      };
      setStatus('Listening · sends after the configured pause');
    } catch (error) {
      setStatus(`Microphone failed: ${error.message}`, true);
    } finally {
      starting = false;
    }
  }

  mic.addEventListener('click', () => {
    if (voiceMode) {
      setVoiceMode(false);
      if (active) void finish(true);
      // The listening prompt belongs to the ended voice session.
      setStatus('');
      return;
    }
    setVoiceMode(true);
    void start();
  });
  if (!navigator.mediaDevices?.getUserMedia) {
    mic.disabled = true;
    setStatus('Microphone requires HTTPS or localhost.', true);
  }
})();
