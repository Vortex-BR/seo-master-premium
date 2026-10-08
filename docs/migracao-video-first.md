# Migração Video-First

Implementação do plano `plano-migracao-video-first-codex.md`, em 8 de outubro de 2026.

O artigo deriva exclusivamente da explicação dos vídeos. A pesquisa esclarece termos já mencionados e não fornece conteúdo ou evidências. O fluxo ativo tem quatro entregas: extração do raciocínio, pauta, artigo completo e conferência factual.

## Checklist implementado

- [x] Pesquisa com `internal_context_only=True`, recebida somente em `agent_background_knowledge`. Páginas e notas `rn` são excluídas das fontes citáveis, mesmo quando material antigo estava marcado como verificado.
- [x] Política compartilhada exige conteúdo, didática, exemplos e voz dos vídeos. Métodos diferentes permanecem como alternativas atribuídas.
- [x] `clean_spoken_transcript` remove CTAs, vinhetas e preenchimento sem alterar a transcrição original, os IDs ou os timestamps. Explicações, analogias, relatos e alertas são preservados.
- [x] `SpokenInsight` registra explicação, dicas práticas, analogias, alertas e trechos de origem. A extração recebe todos os vídeos em uma chamada, sem comparação ou checagem por bloco.
- [x] Pauta única seleciona e organiza apenas os insights dos vídeos. Lacunas visuais e da transcrição chegam à pauta e à redação; pesquisa não as transforma em fatos.
- [x] `composition.write` redige o artigo inteiro, com crédito ao criador e referências temporais. Contexto excessivo é recusado antes da chamada; não há fragmentação por seção ou reparos editoriais automáticos.
- [x] Somente `extractor`, `planner`, `writer` e `fact_reviewer` participam dos novos ciclos. A revisão factual avalia todos os trechos e metadados em uma entrega independente, contra a transcrição original.
- [x] SEO determinístico verifica H2/H3, título, metadescrição, frequência do termo, crédito na introdução, clichês de IA e timestamps. Citações web e falhas estritas permanecem bloqueadas mesmo após aprovação do modelo.
- [x] Perfis antigos são normalizados para o teto de oito chamadas e zero rodadas adicionais. A retomada não aumenta esse teto nem reescreve um rascunho já entregue.

## Orçamento e recuperação

Sem pesquisa ou recuperação, um artigo novo usa quatro chamadas de modelo. A pesquisa opcional usa uma busca e uma extração de termos, reservando até duas chamadas de ferramenta no mesmo teto de oito. O orçamento registra essa reserva conservadora mesmo se a ferramenta usar menos solicitações. Retentativas também contam no teto.

A pesquisa só começa se houver saldo para preservar pauta, redação e revisão. Falhas esperadas ou ausência de termos pertinentes dispensam a pesquisa e ficam visíveis no painel. Não há ciclos de pesquisa, conciliação, edição de voz ou otimização SEO por modelos.

Respostas incompletas ou referências inválidas podem ter uma recuperação por etapa, dentro do saldo. Entregas concluídas, transcrições e rascunhos pagos ficam persistidos. Erros de contexto são identificados antes do provedor, sem consumo de chamadas. Após um bloqueio factual, o usuário pode editar o artigo, ajustar a pauta ou conferir a fonte e executar uma nova revisão explícita.

## Compatibilidade

Artigos e propostas anteriores permanecem acessíveis no histórico. Novos ciclos usam sempre Video-First, inclusive quando a instalação ainda tem `EDITORIAL_FLOW=legacy` ou `EDITORIAL_COMPOSITION=legacy`. Um ciclo anterior à migração não retoma os agentes antigos; as fontes e o texto existente são preservados enquanto o novo fluxo prepara sua entrega.

A interface, a estimativa, os perfis e o script de avaliação usam os novos limites. O reconhecimento de áudio e a obtenção de fontes mantêm seu funcionamento existente. Informações presentes somente nas imagens ainda exigem conferência humana.

## Validação

Os testes usam provedores simulados. Cobrem preservação da fala e da autoria, isolamento da pesquisa, contratos com IDs permitidos, quatro papéis, orçamento com ferramentas e retentativas, revisão independente, referências inválidas, lacunas visuais, edição de pauta, rascunhos, recuperação e exportação. Nenhum teste desta migração exige chamadas pagas.

Resultado da validação em 8 de outubro de 2026: **458 testes passaram**. Também passaram a compilação Python, a sintaxe dos dois arquivos JavaScript e `git diff --check`. O script de avaliação preparou sete casos em modo simulado, sem executar modelos ou serviços pagos.

```powershell
.venv/Scripts/python -m pytest -q
node --check app/static/app.js
node --check app/static/editorial.js
.venv/Scripts/python scripts/evaluate_editorial.py --output .local/editorial-evaluation
```
