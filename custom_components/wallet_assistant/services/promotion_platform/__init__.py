from __future__ import annotations

from .base import BasePromotionPlatform, PromotionPlatformConfig
from .platforms import (
    PLATFORM_ADAPTERS,
    get_platform_adapter,
    register_platform_adapter,
)
from .platforms.benefits_at_work import BenefitsAtWorkPlatform
from .platforms.crelan_coop_deals import CrelanCoopDealsPlatform
from .platforms.edenred_engagement import EdenredEngagementPlatform
from .registry import (
    PROMOTION_CACHE,
    PROMOTION_SEARCH_MIN_LENGTH,
    PromotionPlatformRegistry,
    get_promotion_platform_configs,
)

__all__ = [
    "BasePromotionPlatform",
    "BenefitsAtWorkPlatform",
    "CrelanCoopDealsPlatform",
    "EdenredEngagementPlatform",
    "PLATFORM_ADAPTERS",
    "PROMOTION_CACHE",
    "PROMOTION_SEARCH_MIN_LENGTH",
    "PromotionPlatformConfig",
    "PromotionPlatformRegistry",
    "get_platform_adapter",
    "get_promotion_platform_configs",
    "register_platform_adapter",
]
