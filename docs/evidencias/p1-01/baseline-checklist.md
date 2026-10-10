# Piloto editorial P1_01

O caso obrigatório usa somente `https://www.youtube.com/watch?v=cVnRvZ8uMCo`, produção própria declarada pelo usuário, autorizada para testes. Um vídeo é suficiente. Os demais vídeos não são necessários ao aceite deste piloto.

A fonte histórica já preservada está em `.local/premium-inputs/cVnRvZ8uMCo-sources.json`; o manifesto registra seu hash. Este arquivo privado permanece fora do Git. Disponibilidade da fonte histórica não certifica sua fidelidade ao vídeo nem a qualidade de uma geração atual. A captura não chama provedores, transcrição ou geração.

Para preparar o relatório atual, execute na raiz:

```powershell
.venv/Scripts/python.exe scripts/premium_baseline.py --manifest docs/evidencias/p1-01/baseline-manifest.json --output .local/p1-01-baseline
```

O relatório inclui fingerprint dos bytes do checkout, versão do aplicativo, pipeline, modelo candidato e sua origem. O modelo candidato vem da configuração local quando um banco é indicado, ou do padrão encontrado no código. Ele não certifica o modelo usado em produção. Modelo efetivo, perfil congelado, versão usada, duração e custo pertencem ao recibo de uma execução real.

**Situação atual: pendente.** Não existe artigo atual nem autorização de orçamento específico para chamar um serviço pago neste piloto. Custo, tempo de geração, avaliação humana e melhoria demonstrada permanecem nulos ou não confirmados. Nenhum resultado histórico é apresentado como custo de uma geração atual; percentis precisam da amostra mínima documentada no relatório financeiro.

Após uma geração especificamente autorizada, registrar `run.run_id`, `run.model`, `run.profile`, `run.app_sha256`, `run.wall_seconds` e o `job_id`/snapshot correspondente. `run_id` seleciona somente o uso da execução; o intervalo de índices permanece compatível para recibos antigos. O relatório financeiro do banco mede as tentativas, reservas e serviços externos. A soma de tokens do snapshot é uma medição parcial; não representa fatura, CPU, infraestrutura ou trabalho humano.

A avaliação humana tem notas de 0 a 5, justificativa, IDs da fonte e trechos do artigo. Pesos preservados da ferramenta existente: fidelidade 30%, utilidade 25%, naturalidade/clareza 20%, precisão/atribuição 15% e apresentação SEO 10%. As notas começam nulas. O avaliador precisa conferir:

- Fidelidade das afirmações, métodos, exemplos, condições e ressalvas ao vídeo original.
- Resposta completa e concisa, sem texto acrescentado para atingir tamanho obrigatório.
- Clareza, continuidade, referentes compreensíveis e linguagem objetiva.
- Ausência de experiências pessoais inventadas, falsa autoria ou opiniões convertidas em fatos.
- Conhecimento complementar identificado, atribuído e verificável, quando houver.
- Crédito e referências corretos; qualidade editorial não substitui validação técnica de entrega.

As observações e a avaliação do benchmark não bloqueiam exportação ou envio autorizado de um artigo tecnicamente válido. Nenhum ganho financeiro, de SEO ou de qualidade é afirmado antes de uma comparação nas mesmas condições.
