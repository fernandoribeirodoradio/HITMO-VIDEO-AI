# Backend API

API experimental de transcrição e detecção inicial de comandos de voz do HITMO VIDEO AI.

## Variáveis de ambiente

Configure no provedor de hospedagem:

- `OPENAI_API_KEY`: chave da API OpenAI.
- `HITMO_API_TOKEN`: token secreto longo usado no cabeçalho `Authorization: Bearer`.
- `TRANSCRIPTION_MODEL`: opcional; padrão `whisper-1`.
- `MAX_VIDEO_BYTES`: opcional; padrão 209715200 (200 MB).
- `CORS_ORIGINS`: opcional; origens separadas por vírgula.

**Não publique o serviço sem configurar `HITMO_API_TOKEN` e `OPENAI_API_KEY`.** Nunca coloque esses valores no GitHub, no arquivo OpenAPI ou em mensagens públicas.

## Deploy no Render

1. Crie um Web Service a partir deste repositório.
2. Selecione a branch `feature/voice-editor-api`.
3. Use o arquivo `render.yaml` (ou configure Docker com `backend/Dockerfile` e contexto `backend`).
4. No painel Environment, adicione `OPENAI_API_KEY` e `HITMO_API_TOKEN) como valores secretos.
5. Faça o deploy e teste `https://SEU-SERVICO.onrender.com/health`.

## GPT Action

O contrato OpenAPI está em `backend/openapi.yaml`. Antes de importar no editor de GPTs, substitua `https://SEU-SERVICO.onrender.com` pela URL real do serviço. Configure autenticação do tipo API Key / Bearer com o mesmo valor de `HITMO_API_TOKEN`.

## Endpoint de transcrição

`POST /v1/transcribe` recebe um campo multipart chamado `video` (MP4, MOV, M4V ou WEBM). Exige token Bearer. Retorna transcrição, segmentos temporizados, SRT e sugestões de comandos.

## Limitações atuais

Esta versão transcreve e identifica sugestões de efeitos; ainda **não** insere imagens/animações nem renderiza/exporta o MP4 editado. O upload também está sujeito ao limite de 200 MB e o áudio extraído deve ficar abaixo de 24 MB.
