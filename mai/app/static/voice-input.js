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
  const wavHeaderBytes = 44;
  const pcmSampleBytes = Int16Array.BYTES_PER_ELEMENT;
  let active = null;
  let finishingCapture = null;
  let voiceMode = false;
  let ttsEnabled = false;
  let currentUtterance = null;
  let ttsError = '';
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
      finishingCapture?.controller.abort();
      finishingCapture = null;
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
    if (!voiceMode || active || finishingCapture || webRecognition || starting || currentUtterance) return;
    void start();
  }

  function finishTts(error = '') {
    currentUtterance = null;
    ttsError = error;
    if (error) setStatus('');
    resumeAfterAnswer();
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
    if (!ttsEnabled || !event.detail?.text) { resumeAfterAnswer(); return; }
    try {
      const synth = window.speechSynthesis;
      if (typeof synth?.speak !== 'function') {
        finishTts('Speech synthesis unavailable in this browser.');
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
        const selectedVoice = synth.getVoices().find(voice => voice.voiceURI === selectedVoiceURI);
        if (selectedVoice) {
          utterance.voice = selectedVoice;
          utterance.lang = selectedVoice.lang;
        }
      }
      utterance.onend = () => {
        if (currentUtterance === utterance) finishTts();
      };
      utterance.onerror = eventError => {
        if (currentUtterance === utterance) {
          finishTts(eventError.error === 'canceled' ? '' : eventError.error);
        }
      };
      synth.speak(utterance);
    } catch (error) {
      finishTts(error.message);
    }
  });

  function setStatus(message, failed = false) {
    status.textContent = [message, ttsError && 'TTS error: ' + ttsError].filter(Boolean).join(' · ');
    status.classList.toggle('failed', failed || Boolean(ttsError));
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
    const output = new ArrayBuffer(wavHeaderBytes + samples.length * pcmSampleBytes);
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
    view.setUint32(40, samples.length * pcmSampleBytes, true);
    samples.forEach((sample, index) => {
      const clamped = Math.max(-1, Math.min(1, sample));
      view.setInt16(wavHeaderBytes + index * pcmSampleBytes, clamped < 0 ? clamped * 32768 : clamped * 32767, true);
    });
    return new Blob([output], {type: 'audio/wav'});
  }

  function readSamples(capture, start, end) {
    const samples = new Float32Array(end - start);
    let offset = capture.bufferStart;
    for (const chunk of capture.chunks) {
      const chunkEnd = offset + chunk.length;
      if (chunkEnd > start) {
        const from = Math.max(start, offset);
        const to = Math.min(end, chunkEnd);
        samples.set(chunk.subarray(from - offset, to - offset), from - start);
      }
      if (chunkEnd >= end) break;
      offset = chunkEnd;
    }
    return samples;
  }

  function discardSamplesBefore(capture, sample) {
    let count = 0;
    while (count < capture.chunks.length && capture.bufferStart + capture.chunks[count].length <= sample) {
      capture.bufferStart += capture.chunks[count].length;
      count += 1;
    }
    if (count) capture.chunks.splice(0, count);
    if (sample > capture.bufferStart) {
      capture.chunks[0] = capture.chunks[0].subarray(sample - capture.bufferStart);
      capture.bufferStart = sample;
    }
  }

  function queueTranscription(capture, end) {
    const samples = readSamples(capture, capture.chunkStart, end);
    capture.chunkStart = end;
    const {signal} = capture.controller;
    capture.transcription = capture.transcription.then(async () => {
      if (signal.aborted || capture.transcriptionError) return;
      try {
        const body = new FormData();
        body.append('file', toWave(samples, capture.sampleRate), 'voice.wav');
        const result = await request('/voice/transcribe', {method: 'POST', body, signal});
        if (signal.aborted) return;
        if (typeof result.text !== 'string' || !result.text.trim()) {
          throw new Error('Invalid speech transcription response.');
        }
        capture.transcripts.push(result.text.trim());
      } catch (error) {
        if (signal.aborted) return;
        capture.transcriptionError = error;
        if (active === capture) void finish();
      }
    });
  }

  async function checkSpeechEnd(capture) {
    capture.checking = true;
    try {
      const {activitySamples, sampleRate} = capture;
      const windowEnd = Math.min(capture.sampleCount, capture.checkedThrough + activitySamples);
      const windowStart = Math.max(capture.bufferStart, windowEnd - activitySamples);
      const body = new FormData();
      body.append('file', toWave(readSamples(capture, windowStart, windowEnd), sampleRate), 'activity.wav');
      const result = await request('/voice/activity', {method: 'POST', body, signal: capture.controller.signal});
      if (active !== capture) return;
      const {speech_start_seconds: first, speech_end_seconds: last} = result;
      if (first !== null || last !== null) {
        if (!Number.isFinite(first) || !Number.isFinite(last) || first < 0 || first >= last ||
            first * sampleRate >= windowEnd - windowStart) {
          throw new Error('Invalid speech activity response.');
        }
        const speechStart = windowStart + Math.floor(first * sampleRate);
        const speechEnd = Math.min(windowEnd, windowStart + Math.ceil(last * sampleRate));
        capture.chunkStart ??= speechStart;
        capture.lastSpeechEnd = Math.max(capture.lastSpeechEnd ?? 0, speechEnd);
      }

      capture.checkedThrough = windowEnd;
      while (capture.lastSpeechEnd !== null && capture.lastSpeechEnd - capture.chunkStart > capture.chunkSamples) {
        // Leave audio for the next chunk even if we crossed the limit by a few samples.
        const end = Math.min(capture.chunkStart + capture.chunkSamples, capture.lastSpeechEnd - capture.chunkTailSamples);
        queueTranscription(capture, end);
      }
      if (capture.lastSpeechEnd !== null && windowEnd - capture.lastSpeechEnd >= capture.silenceSamples) {
        void finish();
        return;
      }
      const recentStart = Math.max(capture.bufferStart, windowEnd - activitySamples + sampleRate);
      discardSamplesBefore(capture, Math.min(capture.chunkStart ?? recentStart, recentStart));
    } catch (error) {
      if (active === capture) {
        setStatus(`Voice activity failed: ${error.message} · still listening…`, true);
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
    if (discard) capture.controller.abort();
    else finishingCapture = capture;
    capture.processor.disconnect();
    capture.source.disconnect();
    capture.stream.getTracks().forEach(track => track.stop());
    let retryAfterRecoverableVoiceFailure = false;
    try {
      await capture.context.close();
      if (discard || !voiceMode || revision !== modeRevision) return;
      setStatus('Transcribing…');
      if (!capture.transcriptionError && capture.lastSpeechEnd > capture.chunkStart) {
        queueTranscription(capture, capture.lastSpeechEnd);
      }
      await capture.transcription;
      if (!voiceMode || revision !== modeRevision) return;
      if (capture.transcriptionError) throw capture.transcriptionError;
      submitRecognizedText(capture.transcripts.join(' '));
    } catch (error) {
      retryAfterRecoverableVoiceFailure =
        !capture.transcripts.length && error.status === 422 && error.code === 'insufficient_speech';
      if (voiceMode && revision === modeRevision) {
        setStatus(
          retryAfterRecoverableVoiceFailure
            ? 'No speech recognized · listening again…'
            : `Speech processing failed: ${error.message}`,
          !retryAfterRecoverableVoiceFailure,
        );
      }
    } finally {
      if (finishingCapture === capture) finishingCapture = null;
    }
    if (retryAfterRecoverableVoiceFailure && voiceMode && revision === modeRevision) {
      await start();
    }
  }

  async function start() {
    if (starting || active || finishingCapture || webRecognition || !voiceMode) return;
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
      const {max_recording_seconds: maxSeconds, max_recording_bytes: maxBytes} = config;
      if (!Number.isFinite(maxSeconds) || maxSeconds < 1 || !Number.isSafeInteger(maxBytes) ||
          maxBytes < wavHeaderBytes + pcmSampleBytes) {
        throw new Error('Invalid voice recording limits.');
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
      const chunkSamples = Math.min(
        Math.floor(context.sampleRate * maxSeconds),
        Math.floor((maxBytes - wavHeaderBytes) / pcmSampleBytes),
      );
      const capture = {
        stream, context, source, processor, chunks: [],
        sampleRate: context.sampleRate,
        sampleCount: 0, bufferStart: 0, checkedThrough: 0, lastCheck: 0, checking: false,
        chunkStart: null, lastSpeechEnd: null,
        activitySamples: context.sampleRate * 8,
        silenceSamples: context.sampleRate * config.silence_seconds,
        chunkSamples, chunkTailSamples: Math.min(context.sampleRate, Math.floor(chunkSamples / 2)),
        controller: new AbortController(),
        transcripts: [], transcription: Promise.resolve(), transcriptionError: null,
      };
      active = capture;
      processor.onaudioprocess = event => {
        if (active !== capture) return;
        const pcm = event.inputBuffer.getChannelData(0);
        const chunk = new Float32Array(pcm);
        capture.chunks.push(chunk);
        capture.sampleCount += chunk.length;
        if (!capture.checking && capture.sampleCount - capture.lastCheck >= capture.sampleRate) {
          capture.lastCheck = capture.sampleCount;
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
