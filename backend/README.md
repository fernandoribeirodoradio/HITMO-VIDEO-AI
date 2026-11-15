# HITMO VIDEO AI — Backend

Backend privado de edição de vídeo com processamento local/gratuito. A transcrição usa Whisper.cpp; a composição e renderização usam FFmpeg. Não depende de chave da API OpenAI/GPT.

## Variáveis de ambiente

Configure no Render:
- `HITMO_API_TOKEN`: token secreto longo para proteger os endpoints.
- `MAX_VIDEO_BYTES`: opcional; padrão 209715200 (200 MB).
- `WHISPER_THREADS`: opcional; padrão 2.
- `WHISPER_BINARY` e `WHISPER_MODEL`: já configuradas pelo Dockerfile.

Não coloque tokens no GitHub, no arquivo OpenAPI ou em mensagens públicas.

## Deploy no Render

Use um Web Service a partir deste repositório:
- Branch: `feature/voice-editor-api`
- Root Directory: `backend`
- Runtime: Docker
- Dockerfile: `Dockerfile`
- Health Check Path: `/health`

O Dockerfile compila Whisper.cpp, baixa o modelo tiny quantizado, instala FFmpeg e fontes para legendas. O build valida as bibliotecas compartilhadas do Whisper antes de iniciar o serviço.

## Endpoints

- `GET /health`: informa se o binário e o modelo Whisper estão presentes.
- `POST /v1/transcribe`: recebe um vídeo no campo multipart `video` e devolve transcrição, segmentos temporizados, SRT e comandos detectados.
- `POST /v1/edit`: recebe o vídeo no campo `video` e, opcionalmente, um arquivo `.md` ou `.txt` no campo `instructions`. Transcreve com Whisper.cpp, aplica legendas ASS animadas e renderiza MP4 com FFmpeg. O resultado é devolvido como arquivo MP4.

Todos os endpoints POST exigem `Authorization: Bearer <HITMO_API_TOKEN>`.

## Estado da edição inicial

A primeira versão de edição aplica legendas animadas estilo karaoke em branco/amarelo. Se o briefing pedir zoom ou aproximação, aplica zoom discreto; se pedir limpeza de ruído, aplica filtro de redução de ruído. Preserva a proporção original e exporta H.264/AAC.

Cortes inteligentes de silêncio, análise semântica de quadros, troca de câmera real, geração de imagens/B-roll e overlays contextuais avançados ainda não estão implementados. O arquivo `../HITMO-AGENT.md` descreve a direção completa do agente; ele é uma especificação de comportamento, não um motor de execução.

## Teste rápido

1. Consulte `/health` e confirme `status=ok`, `engine_available=true`, `model_available=true`.
2. Envie um vídeo MP4/MOV/M4V/WEBM e um briefing `.md`/ `.txt` para `POST /v1/edit`.
3. Confira se a resposta é um arquivo MP4 reproduzível.
