# Proposta de Human Knowledge Layer — Fase 0

**Estado:** proposta técnica, sem alteração do pipeline de produção. Auditoria do código local em 09/10/2026. A implementação futura usa um baseline piloto de um vídeo principal, conforme a orientação posterior do usuário; resultados de testes simulados não demonstram melhora editorial.

O documento `06_PROMPT_EXECUCAO_AGENTE.md` limita a primeira entrega à auditoria, matriz de diferenças, baseline e proposta concreta. Esta proposta atende a esse limite. A revisão automática não bloqueante já existe e deve ser preservada.

O usuário confirmou que a produção usa vídeos próprios e que os links fornecidos servem para testes. Também definiu que o artigo deve funcionar com **um único vídeo e ter 100% de seu conteúdo editorial baseado nele**. Para esta execução, um vídeo principal forma o baseline obrigatório; até dois vídeos adicionais são testes opcionais. A amostra de 12 vídeos do plano original passa a ser recomendação futura de diversidade para comparação, sem ser requisito para produzir um artigo ou avançar o piloto.

## Decisão arquitetural

Acrescentar uma camada versionada que **referencie os itens e as explicações já persistidos**, sem substituir `SpokenExtraction`, duplicar `KnowledgeItem` nem regenerar artigos antigos. Começar com um adaptador determinístico em modo de observação: ele registra proveniência e explicita informações desconhecidas, sem novas chamadas de IA e sem modificar prompts, plano, artigo ou exportação.

A camada adicional deve distinguir três coisas: localização na fonte, interpretação editorial e avaliação semântica. Um ID existente e um timestamp válido demonstram localização; não provam a veracidade da afirmação, sua atribuição nem a preservação de uma ressalva.

Preservar integralmente as etapas relevantes, experiências, opiniões, exemplos, condições e ressalvas que sustentam a resposta, inclusive o fim da transcrição e qualificações em trechos distantes. A organização pode adaptar a fala à leitura, mas não acrescentar explicações externas, etapas ou vivências para completar o artigo. “100% baseado no vídeo” define a origem do conteúdo; a conferência deve reunir evidências de fidelidade, sem apresentar uma garantia automática de ausência de erros ou de acesso a conteúdo visual não processado.

## Evidência da arquitetura atual

As referências abaixo apontam para o repositório atual. Números de linha podem mudar em revisões posteriores.

| Área | Evidência local | Consequência para a proposta |
|---|---|---|
| Fluxo efetivamente ativo | `app/editorial/engine.py:40`, `:78`; `app/editorial/workflow.py:264`, `:517`, `:742`, `:864` | Todo ciclo novo usa `video_first=True`, composição completa e revisão factual. O caminho particionado permanece para compatibilidade; inspecionar apenas `planning.py` não descreve o fluxo ativo. |
| Conhecimento falado | `app/editorial/contracts.py:78`, `:96` | `SpokenInsight` já preserva explicação, dicas, analogias, alertas e IDs dos segmentos. `SpokenExtraction` já organiza entregas por vídeo. Reutilizar esses contratos. |
| Conhecimento estruturado anterior | `app/editorial/contracts.py:117`, `:160` | `KnowledgeItem` já oferece natureza, tipo de informação, método, condições, quantidades, restrições, limitações e evidências; `SourceRelation` já modela sequência, exemplo, restrição, condição e contradição. A camada nova deve complementar lacunas do caminho ativo. |
| Normalização do caminho ativo | `app/editorial/video_first.py:86` | Todos os insights recebem `kind='fato'`, `information_type='conceito'`, método vazio, condições vazias e quantidades vazias. Isso perde classificação estruturada de experiência/opinião, embora a explicação livre e `source_spoken_insight` continuem presentes. |
| Estado de suporte inicial | `app/editorial/video_first.py:70`, `:93` | A validação local confere IDs únicos pertencentes ao vídeo. O `check.status='supported'` inicial é descrito como conferência de IDs, com significado reservado à revisão final. Não migrá-lo como aprovação semântica. |
| Relações no caminho ativo | `app/editorial/video_first.py:96` | Os contextos recebem `relations=[]`. Causalidade e sequência podem permanecer no texto livre, mas não ficam estruturadas nesse ponto. |
| Evidências literais | `app/editorial/workflow.py:229`; `app/editorial/evidence_selection.py:14`, `:52` | Citações são conferidas contra o original; revisões escolhem opções, e o servidor resolve trechos literais. Estender esse mecanismo em vez de pedir ao modelo que invente excertos. |
| Fonte por tarefa | `app/editorial/reference_contracts.py:32`; `app/editorial/video_first.py:70` | O esquema restringe referências ao inventário recebido; a validação adicional exige que os segmentos pertençam ao vídeo correto. Aplicar as duas etapas à camada nova. |
| Dados enviados à redação | `app/editorial/workflow.py:834`; `app/editorial/composition.py:79` | `writing_item()` transmite `source_spoken_insight` e a composição exige evidências dos vídeos. A redução dos campos estruturados não demonstra, sozinha, perda no artigo. Medir o resultado final. |
| Atribuição e referências de tempo | `app/editorial/composition.py:171` | Nome do autor, título, URL e referências de tempo disponíveis são enviados ao redator. Não há contrato explícito de locutor no `SpokenInsight`; autor do canal e pessoa que falou não são intercambiáveis em entrevistas. |
| Projeção do dossiê | `app/editorial/workflow.py:616`; `app/schemas.py:84` | O dossiê reduz itens a `statement/kind/evidence`; exemplos dependem de `information_type='exemplo'`. No caminho ativo esse rótulo é sempre `conceito`, portanto essa lista não recupera os exemplos embutidos na explicação. Não usar o dossiê resumido como fonte exclusiva da camada nova. |
| Janelas de contexto | `app/editorial/workflow.py:128`, `:201` | Janelas literais de evidência têm preenchimento de 450 caracteres e metadados `original_spans`; relações conhecidas ampliam os itens relacionados. Ressalvas distantes ainda precisam de vínculos explícitos. No caminho ativo, evidências armazenadas contêm o segmento inteiro, o que preserva seu conteúdo, mas não garante outros segmentos relevantes. |
| Compactação reversível | `app/editorial/context_packing.py:6`, `:23`, `:42` | `content_parts` substitui cópias por referências literais; não resume nem corta texto. Não refazer essa compactação como suposta perda de informação. |
| Recusa por contexto | `app/generation.py:184`, `:420`; `app/editorial/composition.py:107` | O pedido completo é medido antes de chamadas; exceder o limite provoca erro explícito. A camada nova não deve contornar limites apagando trechos nem inserir dados extras no prompt durante o modo de observação. |
| Separação da pesquisa | `app/generation.py:110`, `:137`; `app/editorial/research.py:177`, `:232`; `app/editorial/guidance.py:5` | `evidence_map()` exclui contexto interno; a pesquisa usa `internal_context_only=True`; novos ciclos não iniciam buscas. Informações externas verificadas como conteúdo seria mudança de política futura, não recurso a ativar incidentalmente. |
| Persistência | `app/db.py:41`, `:77`; `app/editorial/store.py:25`, `:137` | Trabalhos são JSON em SQLite; artefatos são imutáveis por hash de conteúdo e dependências. Há infraestrutura para um artefato adicional, sem nova tabela no primeiro incremento. |
| Identidade e retomada | `app/editorial/store.py:85`, `:169`; `app/editorial/engine.py:111`, `:187` | Cache considera ciclo, entrada, papel e fingerprint. Novos contratos e modos precisam participar da identidade das etapas que os consomem, sem invalidar a retomada de ciclos antigos. |
| Revisão e entrega | `app/editorial/video_first.py:155`; `app/editorial/engine.py:441`, `:500`; `app/editorial/delivery.py:11` | Revisão cobre passagens e itens previstos; falha da revisão preserva o artigo exportável. A camada nova não deve criar aprovação humana obrigatória, bloqueio por nota ou ciclo de reescrita. |

## Contrato proposto, versão 1

Introduzir `HumanKnowledgeDossierV1` e `HumanKnowledgeItemV1` em módulo próprio. As tabelas abaixo especificam tipos e validações; **não são classes implementadas**. Objetos novos devem rejeitar campos desconhecidos (`extra='forbid'`), usar números finitos e admitir `null` quando a fonte não permite preencher um valor. Não alterar a política de leitura dos contratos legados.

### Envelope do dossiê

| Campo | Tipo/regra |
|---|---|
| `schema_name` | Literal `human_knowledge_layer`. |
| `schema_version` | Literal inteiro `1`; diferente do hash de versão do artefato. |
| `adapter_version` | Literal inteiro `1`; mudanças de interpretação do legado incrementam este campo. |
| `apuration_version` | Hash do artefato original de apuração. |
| `inputs_version` | Hash dos inputs usados nessa apuração, não apenas do trabalho em seu estado atual. |
| `source_snapshot_version` | Hash de um snapshot imutável das fontes originais necessárias à resolução dos trechos. |
| `origin_contract` | `legacy_spoken` ou `legacy_knowledge`; descreve o adaptador escolhido, sem atribuir ao legado uma versão que ele não possui. |
| `projection_status` | `complete`, `partial` ou `unavailable`, com diagnóstico explícito para referências ausentes. Completo aqui significa projeção do inventário, não cobertura de todo o conteúdo do vídeo. |
| `items` | Um item por ID existente de apuração, sem renumeração; IDs únicos dentro da versão de apuração. |
| `relations` | Relações existentes copiadas com sua evidência e avaliação; vazio quando não existem. Novas relações semânticas somente em fase posterior. |
| `gaps` | Lacunas já registradas, preservando origem e IDs, mais diagnósticos do adaptador. |

O snapshot original deve incluir texto literal e metadados disponíveis por segmento, com associação segmento → vídeo. Pode usar a persistência de `editorial_artifacts`; não deve enviar outra cópia da transcrição aos agentes. Uma referência futura resolve sempre contra esse snapshot, não contra fontes editadas depois. A própria apuração já guarda texto em `inventory.blocks.owned`; o novo snapshot completa metadados de segmentos e reduz a dependência do JSON mutável do trabalho. Não duplicar o `spoken_extraction` dentro de cada item.

### Item de conhecimento

| Campo | Tipo/regra |
|---|---|
| `id` | O ID existente do item. A identidade completa é `(apuration_version, id)`, porque a numeração de insights pode mudar em outra extração. |
| `legacy_ref` | Referência ao item original e, quando presente, a `source_spoken_insight` na mesma versão de apuração. `statement/topic/didática` continuam canônicos nesse registro. |
| `source_video_id` | Derivado pelo servidor dos segmentos originais. Rejeitar divergência entre vídeo declarado e proprietário real; itens legados de múltiplos vídeos exigem diagnóstico explícito. |
| `knowledge_types` | Lista de `fact`, `opinion`, `experience`, `method`, `example`, `limitation`, `reasoning`, `unknown`. Um insight pode reunir tipos diferentes; `unknown` não pode ser acompanhado por classificações afirmadas. |
| `classification_status` | `unclassified`, `legacy_declared`, `source_assessed` ou `human_assessed`. No adaptador `legacy_spoken`, iniciar como `unclassified/unknown`; não usar o `kind='fato'` automático como classificação fiel. |
| `attribution` | Autor do vídeo exatamente como metadado disponível; `speaker=null` quando não identificado; `speaker_basis` somente `unknown`, `provided_metadata` ou `source_assessed`. Não inferir locutor por ser autor do canal. |
| `evidence_refs` | Lista de âncoras para os segmentos originais, descritas abaixo. A evidência literal é resolvida pelo servidor. |
| `reasoning` | `null` ou detalhe com texto e âncoras que apoiem o raciocínio. Começar `null` quando houver apenas explicação livre não analisada. |
| `examples` | Detalhes com texto e âncoras. Lista vazia significa não estruturado/extraído, e não prova ausência de exemplos na explicação. |
| `qualifiers` | Detalhes apoiados na fonte com tipo `condition`, `warning`, `restriction` ou `limitation`. Registrar também sua origem no legado. Condições/alertas já existentes podem ser projetados como declarações ainda não avaliadas semanticamente. |
| `steps` | Detalhes identificados e ordem/dependências somente quando explicitamente sustentados; vazio no adaptador inicial se não existir relação validada. Ordem de timestamp não prova dependência causal. |
| `evidence_status` | `reference_validated`, `source_supported`, `uncertain`, `unsupported` ou `unavailable`. `source_supported` requer avaliação semântica específica e registrada; não significa comprovação factual externa. |
| `assessment_ref` | Referência à avaliação aplicável ou `null`. Preservar separadamente o `check` legado, sem promover a conferência inicial de IDs a revisão semântica. |
| `visual_status` | `not_analyzed`, `source_reports_visual_dependency` ou `analyzed_with_evidence`; o último valor é reservado à fase multimodal com artefato real. |

Cada detalhe futuro (`reasoning`, exemplo, ressalva ou etapa) deve possuir suas próprias âncoras. Não basta citar um segmento genérico para toda uma interpretação longa. O adaptador inicial é uma projeção fiel; não tenta extrair novos detalhes por regex nem inventa classificações.

### Âncora de evidência

| Campo | Tipo/regra |
|---|---|
| `source_segment_id` | ID original, resolvido exclusivamente no snapshot indicado. |
| `offset_start`, `offset_end` | Inteiros de posição de caracteres no texto original, com `0 <= start < end <= len(text)`. Segmento completo usa `0..len(text)`. |
| `excerpt_sha256` | Hash do trecho literal resolvido. Não substituir validação literal por normalização. |
| `start_seconds`, `end_seconds` | Valores originais disponíveis, finitos, não negativos e ordenados; `null` para dado ausente/inválido com diagnóstico. Não interpolar tempo a partir de offsets de caracteres. |
| `transcript_confidence` | `unknown`, `warning_signal` ou `provider_reported`, com referência ao sinal/valor original quando existir. Ausência de alerta não significa alta confiança. |

Não representar trechos separados por um intervalo único artificial. Cada segmento tem sua própria âncora. Repetições de uma citação dentro do mesmo segmento exigem resolver todas as ocorrências ou registrar ambiguidade; não assumir silenciosamente a primeira ocorrência. Para evidência antiga com diferenças de caixa ou citação não literal, registrar referência não resolvida e preservar o registro original.

### Base de uso da fonte

Registrar junto ao snapshot, separado da avaliação editorial, `use_basis.status` (`unrecorded`, `declared_authorized`, `restricted`, `review_required`), declaração, responsável e data quando efetivamente informados. Nesta execução, preservar a declaração já fornecida pelo usuário: vídeos próprios em produção e links fornecidos para testes. O adaptador pode registrar essa declaração com sua origem, sem solicitar novamente a mesma informação. Em outros registros, não deduzir autorização a partir de URL pública, crédito, transcrição enviada ou metadado do autor.

No primeiro incremento a ausência desse dado vira diagnóstico para seleção do corpus. Não acrescentar um bloqueio retroativo às exportações existentes. Tratamento operacional de uma restrição concreta pertence a uma decisão própria, conforme direitos e escopo aplicáveis; a camada de conhecimento não deve presumir nem contornar essa restrição.

### Correspondência com o schema conceitual do plano

| Campo do documento `03` | Correspondência proposta |
|---|---|
| `id`, `source_video_id` | IDs existentes e propriedade dos segmentos conferida pelo servidor. |
| `start_seconds`, `end_seconds`, `original_excerpt` | Âncoras por segmento e resolução literal; sem inventar um intervalo para evidências descontínuas. |
| `speaker`, `attribution` | Locutor separado do autor do vídeo e base explícita de identificação. |
| `knowledge_type`, `claim` | Tipos múltiplos quando necessários; afirmação principal referenciada no item canônico existente. |
| `reasoning`, `example`, `qualifiers` | Complementos estruturados com evidências próprias, ausentes quando não analisados. |
| `transcript_confidence` | Proveniência do sinal, sem converter ausência de aviso em confiança alta. |
| `evidence_status` | Validação de referência separada de apoio semântico e de verificação factual externa. |

## Adaptadores e leitura de dados antigos

1. `from_spoken_apuration_v1(apuration, source_snapshot)` resolve um artefato existente, confere todos os IDs e cria a visão adicional. Preserva a explicação livre, dicas, analogias e alertas por referência. Não usa `Dossier.claims` como entrada substituta.
2. `from_knowledge_apuration_v1(...)` reutiliza `KnowledgeItem`, `SourceRelation` e avaliações existentes. Tipos informados pelo legado ficam `legacy_declared`; somente avaliações realmente registradas podem fundamentar `source_supported`.
3. `read_human_knowledge(...)` seleciona versão conhecida; versão futura desconhecida deixa a projeção indisponível com diagnóstico e mantém leitura/exportação do artigo legado. Não interpretar silenciosamente um schema desconhecido como versão 1.
4. Um round-trip do documento novo preserva todos os campos, IDs, hashes e referências. O adaptador não oferece conversão destrutiva de volta ao legado, porque os campos novos não cabem nele; o legado permanece intacto e pode ser comparado byte a byte.
5. Leitura de um artigo antigo não aciona IA, transcrição ou migração obrigatória. Uma projeção solicitada usa apenas snapshots existentes e informa quando metadados indispensáveis não estão disponíveis.

## Primeiro diff proposto: pequeno e reversível

**Este diff não foi aplicado na Fase 0.** Ordem sugerida para a primeira implementação depois do baseline:

| Arquivo | Mudança futura |
|---|---|
| `app/editorial/human_knowledge_contracts.py` | Contratos da versão 1, sem alterar classes legadas. |
| `app/editorial/human_knowledge.py` | Adaptadores puros, resolvedor de âncoras e diagnóstico; zero chamadas de rede/IA. |
| `tests/test_human_knowledge.py` | Round-trip, invariantes, casos legados e integridade do snapshot. |
| `app/editorial/engine.py` | Congelar modo e versão da camada no início de **novos** ciclos; ciclos antigos conservam sua identidade e retomada. |
| `app/editorial/video_first.py` | Após salvar apuração original, produzir snapshot/projeção apenas no modo de observação. Falha da projeção gera diagnóstico separado e preserva a apuração/artigo existentes. |

Usar inicialmente uma configuração operacional `SEO_HUMAN_KNOWLEDGE_MODE=off|shadow|active`, com padrão `off`; valores inválidos devem produzir diagnóstico de configuração e manter `off`. `active` só pode ser habilitado quando o consumidor estiver implementado e validado. Não inserir uma preferência de implementação na interface editorial.

No modo `shadow`, persistir artefatos `source_snapshot` e `human_knowledge` pela API atual de `store.artifact()`. Suas dependências incluem inputs, versão da apuração, snapshot, schema e adaptador. Não alterar `apuration.version`, `workflow.dependencies()` nem cache de etapas pagas que ainda não consomem a camada. Não anexar o dossiê inteiro aos prompts. Falhas devem registrar código seguro, versão e IDs suficientes ao diagnóstico, sem credenciais ou cópia indiscriminada da transcrição nos logs.

No modo `active` futuro, planejamento, composição e revisão receberão apenas a projeção pertinente. Suas identidades de cache e slots precisarão incluir `schema_version`, `adapter_version`, versão do artefato e modo congelado do ciclo. Isso será um segundo diff, acompanhado de comparação com o mesmo corpus. Mudança global de versões não deve forçar regeneração de artigos nem alterar o orçamento de ciclos retomados.

## Invariantes e testes futuros

| Invariante | Verificação necessária |
|---|---|
| Legado continua intacto | Comparar antes/depois apuração, plano, artigo, hash, revisão e exportações; adaptador puro não muta entrada. Ler/exportar artigo sem camada e com schema desconhecido. |
| IDs não sustentam semântica por si só | Caso com `supported` inicial do caminho falado produz `reference_validated`, nunca `source_supported`. |
| Evidência permanece literal | Resolver offsets contra snapshot imutável; detectar hash divergente, ID ausente, duplicado, vídeo errado e citações repetidas/ambíguas. |
| Não inventar autoria/tempo/confiança | Entrevista sem locutores produz `speaker=null`; timestamp ausente/inválido permanece desconhecido; ausência de alerta não vira confiança alta. |
| Experiência não vira fato universal | Declaração de experiência mantém atribuição e alcance. Mistura de fato/opinião em insight não é achatada em categoria única sem avaliação. |
| Ressalvas e alternativas permanecem recuperáveis | Caso com ressalva distante exige evidência adicional antes de ampliar o plano; métodos divergentes não são conciliados automaticamente. |
| Sequência não é inferida por timestamp | Preservar etapas e dependências somente quando a fala/relação registrada as sustenta. |
| Pesquisa continua fora da evidência do artigo | Notas `rn`, contexto interno e fontes web não entram como âncoras de conhecimento do vídeo. A política atual permanece. |
| Limitação visual permanece explícita | Texto que remete à tela recebe `not_analyzed`/lacuna; não declarar demonstração observada. |
| Sem custos adicionais em observação | Mesmo número de chamadas, mesmos pedidos ao provedor e mesmo orçamento; apenas artefatos adicionais. Medir custo de armazenamento/tempo local, sem prometer custo zero de infraestrutura. |
| Persistência reproduzível | Round-trip JSON/Pydantic; mesmo conteúdo/dependências reutiliza o artefato; mudança de fonte/versão cria novo snapshot; o antigo segue resolvível. |
| Falha recuperável | Interrupção durante observação não repete chamadas concluídas nem elimina artigo; retomada respeita modo congelado e cache legado. |
| Exportação segue não bloqueante | Recomendações, projeção ausente, falha da camada e notas da rubrica não bloqueiam formato tecnicamente válido; manter proteções técnicas e testes WordPress existentes. |
| Base de uso não é inventada | Fonte pública sem declaração permanece `unrecorded`; declaração real preserva responsável, texto e data; restrição não é apagada pelo adaptador. |

Os testes atuais já incluem limpeza conservadora e preservação do insight (`tests/test_spoken_composition.py:11`, `:52`, `:124`), rejeição de conteúdo web na composição (`:152`), timestamps não inventados (`:166`), evidência e cobertura consistentes (`tests/test_delivery_contracts.py:114`) e citações inseridas pelo servidor (`:243`). São proteções a manter; sua existência não substitui avaliação humana com fontes reais. A execução desses testes é registrada pelo relatório da Fase 0, não presumida neste documento.

## Comparação com baseline e implantação

Antes de mudar prompt ou algoritmo, fixar **um vídeo principal** e seu artigo atual como baseline piloto obrigatório. Até dois vídeos adicionais podem testar variação, sem impedir o piloto. Usar os inputs e snapshots locais reais quando disponíveis e consistentes; fixar URL, declaração de uso já fornecida, metadados, transcrição original com tempos disponíveis, versões do código/perfil/modelo, artigo atual, trechos essenciais, etapas, opiniões, ressalvas e exemplos. Comparar as mesmas entradas e condições; registrar separadamente mudanças inevitáveis do provedor/modelo.

O artigo de um vídeo deve ser autossuficiente dentro do conhecimento que a fonte oferece. Conferir o começo, o meio e o fim da transcrição e mapear cada elemento essencial ao texto entregue, incluindo atribuição e ressalvas. Um caso ausente na fonte permanece ausente; extensão ou quantidade de vídeos não autoriza ampliar o conteúdo. A diversidade de 12 vídeos nas seis categorias originais é uma recomendação para avaliação futura de generalização, sem quantidade mínima de fontes por artigo ou condição para aceitar o piloto de um vídeo.

No modo de observação, medir quantos itens têm referência resolvível, metadados desconhecidos, atribuição disponível, avisos de transcrição e lacunas visuais; essas medidas são técnicas. Cobertura de experiências/ressalvas essenciais, fidelidade, utilidade e naturalidade exigem referência humana. Aplicar a rubrica 30/25/20/15/10 do plano como diagnóstico e contar erros críticos separadamente. Não aprovar a versão só por nota automática ou por possuir mais campos.

Prosseguir para consumo ativo em um piloto de um vídeo somente após testes de compatibilidade e comparação real; ampliar gradualmente preservando snapshots. O aceite de melhora editorial permanece pendente até existir evidência desse piloto e avaliação humana. Os dois testes opcionais e a futura amostra diversificada ampliam a evidência de generalização, sem substituir o caso principal. Esta proposta não afirma melhora de notas, garantia automática de fidelidade integral ou análise visual; registra a declaração de uso já fornecida pelo usuário.

## Rollback seguro

Voltar a configuração para `off` em novos ciclos e cessar a criação/consumo de artefatos adicionais. Preservar snapshots já criados, apuração original, artigos, revisões e histórico. Não apagar tabelas, reescrever trabalhos antigos ou regenerar artigos. Ciclos iniciados com um modo congelado precisam de regra de retomada testada; em falha do consumidor ativo, registrar diagnóstico e manter o rascunho exportável, sem misturar caches de versões diferentes. Qualquer retorno ao pipeline anterior que exigiria nova chamada paga deve ser explícito e respeitar os limites existentes.

O primeiro incremento permite rollback por configuração e reversão dos módulos novos, pois o pipeline anterior não depende dos artefatos de observação. Um incremento ativo terá plano de rollback próprio antes de ser habilitado.
