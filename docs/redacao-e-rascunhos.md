# Redação e preservação das entregas — 1.5.18

## Comportamento

Novos ciclos usam `EDITORIAL_COMPOSITION=coherent`. O redator recebe o percurso completo, os fatos conferidos, os trechos originais e a meta de palavras do artigo inteiro. A escrita não precisa preencher a meta quando a pergunta já foi respondida. Tutoriais pedem ações numeradas; comparações e explicações seguem a intenção da pauta. Assunto, produtos e voz pertencem às fontes e ao perfil do projeto.

A solicitação completa é medida antes do envio. Se não couber, a escrita usa partes com parcelas cuja soma corresponde à meta global; não impõe um mínimo de 120 palavras a cada seção. A versão anterior pode ser selecionada com `EDITORIAL_COMPOSITION=legacy`. Ciclos existentes conservam a estratégia com que foram iniciados e seus resultados pagos.

As condições, restrições e limitações dos itens seguem explicitamente para o redator e para a revisão factual. A revisão recebe as fontes originais sem os pareceres de aprovação dos agentes anteriores. Isso melhora as condições da avaliação, mas não comprova que o modelo detectará toda omissão.

## Texto disponível antes da aprovação

- Cada entrega de redação válida é salva em `job.article` antes de ajustes opcionais. Na escrita por partes, isso acontece antes da próxima parte e antes de tentar completar informações omitidas.
- `draft_delivery` identifica texto parcial ou completo e revisão pendente. O painel exibe essa condição, o texto e o download; falhas posteriores não removem a entrega.
- A versão que existia antes do ciclo permanece no histórico. Artefatos de redação e tentativas de correção também continuam salvos.
- O usuário pode ler e exportar durante o processamento. A edição é liberada ao encerrar ou interromper o trabalho, para evitar sobrescrita concorrente. Uma edição manual invalida a retomada automática da versão anterior.
- Texto curto não é rejeitado por ter menos de 100 caracteres. Artigo completo, qualidade editorial e autorização de envio ao WordPress continuam sendo condições diferentes.
- Apontamentos de revisão não escondem o artigo. Uma falha de API ou um orçamento esgotado continua registrada; não é apresentada como aprovação.

## Consumo

O novo ciclo segue da redação para a revisão final, sem as cinco chamadas anteriores de leitor, editor de voz, estrategista, analista Yoast e editor SEO. Metadados são produzidos junto do artigo. Correções continuam limitadas às rodadas configuradas; otimização explicitamente solicitada conserva a equipe SEO.

A reserva genérica de sete ou oito chamadas não impede gastar a última chamada disponível na redação. Antes de iniciar um ciclo novo, a estimativa mínima ainda impede começar quando o orçamento já é insuficiente. `engine.invoke` continua consultando o cache antes de verificar e aplicar o limite real do ciclo. Se faltar orçamento depois, o rascunho fica disponível com revisão pendente. O limite de chamadas, o modelo e a chave não são elevados automaticamente. As estimativas são orientativas, não garantia de conclusão nem preço fixo.

Há no máximo uma correção editorial de composição, além da recuperação limitada de formato já existente. A entrega inicial é preservada antes dessa correção. Uma falha registrada de reparo não abre um ciclo de novas tentativas na retomada.

## Validação e limite das conclusões

Antes das últimas correções, três avaliações locais **pagas**, com transcrições sintéticas, produziram:

| Caso | Chamadas | Resultado observado |
| --- | ---: | --- |
| Tutorial de cópia de fotos, cinco fontes | 44 | 466 palavras e cinco etapas; ainda omitiu ressalvas e apresentou apontamentos de revisão |
| Comparação de agendas, três fontes | 26 | Interrompido com seis chamadas restantes pela reserva de revisão |
| Explicação sobre arquivo digital, três fontes | 26 | Interrompido antes da redação com seis chamadas restantes |

Esses resultados **não aprovam a qualidade editorial**. Foram 96 chamadas registradas, 589.584 tokens de entrada e 39.550 de saída; não há cálculo monetário validado nesta avaliação. O conjunto não testa obtenção de áudio nem reconhecimento visual dos vídeos.

Após a suspensão de novas chamadas pagas, a validação usa SDK com transporte simulado, banco isolado, testes do coordenador e navegador local. Confere preservação e exportação de texto, última chamada disponível, limite real de consumo, retomada sem repetir entregas, falhas de reparo e divisão da meta de palavras. Esses testes provam o comportamento do software; não medem a qualidade de uma nova resposta real do modelo.

O conjunto `tests/evaluation/multidomain_cases.json` mantém perguntas e critérios verificáveis em assuntos diferentes. `scripts/evaluate_editorial.py` opera sem provedor por padrão; chamadas reais exigem `--live --case ID`. Para aprovar a redação, é necessário examinar o texto contra o gabarito, inclusive ressalvas ausentes e afirmações sem apoio. Concluir o pipeline ou obter aprovação de outro agente não basta.

Na validação desta versão, a suíte completa terminou com 358 testes aprovados. Após os ajustes finais de compatibilidade e edição manual, os 26 testes diretamente afetados foram executados novamente e passaram. O navegador local confirmou exibição, download e edição do rascunho interrompido; a reprodução offline do ciclo de produção conservou 58 entregas anteriores e não alterou o artigo. Nenhuma dessas verificações fez chamadas externas de IA.
