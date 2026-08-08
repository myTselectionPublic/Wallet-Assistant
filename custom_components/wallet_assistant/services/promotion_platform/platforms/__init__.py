from __future__ import annotations

from ..base import BasePromotionPlatform
from .argenco import ArgencoPlatform
from .benefits_at_work import BenefitsAtWorkPlatform
from .crelan_coop_deals import CrelanCoopDealsPlatform
from .edenred_engagement import EdenredEngagementPlatform
from .engie_benefits import EngieBenefitsPlatform

PLATFORM_ADAPTERS: dict[str, type[BasePromotionPlatform]] = {
    "argenco": ArgencoPlatform,
    "benefits_at_work": BenefitsAtWorkPlatform,
    "crelan_coop_deals": CrelanCoopDealsPlatform,
    "edenred_engagement": EdenredEngagementPlatform,
    "engie_benefits": EngieBenefitsPlatform,
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
