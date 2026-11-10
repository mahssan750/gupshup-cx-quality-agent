"""Thread-safe USD budget accounting for paid model calls."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4


class BudgetExceeded(RuntimeError):
    """Raised before a request that would exceed the configured stop threshold."""


@dataclass(frozen=True)
class BudgetSnapshot:
    hard_limit_usd: float
    stop_at_usd: float
    spent_usd: float
    reserved_usd: float

    @property
    def remaining_to_stop_usd(self) -> float:
        return max(0.0, self.stop_at_usd - self.spent_usd - self.reserved_usd)

    @property
    def remaining_hard_limit_usd(self) -> float:
        return max(0.0, self.hard_limit_usd - self.spent_usd - self.reserved_usd)


@dataclass(frozen=True)
class BudgetReservation:
    id: str
    estimated_cost_usd: float
    label: str


class BudgetGuard:
    """Reserve before a request and commit the provider-reported cost afterward.

    ``stop_at_usd`` is intentionally lower than the hard key limit. Reservations are
    included in the check so concurrent workers cannot all spend the same remaining funds.
    """

    def __init__(
        self,
        hard_limit_usd: float = 4.0,
        stop_at_usd: float = 3.8,
        initial_spend_usd: float = 0.0,
    ) -> None:
        if hard_limit_usd <= 0:
            raise ValueError("hard_limit_usd must be positive")
        if not 0 < stop_at_usd <= hard_limit_usd:
            raise ValueError("stop_at_usd must be positive and no greater than hard_limit_usd")
        if initial_spend_usd < 0 or initial_spend_usd > hard_limit_usd:
            raise ValueError("initial_spend_usd is outside the budget")
        self.hard_limit_usd = float(hard_limit_usd)
        self.stop_at_usd = float(stop_at_usd)
        self._spent_usd = float(initial_spend_usd)
        self._reservations: dict[str, BudgetReservation] = {}
        self._lock = RLock()

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            reserved = sum(r.estimated_cost_usd for r in self._reservations.values())
            return BudgetSnapshot(
                hard_limit_usd=self.hard_limit_usd,
                stop_at_usd=self.stop_at_usd,
                spent_usd=self._spent_usd,
                reserved_usd=reserved,
            )

    def reserve(self, estimated_cost_usd: float, label: str = "model request") -> BudgetReservation:
        if estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd cannot be negative")
        estimated_cost_usd = float(estimated_cost_usd)
        with self._lock:
            current_reserved = sum(r.estimated_cost_usd for r in self._reservations.values())
            projected = self._spent_usd + current_reserved + estimated_cost_usd
            if projected > self.stop_at_usd + 1e-12:
                raise BudgetExceeded(
                    f"Budget guard stopped {label}: projected spend ${projected:.4f} "
                    f"exceeds stop threshold ${self.stop_at_usd:.2f}."
                )
            reservation = BudgetReservation(
                id=uuid4().hex,
                estimated_cost_usd=estimated_cost_usd,
                label=label,
            )
            self._reservations[reservation.id] = reservation
            return reservation

    def commit(self, reservation: BudgetReservation, actual_cost_usd: float | None = None) -> float:
        """Replace a reservation with actual spend and return total spent."""
        with self._lock:
            active = self._reservations.pop(reservation.id, None)
            if active is None:
                raise ValueError("Unknown or already settled reservation")
            cost = active.estimated_cost_usd if actual_cost_usd is None else float(actual_cost_usd)
            if cost < 0:
                raise ValueError("actual_cost_usd cannot be negative")
            self._spent_usd += cost
            # A provider can report a little more than the preflight estimate. Never allow
            # accounting to conceal that the real hard limit was crossed.
            if self._spent_usd > self.hard_limit_usd + 1e-12:
                raise BudgetExceeded(
                    f"Provider-reported spend ${self._spent_usd:.4f} exceeded the hard "
                    f"limit ${self.hard_limit_usd:.2f}. Stop all further requests."
                )
            return self._spent_usd

    def cancel(self, reservation: BudgetReservation) -> None:
        """Release a reservation after a request failed before incurring cost."""
        with self._lock:
            self._reservations.pop(reservation.id, None)

