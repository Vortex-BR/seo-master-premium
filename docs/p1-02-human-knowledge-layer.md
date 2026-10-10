# P1_02 — Human Knowledge Layer

A Human Knowledge Layer (HKL) preserva referências à fala, suas limitações e a estrutura semântica que já existe no job. A entrega inicial opera em **shadow**: produz um dossiê lateral determinístico para inspeção, sem acrescentar um agente, executar uma classificação paga, modificar o artigo ou exigir aprovação editorial para exportar.

O checkout de partida é `c8831d18af4c16121b070fe0a0479641cb218092`, app `1.5.25`, com o P1_01 do [PR #3](https://github.com/Vortex-BR/seo-master-premium/pull/3). A branch desta etapa é `feat/p1-02-human-knowledge-layer`, baseada no P1_01. A versão do app no checkout é `1.5.26`; editorial `7`, agentes `7`, evidence flow `2` e identidades das chamadas existentes permanecem compatíveis. Esta documentação não comprova deploy. Resultados de engenharia e medições devem ser consultados no [aceite](p1-02-human-knowledge-aceite.md).

## Diagnóstico antes do contrato

A auditoria de referência descreve `2ae42d821087631b2c6e2218c4f6b970d69bb737`, app `1.5.22`. O diagnóstico abaixo foi refeito no checkout de partida desta etapa; as diferenças dos P0_01, P0_02 e P1_01 foram consideradas antes de desenhar o sidecar.

| Superfície e função | O que existe e chega aos consumidores | Limitação confirmada no checkout de partida |
|---|---|---|
| `app/editorial/contracts.py`: `SpokenInsight`, `VideoSpokenInsights`, `SpokenExtraction` | Explicação falada, dicas, analogias, alertas, IDs dos segmentos, resumo e lacunas por vídeo | Não possuem classificação tipada de opinião, experiência, causalidade, condições, quantidades ou speaker |
| `app/editorial/video_first.py`: `extract` | Mantém a extração completa em `spoken_extraction`, `source_spoken_insight` em cada item e evidências dos segmentos originais | A projeção marca todos os insights como `kind='fato'` e `information_type='conceito'`; método, condições e quantidades ficam vazios |
| `app/editorial/contracts.py`: `KnowledgeItem`, `Quantity` | Caminho legado guarda natureza fato/opinião/experiência, tipo de informação, método, condições, unidades, restrições, limitações e evidência | A classificação anterior foi produzida pela IA; não comprova verdade científica ou independente |
| `app/editorial/workflow.py`: `extract`, `check_relations` | Extração legada por bloco, auditoria de cada item, relações entre trechos e snapshots de bloco/vídeo/apuração | Video-First usa outra extração e inicialmente tem `relations=[]`; relações que não foram obtidas não podem ser inventadas |
| `app/editorial/workflow.py`: `compact`, `locator`, `writing_item` | `compact` transporta campos selecionados; `locator` é índice abreviado; `writing_item` acrescenta IDs das fontes e o `source_spoken_insight` completo | `compact` omite o insight falado; `locator` abrevia a afirmação. Nenhum índice substitui a fala original |
| `app/editorial/video_first.py`: `plan`; `app/editorial/planning.py`: `plan` | Video-First envia `writing_item`, lacunas e janelas literais; o planejamento global legado usa `compact` e fontes literais | A redação não recebe uma natureza semântica que a extração nunca forneceu |
| `app/editorial/workflow.py`: `source_fragments`, `related_items`, `context_sources`, `dossier` | Recuperação por evidência, janelas com offsets, contrapontos e relações existentes; dossiê reduzido para o fluxo legado | Seleção conjunta ou ordem de timestamps não prova sequência, causalidade nem concordância |
| `app/editorial/composition.py`: `write`, `creator_voice`, `qualifications` | Redação recebe fala original, plano, insights completos, contrapontos, condições disponíveis e autor/timestamps informados | `creator_voice` não distingue speaker. `author` pode ser o canal/uploader; não comprova quem falou |
| `app/generation.py`: `resolve_evidence`, `evidence_map` | Resolver defensivo de segmentos/cues, timing, metadados e texto original quando disponíveis; IDs ambíguos/internos são rejeitados | Ausência de cues, tempo, speaker ou confiança no legado não pode ser recuperada por dedução |
| `app/generation.py`: `compact_source_provenance`, `context`, `prepare_structured` | Proveniência compacta em todos os caminhos; payload pago omite repetição de cues brutos e conserva fontes permitidas | Acrescentar o sidecar nestes payloads no modo shadow alteraria contexto, tokens e fingerprints |
| `app/editorial/reference_contracts.py`: `scope`; `evidence_selection.py`: `prepare`, `resolve` | IDs limitados ao inventário recebido e citações copiadas pelo servidor, incluindo janelas separadas | ID permitido e citação literal não comprovam apoio semântico, autoria ou verdade universal |
| `app/editorial/context_packing.py`: `pack_article`, `pack_findings` | Reaproveita referências literais sem perder conteúdo de artigo/parecer | Compactação de transporte não classifica a fala |
| `app/editorial/store.py`: `artifact`, `artifacts` | Snapshots imutáveis e content-addressed; identidades e dependências existentes | Não é necessário criar banco, tabela paralela ou reescrever todos os jobs antigos |

As fontes do P0_01 já mantêm `original_cues`, `original_text`, offsets/intervalos disponíveis, normalização e speaker/confiança fornecidos pelo provedor. `app/transcript_integrity.py` conserva esses registros e evita fundir cues incompatíveis. `source_processing.clean_spoken_transcript` produz uma cópia editorial, deixando o original intacto. `source_processing.quality` mantém completude **não verificada**, dependência visual e avisos de transcrição.

O benefício existente deve sobreviver: planejamento e redação recebem evidência original, não apenas um resumo. O novo dossiê é uma projeção dessa mesma fonte, nunca uma confirmação independente.

## Contrato lateral e proveniência

O módulo `app/editorial/human_knowledge.py` concentra `mode`, `project`, `persist_shadow`, `resolve_unit` e `compact`. O contrato da HKL é versionado separadamente: `schema_version='human_knowledge.v1'` e `projection_version=1`, com modo, identidade da projeção, dependência dos bytes de entrada, unidades, relações disponíveis e cobertura. A persistência reutiliza dois tipos de artefato:

- `human_knowledge_sources`: snapshot privado da fonte original, com `schema_version='human_knowledge_sources.v1'` e versão por conteúdo. O snapshot conserva a fala necessária à recuperação futura, mesmo depois de uma alteração da transcrição atual.
- `human_knowledge`: dossiê lateral com referências ao snapshot e às unidades existentes. A identidade é derivada dos dados e dependências, usando `store.artifact()`; repetir a mesma projeção deve reutilizar a identidade correspondente.

O snapshot da fonte não entra no contexto de cada agente. `resolve_unit` recupera dados sem I/O e aceita um snapshot explícito para leitura histórica. Fontes atuais diferentes, sem o snapshot correspondente, produzem estado obsoleto em vez de resolver contra outra fala. A recuperação deve devolver cópias defensivas e conferir versão/hash de fonte e referência; uma referência desconhecida ou ambígua não se resolve silenciosamente. `compact` prepara referências locais para consumo futuro, sem ativar esse consumo nos prompts existentes. Textos raw, banco, snapshots privados e transcrições completas não são publicados nos documentos/evidências do Git.

Cada unidade distingue informação disponível de interpretação. Uma natureza legada `opinião` ou `experiência` pode ser preservada com sua origem de classificação; uma classificação `fato` representa uma **afirmação da fonte**, não um fato verificado externamente. No Video-First, o rótulo sintético `kind='fato'` não autoriza essa promoção: natureza desconhecida continua desconhecida. Explicação, dicas, analogias e alertas já fornecidos pela extração podem ser conservados como campos explícitos, com seus limites de proveniência.

Se forem usados sinais lexicais determinísticos para descoberta — por exemplo uma expressão de dúvida ou um relato em primeira pessoa — eles devem ser marcados como **candidatos**, com o trecho literal e a regra aplicável. Um sinal lexical não certifica a experiência do locutor, não transforma hipótese em fato e não prova a causalidade proposta. A classificação avançada permanece uma evolução futura, sujeita a escopo e orçamento explícitos.

As âncoras mantêm source/segment/cue IDs disponíveis, trecho e/ou hash recuperável, offsets válidos e disponibilidade temporal. Trechos não contíguos preservam seus intervalos separados; o intervalo entre o primeiro e o último trecho não pode ser apresentado como fala contínua. Relações existentes mantêm os IDs relacionados, natureza, explicação e estado de conferência. O sistema não cria uma relação apenas por proximidade ou sequência temporal.

Autoria possui três papéis separados:

- **Autor do vídeo:** metadado fornecido pela fonte, possivelmente um canal. A origem do metadado é preservada.
- **Locutor:** label/nome fornecido explicitamente em segmento/cue. Ausência permanece desconhecida; divergências entre locutores não se resolvem usando o nome do autor.
- **Redator do artigo:** papel editorial do sistema, sem inventar identidade humana, experiência pessoal ou credencial.

`origin=video`, `origin=external_verified` e `origin=editorial_inference` têm significados distintos. O shadow desta etapa não executa pesquisa e não fabrica contexto externo. As notas web/background atuais são `internal_context_only=True`, com referências `verified=False`: elas não são promovidas para `external_verified` nem se tornam evidência dos vídeos.

## Shadow, identidade e integração

`HUMAN_KNOWLEDGE_MODE` controla a execução lateral. O padrão é `shadow`; `off` desativa sua geração. O modo shadow não alimenta automaticamente o plano, o prompt da redação ou a revisão paga com novos campos. Inspeção e preparação de referências compactas são locais; a ativação futura de consumo semântico não é presumida por esta entrega.

A integração prepara a projeção a partir de fontes/apuração disponíveis e permite retomada sem exigir extração paga novamente. Fontes incompletas e sidecars antigos precisam de tratamento explícito; a HKL não força migração de todos os registros ao iniciar o serviço. Artigos e diagnósticos já salvos permanecem acessíveis.

O armazenamento do sidecar não modifica `brief`, `sources` ou `apuration` usados nas chamadas existentes. Tampouco deve alterar `workflow.dependencies`, schemas pagos, instruções/modelos/provedores ou o inventário efetivo dos agentes. `engine.inputs_hash` depende de briefing/fontes; `engine.invocation_inputs` combina payload, scope, versões de plano/apuração e referências. Preservar esses valores é necessário para manter o cache pago. A comprovação exige comparação de requests completos e fingerprints, não apenas contagem de chamadas.


A observação roda após extração/reuso de apuração no `engine.run` e no `finally` de `pipeline.run`, incluindo falha, cancelamento e extração sem IA. `human_knowledge_runtime.observe` registra tentativa local no P1_01; falhas ordinárias ficam visíveis com apenas o tipo da exceção, sem conteúdo privado, e não substituem a entrega. Cancelamento real (`BaseException`) propaga. O registro marca zero operações/tokens adicionais de API e `infrastructure_usd=None`; não há reserva, liberação de saldo ou fatura presumida.

`project` e `resolve_unit` são puros. `persist_shadow` grava somente os dois tipos de artefato já descritos. A extração marcada válida com dependências de fonte/brief antigas é indicada `stale`; dados sem dependências comprováveis ficam `legacy_unverified`. Repetir persistência reaproveita os registros por identidade, mas ainda recalcula a projeção: os tempos de repetição do benchmark incluem esse trabalho. Não existe fast path por lookup nesta versão.

A ferramenta `scripts/human_knowledge.py` lê um job JSON privado sem acessar banco ou provedores. `--unit-id` resolve a fala; `--dossier-json` e `--source-snapshot` permitem recuperação histórica explícita. A saída contém dados privados e deve ficar fora do Git. A ferramenta recusa sobrescrever seus arquivos de entrada. Flag desconhecida equivale a `off`; não habilita modo ativo ou pago.

Os artefatos podem ser inspecionados pelo endpoint autenticado existente:

```text
GET /api/jobs/{job_id}/artifacts?kind=human_knowledge
GET /api/jobs/{job_id}/artifacts?kind=human_knowledge_sources
```

Essas respostas são privadas e podem conter conteúdo do usuário. Não existe autorização para publicar o snapshot ou incluí-lo em um relatório público de evidências. A camada não cria gate de qualidade, tamanho mínimo, seção extra ou permissão adicional para WordPress. Exportação de artigo tecnicamente válido, envio como `pending` e reconciliação contra edição externa continuam sob os controles existentes.

## Custos, medição e limites

O trabalho novo é local: projeção, validação, hashing e persistência de artefatos. A camada não deve abrir nova chamada OpenAI, pesquisa, imagem ou transcrição. Assim, chamadas/tokens adicionais de IA pelo shadow devem ser zero, demonstrados por doubles/requests comparados nos testes; isso não é uma medida de custo total da infraestrutura.

CPU, tempo de parede, memória/alocação e bytes de dossiê/snapshot devem ser medidos em fixtures isoladas. Os resultados medidos em sete repetições por caso estão no [benchmark](evidencias/p1-02/benchmark-human-knowledge.json), com runtime e hashes do código. São valores locais com instrumentação `tracemalloc`, não latência de geração ou preço de infraestrutura. O snapshot de fala adiciona armazenamento privado; repetição idempotente não deve produzir novas cópias para a mesma identidade. Não confundir custo sintético ou ausência de API com custo zero de operação.

O P1_01 permanece responsável por tentativa, uso conhecido/estimado, reservas incertas, orçamento e reconciliação. A HKL não recalcula faturas, não libera reservas e não aumenta o orçamento. Qualquer classificação paga futura deve reutilizar esses controles e depender de autorização específica; não faz parte da ativação padrão.

O piloto aceita **um vídeo**. A produção própria já informada pelo usuário permite usar a amostra local disponível; isso não comprova completude da transcrição, qualidade do artigo, identidade de todos os locutores ou custo de uma geração atual. O uso dos bytes reais disponíveis precisa ser registrado por hash, sem publicar seu texto bruto. Geração paga, avaliação humana e operação em produção só podem ser descritas como realizadas com evidência própria; atualmente não são comprovadas por esta documentação.

| Caso | Segmentos | Projeção mediana | CPU mediana | Pico Python | Dossiê JSON | Crescimento DB inicial / repetido |
|---|---:|---:|---:|---:|---:|---:|
| authorized_primary_source_only | 3 | 0.0040 s | 0.0000 s | 31,743 B | 5,584 B | 8,192 / 0 B |
| synthetic-100-segments | 100 | 0.0790 s | 0.0781 s | 1,578,084 B | 186,969 B | 204,800 / 0 B |
| synthetic-1000-segments | 1000 | 0.8714 s | 0.8438 s | 15,776,580 B | 1,869,981 B | 2,007,040 / 0 B |

Medições de armazenamento usam SQLite temporário recém-inicializado. O crescimento é alocação de arquivos observada nesta estação; a memória é pico de alocações Python, não RSS total. CPU zero no caso pequeno reflete a resolução do relógio Windows. Cada repetição conservou um registro de dossiê e um de fonte, e não alterou o job. Foram resolvidas amostras da primeira/meio/última unidade nos casos sintéticos; no piloto isso abrange os três registros disponíveis. O volume de sidecar cresce com unidades, âncoras e campos legados. As medidas não sustentam economia por artigo nem qualidade humana.

O [recibo do piloto](evidencias/p1-02/pilot-source-recovery.json) registra três segmentos, 2.651 caracteres e hashes antes/depois iguais para `cVnRvZ8uMCo`. Os três originais foram recuperados. Sem apuração atual, os tipos permanecem `unknown` e os indícios literais são apenas candidatos. Não houve extração nova, geração de artigo, pesquisa, transcrição, imagem ou avaliação semântica humana.

## Limitações residuais e evolução

Os prompts e schemas pagos desta entrega permanecem os do checkout de partida. Portanto, o rótulo `kind='fato'` na projeção Video-First e em `supported_claims` da revisão continua presente no fluxo anterior; a HKL o trata como rótulo sintético de apoio à fala. A expressão **“especialista”** no prompt atual de composição também permanece: a arquitetura não verifica credencial profissional. Esses riscos são documentados, não ocultados por um score de cobertura.

Sem classificação semântica validada, algumas condições, exceções, motivos, resultados e experiências só são recuperáveis na fala original. O dossiê registra ausência em vez de inventar uma estrutura completa. Contagem de unidades/tipos e taxa de proveniência disponível medem cobertura estrutural; não medem fidelidade perfeita ou precisão factual do vídeo. Não houve análise visual automática por este patch.

Para a futura Inteligência Editorial Crítica, podem ser reaproveitados os snapshots imutáveis, resolver de evidência, unidades com origens distintas, referências compactas, relações existentes e métricas de ausência. Também permanecem reutilizáveis os contratos restritos por inventário, a seleção server-side de citações, a revisão factual independente, os controles financeiros e a política de exportação não bloqueante. O consumo futuro pelo planejamento/redação deve ser seletivo e condicionado a qualidade demonstrada, preservando os originais para conferência.

## Operação e rollback

Não há migração destrutiva nem backfill compulsório. Antes de um deploy autorizado, fazer backup consistente dos dados afetados, incluindo SQLite/WAL e mídia, mantendo chaves sob guarda privada. Não compartilhar credenciais ou DB em artefatos de entrega.

Para desativar a geração lateral, configurar `HUMAN_KNOWLEDGE_MODE=off` no serviço e reiniciar conforme o procedimento normal de deploy. Essa mudança deve ser feita apenas no ambiente autorizado. O rollback funcional deixa o fluxo editorial anterior operar com os dados já existentes; preserva `human_knowledge` e `human_knowledge_sources` para auditoria. Desativar não significa excluir os artefatos ou restaurar o banco a uma versão antiga.

Para rollback de código, retornar ao commit do P1_01 ou reverter apenas este patch, mantendo o backup e os dados. A reversão de Git não apaga artefatos nem desfaz operações no banco. Snapshots novos não devem alterar a leitura dos artigos anteriores. Não editar ou remover WordPress posts como parte deste rollback.

Esta etapa é uma implementação em branch/PR até que merge e deploy sejam verificados. A entrega de engenharia, o aceite editorial do piloto e a liberação em produção são eventos separados, com evidências próprias.
