// Web Speech is primary. Local capture is used only when starting it throws.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '..', 'mai', 'app', 'static', 'voice-input.js'), 'utf8');

async function scenario({webAvailable = true, failStart = false, failLater = false}) {
  const elements = new Map();
  let localStarts = 0;
  let recognition = null;
  let warnings = [];
  function node(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false, handlers: {},
      classList: {toggle() {}, contains() {return false;}},
      setAttribute() {}, dispatchEvent() {},
      addEventListener(name, fn) {this.handlers[name] = fn;},
    });
    return elements.get(id);
  }
  class Recognition {
    constructor() {recognition = this;}
    start() {if (failStart) throw Error('Web Speech start rejected'); this.started = true;}
    abort() {}
    stop() {}
  }
  class Context {
    constructor() {this.sampleRate = 16000;}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
    createScriptProcessor() {return {connect() {}, disconnect() {}};}
    createGain() {return {gain: {}, connect() {}};}
    async close() {}
  }
  const sandbox = {
    document: {getElementById: node, addEventListener() {}},
    window: webAvailable ? {SpeechRecognition: Recognition} : {},
    navigator: {mediaDevices: {
      async getUserMedia() {localStarts++; return {getTracks() {return [{stop() {}}];}};}
    }},
    AudioContext: Context,
    AbortController, Blob, FormData, ArrayBuffer, DataView, Float32Array, Event: class {},
    setTimeout, clearTimeout,
    console: {warn(...args) {warnings.push(args);}},
    authHeaders: () => ({}),
    async fetch(url) {
      assert.equal(url, '/voice/config');
      return {
        ok: true, status: 200, headers: {get: () => 'application/json'},
        async json() {return {
          stt_provider: 'web_speech', silence_seconds: 5,
          max_recording_seconds: 120, max_recording_bytes: 20 * 1024 * 1024
        };}
      };
    }
  };
  vm.runInNewContext(source, sandbox);
  node('mic-btn').handlers.click();
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  if (failLater && recognition) recognition.onerror({error: 'network'});
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  return {localStarts, recognition, warnings, status: node('upload-status').textContent};
}

(async () => {
  const success = await scenario({});
  assert.equal(success.localStarts, 0);
  assert.equal(success.recognition.started, true);
  const unsupported = await scenario({webAvailable: false});
  assert.equal(unsupported.localStarts, 1);
  assert.match(unsupported.status, /Local STT listening/);
  const failed = await scenario({failStart: true});
  assert.equal(failed.localStarts, 1);
  assert.equal(failed.warnings.length, 1);
  const runtimeError = await scenario({failLater: true});
  assert.equal(runtimeError.localStarts, 0, 'An already started Web Speech error must not select local STT');
  assert.match(runtimeError.status, /Browser speech error: network/);
  console.log('PASS: Web Speech is primary, only startup failures fallback to local capture');
})().catch(e => {console.error(e); process.exitCode = 1;});
