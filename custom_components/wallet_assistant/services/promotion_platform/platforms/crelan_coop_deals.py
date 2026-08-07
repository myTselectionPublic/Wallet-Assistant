from __future__ import annotations

import asyncio
from datetime import date
from html.parser import HTMLParser
import logging
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import aiohttp
from yarl import URL

from ....models.promotion import Promotion
from ..base import (
    BasePromotionPlatform,
    PromotionPlatformAuthenticationError,
    PromotionPlatformError,
)
from ..utils import clean_text, join_text, stable_id

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-BE,nl;q=0.9,fr-BE;q=0.8,en;q=0.7",
}
MAX_CATALOGUE_PAGES = 100
DETAIL_CONCURRENCY = 5
_VALID_UNTIL_RE = re.compile(
    r"\bgeldig\s+(?:t/?m|tot(?:\s+en\s+met)?)\s+(\d{1,2})\s+"
    r"(januari|februari|maart|april|mei|juni|juli|augustus|september|"
    r"oktober|november|december)\s+(\d{4})\b",
    re.IGNORECASE,
)
_DUTCH_MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
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
_BACKGROUND_IMAGE_RE = re.compile(r"url\(['\"]?([^)'\"]+)", re.IGNORECASE)


class CrelanCoopDealsPlatform(BasePromotionPlatform):
    """Adapter for the CrelanCo Deals catalogue."""

    async def async_fetch_promotions(self) -> list[Promotion]:
        if not self.config.base_url:
            return []
        if not self.config.session_cookie and not (
            self.config.username and self.config.password
        ):
            _LOGGER.debug("CrelanCo Deals credentials are not configured")
            return []

        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            timeout=REQUEST_TIMEOUT,
            headers=REQUEST_HEADERS,
        ) as session:
            self._apply_session_cookie(session)
            first_html, first_url = await self._async_get(session, self._catalogue_url)
            if _looks_like_login_page(first_html, first_url):
                await self._async_login(session)
                first_html, first_url = await self._async_get(
                    session, self._catalogue_url
                )
            if _looks_like_login_page(first_html, first_url):
                raise PromotionPlatformAuthenticationError(
                    "CrelanCo Deals session is not authenticated"
                )

            promotions = await self._async_fetch_catalogue(
                session, first_html, first_url
            )
            await self._async_enrich_details(session, promotions)
            return promotions

    async def _async_login(self, session: aiohttp.ClientSession) -> None:
        login_html, login_url = await self._async_get(session, self._login_url)
        parser = _LoginFormParser(login_url)
        parser.feed(login_html)
        if not parser.action_url:
            raise PromotionPlatformAuthenticationError(
                "CrelanCo Deals login form could not be found"
            )
        if "g-recaptcha" in login_html.lower():
            raise PromotionPlatformAuthenticationError(
                "CrelanCo Deals requires an interactive reCAPTCHA; configure a "
                "fresh shop_sid session cookie from an authenticated browser"
            )

        fields = dict(parser.fields)
        fields.update(
            {
                "login-username": self.config.username,
                "login-password": self.config.password,
                "login_submit": fields.get("login_submit", "Login"),
                "login-redirect_url": self._catalogue_url,
            }
        )
        headers = {"Referer": login_url, "Origin": self._origin}
        async with session.post(
            parser.action_url,
            data=fields,
            headers=headers,
            allow_redirects=True,
        ) as response:
            html = await response.text(errors="replace")
            if response.status >= 400 or _looks_like_login_page(
                html, str(response.url)
            ):
                raise PromotionPlatformAuthenticationError(
                    "CrelanCo Deals authentication failed"
                )

    async def _async_fetch_catalogue(
        self,
        session: aiohttp.ClientSession,
        first_html: str,
        first_url: str,
    ) -> list[Promotion]:
        promotions_by_id: dict[str, Promotion] = {}
        pending = [(first_html, first_url)]

        first_parser = _CatalogueParser(
            self.config.platform_id, self.config.name, first_url
        )
        first_parser.feed(first_html)
        page_numbers = first_parser.page_numbers or {1}
        for page_number in sorted(page_numbers)[:MAX_CATALOGUE_PAGES]:
            if page_number == 1:
                continue
            page_url = _with_query(self._catalogue_url, {"page": str(page_number)})
            page_html, final_url = await self._async_get(session, page_url)
            pending.append((page_html, final_url))

        for html, page_url in pending:
            if _looks_like_login_page(html, page_url):
                raise PromotionPlatformAuthenticationError(
                    "CrelanCo Deals session expired while loading the catalogue"
                )
            parser = _CatalogueParser(
                self.config.platform_id, self.config.name, page_url
            )
            parser.feed(html)
            for promotion in parser.promotions:
                promotions_by_id[promotion.promotion_id] = promotion

        _LOGGER.debug(
            "CrelanCo Deals yielded %s promotions across %s catalogue pages",
            len(promotions_by_id),
            len(pending),
        )
        return list(promotions_by_id.values())

    async def _async_enrich_details(
        self,
        session: aiohttp.ClientSession,
        promotions: list[Promotion],
    ) -> None:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def enrich(promotion: Promotion) -> None:
            async with semaphore:
                try:
                    html, final_url = await self._async_get(
                        session, promotion.item_url
                    )
                except PromotionPlatformError as err:
                    _LOGGER.debug(
                        "Unable to load CrelanCo deal %s: %s",
                        promotion.promotion_id,
                        err,
                    )
                    return
                if _looks_like_login_page(html, final_url):
                    return
                parser = _DealDetailParser(final_url)
                parser.feed(html)
                promotion.title = parser.title or promotion.title
                promotion.promotion = parser.promotion or promotion.promotion
                promotion.description = parser.description
                promotion.image_url = parser.image_url or promotion.image_url
                promotion.valid_until = _valid_until(parser.all_text)

        await asyncio.gather(*(enrich(promotion) for promotion in promotions))

    async def _async_get(
        self, session: aiohttp.ClientSession, url: str
    ) -> tuple[str, str]:
        try:
            async with session.get(url, allow_redirects=True) as response:
                if response.status >= 400:
                    raise PromotionPlatformError(
                        f"CrelanCo Deals request returned HTTP {response.status}"
                    )
                return await response.text(errors="replace"), str(response.url)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PromotionPlatformError(
                f"CrelanCo Deals request failed: {type(err).__name__}"
            ) from err

    def _apply_session_cookie(self, session: aiohttp.ClientSession) -> None:
        value = self.config.session_cookie.strip()
        if not value:
            return
        if ";" in value or value.startswith("shop_sid="):
            cookies = {}
            for item in value.split(";"):
                name, separator, cookie_value = item.strip().partition("=")
                if separator and name == "shop_sid":
                    cookies[name] = cookie_value
            value = cookies.get("shop_sid", "")
        if value:
            session.cookie_jar.update_cookies(
                {"shop_sid": value}, response_url=URL(self._origin)
            )

    @property
    def _origin(self) -> str:
        parsed = urlparse(self.config.base_url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    @property
    def _language(self) -> str:
        first_segment = urlparse(self.config.base_url).path.strip("/").split("/", 1)[0]
        return first_segment if first_segment in {"nl", "fr"} else "nl"

    @property
    def _login_url(self) -> str:
        return f"{self._origin}/{self._language}/accounts/login/"

    @property
    def _catalogue_url(self) -> str:
        return f"{self._origin}/{self._language}/catalogue/"


class _LoginFormParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.action_url = ""
        self.fields: dict[str, str] = {}
        self._inside = False

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        if tag == "form":
            marker = " ".join(
                (
                    attrs.get("id", ""),
                    attrs.get("name", ""),
                    attrs.get("action", ""),
                )
            ).lower()
            if "login" in marker:
                self._inside = True
                self.action_url = urljoin(self.page_url, attrs.get("action", ""))
            return
        if self._inside and tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._inside:
            self._inside = False


class _CatalogueParser(HTMLParser):
    def __init__(self, platform_id: str, platform_name: str, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.platform_id = platform_id
        self.platform_name = platform_name
        self.page_url = page_url
        self.promotions: list[Promotion] = []
        self.page_numbers: set[int] = set()
        self._card: dict | None = None
        self._depth = 0
        self._capture = ""

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        classes = set(attrs.get("class", "").split())
        if tag == "a":
            href = attrs.get("href", "")
            query = parse_qs(urlparse(urljoin(self.page_url, href)).query)
            for raw_page in query.get("page", []):
                if raw_page.isdigit():
                    self.page_numbers.add(int(raw_page))

        if not self._card and tag == "div" and "card" in classes:
            self._card = {
                "title": "",
                "promotion": "",
                "image_url": "",
                "item_url": "",
            }
            self._depth = 1
            return
        if not self._card:
            return
        if tag not in _VOID_TAGS:
            self._depth += 1
        if tag == "a" and attrs.get("href") and not self._card["item_url"]:
            self._card["item_url"] = urljoin(self.page_url, attrs["href"])
        if tag == "img":
            self._card["image_url"] = urljoin(
                self.page_url, attrs.get("src", "")
            )
            if not self._card["title"]:
                self._card["title"] = clean_text(attrs.get("alt", ""))
        if tag == "h3" or "card-title" in classes:
            self._capture = "title"
        elif "card-subtitle" in classes:
            self._capture = "promotion"

    def handle_data(self, data: str) -> None:
        if self._card and self._capture:
            self._card[self._capture] = join_text(
                self._card[self._capture], clean_text(data)
            )

    def handle_endtag(self, tag: str) -> None:
        if not self._card:
            return
        if tag in {"h3", "div"}:
            self._capture = ""
        self._depth -= 1
        if self._depth <= 0:
            self._finish_card()

    def _finish_card(self) -> None:
        card = self._card or {}
        self._card = None
        self._depth = 0
        item_url = card.get("item_url", "")
        title = clean_text(card.get("title", ""))
        if not title or not _is_deal_url(item_url):
            return
        path_parts = urlparse(item_url).path.strip("/").split("/")
        promotion_id = path_parts[-1] if path_parts[-1].isdigit() else stable_id(
            self.platform_id, item_url, title
        )
        category = path_parts[1].replace("-", " ").title() if len(path_parts) > 1 else ""
        self.promotions.append(
            Promotion(
                promotion_id=promotion_id,
                platform_id=self.platform_id,
                platform_name=self.platform_name,
                title=title,
                promotion=clean_text(card.get("promotion", "")),
                image_url=card.get("image_url", ""),
                item_url=item_url,
                categories=[category] if category else [],
            )
        )


class _DealDetailParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.title = ""
        self.promotion = ""
        self.image_url = ""
        self._descriptions: list[str] = []
        self._all_text: list[str] = []
        self._capture = ""
        self._capture_depth = 0
        self._buffer: list[str] = []

    @property
    def description(self) -> str:
        return clean_text(" ".join(dict.fromkeys(self._descriptions)))

    @property
    def all_text(self) -> str:
        return clean_text(" ".join(self._all_text))

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        classes = set(attrs.get("class", "").split())
        if tag == "img" and not self.image_url and (
            "product-image" in classes or "slide-content" in classes
        ):
            self.image_url = urljoin(
                self.page_url, attrs.get("src", "") or attrs.get("data-src", "")
            )
        if not self.image_url and "image-slide" in classes:
            background = _BACKGROUND_IMAGE_RE.search(attrs.get("style", ""))
            if background:
                self.image_url = urljoin(self.page_url, background.group(1))
        if not self._capture:
            if tag == "h1" and "product-title" in classes:
                self._start_capture("title")
            elif tag == "h5" and "product-title" in classes:
                self._start_capture("promotion")
            elif "product-description" in classes:
                self._start_capture("description")
        elif tag not in _VOID_TAGS:
            self._capture_depth += 1

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if text:
            self._all_text.append(text)
            if self._capture:
                self._buffer.append(text)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        value = clean_text(" ".join(self._buffer))
        if self._capture == "title" and not self.title:
            self.title = value
        elif self._capture == "promotion" and not self.promotion:
            self.promotion = value
        elif self._capture == "description" and value:
            self._descriptions.append(value)
        self._capture = ""
        self._buffer = []

    def _start_capture(self, field: str) -> None:
        self._capture = field
        self._capture_depth = 1
        self._buffer = []


def _looks_like_login_page(html: str, url: str) -> bool:
    lowered = html.lower()
    return "/accounts/login/" in urlparse(url).path.lower() or (
        "login-username" in lowered and "login-password" in lowered
    )


def _is_deal_url(url: str) -> bool:
    parts = urlparse(url).path.strip("/").split("/")
    return len(parts) >= 5 and parts[-1].isdigit()


def _with_query(url: str, values: dict[str, str]) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=urlencode(values)))


def _valid_until(text: str) -> str:
    match = _VALID_UNTIL_RE.search(text)
    if not match:
        return ""
    try:
        return date(
            int(match.group(3)),
            _DUTCH_MONTHS[match.group(2).lower()],
            int(match.group(1)),
        ).isoformat()
    except ValueError:
        return ""
