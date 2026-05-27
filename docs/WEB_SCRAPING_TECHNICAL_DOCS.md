# Parte 4 — Documentação Explicativa do Web Scraping

**Projeto:** AgroVision AI  
**Arquivo principal:** `services/commodity_scraper.py`  
**Integrado em:** `app.py`

---

## 1. Visão Geral

O sistema AgroVision AI é composto por duas camadas de contexto que alimentam o agente de IA:

- **Camada local:** detecção de objetos em tempo real via YOLO (câmera/stream).
- **Camada externa:** cotações de commodities agrícolas coletadas automaticamente da internet via *web scraping*.

O web scraping foi implementado como um **serviço isolado** (`CommodityScraperService`), separado das rotas e da lógica de detecção. Essa separação garante que, se a fonte externa falhar, o sistema continua funcionando normalmente.

---

## 2. Fonte de Dados

| Item | Valor |
|---|---|
| URL fonte | `https://www.indexmundi.com/commodities/` |
| Tipo de acesso | HTTP GET público (sem autenticação) |
| Formato da página | HTML com tabela `table.tblData` |
| Dados coletados | Média mensal, variação 1 mês, variação 12 meses, variação no ano (YTD) |

Commodities monitoradas por padrão (configuráveis via `.env`):

- Maize (corn) — milho
- Soybeans — soja
- Wheat — trigo
- Sugar — açúcar
- Beef — carne bovina
- Coffee, Other Mild Arabicas — café

---

## 3. Bibliotecas Utilizadas e Por Que Cada Uma

### `httpx`
Responsável por fazer a **requisição HTTP** à página do IndexMundi.

```python
with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
    response = client.get(self.source_url)
    response.raise_for_status()
    html = response.text
```

> `raise_for_status()` lança uma exceção automaticamente se o servidor retornar erro (4xx, 5xx), evitando que dados corrompidos sejam processados.

Um `User-Agent` personalizado é enviado para identificar o sistema de forma transparente:

```python
headers = {
    "User-Agent": "AgroVisionAI/1.0 (+educational monitor)",
    "Accept": "text/html,application/xhtml+xml",
}
```

---

### `BeautifulSoup` (bs4)
Responsável pelo **parse do HTML** retornado pela requisição.

```python
soup = BeautifulSoup(html, "html.parser")
table = soup.find("table", class_="tblData")
```

Após localizar a tabela principal da página, cada linha (`<tr>`) é lida célula por célula (`<td>`), extraindo nome da commodity e valores numéricos.

---

### `re` (expressões regulares)
Usado como **estratégia de fallback** quando a tabela HTML não está disponível. O regex captura dados diretamente do texto bruto da página:

```python
ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<monthly>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<one_month>[-+]?\d+(?:\.\d+)?)%\s*"
    r"(?P<twelve_months>[-+]?\d+(?:\.\d+)?)%\s*"
    r"(?P<ytd>[-+]?\d+(?:\.\d+)?)%$"
)
```

Cada grupo nomeado (`?P<name>`, `?P<monthly>`, etc.) captura um campo específico de cada linha de dados.

---

### `threading.Lock`
Garante **segurança em ambientes concorrentes**. Como o FastAPI pode processar múltiplas requisições simultaneamente, o `Lock` impede que duas threads tentem atualizar o cache ao mesmo tempo:

```python
self._lock = threading.Lock()

with self._lock:
    if not force_refresh and self._cache_payload is not None and now < self._cache_expires_at:
        return {...self._cache_payload, "cached": True}
```

---

### `time`
Controla a **expiração do cache (TTL)**. O timestamp atual é comparado com o momento em que o cache expira:

```python
self._cache_expires_at = time.time() + self.cache_ttl_seconds
```

---

## 4. Fluxo Completo de Funcionamento

A cada chamada ao scraper, o seguinte fluxo ocorre:

```
Chamada a get_snapshot()
         │
         ▼
 Cache válido existe?
    ├── Sim ──► Retorna cache  {"cached": true, "stale": false}
    └── Não ──► Chama _fetch_and_parse()
                    │
                    ▼
             Faz GET na URL (httpx)
                    │
                    ▼
             Parse do HTML (BeautifulSoup)
             Tenta ler tabela tblData
                    │
              Tabela encontrada?
              ├── Sim ──► Extrai linhas da tabela
              └── Não ──► Extrai via regex no texto puro
                    │
                    ▼
             Filtra apenas as commodities alvo
                    │
                    ▼
             Salva resultado no cache
                    │
                    ▼
             Retorna  {"cached": false, "stale": false}
```

**Se ocorrer erro durante a busca:**

```
Erro na requisição/parse
         │
         ▼
  Cache antigo existe?
  ├── Sim ──► Retorna cache antigo  {"ok": false, "stale": true, "error": "..."}
  └── Não ──► Retorna resposta vazia  {"ok": false, "items": [], "error": "..."}
```

Esse comportamento garante **degradação elegante**: o sistema não quebra por causa de uma dependência externa instável.

---

## 5. Explicação Linha a Linha dos Métodos Principais

### `get_snapshot()` — Ponto de entrada público

```python
def get_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
    now = time.time()

    with self._lock:                          # Bloqueia acesso concorrente
        if (
            not force_refresh                 # Não foi pedido refresh forçado
            and self._cache_payload is not None  # Cache existe
            and now < self._cache_expires_at  # Cache ainda é válido
        ):
            return {
                **self._cache_payload,
                "cached": True,
                "stale": False,
            }
        try:
            fresh = self._fetch_and_parse()   # Busca dados novos
        except Exception as exc:
            if self._cache_payload is not None:
                return {                       # Retorna cache antigo em modo degradado
                    **self._cache_payload,
                    "ok": False,
                    "cached": True,
                    "stale": True,
                    "error": str(exc),
                }
            return {                           # Sem cache algum: retorna erro limpo
                "ok": False,
                "items": [],
                "error": str(exc),
                ...
            }

        self._cache_payload = fresh
        self._cache_expires_at = time.time() + self.cache_ttl_seconds
        return {**fresh, "cached": False, "stale": False}
```

---

### `_fetch_and_parse()` — Busca e estrutura os dados

```python
def _fetch_and_parse(self) -> dict[str, Any]:
    headers = {"User-Agent": "AgroVisionAI/1.0 (+educational monitor)", ...}

    with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
        response = client.get(self.source_url)
        response.raise_for_status()   # Erro HTTP vira exceção
        html = response.text

    items, data_as_of = self._parse_items(html)

    if not items:
        raise RuntimeError("Nenhum dado de commodity foi encontrado na pagina.")

    return {
        "ok": True,
        "source": "IndexMundi",
        "fetched_at": _utc_now_iso(),
        "data_as_of": data_as_of,
        "items": items,
        ...
    }
```

---

### `_parse_items()` — Estratégia dupla de extração

```python
def _parse_items(self, html: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    data_as_of = self._parse_data_as_of(soup)  # Extrai a data de referência

    rows = self._parse_rows_from_table(soup)   # Estratégia 1: tabela HTML
    if rows:
        selected = self._select_target_rows(rows)
        if selected:
            return selected[:self.max_items], data_as_of
        return rows[:self.max_items], data_as_of

    rows = self._parse_rows_from_text(soup)    # Estratégia 2: texto puro + regex
    selected = self._select_target_rows(rows)
    if selected:
        return selected[:self.max_items], data_as_of
    return rows[:self.max_items], data_as_of
```

A estratégia dupla torna o scraper **mais resiliente a mudanças de layout**: se a tabela HTML desaparecer, o regex ainda consegue extrair os dados do texto puro.

---

### `_parse_rows_from_table()` — Leitura da tabela HTML

```python
def _parse_rows_from_table(self, soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table", class_="tblData")  # Localiza a tabela pelo CSS class
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:           # Linhas com menos de 5 colunas são ignoradas
            continue

        row = {
            "name": cells[0].get_text(" ", strip=True),
            "monthly_avg": _to_float(cells[1].get_text(strip=True)),
            "one_month_change_pct": _to_percent(cells[2].get_text(strip=True)),
            "twelve_month_change_pct": _to_percent(cells[3].get_text(strip=True)),
            "ytd_change_pct": _to_percent(cells[4].get_text(strip=True)),
        }

        if None in (row["monthly_avg"], row["one_month_change_pct"], ...):
            continue  # Descarta linhas com dados inválidos

        rows.append(row)
    return rows
```

---

### `_select_target_rows()` — Filtragem das commodities alvo

```python
def _select_target_rows(self, rows: list[dict]) -> list[dict]:
    by_name = {row["name"].lower(): row for row in rows}  # Indexa por nome (case-insensitive)
    selected = []

    for target in self.target_commodities:
        row = by_name.get(target.lower())   # Busca sem diferenciar maiúsculas/minúsculas
        if row is not None:
            selected.append(row)

    return selected
```

A comparação é feita em letras minúsculas (`lower()`) para evitar falhas causadas por diferenças de capitalização entre a lista configurada e o que a página retorna.

---

### `build_context_text()` — Formata dados para o agente de IA

```python
def build_context_text(self, snapshot: dict) -> str:
    if not snapshot.get("items"):
        return "Contexto de commodities:\n- Nao foi possivel carregar cotacoes..."

    lines = []
    for item in snapshot["items"][:self.max_items]:
        lines.append(
            f"- {item['name']}: media mensal={item['monthly_avg']:.2f} USD | "
            f"1M={item['one_month_change_pct']:+.2f}% | 12M={item['twelve_month_change_pct']:+.2f}%"
        )

    suffix = " (cache)" if snapshot.get("cached") else ""
    return (
        f"Contexto de commodities:\n"
        f"- Fonte: {snapshot.get('source')} | Data base: {snapshot.get('data_as_of')}{suffix}\n"
        + "\n".join(lines)
    )
```

Esse texto é enviado diretamente como instrução de sistema para o modelo Gemini, enriquecendo as respostas com contexto de mercado.

---

## 6. Funções Auxiliares de Conversão

```python
def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()  # Remove separadores de milhar (1,234 → 1234)
    try:
        return float(cleaned)
    except ValueError:
        return None

def _to_percent(raw: str | None) -> float | None:
    if raw is None:
        return None
    return _to_float(raw.replace("%", "").strip())  # Remove símbolo de porcentagem

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # Ex: 2026-05-26T14:00:00+00:00
```

---

## 7. Estrutura JSON Retornada pelo Endpoint

**`GET /commodities`**

```json
{
  "ok": true,
  "source": "IndexMundi",
  "source_url": "https://www.indexmundi.com/commodities/",
  "fetched_at": "2026-05-26T14:00:00+00:00",
  "cache_ttl_seconds": 900,
  "data_as_of": "March 2026",
  "cached": false,
  "stale": false,
  "items": [
    {
      "name": "Soybeans",
      "monthly_avg": 472.5,
      "one_month_change_pct": 2.86,
      "twelve_month_change_pct": 17.8,
      "ytd_change_pct": 11.31
    },
    {
      "name": "Maize (corn)",
      "monthly_avg": 189.3,
      "one_month_change_pct": -1.2,
      "twelve_month_change_pct": 5.4,
      "ytd_change_pct": 3.1
    }
  ]
}
```

**Em caso de falha com cache disponível:**

```json
{
  "ok": false,
  "cached": true,
  "stale": true,
  "error": "HTTPStatusError: 503 Service Unavailable",
  "items": [...dados antigos...]
}
```

---

## 8. Integração com o Restante do Sistema

O scraper é instanciado **uma única vez** na inicialização do `app.py`:

```python
commodity_scraper = CommodityScraperService(
    source_url=COMMODITY_SCRAPER_SOURCE_URL,
    timeout_seconds=COMMODITY_SCRAPER_TIMEOUT_SECONDS,
    cache_ttl_seconds=COMMODITY_SCRAPER_CACHE_TTL_SECONDS,
    target_commodities=COMMODITY_SCRAPER_TARGETS,
    max_items=COMMODITY_SCRAPER_MAX_ITEMS,
)
```

Ele é usado em três pontos do sistema:

| Ponto | Uso |
|---|---|
| `GET /` (dashboard) | Exibe tabela de commodities na tela principal |
| `GET /commodities` | Endpoint dedicado, retorna JSON completo |
| `POST /chat` | Dados são convertidos em texto e enviados como contexto para o Gemini |

---

## 9. Cache e Boas Práticas de Rate Limiting

O cache com TTL de 15 minutos (padrão) garante que a fonte externa não seja sobrecarregada com requisições repetidas. Cada requisição ao dashboard ou ao chat reutiliza o dado já salvo em memória enquanto ele for válido.

```
Req 1 (14:00) → busca externa → salva cache → expira às 14:15
Req 2 (14:03) → retorna cache (cached: true)
Req 3 (14:09) → retorna cache (cached: true)
Req 4 (14:16) → cache expirou → nova busca externa
```

---

## 10. Variáveis de Ambiente

Todas as configurações do scraper são ajustáveis sem alterar código:

```env
COMMODITY_SCRAPER_SOURCE_URL=https://www.indexmundi.com/commodities/
COMMODITY_SCRAPER_TIMEOUT_SECONDS=12
COMMODITY_SCRAPER_CACHE_TTL_SECONDS=900
COMMODITY_SCRAPER_MAX_ITEMS=6
COMMODITY_SCRAPER_TARGETS=Maize (corn);Soybeans;Wheat;Sugar;Beef;Coffee, Other Mild Arabicas
```

A função `parse_target_commodities()` aceita tanto `;` quanto `,` como separadores, prevenindo erros de configuração.

---

## 11. Limitações Conhecidas

| Limitação | Impacto | Mitigação aplicada |
|---|---|---|
| Dependência de HTML externo | Mudanças no layout quebram o parser | Estratégia dupla (tabela + regex) |
| Sem API oficial | Menor confiabilidade que uma API REST | Cache com fallback para dados antigos |
| Cache em memória | Dados perdidos ao reiniciar o servidor | TTL baixo (15 min) minimiza perda |
| Lista de commodities fixa por configuração | Não adapta ao contexto da detecção | Configurável via `.env` |

---

## 12. Resumo Arquitetural

O web scraping foi implementado respeitando o princípio de **responsabilidade única**: a classe `CommodityScraperService` cuida exclusivamente de buscar, parsear, cachear e formatar dados de commodities. Ela não conhece rotas, templates, banco de dados ou o modelo YOLO — apenas entrega um dicionário de dados ao código que a chama.

Isso torna o módulo testável de forma isolada, fácil de substituir por uma API real no futuro e independente de falhas nas outras camadas do sistema.
