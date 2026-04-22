# AgroVision AI

Projeto didático de monitoramento com **FastAPI + OpenCV + YOLO + agente com Gemini**.

## Requisitos

- Python 3.11+
- Webcam local ou stream público/autorizado
- Chave de API do Gemini

## Setup rápido

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Crie o `.env` com base no arquivo exemplo:

```bash
cp .env.example .env
```

Configuração mínima no `.env`:

```env
GEMINI_API_KEY=...sua-chave...
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.0-flash,gemini-2.0-flash-lite
CAMERA_SOURCE=0
```

## Rodar aplicação

```bash
python -m uvicorn app:app --reload
```

Abra:

- `http://127.0.0.1:8000`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/camera/status`
- `http://127.0.0.1:8000/events`
- `http://127.0.0.1:8000/agent/status`

## Fluxo atual

1. OpenCV abre a câmera/stream.
2. YOLO detecta objetos e desenha boxes.
3. Eventos relevantes são salvos no SQLite (`detections.db`).
4. O agente monta contexto operacional com eventos recentes.
5. O backend chama a API Gemini e retorna resposta no chat.

## Endpoints principais

- `GET /health`
- `GET /camera/status`
- `GET /events`
- `GET /frame`
- `GET /video_feed`
- `GET /agent/status`
- `POST /chat`

Exemplo de payload para `POST /chat`:

```json
{
  "question": "Leia os eventos recentes e avalie o risco.",
  "history": [
    {"role": "user", "content": "O que foi detectado?"},
    {"role": "assistant", "content": "Houve recorrência de veículos."}
  ]
}
```
