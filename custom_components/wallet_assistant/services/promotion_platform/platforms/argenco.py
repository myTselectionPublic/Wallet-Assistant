from __future__ import annotations

import asyncio
from datetime import date
from html.parser import HTMLParser
import logging
import math
import re
from urllib.parse import urlparse, urlunparse

import aiohttp

from ....models.promotion import Promotion
from ..base import (
    BasePromotionPlatform,
    PromotionPlatformAuthenticationError,
    PromotionPlatformError,
)
from ..utils import clean_text, generate_totp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Accept-Language": "nl-BE,nl;q=0.9,fr-BE;q=0.8,en;q=0.7",
    # Public client key sent by the Argenco web application on every API call.
    "X-Kanga-Key": "nwuziog6JX0J9K",
}
BENEFITS_PER_PAGE = 9
MAX_BENEFIT_PAGES = 100
DETAIL_CONCURRENCY = 5
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mrt": 3,
    "maa": 3,
    "mar": 3,
    "apr": 4,
    "avr": 4,
    "mei": 5,
    "may": 5,
    "jun": 6,
    "jui": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "déc": 12,
}
_TEXTUAL_DATE_RE = re.compile(
    r"(\d{1,2})\s+([^\W\d_]{3,})\.?\s+(\d{4})",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


class ArgencoPlatform(BasePromotionPlatform):
    """Adapter for the Argenco shareholder benefits API."""

    async def async_fetch_promotions(self) -> list[Promotion]:
        if not self.config.base_url or not self.config.username or not self.config.password:
            _LOGGER.debug("Argenco is missing its base URL or credentials")
            return []

        async with aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT,
            headers=REQUEST_HEADERS,
        ) as session:
            auth_headers = await self._async_login(session)
            promotions = await self._async_fetch_all_benefits(session)
            await self._async_enrich_details(session, auth_headers, promotions)
            _LOGGER.debug("Argenco yielded %s promotions", len(promotions))
            return promotions

    async def _async_login(
        self, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        validation = await self._async_post(
            session,
            self._custom_action_url,
            {
                "object": "user",
                "class_action": "custom_api_validate",
                "username": self.config.username,
                "password": self.config.password,
                "otp": "",
            },
            authentication=True,
        )
        result = validation.get("data", {}).get("custom_result", {})
        if not isinstance(result, dict) or not result.get("exists"):
            raise PromotionPlatformAuthenticationError(
                "Argenco username or password was rejected"
            )

        token = ""
        if result.get("mfa_status_0_verification"):
            if not self.config.totp_seed:
                raise PromotionPlatformAuthenticationError(
                    "Argenco requires a two-factor authentication seed"
                )
            try:
                token = generate_totp(self.config.totp_seed)
            except ValueError as err:
                raise PromotionPlatformAuthenticationError(
                    "Argenco two-factor authentication seed is invalid"
                ) from err
            verified = await self._async_post(
                session,
                self._custom_action_url,
                {
                    "object": "user",
                    "class_action": "custom_api_validate",
                    "username": self.config.username,
                    "password": self.config.password,
                    "otp": token,
                },
                authentication=True,
            )
            verification = verified.get("data", {}).get("custom_result", {})
            if not isinstance(verification, dict) or not verification.get("exists"):
                raise PromotionPlatformAuthenticationError(
                    "Argenco two-factor authentication failed"
                )

        login_token = f"{token[:3]} {token[3:]}" if len(token) == 6 else token
        login = await self._async_post(
            session,
            self._login_url,
            {
                "fields": ["id", "voorletters", "first_name"],
                "username": self.config.username,
                "password": self.config.password,
                "otp": login_token,
                "is_www": 1,
            },
            authentication=True,
        )
        data = login.get("data", {})
        auth_token = str(data.get("auth_token", "")).strip()
        auth_email = str(data.get("email", "")).strip()
        if not auth_token or not auth_email:
            raise PromotionPlatformAuthenticationError("Argenco login failed")
        return {"auth-email": auth_email, "auth-token": auth_token}

    async def _async_fetch_all_benefits(
        self, session: aiohttp.ClientSession
    ) -> list[Promotion]:
        first_payload = await self._async_fetch_benefit_page(session, 0)
        data = first_payload.get("data", {})
        total = _integer(data.get("count"))
        pages = max(1, math.ceil(total / BENEFITS_PER_PAGE))
        if pages > MAX_BENEFIT_PAGES:
            raise PromotionPlatformError(
                f"Argenco returned an unexpected benefit count ({total})"
            )

        payloads = [first_payload]
        for page in range(1, pages):
            payloads.append(await self._async_fetch_benefit_page(session, page))

        promotions: dict[str, Promotion] = {}
        for payload in payloads:
            result = payload.get("data", {}).get("result", [])
            if not isinstance(result, list):
                raise PromotionPlatformError("Argenco returned an invalid benefit list")
            for item in result:
                promotion = self._promotion_from_item(item)
                if promotion is not None:
                    promotions[promotion.promotion_id] = promotion
        return list(promotions.values())

    async def _async_fetch_benefit_page(
        self, session: aiohttp.ClientSession, page: int
    ) -> dict:
        return await self._async_post(
            session,
            self._fetch_all_url,
            {
                "object": "v2_benefit",
                "fields": ["id", "created_at", "nw_timing"],
                "per_page": BENEFITS_PER_PAGE,
                "page": page,
                "order": "weight DESC",
                "filter": {
                    "advanced": {
                        "enabled": 1,
                        "tag_id": 0,
                        "catch_all": "",
                        "for_lang": self._language,
                        "for_maximum": 0,
                    }
                },
            },
        )

    async def _async_enrich_details(
        self,
        session: aiohttp.ClientSession,
        auth_headers: dict[str, str],
        promotions: list[Promotion],
    ) -> None:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def enrich(promotion: Promotion) -> None:
            async with semaphore:
                try:
                    payload = await self._async_post(
                        session,
                        self._fetch_all_url,
                        {
                            "object": "v2_benefit",
                            "fields": ["id", "created_at", "nw_timing"],
                            "id": promotion.promotion_id,
                        },
                        headers=auth_headers,
                    )
                except PromotionPlatformError as err:
                    _LOGGER.debug(
                        "Unable to load Argenco benefit %s: %s",
                        promotion.promotion_id,
                        err,
                    )
                    return
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    return
                promotion.title = (
                    _localized(data.get("discount_name"), self._language)
                    or promotion.title
                )
                promotion.promotion = _localized(
                    data.get("nw_discount_tag"), self._language
                ) or promotion.promotion
                promotion.description = _description(data, self._language)
                promotion.image_url = str(data.get("get_item") or promotion.image_url)
                valid_from, valid_until = _validity_dates(
                    _localized(data.get("nw_when"), self._language)
                    or _localized(data.get("nw_timing"), self._language)
                )
                promotion.valid_from = valid_from or promotion.valid_from
                promotion.valid_until = valid_until or promotion.valid_until
                linked_code = data.get("linked_code")
                if isinstance(linked_code, dict):
                    promotion.voucher_code = clean_text(
                        str(linked_code.get("code") or linked_code.get("value") or "")
                    )

        await asyncio.gather(*(enrich(promotion) for promotion in promotions))

    def _promotion_from_item(self, item: object) -> Promotion | None:
        if not isinstance(item, dict):
            return None
        promotion_id = str(item.get("id", "")).strip()
        title = _localized(item.get("discount_name"), self._language)
        if not promotion_id or not title:
            return None
        valid_from, valid_until = _validity_dates(
            _localized(item.get("nw_timing"), self._language)
        )
        return Promotion(
            promotion_id=promotion_id,
            platform_id=self.config.platform_id,
            platform_name=self.config.name,
            title=title,
            promotion=_localized(item.get("nw_discount_tag"), self._language),
            image_url=str(item.get("get_item") or ""),
            item_url=f"{self._web_origin}/benefit/{promotion_id}",
            valid_from=valid_from,
            valid_until=valid_until,
        )

    async def _async_post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        body: dict,
        *,
        headers: dict[str, str] | None = None,
        authentication: bool = False,
    ) -> dict:
        request_headers = {
            "Origin": self._web_origin,
            "Referer": f"{self._web_origin}/",
            **(headers or {}),
        }
        try:
            async with session.post(url, json=body, headers=request_headers) as response:
                if response.status >= 400:
                    error_class = (
                        PromotionPlatformAuthenticationError
                        if authentication
                        else PromotionPlatformError
                    )
                    raise error_class(
                        "Argenco request to "
                        f"{urlparse(url).path} returned HTTP {response.status}"
                    )
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise PromotionPlatformError(
                        "Argenco returned an invalid response"
                    ) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PromotionPlatformError(
                f"Argenco request failed: {type(err).__name__}"
            ) from err

        if not isinstance(payload, dict) or not payload.get("success"):
            error_class = (
                PromotionPlatformAuthenticationError
                if authentication
                else PromotionPlatformError
            )
            raise error_class("Argenco rejected the request")
        return payload

    @property
    def _web_origin(self) -> str:
        parsed = urlparse(self.config.base_url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    @property
    def _api_origin(self) -> str:
        parsed = urlparse(self.config.base_url)
        hostname = parsed.hostname or "api.argenco.be"
        if hostname in {"argenco.be", "www.argenco.be"}:
            hostname = "api.argenco.be"
        netloc = hostname
        if parsed.port:
            netloc = f"{hostname}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, "", "", "", ""))

    @property
    def _language(self) -> str:
        return "fr" if "/fr/" in urlparse(self.config.base_url).path.lower() else "nl"

    @property
    def _api_base(self) -> str:
        return f"{self._api_origin}/{self._language}/v3"

    @property
    def _custom_action_url(self) -> str:
        return f"{self._api_base}/pub/custom_action"

    @property
    def _login_url(self) -> str:
        return f"{self._api_base}/users/login"

    @property
    def _fetch_all_url(self) -> str:
        return f"{self._api_base}/pub/fetch_all"


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if text:
            self.parts.append(text)


def _localized(value: object, language: str) -> str:
    if isinstance(value, dict):
        selected = value.get(language) or value.get("nl") or value.get("fr") or ""
        return clean_text(str(selected))
    return clean_text(str(value or ""))


def _html_text(value: object) -> str:
    parser = _HTMLTextParser()
    parser.feed(str(value or ""))
    return clean_text(" ".join(parser.parts))


def _description(data: dict, language: str) -> str:
    parts: list[str] = []
    for field in ("nw_intro", "nw_what", "nw_how", "nw_when", "nw_where"):
        text = _html_text(_localized(data.get(field), language))
        if text:
            parts.append(text)
    blocks = data.get("block_info", [])
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for field in (
                "title_value",
                "string_value",
                "title_value_right",
                "string_value_right",
            ):
                text = _html_text(_localized(block.get(field), language))
                if text:
                    parts.append(text)
    return clean_text(" ".join(dict.fromkeys(parts)))


def _validity_dates(value: str) -> tuple[str, str]:
    text = clean_text(value)
    numeric_dates = [
        _safe_date(int(year), int(month), int(day))
        for day, month, year in _NUMERIC_DATE_RE.findall(text)
    ]
    numeric_dates = [parsed for parsed in numeric_dates if parsed]
    if numeric_dates:
        return numeric_dates[0], numeric_dates[-1]

    range_match = re.search(
        r"(\d{1,2})\s+([^\W\d_]{3,})\.?\s*-\s*"
        r"(\d{1,2})\s+([^\W\d_]{3,})\.?\s+(\d{4})",
        text.lower(),
    )
    if range_match:
        start_day, start_month, end_day, end_month, end_year = range_match.groups()
        end_year_value = int(end_year)
        start_month_value = _month_number(start_month)
        end_month_value = _month_number(end_month)
        start_year = (
            end_year_value - 1
            if start_month_value > end_month_value
            else end_year_value
        )
        return (
            _safe_date(start_year, start_month_value, int(start_day)),
            _safe_date(end_year_value, end_month_value, int(end_day)),
        )

    textual_matches = _TEXTUAL_DATE_RE.findall(text.lower())
    textual_dates = [
        _safe_date(int(year), _month_number(month), int(day))
        for day, month, year in textual_matches
    ]
    textual_dates = [parsed for parsed in textual_dates if parsed]
    if textual_dates:
        return textual_dates[0], textual_dates[-1]
    return "", ""


def _month_number(value: str) -> int:
    normalized = value.lower().rstrip(".")
    if normalized.startswith("juil"):
        return 7
    if normalized.startswith("juin"):
        return 6
    if normalized.startswith(("aoû", "aou")):
        return 8
    if normalized.startswith(("fév", "fev")):
        return 2
    return _MONTHS.get(normalized[:3], 0)


def _safe_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _integer(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
