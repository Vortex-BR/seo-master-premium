# Coleta reproduzível do baseline

O piloto usa um vídeo principal real antes de alterar prompts ou algoritmos. A ferramenta `scripts/premium_baseline.py` congela evidências existentes, identifica pendências e prepara fichas humanas. Lê snapshots JSON ou trabalhos de um SQLite existente; não gera artigos, acessa provedores ou inicializa o banco. Nenhuma validação desta ferramenta altera a disponibilidade de exportação do aplicativo.

## Estado inicial

`amostra.json` registra as três URLs enviadas pelo usuário. `cVnRvZ8uMCo` é o caso principal obrigatório; `uibZD5Dgrao` e `OJeR5Ueb76o` são testes opcionais e independentes. O usuário já declarou produção própria e links destinados somente a testes; essa declaração está registrada em `permission`, com referência à conversa e restrição de uso em testes. Não há nova confirmação de direitos pendente.

Os metadados foram obtidos pelo endpoint público YouTube oEmbed em 09/10/2026; a consulta direta às legendas falhou com `IpBlocked` nos três vídeos. Os registros brutos desta tentativa estão em `.local/premium-source-probe/`. Depois foram encontradas transcrições reais históricas em `.local/transcription-0f495aa7-after.json`, com origem registrada como Whisper local sobre áudio do YouTube. Projeções de uma fonte por caso foram preservadas em `.local/premium-inputs/`, com hashes no manifesto:

| Caso | Papel no piloto | Snapshot de entrada | Segmentos | Caracteres de fala |
|---|---|---|---:|---:|
| `cVnRvZ8uMCo` | Obrigatório | `.local/premium-inputs/cVnRvZ8uMCo-sources.json` | 3 | 2.651 |
| `uibZD5Dgrao` | Opcional | `.local/premium-inputs/uibZD5Dgrao-sources.json` | 22 | 20.243 |
| `OJeR5Ueb76o` | Opcional | `.local/premium-inputs/OJeR5Ueb76o-sources.json` | 7 | 5.800 |

Todos esses segmentos possuem início e fim registrados. Os dados históricos fornecem entradas reais para o piloto; não são artigos gerados na versão atual nem medições novas de transcrição. Títulos e timestamps não certificam duração integral, gênero, locutor, completude visual ou correção factual. Categoria e desafios permanecem desconhecidos sem observação específica.

O banco padrão `data/seo.sqlite3` foi consultado em `mode=ro`: contém zero trabalhos. Isso descreve esse banco local; não comprova ausência de artigos numa implantação remota ou num `DATA_DIR` diferente. Foi verificada somente a presença de configuração/chave OpenAI no banco e no ambiente, com resultado ausente, sem expor valores de segredos. Nenhuma geração atual foi executada: artigo do baseline, modelo/perfil efetivamente usados, custo, tempo e avaliação humana permanecem pendentes.

## Preparar o corpus

1. Manter a declaração de uso já fornecida e registrada no manifesto. A ferramenta exige `permission.status="authorized"`, `basis` e `reference` preenchidos para capturar um caso; os três casos atuais atendem a esse registro. Essa verificação documenta a declaração, sem certificar licença por inferência a partir de URL ou crédito.
2. Usar somente `cVnRvZ8uMCo` no artigo principal. O manifesto fixa `minimum_unique_videos=1`, `minimum_per_category=0`, `required_case_ids=["principal-cVnRvZ8uMCo"]` e `required_challenges=[]`. Os dois casos opcionais não precisam estar completos para o principal ficar pronto. A amostra original de 12 vídeos e seis categorias fica como recomendação futura de diversidade; não existe espera por outros nove vídeos nem mínimo de fontes por artigo.
3. Preservar os snapshots históricos já disponíveis, seus hashes e sua proveniência. Para substituir ou atualizar uma entrada, registrar o novo snapshot e seu hash sem sobrescrever a evidência anterior. Conservar erros, lacunas e incertezas relevantes. Se uma fonte futura não oferecer tempos, registrar essa limitação; a ferramenta sinaliza timestamps incompletos sem inventá-los.
4. Um avaliador registra `essential_excerpts` como objetos `{"source_id":"v1s1","text":"trecho literal"}`, além de `qualifiers`, `examples` e notas. Conferir começo, meio e fim da fala e preservar etapas, experiências, opiniões e ressalvas materiais. As âncoras essenciais são verificadas contra o segmento original; o julgador humano define o que é essencial. Um trecho válido não comprova que o artigo preservou seu significado. “100% baseado no vídeo” define a origem do conteúdo editorial, sem garantir automaticamente ausência de erros ou análise visual.

## Congelar a versão e a execução

Na raiz do projeto, preparar a primeira fotografia antes de gerar o artigo:

```powershell
.venv\Scripts\python.exe scripts\premium_baseline.py
```

Cada execução cria uma pasta exclusiva em `.local/premium-baseline/` com `fingerprint.json`, `manifest.json`, `report.json` e `cases/<id>/human-review.json`. O status inicial será `incomplete`: isso é um resultado da auditoria, não um erro técnico. Nenhuma nota humana é preenchida automaticamente.

O manifesto atual usa `job_json` para os três snapshots de fontes. O script lê esses arquivos sem modificá-los, verifica o `sha256` declarado em `transcript_snapshot` antes de capturar e confere o `job_id`. Um arquivo divergente do hash é rejeitado com diagnóstico. A projeção histórica com fontes disponíveis ainda produz pendências de artigo e execução; sua captura não equivale a um baseline editorial concluído.

`app_sha256` identifica os bytes de todos os módulos, prompts, JSON, HTML, JavaScript e CSS do aplicativo, mais dependências declaradas e Dockerfile. O fingerprint também registra HEAD e estado de trabalho. Assim, as mudanças locais anteriores entram na identidade; HEAD sozinho não representa o aplicativo auditado. O hash não é um backup do código: conservar a árvore correspondente ou um commit/release reproduzível em conjunto com ele.

O fingerprint da árvore de aplicativo auditada permaneceu `7acdaf514b79712f0e0230aae896d3cdc03623639c8408d07f0a254df196f8f2`. Isso identifica o estado atual auditado; não atribui esse estado retroativamente à transcrição histórica nem certifica uma geração que ainda não aconteceu. O relatório também registra `tool_sha256`, identificando a versão exata da ferramenta de coleta.

Gerar o artigo principal com a versão atual, preservando a mesma pauta, uma única fonte, modelo e perfil que serão usados na comparação futura. Os testes opcionais, se executados, geram artigos separados. Usar dados de avaliação separados e respeitar o orçamento existente. A geração continua pelo aplicativo atual; este script não oferece `--live` nem inicia chamadas pagas. Não usar `evaluate_editorial.py --live` como substituto dos vídeos reais: ele chama IA sobre falas sintéticas. A configuração OpenAI atualmente ausente impede uma execução real nessa instalação até haver configuração válida; nenhum custo ou resultado foi preenchido para ocultar essa ausência.

Para cada caso, preencher `job_id` e `run` com:

- `app_sha256`: fingerprint da árvore efetivamente usada na geração, acompanhado do registro da execução. Artigo antigo sem essa informação fica com versão não confirmada; o script não certifica retroativamente sua origem.
- `model` e `profile`: modelo e configuração editorial efetivamente usados, incluindo limites de contexto e orçamento. Credenciais ficam fora do manifesto.
- `wall_seconds`: tempo de parede medido do início ao término/falha da execução. Horário da última edição do artigo não substitui essa medida.
- `usage_start_index` e `usage_end_index`: limites da lista de uso antes/depois dessa execução, para excluir custos de ciclos anteriores. Se ausentes, os totais ficam explicitamente identificados como histórico do artigo.

Para capturar uma execução futura pelo banco, usar um manifesto com `job_id` da execução e sem `job_json` nesse caso:

```powershell
.venv\Scripts\python.exe scripts\premium_baseline.py --database C:\caminho\avaliacao\seo.sqlite3
```

O caminho deve apontar para o banco que contém os IDs informados. Quando `job_json` estiver preenchido, ele tem prioridade sobre `--database`; passar somente o banco não substitui o snapshot histórico do manifesto atual. O script usa uma conexão SQLite `mode=ro`, sem funções mutantes de `app.db`, `store.profile()` ou `spending.summary()`. Banco ausente não é criado. Como alternativa, um snapshot JSON da execução futura pode ser declarado com `job_json`, `job_id` e hash atualizados, preservando o manifesto e as entradas anteriores.

A captura inclui campos selecionados de fontes, apuração, plano, artigo, revisão e cobertura; não copia settings, sessões, senhas, proxies ou o JSON inteiro do trabalho. Cada snapshot tem hash próprio e pasta por caso, evitando colisões entre nomes de casos e relatórios. `exclusive_source=true` verifica que o trabalho contém exatamente uma fonte e que o vídeo do caso está presente; falha se outras fontes tiverem sido misturadas. Essa verificação estrutural não prova, por si só, que todas as afirmações do artigo estão apoiadas na fala.

Custos só são somados quando todas as linhas do intervalo contêm `estimated_usd` numérico. Valor ausente permanece `null`, acompanhado de diagnóstico; não há recálculo com preços supostos. Os valores do aplicativo são estimativas locais, não faturas. Tokens conhecidos são registrados com a quantidade de linhas sem telemetria; transcrição de áudio pode ter custo/duração e nenhum token textual. Uma falha real deve ser mantida no corpus e documentada, não substituída silenciosamente.

## Avaliar e comparar

As fichas humanas preservam a rubrica do documento `04`: fidelidade 30%, utilidade 25%, naturalidade/clareza 20%, precisão/atribuição 15% e SEO/apresentação 10%. Preencher notas 0–5 com justificativa, IDs de fontes e passagens do artigo. Registrar erros críticos em separado: experiência/atribuição inventada, ressalva perdida, fato material sem suporte, reprodução excessiva ou instrução técnica alterada.

Após a evolução, repetir a mesma pauta e o vídeo principal, comparar pares com identidade ocultada quando viável e registrar custo, tempo, falhas e recuperação. Os dois casos opcionais ampliam a comparação quando disponíveis. Notas automatizadas não substituem esse julgamento.

`ready_for_human_evaluation` significa que não há pendências globais e que os casos listados em `required_case_ids` passaram nos requisitos de captura. Pendências dos opcionais permanecem visíveis, mas não impedem esse status. O script continua a exigir artigo completo, versão confirmada da geração, modelo/perfil, intervalo de uso, custo registrado, tempo e trechos essenciais do caso principal. Não significa melhoria ou aceite editorial. A ferramenta sempre deixa `human_evaluation_completed=false` e `demonstrated_improvement=false`; o relatório humano é um artefato posterior. Nenhuma dessas notas bloqueia exportação.
