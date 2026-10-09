'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// Exercise the real UI functions with a small DOM boundary, without an API,
// browser dependency or credentials. Each scenario receives isolated state.
function studio() {
  const nodes = new Map(), handlers = {}, calls = [], messages = [];
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      innerHTML: '', value: '',
      insertAdjacentHTML(_position, html) { this.innerHTML += html; },
    });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {
      querySelector: node,
      querySelectorAll: () => [],
      addEventListener: (event, handler) => { handlers[event] = handler; },
    },
    window: {addEventListener() {}},
    setTimeout, clearTimeout, Date, calls, messages,
  });
  for (const filename of ['app.js', 'editorial.js', 'planning.js', 'publishing.js']) {
    const sourcePath = path.join(__dirname, '..', 'app', 'static', filename);
    // Loading app.js normally starts authentication. Test only the UI boundary.
    const source = fs.readFileSync(sourcePath, 'utf8').replace(/\nboot\(\);\s*$/, '');
    vm.runInContext(source, context, {filename: sourcePath});
  }
  const settings = {wp_url: 'https://example.org', wp_user: 'editor', wp_password_configured: true};
  function render(job, configuration = settings) {
    context.fixtureJob = job;
    context.fixtureSettings = configuration;
    vm.runInContext('state.job=fixtureJob;state.settings=fixtureSettings;exportTab();', context);
    return node('#detail-body').innerHTML;
  }
  async function sendWordPress(dirty = false) {
    context.fixtureDirty = dirty;
    vm.runInContext(`
      state.dirty=fixtureDirty;
      api=async (...args)=>{calls.push(args);return {};};
      refreshJob=async()=>{};
      toast=(...args)=>{messages.push(args);};
    `, context);
    const button = {dataset: {action: 'wordpress'}, innerHTML: 'Enviar', isConnected: true};
    await handlers.click({target: {closest: selector => selector === '[data-action]' ? button : null}});
  }
  return {context, render, node, calls, messages, sendWordPress};
}

const article = {
  title: 'Artigo conciso', slug: 'artigo-conciso',
  markdown: 'Uma resposta objetiva, apoiada nas fontes.',
};
const job = {id: 'saved', status: 'ready', article, article_hash: 'current', review: null};
function button(html, attribute) {
  const found = html.match(new RegExp('<button[^>]*' + attribute + '[^>]*>'));
  assert.ok(found, `Missing button ${attribute}`);
  return found[0];
}
function downloadsEnabled(html, enabled = true) {
  for (const format of ['wordpress', 'html', 'wordpress-html', 'markdown', 'json']) {
    assert.equal(button(html, `data-export="${format}"`).includes('disabled'), !enabled, format);
  }
}
function sendEnabled(html, enabled = true) {
  assert.equal(button(html, 'data-action="wordpress"').includes('disabled'), !enabled);
}

test('a short saved article can be exported and sent without review', () => {
  const html = studio().render(job);
  downloadsEnabled(html);
  sendEnabled(html);
  assert.ok(!html.includes('editorial-approval'));
});

test('legacy findings, stale review and a changed direction do not block delivery', () => {
  const html = studio().render({...job, status: 'needs_review', article_needs_generation: true,
    review: {article_hash: 'previous', findings: [{severity: 'blocking', reason: 'Poderia ser maior'}]}});
  downloadsEnabled(html);
  sendEnabled(html);
});

test('an analysis failure preserves delivery of the saved version', () => {
  const ui = studio();
  const html = ui.render({...job, status: 'error', error: 'Análise indisponível',
    export_available: true, processing_state: 'completed', editorial_state: 'uncertainties',
    delivery_state: 'article_available'});
  downloadsEnabled(html);
  sendEnabled(html);
  const notice = vm.runInContext('deliveryNotice(state.job)', ui.context);
  assert.match(notice, /Artigo pronto para exportação/);
  assert.match(notice, /Processamento: Concluído/);
  assert.match(notice, /Com incertezas identificadas/);
});

for (const [name, fields] of [
  ['generation', {status: 'writing'}],
  ['analysis', {status: 'reviewing'}],
  ['media mutation', {image_busy: true}],
]) {
  test(`${name} prevents concurrent WordPress mutation and allows saved downloads`, () => {
    const html = studio().render({...job, ...fields, export_available: true});
    downloadsEnabled(html);
    sendEnabled(html, false);
    assert.match(html, /processamento em andamento terminar/);
  });
}

test('a technical delivery error disables export and shows its reason', () => {
  const html = studio().render({...job, export_available: false, export_error: 'Conteúdo inválido'});
  downloadsEnabled(html, false);
  sendEnabled(html, false);
  assert.match(html, /Conteúdo inválido/);
});

test('the compatibility fallback rejects an empty article', () => {
  const html = studio().render({...job, article: {...article, markdown: '   '}});
  downloadsEnabled(html, false);
  sendEnabled(html, false);
});

test('missing WordPress credentials preserve local export', () => {
  const html = studio().render(job,
    {wp_url: 'https://example.org', wp_user: 'editor', wp_password_configured: false});
  downloadsEnabled(html);
  sendEnabled(html, false);
  assert.match(html, /senha de aplicativo/);
});

test('partial delivery is presented as a saved draft', () => {
  const ui = studio();
  ui.render({...job, delivery_state: 'partial_draft', export_available: true});
  assert.match(vm.runInContext('jobPill(state.job)', ui.context), /Rascunho disponível/);
  assert.match(vm.runInContext('deliveryNotice(state.job)', ui.context), /Rascunho parcial/);
});

test('editorial and factual findings are optional diagnostics', () => {
  const ui = studio();
  ui.render({...job, review: {
    article_hash: 'current', reviewed_at: '2026-10-09', summary: 'Observações', supported_claims: [],
    findings: [
      {severity: 'blocking', origin: 'model', category: 'recommendation',
        reason: 'Falso positivo', suggestion: 'Expandir', source_ids: []},
      {severity: 'warning', category: 'factual_uncertainty',
        reason: 'Fonte inconclusiva', suggestion: 'Qualificar informação', source_ids: []},
    ],
  }});
  vm.runInContext('reviewTab();', ui.context);
  const html = ui.node('#detail-body').innerHTML;
  assert.match(html, /observação\(ões\) opcional\(is\)/);
  assert.match(html, /Registrar avaliação opcional/);
  assert.match(html, /Informação não confirmada/);
  assert.ok(!html.includes('pill error'));
  assert.ok(!html.includes('pendência(s) para resolver'));
});

test('clicking WordPress sends the saved article without an approval payload', async () => {
  const ui = studio();
  ui.render({...job, status: 'needs_review'});
  await ui.sendWordPress();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0][0], '/jobs/saved/wordpress');
  assert.equal(ui.calls[0][1], 'POST');
  assert.equal(JSON.stringify(ui.calls[0][2]), '{}');
});

test('unsaved edits require saving before sending the selected current version', async () => {
  const ui = studio();
  ui.render(job);
  await ui.sendWordPress(true);
  assert.equal(ui.calls.length, 0);
  assert.match(ui.messages[0][0], /Salve suas alterações/);
});

test('profile offers full generation and keeps planning as an optional action', async () => {
  const ui = studio();
  ui.context.fixtureResponses = {
    '/editorial/profile': {version: 'profile-v1', profile: {auto_write: false}},
    '/knowledge': {rules: [], version: 'knowledge-v1', reviewed_at: '2026-10-09'},
  };
  await vm.runInContext("state.view='editorial';api=async path=>fixtureResponses[path];editorialPage();", ui.context);
  const html = ui.node('#content').innerHTML;
  assert.match(html, /Criar artigo executa o fluxo completo automaticamente/);
  assert.match(html, /Planejar sem redigir continua disponível/);
  assert.ok(!html.includes('name="auto_write" type="checkbox"'));
});
