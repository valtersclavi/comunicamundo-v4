from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import time
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .document_formats import (
    SUPPORTED_CONTENT_TYPES,
    is_docling_format,
    resolve_document_format,
    source_file_name,
)

from .contracts import (
    AcquiredSource,
    AcquisitionAttempt,
    SourceCandidate,
    SourceSearchPlan,
)
from .normalization import normalize_acquired_text


_TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}
_TERMINAL_BROWSER_STATUSES = {"completed", "failed", "stopped", "cancelled"}
_SEARCH_TIMEOUT = (5, 25)
_OPEN_TIMEOUT = (5, 120)
_SERVICE_EXTRACTION_TIMEOUT = (5, 90)


class SourceProvider(Protocol):
    name: str

    def search(self, query: str, *, count: int) -> list[SourceCandidate]: ...


@dataclass(frozen=True, slots=True)
class AcquisitionPayload:
    record: AcquiredSource
    original_bytes: bytes | None = None
    original_content_type: str | None = None


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source URLs must be public HTTPS URLs without credentials")
    query = [
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMETERS
    ]
    netloc = parsed.hostname.lower()
    if parsed.port and parsed.port != 443:
        netloc = f"{netloc}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", netloc, path, urlencode(sorted(query)), ""))


def safe_canonical_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return canonical_url(value)
    except ValueError:
        return None


def canonical_doi(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()


def candidate_identity(*, doi: str | None, url: str) -> str:
    stable = f"doi:{doi}" if doi else f"url:{canonical_url(url)}"
    return sha256(stable.encode()).hexdigest()


class YouSearchProvider:
    name = "you"

    def __init__(self, api_key: str, *, session: requests.Session | None = None) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()

    def search(self, query: str, *, count: int) -> list[SourceCandidate]:
        response = self._session.post(
            "https://ydc-index.io/v1/search",
            headers={"X-API-Key": self._api_key, "Content-Type": "application/json"},
            json={"query": query, "count": count},
            timeout=_SEARCH_TIMEOUT,
        )
        response.raise_for_status()
        web = response.json().get("results", {}).get("web", [])
        result: list[SourceCandidate] = []
        for rank, item in enumerate(web[:count], start=1):
            normalized_url = safe_canonical_url(item.get("url"))
            title = item.get("title")
            if not normalized_url or not isinstance(title, str) or not title.strip():
                continue
            result.append(SourceCandidate(
                candidate_id=candidate_identity(doi=None, url=normalized_url),
                provider=self.name,
                query=query,
                rank=rank,
                title=title.strip(),
                url=normalized_url,
                acquisition_url=normalized_url,
                published_at=item.get("page_age"),
                description=item.get("description"),
            ))
        return result


class OpenAlexSearchProvider:
    name = "openalex"

    def __init__(self, api_key: str, *, session: requests.Session | None = None) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()

    def search(self, query: str, *, count: int) -> list[SourceCandidate]:
        response = self._session.get(
            "https://api.openalex.org/works",
            params={"search": query, "per-page": count, "api_key": self._api_key},
            timeout=_SEARCH_TIMEOUT,
        )
        response.raise_for_status()
        result: list[SourceCandidate] = []
        for rank, item in enumerate(response.json().get("results", [])[:count], start=1):
            title = item.get("display_name") or item.get("title")
            primary = item.get("primary_location") or {}
            best_oa = item.get("best_oa_location") or {}
            source = primary.get("source") or {}
            doi = canonical_doi(item.get("doi"))
            landing = best_oa.get("landing_page_url") or primary.get("landing_page_url")
            public_id = item.get("id")
            url = safe_canonical_url(f"https://doi.org/{doi}" if doi else landing or public_id)
            acquisition_url = safe_canonical_url(
                best_oa.get("pdf_url") or primary.get("pdf_url") or landing or url
            )
            if not isinstance(title, str) or not url or not acquisition_url:
                continue
            result.append(SourceCandidate(
                candidate_id=candidate_identity(doi=doi, url=url),
                provider=self.name,
                query=query,
                rank=rank,
                title=title.strip(),
                url=url,
                acquisition_url=acquisition_url,
                publisher=source.get("display_name"),
                doi=doi,
                published_at=item.get("publication_date"),
                description=_openalex_abstract(item.get("abstract_inverted_index")),
            ))
        return result


def _openalex_abstract(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        positioned.extend(
            (position, word) for position in positions if isinstance(position, int)
        )
    if not positioned:
        return None
    return " ".join(word for _, word in sorted(positioned))


class SourceDiscovery:
    def __init__(self, providers: Sequence[SourceProvider], *, results_per_query: int = 10) -> None:
        if not providers:
            raise ValueError("source discovery requires at least one provider")
        if not 1 <= results_per_query <= 100:
            raise ValueError("results_per_query must be between 1 and 100")
        self._providers = {provider.name: provider for provider in providers}
        self._results_per_query = results_per_query

    def discover(self, plan: SourceSearchPlan) -> list[SourceCandidate]:
        candidates: list[SourceCandidate] = []
        for query in plan.queries:
            candidates.extend(self.discover_query(query.query, query.provider))
        return deduplicate_candidates(candidates)

    def discover_query(self, query: str, provider: str) -> list[SourceCandidate]:
        selected = self._providers.get(provider)
        if selected is None:
            raise ValueError(f"unknown source provider: {provider}")
        try:
            return deduplicate_candidates(
                selected.search(query, count=self._results_per_query)
            )
        except Exception:
            return []


def deduplicate_candidates(candidates: Iterable[SourceCandidate]) -> list[SourceCandidate]:
    chosen: dict[str, SourceCandidate] = {}
    order: list[str] = []
    for candidate in candidates:
        identity = f"doi:{candidate.doi}" if candidate.doi else f"url:{canonical_url(candidate.url)}"
        if identity not in chosen:
            chosen[identity] = candidate
            order.append(identity)
            continue
        current = chosen[identity]
        current_is_pdf = current.acquisition_url.lower().endswith(".pdf")
        candidate_is_pdf = candidate.acquisition_url.lower().endswith(".pdf")
        if candidate_is_pdf and not current_is_pdf:
            chosen[identity] = candidate
    return [chosen[item] for item in order]


def _is_direct_document(
    candidate: SourceCandidate,
    content_type: str | None,
) -> bool:
    if content_type in {
        "text/plain", "text/markdown", "text/html", "application/xhtml+xml"
    }:
        return False
    try:
        value = resolve_document_format(
            source_file_name(candidate.acquisition_url, None, content_type),
            content_type,
        )
        return is_docling_format(value)
    except ValueError:
        return False


class SourceAcquirer:
    def __init__(
        self,
        *,
        you_api_key: str,
        jina_api_key: str,
        browser_use_api_key: str,
        session: requests.Session | None = None,
        browser_poll_seconds: float = 5,
        browser_timeout_seconds: int = 90,
        use_browser: bool = False,
    ) -> None:
        self._you_api_key = you_api_key
        self._jina_api_key = jina_api_key
        self._browser_use_api_key = browser_use_api_key
        self._session = session or requests.Session()
        self._browser_poll_seconds = browser_poll_seconds
        self._browser_timeout_seconds = browser_timeout_seconds
        self._use_browser = use_browser

    def acquire(self, candidate: SourceCandidate) -> AcquisitionPayload:
        attempts: list[AcquisitionAttempt] = []
        original_bytes, original_content_type, direct_text = self._direct(candidate, attempts)
        if (
            original_bytes
            and (
                original_bytes.startswith(b"%PDF")
                or _is_direct_document(candidate, original_content_type)
                or original_content_type in (
                    SUPPORTED_CONTENT_TYPES
                    - {"text/plain", "text/markdown", "text/html", "application/xhtml+xml"}
                )
            )
        ):
            return self._success(
                candidate, "direct", "", attempts,
                final_url=candidate.acquisition_url,
                original_bytes=original_bytes,
                original_content_type=original_content_type or "application/pdf",
            )
        if direct_text and _acceptable(direct_text):
            return self._success(
                candidate, "direct", direct_text, attempts,
                final_url=candidate.acquisition_url,
                original_bytes=original_bytes,
                original_content_type=original_content_type,
            )

        text, _, final_url = self._you_contents(candidate, attempts)
        if text and _acceptable(text):
            return self._success(
                candidate, "you_contents", text, attempts, final_url=final_url,
                original_bytes=original_bytes, original_content_type=original_content_type,
            )

        text, final_url = self._jina(candidate, attempts)
        if text and _acceptable(text):
            return self._success(
                candidate, "jina_reader", text, attempts, final_url=final_url,
                original_bytes=original_bytes, original_content_type=original_content_type,
            )

        if self._use_browser:
            browser_text, browser_original, browser_type, browser_url, browser_status = self._browser(candidate, attempts)
            if browser_status == "human_action_required":
                record = AcquiredSource(
                    candidate=candidate, status=browser_status, attempts=attempts,
                    failure_reason="public acquisition requires human action",
                )
                return AcquisitionPayload(record=record, original_bytes=original_bytes, original_content_type=original_content_type)
            if browser_text and _acceptable(browser_text):
                return self._success(
                    candidate, "browser_use", browser_text, attempts,
                    final_url=browser_url,
                    original_bytes=browser_original or original_bytes,
                    original_content_type=browser_type or original_content_type,
                )
        record = AcquiredSource(
            candidate=candidate,
            status="failed",
            attempts=attempts,
            failure_reason=_last_error(attempts),
        )
        return AcquisitionPayload(record=record, original_bytes=original_bytes, original_content_type=original_content_type)

    def _direct(self, candidate: SourceCandidate, attempts: list[AcquisitionAttempt]) -> tuple[bytes | None, str | None, str | None]:
        started = time.perf_counter()
        try:
            response = self._session.get(
                candidate.acquisition_url,
                headers={"User-Agent": "ComunicAmundo-Acquire/1.0"},
                timeout=_OPEN_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            data = response.content
            content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
            text = None
            if content_type.startswith("text/plain"):
                text = response.text
            attempts.append(_attempt("direct", True, started, response.url, content_type, text))
            return data, content_type, text
        except Exception as error:
            attempts.append(_attempt("direct", False, started, error=str(error)))
            return None, None, None

    def _you_contents(self, candidate: SourceCandidate, attempts: list[AcquisitionAttempt]) -> tuple[str | None, str | None, str]:
        started = time.perf_counter()
        try:
            response = self._session.post(
                "https://ydc-index.io/v1/contents",
                headers={"X-API-Key": self._you_api_key, "Content-Type": "application/json"},
                json={
                    "urls": [candidate.acquisition_url],
                    "formats": ["markdown", "metadata"],
                    "crawl_timeout": 60,
                    "max_age": 0,
                },
                timeout=_SERVICE_EXTRACTION_TIMEOUT,
            )
            response.raise_for_status()
            values = response.json()
            item = values[0] if values else {}
            text = item.get("markdown") or item.get("html")
            final_url = item.get("url") or candidate.acquisition_url
            attempts.append(_attempt("you_contents", bool(text), started, final_url, "text/markdown", text, None if text else "response contained no content"))
            return text, item.get("title"), final_url
        except Exception as error:
            attempts.append(_attempt("you_contents", False, started, error=str(error)))
            return None, None, candidate.acquisition_url

    def _jina(self, candidate: SourceCandidate, attempts: list[AcquisitionAttempt]) -> tuple[str | None, str]:
        started = time.perf_counter()
        try:
            response = self._session.get(
                f"https://r.jina.ai/{candidate.acquisition_url}",
                headers={
                    "Authorization": f"Bearer {self._jina_api_key}",
                    "Accept": "text/markdown",
                    "X-Timeout": "60",
                },
                timeout=_SERVICE_EXTRACTION_TIMEOUT,
            )
            response.raise_for_status()
            text = response.text.strip()
            attempts.append(_attempt("jina_reader", bool(text), started, candidate.acquisition_url, "text/markdown", text, None if text else "response contained no content"))
            return text or None, candidate.acquisition_url
        except Exception as error:
            attempts.append(_attempt("jina_reader", False, started, error=str(error)))
            return None, candidate.acquisition_url

    def _browser(self, candidate: SourceCandidate, attempts: list[AcquisitionAttempt]) -> tuple[str | None, bytes | None, str | None, str, str]:
        started = time.perf_counter()
        headers = {
            "X-Browser-Use-API-Key": self._browser_use_api_key,
            "Content-Type": "application/json",
        }
        try:
            created = self._session.post(
                "https://api.browser-use.com/api/v4/runs",
                headers=headers,
                json={"task": _browser_task(candidate)},
                timeout=45,
            )
            created.raise_for_status()
            run = created.json()
            deadline = time.monotonic() + self._browser_timeout_seconds
            while time.monotonic() < deadline:
                status_response = self._session.get(
                    f"https://api.browser-use.com/api/v4/runs/{run['id']}/status",
                    headers=headers,
                    timeout=30,
                )
                status_response.raise_for_status()
                status = status_response.json().get("status")
                if status in _TERMINAL_BROWSER_STATUSES:
                    break
                time.sleep(self._browser_poll_seconds)
            else:
                raise TimeoutError("Browser Use run did not finish before timeout")
            full = self._session.get(
                f"https://api.browser-use.com/api/v4/runs/{run['id']}",
                headers=headers,
                timeout=30,
            )
            full.raise_for_status()
            details = full.json()
            result = str(details.get("result") or "")
            if "HUMAN_ACTION_REQUIRED" in result.upper():
                attempts.append(_attempt("browser_use", False, started, error="agent requires human action"))
                return None, None, None, candidate.acquisition_url, "human_action_required"
            if details.get("status") != "completed" or "ACQUIRED" not in result.upper():
                error = details.get("error") or result or f"terminal status {details.get('status')}"
                attempts.append(_attempt("browser_use", False, started, error=str(error)[:1000]))
                return None, None, None, candidate.acquisition_url, "failed"
            text, original, content_type = self._browser_files(details["workspaceId"], headers)
            final_url = _result_url(result) or candidate.acquisition_url
            attempts.append(_attempt("browser_use", bool(text), started, final_url, content_type or "text/plain", text, None if text else "agent returned no readable text file"))
            return text, original, content_type, final_url, "acquired" if text else "failed"
        except Exception as error:
            attempts.append(_attempt("browser_use", False, started, error=str(error)))
            return None, None, None, candidate.acquisition_url, "failed"

    def _browser_files(self, workspace_id: str, headers: dict[str, str]) -> tuple[str | None, bytes | None, str | None]:
        response = self._session.get(
            f"https://api.browser-use.com/api/v4/workspaces/{workspace_id}/files",
            headers=headers,
            params={"includeUrls": "true", "limit": 100},
            timeout=30,
        )
        response.raise_for_status()
        files = response.json().get("files", [])
        text_files = sorted(
            (item for item in files if str(item.get("path", "")).lower().endswith((".txt", ".md")) and item.get("url")),
            key=lambda item: int(item.get("size") or 0),
            reverse=True,
        )
        binary_files = sorted(
            (item for item in files if str(item.get("path", "")).lower().endswith(".pdf") and item.get("url")),
            key=lambda item: int(item.get("size") or 0),
            reverse=True,
        )
        text = self._session.get(text_files[0]["url"], timeout=60).text if text_files else None
        original = self._session.get(binary_files[0]["url"], timeout=90).content if binary_files else None
        return text, original, "application/pdf" if original else "text/plain"

    def _success(
        self,
        candidate: SourceCandidate,
        route: str,
        text: str,
        attempts: list[AcquisitionAttempt],
        *,
        final_url: str,
        original_bytes: bytes | None,
        original_content_type: str | None,
    ) -> AcquisitionPayload:
        text = normalize_acquired_text(text)
        record = AcquiredSource(
            candidate=candidate,
            status="acquired",
            selected_route=route,
            final_url=final_url,
            normalized_text=text,
            content_type=original_content_type or "text/markdown",
            binary_sha256=sha256(original_bytes).hexdigest() if original_bytes else None,
            normalized_text_sha256=sha256(text.encode()).hexdigest(),
            attempts=attempts,
        )
        return AcquisitionPayload(record=record, original_bytes=original_bytes, original_content_type=original_content_type)


def _attempt(
    route: str,
    ok: bool,
    started: float,
    final_url: str | None = None,
    content_type: str | None = None,
    text: str | None = None,
    error: str | None = None,
) -> AcquisitionAttempt:
    return AcquisitionAttempt(
        route=route,
        ok=ok,
        duration_ms=int((time.perf_counter() - started) * 1000),
        final_url=final_url,
        content_type=content_type,
        content_characters=len(text or ""),
        error=error,
    )


def _acceptable(text: str) -> bool:
    words = text.split()
    return (
        len(text.strip()) >= 800
        and len(words) >= 100
        and text.count("�") / max(len(text), 1) < 0.005
    )


def _browser_task(candidate: SourceCandidate) -> str:
    return "\n".join([
        "Adquira o conteúdo integral da fonte identificada abaixo.",
        "",
        f"Comece pela URL: {candidate.acquisition_url}",
        f"Título esperado: {candidate.title}",
        f"Instituição/publicador: {candidate.publisher or 'não informado'}",
        f"DOI: {candidate.doi or 'não informado'}",
        "",
        "Confirme que a fonte obtida é exatamente a identificada acima. Não a substitua por outra publicação.",
        "Você pode usar outra cópia pública da MESMA publicação.",
        "Obtenha o conteúdo integral quando ele estiver publicamente acessível.",
        "Não solicite intervenção humana, login, e-mail, aceite de termos, pagamento ou resolução manual de CAPTCHA.",
        "Salve o conteúdo integral legível em arquivo .txt ou .md e preserve o arquivo original quando houver.",
        "Ao terminar, devolva: status ACQUIRED, PARTIAL, HUMAN_ACTION_REQUIRED ou FAILED; URL final; evidências de identidade; arquivos produzidos; e motivo específico quando não obtido.",
    ])


def _result_url(value: str) -> str | None:
    match = re.search(r"https://[^\s)]+", value)
    return match.group(0).rstrip(".,") if match else None


def _last_error(attempts: Sequence[AcquisitionAttempt]) -> str:
    errors = [item.error for item in attempts if item.error]
    return errors[-1] if errors else "no route produced integral readable content"
