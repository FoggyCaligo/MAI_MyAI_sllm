// Regression: continuous voice mode survives silence/empty Whisper, but stops on user request.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'mai/app/static/voice-input.js'), 'utf8');

async function scenario({emptyFirst = false, idleOnly = false}) {
  const nodes = new Map();
  const contexts = [];
  const requests = [];
  let sent = 0, transcribed = 0, microphoneStarts = 0;
  const elt = id => {
    if (!nodes.has(id)) {
      nodes.set(id, {
        value: '', disabled: false, textContent: '', handlers: {}, attrs: {},
        classList: {
          active: false,
          toggle(key, on) { if (key === 'active') this.active = on; },
          contains(key) { return key === 'stop-mode' ? false : this.active; },
        },
        setAttribute(k, v) { this.attrs[k] = v; },
        dispatchEvent() {},
        addEventListener(key, cb) { this.handlers[key] = cb; },
        requestSubmit() { sent++; },
      });
    }
    return nodes.get(id);
  };
  class AudioContext {
    constructor() {
      this.sampleRate = 16000;
      this.currentTime = 0;
      contexts.push(this);
    }
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    createScriptProcessor() {
      const processor = {connect() {}, disconnect() {}};
      this.processor = processor;
      return processor;
    }
    createGain() { return {gain: {}, connect() {}}; }
    async close() {}
  }
  const sandbox = {
    document: {getElementById: elt, addEventListener() {}},
    window: {}, AudioContext, Blob, FormData, Float32Array, ArrayBuffer, DataView,
    Event: class {},
    navigator: {mediaDevices: {async getUserMedia() {
      microphoneStarts++;
      return {getTracks() {return [{stop() {}}];}};
    }}},
    authHeaders: () => ({}),
    async fetch(url) {
      requests.push(url);
      let data;
      if (url === '/voice/config') data = {stt_provider: 'local', silence_seconds: 5};
      else if (url === '/voice/activity') data = {speech_end_seconds: idleOnly ? null : 0.1};
      else if (url === '/voice/transcribe') {
        transcribed++;
        if (emptyFirst && transcribed === 1) {
          return {
            ok: false, status: 422,
            headers: {get: () => 'application/json'},
            async json() {return {detail: {code: 'insufficient_speech', message: 'no speech recognized'}};},
          };
        }
        data = {text: '인식 성공'};
      } else throw new Error('unexpected endpoint ' + url);
      return {ok: true, status: 200,
        headers: {get: () => 'application/json'}, async json() {return data;}};
    },
  };
  vm.runInNewContext(source, sandbox);
  const flush = async () => {
    for (let i = 0; i < 18; i++) await new Promise(resolve => setImmediate(resolve));
  };
  const feed = (context, seconds, amount = 1600) => {
    context.currentTime = seconds;
    context.processor.onaudioprocess({inputBuffer: {
      getChannelData() {return new Float32Array(amount).fill(0.1);}
    }});
  };

  elt('mic-btn').handlers.click();
  await flush();
  assert.equal(microphoneStarts, 1);
  if (idleOnly) {
    // Nothing is detected for two minutes: recorder rotates without submitting.
    feed(contexts[0], 121);
    await flush();
    assert.equal(microphoneStarts, 2);
    assert.equal(sent, 0);
    assert.equal(transcribed, 0);
    assert.equal(elt('mic-btn').classList.active, true);
    // Explicit off prevents any automatic listening restart.
    elt('mic-btn').handlers.click();
    await flush();
    assert.equal(elt('mic-btn').classList.active, false);
    assert.equal(microphoneStarts, 2);
  } else {
    feed(contexts[0], 0.1);
    feed(contexts[0], 6, 94400);
    await flush();
    assert.equal(transcribed, 1);
    assert.equal(sent, 0, 'Empty Whisper transcript must not be auto-submitted');
    assert.equal(microphoneStarts, 2, 'Recoverable STT must restart listening');
    assert.equal(elt('mic-btn').classList.active, true);
    // Next utterance works through the same text form.
    feed(contexts[1], 0.1);
    feed(contexts[1], 6, 94400);
    await flush();
    assert.equal(transcribed, 2);
    assert.equal(sent, 1);
    assert.equal(elt('msg-input').value, '인식 성공');
    elt('mic-btn').handlers.click();
    assert.equal(elt('mic-btn').classList.active, false);
  }
}

(async () => {
  await scenario({idleOnly: true});
  await scenario({emptyFirst: true});
  console.log('PASS: silent voice mode persists, empty Whisper retries, manual stop respected.');
})().catch(err => {console.error(err); process.exitCode = 1;});
