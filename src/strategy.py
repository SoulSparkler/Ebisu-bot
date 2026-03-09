"""
Late Entry V3 Strategy - Full 15-minute window entry with time-based sizing
"""
import time
import logging
from typing import Optional, Dict

logger = logging.getLogger("ebisu.strategy")

# Hard ceiling: never enter when pair cost >= this value
PAIR_COST_CEILING = 0.99  # Must be < 1.00 to have any margin after fees


class LateEntryStrategy:
    """Late Entry V3 - enter across full 15-minute window, buy the favorite"""

    def __init__(self, config: Dict):
        # Read ALL params from config (NO HARDCODED VALUES!)
        strategy_cfg = config.get('strategy', {})

        # Late entry params
        self.entry_window = strategy_cfg.get('entry_window_sec', 900)
        self.entry_freq = strategy_cfg.get('entry_frequency_sec', 7)
        self.min_confidence = strategy_cfg.get('min_confidence', 0.30)
        self.max_spread = strategy_cfg.get('max_spread', 1.05)
        self.price_max = strategy_cfg.get('price_max', 0.93)
        
        # Sizing (contracts) - time-based FROM CONFIG!
        sizing_cfg = strategy_cfg.get('sizing', {})
        self.size_above_180 = sizing_cfg.get('above_180_sec', 8)
        self.size_above_120 = sizing_cfg.get('above_120_sec', 10)
        self.size_below_120 = sizing_cfg.get('below_120_sec', 12)
        
        # Max investment per market
        self.max_investment = strategy_cfg.get('max_investment_per_market', 300)
        
        # Flip-stop price (price reversal protection)
        exit_cfg = config.get('exit', {})
        flip_cfg = exit_cfg.get('flip_stop', {})
        self.flip_stop_price = flip_cfg.get('price_threshold', 0.48)
        
        # Track last entry per market
        self.last_entry = {}
        self.last_favorite = {}
    
    def _pair_cost_ok(self, up_ask: float, down_ask: float) -> bool:
        """
        Check if the current asks allow profitable entry.
        Returns True only if UP_ask + DOWN_ask < PAIR_COST_CEILING.
        """
        if not up_ask or not down_ask or up_ask <= 0 or down_ask <= 0:
            return False  # Can't assess — don't trade

        simple_pair_cost = up_ask + down_ask

        if simple_pair_cost >= PAIR_COST_CEILING:
            logger.debug(
                "PAIR_COST_BLOCKED simple_pair=%.4f ceiling=%.4f "
                "ask_up=%.3f ask_down=%.3f",
                simple_pair_cost, PAIR_COST_CEILING,
                up_ask, down_ask,
            )
            return False

        return True

    def _validate_effective_pair_cost(self, side: str, qty: float, price: float,
                                      position_stats: Optional[Dict]) -> bool:
        """
        Simulate what happens if we accept this fill.
        Reject if the average entry price (total_invested / total_contracts) >= PAIR_COST_CEILING.

        Prevents over-averaging into a position that can never be profitable.
        """
        if not position_stats:
            return True  # No existing position — allow entry

        up_invested = position_stats.get('up_invested', 0.0)
        down_invested = position_stats.get('down_invested', 0.0)
        up_shares = position_stats.get('up_shares', 0.0)
        down_shares = position_stats.get('down_shares', 0.0)

        # Simulate new inventory
        if side == 'UP':
            sim_invested = up_invested + qty * price
            sim_shares = up_shares + qty
        else:
            sim_invested = down_invested + qty * price
            sim_shares = down_shares + qty

        if sim_shares > 0:
            effective_avg_price = sim_invested / sim_shares
            if effective_avg_price >= PAIR_COST_CEILING:
                logger.warning(
                    "REJECT_FILL effective_avg_price=%.4f >= %.2f "
                    "side=%s qty=%.1f price=%.3f",
                    effective_avg_price, PAIR_COST_CEILING, side, qty, price,
                )
                return False

        return True

    def _should_enter(self, market_state: Dict, position_stats: Optional[Dict]) -> Optional[str]:
        """
        Decide whether to enter and which side to buy.

        Entry trigger: pair cost is below ceiling (edge exists).
        Direction: the cheaper side has more upside margin.

        Returns: "UP", "DOWN", or None
        """
        up_ask = market_state.get('up_ask', 0)
        down_ask = market_state.get('down_ask', 0)

        # Step 1: Is there a viable opportunity?
        if not self._pair_cost_ok(up_ask, down_ask):
            return None  # No entry — no edge

        # Step 2: Buy the cheaper side (more room to appreciate)
        if up_ask <= down_ask:
            default_side = "UP"
        else:
            default_side = "DOWN"

        # Step 3: Validate average entry cost won't exceed ceiling
        fav_price = up_ask if default_side == "UP" else down_ask
        if not self._validate_effective_pair_cost(default_side, 1.0, fav_price, position_stats):
            return None  # Averaging up would push cost too high

        return default_side

    def should_enter(self, state: Dict, position: Optional[Dict] = None) -> Optional[Dict]:
        """
        Check if should enter (Late Entry V3 logic)
        
        Args:
            state: Market state with keys:
                - market_slug: str
                - seconds_till_end: int
                - up_ask: float
                - down_ask: float
            position: Optional position stats
        
        Returns:
            Signal dict or None
        """
        market = state['market_slug']
        time_left = state['seconds_till_end']
        up_ask = state['up_ask']
        down_ask = state['down_ask']
        
        # TIME: full window, but kill switch in final 30 s
        if time_left > self.entry_window or time_left < 30:
            return None
        
        # FREQUENCY
        now = time.time()
        if market in self.last_entry and now - self.last_entry[market] < self.entry_freq:
            return None
        
        # Hard pair-cost gate — no orders if pair cost is unprofitable
        if not self._pair_cost_ok(up_ask, down_ask):
            return None  # No maker orders when pair cost is too high

        # CONFIDENCE (min spread between sides indicates market has picked a side)
        confidence = abs(up_ask - down_ask)
        if confidence < self.min_confidence:
            return None

        # Determine entry side via pair-cost-driven logic
        side = self._should_enter(state, position)
        if side is None:
            return None

        fav_price = up_ask if side == 'UP' else down_ask

        # PRICE MAX
        if fav_price > self.price_max:
            return None

        # INVESTMENT LIMIT
        if position:
            total_cost = position.get('total_cost', 0)
            if total_cost >= self.max_investment:
                return None

        # RISK CHECKS - stop-loss removed, only flip-stop via main.py
        # Flip-stop logic in main.py (check: our_price <= strategy.flip_stop_price)

        # ENTRY
        size = self.size_above_180 if time_left > 180 else (self.size_above_120 if time_left > 120 else self.size_below_120)

        self.last_entry[market] = now
        self.last_favorite[market] = side

        return {
            'favored': {
                'side': side,
                'price': fav_price,
                'contracts': size,
            },
            'hedge': {
                'side': 'DOWN' if side == 'UP' else 'UP',
                'price': down_ask if side == 'UP' else up_ask,
                'contracts': 0,
            },
            'confidence': confidence,
            'is_recovery': False,
            'entry_reason': f'late_entry_{time_left}s',
            'winner_ratio': 0.0
        }
    
    def get_stats(self) -> Dict:
        """Get strategy statistics (for dashboard compatibility)"""
        return {
            'generated': 0,
            'skipped': 0,
            'total': 0,
            'skip_breakdown': {},
            'gen_pct': 0,
            'skip_pct': 0,
            'wr_recoveries': 0
        }
    
    def reset_market(self, market_slug: str):
        """Reset tracking for a market"""
        if market_slug in self.last_entry:
            del self.last_entry[market_slug]
        if market_slug in self.last_favorite:
            del self.last_favorite[market_slug]
