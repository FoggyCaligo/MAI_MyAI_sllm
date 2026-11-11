const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'mai', 'app', 'static', 'voice-input.js'), 'utf8');

(async () => {
  const elements = new Map();
  const listeners = {};
  const requests = [];
  const recognitions = [];
  let sent = 0;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', disabled: false, hidden: false, textContent: '', handlers: {},
      classList: {toggle() {}, contains() {return false;}},
      setAttribute() {}, dispatchEvent() {},
      addEventListener(name, callback) {this.handlers[name] = callback;},
      requestSubmit() {sent++;}
    });
    return elements.get(id);
  }
  class SpeechRecognition {
    constructor() {recognitions.push(this);}
    start() {this.started = true;}
    stop() {this.stopped = true;}
    abort() {this.aborted = true;}
  }
  const sandbox = {
    document: {getElementById: element, addEventListener(name, callback) {listeners[name] = callback;}},
    window: {SpeechRecognition},
    navigator: {mediaDevices: {getUserMedia() {throw Error('Local microphone path must not be used');}}},
    Event: class {},
    setTimeout, clearTimeout,
    authHeaders: () => ({}),
    async fetch(url) {
      requests.push(url);
      assert.equal(url, '/voice/config');
      return {ok: true, status: 200, headers: {get: () => 'application/json'}, async json() {
        return {stt_provider: 'web_speech', enrolled: false, silence_seconds: 5};
      }};
    }
  };
  vm.runInNewContext(source, sandbox);
  const flush = async () => {for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));};
  element('mic-btn').handlers.click();
  await flush();
  assert.equal(recognitions.length, 1);
  assert.equal(recognitions[0].started, true);
  assert.deepEqual(requests, ['/voice/config']);
  recognitions[0].onresult({resultIndex: 0, results: [{isFinal: true, 0: {transcript: '안녕하세요'}}]});
  recognitions[0].onend();
  assert.equal(element('msg-input').value, '안녕하세요');
  assert.equal(sent, 1);
  element('tts-toggle-btn').handlers.click();
  listeners['mai:answer-complete']({detail: {text: '반가워요'}});
  await flush();
  assert.equal(recognitions.length, 2, 'TTS-disabled reply must resume listening');
  element('mic-btn').handlers.click();
  assert.equal(recognitions[1].aborted, true);
  console.log('PASS: configured Web Speech STT keeps the continuous voice loop');
})().catch(error => {console.error(error); process.exitCode = 1;});
