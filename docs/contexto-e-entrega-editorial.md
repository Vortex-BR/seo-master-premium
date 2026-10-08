# Contexto e entrega editorial Video-First

O contexto de cada etapa conserva a proveniência do vídeo e separa esclarecimento interno de evidência para publicação. O artigo é escrito inteiro e revisto inteiro; materiais extensos não ativam redação por seções nem ciclos adicionais de agentes.

## Materiais por etapa

| Etapa | Material principal |
| --- | --- |
| Extração | Cópias limpas das transcrições de todos os vídeos, com IDs e timestamps originais |
| Planejamento | Insights do criador, vínculos de origem e pergunta do briefing |
| Redação | Percurso completo do plano, insights, ressalvas, referências de vídeo e atribuição ao criador |
| Revisão factual | Todos os trechos do artigo, metadados e transcrições originais, sem aprovações anteriores |

A limpeza oral produz uma visão derivada. O armazenamento mantém o texto original e todos os seus metadados. Quando a fala mistura um pedido de engajamento com uma explicação técnica, a limpeza conserva a explicação. Analogias, experiências e alertas continuam vinculados aos segmentos que os sustentam.

A revisão recebe as transcrições originais, inclusive material sem citação no artigo, para conferir ressalvas distantes e afirmações novas. O conteúdo visual ausente permanece uma limitação; não se presume que uma cena foi analisada.

## Isolamento da pesquisa

Todo material web usa `internal_context_only=True`. Esclarecimentos validados sobre expressões presentes na fala são enviados somente como `agent_background_knowledge`. Páginas, notas brutas e IDs de pesquisa não entram no inventário de evidências do planejador, redator ou revisor.

A pesquisa não cria informações, seções ou recomendações para publicação e não encerra pendências sobre o áudio original. Um termo compreendido pelo modelo não autoriza completar a explicação do criador com conhecimento externo.

Na etapa específica de entendimento web, os esquemas permitem apenas termo, esclarecimento, referências aos segmentos de vídeo e a marca de uso interno. Campos extras de evidência ou seções são rejeitados. Falhas de pesquisa mantêm o fluxo apoiado nos vídeos.

## Limite antes do envio

A preparação mede a solicitação completa: materiais, instruções e esquema de resposta. Os contratos das etapas lineares são preparados antes de criar a tentativa de execução e antes de abrir o cliente do provedor.

O limite em caracteres é uma proteção local, não uma medição exata da janela de tokens do modelo. A saída tem seu próprio limite. O perfil permite contexto entre 30 e 240 mil caracteres, com padrão de 90 mil; o inventário máximo de transcrições pode exceder o contexto efetivamente disponível.

Uma solicitação que não cabe é recusada sem cobrar chamada, cortar evidências, omitir o fim da transcrição ou iniciar um fallback por seções. O usuário pode reduzir a pauta ou ajustar o contexto para o modelo utilizado. Essas ações não aumentam o teto de chamadas.

Extração, pauta, redação e revisão usam uma chamada cada no caminho normal. O teto de 8 inclui a pesquisa opcional, sua reserva de até duas chamadas de ferramenta e recuperações limitadas de transporte ou formato. Não há agentes adicionais de leitura, edição, SEO ou decisão para ampliar esse contexto.

## Contratos e referências

IDs de segmentos, informações extraídas e passagens do artigo têm funções distintas. Os esquemas restringem as referências ao inventário da própria tarefa; um inventário vazio não permite fabricar IDs.

Na redação, cada bloco Markdown declara suas fontes em uma lista restrita. O servidor insere as referências; IDs digitados diretamente dentro do texto não substituem essa seleção. A resposta entrega corpo e metadados juntos.

Na revisão, cada passagem do artigo exige uma avaliação. Uma passagem factual aprovada precisa de evidência; uma passagem sem apoio não pode contar como informação desenvolvida. Quando a IA seleciona uma opção de evidência, o servidor copia o trecho diretamente do original. Não há necessidade de reproduzir sua grafia por geração.

IDs válidos e citações literais comprovam origem, não veracidade nem apoio semântico. A revisão verifica se a fala sustenta toda a afirmação, incluindo condições, números, atribuições, analogias e alertas. Lacunas e respostas incompletas são registradas ou rejeitadas, sem inventar resultados para preencher cobertura.

## Diagnóstico local e entrega durável

SEO e formatação são verificados em código, sem chamadas extras. O diagnóstico cobre presença de H2, hierarquia dos subtítulos, título e metadescrição, frequência da palavra-chave, integridade das referências e regras Video-First.

A atribuição exigida ao criador na introdução, clichês artificiais e referências externas podem bloquear a aprovação. Marcações de tempo são incentivadas quando disponíveis; a presença de uma marcação não comprova sua exatidão.

Repetições extensas, resíduos de formatação e problemas na organização de um tutorial também são observáveis localmente. Extensão acima da meta e parágrafos longos geram avisos editoriais. Numeração de uma lista não comprova a correção do procedimento, e frequência de palavra-chave não define uma densidade ideal universal.

A versão paga do rascunho fica salva antes da conferência factual. A auditoria factual completa, com suas evidências, bloqueios e cobertura, é persistida antes da finalização das verificações locais. Uma falha posterior mantém esses artefatos disponíveis para inspeção e retomada.

O resultado factual não é substituído por uma decisão de editor-chefe. Bloqueios locais também não são dispensados por uma aprovação do modelo. Preview e exportação continuam disponíveis para o rascunho; publicar exige a aprovação editorial específica do fluxo WordPress.

## Cache, histórico e validação

Uma entrega concluída é reutilizada somente com a mesma identidade de entrada e dependências compatíveis. O cache precede a cobrança: repetir a conferência da mesma versão no mesmo ciclo não consome outra chamada. Mudar o plano invalida a identidade da revisão; mudar o artigo exige conferir a nova versão.

Artigos, transcrições e artefatos históricos permanecem armazenados. Novos ciclos e regenerações de ciclos antigos incompatíveis usam Video-First. Variáveis de ambiente legadas não restauram o fluxo anterior.

Os testes usam banco isolado, provedor simulado e SDK real com transporte local para verificar contratos, persistência, preflight, orçamento e recuperação. Conferir o comportamento do software não mede a qualidade de uma nova resposta real; essa conclusão exige comparar o artigo com as fontes originais.
