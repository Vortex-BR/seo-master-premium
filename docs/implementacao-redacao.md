# Redação em equipes — versão 1.1.0

## Entregue

- Quatro setores, três papéis por setor, com contratos estruturados e responsabilidades em `app/editorial/agents.py`.
- Coordenação persistida em `app/editorial/engine.py`; o app encaminha entregas e problemas concretos. As execuções são sequenciais na fila existente, adequada à instância única atual.
- Perfil editorial versionado, congelado junto ao modelo e pacote documental no começo de cada ciclo. Mudanças globais valem para próximos ciclos.
- Biblioteca de 12 fichas interpretadas, incluindo Google Search, experiências de IA do Google, análise SEO/legibilidade Yoast e escolhas da marca. URLs oficiais, data de revisão, contexto, exemplos, exceções e origem disponíveis na interface. Recuperação por setor e SQLite FTS5.
- Tabelas incrementais `editorial_profiles`, `agent_runs`, `agent_messages`, `change_sets`, `seo_packages` e `seo_rules_fts`. Entregas e estado dos setores ficam associados às execuções e ao JSON do trabalho; não é necessária uma tabela adicional para pareceres duplicados.
- Histórico de chamadas com papel, modelo, contexto, regras consultadas, tokens, situação e entregas. Nenhum segredo vai ao relatório ou ao repositório público.
- Propostas localizadas e ordenadas, com hash do artigo e das fontes, comparação, aplicação, rejeição e desfazimento. Aplicação e registro do histórico ocorrem na mesma transação SQLite. Edições antigas não sobrescrevem versões atuais.
- Revisores escolhem citações entre trechos reais do artigo através do esquema estruturado. IDs documentais e de evidência são conferidos no backend. Uma entrega inválida pode ser corrigida uma vez automaticamente, respeitando o orçamento de chamadas.
- O editor de voz recebe pedidos do leitor crítico; o editor SEO recebe estratégia e análise Yoast; o editor-chefe recebe parecer factual, de leitura e questões ainda abertas. Pedidos de apuração podem iniciar pesquisa complementar quando habilitada.
- Revisão factual após mudanças SEO. Decisões do editor-chefe não dispensam validações de código ou problemas factuais identificados.
- Rodadas extras configuráveis (0–3) e máximo de chamadas do coordenador (12–60). Padrão: uma rodada extra e 24 chamadas. Isso não é um limite monetário do provedor.
- Retomada reutiliza entregas concluídas dentro do mesmo ciclo. Uma proposta já aplicada não é reaplicada após reinício. Alterações manuais invalidam o ciclo para retomada automática.
- Telas “Equipe e voz editorial” e “Equipe editorial”, adequadas a desktop e celular.

## Modos

| Ação | Trabalho executado |
| --- | --- |
| Criar / Gerar novamente | Fontes e os 12 papéis, pesquisa opcional, correções e revisão |
| Melhorar este artigo | Redação/voz, SEO e qualidade sobre o artigo salvo; não extrai novamente os vídeos |
| Revisar artigo | Os três papéis de qualidade, sem alterar o texto |
| Retomar geração | Reutiliza o modo e os checkpoints do ciclo interrompido quando continuam válidos |
| Aplicar / Desfazer proposta | Transação local, sem chamada OpenAI; exige nova revisão |

## Decisões de escopo

A implementação usa a documentação interpretada e verificadores próprios. O motor JavaScript oficial do Yoast, sincronização de campos do plugin, inventário de sites e relatórios Search Console continuam fora desta entrega. O usuário pode utilizar toda a redação sem conectar um WordPress.

Documentos não são atualizados automaticamente a partir da internet. Uma atualização exige revisar as fichas, incrementar a versão em `app/seo/rules.json` e distribuir o novo pacote. Versões anteriores permanecem no banco para retomada e auditoria. Uma fonte externa nunca altera as instruções de um agente por conta própria.

As chamadas compartilham o modelo configurado no app, com tarefas e contextos distintos. Os nomes representam papéis efetivamente executados; não há pontuação “Triple A” inventada. Correlação entre modelos ainda é possível: qualidade é conferida por testes e avaliação editorial.

## Verificação

Testes automatizados usam o provedor simulado e exercitam coordenador, persistência, contratos, rotas e validações reais. O ensaio com OpenAI utiliza um material sintético de café coado, identificado como fixture, sem atribuir uma extração de vídeo real. A avaliação inclui afirmações acrescentadas durante a escrita, citações inválidas, retomada e correção após revisão.
