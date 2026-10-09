# Evolução SEO Master Premium — entrega da Fase 0

Data: 09/10/2026. A auditoria, a proposta técnica e a captura das fontes reais estão concluídas. O piloto usa **um vídeo principal**, com conteúdo integralmente baseado nele; os outros dois links são testes opcionais independentes. A produção própria e o uso somente para testes foram declarados pelo usuário. As transcrições históricas reais estão preservadas com timestamps. Faltam geração pela versão atual e avaliação; não existe evidência de melhoria editorial nesta entrega.

O [documento de execução do plano](C:/Users/windows/Downloads/paginas/configuracao/Plano_Evolucao_SEO_Master_Premium/06_PROMPT_EXECUCAO_AGENTE.md) determina: **“Executar somente a Fase 0: auditoria do código atualizado, matriz de diferenças e baseline.”** Também exige o baseline autorizado antes de modificar prompts ou algoritmos. Esta entrega preserva esse limite e a implementação anterior de revisão não bloqueante. Não houve alteração de código em `app/` nesta fase.

A orientação posterior do usuário substitui a amostra original de 12 por um piloto com um vídeo. O aplicativo já aceita uma fonte (`Brief.urls`, mínimo 1); não foi criado requisito de quantidade para gerar ou exportar. A diversidade de 12 casos pode ser usada numa avaliação futura, sem impedir o piloto.

## Entregas concretas

| Artefato | Conteúdo |
|---|---|
| [Auditoria de fontes](auditoria-fontes.md) | Percurso de aquisição até revisão; proveniência, timestamps, confiança, locutores e quatro perdas reproduzidas em memória. |
| [Matriz T01–T14](matriz-testes-e-aceite.md) | Testes existentes, alcance de suas asserções e lacunas que exigem conteúdo real e avaliação humana. |
| [Proposta da Human Knowledge Layer](proposta-human-knowledge-layer.md) | Contratos v1, adaptação de dados legados, snapshots literais, implantação por modos, primeiro diff futuro, invariantes, testes e rollback. |
| [Protocolo de baseline](protocolo-baseline.md) | Coleta reproduzível, versão da árvore local, modelo/perfil, custos, tempo, avaliação e comparação futura. |
| [Manifesto da amostra](amostra.json) | Um caso principal obrigatório, dois opcionais, declaração de produção própria, metadados e hashes das transcrições históricas reais. |
| `scripts/premium_baseline.py` | Captura local sem chamadas a provedores, sem alteração de banco e sem métricas/autorizações inventadas. |
| `tests/test_premium_baseline.py` | Integridade da captura, direitos de todas as fontes, ausência de banco, nomes Windows, trechos literais, versão, custos e artigos com fontes alteradas. |

## Versão efetivamente auditada

Workspace: `C:\Users\windows\Desktop\SEO`. HEAD: `f84f53cdad73c7e10754e898aeb6bb508eaa0cfc`. A árvore já continha alterações locais da reforma editorial; elas foram mantidas. O `app.zip` citado pelo pacote não foi tratado como fonte atual.

Fingerprint dos bytes do aplicativo e dependências declaradas:

```text
7acdaf514b79712f0e0230aae896d3cdc03623639c8408d07f0a254df196f8f2
```

A impressão completa está em `.local/premium-baseline/20261009T171746Z-716d4e81/fingerprint.json`. Inclui arquivos locais ainda não commitados; o hash não substitui a conservação da árvore correspondente. O mesmo fingerprint do aplicativo foi confirmado antes e depois das adições desta fase. Versões observadas: agentes `7`, geração editorial `7`, workflow `2`, regras SEO `2026-10-09.internal-review.1`. A versão de um contrato futuro deverá ser explícita, separada de hashes de artefatos e versões do fluxo.

Os 63 arquivos desse fingerprint foram copiados e conferidos em `source-tree/` na mesma pasta, com a ferramenta, seus testes e `environment.json` registrando Python e versões instaladas das dependências. Assim, o baseline conserva os bytes auditados sem depender de um HEAD que omite mudanças locais. A fotografia não inclui credenciais, sessões ou dados do banco.

## Matriz consolidada de diferenças

| Área do plano | Implementação atual | Diferença, impacto e ação proposta |
|---|---|---|
| Revisão interna e exportação | `delivery.describe()` separa processamento, editorial e entrega; pareceres, baixa nota e alterações da pauta não bloqueiam um artigo tecnicamente válido. Há correção local limitada, histórico e recuperação de falha de revisão. | Já implementado. Conservar e usar suas regressões; não recriar aprovação editorial obrigatória. Ver [automação editorial anterior](../automacao-editorial.md). |
| Integridade da transcrição | Origem, idioma, segmentos, alertas e tempos disponíveis são registrados. Inventário editorial preserva caracteres e referências. | Antes do inventário, agregação oculta lacunas/descarta metadados, SRT perde o fim, alerta parcialmente sobreposto pode perder vínculo e remoção de HTML apaga desigualdades. Quatro casos reproduzidos, com evidência na auditoria. Prioridade da Fase 1: representação bruta e normalização conservadora, mantendo IDs antigos. |
| Conhecimento humano | `SpokenExtraction`, `KnowledgeItem`, `SourceRelation` e `source_spoken_insight` já existem. A redação recebe a explicação original, dicas, analogias e alertas. | O adaptador ativo rotula todos os insights como fato/conceito e deixa condições, quantidades e relações vazias. Acrescentar projeção estruturada versionada; não substituir a explicação por um resumo nem concluir que todo artigo perdeu o conteúdo. |
| Natureza das evidências | O servidor valida IDs, proprietário do segmento e citações literais. A revisão final compara significado e cobertura. | `supported` inicial confirma IDs, sem análise semântica. Separar `reference_validated` de `source_supported`; tempo e citação válida não provam verdade nem atribuição correta. |
| Contexto, pauta e composição | Novos ciclos usam o caminho Video-First, texto completo e composição única; `context_packing` substitui cópias por referências de forma reversível. Pesquisa permanece contexto interno. | Não foi encontrada redução silenciosa generalizada que justifique refazer a compactação. A projeção `Dossier` e o mapa de evidências têm menos metadados que a apuração. Propagar vínculos e ressalvas estruturados; complemento externo publicado exigiria política e contratos próprios. |
| Comprimento e naturalidade | Texto salvo curto exporta; recomendações não forçam expansão. O brief ainda define meta mínima de 500 palavras. | Distinguir meta de formulário de critério de qualidade. Medir geração real antes de mudar a composição; não premiar expansão artificial nem impor seções universais. |
| Avaliação de qualidade | Há conferência factual e verificações locais. As fixtures exercitam contratos, autoria transmitida, recuperação e entrega. | Não há baseline humano preenchido na rubrica proposta. O avaliador existente usa falas sintéticas até no `--live`. Não declarar fidelidade/utilidade superiores a partir da suíte de mocks. |
| Entrega WordPress | Markdown, HTML, JSON, Gutenberg e XML; WordPress mantém estado `pending`, reconciliação de timeout e proteção de alterações externas. | Não reimplementar a entrega. Envio real a WordPress não foi executado nesta fase; regressões usam transporte controlado. |
| Operação | Uso por chamada, tempos de agentes e ledger financeiro durável já existem. | Custos são controle calculado, não fatura; custo ausente não é zero. Tempo de agente não é automaticamente latência total. `spending.summary()` escreve no ledger, portanto a auditoria usa SQLite em leitura. |
| Direitos e conteúdo visual | Notices informam que imagens não foram analisadas. Não há contrato de licença/base de uso por fonte no aplicativo. | T14 é lacuna. Registrar direitos no corpus e propor proveniência complementar; não inferir autorização pelo crédito nem implementar bloqueio retroativo de exportação. Multimodal continua fase futura. |
| Documentação | A reforma anterior está descrita em `docs/automacao-editorial.md` e no código atual. | `docs/fluxo-evidencias.md:63`, `:65`, `:67` e `docs/contexto-e-entrega-editorial.md:50`, `:56` ainda descrevem aprovação/bloqueios antigos; o fim do README menciona interrupção do perfil que não se aplica ao comando explícito Gerar. Essas divergências foram registradas para atualização documental, sem tomar o texto antigo como contrato do código. |

## Baseline: resultados medidos e pendências

| Medida observada | Resultado | Alcance |
|---|---|---|
| URLs recebidas | 1 principal obrigatório e 2 testes opcionais | Sem exigência de outros nove vídeos ou categorias para o piloto. |
| Base de uso | Produção própria, links somente para testes | Declaração do usuário já registrada; nenhuma nova autorização solicitada. |
| Metadados públicos | 3 respostas HTTP 200 no YouTube oEmbed | Título e crédito de canal; não certifica locutor, duração, direitos ou conteúdo. |
| Acesso atual ao YouTube | 3 falhas `IpBlocked` em legendas; 3 desafios de acesso no extrator de metadados | Tentativas atuais falharam; nenhuma reconstrução fictícia ou fallback pago. |
| Transcrições reais históricas | Principal: 3 segmentos/2.651 caracteres; opcionais: 22/20.243 e 7/5.800 | Whisper local `small`, idioma `pt`, timestamps presentes em todos os segmentos; origem e hash do histórico preservados. Não é nova medição de reconhecimento. |
| Banco local padrão | Zero jobs, artigos e fontes | Consulta somente leitura a `data/seo.sqlite3`; não descreve implantação remota. |
| Baseline capturado | 3 snapshots de fontes e fichas humanas; `incomplete` quanto à geração atual | Relatório em `.local/premium-baseline/20261009T171746Z-716d4e81/report.json`; só o caso principal é obrigatório. |
| Integração de IA local | Chave ausente no ambiente e no setting do banco padrão | Verificada apenas a presença, sem expor credenciais; impede gerar um artigo novo pela versão atual neste workspace. |
| Chamadas pagas | Zero | Não houve geração por IA nem transcrição paga. As consultas públicas de metadados/legendas foram separadas da ferramenta offline. |
| Custo, latência e notas de artigos reais | Não medidos | Permanecem ausentes; nenhuma afirmação de melhoria. |

As tentativas públicas estão registradas em `.local/premium-source-probe/{video_id}.json` e `{video_id}-extractor.json`. As transcrições vêm de `.local/transcription-0f495aa7-after.json` e foram projetadas, sem alteração dos originais, em `.local/premium-inputs/{video_id}-sources.json`. Cada caso contém somente seu vídeo; o manifesto registra o hash esperado e a ferramenta recusa conteúdo divergente.

Foram encontrados artigos históricos, incluindo um artigo que reúne os três vídeos. Eles não foram apresentados como geração atual nem como artigo exclusivo do vídeo principal: suas versões, fontes e finalidade diferem do piloto. Um snapshot de fonte real não substitui um artigo gerado e avaliado. Transcrições e artigos ficam no armazenamento privado de avaliação.

## Validação executada

Antes do envio ao Git, a suíte final completa passou: **661 testes Python**, incluindo 621 regressões do aplicativo e 40 testes da ferramenta de baseline. Foram quatro processos isolados (167 + 166 + 166 + 162), zero falhas, cerca de 4 minutos e 31 segundos. Relatório: `.local/final-regression-d7418a29/result.json`, acompanhado dos logs dos grupos. O fingerprint do aplicativo permanece igual ao auditado.

Os **14 testes JavaScript** de exportação passaram. `py_compile` do script e `git diff --check` passaram. Os testes usam dados isolados e não fazem chamadas pagas. O bloqueio observado de legendas é uma tentativa real de disponibilidade, não uma avaliação de qualidade editorial. As notas humanas permanecem vazias.

## Próximo incremento proposto

Gerar o caso principal pela versão atual usando somente sua transcrição preservada, na instalação com integração de IA configurada, e registrar modelo, perfil, tempo, custos e avaliação. Os outros dois casos não impedem essa execução. Depois do baseline, o primeiro diff proposto da camada nova adiciona contratos v1 e adaptadores puros em módulos próprios, com artefatos de proveniência pela persistência atual. Começar `off/shadow`, sem mudar texto, cache de etapas pagas ou custos; ativação futura depende da comparação pareada de fidelidade, utilidade e atribuição.

Não houve migração, regeneração, publicação, mudança de dependências ou escrita no banco de aplicação nesta fase. O rollback das adições desta entrega consiste em retirar seus documentos, ferramenta e testes; nenhum dado de produção depende deles. As perdas de transcrição identificadas continuam no código atual e ficam explicitamente pendentes para a Fase 1 após o baseline.
