# WordPress e imagens — versão 1.2.0

## Dentro do aplicativo

Na aba **Imagens**, solicite uma imagem baseada no artigo salvo. A direção visual
é opcional; formato, estilo e qualidade são escolhidos pelo editor. O padrão é
`gpt-image-2`, qualidade econômica, uma imagem horizontal por solicitação. O modelo
pode ser alterado em Integrações. A geração usa a chave OpenAI já configurada e tem
cobrança própria; não acontece automaticamente ao gerar ou revisar texto.

Depois de conferir a imagem, preencha seu texto alternativo e, se desejar, legenda
e créditos. Escolha início, final ou um título de seção. Uma imagem pode ser usada
como destaque, no corpo, ou em ambos. A prévia do artigo mostra a composição salva.
Se uma seção for renomeada, sua imagem aparece no final e o painel pede uma nova
posição. A imagem e os metadados ficam separados do Markdown e da revisão factual.
O envio direto exige a confirmação editorial do texto e das imagens.

As imagens ficam no volume persistente `/data/media`, em WebP, com dimensões
limitadas a 2400 px e metadados de origem registrados. Arquivos inválidos, animações
e imagens maiores que os limites de processamento não entram no acervo.

## Exportação WordPress XML

Em **Exportar e enviar → Baixar XML WordPress**, o app produz WXR 1.2 compatível
com Ferramentas → Importar → WordPress. Selecione o autor e marque a opção para
baixar/importar anexos. O pacote contém:

- Artigo como rascunho, título, slug, resumo e tags.
- Blocos de parágrafo, título e imagem. Listas, citações e código usam blocos HTML
  sanitizados para preservar o conteúdo.
- Anexos com texto alternativo e legenda, além da imagem destacada.
- Metadados `_yoast_wpseo_title`, `_yoast_wpseo_metadesc` e `_yoast_wpseo_focuskw`.

O XML concede acesso somente às imagens incluídas, por URLs assinadas válidas por
sete dias. O app deve estar acessível ao WordPress durante a importação. O importador
baixa as imagens para a biblioteca do site e substitui as URLs no conteúdo. Após
esse prazo, gere um novo XML. Remover a imagem no app revoga seus links. O XML não
é um mecanismo de sincronização: evite reimportar para atualizar o mesmo post.

Com o Yoast ativo no site, confira e salve o rascunho após a importação para atualizar
os dados calculados pelo plugin. A pontuação é calculada pelo próprio Yoast.

## Envio direto e outros formatos

O envio direto continua usando a REST API e a senha de aplicativo do WordPress.
O usuário precisa poder editar posts e enviar arquivos. As imagens são enviadas
antes do rascunho; IDs e URLs são persistidos para reutilização nas atualizações.
O app confere slugs e possíveis resultados de envios interrompidos antes de criar
novos registros. Um resultado incerto não provoca recriação automática.

O envio direto sincroniza texto, imagens, legendas, alt e destaque. A escrita dos
campos específicos do Yoast não foi acrescentada a esse caminho: use o XML ou
copie os valores do pacote SEO. Não se presume que a API de leitura do Yoast aceite
atualizações de metadados.

**HTML com imagens** é um arquivo independente, com imagens incorporadas.
**Blocos WordPress** é um fragmento para o editor de código do WordPress, sem H1
duplicado ou documento HTML externo; exige imagens já sincronizadas no site.
**Markdown do texto** contém somente o texto. O **pacote SEO** inclui metadados,
fontes e informações das imagens.

## Execução e recuperação

Uma fila separada atende gerações de imagem. A chave de idempotência evita a
duplicação de uma solicitação reenviada pelo navegador. A tarefa persiste estado,
modelo, formato, qualidade e consumo retornado pela OpenAI. O SDK não repete
automaticamente chamadas de imagem: um timeout pode já ter consumido saldo.
Após reinício, tarefas pendentes ficam interrompidas e dependem de uma nova ação
do editor. A edição e a geração de texto aguardam a imagem terminar, evitando
que gravações simultâneas descartem dados.

## Referências

Validação da entrega: geração real com OpenAI, interface em desktop/celular e
importação pelo WordPress Importer em WordPress 7.1.2 com Yoast ativo. O editor
reconheceu os 31 blocos do artigo de teste; a imagem foi baixada para a biblioteca,
as URLs foram substituídas e destaque, alt e três metadados Yoast foram conferidos.
Os testes automatizados também cobrem recusas/falhas, idempotência, links expirados,
revogação, arquivos inválidos e retomada de envios sem duplicação cega.

- [WordPress: importação de conteúdo](https://developer.wordpress.org/advanced-administration/wordpress/import/).
- [WordPress: mídia pela REST API](https://developer.wordpress.org/rest-api/reference/media/).
- [WordPress: formato dos blocos](https://developer.wordpress.org/block-editor/getting-started/fundamentals/markup-representation-block/).
- [WordPress Importer: anexos e remapeamento](https://github.com/WordPress/wordpress-importer/blob/master/src/class-wp-import.php).
- [OpenAI Docs: geração de imagens](https://developers.openai.com/api/docs/guides/image-generation).
- [Yoast: API de leitura](https://developer.yoast.com/customization/apis/rest-api/).
