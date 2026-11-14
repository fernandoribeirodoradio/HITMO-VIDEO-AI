# HITMO VIDEO AI — MCP Bridge

Serviço MCP independente que expõe as ferramentas `check_health`, `transcribe_video` e `transcribe_chatgpt_file`, reutilizando a API REST já existente. Não altera o serviço de processamento em `backend/`.

## Deploy no Render

Crie um **novo Web Service** a partir deste mesmo repositório:

- Branch: `feature/mcp-bridge`
- Root Directory: `mcp-bridge`
- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `python server.py`
- Health Check Path: `/health`

O endpoint MCP será `https://SEU-NOVO-SERVICO.onrender.com/mcp`.

## Variáveis secretas no Render

Configure no novo serviço:

- `HITMO_API_BASE_URL` = `https://hitmo-video-ai.onrender.com`
- `HITMO_API_TOKEN` = o mesmo token já configurado no serviço da API (não copiar para GitHub/chat).
- `MCP_ACCESS_TOKEN` = um token separado, longo e aleatório, para proteger o endpoint MCP.
- `MAX_VIDEO_BYTES` = `209715200` (opcional).

**Não reutilize o token da API como token de acesso MCP.** O plugin ChatGPT usará o token MCP no fluxo seguro de credenciais; o segredo `HITMO_API_TOKEN` permanece exclusivamente no ambiente do servidor MCP.

## Ferramentas

### `check_health`
Consulta `GET /health` e devolve o resultado HTTP real.

### `transcribe_video`
Recebe um arquivo por `openai/fileParams`, valida nome/extensão/MIME e URL temporária HTTPS do host de arquivos do ChatGPT, baixa o arquivo com limite de 200 MB e envia multipart no campo `video` para `POST /v1/transcribe`.

### `transcribe_chatgpt_file`
Recebe o mesmo tipo de arquivo e traduz os campos de arquivo do ChatGPT (`download_url`, `file_id`, `mime_type`, `file_name`) para o contrato que a implementação REST realmente espera (`name`, `id`, `mime_type`, `download_link`) em `POST /v1/transcribe-from-gpt`.

## Validação da resposta

Só retorna sucesso quando a API responde HTTP 2xx com JSON válido e `status=transcribed`. Valida `text`, `segments` (start/end/text), `subtitles_srt` e `voice_commands`. Erros/timeouts não são apresentados como transcrição concluída.

## Segurança

- `HITMO_API_TOKEN` só é lido pelo processo servidor e enviado no cabeçalho Bearer à API REST.
- `MCP_ACCESS_TOKEN` protege as chamadas ao endpoint MCP e é diferente do token da API.
- URLs de arquivos são limitadas a HTTPS e ao host `files.oaiusercontent.com` para reduzir risco de SSRF.
- Segredos não são escritos em logs nem retornados nas respostas.
