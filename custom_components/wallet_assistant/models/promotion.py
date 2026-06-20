from __future__ import annotations

from dataclasses import dataclass, field


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
        normalized_query = query.strip().lower()
        if not normalized_query:
            return False

        haystack = " ".join(
            [
                self.title,
                self.promotion,
                self.description,
                self.platform_name,
                self.voucher_code,
                " ".join(self.categories),
            ]
        ).lower()
        return normalized_query in haystack
