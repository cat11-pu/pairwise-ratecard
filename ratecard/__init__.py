"""阶梯计费内核：分段单价、跨段拆分、折扣叠加、最低消费与封顶。"""

from .core import (
    KIND_FLAT,
    KIND_PERCENT,
    PERCENT_SCALE,
    PRICE_SCALE,
    Discount,
    RateCard,
    round_half_up,
    segment_amount,
    split_usage,
    validate_tiers,
)

__all__ = [
    "PRICE_SCALE",
    "PERCENT_SCALE",
    "KIND_PERCENT",
    "KIND_FLAT",
    "Discount",
    "RateCard",
    "round_half_up",
    "segment_amount",
    "split_usage",
    "validate_tiers",
]
