(() => {
  const mic = document.getElementById('mic-btn');
  const enroll = document.getElementById('voice-enroll-btn');
  const status = document.getElementById('upload-status');
  const input = document.getElementById('msg-input');
  let active = null;

  function setStatus(message, failed = false) {
    status.textContent = message;
    status.classList.toggle('failed', failed);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {...options, headers: authHeaders(options.headers || {})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Voice HTTP ${response.status}`);
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

  async function finish() {
    if (!active) return;
    const capture = active;
    active = null;
    mic.classList.remove('active');
    mic.setAttribute('aria-pressed', 'false');
    mic.title = '음성 입력 시작';
    mic.setAttribute('aria-label', '음성 입력 시작');
    capture.processor.disconnect();
    capture.source.disconnect();
    capture.stream.getTracks().forEach(track => track.stop());
    await capture.context.close();
    if (!capture.chunks.length || capture.speechSeconds < 2) {
      setStatus('음성이 충분하지 않아 인식을 중단했어.', true);
      return;
    }
    const data = new Float32Array(capture.chunks.reduce((size, chunk) => size + chunk.length, 0));
    let offset = 0;
    for (const chunk of capture.chunks) { data.set(chunk, offset); offset += chunk.length; }
    const body = new FormData();
    body.append('file', toWave(data, capture.sampleRate), 'voice.wav');
    mic.disabled = true;
    try {
      setStatus(capture.mode === 'enroll' ? '목소리 특징 등록 중…' : '화자 확인 및 음성 인식 중…');
      const path = capture.mode === 'enroll' ? '/voice/enroll' : '/voice/transcribe';
      const result = await request(path, {method: 'POST', body});
      if (capture.mode === 'enroll') {
        enroll.hidden = true;
        setStatus('목소리 등록 완료. 마이크 버튼으로 음성 입력할 수 있어.');
      } else {
        input.value = input.value ? input.value + '\n' + result.text : result.text;
        input.dispatchEvent(new Event('input'));
        input.focus();
        setStatus('인식된 내용을 확인한 뒤 전송해 줘.');
      }
    } catch (error) {
      setStatus(`음성 처리 실패: ${error.message}`, true);
    } finally {
      mic.disabled = false;
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
        lastVoice: context.currentTime, began: context.currentTime,
        silenceSeconds: config.silence_seconds
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
        if (speech) {
          capture.speechSeconds += chunk.length / capture.sampleRate;
          capture.lastVoice = now;
        }
        if (now - capture.lastVoice >= capture.silenceSeconds || now - capture.began >= 120) {
          void finish();
        }
      };
      mic.classList.add('active');
      mic.setAttribute('aria-pressed', 'true');
      mic.title = '음성 입력 종료';
      mic.setAttribute('aria-label', '음성 입력 종료');
      setStatus(mode === 'enroll' ? '목소리 등록 녹음 중 (최소 3초 말해 줘)' : '듣고 있어. 다시 누르거나 무음 시간이 지나면 종료돼.');
    } catch (error) {
      setStatus(`마이크 시작 실패: ${error.message}`, true);
    } finally {
      mic.disabled = false;
    }
  }

  mic.addEventListener('click', () => {
    if (active) { void finish(); return; }
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
