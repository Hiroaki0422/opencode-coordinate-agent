"""Provider-neutral search, safe fetching, extraction, and citation evidence."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from ddgs import DDGS
from pydantic import BaseModel, Field

from personal_agent.core.config import ResearchSettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.tools.contracts import ToolEvidence, ToolExecutionResult


class ResearchError(RuntimeError):
    """Sanitized search or retrieval failure."""


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class ResearchSource(BaseModel):
    source_id: str
    title: str
    url: str
    snippet: str
    retrieved_content: str
    fetch_status: str
    untrusted: bool = True


class ResearchBundle(BaseModel):
    query: str
    sources: list[ResearchSource] = Field(default_factory=list)


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]: ...

    async def aclose(self) -> None: ...


class DuckDuckGoSearchProvider:
    """Best-effort, no-key search using the unofficial DDGS package."""

    name = "duckduckgo"

    def __init__(
        self,
        *,
        region: str = "us-en",
        safe_search: str = "moderate",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._region = region
        self._safe_search = safe_search
        self._timeout_seconds = timeout_seconds

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        try:
            raw_results = await asyncio.to_thread(self._search, query, max_results)
        except Exception as error:
            raise ResearchError("DuckDuckGo search failed") from error
        results: list[SearchResult] = []
        for item in raw_results:
            url = str(item.get("href") or item.get("url") or "")
            title = str(item.get("title") or url)
            if not url:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=str(item.get("body") or item.get("snippet") or ""),
                )
            )
        if not results:
            raise ResearchError("DuckDuckGo returned no search results")
        return results

    async def aclose(self) -> None:
        return None

    def _search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        client = DDGS(timeout=max(1, int(self._timeout_seconds)))
        return client.text(
            query,
            region=self._region,
            safesearch=self._safe_search,
            max_results=max_results,
            backend="duckduckgo",
        )


class SearchRouter:
    """Try configured providers in order until one returns results."""

    def __init__(self, providers: Sequence[SearchProvider]) -> None:
        if not providers:
            raise ValueError("search routing requires at least one provider")
        self._providers = list(providers)

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        failures: list[str] = []
        for provider in self._providers:
            try:
                results = await provider.search(query, max_results=max_results)
            except ResearchError:
                failures.append(provider.name)
                continue
            if results:
                return results
            failures.append(provider.name)
        raise ResearchError(f"all search providers failed: {', '.join(failures)}")

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        self.text_parts.append(cleaned)


class FetchedPage(BaseModel):
    url: str
    title: str | None = None
    text: str


class PageFetcher:
    """Fetch public HTTP pages without following redirects into local networks."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_page_bytes: int,
        max_content_chars: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"User-Agent": "personal-agent-research/0.1"},
            follow_redirects=False,
        )
        self._max_page_bytes = max_page_bytes
        self._max_content_chars = max_content_chars

    async def fetch(self, url: str) -> FetchedPage:
        current_url = url
        for _ in range(4):
            self._validate_public_url(current_url)
            try:
                response = await self._client.get(current_url)
            except httpx.HTTPError as error:
                raise ResearchError("web page fetch failed") from error
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ResearchError("web page returned an invalid redirect")
                current_url = urljoin(current_url, location)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise ResearchError("web page fetch failed") from error
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise ResearchError("web page content type is not supported")
            if len(response.content) > self._max_page_bytes:
                raise ResearchError("web page exceeds the configured size limit")
            if "text/html" in content_type:
                extractor = _HTMLTextExtractor()
                extractor.feed(response.text)
                text = " ".join(extractor.text_parts)
                title = " ".join(extractor.title_parts) or None
            else:
                text = response.text
                title = None
            normalized = re.sub(r"\s+", " ", text).strip()
            return FetchedPage(
                url=current_url,
                title=title,
                text=normalized[: self._max_content_chars],
            )
        raise ResearchError("web page exceeded the redirect limit")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ResearchError("only public HTTP and HTTPS URLs are supported")
        hostname = parsed.hostname.casefold()
        if hostname == "localhost" or hostname.endswith(".local"):
            raise ResearchError("local network URLs are not allowed")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise ResearchError("private and reserved IP addresses are not allowed")


class ResearchService:
    def __init__(
        self,
        *,
        search_router: SearchRouter,
        fetcher: PageFetcher,
        max_results: int,
    ) -> None:
        self._search_router = search_router
        self._fetcher = fetcher
        self._max_results = max_results

    async def research(self, query: str) -> ResearchBundle:
        search_results = await self._search_router.search(
            query,
            max_results=self._max_results,
        )
        fetched = await asyncio.gather(
            *(self._fetch_source(index, result) for index, result in enumerate(search_results, 1))
        )
        return ResearchBundle(query=query, sources=list(fetched))

    async def aclose(self) -> None:
        await self._search_router.aclose()
        await self._fetcher.aclose()

    async def _fetch_source(self, index: int, result: SearchResult) -> ResearchSource:
        source_id = str(index)
        try:
            page = await self._fetcher.fetch(result.url)
        except ResearchError:
            return ResearchSource(
                source_id=source_id,
                title=result.title,
                url=result.url,
                snippet=result.snippet,
                retrieved_content=result.snippet,
                fetch_status="snippet_only",
            )
        return ResearchSource(
            source_id=source_id,
            title=page.title or result.title,
            url=page.url,
            snippet=result.snippet,
            retrieved_content=(
                f"<untrusted_web_content source_id={source_id}>"
                f"{page.text}</untrusted_web_content>"
            ),
            fetch_status="fetched",
        )


class ResearchTool:
    name = "web_research"

    def __init__(self, service: ResearchService) -> None:
        self._service = service

    async def execute(self, action: ActionRequest) -> ToolExecutionResult:
        if action.operation != "search":
            return self._failure(action, f"unsupported research operation {action.operation!r}")
        if action.risk_level is not RiskLevel.READ:
            return self._failure(action, "web research must use read risk")
        query = str(action.arguments.get("query") or action.resource).strip()
        if not query:
            return self._failure(action, "web research requires a query")
        try:
            bundle = await self._service.research(query)
        except ResearchError as error:
            return self._failure(action, str(error))
        if not bundle.sources:
            return self._failure(action, "web research returned no sources")
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=True,
            data=bundle.model_dump(mode="json"),
            external_ids=[source.source_id for source in bundle.sources],
            evidence=[
                ToolEvidence(
                    kind="web_source",
                    identifier=source.source_id,
                    title=source.title,
                    url=source.url,
                    excerpt=source.snippet,
                )
                for source in bundle.sources
            ],
        )

    async def aclose(self) -> None:
        await self._service.aclose()

    def _failure(self, action: ActionRequest, error: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=False,
            error=error,
        )


def build_research_tool(settings: ResearchSettings) -> ResearchTool:
    providers: list[SearchProvider] = []
    for provider_name in settings.providers:
        if provider_name == "duckduckgo":
            providers.append(
                DuckDuckGoSearchProvider(
                    region=settings.region,
                    safe_search=settings.safe_search,
                    timeout_seconds=settings.search_timeout_seconds,
                )
            )
        else:
            raise ValueError(f"search provider {provider_name!r} is not registered")
    router = SearchRouter(providers)
    fetcher = PageFetcher(
        timeout_seconds=settings.fetch_timeout_seconds,
        max_page_bytes=settings.max_page_bytes,
        max_content_chars=settings.max_content_chars,
    )
    return ResearchTool(
        ResearchService(
            search_router=router,
            fetcher=fetcher,
            max_results=settings.max_results,
        )
    )
