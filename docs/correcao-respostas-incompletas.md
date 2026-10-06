# Respostas incompletas — versão 1.1.1

A leitura automática do SDK podia lançar `ValidationError` antes de o aplicativo
verificar o status da resposta. Uma saída cortada interrompia a redação, deixava
de registrar o consumo dessa chamada e exibia parte da resposta no erro da tela.

O aplicativo agora pede o mesmo JSON Schema estrito por `responses.create`, salva
o consumo e verifica status, recusa e mensagem final antes de validar o conteúdo.
Somente uma resposta completa e válida pode virar entrega editorial. JSON parcial
não é reparado nem salvo como artigo.

Uma falha de formato ou limite de saída permite uma tentativa automática adicional
por etapa e ciclo. Ela passa pelo coordenador, conta no orçamento e fica no histórico.
Se houve limite de saída, a tentativa recebe até 50% mais tokens, com teto de 16 mil.
Recusas, filtros do provedor e orçamento esgotado não acionam essa recuperação.
Uma segunda falha interrompe a etapa com uma mensagem legível; a retomada reaproveita
as entregas concluídas. Falhas antigas de validação também são redigidas de forma
segura na tela, sem alterar os registros ou artigos existentes.

Mantivemos a versão editorial e os checkpoints, evitando reiniciar ciclos antigos.
O conversor de schema é o mesmo helper interno do SDK OpenAI fixado em requirements;
os testes exercitam a serialização HTTP dessa dependência. Ao atualizar o SDK, rode
os testes de recuperação e de schemas novamente.

Referência: [tratamento de respostas estruturadas incompletas e recusas](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
