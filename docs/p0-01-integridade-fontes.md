# P0_01 — Integridade das fontes

## Escopo e estado da entrega

Implementação do pacote `PATCH_P0_01_Integridade_Fontes_ORCA/P0_01_Integridade_Fontes`, cujos seis Markdown foram conferidos com `MANIFESTO_SHA256.txt`. O checkout inicial correspondia ao baseline auditado `2ae42d821087631b2c6e2218c4f6b970d69bb737`, app 1.5.22, agentes editoriais versão 7. A implementação usa a branch `feat/p0-01-source-integrity`, app 1.5.23, preservando agentes versão 7.

O patch corrige a ingestão e a rastreabilidade no código. A verificação utiliza fixtures e fronteiras de provedor simuladas. Não houve geração paga, chamada real de transcrição/pesquisa/imagem, alteração de configuração ou dados de produção nem publicação no WordPress. A auditoria histórica e os ZIPs existentes não são modificados.

## Diagnóstico reproduzido antes da implementação

Quatro testes foram executados no baseline antes das mudanças de aplicação, todos com falha esperada:

| Defeito | Reprodução | Correção |
| --- | --- | --- |
| Remoção indiscriminada de `<...>` | `2 < valor e valor > 1` perde conteúdo | Parser de texto diferencia marcação reconhecida de expressão técnica; renderizadores continuam escapando/sanitizando conteúdo. |
| Agrupamento apenas por caracteres | Cues `[0,5]` e `[200,205]` viram uma evidência contínua | Agrupamento exige continuidade temporal e metadados compatíveis; intervalos originais permanecem armazenados. |
| Fim de SRT perdido | Cue de 10 a 25 segundos termina em 10 | Parser lê início, fim, duração, identificador e configurações de cue. |
| Warning associado apenas pelo início | Warning `[0,15]` não alcança segmento `[10,20]` | Associação por interseção de intervalos; pontos e avisos sem localização têm tratamento explícito. |

Comando RED: `.venv/Scripts/python.exe -m pytest tests/test_source_integrity.py -q --tb=short --junitxml=.local/p0-01-integridade/red-junit.xml`. O JUnit e `red-result.json` registram o commit e as quatro falhas; não são apresentados como sucesso. O log inicial do PowerShell usa UTF-16, enquanto JUnit/JSON e os logs finais são portáveis.

## Fluxo e arquivos

1. `youtube.extract()` recebe legendas, rows Supadata ou transcrição de áudio existente. `transcripts.normalize()` converte milissegundos e conserva metadados recebidos. O processamento Whisper e seus checkpoints continuam sendo reaproveitados.
2. `transcript_integrity.parse_manual_rows()` interpreta texto, SRT e VTT; `normalize_rows()` produz segmentos e cues originais. `youtube.segment_rows()` e `manual_segments()` mantêm a interface anterior e convertem erros para `SourceError`.
3. `editorial/source_processing.py` calcula qualidade e localiza warnings. O extrator Video-First recebe a cópia editorial limpa, mantendo referências para o material armazenado. Dossiê, apuração, pauta, redação e revisão continuam usando os IDs dos segmentos.
4. `generation.evidence_map()` projeta evidências e metadados compactos; `resolve_evidence()` recupera o segmento ou cue original sem I/O. `compact_source_provenance()` evita transportar cópias brutas para as fronteiras de IA, inclusive estratégia e pesquisa.
5. `source_cache.find_recent()` remapeia a propriedade dos IDs e registra reutilização. `pipeline.py` seleciona somente cache normalizado compatível para extrações novas.
6. `seo/checks.py` compara marcações visíveis com intervalos das referências no mesmo bloco textual. `main.py` valida o documento manual antes de arquivar a revisão e substituir a fonte. A interface exibe tempos desconhecidos sem inventar `0:00`.

## Contratos, compatibilidade e dados

O contrato de normalização é `source-integrity-v1`, com `normalization_options.merge_adjacent`. Os campos anteriores `id`, `text`, `start` e `end` permanecem. Campos aditivos incluem `duration`, `speaker`, `confidence`, `cue_ids`, `intervals`, `normalization_warnings` e `original_cues`.

Cada cue guarda ID local, identificador original quando recebido, texto original e normalizado, início/fim/duração e metadados disponíveis. Campos desconhecidos permanecem `null`; nome do canal não identifica locutor. Uma voz VTT só é atribuída quando a anotação cobre toda a fala da cue. Vozes múltiplas ou anotação parcial geram incerteza explícita.

O agrupamento usa limite nominal de 900 caracteres, tolerância de continuidade de 0,05 segundo para arredondamento e igualdade dos atributos relevantes. Lacunas materiais, sobreposições, tempos desconhecidos, duração zero, mudança de voz/confiança/origem/qualidade ou warnings distintos separam segmentos. A lista dos intervalos originais evita certificar lacunas dentro de um envelope temporal.

SRT/VTT aceitam BOM, CRLF, frações, identificadores, configurações VTT e blocos de metadados. Texto órfão é conservado sem tempo. Intervalos invertidos, timestamps inválidos e cues sem fala produzem erro explícito; um documento parcialmente inválido não é aplicado pela rota manual. Sobreposições e duração zero são preservadas com diagnósticos.

Warnings com intervalos positivos usam limites semiabertos `[início,fim)`. Avisos de um instante são pontos, incluindo tempo zero. Avisos sem localização confiável permanecem globais e não certificam erro factual. Fontes internas e IDs ambíguos não servem para certificar timestamps do artigo.

`resolve_evidence(job, id)` aceita ID de segmento ou cue, retorna cópia dos dados e informa `timing.availability` como `interval`, `point`, `partial` ou `unavailable`. IDs ausentes, ambíguos e material `internal_context_only` não resolvem como evidência. A disponibilidade de cues originais armazenadas é explícita; isso não prova que o provedor reconheceu corretamente a fala.

Jobs antigos continuam legíveis e exportáveis. O formato do mapa de evidências legado permanece igual, evitando invalidar fingerprints gratuitamente. Fontes já salvas no mesmo job não são reprocessadas obrigatoriamente. A nova versão do catálogo SEO só é compatível com a continuação de uma pauta antiga quando os dados e regras anteriores continuam idênticos e a única adição é o diagnóstico local de timestamps. O ciclo mantém sua versão congelada, orçamento, pauta e entregas pagas. Outras mudanças de catálogo mantêm os invalidadores existentes.

Não há migração destrutiva nem alteração de esquema SQL. A inicialização existente registra o novo catálogo SEO de forma aditiva quando a aplicação for instalada. Revisões e histórico de fontes são preservados. Substituição manual remove metadados da extração anterior e mantém o artigo salvo disponível até uma geração explicitamente solicitada.

## Cache e limitações de recuperação

Novas extrações exigem versão/opções compatíveis e cues originais estruturadas no cache normalizado. Cache antigo não é renomeado como fonte íntegra. A leitura compatível antiga continua disponível; a opção de agregação participa da seleção. O remapeamento atualiza IDs próprios de fonte, segmento e cue e suas referências, conservando identificadores opacos do provedor e histórico de origem. Jobs de origem não são alterados.

O adaptador Supadata novo usa `supadata-rows-v2`. Resultados pagos antigos persistiam rows já simplificadas e podiam ter duração desconhecida convertida para zero. Na leitura, uma cópia em memória recebe `provider_cache_legacy` e warning `legacy_provider_metadata`; zero ambíguo volta a desconhecido, enquanto intervalos positivos são conservados. O ticket/result original não é reescrito, invalidado ou reenviado. Isso permite reaproveitar a aquisição existente sem alegar recuperação de metadados perdidos.

Informação apagada antes deste patch não é reconstruída. A disponibilização de texto original refere-se ao material efetivamente recebido pelo normalizador: resultados antigos do adaptador não contêm o payload integral histórico do provedor. Uma nova aquisição de fonte é uma ação explícita, sujeita às opções e custos já existentes, e não uma migração automática.

## Verificação e evidências

As versões locais usadas são Python 3.12.10, pytest 9.1.1 e Node.js 22. As evidências ficam em `.local/p0-01-integridade/`, sem segredos no relatório versionado. Os resultados finais e os casos individualizados são registrados em [evidências de aceite](p0-01-integridade-fontes-aceite.md).

| Cobertura | Arquivo/comando |
| --- | --- |
| Quatro regressões e parser/qualidade/proveniência/cache/exportação/contexto | `tests/test_source_integrity.py` |
| Resolver, ambiguidades, fontes internas, não mutação, projeção de IA e remapeamento | `tests/test_source_provenance.py` |
| Sintaxe versus localização, frações, lacunas, limites, dados incompletos e contexto interno | `tests/test_source_timestamps.py` |
| Substituição manual válida e rejeição sem alterar fonte, artigo, revisão ou histórico | `tests/test_manual_source_integrity.py` |
| Cache Supadata legado/novo, ticket pendente e ausência de reenviar aquisição | `tests/test_supadata_cache_integrity.py` |
| Continuação da pauta congelada e invalidadores precisos | `tests/test_source_legacy_cycle.py` |
| Comparações ambíguas e marcação HTML reconhecida | `tests/test_transcript_math_markup.py` |
| Renderização real dos cards, warnings globais e segurança | `node --test tests/frontend_export.test.cjs tests/frontend_sources.test.cjs` |
| Regressões completas, cancelamento, retomada, falhas, orçamento e WordPress simulado | `.venv/Scripts/python.exe -m pytest -q` |
| Sintaxe frontend | `node --check` para os cinco arquivos JavaScript públicos |
| Construção da imagem | Workflow `.github/workflows/ci.yml`: `docker build -t seo-master-premium:ci .` |

Os testes de integração usam bancos temporários e provedores simulados. Um vídeo continua válido; não há exigência de corpus de 12 vídeos para gerar. Diagnósticos temporais/editoriais não bloqueiam exportação. A regra local não certifica o significado da afirmação, não analisa quadros e não adiciona uma rodada de IA. O fluxo WordPress pendente, idempotência e reconciliação contra edição externa permanecem cobertos pelas regressões existentes.

## Custo incremental observado

Não foram feitas chamadas pagas na validação. O número de chamadas de IA da aplicação não cresce por causa deste patch; orçamento, ledger, reservas e limites continuam nas fronteiras existentes. Não foram medidos tokens faturáveis, fatura, latência de geração, CPU do servidor ou custo real por artigo em produção.

Um benchmark sintético offline usa 700 cues contínuas, 93.800 caracteres de fala, sete repetições e a função antiga isolada do baseline. As duas versões produzem 100 segmentos. O arquivo `benchmark-result.json` registra tempo, alocação Python e bytes JSON, e o script `benchmark_integrity.py` permite reprodução. Os valores finais estão no relatório de aceite.

Armazenar cues originais aumenta o JSON local e a memória transitória. A projeção de IA elimina o texto bruto repetido e intervalos redundantes, mantendo referências e metadados úteis. Ela pode ser maior que o payload anterior por causa dos metadados novos. Bytes JSON não equivalem a tokens, tamanho total do SQLite ou cobrança; não se promete redução de preço a partir dessa medição.

## Liberação e rollback

1. Revisar o PR e exigir as verificações Python, JavaScript e Docker no commit selecionado. Produção/staging não foram alterados por esta execução.
2. Antes de uma futura atualização do serviço, realizar backup consistente de `/data`, incluindo SQLite/WAL, mídia e chaves sob guarda segura. Esses materiais não devem ser publicados em Git ou incluídos no relatório.
3. Testar inicialmente em ambiente isolado com dados copiados, credenciais externas desabilitadas e artigo legado exportável. Não publicar no WordPress como smoke test.
4. Manter `TRANSCRIPT_AGGREGATE_CUES=1` no modo padrão. Se houver regressão no agrupamento, usar `0` em uma liberação autorizada: apenas novas normalizações deixam de agrupar; parser seguro e cues originais continuam preservados.
5. Monitorar erros de ingestão, tamanho de jobs, tempo local, misses de cache e recusas por contexto. Cache normalizado com opção diferente não é reutilizado como se fosse equivalente. Fontes de jobs existentes permanecem intactas.
6. Para rollback de código, restaurar a imagem previamente verificada e preservar o volume. Nenhuma restauração de dados é realizada por `git revert`. Não tratar o normalizador antigo com perda de informação como fallback seguro para novas aquisições; suspender novas gerações ou aplicar correção que conserve o material bruto.

Os leitores anteriores utilizam os campos legados mantidos; os testes verificam exportação e retomada de dados antigos. Compatibilidade com cada imagem histórica não foi testada individualmente. Não houve ensaio de restauração do volume real de produção, implantação Easypanel ou teste de provedor externo nesta etapa.

## Riscos e próximos passos

- Texto misturado com HTML pode ser intrinsecamente ambíguo. Construções técnicas reconhecidas são conservadas e o material original fica disponível; o normalizador não comprova semântica matemática.
- Identidade/confiança desconhecida e áudio mal reconhecido não passam a ser conhecidos por normalização. Whisper continua sem diarização inferida.
- Separar intervalos materialmente distantes pode aumentar o número de segmentos e os IDs no contexto; o limite de contexto existente recusa antes de chamar o provedor, sem truncamento silencioso.
- A futura Inteligência Editorial Crítica pode reaproveitar o resolver, metadados, warnings, mapa compacto, histórico e controles financeiros. Julgamento semântico ou análise visual exige implementação e autorização específicas em etapas posteriores.
