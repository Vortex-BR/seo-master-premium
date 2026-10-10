'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function studio() {
  const nodes = new Map();
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, {innerHTML: ''});
    return nodes.get(key);
  };
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, Date,
    transcriptionSettings: () => '', referenceSettings: () => '',
  });
  const script = fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8')
    .replace(/\nboot\(\);\s*$/, '');
  vm.runInContext(script, context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/editorial.js'), 'utf8'), context);
  return {
    history(usage, id = 'article-1') {
      context.fixture = {usage, events: [], id};
      vm.runInContext('state.job=fixture;historyTab();', context);
      return node('#detail-body').innerHTML;
    },
    spending(value) {
      context.fixture = value;
      return vm.runInContext('spendingHtml(fixture)', context);
    },
    usd(value) {
      context.fixture = value;
      return vm.runInContext('usd(fixture)', context);
    },
    settings(value) {
      context.fixture = value;
      vm.runInContext('state.settings=fixture;settings();', context);
      return node('#content').innerHTML;
    },
    async team(usageRows) {
      context.fixtureJob = {id: 'article-1', status: 'ready'};
      context.fixtureReport = {cycle: {cycle_id: 'cycle-1', calls: 1, profile: {version: 'fixture'},
        knowledge_version: 'fixture'}, runs: [{cycle_id: 'cycle-1', data: {usage: usageRows}}],
        roster: [], changes: [], messages: []};
      vm.runInContext("api=async()=>fixtureReport;state.view='detail';state.tab='team';state.job=fixtureJob;", context);
      await vm.runInContext('teamTab()', context);
      return node('#detail-body').innerHTML;
    },
  };
}

test('audio, images and partial tool usage show known tokens without claiming a complete total', () => {
  const html = studio().history([
    {kind: 'audio', duration_seconds: 60},
    {kind: 'image', input_tokens: 12, output_tokens: null},
    {stage: 'writer', input_tokens: 100, output_tokens: 20},
    {stage: 'research', input_tokens: null, output_tokens: 3},
  ]);
  assert.doesNotMatch(html, /NaN|undefined/);
  assert.match(html, /Pelo menos 135 tokens/);
  assert.match(html, /3 registro\(s\) sem contagem completa/);
  assert.match(html, /histórico/);
});

test('empty or completely unknown usage remains unmeasured rather than zero tokens', () => {
  for (const rows of [[], [{kind: 'audio'}], null]) {
    const html = studio().history(rows);
    assert.match(html, /Tokens não informados/);
    assert.doesNotMatch(html, /Uso registrado: 0|NaN|undefined/);
  }
});

test('observed zero tokens remain valid while malformed counts are excluded', () => {
  const ui = studio();
  assert.match(ui.history([{input_tokens: 0, output_tokens: 0}]), /0 tokens/);
  const html = ui.history([
    {input_tokens: true, output_tokens: -1},
    {input_tokens: '100', output_tokens: Infinity},
    {input_tokens: NaN, output_tokens: 2},
  ]);
  assert.match(html, /Pelo menos 2 tokens/);
  assert.doesNotMatch(html, /NaN|102 tokens|Infinity/);
});

test('provider cached tokens are identified separately from application reuse', () => {
  const html = studio().history([
    {input_tokens: 100, output_tokens: 10, cached_input_tokens: 40},
    {input_tokens: 30, output_tokens: 2, cached_input_tokens: null},
  ]);
  assert.match(html, /142 tokens/);
  assert.match(html, /40 tokens de entrada em cache do provedor/);
  assert.match(html, /reutilização de etapas pela aplicação/);
});

test('budget display distinguishes accounting, pending reservations and provider invoice', () => {
  const html = studio().spending({spent_usd: 0.12, limit_usd: 2, remaining_usd: 1.5,
    reserved_usd: 0.38, historical_estimate: true, accounting_notice: 'fixture ledger'});
  assert.match(html, /Reservado/);
  assert.match(html, /fatura/);
  assert.match(html, /Estimativas históricas/);
  assert.match(html, /todas as versões/);
  assert.match(html, /fixture ledger/);
});

test('missing financial telemetry does not become a free charge', () => {
  const ui = studio();
  for (const value of [null, undefined, '', true, NaN, Infinity, -1]) {
    assert.equal(ui.usd(value), 'Não medido');
  }
  assert.doesNotMatch(ui.usd(0), /Não medido/);
  assert.doesNotMatch(ui.usd('0.025'), /Não medido/);
  const html = ui.spending({spent_usd: null, limit_usd: 1, reserved_usd: null});
  assert.match(html, /Não medido/);
  assert.match(html, /Reservas: não informadas/);
  assert.doesNotMatch(html, /NaN|undefined/);
});

test('connection testing presents its separate configurable paid-call budget', () => {
  const html = studio().settings({connection_test_budget_usd: 0.025});
  assert.match(html, /name="connection_test_budget_usd"[^>]*value="0.025"/);
  assert.match(html, /chamada cobrável/);
  assert.match(html, /separado dos artigos/);
});

test('article history links to its authenticated execution cost report without another request', () => {
  const html = studio().history([], 'article/a');
  assert.match(html, /href="\/api\/jobs\/article%2Fa\/cost-report"/);
  assert.match(html, /Relatório de custos por execução/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test('editorial team keeps entirely unknown audio or tool tokens unmeasured', async () => {
  const html = await studio().team([{kind: 'audio', duration_seconds: 60}]);
  assert.match(html, /Tokens não informados/);
  assert.doesNotMatch(html, /0 tokens registrados|NaN/);
});

test('editorial team labels partial token totals as a lower bound', async () => {
  const html = await studio().team([{input_tokens: 10, output_tokens: 5}, {input_tokens: null, output_tokens: 2}]);
  assert.match(html, /Pelo menos 17 tokens/);
  assert.match(html, /1 registro\(s\) sem contagem completa/);
});
