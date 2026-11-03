"""Money rules, including the installment boundaries of manual section 3.1."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporio import pricing
from emporio.pricing import Promotion


@pytest.mark.parametrize(
    "cents, text",
    [(208905, "R$ 2.089,05"), (32310, "R$ 323,10"), (5, "R$ 0,05"), (123456789, "R$ 1.234.567,89")],
)
def test_format_brl(cents, text):
    assert pricing.format_brl(cents) == text


def test_to_cents_avoids_float_error():
    assert pricing.to_cents(599.9) == 59990
    assert pricing.to_cents(1000) == 100000


def test_pix_applies_only_without_promotion():
    assert pricing.resolve_price(219900, [], "pix").final_cents == 208905
    promotion = Promotion(Decimal("10"), "Oferta")
    resolved = pricing.resolve_price(35900, [promotion], "pix")
    assert (resolved.source, resolved.final_cents) == ("promotion", 32310)


def test_other_payments_keep_list_price():
    assert pricing.resolve_price(219900, [], "debit").final_cents == 219900
    assert pricing.resolve_price(219900, []).source == "list"


def test_multiple_active_promotions_are_not_chosen_arbitrarily():
    promotions = [Promotion(Decimal("10"), "A"), Promotion(Decimal("20"), "B")]
    resolved = pricing.resolve_price(100000, promotions, "pix")
    assert resolved.source == "ambiguous"
    assert resolved.final_cents is None


def test_discount_rounds_half_up():
    assert pricing.apply_discount(59990, Decimal("5")) == 56991


@pytest.mark.parametrize("total, count", [(59990, 6), (42990, 3), (100000, 7), (33333, 2), (1, 1)])
def test_installments_add_up_to_total(total, count):
    parts = pricing.split_installments(total, count)
    assert sum(parts) == total
    assert len(parts) == count
    assert max(parts) - min(parts) <= 1


@pytest.mark.parametrize(
    "count, minimum",
    [(1, 5000), (3, 5000), (4, 8000), (6, 8000), (7, 10000), (12, 10000)],
)
def test_minimum_by_range(count, minimum):
    assert pricing.minimum_installment_cents(count) == minimum


@pytest.mark.parametrize("count", [0, 13, -1])
def test_invalid_installment_count_is_rejected(count):
    with pytest.raises(ValueError):
        pricing.minimum_installment_cents(count)


@pytest.mark.parametrize(
    "total, count, eligible",
    [
        (42990, 6, False),  # R$ 429,90 in 6x gives R$ 71,65, below R$ 80
        (59990, 6, True),  # R$ 599,90 in 6x gives R$ 99,98
        (48000, 6, True),  # exactly R$ 80 per installment
        (47999, 6, False),  # one cent short once split
        (15000, 3, True),  # exactly R$ 50 per installment
        (14999, 3, False),
        (120000, 12, True),  # exactly R$ 100 per installment
        (119999, 12, False),
        (3000, 1, True),  # a single charge is not split
    ],
)
def test_eligibility_uses_the_smallest_installment(total, count, eligible):
    assert pricing.is_installment_eligible(total, count) is eligible


@pytest.mark.parametrize(
    "total, expected", [(42990, 5), (59990, 6), (219900, 12), (15990, 3), (35900, 4)]
)
def test_max_eligible_installments(total, expected):
    assert pricing.max_eligible_installments(total) == expected


def test_schedule_groups_equal_values():
    assert pricing.installment_schedule(59990, 6) == [
        {"count": 2, "value": "R$ 99,99"},
        {"count": 4, "value": "R$ 99,98"},
    ]
