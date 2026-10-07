# Transcrição de áudio local — versão 1.5.1

## Escolha e capacidade

O padrão usa **faster-whisper**, implementação aberta de Whisper, no próprio
servidor. Não há assinatura nem cota de minutos de uma API de transcrição. CPU,
memória, armazenamento e tempo de processamento continuam sendo recursos finitos.
A geração e revisão editorial com OpenAI têm seu consumo próprio.

O perfil inicial é `small`, CPU, `int8`, duas threads e uma tarefa editorial por
vez. Como ponto de partida operacional, reserve quatro vCPUs, 8 GB de RAM e espaço
para modelo e arquivos; ajuste após medir vídeos representativos no seu servidor.
Isso é uma orientação de capacidade, não uma garantia de desempenho. `medium` e
`large-v3` exigem mais recursos; `tiny` e `base` reduzem consumo com possível perda
de precisão. GPU exige CUDA/cuDNN e uma imagem compatível; o Docker fornecido usa CPU.

O padrão permite 180 minutos e 256 MB por fonte, configuráveis até 360 minutos e
1024 MB. O limite editorial continua sendo 120 mil caracteres por vídeo e 180 mil
por artigo, até cinco vídeos. Nenhuma etapa corta texto para caber nesses limites.
O prazo padrão de inferência é três horas, configurável entre cinco minutos e seis
horas; blocos concluídos são reutilizados na retomada, inclusive ao aumentar o prazo.

## Instalação e deploy

Reconstrua o Dockerfile atualizado e preserve o volume `/data`. A imagem contém
FFmpeg, Node.js 22 e `yt-dlp[default]`, incluindo os componentes EJS usados para
obter áudio do YouTube. Whisper, CTranslate2, PyAV e demais componentes centrais
têm versões fixadas. PyAV 19 removeu uma opção usada pelo faster-whisper 1.2.1;
esta distribuição fixa uma versão compatível, sem alterar código de bibliotecas.

O modelo é baixado de seu repositório público no primeiro uso e permanece em
`/data/whisper-models`. Esse primeiro carregamento exige rede e espaço em disco;
não é repetido em cada vídeo ou deploy quando o volume é preservado. A configuração
avançada `WHISPER_MODEL_REVISION` permite fixar uma revisão do modelo. Downloads
de pesos podem ter limites do hospedeiro; a inferência local depois do cache não
depende de uma cota remota de minutos.

Em **Integrações → Transcrição dos vídeos**, mantenha **Áudio → Whisper local**,
confira proxies e ajuste duração, tamanho, modelo e threads de acordo com o servidor.
Valores salvos no painel prevalecem sobre variáveis de ambiente. Instalações antigas
sem uma escolha explícita passam a usar áudio local. Os modos de legendas e Supadata
permanecem disponíveis por escolha explícita; `auto` antigo corresponde ao padrão local.

O painel é administrado pelo desktop; a conferência de fontes, reprodução de áudio,
diagnósticos e configurações de capacidade são orientados a telas grandes.

## Obter e conferir as fontes

1. O sistema procura uma transcrição automática de áudio recente no estúdio. Uma
   legenda não pode substituir esse áudio no modo local.
2. Obtém o áudio usando as conexões configuradas, até três rotas disponíveis por
   consulta. No padrão **Automática**, tenta primeiro a conexão direta do servidor
   e depois proxies disponíveis. Ter proxies salvos não exclui a conexão direta.
   **Somente proxies** e **Somente conexão direta** permitem restringir essa escolha
   em Integrações. Instalações sem escolha anterior adotam Automática; quando o uso
   exclusivo dos proxies for necessário, salve Somente proxies antes de extrair.
   Rotas de download e de legendas têm saúde separada. Bloqueio de IP,
   falha de autenticação, restrição do vídeo, ausência de ferramentas e falha de
   conexão recebem diagnósticos distintos. Credenciais e respostas brutas não são
   exibidas nos diagnósticos.
3. Confere formato, duração e tamanho. Decodifica blocos de até dez minutos para
   PCM mono de 16 kHz. Um bloco cuja duração decodificada não corresponde à original
   é recusado; não se aceita uma transcrição parcialmente decodificada como completa.
4. Whisper transcreve com detecção de fala, sem condicionamento automático ao texto
   anterior. Timestamps globais e blocos concluídos ficam persistidos. O progresso
   aparece na aba Fontes; o processo separado limita tempo e libera memória depois.
5. O texto validado entra na apuração editorial. Trechos de baixa confiança são
   sinalizados no painel e enviados como limitações à apuração. Quando sustentam
   informações usadas no artigo, geram pendências de conferência no áudio original;
   uma aprovação textual do modelo não elimina essa pendência. Isso não certifica
   nomes, números, termos ou fatos: confira o áudio nos trechos importantes. Dados
   exibidos apenas na tela continuam exigindo conferência adicional.

O coordenador preserva fontes concluídas quando outra falha e não inicia redação
sem todas as bases. Retranscrever legendas anteriores preserva a fonte anterior no
histórico e invalida plano/revisão que dependiam dela.

## Quando o YouTube bloqueia os proxies

Em **Fontes → Testar acesso ao áudio no servidor**, a aplicação usa as conexões
salvas para verificar uma amostra de áudio, sem iniciar Whisper, pesquisa ou redação.
O teste leva até 45 segundos, respeita conexões em pausa e reaproveita resultados
iguais por 60 segundos. O resultado identifica a conexão por um código sem revelar
host, usuário ou senha. Uma amostra acessível não garante o download integral.
O teste deve ser feito depois de salvar mudanças de conexão em Integrações.

Os diagnósticos distinguem verificação antirobô, limite de consultas, autenticação
do proxy, confirmação de idade, sessão exigida e indisponibilidade do vídeo. Uma
exigência de idade não desativa a conexão para os demais vídeos. Tokens de reprodução
são identificados quando o extrator informa sua ausência; não são tratados como
solução universal para bloqueio de IP.

Whisper transcreve um áudio disponível; não remove restrições de download do YouTube.
Trocar o reconhecedor não torna um proxy bloqueado funcional. Um serviço de proxies
precisa ser compatível com o acesso desejado; não há promessa de download irrestrito.

Na aba **Fontes**, **Enviar áudio para transcrição local** aceita MP3, WAV, M4A,
MP4, WebM, OGG, FLAC e AAC. O upload é privado, autenticado e limitado durante o
recebimento, sem carregar o arquivo inteiro na memória. Apenas a transcrição é
enfileirada; enviar não chama a redação nem um provedor pago. Formatos são conferidos
com demuxers específicos, sem aceitar playlists disfarçadas que busquem outros arquivos.
O endpoint de áudio usa o limite de áudio configurado, em vez do limite geral de
1,5 MB para requisições comuns. Se o proxy reverso do seu servidor tiver um limite
menor de upload, ele também precisa ser ajustado na infraestrutura.

Arquivos enviados permanecem associados ao artigo no volume privado; não são
reutilizados automaticamente em outros artigos como se fossem áudio do YouTube.
O vínculo entre o arquivo e o link informado fica identificado como não verificado.
Downloads de áudio têm cache de 24 horas; limpeza ocorre entre tarefas. Modelos e
entregas textuais/checkpoints persistem. Monitore e faça backup do volume, incluindo
os originais enviados, segundo a política de retenção da instalação.

Texto revisado, TXT, SRT ou VTT também pode ser fornecido. O sistema preserva
timestamps recebidos e não inventa marcações para texto simples.

## Provedores opcionais

No modo de legendas, Supadata pode servir de alternativa se houver chave. No modo
Supadata, o padrão `native` pede somente legendas; `auto` autoriza geração remota
e pode ser cobrado por duração. O modo local não chama esses serviços.

Pedidos externos HTTP 202 são persistidos antes de consultar os resultados. A
retomada usa o mesmo identificador, respeita `Retry-After` e nunca reenvia
silenciosamente uma solicitação cujo envio ficou sem confirmação. Essa situação
exige conferir a conta e autorizar **Reiniciar pedido externo**. Pedidos pendentes
não podem ser substituídos por um novo envio. Falhas definitivas permanecem no
diagnóstico; trocar credenciais/mode cria uma configuração distinta.

## Referências técnicas

- [faster-whisper: instalação, CPU, VAD e modelos](https://github.com/SYSTRAN/faster-whisper)
- [yt-dlp: requisitos JavaScript e EJS](https://github.com/yt-dlp/yt-dlp/wiki/ejs)
- [Compatibilidade entre faster-whisper e PyAV 19](https://github.com/SYSTRAN/faster-whisper/issues/1492)
- [Supadata: formato de transcrições e consultas assíncronas](https://docs.supadata.ai/get-transcript)
