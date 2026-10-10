'use strict';

function imagePositions(selected='start') {
  const options=[['start','No início do artigo'],...(state.job.image_positions||[]).map(h=>[h.id,'Após o título: '+h.title]),['end','No final do artigo']];
  if(!options.some(([id])=>id===selected))options.push([selected,'Seção alterada — imagem no final']);
  return options.map(([id,label])=>`<option value="${esc(id)}" ${id===selected?'selected':''}>${esc(label)}</option>`).join('');
}

function referenceLinks(references) {
  return (references||[]).map(r=>`<a href="${esc(r.page_url)}" target="_blank" rel="noopener noreferrer">${esc(r.author)} · ${r.provider==='pexels'?'Pexels':'Pixabay'}</a>`).join(' · ');
}

function referenceResults(result) {
  if(!result||result.expires_at*1000<=Date.now())return '<p class="hint">Busque referências para conferir as fotos e escolher até três. A busca automática também pode escolher por você.</p>';
  return `${(result.warnings||[]).map(w=>`<p class="hint">${esc(w)}</p>`).join('')}<div class="reference-grid">${result.items.map(r=>`<div class="reference-card"><label><img src="${esc(r.preview_url)}" alt="${esc(r.description)}" loading="lazy" referrerpolicy="no-referrer"><span><input type="checkbox" name="reference_ids" value="${esc(r.id)}"> Usar como referência</span></label><p class="hint">${referenceLinks([r])}</p></div>`).join('')}</div>`;
}

function imagesTab() {
  const j=state.job,identity=viewIdentity(j.id),edits=formEdits();
  if(!j.article){$('#detail-body').innerHTML=noArticle();return;}
  const working=activeStates.has(j.status)||j.image_busy, images=j.images||[], task=j.image_tasks?.at(-1);
  const banks=state.settings.pexels_api_key_configured||state.settings.pixabay_api_key_configured;
  const query=j.image_reference_results?.query||j.brief.keyword||j.brief.topic||j.article.title;
  $('#detail-body').innerHTML=`<section class="panel"><div class="panel-header"><div><h2>Imagens para o seu artigo</h2><p class="subtext">Crie um banner com referências visuais e composição pensada para desktop e celular.</p></div><span class="pill">1280 × 420 · WebP</span></div><form class="panel-body" id="image-generation-form"><fieldset class="editorial-fields" ${working?'disabled':''}>
  <div class="field"><label for="image-reference-mode">Referências visuais</label><select id="image-reference-mode" name="reference_mode"><option value="auto" ${banks?'selected':''}>Buscar e escolher automaticamente</option><option value="selected">Usar as referências selecionadas</option><option value="none" ${banks?'':'selected'}>Gerar sem referências</option></select><p class="hint">A IA recebe as imagens por URL para orientar luz, textura e composição. As fotos dos bancos não entram no acervo do artigo.</p></div>
  ${!banks?'<div class="info-box spaced">Conecte Pexels ou Pixabay em <a href="#settings">Integrações</a> para buscar referências visuais.</div>':''}
  <div class="field spaced"><label for="image-query">Tema da busca</label><div class="reference-search"><input id="image-query" name="reference_query" maxlength="100" value="${esc(query.slice(0,100))}" placeholder="Ex.: broto de manjericão"><button type="button" class="btn small" id="search-image-references" ${banks?'':'disabled'}>${icon('search')}Buscar referências</button></div><p class="hint">Use um assunto curto e específico. Fotos de <a href="https://www.pexels.com" target="_blank" rel="noopener noreferrer">Pexels</a> e <a href="https://pixabay.com" target="_blank" rel="noopener noreferrer">Pixabay</a>.</p></div>
  <div id="image-reference-results">${referenceResults(j.image_reference_results)}</div>
  <div class="field spaced"><label for="image-prompt">Direção visual <span class="optional">opcional</span></label><textarea id="image-prompt" name="prompt" maxlength="3000" rows="3" placeholder="Ex.: Um broto saudável em destaque, luz natural suave e fundo limpo."></textarea><p class="hint">Descreva o assunto principal e o ambiente. A composição mantém os detalhes essenciais no centro para recortes no celular.</p></div>
  <div class="image-form-grid"><div class="field"><label for="image-style">Estilo</label><select id="image-style" name="style"><option value="photo">Fotográfico</option><option value="illustration">Ilustração editorial</option></select></div><div class="field"><label for="image-size">Entrega</label><input id="image-size" value="1280 × 420 px · WebP" readonly><input type="hidden" name="size" value="1280x420"><p class="hint">Compressão WebP sem perda adicional. O peso depende dos detalhes da cena.</p></div><div class="field"><label for="image-quality">Qualidade da geração</label><select id="image-quality" name="quality"><option value="high" selected>Alta</option><option value="medium">Média</option><option value="low">Econômica</option></select></div></div>
  <div class="field"><label for="image-position">Posição no artigo</label><select id="image-position" name="position">${imagePositions()}</select></div><label class="check-row"><input type="checkbox" name="featured" ${images.length?'':'checked'}><span>Usar também como imagem de destaque</span></label><div class="form-actions image-form-actions"><p>Uma imagem por solicitação. Usa ${esc(state.settings.image_model||'gpt-image-2')} e gera cobrança na sua conta OpenAI. Qualidades maiores podem custar mais.</p><button class="btn primary" type="submit">${icon('spark')}Gerar imagem com IA</button></div></fieldset></form></section>
  ${j.image_busy?'<div class="info-box spaced"><span class="loader"></span> Criando sua imagem. Você pode acompanhar por aqui; a geração continua no servidor.</div>':''}
  ${task?.error?`<div class="info-box warning spaced">${esc(task.error)} Não repetimos automaticamente a geração da imagem.</div>`:''}
  ${(task?.reference_warnings||[]).map(w=>`<p class="hint spaced">${esc(w)}</p>`).join('')}
  <div class="image-gallery spaced">${images.map(m=>`<section class="panel image-card"><div class="image-preview"><img src="${esc(m.url)}" alt="${esc(m.alt)}" loading="lazy"></div><div class="image-mobile-check"><details><summary>Conferir no celular</summary><p class="hint">Banner reduzido a 375 px e simulação de recorte central 16:9. O recorte do seu site depende do tema.</p><div class="mobile-banner-preview"><img src="${esc(m.url)}" alt="${esc(m.alt)}" loading="lazy"></div><div class="mobile-crop-preview"><img src="${esc(m.url)}" alt="${esc(m.alt)}" loading="lazy"></div></details>${m.references?.length?`<p class="hint spaced">Referências visuais: ${referenceLinks(m.references)}</p>`:''}</div><form class="panel-body" data-image-form="${esc(m.id)}"><fieldset class="editorial-fields" ${working?'disabled':''}><div class="image-info"><span class="pill">${m.featured?'Destaque':m.origin==='ai'?'Criada com IA':'Imagem'}</span><span class="hint">${m.width} × ${m.height} · ${Math.round(m.bytes/1024)} KB · WebP${m.compression==='lossless'?' sem perda':''}</span></div>${m.position_missing?'<div class="info-box warning spaced">A seção escolhida mudou. A imagem aparece no final; escolha uma nova posição.</div>':''}<div class="field spaced"><label for="alt-${m.id}">Texto alternativo</label><input id="alt-${m.id}" name="alt" maxlength="500" value="${esc(m.alt)}" placeholder="Descreva o que aparece na imagem"><p class="hint">Confira a imagem e descreva o que é relevante para o leitor, sem repetir palavras-chave.</p></div><div class="field"><label for="caption-${m.id}">Legenda</label><input id="caption-${m.id}" name="caption" maxlength="1000" value="${esc(m.caption)}"></div><div class="field"><label for="credit-${m.id}">Créditos</label><input id="credit-${m.id}" name="credit" maxlength="300" value="${esc(m.credit)}"></div><div class="field"><label for="position-${m.id}">Posição</label><select id="position-${m.id}" name="position">${imagePositions(m.position)}</select></div><label class="check-row"><input type="checkbox" name="in_body" ${m.in_body?'checked':''}><span>Exibir no corpo do artigo</span></label><label class="check-row spaced"><input type="checkbox" name="featured" ${m.featured?'checked':''}><span>Usar como destaque no WordPress</span></label><div class="button-row spaced"><button type="submit" class="btn primary small">Salvar imagem</button><a class="btn small" href="${esc(m.url)}" download="imagem-${m.id.slice(0,8)}.webp">Baixar</a><button type="button" class="btn ghost small" data-remove-image="${esc(m.id)}">Remover</button></div></fieldset></form></section>`).join('')}</div>${images.length?'<button class="btn spaced" data-action="preview">'+icon('eye')+'Ver artigo com imagens</button>':''}`;
  const form=$('#image-generation-form');
  edits.watch(form,()=>{delete form.dataset.requestId;});
  $('#image-reference-results').onchange=e=>{
    if(e.target.name!=='reference_ids')return;
    if(form.querySelectorAll('[name="reference_ids"]:checked').length>3){e.target.checked=false;toast('Escolha até três referências.',true);return;}
    $('#image-reference-mode').value='selected';
  };
  $('#search-image-references').onclick=e=>busy(e.currentTarget,async()=>{
    const result=await api(`/jobs/${j.id}/images/references`,'POST',{query:$('#image-query').value});
    if(!sameView(identity))return;
    j.image_reference_results=result;$('#image-reference-results').innerHTML=referenceResults(result);
    $('#image-reference-mode').value='auto';edits.mark(form);delete form.dataset.requestId;
    toast(result.items.length?`${result.items.length} referências encontradas.`:'Confira o resultado da busca.');
  });
  form.onsubmit=e=>{e.preventDefault();const submitted=edits.begin(form);if(!submitted)return;busy(e.submitter,async()=>{try{
    const fields=new FormData(form),body=Object.fromEntries(fields);
    form.dataset.requestId ||= crypto.randomUUID();body.request_id=form.dataset.requestId;body.featured=fields.has('featured');
    body.reference_ids=body.reference_mode==='selected'?fields.getAll('reference_ids'):[];
    await api(`/jobs/${j.id}/images/generate`,'POST',body);if(!sameView(identity))return;
    if(!edits.acknowledge(submitted)){await refreshJob();toast('Geração iniciada. As alterações ainda não enviadas continuam nos formulários.');return;}
    await refreshJob();toast('Geração de imagem iniciada.');
  }finally{edits.finish(submitted);}});};
  document.querySelectorAll('[data-image-form]').forEach(form=>{
    edits.watch(form);
    form.onsubmit=e=>{e.preventDefault();const submitted=edits.begin(form);if(!submitted)return;busy(e.submitter,async()=>{try{
      const fields=new FormData(form),body=Object.fromEntries(fields);body.in_body=fields.has('in_body');body.featured=fields.has('featured');
      await api(`/jobs/${j.id}/images/${form.dataset.imageForm}`,'PUT',body);if(!sameView(identity))return;
      if(!edits.acknowledge(submitted)){await refreshJob();toast('Imagem enviada salva. As alterações ainda não enviadas continuam nos formulários.');return;}
      await refreshJob();toast('Imagem atualizada no artigo.');
    }finally{edits.finish(submitted);}});};
  });
  document.querySelectorAll('[data-remove-image]').forEach(button=>{button.onclick=()=>busy(button,async()=>{
    if(state.dirty)throw new Error('Salve suas alterações antes de remover uma imagem.');
    if(!confirm('Remover esta imagem do artigo? Imagens já enviadas ao WordPress continuam na biblioteca do site.'))return;
    await api(`/jobs/${j.id}/images/${button.dataset.removeImage}`,'DELETE');if(!sameView(identity))return;await refreshJob();toast('Imagem removida.');
  });});
}

function exportTab() {
  const j=state.job;
  if(!j.article){$('#detail-body').innerHTML=noArticle();return;}
  const working=activeStates.has(j.status)||j.image_busy;
  const available=exportAvailable(j),connected=Boolean(state.settings.wp_url&&state.settings.wp_user&&state.settings.wp_password_configured);
  const canSend=available&&!working&&connected;
  $('#detail-body').innerHTML=`<div class="settings-grid"><section class="panel"><div class="panel-header"><h2>Exportar o artigo salvo</h2></div><div class="panel-body"><p class="subtext">Exporte a versão salva com imagens, destaque, tags e campos do Yoast SEO. As observações editoriais são opcionais.</p>${!available?`<div class="info-box warning spaced">${esc(j.export_error||'O artigo precisa de título, slug e conteúdo válidos para exportação.')}</div>`:''}<button class="btn primary full spaced" data-export="wordpress" ${available?'':'disabled'}>${icon('download')}Baixar XML WordPress</button><ol class="export-instructions"><li>No WordPress, abra <strong>Ferramentas → Importar → WordPress</strong>.</li><li>Selecione o XML e escolha o autor do artigo.</li><li>Marque <strong>baixar e importar anexos</strong> para levar as imagens.</li><li>Confira a postagem pendente no editor e salve para atualizar o Yoast.</li></ol><p class="hint">As imagens do XML ficam disponíveis para importação por 7 dias. Depois desse prazo, baixe um novo XML. O app precisa estar online durante a importação. O XML cria conteúdo; para atualizar a mesma postagem, use o envio direto ao lado.</p><div class="export-options">${[['html','HTML com imagens'],['wordpress-html','Blocos WordPress'],['markdown','Markdown do texto'],['json','Pacote SEO + fontes']].map(([f,l])=>`<button class="btn small" data-export="${f}" ${available?'':'disabled'}>${icon('download')}${l}</button>`).join('')}</div><p class="hint">HTML com imagens funciona como arquivo independente. Blocos WordPress usa imagens já enviadas ao site. Markdown contém apenas o texto.</p></div></section><section class="panel"><div class="panel-header"><h2>Enviar ao WordPress</h2><span class="pill">Postagem pendente</span></div><div class="panel-body"><p class="subtext">O app envia a versão salva e as imagens à biblioteca de mídia, com legenda, texto alternativo e destaque. A publicação fica sob seu controle no WordPress.</p>${j.wordpress?.id?`<div class="info-box spaced">Postagem #${j.wordpress.id} disponível no WordPress. <a href="${esc(j.wordpress.edit_url)}" target="_blank" rel="noopener noreferrer">Abrir no WordPress</a></div>`:''}<div class="info-box spaced">O envio direto leva texto e imagens. Os campos do Yoast vão no XML ou no pacote SEO; confira-os ao usar o envio direto.</div>${working?'<div class="info-box spaced">O envio direto fica disponível assim que o processamento em andamento terminar. Você já pode baixar a versão salva disponível.</div>':''}${!connected?'<div class="info-box warning spaced">Configure o endereço, o usuário e a senha de aplicativo do site em <a href="#settings">Integrações</a>.</div>':''}<p class="hint spaced">O clique envia a versão salva como postagem pendente. Sugestões editoriais não exigem aprovação individual.</p><button class="btn primary full spaced" data-action="wordpress" ${canSend?'':'disabled'}>${icon('globe')}${j.wordpress?.id?'Atualizar postagem pendente':'Enviar ao WordPress'}</button></div></section></div>`;
  document.querySelectorAll('[data-export]').forEach(button=>{button.onclick=()=>busy(button,async()=>{
    const response=await fetch(`/api/jobs/${j.id}/export?format=${button.dataset.export}`);
    if(!response.ok){const error=await response.json();throw new Error(error.detail||'Não foi possível exportar.');}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    link.href=url;link.download=response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]||'artigo';
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });});
}
