from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata


@dataclass(slots=True)
class Promotion:
    """Normalized promotion or voucher from an external platform."""

    promotion_id: str
    platform_id: str
    platform_name: str
    title: str
    promotion: str = ""
    description: str = ""
    image_url: str = ""
    item_url: str = ""
    voucher_code: str = ""
    valid_from: str = ""
    valid_until: str = ""
    categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "promotion_id": self.promotion_id,
            "platform_id": self.platform_id,
            "platform_name": self.platform_name,
            "title": self.title,
            "promotion": self.promotion,
            "description": self.description,
            "image_url": self.image_url,
            "item_url": self.item_url,
            "voucher_code": self.voucher_code,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "categories": self.categories,
        }

    def matches(self, query: str) -> bool:
        normalized_query = _normalize_search_text(query)
        if not normalized_query:
            return False

        haystack = _normalize_search_text(
            " ".join(
                [
                    self.title,
                    self.promotion,
                    self.description,
                    self.platform_name,
                    self.voucher_code,
                    self.item_url,
                    " ".join(self.categories),
                ]
            )
        )
        compact_query = normalized_query.replace(" ", "")
        compact_haystack = haystack.replace(" ", "")

        if normalized_query in haystack or compact_query in compact_haystack:
            return True

        return all(
            token in haystack or token in compact_haystack
            for token in normalized_query.split()
        )


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        char
        for char in normalized
        if not unicodedata.combining(char)
    ).lower()
    return " ".join(
        "".join(
            [
                char if char.isalnum() else " "
                for char in ascii_text
            ]
        ).split()
    )
