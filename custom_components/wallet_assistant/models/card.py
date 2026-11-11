from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Optional

from ..const import (
    DEFAULT_BARCODE_FORMAT,
    DEFAULT_VIEW,
    DEFAULT_VIEWS,
    ITEM_TYPES,
    TYPE_LOYALTY,
)


def money(value) -> Decimal:
    """Validate an amount without introducing floating point rounding errors."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise ValueError("Amounts must be non-negative numbers with at most two decimal places") from None


@dataclass
class WalletItem:
    item_id: str
    name: str
    code: str
    owner: str
    user_id: str
    item_type: str = TYPE_LOYALTY
    format: Optional[str] = DEFAULT_BARCODE_FORMAT
    default_view: str = DEFAULT_VIEW
    logo_slug: Optional[str] = None
    expires_on: Optional[str] = None
    notes: Optional[str] = None
    promo_url: Optional[str] = None
    initial_value: Optional[float] = None
    currency: str = "EUR"
    spending: list = field(default_factory=list)

    @property
    def remaining_balance(self):
        if self.initial_value is None:
            return None
        return float(money(self.initial_value) - sum((money(entry["amount"]) for entry in self.spending), Decimal(0)))

    @classmethod
    def from_dict(cls, data: dict) -> "WalletItem":
        item_type = data.get("item_type") or TYPE_LOYALTY
        if item_type not in ITEM_TYPES:
            item_type = TYPE_LOYALTY

        default_view = data.get("default_view") or DEFAULT_VIEW
        if default_view not in DEFAULT_VIEWS:
            default_view = DEFAULT_VIEW

        initial_value = data.get("initial_value") if item_type == "voucher" else None
        if initial_value is not None:
            initial_value = float(money(initial_value))
        currency = data.get("currency", "EUR")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Currency must be a three-letter uppercase code")
        spending = data.get("spending", []) if item_type == "voucher" else []
        if not isinstance(spending, list):
            raise ValueError("Invalid spending history")
        for entry in spending:
            if not isinstance(entry, dict) or not entry.get("id") or money(entry.get("amount")) <= 0:
                raise ValueError("Invalid spending entry")
            date.fromisoformat(entry.get("spent_on", ""))
        if spending and (initial_value is None or sum((money(e["amount"]) for e in spending), Decimal(0)) > money(initial_value)):
            raise ValueError("Voucher value cannot be less than recorded spending")

        return cls(
            item_id=data.get("item_id"),
            name=data.get("name", ""),
            code=data.get("code", ""),
            owner=data.get("owner", ""),
            user_id=data.get("user_id", ""),
            item_type=item_type,
            format=data.get("format") or DEFAULT_BARCODE_FORMAT,
            default_view=default_view,
            logo_slug=data.get("logo_slug") or None,
            expires_on=data.get("expires_on") or data.get("expiry_date") or None,
            notes=data.get("notes") or None,
            promo_url=data.get("promo_url") or data.get("url") or None,
            initial_value=initial_value,
            currency=currency,
            spending=spending,
        )

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "code": self.code,
            "owner": self.owner,
            "user_id": self.user_id,
            "item_type": self.item_type,
            "format": self.format or DEFAULT_BARCODE_FORMAT,
            "default_view": self.default_view or DEFAULT_VIEW,
            "logo_slug": self.logo_slug,
            "expires_on": self.expires_on,
            "notes": self.notes,
            "promo_url": self.promo_url,
            "initial_value": self.initial_value,
            "currency": self.currency,
            "spending": self.spending,
            "remaining_balance": self.remaining_balance,
        }
