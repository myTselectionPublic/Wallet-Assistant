from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp

from ....models.promotion import Promotion
from ..base import BasePromotionPlatform
from ..utils import (
    clean_text,
    first_meaningful_text,
    generate_totp,
    join_text,
    stable_id,
)

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
    "/merchant",
    "/reward",
    "/rewards",
    "/search",
    "/shop",
    "/shops",
)


@dataclass(slots=True)
class _EdenredLoginResult:
    success: bool
    reason: str
    retry_fields: dict[str, str] = field(default_factory=dict)
    next_url: str = ""
    follow_url: str = ""


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

            if _looks_like_authentication_flow(main_html, main_url):
                _LOGGER.warning(
                    (
                        "Edenred Engagement platform %s is still in the authentication "
                        "flow after login: final_url=%s"
                    ),
                    self.config.platform_id,
                    main_url,
                )
                return []

            category_links = self._category_links(main_html, main_url)
            if category_links:
                _LOGGER.debug(
                    (
                        "Edenred Engagement platform %s authentication confirmed; "
                        "found %s category pages"
                    ),
                    self.config.platform_id,
                    len(category_links),
                )
                return await self._async_fetch_category_promotions(
                    session,
                    category_links,
                )

            promotions = self._parse_promotions(main_html, main_url, [])
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

                for promotion in self._parse_promotions(page_html, final_url, []):
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

    async def _async_fetch_category_promotions(
        self,
        session: aiohttp.ClientSession,
        category_links: list[tuple[str, str]],
    ) -> list[Promotion]:
        promotions_by_id: dict[str, Promotion] = {}
        for category_name, category_url in category_links:
            page_html, final_url = await self._async_fetch_category_page(
                session,
                category_name,
                category_url,
            )
            if not page_html:
                continue

            if _looks_like_login_page(page_html):
                _LOGGER.warning(
                    "Edenred Engagement category %s returned the login page",
                    category_name,
                )
                continue

            category_promotions = self._parse_promotions(
                page_html,
                final_url,
                [category_name],
            )
            _LOGGER.debug(
                "Edenred Engagement category %s yielded %s promotions",
                category_name,
                len(category_promotions),
            )
            for promotion in category_promotions:
                existing = promotions_by_id.get(promotion.promotion_id)
                if existing is None:
                    promotions_by_id[promotion.promotion_id] = promotion
                    continue

                existing.categories = list(
                    dict.fromkeys([*existing.categories, *promotion.categories])
                )

        _LOGGER.debug(
            "Edenred Engagement platform %s yielded %s unique promotions from categories",
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
        known_fields = dict(form.fields)
        referer = form.page_url or self._signin_url

        for attempt in range(1, 4):
            fields = self._login_fields(known_fields)
            _LOGGER.debug(
                (
                    "Edenred Engagement login attempt %s for platform %s: "
                    "action_url=%s form_uuid_present=%s csrf_present=%s fields=%s"
                ),
                attempt,
                self.config.platform_id,
                login_url,
                bool(fields.get("form_uuid")),
                bool(fields.get("CSRF_TOKEN")),
                sorted(fields),
            )

            post_result = await self._async_post_login_form(
                session,
                login_url,
                fields,
                referer,
            )
            if post_result is None:
                return False

            post_result = await self._async_complete_multifactor(
                session,
                post_result,
            )
            if post_result is None:
                return False

            response_body, response_content_type, response_url = post_result
            login_result = self._login_result_from_response(
                response_body,
                response_content_type,
                response_url,
            )
            if login_result.success:
                _LOGGER.debug(
                    "Edenred Engagement login succeeded for platform %s: %s",
                    self.config.platform_id,
                    login_result.reason,
                )
                return True

            if login_result.follow_url:
                follow_login_result = await self._async_follow_login_urls(
                    session,
                    login_result,
                    response_url or referer,
                )
                if follow_login_result.success:
                    _LOGGER.debug(
                        "Edenred Engagement login succeeded for platform %s: %s",
                        self.config.platform_id,
                        follow_login_result.reason,
                    )
                    return True

                _LOGGER.warning(
                    "Edenred Engagement login callback did not complete for platform %s: %s",
                    self.config.platform_id,
                    follow_login_result.reason,
                )
                return False

            if not login_result.retry_fields:
                _LOGGER.warning(
                    "Edenred Engagement login did not complete for platform %s: %s",
                    self.config.platform_id,
                    login_result.reason,
                )
                return False

            known_fields.update(login_result.retry_fields)
            login_url = login_result.next_url or login_url
            referer = response_url or referer

        _LOGGER.warning(
            "Edenred Engagement login did not complete for platform %s after JSON form retries",
            self.config.platform_id,
        )
        return False

    async def _async_follow_login_urls(
        self,
        session: aiohttp.ClientSession,
        login_result: _EdenredLoginResult,
        referer: str,
    ) -> _EdenredLoginResult:
        current_result = login_result
        current_referer = referer

        for follow_attempt in range(1, 6):
            if not current_result.follow_url:
                return current_result

            _LOGGER.debug(
                (
                    "Edenred Engagement login follow-up %s for platform %s: "
                    "url=%s reason=%s"
                ),
                follow_attempt,
                self.config.platform_id,
                current_result.follow_url,
                current_result.reason,
            )
            follow_result = await self._async_follow_login_url(
                session,
                current_result.follow_url,
                current_referer,
            )
            if follow_result is None:
                return _EdenredLoginResult(
                    success=False,
                    reason="login follow-up request failed",
                )

            follow_result = await self._async_complete_multifactor(
                session,
                follow_result,
            )
            if follow_result is None:
                return _EdenredLoginResult(
                    success=False,
                    reason="multi-factor authentication did not complete",
                )

            follow_body, follow_content_type, follow_url = follow_result
            current_referer = follow_url
            current_result = self._login_result_from_response(
                follow_body,
                follow_content_type,
                follow_url,
            )

        return _EdenredLoginResult(
            success=False,
            reason="login follow-up chain did not complete after 5 requests",
        )

    async def _async_complete_multifactor(
        self,
        session: aiohttp.ClientSession,
        response_data: tuple[str, str, str],
    ) -> tuple[str, str, str] | None:
        """Submit a TOTP token when Edenred redirects to its MFA form."""
        body, _, response_url = response_data
        if not _looks_like_multifactor_page(body, response_url):
            return response_data

        if not self.config.totp_seed:
            _LOGGER.warning(
                (
                    "Edenred Engagement platform %s requires a two-factor token; "
                    "configure its TOTP seed in the integration options"
                ),
                self.config.platform_id,
            )
            return None

        form = _EdenredMultifactorFormParser(response_url)
        form.feed(body)
        if not form.token_field:
            _LOGGER.warning(
                "Unable to find the Edenred two-factor token field for platform %s",
                self.config.platform_id,
            )
            return None

        try:
            token = generate_totp(self.config.totp_seed)
        except ValueError as err:
            _LOGGER.warning(
                "Invalid Edenred TOTP seed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return None

        fields = dict(form.fields)
        fields[form.token_field] = token
        self._last_totp_token = token
        try:
            _LOGGER.debug(
                (
                    "Submitting Edenred two-factor token for platform %s: "
                    "action_url=%s token_field=%s fields=%s"
                ),
                self.config.platform_id,
                form.action_url,
                form.token_field,
                sorted(fields),
            )
            return await self._async_post_login_form(
                session,
                form.action_url or response_url,
                fields,
                response_url,
            )
        finally:
            self._last_totp_token = ""

    async def _async_post_login_form(
        self,
        session: aiohttp.ClientSession,
        login_url: str,
        fields: dict[str, str],
        referer: str,
    ) -> tuple[str, str, str] | None:
        body, content_type = _multipart_body(fields)
        headers = {
            "Content-Type": content_type,
            "Origin": self._origin,
            "Referer": referer,
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
                        (
                            "Edenred Engagement login POST returned HTTP %s for "
                            "platform %s; parsing response for form errors: %s"
                        ),
                        response.status,
                        self.config.platform_id,
                        self._response_details(response, response_body),
                    )
                return (
                    response_body,
                    response.headers.get("Content-Type", ""),
                    str(response.url),
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement login POST request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return None

    async def _async_follow_login_url(
        self,
        session: aiohttp.ClientSession,
        follow_url: str,
        referer: str,
    ) -> tuple[str, str, str] | None:
        try:
            async with session.get(
                follow_url,
                headers={"Referer": referer},
                allow_redirects=True,
            ) as response:
                response_body = await response.text(errors="replace")
                self._log_response("login callback GET", response, response_body)
                if response.status >= 400:
                    _LOGGER.debug(
                        (
                            "Edenred Engagement login callback returned HTTP %s for "
                            "platform %s: %s"
                        ),
                        response.status,
                        self.config.platform_id,
                        self._response_details(response, response_body),
                    )
                    return None
                return (
                    response_body,
                    response.headers.get("Content-Type", ""),
                    str(response.url),
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement login callback request failed for platform %s: %s",
                self.config.platform_id,
                err,
            )
            return None

    def _login_fields(self, known_fields: dict[str, str]) -> dict[str, str]:
        form_uuid = known_fields.get("form_uuid") or known_fields.get("uuid") or ""
        fields = {
            "form_uuid": form_uuid,
            "Username": self.config.username,
            "Password": self.config.password,
            "CSRF_TOKEN": known_fields.get("CSRF_TOKEN", ""),
            "forgot_password": known_fields.get("forgot_password", "form:recover"),
            "submit": known_fields.get("submit", "Login"),
        }
        for key, value in known_fields.items():
            fields.setdefault(key, value)
        return fields

    def _login_result_from_response(
        self,
        body: str,
        content_type: str,
        response_url: str,
    ) -> _EdenredLoginResult:
        if "application/json" in content_type.lower():
            return self._login_result_from_json(body, response_url)

        device_follow_url = _device_check_follow_url(body, response_url)
        if device_follow_url:
            return _EdenredLoginResult(
                success=False,
                reason="HTML response requested Edenred device registration",
                follow_url=device_follow_url,
            )

        login_page_returned = _looks_like_login_page(body)
        authentication_flow_returned = _looks_like_authentication_flow(body, response_url)
        _LOGGER.debug(
            (
                "Edenred Engagement login HTML response for platform %s: "
                "login_page_returned=%s authentication_flow_returned=%s final_url=%s"
            ),
            self.config.platform_id,
            login_page_returned,
            authentication_flow_returned,
            response_url,
        )
        if login_page_returned or authentication_flow_returned:
            return _EdenredLoginResult(
                success=False,
                reason=(
                    "HTML response is still in the Edenred authentication flow "
                    f"(final_url={response_url})"
                ),
            )
        return _EdenredLoginResult(
            success=True,
            reason=f"HTML response left the authentication flow (final_url={response_url})",
        )

    def _login_result_from_json(
        self,
        body: str,
        response_url: str,
    ) -> _EdenredLoginResult:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return _EdenredLoginResult(
                success=False,
                reason="login response was JSON content-type but could not be decoded",
            )

        errors = _json_form_errors(payload)
        fields = _json_form_fields(payload)
        uuid_value = str(payload.get("uuid") or "").strip()
        if uuid_value:
            fields["form_uuid"] = uuid_value
            fields["uuid"] = uuid_value

        next_action = payload.get("nextAction")
        next_url = ""
        next_method = ""
        redirect = False
        if isinstance(next_action, dict):
            next_url = urljoin(
                response_url or self._login_url,
                str(next_action.get("actionUrl") or ""),
            )
            next_method = str(next_action.get("method") or "").upper()
            redirect = bool(next_action.get("redirect"))

        raw_redirect_url = str(
            payload.get("redirectUrl")
            or payload.get("redirect")
            or payload.get("url")
            or ""
        )
        redirect_url = (
            urljoin(response_url or self._origin, raw_redirect_url)
            if raw_redirect_url
            else ""
        )
        success = bool(
            payload.get("success")
            or payload.get("authenticated")
            or (redirect and redirect_url and not _url_is_authentication_flow(redirect_url))
        )

        _LOGGER.debug(
            (
                "Edenred Engagement login JSON response for platform %s: "
                "success=%s uuid_present=%s retry_field_keys=%s errors=%s "
                "next_url=%s redirect=%s redirect_url=%s keys=%s"
            ),
            self.config.platform_id,
            success,
            bool(uuid_value),
            sorted(fields),
            errors or "none",
            next_url,
            redirect,
            redirect_url,
            sorted(payload) if isinstance(payload, dict) else [],
        )

        if success:
            return _EdenredLoginResult(
                success=True,
                reason="JSON response reported successful authentication",
            )

        if next_method == "GET" and next_url:
            return _EdenredLoginResult(
                success=False,
                reason="JSON login response requested authentication callback GET",
                follow_url=next_url,
            )

        if errors:
            return _EdenredLoginResult(
                success=False,
                reason=f"JSON login form returned errors: {'; '.join(errors)}",
            )

        if fields:
            return _EdenredLoginResult(
                success=False,
                reason="JSON login form returned next fields to submit",
                retry_fields=fields,
                next_url=next_url,
            )

        return _EdenredLoginResult(
            success=False,
            reason="JSON login response did not report success or provide retry fields",
        )

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

                device_follow_url = _device_check_follow_url(body, str(response.url))
                if device_follow_url:
                    _LOGGER.debug(
                        (
                            "Edenred Engagement main page for platform %s requires "
                            "device registration; following %s"
                        ),
                        self.config.platform_id,
                        device_follow_url,
                    )
                    follow_result = await self._async_follow_login_url(
                        session,
                        device_follow_url,
                        str(response.url),
                    )
                    if follow_result is not None:
                        follow_body, _, follow_url = follow_result
                        return follow_body, follow_url

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

    async def _async_fetch_category_page(
        self,
        session: aiohttp.ClientSession,
        category_name: str,
        category_url: str,
    ) -> tuple[str, str]:
        try:
            async with session.get(
                category_url,
                headers={"Referer": self._main_url},
            ) as response:
                body = await response.text(errors="replace")
                self._log_response(f"category GET {category_name}", response, body)
                if response.status >= 400:
                    _LOGGER.debug(
                        (
                            "Edenred Engagement category %s request failed for "
                            "platform %s: %s"
                        ),
                        category_name,
                        self.config.platform_id,
                        self._response_details(response, body),
                    )
                    return "", category_url
                return body, str(response.url)
        except aiohttp.ClientError as err:
            _LOGGER.debug(
                "Edenred Engagement category %s request failed for platform %s: %s",
                category_name,
                self.config.platform_id,
                err,
            )
            return "", category_url

    def _parse_promotions(
        self,
        html: str,
        page_url: str,
        categories: list[str],
    ) -> list[Promotion]:
        parser = _EdenredPromotionParser(
            platform_id=self.config.platform_id,
            platform_name=self.config.name,
            page_url=page_url,
            categories=categories,
        )
        parser.feed(html)
        return parser.promotions

    def _category_links(self, html: str, page_url: str) -> list[tuple[str, str]]:
        parser = _EdenredCategoryParser(page_url)
        parser.feed(html)
        return parser.category_links

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
        return urljoin(self._origin, "/SmartSpending")

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
        token = getattr(self, "_last_totp_token", "")
        if token:
            excerpt = excerpt.replace(token, "[redacted-totp-token]")
        return excerpt


class _EdenredMultifactorFormParser(HTMLParser):
    """Extract the Edenred MFA action, hidden fields, and token field name."""

    _TOKEN_MARKERS = (
        "authenticator",
        "code",
        "multifactor",
        "mfa",
        "one-time",
        "onetime",
        "otp",
        "token",
        "verification",
    )

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.action_url = page_url
        self.fields: dict[str, str] = {}
        self.token_field = ""
        self._inside_form = False
        self._token_candidates: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        if tag == "form":
            action = attrs.get("action", "").strip()
            action_url = urljoin(self.page_url, action) if action else self.page_url
            if _url_is_multifactor_flow(action_url) or _url_is_multifactor_flow(
                self.page_url
            ):
                self._inside_form = True
                self.action_url = action_url
            return

        if not self._inside_form or tag not in {"input", "button"}:
            return

        name = attrs.get("name", "").strip()
        if not name or "disabled" in attrs:
            return

        field_type = attrs.get("type", "text").lower()
        value = attrs.get("value", "")
        self.fields[name] = value
        if tag == "button" or field_type in {"hidden", "submit", "button", "reset"}:
            return

        candidate_text = " ".join(
            (
                name,
                attrs.get("id", ""),
                attrs.get("autocomplete", ""),
                attrs.get("placeholder", ""),
            )
        ).lower()
        score = 0
        if attrs.get("autocomplete", "").lower() == "one-time-code":
            score += 100
        if any(marker in candidate_text for marker in self._TOKEN_MARKERS):
            score += 50
        if field_type in {"number", "tel"}:
            score += 20
        self._token_candidates.append((score, name))
        self.token_field = max(
            self._token_candidates,
            key=lambda candidate: candidate[0],
        )[1]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._inside_form:
            self._inside_form = False


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


class _EdenredCategoryParser(HTMLParser):
    """Extract Edenred retail category search links from the homepage."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.category_links: list[tuple[str, str]] = []
        self._seen_urls: set[str] = set()
        self._current_href = ""
        self._current_name = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attrs = {key: value or "" for key, value in attrs_list}
        href = attrs.get("href", "").strip()
        category_id = attrs.get("data-category-id", "").strip()
        category_name = attrs.get("data-category-name", "").strip()
        is_category_link = (
            bool(category_id)
            or "stype=category" in href.lower()
            or "sfields[c]=" in href.lower()
        )
        if not href or not is_category_link:
            return

        self._current_href = href
        self._current_name = category_name
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        category_url = urljoin(self.page_url, self._current_href)
        category_name = clean_text(
            self._current_name or _clean_category_label(" ".join(self._text_parts))
        )
        self._current_href = ""
        self._current_name = ""
        self._text_parts = []

        if not category_name or category_url in self._seen_urls:
            return

        self._seen_urls.add(category_url)
        self.category_links.append((category_name, category_url))


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
        self._capture_stack: list[tuple[str, str]] = []
        self._skip_text_depth = 0
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

        if tag == "button":
            self._skip_text_depth += 1

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
            self._capture_stack.append((tag, capture_field))

        self._set_from_attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self._current:
            return

        if self._capture_stack and self._capture_stack[-1][0] == tag:
            self._capture_stack.pop()

        self._card_depth -= 1
        if tag == "button" and self._skip_text_depth > 0:
            self._skip_text_depth -= 1
        if self._card_depth <= 0:
            self._finish_card()

    def handle_data(self, data: str) -> None:
        if not self._current:
            return

        text = clean_text(data)
        if not text:
            return

        self._current["text"].append(text)
        if self._capture_stack and self._skip_text_depth == 0:
            _, field = self._capture_stack[-1]
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

        if attrs.get("data-retailer-id") or attrs.get("data-retailer-name"):
            return True

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
            marker in class_text
            for marker in ("description", "summary", "text", "copy", "preview")
        ):
            return "description"
        if any(marker in class_text for marker in ("discount", "saving", "value", "tag")):
            return "promotion"
        if "amount" in class_text:
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
            "data-retailer-id",
            "id",
        ):
            value = attrs.get(key, "").strip()
            if value and not self._current.get("promotion_id"):
                self._current["promotion_id"] = value
                break

        retailer_name = attrs.get("data-retailer-name", "").strip()
        if retailer_name and not self._current.get("title"):
            self._current["title"] = clean_text(retailer_name)

    def _finish_card(self) -> None:
        if not self._current:
            return

        text = clean_text(" ".join(self._current.get("text", [])))
        title = _clean_edenred_title(
            self._current.get("title") or first_meaningful_text(text)
        )
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
        self._skip_text_depth = 0


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


class _EdenredMetaRefreshParser(HTMLParser):
    """Extract a meta refresh URL from Edenred interstitial pages."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.refresh_url = ""

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.refresh_url:
            return

        attrs = {key.lower(): value or "" for key, value in attrs_list}
        if attrs.get("http-equiv", "").lower() != "refresh":
            return

        content = attrs.get("content", "")
        content_lower = content.lower()
        marker = "url="
        marker_index = content_lower.find(marker)
        if marker_index == -1:
            return

        refresh_target = content[marker_index + len(marker):].strip().strip("'\"")
        if refresh_target:
            self.refresh_url = urljoin(self.page_url, refresh_target)


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


def _json_form_fields(payload: object) -> dict[str, str]:
    fields: dict[str, str] = {}
    _collect_json_form_fields(payload, fields)
    return fields


def _collect_json_form_fields(value: object, fields: dict[str, str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_json_form_fields(item, fields)
        return

    if not isinstance(value, dict):
        return

    field_name = str(value.get("name") or value.get("id") or "").strip()
    if field_name:
        field_value = value.get("value", "")
        if field_value is None:
            field_value = ""
        fields[field_name] = str(field_value)

    for nested_key in ("fields", "formFields", "formFieldSections", "sections"):
        nested_value = value.get(nested_key)
        if nested_value is not None:
            _collect_json_form_fields(nested_value, fields)


def _json_form_errors(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []

    errors: list[str] = []
    global_error = payload.get("globalFormError")
    if isinstance(global_error, dict):
        errors.extend(
            clean_text(str(global_error.get(key) or ""))
            for key in ("title", "message")
            if clean_text(str(global_error.get(key) or ""))
        )

    for key in ("error", "errorMessage", "message"):
        message = clean_text(str(payload.get(key) or ""))
        if message:
            errors.append(message)

    _collect_json_field_errors(payload.get("formFieldSections"), errors)
    return list(dict.fromkeys(errors))


def _collect_json_field_errors(value: object, errors: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_json_field_errors(item, errors)
        return

    if not isinstance(value, dict):
        return

    for key in ("error", "errorMessage", "validationError", "message"):
        message = clean_text(str(value.get(key) or ""))
        if message:
            errors.append(message)

    for nested_key in ("fields", "formFields", "formFieldSections", "sections"):
        nested_value = value.get(nested_key)
        if nested_value is not None:
            _collect_json_field_errors(nested_value, errors)


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


def _looks_like_multifactor_page(html: str, url: str) -> bool:
    lowered = html.lower()
    return _url_is_multifactor_flow(url) or (
        "multifactor" in lowered
        and "validate" in lowered
        and "<form" in lowered
    )


def _url_is_multifactor_flow(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    return path.endswith("/authentication/multifactor/validate")


def _device_check_follow_url(html: str, url: str) -> str:
    path = urlparse(url).path.lower()
    if path != "/authentication/device/check":
        return ""

    parser = _EdenredMetaRefreshParser(url)
    parser.feed(html)
    if "/authentication/device/unique" in urlparse(parser.refresh_url).path.lower():
        return parser.refresh_url
    return ""


def _looks_like_authentication_flow(html: str, url: str) -> bool:
    lowered = html.lower()
    return _url_is_authentication_flow(url) and "retail-categories" not in lowered


def _url_is_authentication_flow(url: str) -> bool:
    return urlparse(url).path.lower().startswith("/authentication")


def _looks_like_promotion_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(marker in path for marker in PROMOTION_LINK_MARKERS)


def _clean_category_label(value: str) -> str:
    text = clean_text(value)
    if "(" in text:
        text = text.rsplit("(", 1)[0]
    return clean_text(text)


def _clean_edenred_title(value: str) -> str:
    title = clean_text(value)
    for suffix in (
        "- Bekijk aanbiedingen",
        "- Bekijk aanbieding",
        "- Bekijk voordelen",
        "- View offers",
    ):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    return clean_text(title)
