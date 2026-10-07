# Apuração e redação com evidências — versão 1.4.0

## Operação

O fluxo padrão analisa um a cinco vídeos individualmente, com até 120 mil caracteres
por fonte e 180 mil no conjunto. A quantidade de links não determina a quantidade
de chamadas. Cada bloco é extraído e conferido; vídeos longos exigem mais etapas.
Entradas acima dos limites são recusadas, sem cortar material para acomodá-lo.

Na aba **Planejamento**, **Obter fontes** apenas obtém transcrições. **Planejar sem
redigir** apura, compara e organiza as seções. **Redigir a partir do plano** usa a
versão salva e executa edição, SEO e qualidade. O perfil permite desligar a redação
automática. Salvar direção, plano ou resolução editorial não chama a OpenAI.

O briefing inclui pergunta central, intenção, gênero, público, exclusões e extensão.
Seções registram pergunta, finalidade, informações, pré-requisitos, condições,
transição e pendências. O editor pode reorganizar seções, alterar seus campos e
destinar uma informação a uma seção ou justificar sua exclusão.

Cada parágrafo deve desenvolver uma ideia com contexto. O artigo situa a pergunta,
constrói a resposta e encerra o raciocínio. Comparações e retomadas podem ajudar o
leitor; nenhuma ligação autoriza inventar uma causa ou conciliação entre fontes.

## Apuração, comparação e pesquisa

`source_processing.py` preserva todos os caracteres dos trechos em faixas contíguas,
preferindo limites de frase. A faixa vizinha serve de contexto e não muda o texto
original. IDs de evidência continuam levando à fonte e ao timestamp disponível.
Legendas automáticas são identificadas quando o provedor informa essa característica.
Duplicações, timestamps fora de ordem, intervalos e caracteres suspeitos geram avisos.
O sistema não certifica a completude da transcrição e não analisa imagens dos vídeos.

Os contratos guardam natureza, tipo de informação, método, condições, quantidades,
unidades, restrições e evidência. O checador precisa entregar uma situação para cada
ID, incluindo o último do lote. A existência literal de uma citação não basta:
uma formulação pode ser não sustentada ou incerta e ficará fora da redação.

Desde a versão 1.5.3, o extrator e o revisor factual selecionam IDs de trechos
disponíveis no contexto. O servidor copia a citação diretamente do original;
a IA não precisa reproduzir sua grafia. Os IDs são limitados pelo esquema da
resposta. A seleção respeita o bloco recebido, inclui o final da fonte e não
atravessa intervalos omitidos dos contextos de revisão. Isso garante a origem
literal da citação, mas não seu apoio semântico: o checador continua avaliando
significado, condições, atribuições e ressalvas antes de liberar informações.

Falhas de correspondência de evidência têm diagnóstico próprio, separado de
respostas truncadas. Falhas de esquema registram apenas o tipo de validação e
posições anonimizadas, sem salvar valores rejeitados ou mensagens do provedor.
As tentativas permanecem limitadas e contabilizadas no orçamento existente.

Na versão 1.5.4, a conferência de conhecimento e de relações exige uma resposta
por ID como propriedade obrigatória do esquema. A IA não escolhe novamente o
ID nem pode substituir a relação pelos itens aninhados que a sustentam. O
servidor converte o resultado para o formato de auditoria já persistido. IDs
ausentes, estranhos ou cobertura incompleta continuam bloqueando a entrega.

Na versão 1.5.5, relações de vídeo, agrupamento de assuntos, comparações, seções
do plano, redação por seções e revisão factual restringem os IDs ao inventário
da própria tarefa. Os esquemas distinguem IDs de fontes, informações e trechos
do artigo. Seções só podem selecionar informações sustentadas; a consolidação
só pode usar informações destinadas à redação no plano. Inventários vazios
aceitam apenas listas vazias. A validação de cobertura e significado continua
independente da seleção de IDs, inclusive para entregas já salvas.
A resolução de pesquisa também restringe IDs às pendências recebidas e seleciona
suas citações entre os trechos originais das páginas lidas.

Na versão 1.5.6, a resolução de pesquisa conserva todas as informações conferidas,
suas condições, quantidades e restrições, apontando os IDs das fontes em vez de
repetir a mesma citação em cada item. Quando uma etapa seleciona evidências por
ID, o texto literal é enviado uma vez em `evidence_options`; o mapa de fontes
conserva metadados e referências aos textos. Os originais, a validação e o limite
de contexto permanecem preservados. Contextos maiores que o limite continuam
sendo recusados antes de qualquer chamada de IA.

Na versão 1.5.7, classificação de assuntos, comparações, destinação no plano,
revisão de trechos e resolução de pesquisa exigem uma propriedade obrigatória
por ID do lote. Os modelos avaliam cada item sob sua própria chave; o servidor
restaura o formato de armazenamento sem preencher respostas ausentes. Cada
comparação inclui o item da chave e suas contrapartes permitidas. Omissões são
rejeitadas pelo esquema, e as validações de significado continuam independentes.
O agrupamento compartilha um catálogo de até oito famílias editoriais entre os
lotes, para comparar métodos e condições do mesmo assunto. O conjunto de tópicos
originais orienta esse catálogo, e todas as informações continuam individualizadas
para conferência, planejamento e destinação explícita.

Na versão 1.5.8, a consolidação também exige uma destinação por informação marcada
como usada. Cada chave escolhe uma ou mais seções presentes no plano. O servidor
restaura os vínculos sem inventar destinações; seções duplicadas ou ausentes são
rejeitadas. A consolidação não pode perder detalhes ao reunir o plano dos assuntos.
O planejador também justifica a prioridade das pendências de comparação em relação
à pergunta central. A lacuna permanece aberta e a informação incerta continua
impedida de entrar no texto. A prioridade só vale para esse plano válido e essas
fontes; pendências de transcrição e planos antigos conservam seus bloqueios.
A passagem de planejamento para redação conserva o orçamento e as entregas do
ciclo inacabado quando fontes, modelo e perfil de voz permanecem compatíveis.

Na versão 1.5.9, a redação por seções conserva todos os campos do conhecimento
conferido e seus vínculos de origem, com o texto literal entregue somente nas
fontes. Os itens da seção não são repetidos na lista de contrapontos. Os originais,
as condições, os números, as ressalvas e a revisão factual permanecem preservados.
A revisão recebe os mesmos fatos completos com referências, conferindo-os contra
os textos originais. A retomada aplica aumentos do limite de contexto configurado
e mantém a voz, os resultados e a contagem de chamadas do ciclo.

Na versão 1.5.10, a redação por seções entrega blocos Markdown e escolhe suas
fontes em listas restritas aos IDs disponíveis. O servidor insere as citações no
fim de cada bloco. IDs digitados dentro da redação são rejeitados, assim como
fontes externas ao lote. A revisão continua conferindo o significado e o apoio
das fontes de cada parágrafo, incluindo todas as condições e informações previstas.

Na versão 1.5.11, cada informação prevista recebe uma avaliação obrigatória de
uso na redação. Uma informação não desenvolvida continua sem contar como usada,
e a seção incompleta é rejeitada com um motivo específico de cobertura. A revisão
semântica verifica independentemente se o texto desenvolveu o conteúdo declarado.

Na versão 1.5.12, leitura, edição e decisão selecionam IDs de trechos do artigo,
com cópia literal feita pelo servidor. Aspas e outros caracteres permanecem no
texto original, sem integrar os enums do contrato. O título avaliado também usa
uma referência estável. As pendências enviadas aos revisores respeitam a prioridade
justificada no plano vigente; comparações permanecem disponíveis em resumos completos.

Uma passagem por vídeo procura ressalvas distantes e ligações entre informações.
Índices abreviados ajudam a localizar contrapartes; não substituem os itens completos.
As relações propostas são conferidas novamente com os itens e trechos originais.
A comparação distingue complementação, repetição, concordância nas mesmas condições,
métodos diferentes, divergência e informação insuficiente. Não usa votação e não
inventa relações como “ideal versus máximo”.

A pesquisa é acionada por perguntas específicas e respeita a configuração do
briefing. As notas geradas permanecem identificadas e são excluídas do mapa factual.
Somente páginas HTML públicas efetivamente lidas fornecem trechos originais para
extração e conferência. O leitor limita tamanho, recusa destinos privados e fixa o
IP validado, preservando Host e verificação TLS. Não segue redirecionamentos nem
baixa imagens. PDF, páginas bloqueadas, redirecionamentos e páginas sem texto
utilizável permanecem como referências com limitações explícitas.

O checador pode registrar uma resolução de pesquisa, com trecho original verificado
e justificativa. A resolução mantém o problema anterior. Uma pesquisa não encerra
automaticamente uma pendência apenas por citar uma URL. Pendências indispensáveis
ou um plano marcado como insuficiente impedem a redação.

## Redação, revisão e cobertura

Quando o material cabe no contexto, a redação usa uma chamada. Materiais maiores
são escritos por partes, com plano, informações conferidas, contrapontos e texto
anterior. Uma leitura do artigo inteiro e a revisão após SEO conferem as costuras.

A revisão factual particiona todo o artigo, incluindo título, metadescrição,
resumo, slug, tags, subtítulos e corpo. Cada trecho precisa de uma avaliação.
Não há o antigo corte em 80 IDs de evidência. Lotes incompletos são rejeitados.
A avaliação global recebe o artigo completo e os apontamentos dos lotes.

O relatório liga blocos, informações e destinos: usada, duplicada, fora da pauta,
sem apoio ou pendente. Informação prevista no plano e não desenvolvida com apoio
na versão final gera bloqueio. Não há quota de citações por vídeo. Alterações de
SEO não dispensam a conferência de condições, números, unidades e atribuições.

## Orçamento e retomada

O perfil novo começa com 120 chamadas do coordenador, até uma rodada adicional,
quatro chamadas de ferramenta por pesquisa, blocos de 7 mil caracteres e limite
conservador de entrada de 90 mil caracteres. Os limites são configuráveis:
12–400 chamadas, 0–3 rodadas, 1–12 chamadas de ferramenta, blocos de 3–12 mil
caracteres e contexto de 30–240 mil caracteres. Perfis personalizados anteriores
mantêm seus valores; um limite de 24 chamadas pode ser insuficiente para vários vídeos.

O contexto controla instruções e materiais enviados; não mede tokens exatamente
nem identifica automaticamente a janela de qualquer modelo. A saída tem limite
separado de tokens. Configure o contexto para o modelo escolhido. Uma etapa que
não cabe para antes de chamar o provedor, sem retirar evidências silenciosamente.

A estimativa mostra intervalo de chamadas e o limite escolhido. Reservas protegem
as etapas finais, mas a complexidade só fica conhecida durante a apuração. O limite
não é um teto monetário: ferramentas e transcrição têm consumo próprio.
Retentativas do coordenador contam no orçamento; o SDK de texto não faz tentativas
adicionais ocultas. Uma falha de conexão ou formato permite uma recuperação por
etapa. Recusa, filtro e falta de orçamento não autorizam respostas parciais.

`budget_exhausted` preserva as entregas e informa a etapa pendente. Ao aumentar o
limite e retomar, o ciclo mantém sua voz e seus modelos congelados. Blocos concluídos,
propostas aplicadas e artefatos válidos não são executados novamente. Uma chamada
sem resposta persistida pode precisar ser repetida.

## Versões e compatibilidade

O startup cria incrementalmente `editorial_artifacts` e `editorial_issues`, sem
reescrever artigos existentes. Artefatos conservam dados, dependências e versões;
as pendências conservam situação, justificativa e resolução. Mudança de fontes ou
direção invalida apuração e plano; mudança do plano invalida redação e revisão;
mudança do texto invalida a revisão. Salvamento de plano exige sua versão atual,
e edições ficam bloqueadas durante processamento.

Os doze papéis são mantidos. Execuções registram modelo, perfil, instruções
versionadas, entradas, consumo e situação. Reutilização de uma entrega verifica
seu hash; mudar a entrada de um mesmo papel não reutiliza seu resultado antigo.
Propostas de edição também incluem a versão do plano nas dependências.

`EDITORIAL_FLOW=evidence` ativa o caminho novo, por padrão. `EDITORIAL_FLOW=legacy`
permite retorno temporário ao processamento anterior, mantendo as correções de
rigor e coerência dos prompts. Artigos sem registro novo são identificados no painel;
uma revisão antiga não é apresentada como cobertura da nova apuração. Os módulos
de imagens e publicação continuam separados. O JSON exportado inclui plano,
apuração, cobertura e pendências.

## Avaliação

Os testes simulam o provedor, executando coordenador, SQLite, contratos, recuperação
e validações reais. Cobrem cinco vídeos complementares e longos, detalhe final,
ressalvas distantes, unidades diferentes, citação literal sem apoio semântico,
informação visual indisponível, redação por seções, mais de 80 evidências, revisão
incompleta, pesquisa inacessível, retomada, concorrência e limite de chamadas.
Também exercitam o SDK instalado e a interface em desktop e celular.

`tests/evaluation/editorial_cases.json` contém sete cenários e gabaritos para um
revisor humano. Prepare a avaliação, sem chamadas externas:

```powershell
.venv/Scripts/python scripts/evaluate_editorial.py
```

Para comparar saídas reais de um caso, com consumo na conta configurada:

```powershell
.venv/Scripts/python scripts/evaluate_editorial.py --live --case different_methods --max-calls 40
```

Os resultados ficam em uma pasta isolada em `.local/editorial-evaluation`, com
artigos, estado completo, chamadas, tokens, tempo e ficha `review.json`. `--flow`
seleciona um caminho ou ambos. Casos longos podem exigir maior orçamento e contexto.
A ficha não atribui notas automáticas nem transforma concordância entre agentes
em confiança factual. O revisor preenche omissões, afirmações sem apoio, atribuições,
condições, tratamento de divergências, contexto dos parágrafos e correções necessárias.

Testes de coordenação não comprovam fidelidade do modelo em vídeos reais. A aprovação
de qualidade editorial exige ler essas saídas e conferir o gabarito e as fontes.
Formato estruturado ajuda a controlar entregas, mas não garante verdade semântica:
[documentação oficial de Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
