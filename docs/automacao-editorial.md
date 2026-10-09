# Revisão automática e disponibilidade de entrega

A revisão melhora o artigo internamente. Sua conclusão, suas sugestões e a severidade legada `blocking` não autorizam nem impedem a exportação de conteúdo salvo e tecnicamente válido. O clique de envio ao WordPress expressa a intenção do usuário e envia a versão salva como `pending`.

## Arquitetura

`app/editorial/delivery.py` centraliza a validade técnica do artigo e deriva três dimensões independentes para lista, detalhe e pacote JSON da API:

| Campo | Valores |
| --- | --- |
| `processing_state` | `idle`, `queued`, `processing_sources`, `planning`, `writing`, `optimizing`, `completed`, `awaiting_input`, `technical_error` |
| `editorial_state` | `not_evaluated`, `analyzing`, `clear`, `recommendations`, `uncertainties` |
| `delivery_state` | `unavailable`, `partial_draft`, `article_available`, `technical_unavailable` |
| `export_available` | Validade técnica do artigo salvo, independente da revisão e do status legado |
| `export_error` | Motivo técnico quando não há conteúdo exportável |

Os estados existentes permanecem salvos, sem migração destrutiva. Por exemplo, um artigo antigo `needs_review` pode ser `completed`, com `uncertainties` e `article_available`. Mudanças de direção, fontes ou hash da revisão não tornam inválida a versão salva: somente futuras gerações e propostas dependem dessas versões.

Exportação e envio validam o contrato `Article`, título, slug e corpo não vazios e caracteres de controle incompatíveis. Arquivos de mídia ausentes ou inválidos, credenciais, permissão de acesso e incompatibilidades do WordPress continuam produzindo erros técnicos.

## Geração e revisão

**Criar artigo** executa extração, planejamento, redação e revisão factual sem aprovação de sugestões. A configuração legada `auto_write=False` não interrompe essa ação. **Planejar** permanece uma ação explícita para quem deseja somente o plano.

Uma opinião do planejador de que faltam informações não obriga o usuário a resolver uma pendência quando há conteúdo sustentado selecionado. A redação recebe esse escopo, as fontes e as limitações, com instruções para preservar atribuições, delimitar incertezas e evitar completar lacunas. Ausência real de conhecimento útil continua interrompendo a geração; uma versão anterior salva permanece disponível.

`review_policy.py` classifica apontamentos em `objective`, `recommendation` e `factual_uncertainty`. A categoria e os dados de verificação indicam se o problema foi reproduzido localmente ou é uma observação do modelo. Ter um ID de fonte ou uma avaliação negativa não comprova um erro factual. Todo apontamento recebe `export_blocking=False`; a severidade antiga fica preservada para auditoria.

Antes da revisão factual, o coordenador executa uma única passagem local, sem chamadas de IA. Pode corrigir:

- Cópias adjacentes e literalmente iguais de parágrafos de prosa, mantendo texto e referências.
- Quebras de parágrafo serializadas quando há limites reconhecíveis de frases ou títulos, fora de código.
- Notas sem definição cujo identificador corresponde a uma evidência de vídeo existente, usando o formato de referência correto.

Cada alteração exige reprodução local da correção, trecho literal único, comparação da versão candidata e preservação de números e integridade das referências. Mudanças que acrescentem defeitos são rejeitadas. A versão anterior, o antes/depois, a prova e o resultado permanecem no histórico. Não são aplicadas correções factuais arbitrárias, reescritas por preferência do revisor ou expansões para alcançar tamanho mínimo.

Essa passagem é limitada a uma execução por ciclo, com identificação de entradas equivalentes. `max_rounds=0` continua impedindo reescritas editoriais pagas; `max_calls` e o orçamento financeiro acumulado conservam seus limites. Recuperações de formato e transporte continuam limitadas a uma retentativa por etapa, usando o cache e o consumo persistidos.

Quando o serviço de revisão falha ou o orçamento termina após a redação válida, o artigo é entregue com `review_incomplete=True`. Uma auditoria factual concluída antes de uma falha posterior é recuperada somente quando pertence ao artigo e ciclo atuais. Os diagnósticos incompletos não viram aprovação factual fictícia.

## Histórico e interface

Editar, mudar fontes ou iniciar outro ciclo arquiva a revisão anterior em artefatos imutáveis `review_history`. O artigo e suas versões permanecem no histórico existente. A API conserva os contratos anteriores e acrescenta os campos de disponibilidade.

A interface apresenta artigo disponível ou rascunho parcial, com diagnósticos opcionais em painel secundário. Nenhum checkbox de aprovação, justificativa ou decisão individual é exigido para envio. A revisão manual e as propostas continuam acessíveis, sem impedir o uso da versão existente.

Downloads podem usar o conteúdo salvo durante processamento. O envio direto e outras gravações simultâneas aguardam o processamento de texto ou mídia terminar para evitar perda de dados em gravações concorrentes do mesmo trabalho. Essa restrição é técnica e temporária; após o processamento, apontamentos pendentes ou revisão incompleta não interferem no envio.

## WordPress e integridade

Markdown, HTML, XML/WXR, Gutenberg e o envio REST usam a mesma validação técnica. Imagens, destaque, legendas, alt, referências, tags e metadados dos formatos existentes são preservados.

O envio REST conserva autenticação, verificação de destino, vínculo com o site, proteção de slug, confirmação da postagem existente e intenção durável antes da requisição. Um timeout não autoriza criar outra cópia. Posts publicados ou com alterações externas não são sobrescritos automaticamente; o conteúdo remoto recebe uma impressão para conferência em envios posteriores. Envios antigos reaproveitam a versão vinculada ao histórico para reconstruir essa impressão. Se não houver uma base verificável, a atualização é recusada para preservar o conteúdo remoto. O sistema não muda automaticamente para `publish`.

## Validação

As regressões cobrem artigo conciso, sugestões e falsos positivos, edição sem outra revisão, diagnóstico incompleto por orçamento ou falha de IA, cache e limites de correção, compatibilidade com `needs_review`, conteúdo ausente ou inválido, exportações com mídia, envio pendente, idempotência e conflitos externos.

| Cenário obrigatório do plano | Evidência automatizada |
| --- | --- |
| 1. Artigo curto e adequado | `test_export_availability.py`: todos os formatos com artigo conciso; `test_review_policy.py`: preservação da redação curta |
| 2. Recomendações pendentes | `test_export_availability.py`: envio WordPress sem aprovação e exportação com diferentes estados editoriais |
| 3. Falso positivo | `test_editorial_automation.py`: artigo e contagem de chamadas preservados; `test_review_policy.py`: opinião ou citação do modelo não autoriza edição |
| 4. Edição manual | `test_export_availability.py`: nova versão importada sem outra revisão, com diagnóstico anterior arquivado |
| 5. Limite de correção | `test_review_policy.py`: passagem limitada e cache sem novas versões; `test_budget_flow.py`: entrega após orçamento da revisão terminar |
| 6. Revisão indisponível | `test_editorial_automation.py`: falha de conexão, resposta inválida e falha interna do revisor; recuperação da auditoria e falha do diagnóstico local |
| 7. Falha técnica real | `test_export_availability.py`, `test_publishing.py` e `test_security.py`: conteúdo, arquivos, autenticação e operação inválidos |
| 8. Segurança da importação | `test_export_availability.py`: um post após repetição, reconciliação de timeout e preservação de corpo, título, resumo, slug e destaque externos |
| 9. Artigos antigos | `test_editorial_automation.py` e `test_export_availability.py`: `needs_review` sem migração ou nova geração |
| 10. Preservação da qualidade | `test_review_policy.py`: referências, código, prosa semelhante, histórico, desfazer e rollback; `test_editorial_automation.py`: escopo sustentado sem fabricar respostas |

Foram acrescentados `tests/test_export_availability.py`, `tests/test_review_policy.py`, `tests/test_editorial_automation.py` e `tests/frontend_export.test.cjs`. As expectativas antigas de aprovação obrigatória foram atualizadas em `test_direction.py`, `test_editorial.py`, `test_evidence_workflow.py`, `test_newsroom.py`, `test_composition.py` e `test_publishing.py`. O cenário de edição válida em `test_research_progress.py` agora usa um parágrafo distinto; a correção de cópias adjacentes possui cobertura própria.

Resultado final em 09/10/2026: **621 testes Python aprovados** na versão final, distribuídos em quatro processos com coleta completa e bancos/cache temporários isolados (153 + 158 + 157 + 153). A execução demorou cerca de 3 minutos e 35 segundos. O relatório verificável está em `.local/final-regression-bf48d8d8/result.json`, acompanhado dos logs de cada grupo. **14 testes de interface Node aprovados**; sintaxe dos quatro JavaScripts, compilação Python e `git diff --check` também passaram.

Comandos de verificação:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
node --check app/static/editorial.js
node --check app/static/publishing.js
node --check app/static/planning.js
node --test tests/frontend_export.test.cjs
```

Os testes usam fornecedores e transporte simulados; não consomem chamadas pagas nem escrevem no WordPress de produção. O artigo mencionado no plano não estava acessível pela consulta pública e não existe no banco local, portanto a validação usa cenários equivalentes no ambiente local.

Uma verificação adicional com Playwright/Chromium e banco isolado conferiu as telas reais em desktop e celular, o salvamento via API, downloads Markdown e HTML atualizados e o clique WordPress com `POST {}`. O transporte WordPress foi interceptado nesse navegador; não ocorreram requisições externas ou erros JavaScript. As capturas e `result.json` ficaram em `.local/editorial-export-smoke-634ea076/`.

Arquivos principais: `app/editorial/delivery.py`, `review_policy.py`, `engine.py`, `store.py`, `changes.py`, `contracts.py`, `text_checks.py`, `agents.py`, `workflow.py`, `video_first.py`, `composition.py`; `app/pipeline.py`, `generation.py`, `main.py`, `publishing.py`, `wordpress.py`, `schemas.py`; `app/seo/checks.py` e `rules.json`; `app/static/app.js`, `editorial.js`, `publishing.js`, `planning.js`.

Documentação atualizada: `README.md`, este relatório, `docs/redacao-e-rascunhos.md` e as referências históricas em `especificacao-inicial.md`, `implementacao-redacao.md` e `redacao-multiagentes.md`. A versão dos agentes e do pacote de orientações foi incrementada para distinguir a política atual dos artefatos antigos, mantendo as versões salvas disponíveis.
