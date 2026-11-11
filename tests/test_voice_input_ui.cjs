const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'mai', 'app', 'static', 'voice-input.js'), 'utf8');

async function run() {
  const nodes = new Map();
  const calls = [];
  let context;
  let processor;
  let sent = 0;
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', disabled: false, textContent: '', handlers: {},
      classList: {toggle() {}, contains() {return false;}},
      setAttribute() {}, dispatchEvent() {},
      addEventListener(name, fn) {this.handlers[name] = fn;},
      requestSubmit() {sent++;}
    });
    return nodes.get(id);
  }
  class AudioContext {
    constructor() {this.currentTime = 0; this.sampleRate = 16000; context = this;}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
    createScriptProcessor() {processor = {connect() {}, disconnect() {}}; return processor;}
    createGain() {return {gain: {}, connect() {}};}
    async close() {}
  }
  const sandbox = {
    document: {getElementById: element, addEventListener() {}},
    window: {}, navigator: {mediaDevices: {async getUserMedia() {return {getTracks() {return [{stop() {}}];}};}}},
    AudioContext, Blob, FormData, ArrayBuffer, DataView, Float32Array, Event: class {},
    authHeaders: () => ({}),
    async fetch(path) {
      calls.push(path);
      const data = path === '/voice/config'
        ? {stt_provider: 'local', silence_seconds: 5}
        : path === '/voice/activity' ? {speech_end_seconds: 0.1} : {text: '응'};
      return {ok: true, status: 200, headers: {get() {return 'application/json';}}, async json() {return data;}};
    }
  };
  vm.runInNewContext(source, sandbox);
  const flush = async () => {for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));};
  element('mic-btn').handlers.click();
  await flush();
  assert.equal(element('upload-status').textContent, 'Listening · sends after the configured pause');
  context.currentTime = 0.1;
  processor.onaudioprocess({inputBuffer: {getChannelData() {return new Float32Array(1600).fill(0.2);}}});
  context.currentTime = 6;
  processor.onaudioprocess({inputBuffer: {getChannelData() {return new Float32Array(94400);}}});
  await flush();
  assert.equal(calls.filter(path => path === '/voice/transcribe').length, 1);
  assert.equal(sent, 1);
  assert.equal(element('msg-input').value, '응');
}

(async () => {
  await run();
  console.log('PASS: short voice input submitted without speaker registration.');
})().catch(error => {console.error(error); process.exitCode = 1;});
