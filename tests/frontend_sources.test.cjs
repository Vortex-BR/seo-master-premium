'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function sourcesUI() {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {innerHTML: ''});
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, setTimeout, clearTimeout,
  });
  for (const filename of ['app.js', 'transcription.js', 'editorial.js', 'planning.js', 'publishing.js']) {
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', filename), 'utf8')
      .replace(/\nboot\(\);\s*$/, '');
    vm.runInContext(source, context);
  }
  return {context, node};
}

test('unknown source time remains unknown without a fabricated link', () => {
  const {context} = sourcesUI();
  for (const value of [undefined, null, NaN, Infinity, -1, true, '15']) {
    context.value = value;
    assert.equal(vm.runInContext('sourceTimeLabel(value)', context), 'Trecho');
    assert.equal(vm.runInContext('sourceTimeUrl("https://youtube.com/watch?v=abcdefghijk",value)', context),
      'https://youtube.com/watch?v=abcdefghijk');
  }
  assert.equal(vm.runInContext('sourceTimeLabel(0)', context), '0:00');
  assert.equal(vm.runInContext('sourceTimeLabel(225.95)', context), '3:45');
  assert.equal(vm.runInContext('sourceTimeUrl("https://youtube.com/watch?v=abcdefghijk",225.95)', context),
    'https://youtube.com/watch?v=abcdefghijk&t=225s');
});

test('global recognition warnings display uncertainty and escape content', () => {
  const {context} = sourcesUI();
  context.warnings = [
    {start: null, reason: '<script>global</script>'}, {reason: 'Sem localização'},
    {start: 0, reason: 'Início'}, {start: 225, reason: 'Termo incerto'},
  ];
  const html = vm.runInContext('sourceAudioWarnings({transcription_warnings:warnings})', context);
  assert.equal((html.match(/Tempo não informado/g) || []).length, 2);
  assert.match(html, /0:00 · Início/);
  assert.match(html, /3:45 · Termo incerto/);
  assert.match(html, /&lt;script&gt;global&lt;\/script&gt;/);
  assert.doesNotMatch(html, /NaN|undefined|<script>/);
});

test('source cards preserve math as inert text and unknown timestamps', () => {
  const {context, node} = sourcesUI();
  context.source = {
    id: 'v1', video_id: 'abcdefghijk', title: 'Teste', author: 'Autor', thumbnail: '',
    url: 'https://youtube.com/watch?v=abcdefghijk', status: 'ok',
    segments: [{id: 'v1s1', text: '2 < valor < 10 e <script>inert</script>'}],
  };
  vm.runInContext('state.settings={};state.job={id:"saved",status:"ready",sources:[source]};sourcesTab();', context);
  const html = node('#detail-body').innerHTML;
  assert.match(html, /2 &lt; valor &lt; 10/);
  assert.match(html, /v1s1<br>Trecho/);
  assert.doesNotMatch(html, /&amp;t=|NaN|undefined|<script>/);
});
