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
  const utterances = [];
  let synthesisError = null;
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
    document: {
      getElementById(id) {return id === 'tts-voice-select' ? null : element(id);},
      addEventListener(name, callback) {listeners[name] = callback;},
      createElement() {
        return {set innerHTML(text) {this.textContent = text;}, querySelectorAll() {return [];}};
      },
    },
    window: {
      SpeechRecognition,
      speechSynthesis: {
        getVoices() {return [];},
        cancel() {},
        speak(utterance) {
          if (synthesisError) throw synthesisError;
          utterances.push(utterance);
        },
      },
    },
    SpeechSynthesisUtterance: class {},
    renderMarkdown: text => text,
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
  const submitVoiceTurn = () => {
    element('msg-input').value = '';
    const recognition = recognitions.at(-1);
    recognition.onresult({resultIndex: 0, results: [{isFinal: true, 0: {transcript: '안녕하세요'}}]});
    recognition.onend();
  };
  const completeAnswer = () => listeners['mai:answer-complete']({detail: {text: '반가워요'}});
  submitVoiceTurn();
  assert.equal(element('msg-input').value, '안녕하세요');
  assert.equal(sent, 1);

  completeAnswer();
  await flush();
  assert.equal(recognitions.length, 1, 'STT must wait while TTS is active');
  utterances[0].onerror({error: 'synthesis-failed'});
  await flush();
  assert.equal(recognitions.length, 2, 'TTS error must resume listening');
  assert.match(element('upload-status').textContent, /Listening.*TTS error: synthesis-failed/);

  submitVoiceTurn();
  assert.equal(sent, 2, 'Recognition after TTS failure must still submit the next turn');
  completeAnswer();
  utterances[0].onend();
  await flush();
  assert.equal(recognitions.length, 2, 'A stale TTS callback must not resume listening');
  utterances[1].onend();
  await flush();
  assert.equal(recognitions.length, 3, 'Successful TTS must resume listening');
  assert.doesNotMatch(element('upload-status').textContent, /TTS error/);

  submitVoiceTurn();
  synthesisError = new Error('speak failed');
  completeAnswer();
  await flush();
  assert.equal(recognitions.length, 4, 'A synchronous TTS failure must resume listening');
  assert.match(element('upload-status').textContent, /Listening.*TTS error: speak failed/);

  submitVoiceTurn();
  element('tts-toggle-btn').handlers.click();
  completeAnswer();
  await flush();
  assert.equal(recognitions.length, 5, 'TTS-disabled reply must resume listening');
  element('mic-btn').handlers.click();
  assert.equal(recognitions[4].aborted, true);
  console.log('PASS: Web Speech STT resumes after TTS completion, failure, or mute');
})().catch(error => {console.error(error); process.exitCode = 1;});
