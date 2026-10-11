# Referências incompatíveis no planejamento — diagnóstico e correção

Trabalho: `eaff7ddb70384c8d9926f680db062ae6`, em `premium.vorte-x.xyz`. A consulta autenticada ao trabalho, artefatos e equipe confirmou a falha no planejador, depois de uma extração concluída. A investigação não retomou a geração em produção, não acionou provedores pagos e não alterou artigos nem configuração de produção.

## Evidências observadas

- Três vídeos; apuração válida com os itens `v1i1`, `v2i1` e `v3i1`.
- Cinco pendências abertas de transcrição; nenhuma pendência de comparação.
- Uma extração concluída e duas execuções do planejador com `error_reason=unknown_reference`. A recuperação automática da mesma etapa já foi consumida.
- Nenhum plano nem artigo novo gravado. O artefato de apuração e seu checkpoint permanecem salvos.
- O controle financeiro informa US$ 0,145879 consumidos, incluindo US$ 0,096339 nas duas respostas rejeitadas do planejador. São estimativas registradas pela aplicação, não o extrato do provedor. O modelo dessas chamadas foi `gpt-4o`.
- Existe também uma execução de extração interrompida por reinício, ainda registrada como `running`, antes da extração concluída. Seu consumo externo não pode ser inferido dos três registros de uso disponíveis.
- O EasyPanel informa branch `main`, commit `2ae42d821087631b2c6e2218c4f6b970d69bb737`. Esse commit precede P0_01, P0_02, P1_01, P1_02 e P2_01 presentes no checkout. A informação é do serviço configurado no painel; não houve inspeção do filesystem do contêiner.

O recibo sanitizado está em [incident.json](evidencias/incidente-referencias/incident.json). Os snapshots completos ficam somente em `.local`, fora do Git, e não acompanham a entrega.

## Lacuna reproduzida

`EditorialPlan.issue_priorities[].issue_id` aceitava qualquer string no esquema estruturado enviado ao modelo. `workflow.validate_plan` aceita somente pendências abertas com origem `comparison`. Portanto, a IA podia devolver uma pendência de transcrição ou ID de trecho, satisfazer o esquema e depois receber a rejeição `unknown_reference`. No planejamento consolidado `PlanStructure`, o inventário vazio também deixava a lista irrestrita.

A reprodução usa o SDK instalado e transporte HTTP em memória. Não requer chave real nem gera cobrança. As respostas rejeitadas daquele trabalho não foram armazenadas, nem há diagnóstico do campo no registro antigo: o campo e o ID exatos do incidente permanecem **não verificáveis**. A lacuna e a mensagem correspondente foram reproduzidas; isso não constitui recuperação das respostas originais.

## Comportamento corrigido

O esquema do planejador permite prioridades somente para IDs de comparações abertas. Sem comparações abertas, `issue_priorities` exige lista vazia. O planejamento consolidado conserva a avaliação obrigatória de cada comparação elegível e rejeita chaves extras, inclusive na validação local. Alertas de transcrição continuam visíveis e abertos; não são convertidos em prioridades de comparação.

As instruções explicam a distinção entre IDs de pendências, informações e trechos. O coordenador mantém a validação posterior, rejeita entregas inválidas sem filtrá-las e registra um diagnóstico com o escopo da referência, sem copiar o ID inválido nem o texto devolvido pelo modelo.

A versão da aplicação passa para `1.5.28`. Versões de agentes e fluxo permanecem iguais: esta correção não modifica a interpretação da extração e não deve invalidar os checkpoints pagos dessa etapa.

## Validação e retomada

`tests/test_plan_issue_references.py` cobre o esquema efetivamente serializado, inventários vazios/mistos, pendências fechadas e transcrições, preservação das pendências e prioridades de comparação válidas. `tests/test_planner_reference_resume.py` reproduz duas falhas com o contrato anterior e retoma com o contrato corrigido: conserva ciclo, extração, chamadas, recuperação consumida e lançamentos financeiros; conclui planejamento, redação e revisão sem reextração. A transcrição incerta continua marcada e o artigo pode terminar em `needs_review`, com exportação disponível.

Uma verificação adicional utilizou uma cópia local do trabalho real e dos registros de equipe em SQLite isolado. `inputs_hash`, dependências de apuração e ciclo permanecem compatíveis com o código atual; `engine.start(..., 'resume')` preserva os checkpoints e o contador de chamadas. Essa verificação não executa o restante da geração nem prova o resultado de uma nova resposta real do modelo.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_reference_contracts.py tests/test_delivery_contracts.py tests/test_strategy_contract_integrity.py tests/test_response_recovery.py tests/test_plan_issue_references.py tests/test_planner_reference_resume.py
git diff --check
```

Resultados finais e CI devem ser consultados na pull request da correção. O workflow integral verifica Python, frontend e build Docker. Testes com transporte em memória comprovam contratos e retomada; a qualidade de um artigo produzido após o deploy continua dependendo de geração e avaliação reais.

## Operação

Primeiro disponibilizar uma versão que contenha a correção e as dependências anteriores no serviço. O deploy não foi realizado nesta investigação. Conferir commit e conclusão do build no EasyPanel antes de usar **Retomar** no trabalho existente. Evitar criar outro trabalho ou iniciar novo ciclo se a intenção for reutilizar a apuração salva; manter fontes e briefing.

A retomada realiza novas chamadas cobradas para as etapas ainda incompletas e conserva o orçamento acumulado. Não elimina as cinco incertezas de transcrição. Se a referência incompatível voltar a ocorrer, conferir `validation_errors.reference_scope` no registro de execução; o diagnóstico novo identifica qual fronteira rejeitou a entrega.

Rollback de código exige restaurar o commit anterior; não apagar banco, artefatos, transcrições ou lançamentos financeiros. Voltar à versão anterior reintroduz a lacuna do contrato, portanto não é uma forma de concluir o trabalho interrompido.
