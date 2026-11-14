# HITMO VIDEO AI

Projeto Android inicial do HITMO VIDEO AI.

## Voice Video Editor API (experimental)

Backend inicial disponível em `backend/`:
- recebe vídeo MP4/MOV/M4V/WEBM;
- extrai o áudio com FFmpeg;
- transcreve em português com timestamps usando Whisper;
- gera legendas SRT;
- detecta frases de comando cadastradas e devolve sugestões com tempo de entrada.

**Estado desta entrega:** transcrição e detecção de comandos implementadas no código. A inserção de imagens/animações e a renderização/exportação do MP4 editado ainda não estão implementadas. O serviço também ainda precisa ser publicado e configurado com uma chave OpenAI API.

## Deploy

O `render.yaml` prepara o serviço no Render. Conecte o repositório, selecione a branch `feature/voice-editor-api`, configure o segredo `OPENAI_API_KEY` no painel e faça o deploy. Valide a URL pública em `/health`.

Documentação local e teste: consulte `backend/README.md` e `/docs`.
