# Web Scraping de Commodities no AgroVision

Este documento explica como a camada de web scraping foi implementada no projeto, quais ferramentas foram usadas e qual a finalidade dela dentro do sistema.

## Objetivo da Camada

O YOLO detecta o que está acontecendo no ambiente (pessoas, veículos, máquinas etc.).  
O scraping adiciona contexto econômico externo (commodities agrícolas) para melhorar a análise operacional do agente.

Em resumo:

- visão computacional = contexto operacional local;
- scraping de commodities = contexto de mercado;
- agente = resposta combinando os dois contextos.

## Fonte de Dados

- Fonte pública e gratuita usada: `https://www.indexmundi.com/commodities/`
- Tipo de dado coletado:
  - nome da commodity;
  - média mensal (`monthly_avg`);
  - variação de 1 mês (`one_month_change_pct`);
  - variação de 12 meses (`twelve_month_change_pct`);
  - variação no ano (`ytd_change_pct`);
  - data de referência (`data_as_of`).

## Tecnologias Utilizadas

- `httpx`: faz a requisição HTTP da página.
- `BeautifulSoup` (`bs4`): parse do HTML.
- `re` (regex): extrai os campos numéricos da linha de texto.
- `threading.Lock`: garante segurança de concorrência no cache.
- `time`: controle de expiração do cache (TTL).

Arquivo principal do serviço:

- [commodity_scraper.py](/Users/gustavoferreira/Documents/Faculdade/bigodão/agrovision_ia/services/commodity_scraper.py)

## Como o Scraping Funciona

1. O método `get_snapshot()` verifica se já existe cache válido.
2. Se existir cache dentro do TTL, retorna cache (`cached=true`) e não chama a fonte externa.
3. Se não existir cache válido, chama `_fetch_and_parse()`:
   - faz `GET` na URL;
   - valida status HTTP (`raise_for_status()`);
   - parseia o HTML com `BeautifulSoup`;
   - extrai linhas e aplica regex para montar itens estruturados.
4. O resultado é salvo em memória e disponibilizado em JSON.

## Rate Limit / Boas Práticas

Para evitar excesso de requisições:

- foi implementado cache com TTL (`COMMODITY_SCRAPER_CACHE_TTL_SECONDS`);
- valor padrão: `900` segundos (15 minutos);
- chamadas repetidas aos endpoints usam o cache em vez de bater na fonte toda hora.

## Tratamento de Erros

Se ocorrer falha (site fora do ar, timeout, DNS, layout inesperado):

- quando existe cache anterior:
  - retorna último dado conhecido com `stale=true`;
  - inclui `ok=false` e `error` para sinalizar degradação.
- quando não existe cache:
  - retorna JSON vazio com `ok=false`, `items=[]` e detalhes em `error`.

Esse comportamento evita quebrar o sistema por dependência externa.

## Estrutura JSON Retornada

Endpoint: `GET /commodities`

Exemplo:

```json
{
  "ok": true,
  "source": "IndexMundi",
  "source_url": "https://www.indexmundi.com/commodities/",
  "fetched_at": "2026-05-25T20:40:00+00:00",
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
    }
  ]
}
```

## Integração com o Sistema

A camada foi integrada em 3 pontos:

1. API:
   - endpoint novo `GET /commodities`.
2. Dashboard:
   - tabela de commodities na tela principal.
3. Agente (`/chat`):
   - contexto de commodities é enviado como instrução de sistema junto aos eventos YOLO.

Arquivos de integração:

- [app.py](/Users/gustavoferreira/Documents/Faculdade/bigodão/agrovision_ia/app.py)
- [index.html](/Users/gustavoferreira/Documents/Faculdade/bigodão/agrovision_ia/templates/index.html)

## Variáveis de Ambiente

No `.env`/`.env.example`:

```env
COMMODITY_SCRAPER_SOURCE_URL=https://www.indexmundi.com/commodities/
COMMODITY_SCRAPER_TIMEOUT_SECONDS=12
COMMODITY_SCRAPER_CACHE_TTL_SECONDS=900
COMMODITY_SCRAPER_MAX_ITEMS=6
COMMODITY_SCRAPER_TARGETS=Maize (corn);Soybeans;Wheat;Sugar;Beef;Coffee, Other Mild Arabicas
```

## Finalidade no AgroVision

Essa camada não foi feita apenas para “copiar dados da internet”.  
Ela existe para melhorar decisão operacional:

- enriquecer a leitura dos eventos detectados;
- adicionar impacto potencial de mercado na recomendação do agente;
- aumentar valor prático da resposta para operações agro.

## Limitações Conhecidas

- Mudanças no layout da página podem exigir ajuste no parser/regex.
- Dependência de rede externa; por isso existe fallback com cache.
- É scraping de HTML (não API oficial), então robustez depende da estabilidade da fonte.
