# AgroVision AI

Projeto didático de monitoramento com **FastAPI + OpenCV + YOLO + agente com Gemini**.
Agora com camada de **web scraping de commodities agrícolas** para enriquecer a análise.

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

Configuração opcional do scraper de commodities:

```env
COMMODITY_SCRAPER_SOURCE_URL=https://www.indexmundi.com/commodities/
COMMODITY_SCRAPER_TIMEOUT_SECONDS=12
COMMODITY_SCRAPER_CACHE_TTL_SECONDS=900
COMMODITY_SCRAPER_MAX_ITEMS=6
COMMODITY_SCRAPER_TARGETS=Maize (corn);Soybeans;Wheat;Sugar;Beef;Coffee, Other Mild Arabicas
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
- `http://127.0.0.1:8000/commodities`
- `http://127.0.0.1:8000/agent/status`

## Fluxo atual

1. OpenCV abre a câmera/stream.
2. YOLO detecta objetos e desenha boxes.
3. Eventos relevantes são salvos no SQLite (`detections.db`).
4. O agente monta contexto operacional com eventos recentes.
5. A camada de scraping coleta cotação pública de commodities com cache/rate-limit.
6. O agente combina eventos + commodities e chama a API Gemini para responder no chat.

## Endpoints principais

- `GET /health`
- `GET /camera/status`
- `GET /events`
- `GET /commodities`
- `GET /frame`
- `GET /video_feed`
- `GET /agent/status`
- `POST /chat`

## Web scraping implementado

Documentação completa desta camada:
- [docs/WEB_SCRAPING_COMMODITIES.md](/Users/gustavoferreira/Documents/Faculdade/bigodão/agrovision_ia/docs/WEB_SCRAPING_COMMODITIES.md)

- **Fonte pública e gratuita:** IndexMundi (`/commodities`).
- **Serviço separado:** `services/commodity_scraper.py`.
- **Tratamento de erro:** se a fonte cair, retorna erro e tenta usar cache anterior (stale).
- **Limite de requisições:** cache com TTL (`COMMODITY_SCRAPER_CACHE_TTL_SECONDS`) evita múltiplas chamadas seguidas.
- **Formato estruturado:** retorno JSON com `items`, `data_as_of`, `cached`, `stale` e metadados da fonte.
- **Integração no sistema:** dados aparecem no dashboard e entram no contexto do agente em `/chat`.

### Finalidade no AgroVision

Os eventos de visão (pessoas, veículos, máquinas, movimentação) mostram **o que está acontecendo no campo**.
As commodities mostram **o contexto econômico do momento**.
Juntos, isso permite recomendações operacionais melhores, por exemplo:

- priorizar segurança/logística em períodos de alta de preço;
- contextualizar risco operacional com impacto potencial de mercado;
- apoiar decisão de resposta rápida com visão técnica + econômica.

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
