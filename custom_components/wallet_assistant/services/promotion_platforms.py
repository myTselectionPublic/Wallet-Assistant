from __future__ import annotations

import asyncio
import hashlib
import json
from html import unescape
from html.parser import HTMLParser
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    CONF_PROMOTION_PLATFORMS,
    DOMAIN,
    parse_promotion_platforms_config,
)
from ..models.promotion import Promotion

_LOGGER = logging.getLogger(__name__)

PROMOTION_CACHE = "promotion_cache"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,en-US;q=0.8,en;q=0.7,fr;q=0.6",
}
BODY_EXCERPT_LENGTH = 500
MAX_CATEGORY_PAGES = 100
PROMOTION_SEARCH_MIN_LENGTH = 3


@dataclass(slots=True)
class PromotionPlatformConfig:
    platform_id: str
    name: str
    enabled: bool
    base_url: str
    username: str
    password: str

    @classmethod
    def from_dict(cls, data: dict) -> PromotionPlatformConfig:
        return cls(
            platform_id=str(data.get("platform_id", "")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", False)),
            base_url=str(data.get("base_url", "")),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
        )

    def public_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "name": self.name,
            "enabled": self.enabled,
            "configured": bool(self.base_url and self.username and self.password),
        }


class BasePromotionPlatform:
    """Base class for external promotion platform adapters."""

    def __init__(self, hass: HomeAssistant, config: PromotionPlatformConfig) -> None:
        self.hass = hass
        self.config = config

    async def async_search(self, query: str) -> list[Promotion]:
        """Return normalized promotions matching query."""
        return [
            promotion
            for promotion in await self.async_fetch_promotions()
            if promotion.matches(query)
        ]

    async def async_fetch_promotions(self) -> list[Promotion]:
        """Return all normalized promotions available on the platform."""
        return []


class BenefitsAtWorkPlatform(BasePromotionPlatform):
    """Adapter for Corporate Benefits / Benefits at Work portals."""

    async def async_fetch_promotions(self) -> list[Promotion]:
        if not self.config.base_url or not self.config.username or not self.config.password:
            _LOGGER.debug(
                "Benefits at Work platform %s is missing base URL or credentials",
                self.config.platform_id,
            )
            return []

        _LOGGER.debug(
            (
                "Refreshing Benefits at Work platform %s: base_url=%s origin=%s "
                "login_url=%s disclaimer_url=%s main_url=%s username_configured=%s"
            ),
            self.config.platform_id,
            self.config.base_url,
            self._origin,
            self._login_url,
            self._disclaimer_url,
            self._main_url,
            bool(self.config.username),
        )

        cookie_jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(
            cookie_jar=cookie_jar,
            timeout=REQUEST_TIMEOUT,
            headers=REQUEST_HEADERS,
        ) as session:
            if not await self._async_login(session):
                _LOGGER.warning(
                    "Unable to authenticate Benefits at Work platform %s",
                    self.config.platform_id,
                )
                return []

            if not await self._async_accept_disclaimer(session):
                _LOGGER.warning(
                    "Unable to accept Benefits at Work disclaimer for platform %s",
                    self.config.platform_id,
                )
                return []

            main_html, main_url = await self._async_fetch_main_page(session)
            if not main_html:
                return []

            if _looks_like_login_page(main_html):
                _LOGGER.warning(
                    "Benefits at Work platform %s returned the login page for main page",
                    self.config.platform_id,
                )
                return []

            category_links = self._category_links(main_html, main_url)
            if not category_links:
                _LOGGER.warning(
                    (
                        "Benefits at Work platform %s did not expose category overview "
                        "links; falling back to main page parsing"
                    ),
                    self.config.platform_id,
                )
                return self._parse_promotions(main_html, main_url, [])

            _LOGGER.debug(
                "Benefits at Work platform %s found %s category overview pages",
                self.config.platform_id,
                len(category_links),
            )
            return await self._async_fetch_category_promotions(session, category_links)

    async def _async_fetch_category_promotions(
        self,
        session: aiohttp.ClientSession,
        category_links: list[tuple[str, str]],
    ) -> list[Promotion]:
        promotions_by_id: dict[str, Promotion] = {}
        for category_name, category_url in category_links[:MAX_CATEGORY_PAGES]:
            page_html, page_url = await self._async_fetch_category_page(
                session,
                category_name,
                category_url,
            )
            if not page_html:
                continue

            if _looks_like_login_page(page_html):
                _LOGGER.warning(
                    "Benefits at Work category %s returned the login page",
                    category_name,
                )
                continue

            category_promotions = self._parse_promotions(
                page_html,
                page_url,
                [category_name],
            )
            _LOGGER.debug(
                "Benefits at Work category %s yielded %s promotions",
                category_name,
                len(category_promotions),
            )
            for promotion in category_promotions:
                existing = promotions_by_id.get(promotion.promotion_id)
                if existing is None:
                    promotions_by_id[promotion.promotion_id] = promotion
                    continue

                merged_categories = list(
                    dict.fromkeys([*existing.categories, *promotion.categories])
                )
                existing.categories = merged_categories

        _LOGGER.debug(
            "Benefits at Work platform %s yielded %s unique promotions",
            self.config.platform_id,
            len(promotions_by_id),
        )
        return list(promotions_by_id.values())

    def _parse_promotions(
        self,
        page_html: str,
        page_url: str,
        categories: list[str],
    ) -> list[Promotion]:
        if _looks_like_login_page(page_html):
            _LOGGER.warning(
                "Benefits at Work platform %s returned the login page for promotions",
                self.config.platform_id,
            )
            return []

        parser = _BenefitsAtWorkSearchParser(
            platform_id=self.config.platform_id,
            platform_name=self.config.name,
            page_url=page_url,
            categories=categories,
        )
        parser.feed(page_html)
        return parser.promotions

    async def _async_login(self, session: aiohttp.ClientSession) -> bool:
        login_url = self._login_url
        try:
            async with session.get(login_url) as response:
                body = await response.text(errors="replace")
                self._log_response("login page GET", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Benefits at Work login page failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return False
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Benefits at Work login page request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return False

        if _looks_like_login_page(body):
            _LOGGER.debug(
                "Benefits at Work login form detected for platform %s",
                self.config.platform_id,
            )
        else:
            _LOGGER.debug(
                (
                    "Benefits at Work login page for platform %s did not contain the "
                    "expected login form markers"
                ),
                self.config.platform_id,
            )

        data = {
            "loginData[email]": self.config.username,
            "loginData[password]": self.config.password,
            "cbg3-submit": "Login",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self._origin,
            "Referer": login_url,
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with session.post(
                login_url,
                data=data,
                headers=headers,
                allow_redirects=True,
            ) as response:
                body = await response.text(errors="replace")
                content_type = response.headers.get("Content-Type", "")
                self._log_response("login POST", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Benefits at Work login POST failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return False
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Benefits at Work login POST request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return False

        if "application/json" in content_type:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                _LOGGER.debug(
                    "Benefits at Work login JSON decode failed for platform %s: %s",
                    self.config.platform_id,
                    self._safe_excerpt(body),
                )
                return False

            success = bool(payload.get("success") or payload.get("isValid"))
            _LOGGER.debug(
                "Benefits at Work login JSON response for platform %s: success=%s keys=%s",
                self.config.platform_id,
                success,
                sorted(payload),
            )
            return success

        login_page_returned = _looks_like_login_page(body)
        _LOGGER.debug(
            "Benefits at Work login HTML response for platform %s: login_page_returned=%s",
            self.config.platform_id,
            login_page_returned,
        )
        return not login_page_returned

    async def _async_accept_disclaimer(self, session: aiohttp.ClientSession) -> bool:
        disclaimer_url = self._disclaimer_url
        data = {"disclaimerAccept": "1"}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self._origin,
            "Referer": self._origin,
        }

        try:
            async with session.post(
                disclaimer_url,
                data=data,
                headers=headers,
                allow_redirects=True,
            ) as response:
                body = await response.text(errors="replace")
                self._log_response("disclaimer POST", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Benefits at Work disclaimer POST failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return False
                return not _looks_like_login_page(body)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Benefits at Work disclaimer POST request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return False

    async def _async_fetch_main_page(
        self,
        session: aiohttp.ClientSession,
    ) -> tuple[str, str]:
        main_url = self._main_url
        try:
            async with session.get(
                main_url,
                headers={"Referer": self._origin},
            ) as response:
                body = await response.text(errors="replace")
                self._log_response("main page GET", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Benefits at Work main page request failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", main_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Benefits at Work main page request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return "", main_url

    async def _async_fetch_category_page(
        self,
        session: aiohttp.ClientSession,
        category_name: str,
        category_url: str,
    ) -> tuple[str, str]:
        try:
            async with session.get(
                category_url,
                headers={"Referer": self._origin},
            ) as response:
                body = await response.text(errors="replace")
                self._log_response(f"category GET {category_name}", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        (
                            "Benefits at Work category %s request failed for platform "
                            "%s: %s"
                        ),
                        category_name,
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", category_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Benefits at Work category %s request failed for platform %s: %s",
                category_name,
                self.config.platform_id,
                err,
            )
            return "", category_url

    @property
    def _login_url(self) -> str:
        parsed = urlparse(self.config.base_url)
        if parsed.path and parsed.path != "/":
            return self.config.base_url
        return urljoin(self._origin, "/login")

    @property
    def _disclaimer_url(self) -> str:
        return urljoin(self._origin, "/")

    @property
    def _main_url(self) -> str:
        return urljoin(self._origin, "/")

    @property
    def _origin(self) -> str:
        parsed = urlparse(self.config.base_url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    def _category_links(self, html: str, page_url: str) -> list[tuple[str, str]]:
        parser = _BenefitsAtWorkCategoryParser(page_url)
        parser.feed(html)
        return parser.category_links

    def _log_response(
        self,
        label: str,
        response: aiohttp.ClientResponse,
        body: str,
    ) -> None:
        _LOGGER.debug(
            "Benefits at Work %s response for platform %s: %s",
            label,
            self.config.platform_id,
            self._response_details(response, body),
        )

    def _response_details(
        self,
        response: aiohttp.ClientResponse,
        body: str,
    ) -> str:
        history = " -> ".join(
            f"{historical.status}:{historical.url}"
            for historical in response.history
        )
        return (
            f"status={response.status} final_url={response.url} "
            f"content_type={response.headers.get('Content-Type', '')!r} "
            f"history={history or 'none'} "
            f"body_excerpt={self._safe_excerpt(body)!r}"
        )

    def _safe_excerpt(self, body: str) -> str:
        excerpt = _clean_text(body)[:BODY_EXCERPT_LENGTH]
        if self.config.username:
            excerpt = excerpt.replace(self.config.username, "[redacted-username]")
        if self.config.password:
            excerpt = excerpt.replace(self.config.password, "[redacted-password]")
        return excerpt


class EdenredEngagementPlatform(BasePromotionPlatform):
    """Adapter placeholder for Edenred Engagement portals."""


PLATFORM_ADAPTERS = {
    "benefits_at_work": BenefitsAtWorkPlatform,
    "edenred_engagement": EdenredEngagementPlatform,
}


class PromotionPlatformRegistry:
    """Search configured promotion platforms and return normalized results."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_search(self, query: str) -> list[Promotion]:
        clean_query = query.strip()
        if len(clean_query) < PROMOTION_SEARCH_MIN_LENGTH:
            return []

        cache = self.cached_data
        if not cache.get("updated_at"):
            cache = await self.async_refresh()

        return [
            _promotion_from_dict(promotion)
            for promotion in cache.get("promotions", [])
            if _promotion_dict_matches(promotion, clean_query)
        ]

    async def async_refresh(self) -> dict:
        adapters = [
            self._create_adapter(config)
            for config in get_promotion_platform_configs(self.hass)
            if config.enabled
        ]
        results = await asyncio.gather(
            *(adapter.async_fetch_promotions() for adapter in adapters),
            return_exceptions=True,
        )

        promotions: list[Promotion] = []
        platform_statuses: list[dict] = []
        for adapter, result in zip(adapters, results, strict=False):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Unable to refresh promotion platform %s: %s",
                    adapter.config.platform_id,
                    result,
                )
                platform_statuses.append(
                    {
                        "platform_id": adapter.config.platform_id,
                        "platform_name": adapter.config.name,
                        "success": False,
                        "count": 0,
                        "error": str(result),
                    }
                )
                continue
            promotions.extend(result)
            platform_statuses.append(
                {
                    "platform_id": adapter.config.platform_id,
                    "platform_name": adapter.config.name,
                    "success": True,
                    "count": len(result),
                    "error": "",
                }
            )

        cache = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "platforms": platform_statuses,
            "promotions": [promotion.to_dict() for promotion in promotions],
        }
        self.hass.data.setdefault(DOMAIN, {})[PROMOTION_CACHE] = cache
        return cache

    @property
    def cached_data(self) -> dict:
        return self.hass.data.setdefault(DOMAIN, {}).setdefault(
            PROMOTION_CACHE,
            {"updated_at": "", "platforms": [], "promotions": []},
        )

    def _create_adapter(self, config: PromotionPlatformConfig) -> BasePromotionPlatform:
        adapter_class = PLATFORM_ADAPTERS.get(config.platform_id, BasePromotionPlatform)
        return adapter_class(self.hass, config)


def get_promotion_platform_configs(hass: HomeAssistant) -> list[PromotionPlatformConfig]:
    entries = hass.config_entries.async_entries(DOMAIN)
    options = entries[0].options if entries else {}
    configured_platforms = options.get(CONF_PROMOTION_PLATFORMS)
    return [
        PromotionPlatformConfig.from_dict(platform)
        for platform in parse_promotion_platforms_config(configured_platforms)
    ]


class _BenefitsAtWorkCategoryParser(HTMLParser):
    """Extract top-level category overview links from Benefits at Work navigation."""

    _CATEGORY_PREFIX = "Alle voordelen in "

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.category_links: list[tuple[str, str]] = []
        self._current_href = ""
        self._capturing = False
        self._text_parts: list[str] = []
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attrs = {key: value or "" for key, value in attrs_list}
        href = attrs.get("href", "").strip()
        if not href or not href.startswith("/overview/") or "#" in href:
            return

        self._current_href = href
        self._capturing = True
        self._text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._capturing:
            return

        label = _clean_text(" ".join(self._text_parts))
        self._capturing = False
        self._text_parts = []

        if not label.startswith(self._CATEGORY_PREFIX):
            return

        category_name = _clean_text(label.removeprefix(self._CATEGORY_PREFIX))
        category_url = urljoin(self.page_url, self._current_href)
        if not category_name or category_url in self._seen_urls:
            return

        self._seen_urls.add(category_url)
        self.category_links.append((category_name, category_url))

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._text_parts.append(data)


class _BenefitsAtWorkSearchParser(HTMLParser):
    """Extract server-rendered offer cards from Benefits at Work search pages."""

    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _CARD_CLASSES = {
        "cbg3-list-item",
        "cbg3-search-result",
        "cbg3-offer",
        "cbg3-coupon-list-element",
    }

    def __init__(
        self,
        platform_id: str,
        platform_name: str,
        page_url: str,
        categories: list[str],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.platform_id = platform_id
        self.platform_name = platform_name
        self.page_url = page_url
        self.categories = categories
        self.promotions: list[Promotion] = []
        self._card_depth = 0
        self._current: dict | None = None
        self._capture_stack: list[str] = []
        self._seen_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        classes = set(attrs.get("class", "").split())

        if self._is_card(tag, classes):
            self._card_depth = 1
            self._current = {
                "title": "",
                "promotion": "",
                "description": "",
                "image_url": "",
                "item_url": "",
                "categories": [],
                "text": [],
            }
            self._set_from_attrs(attrs)
            return

        if not self._current:
            return

        if tag not in self._VOID_TAGS:
            self._card_depth += 1

        if tag == "a":
            href = attrs.get("href", "").strip()
            title = attrs.get("title", "").strip()
            if href and not self._current.get("item_url"):
                self._current["item_url"] = urljoin(self.page_url, href)
            if title and not self._current.get("title"):
                self._current["title"] = _clean_text(title)

        if tag == "img":
            src = attrs.get("src", "").strip() or attrs.get("data-original", "").strip()
            alt = attrs.get("alt", "").strip()
            if src and not self._current.get("image_url"):
                self._current["image_url"] = urljoin(self.page_url, src)
            if alt and not self._current.get("title"):
                self._current["title"] = _clean_text(alt)

        capture_field = self._capture_field(tag, classes)
        if capture_field:
            self._capture_stack.append(capture_field)

        self._set_from_attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self._current:
            return

        if self._capture_stack and tag in {"h1", "h2", "h3", "h4", "p", "span", "small"}:
            self._capture_stack.pop()

        self._card_depth -= 1
        if self._card_depth <= 0:
            self._finish_card()

    def handle_data(self, data: str) -> None:
        if not self._current:
            return

        text = _clean_text(data)
        if not text:
            return

        self._current["text"].append(text)
        if self._capture_stack:
            field = self._capture_stack[-1]
            existing = self._current.get(field, "")
            self._current[field] = _join_text(existing, text)

    def _is_card(self, tag: str, classes: set[str]) -> bool:
        return (
            tag in {"article", "div", "li", "section"}
            and not self._current
            and bool(classes & self._CARD_CLASSES)
        )

    def _capture_field(self, tag: str, classes: set[str]) -> str:
        if tag in {"h1", "h2", "h3", "h4"}:
            return "title"
        if "cbg3-list-item--copy" in classes or "copy" in classes:
            return "description"
        if "cbg3-offerlistitem--infos" in classes or "discount" in classes:
            return "promotion"
        if tag in {"p", "span"} and ("headline" in classes or "title" in classes):
            return "title"
        return ""

    def _set_from_attrs(self, attrs: dict[str, str]) -> None:
        if not self._current:
            return

        for key in ("data-offerid", "data-offer-id", "data-id", "id"):
            value = attrs.get(key, "").strip()
            if value and not self._current.get("promotion_id"):
                self._current["promotion_id"] = value
                break

    def _finish_card(self) -> None:
        if not self._current:
            return

        text = _clean_text(" ".join(self._current.get("text", [])))
        title = _clean_text(self._current.get("title") or _first_meaningful_text(text))
        title = _clean_benefits_title(title)
        item_url = self._current.get("item_url", "")

        if not title or not item_url:
            self._reset_card()
            return

        promotion_id = self._current.get("promotion_id") or _stable_id(
            self.platform_id,
            item_url,
            title,
        )
        if promotion_id in self._seen_ids:
            self._reset_card()
            return

        self._seen_ids.add(promotion_id)
        self.promotions.append(
            Promotion(
                promotion_id=promotion_id,
                platform_id=self.platform_id,
                platform_name=self.platform_name,
                title=title,
                promotion=_clean_text(self._current.get("promotion", "")),
                description=_clean_text(self._current.get("description", "")),
                image_url=self._current.get("image_url", ""),
                item_url=item_url,
                categories=list(
                    dict.fromkeys([*self.categories, *self._current.get("categories", [])])
                ),
            )
        )
        self._reset_card()

    def _reset_card(self) -> None:
        self._card_depth = 0
        self._current = None
        self._capture_stack = []


def _looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return (
        'name="logindata[email]"' in lowered
        or "logindata&#x5b;email&#x5d;" in lowered
        or "loginData[email]" in html
        or 'id="cbg-login--form"' in lowered
    )


def _promotion_from_dict(data: dict) -> Promotion:
    return Promotion(
        promotion_id=str(data.get("promotion_id", "")),
        platform_id=str(data.get("platform_id", "")),
        platform_name=str(data.get("platform_name", "")),
        title=str(data.get("title", "")),
        promotion=str(data.get("promotion", "")),
        description=str(data.get("description", "")),
        image_url=str(data.get("image_url", "")),
        item_url=str(data.get("item_url", "")),
        voucher_code=str(data.get("voucher_code", "")),
        valid_from=str(data.get("valid_from", "")),
        valid_until=str(data.get("valid_until", "")),
        categories=[
            str(category)
            for category in data.get("categories", [])
            if str(category).strip()
        ],
    )


def _promotion_dict_matches(data: dict, query: str) -> bool:
    return _promotion_from_dict(data).matches(query)


def _clean_text(value: str) -> str:
    return " ".join(unescape(str(value)).split())


def _clean_benefits_title(value: str) -> str:
    title = _clean_text(value)
    for prefix in ("Naar het voordeel ", "To the benefit ", "Vers l'avantage "):
        if title.startswith(prefix):
            title = title.removeprefix(prefix)
            break
    return title.rstrip(" -")


def _join_text(existing: str, extra: str) -> str:
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing} {extra}"


def _first_meaningful_text(text: str) -> str:
    for part in text.split("  "):
        clean = _clean_text(part)
        if len(clean) > 2:
            return clean
    return text[:120]


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
