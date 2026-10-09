# Recuperação de marcações de tempo do Whisper — 1.5.21

O vídeo `cVnRvZ8uMCo`, de aproximadamente 193 segundos, falhava repetidamente perto
de 98% no servidor. O diagnóstico persistido confirmou `ValueError` na validação
de tempos de `whisper_worker.py`. O aviso genérico sobre memória não identificava
essa causa. A reprodução local com o mesmo modelo terminou normalmente; ela,
isoladamente, não demonstrava recuperação em produção.

Cada bloco agora é acumulado separadamente antes de entrar no checkpoint. Quando
o motor retorna tempos inválidos, o mesmo bloco recebe uma única nova passagem
com alinhamento por palavra (`word_timestamps=True`). Os blocos já concluídos não
são repetidos. O alinhamento usa o áudio original com o Whisper instalado no
servidor, sem API de IA paga.

Tempos não finitos, invertidos ou fora do áudio não são aceitos. Apenas desvios
de arredondamento de até 50 ms nas bordas são normalizados para os limites do
áudio; excessos maiores exigem o alinhamento. Se a recuperação também falhar, o
checkpoint permanece intacto e o erro é identificado como `audio_timestamps`.
Nenhum resultado incompleto é marcado como transcrição concluída.

Ao retomar, o supervisor limpa o erro e o progresso antigos, preservando os
checkpoints. A interface passa a mostrar quando há realinhamento. O resultado
registra os índices dos blocos recuperados.

O endpoint autenticado `GET /api/jobs/{job_id}/sources/{video_id}/diagnostics`
consulta somente as tentativas da fonte pertencente ao artigo. Ele expõe modelo,
duração, contadores e exceção com nomes de arquivos/funções. Não entrega áudio,
transcrição, caminhos absolutos, mensagens livres de exceções ou logs.

Os testes cobrem isolamento e autenticação do diagnóstico, recuperação, limite
de tentativas, ausência de duplicação, preservação do checkpoint, arredondamento,
limite de caracteres e limpeza de progresso antigo. A verificação real em
produção usa exclusivamente o modo `extract`, que termina antes da redação.

Referência: [alinhamento por palavra no faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py).
