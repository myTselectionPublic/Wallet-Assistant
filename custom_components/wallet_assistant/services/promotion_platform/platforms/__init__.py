from __future__ import annotations

from ..base import BasePromotionPlatform
from .benefits_at_work import BenefitsAtWorkPlatform
from .edenred_engagement import EdenredEngagementPlatform

PLATFORM_ADAPTERS: dict[str, type[BasePromotionPlatform]] = {
    "benefits_at_work": BenefitsAtWorkPlatform,
    "edenred_engagement": EdenredEngagementPlatform,
}


def register_platform_adapter(
    platform_id: str,
    adapter_class: type[BasePromotionPlatform],
) -> None:
    """Register or replace a promotion platform adapter."""
    PLATFORM_ADAPTERS[platform_id] = adapter_class


def get_platform_adapter(platform_id: str) -> type[BasePromotionPlatform]:
    """Return the adapter class for a configured platform id."""
    return PLATFORM_ADAPTERS.get(platform_id, BasePromotionPlatform)
