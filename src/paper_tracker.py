"""
Paper trading tracker — records strategy predictions vs actual outcomes
without placing any real orders.

Tracks whether the 'cheaper side' prediction (paired with pair-cost gate)
would have been correct over time, to validate strategy accuracy before
risking capital in new markets.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict

logger = logging.getLogger("ebisu.strategy.paper_tracker")


@dataclass
class PaperWindow:
    """One market window's paper record."""
    window_id: str = ""
    asset: str = ""

    # Strategy prediction at entry time
    predicted_side: str = "NONE"       # UP, DOWN, or NONE
    pair_cost: float = 0.0
    up_ask: float = 0.0
    down_ask: float = 0.0
    pair_cost_ok: bool = False         # Was pair cost below ceiling?

    # Actual outcome
    outcome: Optional[str] = None      # "UP" or "DOWN" after resolution

    # Timing
    timestamp: float = 0.0


class PaperTracker:
    """
    Track strategy prediction accuracy without trading.
    Logs every window's prediction and outcome for offline analysis.
    """

    def __init__(self):
        self._windows: List[PaperWindow] = []
        self._current: Optional[PaperWindow] = None

    def record_prediction(
        self,
        window_id: str,
        asset: str,
        predicted_side: str,
        up_ask: float,
        down_ask: float,
        pair_cost_ok: bool,
    ) -> None:
        """Record strategy prediction for current window."""
        self._current = PaperWindow(
            window_id=window_id,
            asset=asset,
            predicted_side=predicted_side,
            up_ask=up_ask,
            down_ask=down_ask,
            pair_cost=up_ask + down_ask,
            pair_cost_ok=pair_cost_ok,
            timestamp=time.time(),
        )

    def record_outcome(self, outcome: str) -> None:
        """Record actual window outcome (UP or DOWN)."""
        if self._current:
            self._current.outcome = outcome
            self._windows.append(self._current)
            self._log_result()
            self._current = None

    def _log_result(self) -> None:
        """Log the prediction vs outcome."""
        w = self._windows[-1]
        correct = (w.predicted_side == w.outcome)

        # Running accuracy on windows where we had a valid entry signal
        actionable = [x for x in self._windows if x.pair_cost_ok and x.outcome and x.predicted_side != "NONE"]
        sub_dollar = [x for x in actionable if x.pair_cost < 1.00]

        total = len(actionable)
        wins = sum(1 for x in actionable if x.predicted_side == x.outcome)
        accuracy = (wins / total * 100) if total > 0 else 0

        sub_total = len(sub_dollar)
        sub_wins = sum(1 for x in sub_dollar if x.predicted_side == x.outcome)
        sub_accuracy = (sub_wins / sub_total * 100) if sub_total > 0 else 0

        logger.info(
            "PAPER_RESULT window=%s asset=%s predicted=%s actual=%s "
            "correct=%s pair_cost=%.3f pair_cost_ok=%s | "
            "overall=%d/%d (%.1f%%) sub_dollar=%d/%d (%.1f%%)",
            w.window_id, w.asset, w.predicted_side, w.outcome,
            correct, w.pair_cost, w.pair_cost_ok,
            wins, total, accuracy,
            sub_wins, sub_total, sub_accuracy,
        )

        # Log full summary every 10 windows
        if len(self._windows) % 10 == 0:
            self._log_summary()

    def _log_summary(self) -> None:
        """Periodic summary of paper trading performance."""
        actionable = [x for x in self._windows if x.pair_cost_ok and x.outcome and x.predicted_side != "NONE"]
        sub_dollar = [x for x in actionable if x.pair_cost < 1.00]

        logger.info(
            "PAPER_SUMMARY total_windows=%d actionable=%d "
            "sub_dollar_windows=%d "
            "overall_accuracy=%.1f%% sub_dollar_accuracy=%.1f%%",
            len(self._windows),
            len(actionable),
            len(sub_dollar),
            (sum(1 for x in actionable if x.predicted_side == x.outcome) / max(len(actionable), 1) * 100),
            (sum(1 for x in sub_dollar if x.predicted_side == x.outcome) / max(len(sub_dollar), 1) * 100),
        )

    @property
    def ready_for_live(self) -> bool:
        """
        Are we confident enough to trust the strategy?
        Requires 100+ actionable windows with >55% accuracy
        on sub-$1.00 pair cost windows specifically.
        """
        actionable = [x for x in self._windows if x.pair_cost_ok and x.outcome and x.predicted_side != "NONE"]
        sub_dollar = [x for x in actionable if x.pair_cost < 1.00]

        if len(sub_dollar) < 100:
            return False

        wins = sum(1 for x in sub_dollar if x.predicted_side == x.outcome)
        accuracy = wins / len(sub_dollar)

        return accuracy >= 0.55
