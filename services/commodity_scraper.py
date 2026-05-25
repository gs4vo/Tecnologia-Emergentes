import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup


DEFAULT_TARGET_COMMODITIES = [
    "Maize (corn)",
    "Soybeans",
    "Wheat",
    "Sugar",
    "Beef",
    "Coffee, Other Mild Arabicas",
]

ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<monthly>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<one_month>[-+]?\d+(?:\.\d+)?)%\s*"
    r"(?P<twelve_months>[-+]?\d+(?:\.\d+)?)%\s*"
    r"(?P<ytd>[-+]?\d+(?:\.\d+)?)%$"
)


def parse_target_commodities(raw_value: str) -> list[str]:
    raw_value = raw_value.strip()
    if not raw_value:
        return list(DEFAULT_TARGET_COMMODITIES)

    separator = ";" if ";" in raw_value else ","
    return [item.strip() for item in raw_value.split(separator) if item.strip()]


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_percent(raw: str | None) -> float | None:
    if raw is None:
        return None
    return _to_float(raw.replace("%", "").strip())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CommodityScraperService:
    def __init__(
        self,
        source_url: str,
        timeout_seconds: float = 12.0,
        cache_ttl_seconds: int = 900,
        target_commodities: list[str] | None = None,
        max_items: int = 8,
    ) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = max(60, cache_ttl_seconds)
        self.target_commodities = target_commodities or list(DEFAULT_TARGET_COMMODITIES)
        self.max_items = max(1, max_items)

        self._lock = threading.Lock()
        self._cache_payload: dict[str, Any] | None = None
        self._cache_expires_at = 0.0

    def get_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()

        with self._lock:
            if (
                not force_refresh
                and self._cache_payload is not None
                and now < self._cache_expires_at
            ):
                return {
                    **self._cache_payload,
                    "cached": True,
                    "stale": False,
                }
            try:
                fresh = self._fetch_and_parse()
            except Exception as exc:
                if self._cache_payload is not None:
                    return {
                        **self._cache_payload,
                        "ok": False,
                        "cached": True,
                        "stale": True,
                        "error": str(exc),
                    }

                return {
                    "ok": False,
                    "source": "IndexMundi",
                    "source_url": self.source_url,
                    "fetched_at": _utc_now_iso(),
                    "cached": False,
                    "stale": False,
                    "cache_ttl_seconds": self.cache_ttl_seconds,
                    "items": [],
                    "data_as_of": None,
                    "error": str(exc),
                }

            self._cache_payload = fresh
            self._cache_expires_at = time.time() + self.cache_ttl_seconds

            return {
                **fresh,
                "cached": False,
                "stale": False,
            }

    def build_context_text(self, snapshot: dict[str, Any]) -> str:
        if not snapshot.get("items"):
            return (
                "Contexto de commodities:\n"
                "- Nao foi possivel carregar cotacoes de commodities no momento."
            )

        lines = []
        for item in snapshot["items"][: self.max_items]:
            monthly_avg = item["monthly_avg"]
            one_month = item["one_month_change_pct"]
            twelve_months = item["twelve_month_change_pct"]
            lines.append(
                f"- {item['name']}: media mensal={monthly_avg:.2f} USD | "
                f"1M={one_month:+.2f}% | 12M={twelve_months:+.2f}%"
            )

        suffix = " (cache)" if snapshot.get("cached") else ""
        return (
            "Contexto de commodities:\n"
            f"- Fonte: {snapshot.get('source')} | Data base: {snapshot.get('data_as_of')}{suffix}\n"
            + "\n".join(lines)
        )

    def _fetch_and_parse(self) -> dict[str, Any]:
        headers = {
            "User-Agent": "AgroVisionAI/1.0 (+educational monitor)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
            response = client.get(self.source_url)
            response.raise_for_status()
            html = response.text

        items, data_as_of = self._parse_items(html)
        if not items:
            raise RuntimeError("Nenhum dado de commodity foi encontrado na pagina.")

        return {
            "ok": True,
            "source": "IndexMundi",
            "source_url": self.source_url,
            "fetched_at": _utc_now_iso(),
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "data_as_of": data_as_of,
            "items": items,
        }

    def _parse_items(self, html: str) -> tuple[list[dict[str, Any]], str | None]:
        soup = BeautifulSoup(html, "html.parser")
        data_as_of = self._parse_data_as_of(soup)

        rows = self._parse_rows_from_table(soup)
        if rows:
            selected = self._select_target_rows(rows)
            if selected:
                return selected[: self.max_items], data_as_of
            return rows[: self.max_items], data_as_of

        rows = self._parse_rows_from_text(soup)
        selected = self._select_target_rows(rows)
        if selected:
            return selected[: self.max_items], data_as_of
        return rows[: self.max_items], data_as_of

    def _parse_data_as_of(self, soup: BeautifulSoup) -> str | None:
        updated_tag = soup.select_one("p.updated")
        if updated_tag:
            text = updated_tag.get_text(" ", strip=True)
            if "Data as of" in text:
                return text.replace("Data as of", "", 1).strip()

        text = soup.get_text("\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("Data as of"):
                return line.replace("Data as of", "", 1).strip()
        return None

    def _parse_rows_from_table(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        table = soup.find("table", class_="tblData")
        if table is None:
            return []

        rows = []
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue

            row = {
                "name": cells[0].get_text(" ", strip=True),
                "monthly_avg": _to_float(cells[1].get_text(strip=True)),
                "one_month_change_pct": _to_percent(cells[2].get_text(strip=True)),
                "twelve_month_change_pct": _to_percent(cells[3].get_text(strip=True)),
                "ytd_change_pct": _to_percent(cells[4].get_text(strip=True)),
            }

            if None in (
                row["monthly_avg"],
                row["one_month_change_pct"],
                row["twelve_month_change_pct"],
                row["ytd_change_pct"],
            ):
                continue

            rows.append(row)

        return rows

    def _parse_rows_from_text(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        text = soup.get_text("\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        in_table = False
        rows: list[dict[str, Any]] = []

        for line in lines:
            if line.startswith("Commodity Monthly Avg"):
                in_table = True
                continue

            if not in_table:
                continue

            if line.startswith(("Commodities Market", "Sign up to get an email", "Embed this graph")):
                break

            match = ROW_PATTERN.match(line)
            if not match:
                continue

            parsed_row = {
                "name": match.group("name").strip(),
                "monthly_avg": _to_float(match.group("monthly")),
                "one_month_change_pct": _to_percent(match.group("one_month")),
                "twelve_month_change_pct": _to_percent(match.group("twelve_months")),
                "ytd_change_pct": _to_percent(match.group("ytd")),
            }

            if None in (
                parsed_row["monthly_avg"],
                parsed_row["one_month_change_pct"],
                parsed_row["twelve_month_change_pct"],
                parsed_row["ytd_change_pct"],
            ):
                continue

            rows.append(parsed_row)

        return rows

    def _select_target_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []

        by_name = {row["name"].lower(): row for row in rows}
        selected = []

        for target in self.target_commodities:
            row = by_name.get(target.lower())
            if row is not None:
                selected.append(row)

        return selected
