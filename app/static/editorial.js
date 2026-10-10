'use strict';
const sectorNames = {apuration:'Apuração e pauta',writing:'Redação e voz',seo:'SEO',quality:'Qualidade final'};
const changeLabels = {pending:'Sugestão opcional',applied:'Aplicada',rejected:'Rejeitada',undone:'Desfeita',unchanged:'Texto preservado',invalid:'Proposta não aplicada'};
const intelligenceResults = new Map();
const intelligenceRequests = new Map();
let intelligenceSequence = 0;

function intelligenceResultHtml(result,articleHash){
  if(!result)return '<p class="subtext">Analise a clareza do texto salvo ou solicite sugestões com um orçamento definido por você.</p>';
  const report=result.report||result.last_result||result;
  const previous=articleHash&&report.article_hash&&report.article_hash!==articleHash;
  const status=previous?'previous_version':['running','pending'].includes(result.status)?result.status:report.status||result.status;
  const labels={no_change:'Texto preservado: nenhuma melhoria foi indicada.',running:'Análise em andamento. Consulte o resultado novamente.',pending:'Análise em andamento. Consulte o resultado novamente.',previous_version:'Esta análise pertence a uma versão anterior do artigo.',completed:'Análise concluída.',suggested:'Sugestões disponíveis no histórico abaixo.',applied:'Melhoria aplicada.',unavailable:'A análise não foi concluída; o artigo salvo continua disponível.',failed:'A análise não foi concluída; o artigo salvo continua disponível.',conflict:'O artigo mudou; a versão atual foi preservada.'};
  const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
  const costs=report.costs||{},costLines=[];
  if(finite(costs.calculated_usd))costLines.push(`Custo calculado desta análise: ${usd(costs.calculated_usd)}.`);
  else if(finite(costs.known_calculated_usd))costLines.push(`Custo calculado conhecido: ${usd(costs.known_calculated_usd)}; o total não foi medido.`);
  else costLines.push('Custo desta análise: não medido.');
  if(finite(costs.spent_usd))costLines.push(`Valor contabilizado: ${usd(costs.spent_usd)}.`);
  if(finite(costs.reserved_usd)&&costs.reserved_usd>0)costLines.push(`Valor reservado ou ainda sem confirmação: ${usd(costs.reserved_usd)}.`);
  const limitations=Array.isArray(report.limitations)?report.limitations.filter(value=>typeof value==='string'):[];
  const rejections=Array.isArray(report.rejections)?report.rejections.filter(value=>typeof value?.reason==='string'):[];
  return `<div class="info-box spaced"><strong>${esc(labels[status]||'Resultado da análise opcional')}</strong>${report.summary?`<p>${esc(report.summary)}</p>`:''}${report.notice?`<p>${esc(report.notice)}</p>`:''}<p class="hint">${esc(costLines.join(' '))} Os valores calculados e contabilizados não são a fatura do provedor.</p>${limitations.map(value=>`<p class="hint">${esc(value)}</p>`).join('')}${rejections.map(value=>`<p class="hint">Sugestão preservada sem aplicação: ${esc(value.reason)}</p>`).join('')}<p class="hint">O artigo salvo continua disponível para exportação. Aplicar uma sugestão é opcional.</p></div>`;
}

function intelligencePanel(job,working){
  if(!job.article)return '';
  const saved=intelligenceResults.get(job.id),result=saved?.articleHash===job.article_hash?saved.result:null;
  const running=['running','pending'].includes((result?.report||result?.last_result||result)?.status);
  const disabled=working||!job.article_hash||running;
  return `<section class="panel spaced"><div class="panel-header"><h2>Clareza e conhecimento complementar</h2></div><div class="panel-body"><p class="subtext">Revise o artigo completo e busque explicações que ajudem o leitor. O texto pode ser preservado quando já estiver claro.</p><div class="button-row spaced"><button class="btn small" id="intelligence-analyze" ${disabled?'disabled':''}>Analisar clareza</button><button class="btn small" id="intelligence-load" ${!job.article_hash?'disabled':''}>Consultar última análise</button></div><p class="hint">Analisar clareza usa verificações locais, sem chamadas de IA paga. Não altera o artigo.</p><details class="spaced"><summary>Gerar sugestões com IA</summary><form id="intelligence-form"><label class="spaced">Orçamento desta análise em US$<input type="number" name="budget_usd" min="0" step="any" required placeholder="Defina o valor autorizado"></label><p class="hint">A geração de sugestões pode consumir IA paga. Este limite também respeita o orçamento total disponível do artigo. Você pode conservar o texto se não houver benefício.</p><label class="check-row spaced"><input type="checkbox" name="allow_external" id="intelligence-allow-external"><span>Permitir consulta às fontes complementares informadas abaixo</span></label><fieldset id="intelligence-external" class="editorial-fields" disabled><label class="spaced">Páginas de referência<textarea name="external_urls" rows="3" placeholder="Uma URL pública HTTPS por linha"></textarea></label><label class="spaced">Domínios que você considera confiáveis<input name="trusted_domains" placeholder="exemplo.org, instituicao.gov.br"></label><p class="hint">Informe fontes pertinentes ao tema. As informações complementares serão identificadas separadamente das falas do vídeo.</p></fieldset><button class="btn primary spaced" type="submit" ${disabled?'disabled':''}>Gerar sugestões</button></form></details><div id="intelligence-result" role="status" aria-live="polite">${intelligenceResultHtml(result,job.article_hash)}</div></div></section>`;
}

function intelligenceRequestBody(form,articleHash){
  const fields=new FormData(form),raw=String(fields.get('budget_usd')??'').trim();
  if(!/^(?:\d+(?:[.,]\d+)?|[.,]\d+)$/.test(raw))throw new Error('Informe um orçamento positivo para gerar sugestões.');
  const budget=Number(raw.replace(',','.'));
  if(!Number.isFinite(budget)||budget<=0)throw new Error('Informe um orçamento positivo para gerar sugestões.');
  const allowExternal=fields.has('allow_external');
  const urls=allowExternal?[...new Set(String(fields.get('external_urls')||'').split(/\r?\n/).map(value=>value.trim()).filter(Boolean))]:[];
  const domains=allowExternal?[...new Set(String(fields.get('trusted_domains')||'').split(/[\s,;]+/).map(value=>value.trim().toLowerCase()).filter(Boolean))]:[];
  if(allowExternal&&(!urls.length||!domains.length))throw new Error('Informe as páginas HTTPS e os domínios confiáveis para consultar fontes complementares.');
  if(urls.some(value=>!/^https:\/\/[^\s/@]+(?:\/[^\s]*)?$/.test(value)))throw new Error('Use URLs públicas HTTPS, sem usuário ou senha, para as fontes complementares.');
  if(domains.some(value=>!/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/.test(value)))throw new Error('Informe somente os nomes dos domínios confiáveis, sem caminho ou protocolo.');
  return {article_hash:articleHash,mode:'suggest',budget_usd:budget,allow_external:allowExternal,external_urls:urls,trusted_domains:domains};
}

function bindIntelligence(job,identity){
  if(!job.article)return;
  const articleHash=job.article_hash;
  const current=()=>sameView(identity)&&state.job.article_hash===articleHash;
  const request=async(button,body)=>{
    if(!current()||intelligenceRequests.has(job.id))return;
    if(state.dirty){toast('Salve suas alterações antes de analisar a versão atual.',true);return;}
    const sequence=++intelligenceSequence;
    intelligenceRequests.set(job.id,sequence);
    await busy(button,async()=>{
      try{
        const result=await api(`/jobs/${encodeURIComponent(job.id)}/editorial-intelligence`,body?'POST':'GET',body);
        if(!current()||intelligenceRequests.get(job.id)!==sequence)return;
        const report=result.report||result.last_result;
        intelligenceResults.set(job.id,{articleHash,result});
        $('#intelligence-result').innerHTML=intelligenceResultHtml(result,articleHash);
        const running=['running','pending'].includes(result.status||report?.status);
        $('#intelligence-analyze').disabled=running;
        $('#intelligence-form button[type="submit"]').disabled=running;
        if(body?.mode==='suggest'&&Array.isArray(result.changes)&&result.changes.length){
          await refreshJob();
          if(current())await teamTab();
        }
      }catch(error){
        if(!current())return;
        $('#intelligence-result').innerHTML=`<div class="info-box spaced"><p>${esc(error.message)}</p><p class="hint">A análise opcional não foi concluída. O artigo salvo continua disponível para exportação.</p></div>`;
      }finally{
        if(intelligenceRequests.get(job.id)===sequence)intelligenceRequests.delete(job.id);
      }
    });
    if(current()){
      const saved=intelligenceResults.get(job.id)?.result,report=saved?.report||saved?.last_result;
      const running=['running','pending'].includes(saved?.status||report?.status);
      $('#intelligence-analyze').disabled=running;
      $('#intelligence-form button[type="submit"]').disabled=running;
    }
  };
  $('#intelligence-analyze').onclick=()=>request($('#intelligence-analyze'),{article_hash:articleHash,mode:'shadow'});
  $('#intelligence-load').onclick=()=>request($('#intelligence-load'));
  $('#intelligence-allow-external').onchange=event=>{$('#intelligence-external').disabled=!event.target.checked;};
  $('#intelligence-form').onsubmit=event=>{
    event.preventDefault();
    if(!current()||intelligenceRequests.has(job.id))return;
    try{return request(event.submitter,intelligenceRequestBody(event.target,articleHash));}
    catch(error){if(current())toast(error.message,true);}
  };
}

async function editorialPage(){
  const navigation=state.navigationEpoch,sequence=++state.readSequence,current=()=>state.view==='editorial'&&state.navigationEpoch===navigation&&state.readSequence===sequence;
  try{
    const [profile,bundle]=await Promise.all([api('/editorial/profile'),api('/knowledge')]);
    if(!current())return;
    const p=profile.profile;
    $('#content').innerHTML=`<div class="page-heading"><div><div class="eyebrow">DIREÇÃO COMPARTILHADA</div><h1>Uma redação. A mesma voz.</h1><p class="subtext">O conteúdo e a voz vêm dos vídeos: extração, pauta, redação e revisão factual. O perfil orienta os próximos artigos.</p></div></div>
    <form id="voice-form" class="panel"><div class="panel-header"><h2>Perfil editorial</h2><span class="pill">Versão ${esc(profile.version.slice(0,8))}</span></div><div class="panel-body">
    <div class="field-row">${[['tone','Tom'],['address','Como tratar o leitor']].map(([k,l])=>`<div class="field"><label for="voice-${k}">${l}</label><textarea id="voice-${k}" name="${k}" rows="3" maxlength="${k==='tone'?1000:500}">${esc(p[k])}</textarea></div>`).join('')}</div>
    ${[['vocabulary','Vocabulário',1500],['rhythm','Ritmo e transições',1500],['avoid','Construções a evitar',2000],['exceptions','Precisão e exceções',2000],['approved_examples','Exemplos aprovados da sua escrita',6000]].map(([k,l,m])=>`<div class="field"><label for="voice-${k}">${l}</label><textarea id="voice-${k}" name="${k}" rows="3" maxlength="${m}">${esc(p[k])}</textarea></div>`).join('')}
    <p class="hint">O nome e a descrição geral da marca continuam em Integrações e marca. Cada ciclo guarda uma cópia do perfil utilizado.</p>
    <div class="divider-line"></div><input type="hidden" name="auto_apply" value="${p.auto_apply?'1':'0'}">
    <input type="hidden" name="auto_write" value="1"><p class="hint spaced">Criar artigo executa o fluxo completo automaticamente. Planejar sem redigir continua disponível como ação opcional no artigo.</p>
    <div class="field-row spaced"><input type="hidden" name="research_tool_calls" value="${p.research_tool_calls}"><div class="field"><label>Limite de contexto em caracteres<input type="number" name="context_chars" min="30000" max="240000" value="${p.context_chars}"></label><p class="hint">Configure conforme o modelo utilizado; o limite inclui instruções e materiais enviados. Não é uma medição exata de tokens.</p></div></div>
    <div class="field"><label>Tamanho alvo dos blocos em caracteres<input type="number" name="block_chars" min="3000" max="12000" value="${p.block_chars}"></label><p class="hint">A divisão preserva os trechos originais e usa contexto das partes vizinhas.</p></div>
    <div class="field-row spaced"><div class="field"><label for="max-spend">Teto total por artigo em US$</label><input id="max-spend" type="number" name="max_spend_usd" min="0.01" step="0.01" required value="${p.max_spend_usd}"><p class="hint">Defina o orçamento autorizado para este artigo. O sistema busca gastar o mínimo necessário. O gasto é acumulado no artigo, incluindo pesquisas anteriores, imagens e retentativas; retomar ou gerar outra versão conserva esse gasto.</p></div><div class="field"><label for="max-calls">Limite de segurança de chamadas por ciclo</label><input id="max-calls" type="number" name="max_calls" min="4" max="24" value="${p.max_calls}"><p class="hint">Até 24 chamadas para evitar repetições sem fim. O orçamento em dólares é verificado antes de cada chamada. SEO é verificado localmente.</p></div></div>
    <input type="hidden" name="max_rounds" value="0"><p class="hint spaced">A revisão interna tem execução limitada. O artigo salvo continua disponível mesmo quando houver recomendações ou a análise não concluir.</p>
    </div><div class="form-actions"><p>Salvar o perfil não chama a OpenAI nem altera artigos existentes.</p><button class="btn primary" type="submit">${icon('save')}Salvar perfil</button></div></form>
    <section class="panel spaced"><div class="panel-header"><div><h2>Biblioteca de SEO dos agentes</h2><p class="subtext">${bundle.rules.length} orientações · versão ${esc(bundle.version)} · revisão em ${esc(bundle.reviewed_at)}</p></div></div><div class="panel-body"><p class="subtext">${esc(bundle.mode)} ${esc(bundle.update_policy)}</p><div class="field spaced"><label for="knowledge-query">Buscar uma orientação</label><input id="knowledge-query" type="search" placeholder="Título, transições, palavra-chave…"></div><div id="knowledge-cards"></div></div></section>`;
    const showRules=()=>{
      const q=$('#knowledge-query').value.toLocaleLowerCase();
      const rules=bundle.rules.filter(r=>JSON.stringify(r).toLocaleLowerCase().includes(q));
      $('#knowledge-cards').innerHTML=rules.map(r=>`<details class="knowledge-rule"><summary><strong>${esc(r.title)}</strong><span>${esc(r.publisher)} · ${r.origin==='brand_preference'?'Escolha editorial':r.origin==='plugin_guidance'?'Orientação do plugin':'Documentação oficial'}</span></summary><p>${esc(r.guidance)}</p><p><strong>Aplicação:</strong> ${esc(r.application)}</p><p><strong>Contexto e exceções:</strong> ${esc(r.exceptions)}</p><p><strong>Exemplo:</strong> ${esc(r.example)}</p><p><strong>Conferência:</strong> ${esc(r.evaluation)}</p>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Consultar documentação ${icon('external')}</a>`:''}</details>`).join('')||'<p class="subtext">Nenhuma orientação encontrada.</p>';
    };showRules();$('#knowledge-query').oninput=showRules;
    const form=$('#voice-form'),edits=formEdits();edits.watch(form);
    form.onsubmit=e=>{e.preventDefault();const submitted=edits.begin(form);if(!submitted)return;busy(e.submitter,async()=>{try{
      const f=new FormData(form),body=Object.fromEntries(f);body.auto_apply=f.get('auto_apply')==='1';body.auto_write=true;
      for(const key of ['research_tool_calls','context_chars','block_chars','max_calls','max_spend_usd','max_rounds'])body[key]=Number(body[key]);
      const saved=await api('/editorial/profile','PUT',{base_version:profile.version,profile:body});if(!current())return;
      if(saved.version)profile.version=saved.version;
      if(!edits.acknowledge(submitted)){toast('Perfil enviado salvo. Suas alterações posteriores continuam neste formulário.');return;}
      await editorialPage();toast('Perfil salvo para os próximos ciclos.');
    }finally{edits.finish(submitted);}});};
  }catch(e){if(current())toast(e.message,true);}
}

function findingsHtml(findings){
  return (findings||[]).map(f=>`<div class="team-finding"><strong>${esc(f.reason)}</strong>${f.passage?`<blockquote>${esc(f.passage)}</blockquote>`:''}<p>${esc(f.suggestion)}</p><small>${esc((f.rule_ids||[]).join(' · '))}</small></div>`).join('');
}

async function teamTab(){
  const job=state.job,jobId=job.id,identity=viewIdentity(jobId),epoch=state.readEpoch;
  try{
    const report=await api(`/jobs/${jobId}/team`);
    if(!sameView(identity)||epoch!==state.readEpoch)return;
    const cycle=report.cycle,working=activeStates.has(state.job.status);
    const runs=report.runs.filter(r=>r.cycle_id===cycle?.cycle_id);
    const current=Object.fromEntries([...runs].reverse().map(r=>[r.role,r]));
    const tokens=usageTotals(runs.flatMap(r=>Array.isArray(r.data.usage)?r.data.usage:[]));
    $('#detail-body').innerHTML=`<section class="panel"><div class="panel-header"><div><h2>Sua equipe editorial</h2><p class="subtext">Apuração, escrita, SEO e revisão com responsabilidades próprias.</p></div>${job.article&&!working?'<button class="btn primary" data-action="optimize">'+icon('spark')+'Melhorar este artigo</button>':''}</div><div class="panel-body">
    ${!cycle?'<div class="info-box">Este artigo ainda não passou pela nova equipe. A próxima geração usa os quatro setores; Melhorar este artigo trabalha sobre o texto existente.</div>':''}
    ${cycle?.stale?`<div class="info-box warning">${esc(cycle.stale_reason)} Os pareceres anteriores permanecem no histórico.</div>`:''}
    <div class="team-grid spaced">${Object.entries(sectorNames).map(([sector,label])=>`<section class="team-sector"><h3>${label}</h3>${report.roster.filter(r=>r.sector===sector).map(r=>{const run=current[r.id];return `<div class="team-role"><span>${esc(r.name)}</span><small class="${run?.status==='completed'?'complete':''}">${run?.status==='completed'?'Concluído':run?.status==='failed'?'Interrompido':working&&cycle?.current_role===r.id?'Trabalhando':run?.status==='running'?'Interrompido':cycle?.mode==='review'&&r.sector!=='quality'||cycle?.mode==='optimize'&&r.sector==='apuration'?'Fora deste ciclo':'Aguardando'}</small></div>`;}).join('')}</section>`).join('')}</div>
    ${cycle?`<p class="usage">${cycle.calls} chamada(s) neste ciclo · ${esc(tokensText(tokens))} · Perfil ${esc(cycle.profile.version.slice(0,8))} · Documentação ${esc(cycle.knowledge_version)}</p>`:''}
    ${spendingHtml(report.spending||job.spending)}
    ${cycle?.decision?`<div class="info-box spaced"><strong>Parecer do editor-chefe${cycle.stale?' — versão anterior':''}</strong><p>${esc(cycle.decision.summary)}</p></div>`:''}
    <p class="hint spaced">A qualidade é avaliada por fidelidade, leitura, voz e SEO. Os pareceres e as propostas são consultivos; o artigo salvo continua disponível para exportação.</p></div></section>
    ${intelligencePanel(job,working)}
    <section class="panel spaced"><div class="panel-header"><h2>Histórico de melhorias e sugestões opcionais</h2></div>${report.changes.length?report.changes.map(row=>{const c=row.data;return `<details class="finding change-card"><summary><strong>${esc(changeLabels[c.status]||c.status)}</strong> · ${esc(c.summary)}</summary>${c.error?`<p>${esc(c.error)}</p>`:''}${c.changes.map(change=>`<div class="change-pair"><p><strong>${esc(change.field)}</strong> · ${esc(change.reason)}</p><div class="change-columns"><div><small>Antes</small><pre>${esc(change.before||'(vazio)')}</pre></div><div><small>Depois</small><pre>${esc(change.after||'(removido)')}</pre></div></div><small>${esc((change.rule_ids||[]).join(' · '))}</small></div>`).join('')}<div class="button-row spaced">${!working&&c.status==='pending'?`<button class="btn small" data-change="${esc(c.id)}" data-decision="apply">Aplicar proposta</button><button class="btn small" data-change="${esc(c.id)}" data-decision="reject">Rejeitar</button>`:''}${!working&&c.status==='applied'&&c.result_hash===state.job.article_hash?`<button class="btn small" data-change="${esc(c.id)}" data-decision="undo">Desfazer alteração</button>`:''}</div></details>`;}).join(''):'<div class="panel-body subtext">As melhorias e sugestões aparecerão aqui. Aplicar ou dispensar uma proposta manualmente é opcional. Um agente também pode preservar um texto que já está claro.</div>'}</section>
    <section class="panel spaced"><div class="panel-header"><h2>Entregas e pareceres dos agentes</h2></div>${runs.map(r=>`<details class="finding"><summary>${esc(r.data.name)} · ${r.status==='completed'?'Concluído':r.status==='failed'?'Interrompido':'Em andamento'} · ${time(r.created_at)}</summary><p>${esc(r.data.output?.summary||r.data.output?.title||'A entrega ainda não foi concluída.')}</p>${findingsHtml(r.data.output?.findings)}<p class="hint">Orientações consultadas: ${esc((r.data.rule_ids||[]).join(' · '))}</p></details>`).join('')||'<div class="panel-body subtext">Nenhuma execução registrada neste ciclo.</div>'}</section>
    <section class="panel spaced"><div class="panel-header"><h2>Comunicação entre setores</h2><p class="hint">Exibindo as 35 mensagens mais recentes do ciclo. ${report.totals?.messages??report.messages.length} registros no histórico.</p></div>${report.messages.filter(m=>m.cycle_id===cycle?.cycle_id).slice(0,35).map(m=>`<div class="finding"><p><strong>${esc(report.roster.find(r=>r.id===m.data.sender)?.name||m.data.sender)}</strong> → ${esc(sectorNames[m.data.recipient]||'Coordenação')}</p><p>${esc(m.data.summary||m.data.finding?.reason||'Entrega registrada.')}</p></div>`).join('')||'<div class="panel-body subtext">Os pedidos e decisões aparecem durante o trabalho.</div>'}</section>`;
    document.querySelectorAll('[data-change]').forEach(button=>{button.onclick=()=>busy(button,async()=>{if(state.dirty)throw new Error('Salve suas alterações primeiro.');await api(`/jobs/${jobId}/changes/${button.dataset.change}`,'POST',{article_hash:job.article_hash,action:button.dataset.decision});if(!sameView(identity))return;await refreshJob();toast('Decisão registrada. A versão salva continua disponível para exportação.');});});
    bindIntelligence(job,identity);
  }catch(e){if(sameView(identity)&&epoch===state.readEpoch)toast(e.message,true);}
}
