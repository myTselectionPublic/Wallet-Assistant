from __future__ import annotations

import logging
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp

from ....models.promotion import Promotion
from ..base import BasePromotionPlatform
from ..utils import clean_text, first_meaningful_text, join_text, stable_id

_LOGGER = logging.getLogger(__name__)

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
MAX_PROMOTION_PAGES = 30
PROMOTION_LINK_MARKERS = (
    "/benefit",
    "/benefits",
    "/deal",
    "/deals",
    "/discount",
    "/discounts",
    "/offer",
    "/offers",
    "/promotion",
    "/promotions",
    "/reward",
    "/rewards",
    "/shop",
    "/shops",
)


class EdenredEngagementPlatform(BasePromotionPlatform):
    """Adapter for tenant-specific Edenred Engagement portals."""

    async def async_fetch_promotions(self) -> list[Promotion]:
        if not self.config.base_url or not self.config.username or not self.config.password:
            _LOGGER.debug(
                "Edenred Engagement platform %s is missing base URL or credentials",
                self.config.platform_id,
            )
            return []

        _LOGGER.debug(
            (
                "Refreshing Edenred Engagement platform %s: base_url=%s origin=%s "
                "signin_url=%s login_url=%s username_configured=%s"
            ),
            self.config.platform_id,
            self.config.base_url,
            self._origin,
            self._signin_url,
            self._login_url,
            bool(self.config.username),
        )

        cookie_jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(
            cookie_jar=cookie_jar,
            timeout=REQUEST_TIMEOUT,
            headers=REQUEST_HEADERS,
        ) as session:
            signin_html, signin_url = await self._async_fetch_signin_page(session)
            if not signin_html:
                return []

            form = _EdenredSigninFormParser(signin_url)
            form.feed(signin_html)
            if not await self._async_login(session, form):
                _LOGGER.warning(
                    "Unable to authenticate Edenred Engagement platform %s",
                    self.config.platform_id,
                )
                return []

            main_html, main_url = await self._async_fetch_main_page(session)
            if not main_html:
                return []

            if _looks_like_login_page(main_html):
                _LOGGER.warning(
                    "Edenred Engagement platform %s returned the login page after login",
                    self.config.platform_id,
                )
                return []

            promotions = self._parse_promotions(main_html, main_url)
            link_promotions, page_links = self._promotion_link_promotions(main_html, main_url)
            _LOGGER.debug(
                (
                    "Edenred Engagement platform %s found %s card promotions, "
                    "%s link promotions, and %s candidate pages"
                ),
                self.config.platform_id,
                len(promotions),
                len(link_promotions),
                len(page_links),
            )

            promotions_by_id = {promotion.promotion_id: promotion for promotion in promotions}
            for promotion in link_promotions:
                promotions_by_id.setdefault(promotion.promotion_id, promotion)
            for page_url in page_links[:MAX_PROMOTION_PAGES]:
                page_html, final_url = await self._async_fetch_promotion_page(session, page_url)
                if not page_html or _looks_like_login_page(page_html):
                    continue

                page_link_promotions, _ = self._promotion_link_promotions(page_html, final_url)
                for promotion in page_link_promotions:
                    promotions_by_id.setdefault(promotion.promotion_id, promotion)

                for promotion in self._parse_promotions(page_html, final_url):
                    existing = promotions_by_id.get(promotion.promotion_id)
                    if existing is None:
                        promotions_by_id[promotion.promotion_id] = promotion
                        continue

                    existing.categories = list(
                        dict.fromkeys([*existing.categories, *promotion.categories])
                    )

            _LOGGER.debug(
                "Edenred Engagement platform %s yielded %s unique promotions",
                self.config.platform_id,
                len(promotions_by_id),
            )
            return list(promotions_by_id.values())

    async def _async_fetch_signin_page(
        self,
        session: aiohttp.ClientSession,
    ) -> tuple[str, str]:
        try:
            async with session.get(self._signin_url) as response:
                body = await response.text(errors="replace")
                self._log_response("signin page GET", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Edenred Engagement signin page failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", self._signin_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement signin page request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return "", self._signin_url

    async def _async_login(
        self,
        session: aiohttp.ClientSession,
        form: _EdenredSigninFormParser,
    ) -> bool:
        login_url = form.action_url or self._login_url
        form_uuid = form.fields.get("form_uuid", "")
        csrf_token = form.fields.get("CSRF_TOKEN", "")
        _LOGGER.debug(
            (
                "Edenred Engagement login form for platform %s: action_url=%s "
                "form_uuid_present=%s csrf_present=%s fields=%s"
            ),
            self.config.platform_id,
            login_url,
            bool(form_uuid),
            bool(csrf_token),
            sorted(form.fields),
        )

        fields = {
            "form_uuid": form_uuid,
            "Username": self.config.username,
            "Password": self.config.password,
            "CSRF_TOKEN": csrf_token,
            "forgot_password": form.fields.get("forgot_password", "form:recover"),
            "submit": form.fields.get("submit", "Login"),
        }
        body, content_type = _multipart_body(fields)
        headers = {
            "Content-Type": content_type,
            "Origin": self._origin,
            "Referer": form.page_url or self._signin_url,
        }

        try:
            async with session.post(
                login_url,
                data=body,
                headers=headers,
                allow_redirects=True,
            ) as response:
                response_body = await response.text(errors="replace")
                self._log_response("login POST", response, response_body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Edenred Engagement login POST failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, response_body),
                    )
                    return False
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement login POST request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return False

        login_page_returned = _looks_like_login_page(response_body)
        _LOGGER.debug(
            "Edenred Engagement login response for platform %s: login_page_returned=%s",
            self.config.platform_id,
            login_page_returned,
        )
        return not login_page_returned

    async def _async_fetch_main_page(
        self,
        session: aiohttp.ClientSession,
    ) -> tuple[str, str]:
        try:
            async with session.get(
                self._main_url,
                headers={"Referer": self._signin_url},
            ) as response:
                body = await response.text(errors="replace")
                self._log_response("main page GET", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        "Edenred Engagement main page request failed for platform %s: %s",
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", self._main_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement main page request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return "", self._main_url

    async def _async_fetch_promotion_page(
        self,
        session: aiohttp.ClientSession,
        page_url: str,
    ) -> tuple[str, str]:
        try:
            async with session.get(
                page_url,
                headers={"Referer": self._main_url},
            ) as response:
                body = await response.text(errors="replace")
                self._log_response("promotion page GET", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        (
                            "Edenred Engagement promotion page request failed for "
                            "platform %s: %s"
                        ),
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", page_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement promotion page request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return "", page_url

    def _parse_promotions(self, html: str, page_url: str) -> list[Promotion]:
        parser = _EdenredPromotionParser(
            platform_id=self.config.platform_id,
            platform_name=self.config.name,
            page_url=page_url,
        )
        parser.feed(html)
        return parser.promotions

    def _promotion_link_promotions(
        self,
        html: str,
        page_url: str,
    ) -> tuple[list[Promotion], list[str]]:
        parser = _EdenredPromotionLinkParser(page_url)
        parser.feed(html)
        promotions = [
            Promotion(
                promotion_id=stable_id(self.config.platform_id, url, title),
                platform_id=self.config.platform_id,
                platform_name=self.config.name,
                title=title,
                item_url=url,
            )
            for title, url in parser.link_items
            if title and url
        ]
        return promotions, [url for _, url in parser.link_items]

    @property
    def _signin_url(self) -> str:
        parsed = urlparse(self.config.base_url)
        if parsed.path:
            return self.config.base_url
        return urljoin(self._origin, "/signin")

    @property
    def _login_url(self) -> str:
        return urljoin(self._origin, "/Authentication/form/signin")

    @property
    def _main_url(self) -> str:
        return urljoin(self._origin, "/")

    @property
    def _origin(self) -> str:
        parsed = urlparse(self.config.base_url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    def _log_response(
        self,
        label: str,
        response: aiohttp.ClientResponse,
        body: str,
    ) -> None:
        _LOGGER.debug(
            "Edenred Engagement %s response for platform %s: %s",
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
        excerpt = clean_text(body)[:BODY_EXCERPT_LENGTH]
        if self.config.username:
            excerpt = excerpt.replace(self.config.username, "[redacted-username]")
        if self.config.password:
            excerpt = excerpt.replace(self.config.password, "[redacted-password]")
        return excerpt


class _EdenredSigninFormParser(HTMLParser):
    """Extract signin action and hidden form fields."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.action_url = ""
        self.fields: dict[str, str] = {}
        self._inside_signin_form = False

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        if tag == "form":
            action = attrs.get("action", "").strip()
            form_id = attrs.get("id", "").lower()
            form_name = attrs.get("name", "").lower()
            if "signin" in action.lower() or "signin" in form_id or "signin" in form_name:
                self._inside_signin_form = True
                self.action_url = urljoin(self.page_url, action) if action else ""
            return

        if tag != "input":
            return

        name = attrs.get("name", "").strip()
        if not name:
            return

        if self._inside_signin_form or name in {
            "form_uuid",
            "CSRF_TOKEN",
            "forgot_password",
            "submit",
        }:
            self.fields[name] = attrs.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._inside_signin_form:
            self._inside_signin_form = False


class _EdenredPromotionParser(HTMLParser):
    """Extract generic promotion cards from Edenred Engagement pages."""

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
    _CARD_CLASS_MARKERS = (
        "benefit",
        "deal",
        "discount",
        "offer",
        "promotion",
        "reward",
        "shop",
        "tile",
        "voucher",
    )

    def __init__(self, platform_id: str, platform_name: str, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.platform_id = platform_id
        self.platform_name = platform_name
        self.page_url = page_url
        self.promotions: list[Promotion] = []
        self._card_depth = 0
        self._current: dict | None = None
        self._capture_stack: list[str] = []
        self._seen_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        classes = set(attrs.get("class", "").split())

        if self._is_card(tag, attrs, classes):
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
            title = attrs.get("title", "").strip() or attrs.get("aria-label", "").strip()
            if href and not self._current.get("item_url"):
                self._current["item_url"] = urljoin(self.page_url, href)
            if title and not self._current.get("title"):
                self._current["title"] = clean_text(title)

        if tag == "img":
            src = (
                attrs.get("src", "").strip()
                or attrs.get("data-src", "").strip()
                or attrs.get("data-original", "").strip()
                or attrs.get("data-lazy-src", "").strip()
            )
            alt = attrs.get("alt", "").strip()
            if src and not self._current.get("image_url"):
                self._current["image_url"] = urljoin(self.page_url, src)
            if alt and not self._current.get("title"):
                self._current["title"] = clean_text(alt)

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

        text = clean_text(data)
        if not text:
            return

        self._current["text"].append(text)
        if self._capture_stack:
            field = self._capture_stack[-1]
            existing = self._current.get(field, "")
            self._current[field] = join_text(existing, text)

    def _is_card(
        self,
        tag: str,
        attrs: dict[str, str],
        classes: set[str],
    ) -> bool:
        if tag not in {"article", "div", "li", "section"} or self._current:
            return False

        candidates = [
            attrs.get("class", ""),
            attrs.get("data-testid", ""),
            attrs.get("data-test", ""),
            attrs.get("id", ""),
            attrs.get("itemtype", ""),
        ]
        return any(
            marker in candidate.lower()
            for candidate in candidates
            for marker in self._CARD_CLASS_MARKERS
        ) or bool(classes & set(self._CARD_CLASS_MARKERS))

    def _capture_field(self, tag: str, classes: set[str]) -> str:
        class_text = " ".join(classes).lower()
        if tag in {"h1", "h2", "h3", "h4"}:
            return "title"
        if tag in {"p", "span", "small"} and any(
            marker in class_text for marker in ("description", "summary", "text", "copy")
        ):
            return "description"
        if any(marker in class_text for marker in ("discount", "saving", "value", "tag")):
            return "promotion"
        if tag in {"p", "span"} and any(
            marker in class_text for marker in ("brand", "merchant", "name", "title")
        ):
            return "title"
        return ""

    def _set_from_attrs(self, attrs: dict[str, str]) -> None:
        if not self._current:
            return

        for key in (
            "data-benefit-id",
            "data-deal-id",
            "data-offer-id",
            "data-promotion-id",
            "data-reward-id",
            "data-id",
            "id",
        ):
            value = attrs.get(key, "").strip()
            if value and not self._current.get("promotion_id"):
                self._current["promotion_id"] = value
                break

    def _finish_card(self) -> None:
        if not self._current:
            return

        text = clean_text(" ".join(self._current.get("text", [])))
        title = clean_text(self._current.get("title") or first_meaningful_text(text))
        item_url = self._current.get("item_url", "")

        if not title or not item_url or not _looks_like_promotion_url(item_url):
            self._reset_card()
            return

        promotion_id = self._current.get("promotion_id") or stable_id(
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
                promotion=clean_text(self._current.get("promotion", "")),
                description=clean_text(self._current.get("description", "")),
                image_url=self._current.get("image_url", ""),
                item_url=item_url,
                categories=list(dict.fromkeys(self._current.get("categories", []))),
            )
        )
        self._reset_card()

    def _reset_card(self) -> None:
        self._card_depth = 0
        self._current = None
        self._capture_stack = []


class _EdenredPromotionLinkParser(HTMLParser):
    """Extract candidate promotion detail/list links from authenticated pages."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.link_items: list[tuple[str, str]] = []
        self._seen: set[str] = set()
        self._current_url = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attrs = {key: value or "" for key, value in attrs_list}
        href = attrs.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return

        url = urljoin(self.page_url, href)
        if not _looks_like_promotion_url(url) or url in self._seen:
            return

        self._seen.add(url)
        self._current_url = url
        self._text_parts = []
        label = clean_text(attrs.get("title", "") or attrs.get("aria-label", ""))
        if label:
            self.link_items.append((label, url))
            self._current_url = ""

    def handle_data(self, data: str) -> None:
        if self._current_url:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_url:
            return

        label = clean_text(" ".join(self._text_parts))
        self.link_items.append((label, self._current_url))
        self._current_url = ""
        self._text_parts = []


def _multipart_body(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
    chunks: list[str] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}",
                f'Content-Disposition: form-data; name="{name}"',
                "",
                str(value),
            ]
        )
    chunks.append(f"--{boundary}--")
    chunks.append("")
    return "\r\n".join(chunks).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return (
        'name="username"' in lowered
        and 'name="password"' in lowered
        and (
            'name="csrf_token"' in lowered
            or "/authentication/form/signin" in lowered
            or "/signin" in lowered
        )
    )


def _looks_like_promotion_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(marker in path for marker in PROMOTION_LINK_MARKERS)
