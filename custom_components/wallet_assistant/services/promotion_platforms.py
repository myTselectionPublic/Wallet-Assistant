from __future__ import annotations

from .promotion_platform import (
    BasePromotionPlatform,
    BenefitsAtWorkPlatform,
    CrelanCoopDealsPlatform,
    EdenredEngagementPlatform,
    PLATFORM_ADAPTERS,
    PROMOTION_CACHE,
    PROMOTION_SEARCH_MIN_LENGTH,
    PromotionPlatformConfig,
    PromotionPlatformRegistry,
    get_platform_adapter,
    get_promotion_platform_configs,
    register_platform_adapter,
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
