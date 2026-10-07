# WordPress e imagens — versão 1.3.0

## Dentro do aplicativo

Na aba **Imagens**, solicite uma imagem baseada no artigo salvo. A direção visual
é opcional; estilo e qualidade são escolhidos pelo editor. A entrega é sempre
**1280 × 420 px em WebP**, com compressão sem perda adicional após o ajuste de
dimensões. O padrão é `gpt-image-2`, qualidade alta, um banner por solicitação. O modelo
pode ser alterado em Integrações. A geração usa a chave OpenAI já configurada e tem
cobrança própria; não acontece automaticamente ao gerar ou revisar texto.

Depois de conferir a imagem, preencha seu texto alternativo e, se desejar, legenda
e créditos. Escolha início, final ou um título de seção. Uma imagem pode ser usada
como destaque, no corpo, ou em ambos. A prévia do artigo mostra a composição salva.
Se uma seção for renomeada, sua imagem aparece no final e o painel pede uma nova
posição. A imagem e os metadados ficam separados do Markdown e da revisão factual.
O envio direto exige a confirmação editorial do texto e das imagens.

As imagens ficam no volume persistente `/data/media`, em WebP, com metadados de
origem registrados. Novas imagens de IA têm exatamente 1280 × 420 px; imagens
anteriores permanecem disponíveis. Arquivos inválidos, animações
e imagens maiores que os limites de processamento não entram no acervo.

## Referências visuais e composição mobile

Em **Integrações → Bancos de referências visuais**, salve uma chave do
[Pexels](https://www.pexels.com/api/) e/ou do [Pixabay](https://pixabay.com/api/docs/).
Também são aceitas as variáveis `PEXELS_API_KEY` e `PIXABAY_API_KEY`. As chaves são
cifradas; campos vazios preservam as credenciais existentes.

Na aba **Imagens**, a busca automática usa a palavra-chave ou o tema do artigo.
Você pode ajustar o termo, clicar em **Buscar referências** e escolher até três
fotos. A busca consulta apenas os bancos configurados. **Gerar sem referências**
permite usar somente o artigo e a direção visual. Buscar referências não chama a
OpenAI nem inicia geração paga.

As referências são enviadas à API de imagens como **URLs de imagens**, não apenas
links de páginas no prompt. O servidor guarda metadados, autoria e origem, sem
baixar nem incorporar as fotos dos bancos ao artigo. A OpenAI acessa essas URLs
para processar as referências. As miniaturas aparecem diretamente do banco na
busca; o artigo recebe somente a imagem criada com IA. Os resultados da API ficam
em cache por 24 horas, e seleções expiradas exigem nova busca. Se a busca automática
não obtiver referências de bancos configurados, a tarefa para antes da chamada
paga; o editor pode ajustar a busca ou escolher gerar sem referências.

O prompt orienta uma cena simples, luz natural, proporções plausíveis e assunto
principal centralizado, com espaço nas laterais e detalhes legíveis no celular.
Para GPT Image 2, a geração usa 1536 × 512 px, dentro dos limites da API; o servidor
faz um pequeno recorte central e redimensiona com Lanczos para 1280 × 420 px.
Outros modelos mantêm a tela horizontal compatível e recebem orientação para
preservar o assunto na faixa central. A entrega não distorce a proporção da cena.

**Conferir no celular** mostra o banner reduzido a 375 px e uma simulação do recorte
central 16:9. A prévia e o HTML independente preservam a proporção ao reduzir a
largura. O recorte da imagem destacada no WordPress depende do tema; confira-o no
site. O modelo pode desobedecer à composição, portanto confira a imagem gerada.

O WebP usa compressão **lossless**, preservando os pixels do banner ajustado, sem
uma segunda compressão com perdas. O redimensionamento e o recorte alteram a imagem
original. Não há limite fixo de KB: imagens com muitos detalhes pesam mais. O peso
real aparece no painel. A qualidade da geração é uma escolha separada da compressão.

## Exportação WordPress XML

Em **Exportar e enviar → Baixar XML WordPress**, o app produz WXR 1.2 compatível
com Ferramentas → Importar → WordPress. Selecione o autor e marque a opção para
baixar/importar anexos. O pacote contém:

- Artigo com status **Pendente de revisão**, título, slug, resumo e tags.
- Blocos nativos de parágrafo, títulos H2/H3/H4, listas ordenadas e não ordenadas
  e imagens. O título da postagem é o H1 do documento. Citações e código usam
  blocos HTML sanitizados para preservar o conteúdo.
- Anexos com texto alternativo e legenda, além da imagem destacada.
- Metadados `_yoast_wpseo_title`, `_yoast_wpseo_metadesc` e `_yoast_wpseo_focuskw`.

O XML concede acesso somente às imagens incluídas, por URLs assinadas válidas por
sete dias. O app deve estar acessível ao WordPress durante a importação. O importador
baixa as imagens para a biblioteca do site e substitui as URLs no conteúdo. Após
esse prazo, gere um novo XML. Remover a imagem no app revoga seus links. O XML não
é um mecanismo de sincronização: evite reimportar para atualizar o mesmo post.

Com o Yoast ativo no site, confira e salve a postagem pendente após a importação para atualizar
os dados calculados pelo plugin. A pontuação é calculada pelo próprio Yoast.

## Envio direto e outros formatos

O envio direto continua usando a REST API e a senha de aplicativo do WordPress.
O usuário precisa poder editar posts e enviar arquivos. As imagens são enviadas
antes da postagem pendente; IDs e URLs são persistidos para reutilização nas atualizações.
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
- [OpenAI: referências por URL no endpoint de imagens](https://developers.openai.com/api/reference/resources/images/methods/edit).
- [Pexels: API e atribuição](https://www.pexels.com/api/documentation/).
- [Pixabay: API, cache e prévias temporárias](https://pixabay.com/api/docs/).
- [Yoast: API de leitura](https://developer.yoast.com/customization/apis/rest-api/).
