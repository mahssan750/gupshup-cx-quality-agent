import pytest

from cxquality.budget import BudgetExceeded, BudgetGuard


def test_reservations_block_concurrent_overspend():
    guard = BudgetGuard(hard_limit_usd=4, stop_at_usd=3.8)
    first = guard.reserve(2.0, "first")
    with pytest.raises(BudgetExceeded):
        guard.reserve(2.0, "second")
    guard.commit(first, 1.5)
    second = guard.reserve(2.0, "second")
    guard.cancel(second)
    assert guard.snapshot().spent_usd == 1.5


def test_provider_cost_is_authoritative():
    guard = BudgetGuard(hard_limit_usd=4, stop_at_usd=3.8)
    reservation = guard.reserve(0.1)
    guard.commit(reservation, 0.25)
    assert guard.snapshot().spent_usd == pytest.approx(0.25)


def test_invalid_limits_are_rejected():
    with pytest.raises(ValueError):
        BudgetGuard(hard_limit_usd=4, stop_at_usd=4.1)

