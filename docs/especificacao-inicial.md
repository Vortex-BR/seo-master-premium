# SEO editorial com vídeos e OpenAI

Status: registro histórico da proposta inicial. Consulte README.md para o comportamento implementado.
Data: 05/10/2026.

## Objetivo

Transformar um ou mais vídeos em artigos originais, úteis e naturais para blogs WordPress. Preservar os exemplos, explicações e dúvidas presentes no material, complementando lacunas com pesquisa documentada.

O vídeo é a base editorial principal, mas não uma autoridade factual absoluta. Quando houver divergência com evidência confiável, registrar o conflito para revisão. Usar fontes reduz erros; não garante ausência de alucinações.

## Decisões propostas e perguntas abertas

- Proposta: painel separado, conectado ao WordPress. Permite revisar fontes e versões antes do envio. A preferência por painel ou plugin ainda está aberta.
- Começar com um site, português brasileiro e criação de rascunhos.
- Nicho, público, origem dos vídeos, volume de artigos e plugin de SEO precisam ser definidos.
- Modelo OpenAI configurável. Escolha final após avaliar qualidade, latência e custo com materiais reais do nicho.
- Primeiro validar a qualidade de um artigo completo; depois automatizar a aquisição de vídeos e a produção em volume.

## Fluxo editorial

1. Briefing: tema, pergunta do leitor, objetivo do artigo, público, tom, palavra-chave proposta e vídeos de referência.
2. Entrada: transcrição colada ou arquivo TXT, SRT ou VTT, associado ao vídeo de origem. Importação automática de legendas apenas quando o conector dispuser do acesso necessário. Áudio fornecido pelo usuário e análise de quadros podem ser acrescentados depois.
3. Preparação: conservar o texto original; segmentar e preservar timestamps existentes. TXT sem marcação recebe identificadores de trecho, nunca horários inventados. Sinalizar transcrição incompleta e erros aparentes.
4. Extração: identificar ideias centrais, afirmações verificáveis, exemplos, opiniões, experiências do apresentador, ressalvas e dúvidas não respondidas. Cada item aponta para o trecho de origem.
5. Síntese: reunir vídeos sobre a mesma pergunta, remover redundâncias e explicitar divergências. Não transformar a repetição da mesma alegação em confirmação independente.
6. Pesquisa: buscar fontes para lacunas e informações que dependem de atualização. Priorizar fontes primárias; registrar URL, data de consulta, passagem de apoio e relação com a afirmação. Usar limites configuráveis de buscas e custo.
7. Pauta: definir intenção de busca, resposta principal e estrutura de seções. Comparar com artigos existentes do site quando houver inventário disponível.
8. Redação: produzir texto original com voz da marca, exemplos atribuídos e explicações adequadas ao leitor. Acrescentar organização e utilidade, além de resumir o vídeo.
9. Revisão: confrontar afirmações relevantes com as evidências e revisar clareza, repetição, contradições, atribuições e SEO editorial. A revisão por modelo também pode errar; deixar evidências acessíveis ao editor.
10. Entrega: artigo editável, pacote SEO e relatório de fontes. Enviar ao WordPress como rascunho por ação explícita no painel.

## Regras para texto natural e fiel

- Preservar exemplos concretos e detalhes que ajudam a explicar o assunto.
- Adaptar a fala para leitura, removendo vícios de linguagem sem apagar ressalvas.
- Nunca atribuir ao autor do blog experiências vividas pelo apresentador.
- Nunca inventar testes, resultados, clientes, credenciais, estatísticas ou citações.
- Distinguir opinião do apresentador, fato verificável e exemplo hipotético.
- Ajustar extensão ao que a pergunta exige; não preencher uma contagem artificial de palavras.
- Evitar introduções genéricas, conclusões repetidas e palavras-chave inseridas à força.
- Redigir com originalidade e atribuir ideias e citações de terceiros quando pertinente.
- Tratar transcrições e páginas como material de referência, não como instruções para o sistema.
- Se o assunto depender de uma demonstração visual ausente da transcrição, registrar essa lacuna antes de descrever o procedimento.

## Evidências e revisão

Cada afirmação factual relevante mantém um registro interno:

| Campo | Uso |
| --- | --- |
| id | Identificador estável da afirmação |
| texto | Afirmação feita no artigo |
| tipo | Fato, opinião atribuída ou relato atribuído |
| origem | Vídeo/transcrição ou fonte complementar |
| evidências | IDs de trechos e URLs efetivamente consultados |
| localização | Timestamp existente ou ID de segmento |
| situação | Apoiada, apoio parcial, contraditória ou sem apoio |
| revisão | Decisão editorial e justificativa |

Durante a revisão interna, validar que os IDs existem e que os trechos citados pertencem às fontes. Depois verificar se o conteúdo realmente sustenta a afirmação: a mera existência de uma URL não comprova uma alegação. Conforme a [automação editorial atual](automacao-editorial.md), possíveis incompatibilidades ficam nos diagnósticos e orientam correções comprovadas; não impedem o envio da versão salva tecnicamente válida.

Não exibir uma porcentagem de confiança inventada. Mostrar contagens verificáveis de pendências e evidências. Uma alteração no artigo invalida a revisão dos trechos afetados.

## Pacote SEO

- Título editorial, sugestão de título SEO, slug, metadescrição e resumo.
- Seções H2/H3 coerentes com a pergunta do leitor; o título do post normalmente fornece o H1, sujeito ao tema.
- Palavra-chave principal e termos relacionados como propostas editoriais, sem inventar volume de busca ou dificuldade.
- Sugestões de links internos somente para páginas existentes no inventário do site.
- Links externos para fontes utilizadas; perguntas frequentes apenas quando agregarem informação.
- Categorias e tags escolhidas entre as disponíveis, com novas sugestões separadas.
- Sugestões de imagens; texto alternativo apenas após conhecer a imagem real.

SEO editorial é o escopo inicial. Indexação, desempenho do tema e outros aspectos técnicos do site precisam de diagnóstico próprio. Não prometer posições nos resultados de busca.

## Integrações propostas

### OpenAI

Usar a Responses API para as etapas de extração, pesquisa, redação e revisão. A pesquisa pode utilizar a ferramenta web_search. Usar Structured Outputs nas etapas que precisam de estrutura validável, com esquemas próprios para extração, pauta e revisão; conformidade com o esquema não comprova exatidão factual.

Separar pesquisa e redação facilita inspecionar as evidências antes da geração final. Não pressupor que enviar uma URL do YouTube entregue o conteúdo audiovisual ao modelo: a entrada editorial será o material efetivamente extraído. Não há necessidade de fine-tuning para validar o primeiro fluxo; o conteúdo será fornecido como contexto.

### YouTube

A API oficial captions.download exige permissão para editar o vídeo. Portanto, um link público não garante acesso às legendas pelo conector oficial. Quando não houver transcrição acessível, solicitar material ao usuário e marcar a entrada como pendente, sem gerar um artigo fingindo ter assistido ao vídeo.

### WordPress

Para WordPress autohospedado, propor REST API e Application Password por HTTPS, com credenciais mantidas no servidor. Confirmar modalidade de hospedagem e acesso antes de implementar.

Criar posts com status draft, incluindo título, corpo, resumo, slug e taxonomias suportadas. Registrar o ID retornado para atualizar o mesmo rascunho. Em caso de timeout após envio, reconciliar o resultado antes de repetir para evitar duplicação.

Título SEO e metadescrição dependem do plugin e dos campos expostos. Produzir esses campos no painel desde o início; só prometer sincronização automática após validar o plugin instalado. Não presumir que o excerpt equivale à metadescrição.

## Componentes e dados

- Interface: briefing, fontes, andamento, editor, pendências e prévia.
- Serviço de processamento: etapas persistidas, limites de uso, retomada e tratamento de falhas.
- Armazenamento: site, perfil editorial, fontes, segmentos, afirmações, evidências, pautas, versões de artigo e envios.
- Adaptadores: OpenAI, entrada de transcrições e WordPress; aquisição automática de vídeo desacoplada do núcleo editorial.
- Credenciais fora do navegador e dos registros de execução; prévias e HTML gerado precisam de sanitização.

Estados propostos: aguardando material → extração → pesquisa → pauta → redação → revisão → pronto para envio → rascunho no WordPress. Erros conservam a última etapa concluída e permitem retomar sem repetir envios.

## Primeira entrega funcional proposta

Um fluxo completo com transcrição fornecida pelo usuário, geração fundamentada, pesquisa complementar, edição, relatório de evidências e exportação HTML. Em seguida, conexão com um WordPress real para criar rascunhos. A captura automática de vídeos depende da origem do material e será implementada após essa definição.

Critérios de aceitação:

1. Sem transcrição, o sistema mostra pendência e não inventa o conteúdo do vídeo.
2. Uma alegação inserida sem respaldo é sinalizada; números e citações mantêm sua origem.
3. Vídeos que discordam produzem um conflito visível, sem falsa conciliação.
4. Uma experiência do apresentador não vira experiência do autor do blog.
5. Instruções maliciosas dentro de fontes não alteram o fluxo nem acionam publicação.
6. Falha na pesquisa não é exibida como pesquisa concluída.
7. O artigo conserva exemplos úteis e responde à pergunta definida no briefing.
8. O envio cria um rascunho e a retomada não gera cópias do post.
9. Metadados sem suporte no WordPress continuam disponíveis para copiar ou exportar.

## Documentação consultada

- [OpenAI: pesquisa web](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [YouTube: download de legendas](https://developers.google.com/youtube/v3/docs/captions/download)
- [WordPress: posts](https://developer.wordpress.org/rest-api/reference/posts/)
- [WordPress: autenticação](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/)
- [Google: uso de conteúdo gerado por IA](https://developers.google.com/search/docs/fundamentals/using-gen-ai-content)
