# Orçamento acumulado por artigo

O perfil editorial define o limite financeiro das solicitações OpenAI vinculadas a um artigo. O padrão é **US$ 1,00**, configurável entre **US$ 0,01 e US$ 1,00**. O servidor valida esse intervalo; o painel não permite autorizar um valor maior.

O saldo pertence ao ID do artigo e acumula todas as suas execuções. Retomar, mudar o modelo, alterar a direção, editar as fontes ou iniciar um novo ciclo do mesmo artigo não renova a cota. Aumentar um limite menor pode liberar somente a diferença até US$ 1,00, conservando os gastos anteriores.

## Escopo

O mesmo ledger contabiliza:

- Extração editorial da fala, planejamento, redação e conferência factual.
- Pesquisa web, chamadas das ferramentas e extração do contexto interno.
- Retentativas e respostas incompletas, quando o provedor já executou a solicitação.
- Geração e edição de imagens, inclusive referências visuais.
- Fallback de transcrição paga OpenAI com `whisper-1`, quando habilitado explicitamente e vinculado ao artigo.

A transcrição padrão usa Whisper local, sem cobrança por minuto de API. CPU, memória, armazenamento, tráfego, proxies e serviços externos como Supadata têm custos próprios, fora deste orçamento OpenAI. O ledger também não consolida chamadas feitas por outras aplicações ou chaves fora desse artigo.

## Prioridade de entrega e limite de chamadas

O fluxo principal faz quatro entregas: **extração → planejamento → redação → conferência factual**. Cada entrega usa normalmente uma chamada. Uma recuperação limitada em cada etapa pode elevar o total principal a oito chamadas; não há um bloqueio fixo na oitava.

O perfil mantém uma proteção auxiliar de **4 a 24 unidades por ciclo**, padrão 24. Essa contagem inclui retentativas e reservas das ferramentas de pesquisa. A migração substitui o antigo teto forçado de oito pelo padrão 24. Ter unidades disponíveis não autoriza ultrapassar o saldo em dólares nem dispara novas rodadas automaticamente.

Novas pesquisas estão desativadas durante geração, planejamento e retomada, inclusive em briefings antigos com `research=True`. O fluxo usa os vídeos fornecidos e as etapas já salvas. Pesquisas históricas continuam contabilizadas e disponíveis para inspeção; o saldo restante nunca autoriza novas buscas.

SEO e formatação são verificados localmente. Cache válido é consultado antes de solicitar outra geração. Salvar edições, revisar o plano manualmente e consultar relatórios não executa geração de conteúdo.

## Reserva antes da chamada

Os valores são calculados com `Decimal` e arredondamento conservador para seis casas decimais. Antes de enviar uma solicitação paga, o sistema estima entrada, teto de saída, ferramentas e margem de segurança, verifica o saldo e registra a reserva em `spend_reservations`.

A operação usa **`BEGIN IMMEDIATE`** no SQLite para serializar reservas concorrentes do mesmo saldo. O registro financeiro fica separado do JSON do artigo. Uma gravação com dados antigos do trabalho não apaga uma reserva nem devolve dinheiro já contabilizado. A importação de uso histórico também é persistida, inclusive quando uma nova solicitação é negada por falta de saldo.

Solicitações textuais usam a consulta de contagem de tokens da Responses API quando disponível, incluindo instruções e contrato. Essa consulta não executa uma geração e usa **timeout de cinco segundos**. Se ela falhar, entradas textuais usam uma estimativa conservadora baseada no tamanho UTF-8 e margem para a estrutura da solicitação. O envio continua condicionado ao teto configurado de saída e ao saldo.

Um modelo sem tarifa cadastrada é recusado antes de enviar a geração. Não se presume preço zero nem se muda o modelo silenciosamente para contornar o controle.

## Estados e incerteza

| Estado | Efeito no saldo |
| --- | --- |
| `reserved` | Guarda o valor antes de enviar a solicitação. |
| `completed` | Contabiliza o custo conservador calculado a partir do uso confirmado, liberando a diferença da reserva. |
| `released` | Libera uma solicitação comprovadamente rejeitada sem execução. |
| `uncertain` | Mantém toda a reserva quando não há confirmação suficiente sobre a cobrança. |

Rejeições HTTP 400, 401, 403, 404, 422 e 429 liberam a reserva da solicitação. Timeout, desconexão, erro 5xx e ausência de telemetria válida mantêm o valor reservado: pode ter havido execução no provedor. Reiniciar o serviço ou retomar o ciclo não libera essa reserva automaticamente. Cada nova tentativa precisa caber no saldo disponível.

O histórico anterior ao ledger é importado com estimativas conservadoras. Entradas antigas podem não distinguir cache ou chamadas de pesquisa; o controle reserva margem para essas lacunas. Tarifas históricas desconhecidas não recebem custo zero. O valor apresentado pode superar o custo real confirmado na fatura.

## Tarifas e imagens

As tarifas foram conferidas em **9 de outubro de 2026**, na [documentação oficial de preços da OpenAI](https://developers.openai.com/api/docs/pricing). O fluxo textual solicita `service_tier=default`; usa preços padrão, sem pressupor descontos de Batch, Flex ou processamento prioritário. Custos de ferramentas são somados aos tokens, conforme a mesma documentação.

Para texto, a reserva considera a entrada estimada, o `max_output_tokens` enviado e os limites das ferramentas. O acerto usa tokens de entrada, cache, saída e chamadas web informados pelo provedor, com **15% de margem**. A transcrição paga usa a duração medida do arquivo, arredondada para cima em segundos, à tarifa padrão de **US$ 0,006 por minuto**, com a mesma margem.

Para imagens, a estimativa considera modelo, qualidade explícita, tamanho, prompt e dimensões das referências. O aplicativo usa uma tabela conservadora para imagens, um **fator adicional de 2 sobre a estimativa** e **15% de margem**. A fórmula de saída deriva do [calculador oficial de geração de imagens](https://developers.openai.com/api/docs/guides/image-generation); dimensões das referências devem permitir estimar a entrada. Tamanho ou qualidade automáticos e valores sem estimativa validada não iniciam a geração.

**A Images API não oferece `max_output_tokens`; o seu calculador fornece estimativas.** O teto de US$ 1 controla as autorizações da aplicação com essas margens e impede novas solicitações que não caibam. Ele não constitui uma garantia absoluta da fatura final do provedor, especialmente para imagens, alterações futuras de tarifas e valores sem confirmação. Aumentos no uso confirmado permanecem contabilizados e reduzem o saldo disponível para solicitações seguintes. O ledger é um controle operacional conservador, não o extrato da OpenAI.

## Conferência no painel

O administrador pode definir o limite no **Perfil editorial** e conferir, no artigo e nos relatórios, o teto, o uso contabilizado, as reservas e o saldo restante. O aviso de contabilização informa que valores incertos permanecem reservados. Estimativas anteriores ao processamento não substituem o uso confirmado; alterações de tarifas exigem atualizar a tabela do aplicativo.

Se o orçamento acabar depois da redação, o rascunho permanece visível e exportável, com **revisão pendente** e estado `needs_review`. O sistema não transforma ausência de revisão em aprovação. Se ainda não houver um rascunho atual, as etapas concluídas ficam preservadas para conferência e retomada dentro do saldo disponível.

A validação automatizada usa provedores simulados para verificar reservas, concorrência, recuperação, limites e persistência, sem consumo pago. Ela não comprova a qualidade de uma resposta real: a avaliação editorial continua exigindo comparar o artigo com a explicação das fontes.
