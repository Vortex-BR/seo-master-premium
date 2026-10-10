'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const article = {title:'Título',seo_title:'Título SEO',slug:'titulo',meta_description:'Descrição',excerpt:'Resumo',tags:['tema'],markdown:'Conteúdo salvo.'};
const job = (id, hash = 'base', updated = hash) => ({id,article_hash:hash,article:{...article},updated_at:updated,status:'ready',brief:{urls:[],topic:''},sources:[],checks:[],usage:[],events:[]});
const response = (data, status = 200) => ({ok:status >= 200 && status < 300,status,json:async()=>data});

// The real handlers run against an isolated DOM/network boundary. Promises are
// deliberately completed out of order to reproduce browser races offline.
function studio(fetcher) {
  const nodes = new Map(), handlers = {}, rendered = [], downloads = [], links = [], intervals = [], collections = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      innerHTML:'',textContent:'',className:'',value:'',dataset:{},isConnected:true,
      fields:Object.entries({...article,tags:'tema'}),
      insertAdjacentHTML(_position, html) { this.innerHTML += html; },
      querySelector:selector=>node(selector),
      querySelectorAll:()=>[],
    });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document:{querySelector:node,querySelectorAll:selector=>collections.get(selector)||[],addEventListener:(event, handler)=>{handlers[event]=handler;},createElement:()=>{const link={click(){},remove(){}};links.push(link);return link;},body:{append(){}}},
    window:{addEventListener(){}},location:{hash:'#article/a'},history:{replaceState(){}},
    fetch:fetcher,FormData:class {constructor(form){this.fields=form.fields;}[Symbol.iterator](){return this.fields[Symbol.iterator]();}get(name){return this.fields.find(([key])=>key===name)?.[1]??null;}has(name){return this.fields.some(([key])=>key===name);}getAll(name){return this.fields.filter(([key])=>key===name).map(([,value])=>value);}},
    setTimeout:()=>1,clearTimeout(){},setInterval:callback=>{intervals.push(callback);return intervals.length;},clearInterval(){},
    confirm:()=>true,URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:local';},revokeObjectURL(){}},Blob,
    structuredClone,Date,rendered,crypto:require('node:crypto').webcrypto,editorialPage:()=>{},
  });
  let script = fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
  script = script.replace(/\nboot\(\);\s*$/, '\n');
  vm.runInContext(script,context);
  vm.runInContext(`const originalBusy=busy;busy=(...args)=>globalThis.lastOperation=originalBusy(...args);shell=()=>{};detail=()=>rendered.push(state.job.id);`,context);
  const run = code => vm.runInContext(code,context);
  const select = fixture => {context.fixture=fixture;run("state.view='detail';state.tab='article';state.job=fixture;");};
  const editor = fixture => {select(fixture);run('articleTab();');return node('#article-form');};
  const load=name=>vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8'),context);
  return {context,run,node,handlers,rendered,downloads,links,intervals,collections,select,editor,load};
}

function submit(ui,form){form.onsubmit({preventDefault(){},target:form,submitter:ui.node('save')});return ui.context.lastOperation;}

test('late polling for a different job cannot replace the current article',async()=>{
  const pending = deferred(),ui = studio(()=>pending.promise);
  ui.select(job('a'));const read=ui.run('refreshJob()');
  ui.select(job('b'));pending.resolve(response(job('a','old')));await read;
  assert.equal(ui.run('state.job.id'),'b');assert.deepEqual(ui.rendered,[]);
});

test('polling responses arriving out of order keep the newest request',async()=>{
  const first=deferred(),second=deferred(),queue=[first,second],ui=studio(()=>queue.shift().promise);
  ui.select(job('a'));const read1=ui.run('refreshJob()'),read2=ui.run('refreshJob()');
  second.resolve(response(job('a','new')));await read2;
  first.resolve(response(job('a','old')));await read1;
  assert.equal(ui.run('state.job.article_hash'),'new');
});

test('a mutation invalidates an already pending article read',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='GET'?pending.promise:Promise.resolve(response({})));
  ui.select(job('a'));const read=ui.run('refreshJob()');
  await ui.run("api('/jobs/a/article','PUT',{base_article_hash:'base',markdown:'Novo'})");
  ui.select(job('a','saved'));pending.resolve(response(job('a','old')));await read;
  assert.equal(ui.run('state.job.article_hash'),'saved');
});

test('slow navigation cannot render a job after a newer navigation finished',async()=>{
  const a=deferred(),b=deferred(),ui=studio(url=>url.endsWith('/a')?a.promise:b.promise);
  const navA=ui.run('navigate()');ui.context.location.hash='#article/b';const navB=ui.run('navigate()');
  b.resolve(response(job('b')));await navB;a.resolve(response(job('a')));await navA;
  assert.equal(ui.run('state.job.id'),'b');assert.deepEqual(ui.rendered,['b']);
});

test('saving sends the hash of the rendered editor even when polling changed state',async()=>{
  const calls=[],ui=studio((url,options)=>{calls.push({url,...options});return Promise.resolve(response(options.method==='PUT'?{}:job('a','remote')));});
  const form=ui.editor(job('a','opened'));form.oninput();await ui.run('refreshJob()');
  form.onsubmit({preventDefault(){},target:form,submitter:ui.node('save')});await ui.context.lastOperation;
  assert.equal(JSON.parse(calls.find(c=>c.method==='PUT').body).base_article_hash,'opened');
});

test('polling preserves a dirty editor instead of rendering a remote revision',async()=>{
  const ui=studio(()=>Promise.resolve(response(job('a','remote'))));
  const form=ui.editor(job('a'));form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Texto ainda local':value]);form.oninput();
  await ui.run('refreshJob()');
  assert.equal(ui.run('state.job.article_hash'),'remote');assert.equal(ui.run('state.dirty'),true);
  assert.deepEqual(ui.rendered,[]);assert.equal(form.fields.find(([key])=>key==='markdown')[1],'Texto ainda local');
});

test('late polling cannot update a different studio tab',async()=>{
  const pending=deferred(),ui=studio(()=>pending.promise);
  ui.select(job('a'));const read=ui.run('refreshJob()');
  await ui.handlers.click({target:{closest:selector=>selector==='[data-tab]'?{dataset:{tab:'sources'}}:null}});
  pending.resolve(response(job('a','late')));await read;
  assert.equal(ui.run('state.tab'),'sources');assert.equal(ui.run('state.job.article_hash'),'base');
});

test('an obsolete polling failure does not report an error in another job',async()=>{
  const pending=deferred(),ui=studio(()=>pending.promise);
  ui.select(job('a'));const read=ui.run('refreshJob()');ui.select(job('b'));
  pending.reject(new Error('A consulta antiga falhou'));assert.equal(await read,false);
  assert.equal(ui.run('state.job.id'),'b');assert.equal(ui.node('#toast').textContent,'');
});

test('polling pauses while a mutation is pending and resumes after it completes',async()=>{
  const pending=deferred(),calls=[],ui=studio((_url,options)=>{calls.push(options.method);return options.method==='PUT'?pending.promise:Promise.resolve(response(job('a','saved')));});
  ui.select(job('a'));const write=ui.run("api('/jobs/a/article','PUT',{base_article_hash:'base'})");
  assert.equal(await ui.run('refreshJob()'),false);assert.deepEqual(calls,['PUT']);
  pending.resolve(response({ok:true,article_hash:'saved'}));await write;assert.equal(await ui.run('refreshJob()'),true);
  assert.deepEqual(calls,['PUT','GET']);
});

test('a double submit cannot send two writes from the same editor',async()=>{
  const pending=deferred(),calls=[],ui=studio((_url,options)=>{calls.push(options.method);return options.method==='PUT'?pending.promise:Promise.resolve(response(job('a','saved')));});
  const form=ui.editor(job('a'));form.oninput();const first=submit(ui,form);submit(ui,form);
  assert.deepEqual(calls,['PUT']);pending.resolve(response({ok:true,article_hash:'saved'}));await first;
  assert.deepEqual(calls,['PUT','GET']);
});

test('a failed save preserves the editor and can retry with the same base revision',async()=>{
  let attempt=0;const writes=[],ui=studio((_url,options)=>{
    if(options.method==='GET')return Promise.resolve(response(job('a','saved')));
    writes.push(JSON.parse(options.body));return Promise.resolve(++attempt===1?response({detail:'Indisponível'},503):response({ok:true,article_hash:'saved'}));
  });
  const form=ui.editor(job('a'));form.oninput();await submit(ui,form);
  assert.equal(ui.run('state.dirty'),true);assert.match(ui.node('#save-status').textContent,/Sua edição permanece/);
  await submit(ui,form);assert.deepEqual(writes.map(write=>write.base_article_hash),['base','base']);assert.equal(ui.run('state.dirty'),false);
});

test('continuing to type after a save uses the acknowledged hash for the next write',async()=>{
  const pending=deferred(),writes=[],ui=studio((_url,options)=>{
    if(options.method==='GET')return Promise.resolve(response(job('a','saved2')));
    writes.push(JSON.parse(options.body));return writes.length===1?pending.promise:Promise.resolve(response({ok:true,article_hash:'saved2'}));
  });
  const form=ui.editor(job('a'));form.oninput();const first=submit(ui,form);
  form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Continuação':value]);form.oninput();
  pending.resolve(response({ok:true,article_hash:'saved1'}));await first;await submit(ui,form);
  assert.deepEqual(writes.map(write=>write.base_article_hash),['base','saved1']);assert.equal(writes[1].markdown,'Continuação');
});

test('a completed save in a previous job does not clear another job draft',async()=>{
  const pending=deferred(),ui=studio(()=>pending.promise);
  const firstForm=ui.editor(job('a'));firstForm.oninput();const write=submit(ui,firstForm);
  const nextForm=ui.editor(job('b'));nextForm.oninput();pending.resolve(response({ok:true,article_hash:'saved'}));await write;
  assert.equal(ui.run('state.job.id'),'b');assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('draft recovery downloads all current fields without sending them to the server',async()=>{
  let calls=0;const ui=studio(()=>{calls++;return Promise.resolve(response({detail:'Conflito'},409));});
  const form=ui.editor(job('a','opened'));form.oninput();await submit(ui,form);
  form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Texto depois do conflito':value]);form.oninput();
  ui.node('#download-local-article').onclick();const downloaded=JSON.parse(await ui.downloads[0].text());
  assert.equal(downloaded.markdown,'Texto depois do conflito');assert.equal(downloaded.base_article_hash,'opened');
  assert.deepEqual(downloaded.tags,['tema']);assert.equal(downloaded.slug,'titulo');assert.equal(calls,1);
  assert.match(ui.node('#save-status').innerHTML,/Baixar minha edição/);assert.match(ui.links[0].download,/\.json$/);
});

test('declining a remote reload keeps the local draft without making another request',async()=>{
  let calls=0;const ui=studio(()=>{calls++;return Promise.resolve(response({detail:'Conflito'},409));});
  const form=ui.editor(job('a'));form.oninput();await submit(ui,form);ui.context.confirm=()=>false;
  await ui.node('#reload-saved-article').onclick({currentTarget:ui.node('reload')});
  assert.equal(calls,1);assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('typing while a confirmed remote reload is pending cancels replacement',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='PUT'?Promise.resolve(response({detail:'Conflito'},409)):pending.promise);
  const form=ui.editor(job('a'));form.oninput();await submit(ui,form);
  const reload=ui.node('#reload-saved-article').onclick({currentTarget:ui.node('reload')});
  form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Texto digitado durante consulta':value]);form.oninput();
  pending.resolve(response(job('a','remote')));await reload;
  assert.equal(ui.run('state.dirty'),true);assert.equal(ui.run('state.job.article_hash'),'base');assert.deepEqual(ui.rendered,[]);
});

test('two independent editors keep the first remote save and the second local draft',async()=>{
  let remote=job('a'),writes=0;
  const server=(_url,options)=>{
    if(options.method==='GET')return Promise.resolve(response(structuredClone(remote)));
    const body=JSON.parse(options.body);
    if(body.base_article_hash!==remote.article_hash)return Promise.resolve(response({detail:'O artigo foi alterado em outra aba.'},409));
    remote={...remote,article_hash:'revision-'+(++writes),updated_at:'revision-'+writes,article:{...remote.article,...body}};
    return Promise.resolve(response({ok:true,article_hash:remote.article_hash}));
  };
  const first=studio(server),second=studio(server),formA=first.editor(structuredClone(remote)),formB=second.editor(structuredClone(remote));
  formA.fields=formA.fields.map(([key,value])=>[key,key==='markdown'?'Versão da primeira aba':value]);formA.oninput();await submit(first,formA);
  formB.fields=formB.fields.map(([key,value])=>[key,key==='markdown'?'Versão da segunda aba':value]);formB.oninput();await submit(second,formB);
  assert.equal(writes,1);assert.equal(remote.article.markdown,'Versão da primeira aba');assert.equal(second.run('state.dirty'),true);
  assert.equal(formB.fields.find(([key])=>key==='markdown')[1],'Versão da segunda aba');assert.match(second.node('#save-status').innerHTML,/Carregar versão salva/);
});

test('a nullable content length stays optional in the production briefing form',()=>{
  const ui=studio(()=>Promise.reject(new Error('No network expected')));ui.context.fixture={target_words:null};
  const html=ui.run('directionFields(fixture)');assert.match(html,/name="target_words"[^>]*value=""/);
  assert.doesNotMatch(html.match(/<input[^>]+name="target_words"[^>]*>/)[0],/required/);
});

test('scheduled polling waits for its pending read instead of starving on slow responses',async()=>{
  const pending=deferred();let calls=0;
  const ui=studio(()=>Promise.resolve(++calls===1?response({...job('a'),status:'writing'}):pending.promise));
  await ui.run('navigate()');const read=ui.intervals[0]();ui.intervals[0]();ui.intervals[0]();
  assert.equal(calls,2);pending.resolve(response({...job('a','progress'),status:'writing'}));await read;
  assert.equal(ui.run('state.job.article_hash'),'progress');await ui.intervals[0]();assert.equal(calls,3);
});

test('the two new article buttons cannot create parallel jobs from the same form',async()=>{
  const pending=deferred();let calls=0;const ui=studio(()=>{calls++;return pending.promise;});
  ui.run("state.view='new';newArticle();");const form=ui.node('#new-form');form.fields.push(['urls','https://www.youtube.com/watch?v=abcdefghijk'],['target_words','']);
  const generate={innerHTML:'Criar artigo',value:'generate',isConnected:true},extract={innerHTML:'Extrair fontes',value:'extract',isConnected:true};
  form.onsubmit({preventDefault(){},target:form,submitter:generate});const first=ui.context.lastOperation;
  form.onsubmit({preventDefault(){},target:form,submitter:extract});assert.equal(calls,1);
  pending.resolve(response({id:'created'}));await first;assert.equal(ui.context.location.hash,'#article/created');
});

test('an article conflict keeps typed text and offers explicit draft recovery',async()=>{
  const ui=studio(()=>Promise.resolve(response({detail:'O artigo foi alterado em outra aba.'},409)));
  const form=ui.editor(job('a'));form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Meu rascunho local':value]);form.oninput();
  form.onsubmit({preventDefault(){},target:form,submitter:ui.node('save')});await ui.context.lastOperation;
  assert.equal(ui.run('state.dirty'),true);
  assert.equal(form.fields.find(([key])=>key==='markdown')[1],'Meu rascunho local');
  assert.match(ui.node('#save-status').innerHTML,/Baixar minha edição/);
  assert.match(ui.node('#save-status').innerHTML,/Carregar versão salva/);
});

test('typing during a successful save preserves the additional unsaved changes',async()=>{
  const saved=deferred(),ui=studio((_url,options)=>options.method==='PUT'?saved.promise:Promise.resolve(response(job('a','saved'))));
  const form=ui.editor(job('a'));form.oninput();
  form.onsubmit({preventDefault(){},target:form,submitter:ui.node('save')});
  form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Continuação ainda local':value]);form.oninput();
  saved.resolve(response(job('a','saved')));await ui.context.lastOperation;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
  assert.equal(form.fields.find(([key])=>key==='markdown')[1],'Continuação ainda local');
});

test('a failed generation request cannot redraw over an article typed while waiting',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='POST'?pending.promise:Promise.resolve(response(job('a'))));
  ui.select(job('a'));const button={dataset:{action:'generate'},innerHTML:'Gerar',isConnected:true};
  const write=ui.handlers.click({target:{closest:selector=>selector==='[data-action]'?button:null}});
  ui.rendered.length=0;const form=ui.editor(ui.run('state.job'));
  form.fields=form.fields.map(([key,value])=>[key,key==='markdown'?'Rascunho digitado durante a espera':value]);form.oninput();
  pending.resolve(response({detail:'Fila cheia'},429));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('saving a briefing preserves typing made after its submission',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='PUT'?pending.promise:Promise.resolve(response(job('a','updated'))));
  ui.select(job('a'));ui.run('briefTab()');const form=ui.node('#brief-form');form.fields=[['topic','Enviado'],['target_words','']];form.oninput();
  const write=submit(ui,form);form.fields=[['topic','Ainda local'],['target_words','']];form.oninput();
  pending.resolve(response({ok:true}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('saving one source preserves another source draft on the same screen',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='POST'?pending.promise:Promise.resolve(response(job('a','updated'))));
  ui.load('transcription.js');ui.load('planning.js');const fixture=job('a');fixture.sources=[{id:'v1',video_id:'abcdefghijk',url:'https://www.youtube.com/watch?v=abcdefghijk',status:'ok',segments:[]}];ui.select(fixture);
  const first=ui.node('source-one'),second=ui.node('source-two');first.dataset.source='abcdefghijk';second.dataset.source='lmnopqrstuv';
  ui.collections.set('form[data-source]',[first,second]);ui.run('sourcesTab()');
  first.fields=[['text','Primeira fonte']];second.fields=[['text','Rascunho da segunda fonte']];first.oninput();second.oninput();
  const write=submit(ui,first);pending.resolve(response({ok:true}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('saving a source preserves further typing in that source',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='POST'?pending.promise:Promise.resolve(response(job('a','updated'))));
  ui.load('transcription.js');ui.select(job('a'));const form=ui.node('source-one');form.dataset.source='abcdefghijk';ui.collections.set('form[data-source]',[form]);ui.run('sourcesTab()');
  form.fields=[['text','Transcrição enviada']];form.oninput();const write=submit(ui,form);
  form.fields=[['text','Mais texto depois do submit']];form.oninput();pending.resolve(response({ok:true}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('a profile save acknowledges its version without discarding later typing',async()=>{
  const pending=deferred(),writes=[];let saves=0;
  const ui=studio((url,options)=>{
    if(options.method==='PUT'){writes.push(JSON.parse(options.body));return ++saves===1?pending.promise:Promise.resolve(response({version:'second-profile'}));}
    return Promise.resolve(response(url.endsWith('/knowledge')?{rules:[]}:{version:'base-profile',profile:{}}));
  });
  ui.load('editorial.js');ui.run("state.view='editorial'");await ui.run('editorialPage()');
  const form=ui.node('#voice-form');form.fields=[['tone','Tom enviado']];form.oninput();const write=submit(ui,form);
  form.fields=[['tone','Tom ainda local']];form.oninput();pending.resolve(response({version:'saved-profile'}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
  await submit(ui,form);assert.deepEqual(writes.map(body=>body.base_version),['base-profile','saved-profile']);
});

test('a plan save acknowledges its version without discarding later typing',async()=>{
  const pending=deferred(),writes=[];let saves=0;
  const ui=studio((_url,options)=>{
    if(options.method==='PUT'){writes.push(JSON.parse(options.body));return ++saves===1?pending.promise:Promise.resolve(response({version:'second-plan'}));}
    return Promise.resolve(response({available:false,notice:'Local'}));
  });
  ui.load('planning.js');const fixture=job('a');fixture.plan={valid:true,version:'base-plan',data:{title:'Plano',main_question:'Pergunta?',opening:'Início',closing:'Fim',sections:[],dispositions:[],pending:[]}};
  ui.select(fixture);await ui.run('planningTab()');const form=ui.node('#plan-form');form.fields=[['title','Enviado']];form.oninput();const write=submit(ui,form);
  form.fields=[['title','Título ainda local']];form.oninput();pending.resolve(response({version:'saved-plan'}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
  await submit(ui,form);assert.deepEqual(writes.map(body=>body.base_version),['base-plan','saved-plan']);
});

test('saving image details preserves a different image draft and later typing',async()=>{
  const pending=deferred(),ui=studio((_url,options)=>options.method==='PUT'?pending.promise:Promise.resolve(response(job('a','updated'))));
  ui.load('publishing.js');ui.select(job('a'));const first=ui.node('image-one'),second=ui.node('image-two');first.dataset.imageForm='one';second.dataset.imageForm='two';
  ui.collections.set('[data-image-form]',[first,second]);ui.run('imagesTab()');
  first.fields=[['alt','Texto enviado']];second.fields=[['alt','Outro rascunho']];first.oninput();second.oninput();const write=submit(ui,first);
  first.fields=[['alt','Texto depois do submit']];first.oninput();pending.resolve(response({ok:true}));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);
});

test('typing during image submission keeps the draft and still observes image progress',async()=>{
  const pending=deferred(),updated={...job('a','updated'),image_busy:true};
  const ui=studio((_url,options)=>options.method==='POST'?pending.promise:Promise.resolve(response(updated)));
  ui.load('publishing.js');ui.select(job('a'));ui.run('imagesTab()');const form=ui.node('#image-generation-form');
  form.fields=[['prompt','Enviado'],['reference_mode','none']];form.oninput();const write=submit(ui,form);
  form.fields=[['prompt','Outra direção ainda local'],['reference_mode','none']];form.oninput();pending.resolve(response({ok:true},202));await write;
  assert.equal(ui.run('state.dirty'),true);assert.deepEqual(ui.rendered,[]);assert.equal(ui.run('state.job.image_busy'),true);
});

test('a failed source save can retry without clearing its draft or leaving the submit blocked',async()=>{
  let attempts=0;const ui=studio((_url,options)=>options.method==='POST'?Promise.resolve(++attempts===1?response({detail:'Temporariamente indisponível'},503):response({ok:true})):Promise.resolve(response(job('a','updated'))));
  ui.load('transcription.js');ui.select(job('a'));const form=ui.node('source-one');form.dataset.source='abcdefghijk';ui.collections.set('form[data-source]',[form]);ui.run('sourcesTab()');
  form.fields=[['text','Transcrição local']];form.oninput();await submit(ui,form);assert.equal(ui.run('state.dirty'),true);
  await submit(ui,form);assert.equal(attempts,2);assert.equal(ui.run('state.dirty'),false);
});
