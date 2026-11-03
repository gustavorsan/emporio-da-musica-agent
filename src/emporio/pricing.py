"""Deterministic money rules: promotions, PIX discount and installments.

Each rule traces back to the store manual (sections 3, 3.1 and 6.2). Editing the policy
Markdown does not change these calculations, so tests pin both sides together.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Sequence

PIX_DISCOUNT_PERCENT = Decimal("5")
MAX_INSTALLMENTS = 12

PriceSource = Literal["list", "promotion", "pix", "ambiguous"]


@dataclass(frozen=True)
class Promotion:
    percent: Decimal
    description: str | None


@dataclass(frozen=True)
class ResolvedPrice:
    list_cents: int
    source: PriceSource
    # None only when more than one promotion is active and no price can be chosen.
    final_cents: int | None
    promotion: Promotion | None


def format_brl(cents: int) -> str:
    """Format cents in Brazilian notation, for example R$ 2.089,05."""
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def format_percent(percent: Decimal) -> str:
    return f"{percent.normalize():f}".replace(".", ",") + "%"


def to_cents(amount_brl: float | int | str | Decimal) -> int:
    """Convert a customer-facing amount to cents; str() avoids binary float artifacts."""
    return int(
        (Decimal(str(amount_brl)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def apply_discount(cents: int, percent: Decimal) -> int:
    discounted = Decimal(cents) * (Decimal(100) - percent) / Decimal(100)
    return int(discounted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def resolve_price(
    list_cents: int, active_promotions: Sequence[Promotion], payment: str | None = None
) -> ResolvedPrice:
    """Pick the price for a payment method; discounts never stack (manual, section 6.2)."""
    if len(active_promotions) > 1:
        return ResolvedPrice(list_cents, "ambiguous", None, None)
    if active_promotions:
        promotion = active_promotions[0]
        final = apply_discount(list_cents, promotion.percent)
        return ResolvedPrice(list_cents, "promotion", final, promotion)
    if payment == "pix":
        final = apply_discount(list_cents, PIX_DISCOUNT_PERCENT)
        return ResolvedPrice(list_cents, "pix", final, None)
    return ResolvedPrice(list_cents, "list", list_cents, None)


def minimum_installment_cents(count: int) -> int:
    """Minimum per installment (section 3.1); the R$ 50 up-to-3x floor is an assumption."""
    if not 1 <= count <= MAX_INSTALLMENTS:
        raise ValueError(f"installments must be between 1 and {MAX_INSTALLMENTS}")
    if count <= 3:
        return 5_000
    if count <= 6:
        return 8_000
    return 10_000


def split_installments(total_cents: int, count: int) -> list[int]:
    """Split a total so the values add up exactly; leftover cents go to the first ones."""
    base, remainder = divmod(total_cents, count)
    return [base + 1] * remainder + [base] * (count - remainder)


def is_installment_eligible(total_cents: int, count: int) -> bool:
    # A single charge is not split, so the per-installment minimum does not restrict it.
    if count == 1:
        return True
    return min(split_installments(total_cents, count)) >= minimum_installment_cents(count)


def max_eligible_installments(total_cents: int) -> int:
    return max(
        count
        for count in range(1, MAX_INSTALLMENTS + 1)
        if is_installment_eligible(total_cents, count)
    )


def installment_schedule(total_cents: int, count: int) -> list[dict]:
    """Group equal installments, for example 2x of R$ 99,99 plus 4x of R$ 99,98."""
    groups: dict[int, int] = {}
    for value in split_installments(total_cents, count):
        groups[value] = groups.get(value, 0) + 1
    return [{"count": times, "value": format_brl(value)} for value, times in groups.items()]
