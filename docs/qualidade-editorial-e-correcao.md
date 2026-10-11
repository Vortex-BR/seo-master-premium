# Qualidade editorial e correção automática — 1.5.29

O fluxo anterior verificava referências e afirmações do artigo, mas podia entregar um resumo que omitia informações relevantes do vídeo. A extração classificava todas as contribuições como conceitos, e a cobertura final considerava somente os itens previamente selecionados. A nova conferência compara também cada trecho original com o corpo entregue.

## Extração e arquitetura do artigo

- A extração preserva a natureza da informação, métodos, condições, quantidades, restrições, motivos e sequência quando presentes na fala. Não há uma quantidade mínima artificial de insights.
- A contagem de trechos referenciados é um diagnóstico de procedência. Ela não comprova cobertura semântica nem a completude da transcrição do vídeo.
- O título do artigo é o H1. As seções são H2; subdivisões são H3; `presentation.nested_subheadings` permite H4 ligados a um H3 existente. H4 é opcional.
- Listas atendem materiais, verificações e sequências curtas. Etapas que exigem explicação têm desenvolvimento próprio; um marcador ou subtítulo isolado não comprova qualidade.
- A redação recebe os trechos citados completos. A conferência final recebe também trechos não citados, plano e lacunas dos vídeos.

## Conferência e correção

A revisão mantém duas avaliações separadas: apoio factual das passagens do artigo e cobertura das informações relevantes de cada trecho original. A segunda registra `complete`, `partial`, `missing`, `excluded` ou `uncertain`, com identificação dos detalhes ausentes. Esses estados são avaliações do revisor sobre as transcrições disponíveis; não são prova independente de que todo o vídeo foi transcrito.

Novos ciclos de geração podem fazer uma correção automática quando houver omissões, desalinhamento, afirmações sem apoio ou defeitos estruturais corrigíveis. O corretor recebe o texto salvo, apontamentos concretos, plano e fontes originais. Uma segunda revisão confere o candidato e o compara ao texto anterior para identificar perda de informação correta.

A instalação exige melhora observada, preservação dos itens já desenvolvidos e ausência de regressão por fonte. Reduzir um indicador agregado não autoriza trocar uma informação perdida por outra. Nenhuma versão candidata substitui o artigo salvo antes de sua conferência. Artigo, parecer, histórico e resultado da correção são instalados na mesma transação SQLite.

## Custos e retomada

- Uma primeira entrega aprovada continua com quatro chamadas: extração, planejamento, redação e conferência.
- A correção acrescenta no máximo uma redação e uma conferência por ciclo. Essas chamadas opcionais não têm recuperação automática de transporte/formato.
- As quatro etapas principais conservam sua recuperação limitada já existente. A estimativa passa a representar até dez chamadas considerando essas recuperações e a correção opcional; o teto financeiro e o limite de chamadas continuam prevalecendo.
- Antes da correção, um cálculo local considera instruções, materiais, esquema e saída máxima. A reserva da redação mantém uma margem monetária explícita para a conferência.
- O candidato é medido novamente antes de conferir. Se ultrapassar o saldo ou o contexto, fica preservado como artefato; o texto anterior continua disponível. A margem antecipada é uma estimativa conservadora, não garantia de que qualquer candidato arbitrariamente maior caberá.
- Entregas já pagas são reutilizadas. Uma chamada opcional interrompida ou de cobrança incerta não é repetida silenciosamente. Candidato e conferência concluídos podem ser instalados após retomada sem novas chamadas, inclusive com limite de chamadas esgotado.
- Consultar ou revisar um artigo não inicia correção automática. Ciclos anteriores sem `quality_repair_version` não recebem novas chamadas opcionais pela simples atualização do servidor.

## Evidências e operação

Os artefatos `quality_repair_before`, `quality_repair_candidate`, `quality_repair_review` e `quality_repair` preservam o texto anterior, candidato, diagnóstico e decisão. Versões anteriores permanecem no histórico habitual. A entrega continua exportável mesmo quando houver incertezas ou quando não for possível concluir uma correção.

Os testes usam casos permitidos de montagem de móveis e outros tutoriais: detalhe omitido dentro de um único insight, estrutura incompleta, perda de informação correta, correção pior, falha de serviço, orçamento insuficiente, interrupção e retomada. Provedores simulados verificam a orquestração e seus contratos; não substituem uma avaliação editorial de respostas de modelos reais.

Não há migração destrutiva, troca de modelo ou alteração de limite financeiro. Campos novos têm leitura compatível com extrações, planos e pareceres existentes. Para rollback do código, retornar ao commit anterior `a1c3f5ddfc0212b89b42e9d9ffc7ef6154a0abf3`; manter o volume persistente e o histórico financeiro, sem restaurar um banco antigo sobre cobranças novas.
