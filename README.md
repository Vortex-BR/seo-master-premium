# SEO MASTER PREMIUM

Estúdio editorial para transformar **links do YouTube** em artigos SEO para WordPress, com fontes rastreáveis, pesquisa complementar, voz da marca e revisão.

## Funcionalidades

- Entrada de 1 a 5 links do YouTube por artigo, incluindo Shorts e lives gravadas.
- Extração automática de legendas com timestamps. Alternativas opcionais: Supadata e transcrição de áudio OpenAI.
- Reaproveitamento de transcrições automáticas do mesmo vídeo extraídas nas últimas 24 horas neste estúdio, com origem e data visíveis. Transcrições manuais não são reaproveitadas entre artigos.
- Proxies Webshare configuráveis no painel, com tentativas alternativas e credenciais cifradas.
- Redação com 12 agentes: três em apuração, três em redação e voz, três em SEO e três em qualidade final. Trocam entregas e pedidos de correção dentro do aplicativo.
- Pipeline persistido: extração → pauta sobre o assunto → pesquisa → redação → SEO → revisão, com rodadas de correção configuráveis.
- Biblioteca versionada de orientações interpretadas do Google e Yoast, com busca textual, origem, contexto, exemplos e exceções. Cada agente recebe as orientações pertinentes à sua tarefa.
- Perfil editorial compartilhado: tom, vocabulário, ritmo, tratamento do leitor e exemplos aprovados. O perfil e a documentação usados ficam vinculados ao ciclo.
- Propostas de alteração com antes/depois, aplicação automática opcional, decisão manual e desfazer. Mudanças invalidam a revisão da versão anterior.
- Artigos com redação própria que ensinam o tema das fontes. Os vídeos servem como referência; resenhas exigem pedido explícito no briefing.
- Direção do artigo editável: tema, público, palavra-chave, tom, extensão, orientações e pesquisa. Salvar a direção não inicia chamadas pagas.
- OpenAI Responses API e Structured Outputs; modelo configurável, padrão `gpt-4.1-mini`.
- Pesquisa web opcional com referências. Limite de duas chamadas de ferramenta por execução de pesquisa.
- Editor Markdown, prévia HTML segura, pacote SEO, checklist editorial e histórico de versões.
- Evidências por trecho, revisão factual, conferência da direção editorial e detecção de referências inexistentes.
- Decisões editoriais por apontamento, com justificativa, versão e histórico, para conferir falsos positivos da revisão por IA.
- Exportação HTML, Markdown e JSON com metadados e fontes.
- Integração WordPress REST API para criar e atualizar **rascunhos**.
- Login privado, sessões revogáveis, proteção contra CSRF, credenciais cifradas e volume persistente.
- Interface em português, adaptada a desktop e celular.

## Executar localmente

Requer Python 3.12+. FFmpeg é necessário apenas para a alternativa de áudio.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
$env:ADMIN_PASSWORD = 'defina-uma-senha-longa-e-unica'
$env:DATA_DIR = './data'
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abra `http://localhost:8000`. Entre com `ADMIN_PASSWORD` e configure a OpenAI em **Integrações e marca**. O arquivo `.env.example` é uma referência: a aplicação lê variáveis do processo, não carrega `.env` automaticamente.

## Docker e EasyPanel

```sh
docker build -t seo-master-premium .
docker run -d --name seo-master-premium -p 8000:8000 \
  -e ADMIN_PASSWORD='defina-uma-senha-longa-e-unica' \
  -v seo-master-data:/data seo-master-premium
```

No EasyPanel, criar um serviço App com fonte Git deste repositório, branch `main`, build `Dockerfile`, porta interna **8000** e volume nomeado em **`/data`**. Configurar domínio HTTPS e as variáveis abaixo. Usar **uma réplica** e desativar implantação com sobreposição de réplicas, pois a fila e o SQLite pertencem a uma única instância.

| Variável | Uso |
| --- | --- |
| `ADMIN_PASSWORD` | Obrigatória na primeira inicialização; mínimo de 12 caracteres. Depois a senha pode ser alterada no painel. |
| `DATA_DIR` | `/data` no container. |
| `COOKIE_SECURE` | `1` em produção HTTPS; `0` para desenvolvimento HTTP local. |
| `APP_URL` | Origem pública exata, por exemplo `https://seo.example.com`, para validação de origem. |
| `OPENAI_API_KEY` | Opcional: pode ser configurada no painel. |
| `OPENAI_MODEL` | Opcional: padrão `gpt-4.1-mini`. |
| `SUPADATA_API_KEY` | Opcional: alternativa de extração via Supadata. |
| `YOUTUBE_PROXY_URLS` | Opcional: URLs HTTP/HTTPS de proxies separadas por linhas; podem ser configuradas no painel. |

Configurações salvas no painel têm precedência sobre variáveis de ambiente. Campos de senha vazios mantêm os valores existentes. O endpoint público `/health` verifica disponibilidade e acesso ao banco, sem revelar configurações.

O volume contém o SQLite e `encryption.key`. **Faça backup do volume inteiro**, mantendo a chave junto ao banco e fora do repositório. Para backup com a aplicação em uso, prefira a API de backup do SQLite ou um snapshot consistente do volume. Se copiar arquivos manualmente, pare o serviço antes.

## Como usar

1. Salve sua chave OpenAI em Integrações e teste a conexão.
2. Em Criar artigo, cole os links e defina tema, público, palavra-chave e tom. Esses campos orientam o conteúdo; não representam uma pesquisa de volume de palavras-chave.
3. Use **Extrair fontes** para conferir o material antes de consumir tokens de geração, ou **Criar artigo** para executar o fluxo completo.
4. Confira Fontes, Pesquisa e Revisão; ajuste o texto no editor. Edições invalidam a revisão anterior.
5. Execute **Revisar artigo** após editar. Na aba Revisão, confira os apontamentos: corrija o texto ou registre uma decisão editorial com a fonte conferida quando o apontamento não se aplicar. Referências inexistentes e falhas de estrutura precisam ser corrigidas no texto. Decisões ficam no histórico e perdem validade quando o artigo muda.
6. Exporte ou envie um rascunho ao WordPress depois de conferir o artigo.

Em **Equipe e voz editorial**, configure o padrão de escrita e consulte a biblioteca de SEO. As orientações priorizam clareza e fidelidade: não impõem cotas de conectivos ou repetição artificial de palavras-chave. Salvar o perfil não consome a OpenAI e não reescreve artigos existentes.

A aba **Equipe editorial** de cada artigo mostra os 12 papéis, entregas, comunicação entre setores, uso de tokens e mudanças propostas. **Melhorar este artigo** executa redação/voz, SEO e revisão sobre o texto existente, preservando as fontes. **Revisar artigo** executa os três agentes da revisão final, sem aplicar mudanças. O fluxo completo de um novo artigo executa os 12 papéis; pesquisa, tentativas de correção de respostas inválidas e rodadas adicionais podem acrescentar chamadas.

Se a aplicação automática estiver desativada, aceite ou rejeite as propostas na aba da equipe. Propostas valem para a versão do artigo e das fontes que examinaram: após uma alteração, propostas antigas podem precisar ser refeitas. Depois de aplicar ou desfazer uma mudança, revise novamente.

Para mudar o foco de um artigo existente, abra **Direção do artigo**, edite e salve. Depois clique em **Gerar novamente**. As transcrições são reaproveitadas; pauta, pesquisa, texto e revisão são refeitos para a nova direção. O texto anterior permanece disponível e vai para o histórico ao ser substituído. Artigos anteriores à atualização editorial são identificados no painel; confira as instruções antigas antes de gerar novamente.

Se a extração falhar, o motivo aparece no artigo e na aba **Fontes**. **Repetir extração** tenta obter somente as fontes, sem iniciar a redação. Quando há uma transcrição automática recente desse vídeo em outro artigo do estúdio, ela é reaproveitada antes de tentar nova conexão com o YouTube. Os IDs dos trechos são ajustados ao novo artigo, preservando texto e timestamps. As alternativas de Supadata e áudio, se usadas, mantêm a cobrança dos respectivos provedores.

Exemplo: um vídeo sobre preparo de café coado deve originar um artigo que explique o preparo ao leitor, com estrutura própria e referências. Um texto sobre as motivações ou a comunicação do apresentador não atende a essa pauta. A revisão verifica esse desvio de foco, além da fidelidade factual. Experiências particulares continuam atribuídas à fonte; a aplicação não inventa que a marca realizou os testes.

## Comportamento e limites

- A extração direta depende do acesso do servidor ao YouTube. IPs de datacenter podem ser bloqueados. Configure proxies em Integrações (lista Webshare `host:porta:usuario:senha` ou URLs autenticadas). O aplicativo tenta até três proxies por vídeo. Supadata é outra alternativa, com cobrança separada. Falhas são exibidas; o aplicativo não inventa que assistiu a um vídeo.
- O núcleo analisa transcrições. Informações presentes apenas nas imagens do vídeo precisam de conferência editorial.
- A transcrição de áudio é opcional, usa `yt-dlp` + FFmpeg + `whisper-1` e aceita até 45 minutos/24 MB de áudio convertido. Ela também depende do acesso ao YouTube.
- Limites de entrada: 120 mil caracteres por vídeo, 180 mil por artigo; até 10 trabalhos na fila e um em execução.
- Processamentos interrompidos por reinício ficam visíveis e exigem retomada. A retomada reutiliza as etapas concluídas e persistidas; uma chamada interrompida antes de salvar pode ser repetida. Gerar novamente um artigo concluído inicia outra geração e conserva a versão anterior.
- A revisão é assistida por IA, complementada por validações de IDs, trechos e versão. Não garante verdade factual. As evidências web são notas da pesquisa com citações, não um arquivo integral das páginas.
- Se a pesquisa terminar sem citações utilizáveis, o painel informa essa limitação e a redação usa apenas os vídeos. Falhas de conexão ou execução interrompem a etapa para retomada.
- O histórico registra tokens de chamadas concluídas. Pesquisa, transcrição e tentativas externas podem ter cobrança adicional no provedor. Não há cálculo de custo monetário nem limite financeiro rígido; configure limites no provedor.
- O perfil limita chamadas do coordenador por ciclo (padrão 24) e rodadas adicionais (padrão 1). Tentativas de transporte do SDK e ferramentas de pesquisa podem acrescentar uso. O perfil é congelado no início: alterá-lo vale para novos ciclos.
- A biblioteca reúne fichas interpretadas e revisadas, não uma cópia integral da documentação. Atualizações entram como versões distribuídas com o app; não há ingestão automática de novas regras da web. Buscas usam SQLite FTS5 e a seleção por responsabilidade do agente.
- O app interpreta orientações Yoast e executa verificações próprias. Não executa o motor oficial do plugin nem promete equivalência de pontuação. Indexação, rastreamento e links internos sem contexto do site aparecem como não verificados. O fluxo editorial não exige conectar WordPress.
- A integração inicial é para WordPress com REST API e Application Password. Não publica automaticamente. Taxonomias e campos de Yoast/Rank Math não são sincronizados; tags, título SEO e metadescrição ficam no pacote exportável.
- O envio registra a intenção antes da requisição. Um timeout impede repetição cega da criação. Se o resultado continuar incerto, conferir manualmente o WordPress; o sistema não cria outra cópia para tentar resolver.
- O aplicativo é de um workspace com um administrador. Não inclui multiusuário, cobrança SaaS, análise de SERP nem monitoramento de ranking.

## Testes

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/app.js
node --check app/static/editorial.js
```

A suíte cobre autenticação, CSRF, segredos, mudança de senha, bloqueio de destinos internos no conector WordPress, URLs do YouTube, timestamps, referências, HTML seguro, recuperação de trabalhos, revisão após edição e envio/reconciliação de rascunhos com transporte simulado. Não consome serviços pagos.

Os testes da redação cobrem execução dos 12 papéis, comunicação, perfil compartilhado, consulta documental, aplicação/desfazimento, rejeição de propostas vencidas, integridade de citações, limite de chamadas, revisão final da versão modificada e retomada após falha durante uma correção.

## Organização

```text
app/
  main.py          API, autenticação e exportação
  db.py            SQLite e versões
  security.py      sessões, segredos e validação de destinos
  youtube.py       extração por link e alternativas
  generation.py    análise, pesquisa, redação e revisão
  pipeline.py      fila, estados e recuperação
  wordpress.py     conexão e rascunhos
  static/          interface, sem dependências de CDN
tests/             testes automatizados
docs/              histórico da especificação
```

## Referências técnicas

- [OpenAI Responses e pesquisa web](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [YouTube Transcript API](https://github.com/jdepoix/youtube-transcript-api)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Supadata: transcrições](https://docs.supadata.ai/get-transcript)
- [WordPress REST API: posts](https://developer.wordpress.org/rest-api/reference/posts/)

Este repositório contém apenas código, documentação e testes. Chaves, senhas, transcrições de trabalho e artigos ficam no armazenamento privado da instalação.
