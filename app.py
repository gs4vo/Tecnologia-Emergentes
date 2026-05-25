import os
import cv2
import time
import uuid
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from services.commodity_scraper import CommodityScraperService, parse_target_commodities
from ultralytics import YOLO

load_dotenv()


# =========================
# CONFIGURACOES
# =========================
def parse_camera_source(value: str):
    value = value.strip()
    if value.isdigit():
        return int(value)
    return value


def detect_source_type(source) -> str:
    if isinstance(source, int):
        return "webcam"

    lower = str(source).lower()
    if lower.startswith("rtsp://"):
        return "rtsp"
    if lower.startswith("http://") or lower.startswith("https://"):
        return "stream"
    if lower.endswith((".mp4", ".avi", ".mov", ".mkv")):
        return "file"
    return "unknown"


def normalize_gemini_model_name(model_name: str) -> str:
    normalized = model_name.strip()
    if normalized.startswith("models/"):
        normalized = normalized.split("/", 1)[1]
    return normalized


def parse_gemini_model_list(raw_value: str) -> list[str]:
    models = []
    for item in raw_value.split(","):
        normalized = normalize_gemini_model_name(item)
        if normalized:
            models.append(normalized)
    return models


CAMERA_SOURCE_RAW = os.getenv("CAMERA_SOURCE", "0")
CAMERA_SOURCE = parse_camera_source(CAMERA_SOURCE_RAW)
CAMERA_SOURCE_TYPE = detect_source_type(CAMERA_SOURCE)
CAMERA_RECONNECT_SECONDS = float(os.getenv("CAMERA_RECONNECT_SECONDS", "5"))

MODEL_PATH = os.getenv("MODEL_PATH", "yolo11n.pt")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.45"))
TARGET_CLASSES = {
    name.strip()
    for name in os.getenv("TARGET_CLASSES", "person,car,motorcycle,truck,bus").split(",")
    if name.strip()
}
MIN_CONSECUTIVE_FRAMES = int(os.getenv("MIN_CONSECUTIVE_FRAMES", "3"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "20"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = normalize_gemini_model_name(
    os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
)
GEMINI_FALLBACK_MODELS = parse_gemini_model_list(
    os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.0-flash,gemini-2.0-flash-lite")
)
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "120"))
AGENT_EVENT_LIMIT = int(os.getenv("AGENT_EVENT_LIMIT", "12"))
MAX_HISTORY_MESSAGES = int(os.getenv("AGENT_MAX_HISTORY_MESSAGES", "8"))

COMMODITY_SCRAPER_SOURCE_URL = os.getenv(
    "COMMODITY_SCRAPER_SOURCE_URL",
    "https://www.indexmundi.com/commodities/",
).strip()
COMMODITY_SCRAPER_TIMEOUT_SECONDS = float(
    os.getenv("COMMODITY_SCRAPER_TIMEOUT_SECONDS", "12")
)
COMMODITY_SCRAPER_CACHE_TTL_SECONDS = int(
    os.getenv("COMMODITY_SCRAPER_CACHE_TTL_SECONDS", "900")
)
COMMODITY_SCRAPER_MAX_ITEMS = int(os.getenv("COMMODITY_SCRAPER_MAX_ITEMS", "6"))
COMMODITY_SCRAPER_TARGETS = parse_target_commodities(
    os.getenv(
        "COMMODITY_SCRAPER_TARGETS",
        "Maize (corn);Soybeans;Wheat;Sugar;Beef;Coffee, Other Mild Arabicas",
    )
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
SAVE_DIR = STATIC_DIR / "captures"
DB_PATH = BASE_DIR / "detections.db"

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


# =========================
# APP
# =========================
app = FastAPI(title="AgroVision AI")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
SAVE_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

model = None
model_error = None

last_frame = None
last_frame_lock = threading.Lock()

camera_connected = False
camera_last_error = ""
camera_state_lock = threading.Lock()

active_gemini_model = GEMINI_MODEL
active_gemini_model_lock = threading.Lock()

detection_state = defaultdict(int)
last_alert_time = defaultdict(lambda: 0.0)
alerted_labels = defaultdict(bool)

commodity_scraper = CommodityScraperService(
    source_url=COMMODITY_SCRAPER_SOURCE_URL,
    timeout_seconds=COMMODITY_SCRAPER_TIMEOUT_SECONDS,
    cache_ttl_seconds=COMMODITY_SCRAPER_CACHE_TTL_SECONDS,
    target_commodities=COMMODITY_SCRAPER_TARGETS,
    max_items=COMMODITY_SCRAPER_MAX_ITEMS,
)


# =========================
# AGENTE
# =========================
@dataclass(frozen=True)
class AgentProfile:
    name: str
    role: str
    goal: str


AGENT_PROFILE = AgentProfile(
    name="Agente AgroVision",
    role="triagem operacional de eventos",
    goal="Analisar deteccoes recentes, explicar riscos e sugerir a proxima acao.",
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    events_in_context: int


# =========================
# BANCO DE DADOS
# =========================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_time TEXT,
            label TEXT,
            confidence REAL,
            image_path TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_event(event_id: str, label: str, confidence: float, image_path: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO events (id, event_time, label, confidence, image_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            label,
            confidence,
            image_path,
        ),
    )
    conn.commit()
    conn.close()


def list_events(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, event_time, label, confidence, image_path
        FROM events
        ORDER BY event_time DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "event_time": row[1],
            "label": row[2],
            "confidence": row[3],
            "image_path": row[4],
        }
        for row in rows
    ]


# =========================
# FUNCOES DE DETECCAO
# =========================
def load_model() -> None:
    global model, model_error
    try:
        model = YOLO(MODEL_PATH)
        model_error = None
        print(f"Modelo carregado com sucesso: {MODEL_PATH}")
    except Exception as exc:  # pragma: no cover
        model = None
        model_error = str(exc)
        print(f"Falha ao carregar modelo YOLO: {exc}")


def draw_box(frame, x1, y1, x2, y2, label, conf) -> None:
    text = f"{label} {conf:.2f}"
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(
        frame,
        text,
        (x1, max(20, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
    )


def should_alert(label: str) -> bool:
    now = time.time()
    return (now - last_alert_time[label]) > ALERT_COOLDOWN_SECONDS


def update_camera_state(connected: bool, error: str = "") -> None:
    global camera_connected, camera_last_error
    with camera_state_lock:
        camera_connected = connected
        camera_last_error = error


def process_stream() -> None:
    global last_frame

    while True:
        cap = cv2.VideoCapture(CAMERA_SOURCE)
        if not cap.isOpened():
            update_camera_state(False, "Erro ao abrir camera/stream")
            print("Erro ao abrir camera/stream. Nova tentativa em breve.")
            time.sleep(CAMERA_RECONNECT_SECONDS)
            continue

        update_camera_state(True)
        print("Camera/stream iniciado com sucesso.")

        while True:
            ok, frame = cap.read()
            if not ok:
                update_camera_state(False, "Falha na leitura de frame")
                print("Falha ao ler frame. Reconectando...")
                break

            if model is not None:
                results = model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)
                found_labels_in_frame = set()
                best_conf_by_label = {}

                for result in results:
                    if result.boxes is None:
                        continue

                    for box in result.boxes:
                        cls_id = int(box.cls[0].item())
                        conf = float(box.conf[0].item())
                        label = model.names[cls_id]

                        if label not in TARGET_CLASSES:
                            continue

                        found_labels_in_frame.add(label)
                        if label not in best_conf_by_label or conf > best_conf_by_label[label]:
                            best_conf_by_label[label] = conf

                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        draw_box(frame, x1, y1, x2, y2, label, conf)

                for label in TARGET_CLASSES:
                    if label in found_labels_in_frame:
                        detection_state[label] += 1
                    else:
                        detection_state[label] = 0
                        alerted_labels[label] = False

                for label in found_labels_in_frame:
                    if (
                        detection_state[label] >= MIN_CONSECUTIVE_FRAMES
                        and not alerted_labels[label]
                        and should_alert(label)
                    ):
                        event_id = str(uuid.uuid4())[:8]
                        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}_{event_id}.jpg"
                        filepath = SAVE_DIR / filename

                        cv2.imwrite(str(filepath), frame)
                        image_path = f"/static/captures/{filename}"
                        confidence = best_conf_by_label.get(label, 0.0)

                        save_event(event_id, label, confidence, image_path)
                        last_alert_time[label] = time.time()
                        alerted_labels[label] = True
                        print(f"[ALERTA] {label} detectado. Evidencia salva em {filepath}")

            with last_frame_lock:
                last_frame = frame.copy()

            time.sleep(0.05)

        cap.release()
        time.sleep(CAMERA_RECONNECT_SECONDS)


# =========================
# AGENTE + GEMINI
# =========================
def normalize_history(history: list[ChatMessage]) -> list[dict[str, str]]:
    normalized = []
    for item in history:
        content = item.content.strip()
        if not content:
            continue
        normalized.append({"role": item.role, "content": content})
    return normalized[-MAX_HISTORY_MESSAGES:]


def build_event_context(events: list[dict]) -> str:
    if not events:
        return (
            "Contexto operacional para o agente:\n"
            "- Eventos considerados: 0\n"
            "- Nao ha deteccoes recentes para interpretar."
        )

    latest = events[0]
    distribution = Counter(event["label"] for event in events)
    distribution_text = ", ".join(f"{label}: {count}" for label, count in distribution.items())
    avg_confidence = sum(event["confidence"] for event in events) / len(events)

    recent_lines = []
    for event in events[:8]:
        recent_lines.append(
            f"- #{event['id']} | {event['event_time']} | {event['label']} | conf={event['confidence']:.2f}"
        )

    return (
        "Contexto operacional para o agente:\n"
        f"- Eventos considerados: {len(events)}\n"
        f"- Evento mais recente: {latest['label']} em {latest['event_time']}\n"
        f"- Distribuicao recente: {distribution_text}\n"
        f"- Confianca media: {avg_confidence:.2f}\n"
        "Eventos recentes:\n"
        + "\n".join(recent_lines)
    )


def build_commodity_context(commodity_snapshot: dict) -> str:
    return commodity_scraper.build_context_text(commodity_snapshot)


def build_agent_messages(
    question: str,
    history: list[ChatMessage],
    events: list[dict],
    commodity_snapshot: dict,
) -> list[dict[str, str]]:
    system_prompt = (
        f"Voce e o {AGENT_PROFILE.name}, um agente de {AGENT_PROFILE.role}. "
        f"Objetivo: {AGENT_PROFILE.goal} "
        "Trate os dados como monitoramento operacional autorizado de ambiente real. "
        "Responda em portugues do Brasil, de forma direta e util. "
        "Use os eventos fornecidos como fonte principal e use o contexto de commodities "
        "como complemento para impacto economico. "
        "Nao invente dados que nao aparecem no contexto. "
        "Nao tente identificar pessoas; fale apenas sobre eventos, riscos e proximas acoes. "
        "Quando fizer sentido, organize a resposta em: leitura, risco e recomendacao."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": build_event_context(events)},
        {"role": "system", "content": build_commodity_context(commodity_snapshot)},
        *normalize_history(history),
        {"role": "user", "content": question.strip()},
    ]


def build_gemini_payload(messages: list[dict[str, str]]) -> dict:
    system_text = "\n\n".join(
        message["content"] for message in messages if message["role"] == "system"
    )

    contents = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue

        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": message["content"]}]})

    while contents and contents[0]["role"] == "model":
        contents.pop(0)

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    return payload


def extract_gemini_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    for candidate in candidates:
        parts = (candidate.get("content") or {}).get("parts") or []
        texts = [part.get("text", "").strip() for part in parts if part.get("text")]
        text = "\n".join(texts).strip()
        if text:
            return text

    prompt_feedback = data.get("promptFeedback")
    if prompt_feedback:
        return f"Nao consegui responder agora. Motivo reportado pelo Gemini: {prompt_feedback}"

    return "Nao consegui gerar uma resposta no momento."


def update_active_gemini_model(model_name: str) -> None:
    global active_gemini_model
    with active_gemini_model_lock:
        active_gemini_model = model_name


def get_active_gemini_model() -> str:
    with active_gemini_model_lock:
        return active_gemini_model


def get_gemini_candidate_models() -> list[str]:
    seen = set()
    candidates = []
    for model_name in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        normalized = normalize_gemini_model_name(model_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def call_gemini(messages: list[dict[str, str]]) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY nao configurada")

    payload = build_gemini_payload(messages)
    candidate_models = get_gemini_candidate_models()
    if not candidate_models:
        raise RuntimeError("Nenhum modelo Gemini configurado em GEMINI_MODEL.")

    last_not_found_detail = ""
    request_error_details = []
    with httpx.Client(timeout=GEMINI_TIMEOUT) as client:
        for model_name in candidate_models:
            url = GEMINI_API_URL.format(model=model_name)
            try:
                response = client.post(url, params={"key": GEMINI_API_KEY}, json=payload)
            except httpx.RequestError as exc:
                request_error_details.append(f"{model_name}: {exc}")
                continue

            if response.status_code == 404:
                detail = response.text
                lower_detail = detail.lower()
                if "not found" in lower_detail or "not supported for generatecontent" in lower_detail:
                    last_not_found_detail = detail[:600]
                    continue

            if response.status_code in {429, 500, 502, 503, 504}:
                request_error_details.append(
                    f"{model_name}: {response.status_code} {response.text[:300]}"
                )
                continue

            response.raise_for_status()
            data = response.json()
            update_active_gemini_model(model_name)
            return extract_gemini_text(data)

    tried_models = ", ".join(candidate_models)
    extra_detail = last_not_found_detail or "; ".join(request_error_details)
    raise RuntimeError(
        "Nenhum modelo Gemini disponivel para generateContent. "
        f"Modelos tentados: {tried_models}. "
        f"Detalhe: {extra_detail}"
    )


# =========================
# INICIALIZACAO
# =========================
@app.on_event("startup")
def startup_event() -> None:
    init_db()
    load_model()
    thread = threading.Thread(target=process_stream, daemon=True)
    thread.start()


# =========================
# ROTAS
# =========================
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    events = list_events(20)
    commodity_snapshot = commodity_scraper.get_snapshot()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": events,
            "model_path": MODEL_PATH,
            "model_error": model_error,
            "gemini_model": get_active_gemini_model(),
            "gemini_enabled": bool(GEMINI_API_KEY),
            "commodity_snapshot": commodity_snapshot,
        },
    )


@app.get("/health")
def health():
    commodity_snapshot = commodity_scraper.get_snapshot()
    return {
        "status": "ok",
        "service": "AgroVision AI",
        "model_loaded": model is not None,
        "model_path": MODEL_PATH,
        "model_error": model_error,
        "gemini_enabled": bool(GEMINI_API_KEY),
        "gemini_model": get_active_gemini_model(),
        "gemini_model_configured": GEMINI_MODEL,
        "commodity_scraper_ok": commodity_snapshot.get("ok", False),
        "commodity_source": commodity_snapshot.get("source"),
        "commodity_data_as_of": commodity_snapshot.get("data_as_of"),
        "commodity_items": len(commodity_snapshot.get("items", [])),
    }


@app.get("/camera/status")
def camera_status():
    with camera_state_lock:
        connected = camera_connected
        error = camera_last_error

    with last_frame_lock:
        has_live_frame = last_frame is not None

    return {
        "online": connected and has_live_frame,
        "connected": connected,
        "has_live_frame": has_live_frame,
        "source": str(CAMERA_SOURCE),
        "source_type": CAMERA_SOURCE_TYPE,
        "last_error": error,
    }


@app.get("/events")
def get_events():
    return JSONResponse(content=list_events(50))


@app.get("/commodities")
def get_commodities(force_refresh: bool = False):
    snapshot = commodity_scraper.get_snapshot(force_refresh=force_refresh)
    return JSONResponse(content=snapshot)


@app.get("/frame")
def get_frame():
    with last_frame_lock:
        if last_frame is None:
            return JSONResponse(
                content={"message": "Ainda sem frame disponivel."},
                status_code=503,
            )

        success, buffer = cv2.imencode(".jpg", last_frame)
        if not success:
            return JSONResponse(
                content={"message": "Erro ao converter frame."},
                status_code=500,
            )

        return Response(content=buffer.tobytes(), media_type="image/jpeg")


@app.get("/video_feed")
def video_feed():
    def generate():
        while True:
            with last_frame_lock:
                current_frame = None if last_frame is None else last_frame.copy()

            if current_frame is None:
                time.sleep(0.1)
                continue

            ok, buffer = cv2.imencode(".jpg", current_frame)
            if not ok:
                time.sleep(0.05)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )
            time.sleep(0.05)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/agent/status")
def agent_status():
    events = list_events(AGENT_EVENT_LIMIT)
    commodity_snapshot = commodity_scraper.get_snapshot()
    context = (
        build_event_context(events)
        + "\n\n"
        + build_commodity_context(commodity_snapshot)
    )
    return {
        "name": AGENT_PROFILE.name,
        "role": AGENT_PROFILE.role,
        "goal": AGENT_PROFILE.goal,
        "llm_provider": "gemini",
        "model": get_active_gemini_model(),
        "model_configured": GEMINI_MODEL,
        "gemini_enabled": bool(GEMINI_API_KEY),
        "events_in_context": len(events),
        "max_history_messages": MAX_HISTORY_MESSAGES,
        "commodity_scraper_ok": commodity_snapshot.get("ok", False),
        "commodity_data_as_of": commodity_snapshot.get("data_as_of"),
        "commodity_items": len(commodity_snapshot.get("items", [])),
        "context_preview": context[:1500],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Gemini nao configurado. Defina GEMINI_API_KEY no ambiente ou .env.",
        )

    events = list_events(AGENT_EVENT_LIMIT)
    commodity_snapshot = commodity_scraper.get_snapshot()
    messages = build_agent_messages(
        payload.question,
        payload.history,
        events,
        commodity_snapshot,
    )

    try:
        answer = call_gemini(messages)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Erro na API Gemini: {detail}",
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao gerar resposta do agente: {exc}",
        )

    return ChatResponse(answer=answer, events_in_context=len(events))
