// Regression: local voice mode keeps listening across idle time and transcription boundaries.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'mai/app/static/voice-input.js'), 'utf8');

async function scenario({emptyFirst = false, longSpeech = false, failSecond = false, stopWhileTranscribing = false}) {
  const nodes = new Map();
  const listeners = {};
  const contexts = [];
  const recordingLengths = [];
  const sampleRate = 8000;
  let resolveFirstChunk;
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
      this.sampleRate = sampleRate;
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
    document: {getElementById: elt, addEventListener(name, callback) {listeners[name] = callback;}},
    window: {}, AudioContext, AbortController, Blob, FormData, Float32Array, ArrayBuffer, DataView,
    Event: class {},
    navigator: {mediaDevices: {async getUserMedia() {
      microphoneStarts++;
      return {getTracks() {return [{stop() {}}];}};
    }}},
    authHeaders: () => ({}),
    async fetch(url, options) {
      let data;
      if (url === '/voice/config') {
        data = {stt_provider: 'local', silence_seconds: 5, max_recording_seconds: 120, max_recording_bytes: 20 * 1024 * 1024};
      } else if (url === '/voice/activity') {
        const audio = new Int16Array(await options.body.get('file').arrayBuffer(), 44);
        assert.ok(audio.length <= 8 * sampleRate, 'VAD uses a bounded audio window');
        const first = audio.findIndex(sample => sample !== 0);
        const last = audio.findLastIndex(sample => sample !== 0);
        data = {
          speech_start_seconds: first < 0 ? null : first / sampleRate,
          speech_end_seconds: last < 0 ? null : (last + 1) / sampleRate,
        };
      } else if (url === '/voice/transcribe') {
        transcribed++;
        const audio = new Int16Array(await options.body.get('file').arrayBuffer(), 44);
        recordingLengths.push(audio.length);
        assert.ok(audio.length <= 120 * sampleRate, 'A transcription chunk must meet the server time limit');
        assert.ok(audio[0], 'The first chunk must start with the detected speech');
        if (emptyFirst && transcribed === 1) {
          return {
            ok: false, status: 422,
            headers: {get: () => 'application/json'},
            async json() {return {detail: {code: 'insufficient_speech', message: 'no speech recognized'}};},
          };
        }
        if (failSecond && transcribed === 2) throw new Error('second chunk failed');
        if (stopWhileTranscribing && transcribed === 1) {
          await new Promise(resolve => {resolveFirstChunk = resolve;});
          assert.equal(options.signal.aborted, true);
        }
        data = {text: longSpeech ? `구간 ${transcribed}` : '인식 성공'};
      } else throw new Error('unexpected endpoint ' + url);
      return {ok: true, status: 200,
        headers: {get: () => 'application/json'}, async json() {return data;}};
    },
  };
  vm.runInNewContext(source, sandbox);
  const flush = async () => {
    for (let i = 0; i < 18; i++) await new Promise(resolve => setImmediate(resolve));
  };
  const feed = (context, seconds, speaking = false) => {
    context.processor.onaudioprocess({inputBuffer: {
      getChannelData() {return new Float32Array(Math.round(seconds * sampleRate)).fill(speaking ? 0.1 : 0);}
    }});
  };
  const feedSeconds = async (context, seconds, speaking = false) => {
    for (let i = 0; i < seconds; i++) {
      feed(context, 1, speaking);
      await flush();
    }
  };

  elt('mic-btn').handlers.click();
  await flush();
  assert.equal(microphoneStarts, 1);
  if (longSpeech) {
    const context = contexts[0];
    await feedSeconds(context, 130);
    await feedSeconds(context, 120, true);
    assert.equal(microphoneStarts, 1, 'Idle time and long speech keep the same microphone capture');
    assert.equal(transcribed, 0, 'The 120-second clock begins at speech, not microphone start');
    await feedSeconds(context, failSecond ? 121 : 125, true);
    assert.equal(sent, 0, 'A full chunk must not send a chat message');
    if (stopWhileTranscribing) {
      await feedSeconds(context, 5);
      elt('mic-btn').handlers.click();
      resolveFirstChunk();
      await flush();
      assert.equal(transcribed, 1, 'Stopping drops queued transcription chunks');
      assert.equal(sent, 0, 'Stopping during transcription must discard the pending message');
      return;
    }
    assert.equal(transcribed, 2, 'Audio keeps reaching Whisper across both chunk boundaries');
    if (failSecond) {
      assert.match(elt('upload-status').textContent, /second chunk failed/);
      assert.equal(sent, 0, 'A failed chunk must not send partial text');
      return;
    }
    await feedSeconds(context, 4);
    assert.equal(sent, 0, 'An utterance stays open until the full configured silence');
    await feedSeconds(context, 1);
    assert.equal(sent, 1);
    assert.deepEqual(recordingLengths, [120, 120, 5].map(seconds => seconds * sampleRate));
    assert.equal(elt('msg-input').value, '구간 1 구간 2 구간 3');
  } else {
    feed(contexts[0], 0.1, true);
    feed(contexts[0], 5.9);
    await flush();
    assert.equal(transcribed, 1);
    assert.equal(sent, 0, 'Empty Whisper transcript must not be auto-submitted');
    assert.equal(microphoneStarts, 2, 'Recoverable STT must restart listening');
    assert.equal(elt('mic-btn').classList.active, true);
    // Next utterance works through the same text form.
    feed(contexts[1], 0.1, true);
    feed(contexts[1], 5.9);
    await flush();
    assert.equal(transcribed, 2);
    assert.equal(sent, 1);
    assert.equal(elt('msg-input').value, '인식 성공');
    listeners['mai:answer-complete']({detail: {text: '반가워요'}});
    await flush();
    assert.equal(microphoneStarts, 3, 'Unavailable TTS must resume local capture');
    assert.match(elt('upload-status').textContent, /Listening.*Speech synthesis unavailable/);
  }
  elt('mic-btn').handlers.click();
  assert.equal(elt('mic-btn').classList.active, false);
}

(async () => {
  await scenario({emptyFirst: true});
  await scenario({longSpeech: true});
  await scenario({longSpeech: true, failSecond: true});
  await scenario({longSpeech: true, stopWhileTranscribing: true});
  console.log('PASS: local STT keeps a single turn across chunk boundaries and discards canceled or failed turns.');
})().catch(err => {console.error(err); process.exitCode = 1;});
