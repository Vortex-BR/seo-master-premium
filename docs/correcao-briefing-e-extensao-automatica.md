# Correção da entrega editorial — 1.5.30

## Problemas corrigidos

Uma entrega em extensão automática (`target_words=null`) podia terminar com a
correção automática em `failed/TypeError`: o cálculo da previsão multiplicava
uma meta ausente. A revisão geral também podia aprovar o briefing apesar de
registrar omissões relevantes. Além disso, planejamento e redação recebiam
apenas fontes selecionadas pelos itens extraídos, deixando detalhes perdidos na
extração fora do alcance da primeira redação.

Novos ciclos registram `brief_contract_version=1`. Planejamento e redação recebem
as fontes originais disponíveis, dentro dos limites de contexto existentes.
O modelo escolhe a estrutura conforme a pergunta, sem quantidade fixa de títulos,
listas ou palavras. Um tutorial organizado com subtítulos não precisa ser numerado.

## Conferência e correção

Cada pergunta, orientação e exclusão tem uma avaliação obrigatória com referência
ao artigo real. `met`, `partial`, `missing`, `violated`, `conflict` e `uncertain`
aparecem na interface em português. Títulos sozinhos não comprovam resposta.

A revisão compara também unidades de sentido de cada referência original:
ações, motivos, condições, cuidados e critérios. O formato enviado ao provedor
obriga uma avaliação por referência, usando chaves selecionadas; o servidor copia
as citações literais e associa os detalhes à fonte correta. Não se pede ao modelo
que reescreva uma citação. O parecer geral é reconciliado com as avaliações e
com a coerência factual, evitando aprovação simultânea com lacunas ou incertezas.

Exclusões legítimas não viram instruções para reinserir conteúdo proibido. Um item
inteiro excluído pode ser retirado mesmo quando compartilha a fonte com conteúdo
pertinente. Exclusões de fontes/itens ainda utilizados no corpo não são confirmadas.

O ciclo admite uma correção automática, com redação e auditoria independentes.
Ela precisa apresentar melhora confirmada e preservar detalhes corretos,
requisitos atendidos e evidências. Falha, interrupção ou proposta sem melhora
preservam a entrega anterior e deixam um aviso explícito; não provocam repetição
automática de chamadas incertas.

## Orçamento, cache e compatibilidade

O modo automático não cria uma meta artificial de palavras. O preflight mantém
limites de saída, ferramentas, saldo, margem de segurança e reserva da revisão.
Se a estimativa offline impedir uma correção que ainda pode caber no saldo,
consulta a contagem de entrada do provedor antes de recusar. A previsão de
crescimento conserva uma margem adicional; o candidato é medido novamente antes
da revisão. Falha da contagem mantém o cálculo conservador, sem ampliar o teto.

Os formatos compartilham definições, removem títulos automáticos redundantes e
compactam a representação de novos contextos sem truncar conteúdo. O nome do
formato usa um identificador curto aceito pelo provedor. Campos reais `title`,
regras, enumerações e descrições continuam presentes.

Ciclos anteriores mantêm seu contrato e cache de etapas pagas. A retomada explícita
pode recuperar o antigo `TypeError` de preflight em extensão automática somente
com hashes e versões idênticos e nenhuma tentativa de correção já enviada.
Falhas novas registram a fase e não são confundidas com esse defeito antigo.
O deploy não regenera nem revisa artigos existentes.

## Evidências e limites

Testes cobrem extensão automática, correção de violações/omissões, preservação,
falhas e retomada sem repetição de cobranças, exclusões parciais, referências
obrigatórias, citações copiadas pelo servidor, orçamento e contextos com 90 trechos.
O transporte de teste usa o SDK real com respostas locais; isso valida integração
e contratos, sem medir a qualidade semântica de um modelo real.

Foi feita uma avaliação real isolada com uma transcrição original e benigna sobre
ajuste de cadeira, usando `gpt-4o` e teto total de US$ 0,60. A primeira geração
registrou US$ 0,142718. A correção recuperou preparativos omitidos, chegando a
US$ 0,235809. A leitura humana encontrou cuidados ainda omitidos apesar do parecer
positivo, motivando a conferência por detalhe e o formato final de referências.
Uma tentativa intermediária expôs a fragilidade de citações livres; esse formato
foi substituído pela seleção de referências. O consumo total do experimento foi
US$ 0,390597 no registro conservador da plataforma, incluindo tentativas pagas.

A contagem do provedor aceitou o formato final. A última geração de revisão foi
recusada com HTTP 429 (`RateLimitError`); a reserva foi liberada e não houve nova
geração contabilizada. Portanto, a aprovação semântica do fluxo final completo
pelo modelo real permanece não verificada. Os valores acima são registros da
plataforma, não uma conferência com a fatura do provedor nem previsão universal
de custo por artigo. Nenhuma geração foi executada em produção nesta validação.

A conferência usa a transcrição disponível. Completude do vídeo inteiro e imagens
não analisadas continuam explicitamente não verificadas. Avaliações semânticas
dependem do modelo: os contratos reduzem omissões e incoerências, mas não
constituem garantia de que qualquer artigo terá qualidade perfeita.

## Implantação e rollback

Publicar a revisão no `main`, aguardar os checks de Python, frontend e Docker e
implantar o mesmo SHA no EasyPanel (`mj/seo-master-premium`). Confirmar `/health`
com versão 1.5.30, acesso autenticado e preservação dos artigos, fontes, briefings
e registros de consumo existentes. A migração não exige alteração do banco.

Rollback: restaurar o commit anterior `f7c1017e96683efca39d02853e1d62b8884256e9`
(1.5.29) preservando o volume `/data`. Evitar retomar sob versão anterior um ciclo
novo iniciado com contratos posteriores; as entregas salvas permanecem legíveis.
