# Documento histórico

Esta especificação descreve a arquitetura anterior. O fluxo ativo está documentado em [Migração Video-First](migracao-video-first.md), com quatro papéis e no máximo oito chamadas, sem evidências web ou rodadas editoriais automáticas.

# Redação em equipes — versão 1.4.0

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
- Contexto e desenvolvimento em cada parágrafo, ligação entre ideias e início, meio e fim do artigo são exigidos no planejamento, redação, edição e revisão. Quebras que impeçam a compreensão retornam à redação; preferências de ritmo e transições são avisos. A regra não impõe comprimento de parágrafo nem títulos fixos.
- Rodadas extras configuráveis (0–3) e máximo de chamadas do coordenador (12–400). Padrão novo: uma rodada extra e 120 chamadas; perfis existentes conservam seus valores. Isso não é um limite monetário do provedor.
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

### Coerência dos parágrafos — versão editorial 4

Cada parágrafo deve apresentar uma ideia identificável, com contexto suficiente e
desenvolvimento pertinente. O contexto pode vir do título ou do parágrafo anterior,
sem precisar ser repetido. Frases, exemplos e condições precisam ter uma ligação
compreensível com o assunto da seção e com as ideias que as antecedem.

O artigo deve ter início que situe o tema e a pergunta, meio que desenvolva a
resposta com base nas fontes e fim que encerre o raciocínio. Essa organização não
exige títulos como “Introdução” e “Conclusão”, uma quantidade fixa de frases nem
um fechamento que apenas repita o texto. Não se inventam causas ou relações entre
fontes para dar aparência de continuidade.

Leitor crítico, revisores e editor-chefe recebem essa orientação. Uma falha que
prejudique materialmente a compreensão é bloqueante e encaminhada à redação;
preferências de ritmo e conectivos são avisos. Um parágrafo curto pode estar completo.
Essa avaliação é feita pela IA e não constitui uma garantia automática de qualidade.

Novas gerações, melhorias e revisões usam as instruções atualizadas. A versão dos
agentes passa a 2 para impedir retomada de entregas antigas com as novas instruções.
O padrão de ritmo vale para perfis novos; perfis personalizados mantêm seus valores,
e a regra compartilhada de coerência é enviada a todas as etapas editoriais.

As instruções ficam versionadas no código, conforme a [documentação oficial da
OpenAI sobre prompting](https://developers.openai.com/api/docs/guides/prompting).

Testes automatizados usam o provedor simulado e exercitam coordenador, persistência, contratos, rotas e validações reais. O ensaio com OpenAI utiliza um material sintético de café coado, identificado como fixture, sem atribuir uma extração de vídeo real. A avaliação inclui afirmações acrescentadas durante a escrita, citações inválidas, retomada e correção após revisão.


## Apuração por evidências — versão editorial 5

O fluxo completo foi implementado com extração e conferência por blocos, comparação conferida, plano editável, pesquisa de páginas originais, redação por seções quando necessária e revisão com cobertura explícita. Os agentes usam a versão 3 das instruções. Consulte [a documentação de operação e avaliação](fluxo-evidencias.md).
