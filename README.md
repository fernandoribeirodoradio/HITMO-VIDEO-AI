# HITMO VIDEO AI

Editor pessoal de vídeo com processamento local/gratuito.

## O que existe nesta versão
- Whisper.cpp para transcrição em português com timestamps.
- Geração de legendas ASS animadas estilo karaoke.
- Endpoint `POST /v1/edit`: recebe um vídeo e um briefing `.md` ou `.txt`, renderiza e devolve MP4.
- FFmpeg para composição e exportação H.264/AAC.
- Arquivo de comportamento do agente: [HITMO-AGENT.md](HITMO-AGENT.md).
- Modelo de briefing por vídeo: [HITMO-BRIEF.md](HITMO-BRIEF.md).

## Deploy do backend
No Render, use:
- Branch: `feature/voice-editor-api`
- Root Directory: `backend`
- Runtime: Docker
- Dockerfile: `Dockerfile`
- Health Check Path: `/health`
- Segredo: `HITMO_API_TOKEN`

Não é necessária chave paga da API OpenAI/GPT.

## Limitações da primeira versão de edição
A primeira versão aplica legendas animadas, zoom discreto e redução de ruído quando explicitamente pedidos no briefing. Cortes inteligentes de silêncio, análise semântica de imagem, troca real de câmera e inserção automática de imagens/B-roll ainda estão em desenvolvimento.

Consulte [backend/README.md](backend/README.md) para detalhes e teste.
