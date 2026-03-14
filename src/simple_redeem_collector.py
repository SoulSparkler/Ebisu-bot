"""
Simple Redeem Collector
Periodically collects all unredeemed positions via Polymarket API
Simple replacement for complex system pending_markets
"""
import time
import threading
import requests
from typing import Dict, List, Optional


class SimpleRedeemCollector:
    """
    Simple collector for unredeemed positions on timer
    
    Uses Polymarket API for automatic detection of all
    redeemable positions and triggers redeem for each.
    
    ✅ Does NOT block main processes (trading)
    ✅ Runs in separate daemon thread
    ✅ Finds ALL positions (even after restart)
    """
    
    def __init__(self, wallet_address: str, config: dict, order_executor, trader_module,
                 multi_trader=None, notifier=None):
        """
        Args:
            wallet_address: Wallet address (0x...)
            config: Configuration with parameters
            order_executor: OrderExecutor instance for redeem
            trader_module: Trader module for getting token IDs
            multi_trader: MultiTrader instance for creating trade records (optional)
            notifier: TelegramNotifier for notifications (optional)
        """
        self.wallet = wallet_address
        self.config = config
        self.executor = order_executor
        self.trader = trader_module
        self.multi_trader = multi_trader
        self.notifier = notifier
        
        # Load parameters from config
        redeem_cfg = config.get('execution', {}).get('redeem', {})
        self.check_interval = redeem_cfg.get('check_interval_sec', 300)  # 5 min
        self.startup_delay = redeem_cfg.get('startup_check_delay_sec', 60)  # 1 min
        self.first_delay = redeem_cfg.get('first_check_delay_sec', 480)  # 8 min
        self.pause_between = redeem_cfg.get('pause_between_redeems_sec', 2)
        self.size_threshold = redeem_cfg.get('sizeThreshold', 0.1)
        
        # Rate limit protection
        self.api_max_retries = redeem_cfg.get('api_max_retries', 3)
        self.api_retry_delay = redeem_cfg.get('api_retry_delay_sec', 60)
        self.api_timeout = redeem_cfg.get('api_timeout_sec', 30)
        
        # State
        self.is_running = False
        self.last_check = 0
        self.stats = {
            'total_checks': 0,
            'total_redeemed': 0,
            'startup_check_done': False
        }
        
        print(f"[REDEEM COLLECTOR] Initialized:")
        print(f"  Wallet: {wallet_address[:10]}...{wallet_address[-8:]}")
        print(f"  Startup check: {self.startup_delay}s")
        print(f"  Regular checks: every {self.check_interval//60} minutes")
    
    def start(self):
        """Start in background thread (daemon - doesn't block shutdown)"""
        if self.is_running:
            print("[REDEEM COLLECTOR] Already running!")
            return
        
        self.is_running = True
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="SimpleRedeemCollector"
        )
        self.thread.start()
        print(f"[REDEEM COLLECTOR] ✅ Started (daemon thread)")
    
    def stop(self):
        """Stop background thread"""
        self.is_running = False
        if hasattr(self, 'thread') and self.thread:
            self.thread.join(timeout=5)
        print(f"[REDEEM COLLECTOR] Stopped")
    
    def _loop(self):
        """Background loop - runs in separate thread"""
        print(f"\n[REDEEM COLLECTOR] Background loop started")
        
        # 🔥 STARTUP CHECK: right after start (after startup_delay)
        # Goal: collect everything accumulated before script start
        print(f"[REDEEM COLLECTOR] ⏰ Startup check in {self.startup_delay}s...")
        print(f"[REDEEM COLLECTOR]    Will collect all unredeemed positions from before restart")
        time.sleep(self.startup_delay)
        
        print(f"\n[REDEEM COLLECTOR] 🚀 STARTUP CHECK")
        try:
            self._check_and_redeem_all(check_type="STARTUP")
            self.stats['startup_check_done'] = True
        except Exception as e:
            print(f"[REDEEM COLLECTOR] ⚠️ Startup check error: {e}")
            import traceback
            traceback.print_exc()
        
        # 🔥 FIRST REGULAR CHECK: after first_delay from startup
        # (for fresh markets that just closed)
        remaining_delay = max(0, self.first_delay - self.startup_delay)
        if remaining_delay > 0:
            print(f"\n[REDEEM COLLECTOR] ⏰ First regular check in {remaining_delay//60} minutes...")
            time.sleep(remaining_delay)
        
        # 🔥 REGULAR CHECKS: every check_interval
        while self.is_running:
            try:
                self._check_and_redeem_all(check_type="PERIODIC")
            except Exception as e:
                print(f"[REDEEM COLLECTOR] ⚠️ Periodic check error: {e}")
                import traceback
                traceback.print_exc()
            
            # Wait until next check
            if self.is_running:
                print(f"[REDEEM COLLECTOR] ⏰ Next check in {self.check_interval//60} minutes...")
                time.sleep(self.check_interval)
    
    def _check_and_redeem_all(self, check_type: str = "PERIODIC"):
        """
        Check API and redeem ALL

        Args:
            check_type: "STARTUP" (at startup) or "PERIODIC" (regular)
        """
        print(f"\n{'='*80}")
        if check_type == "STARTUP":
            print(f"[REDEEM COLLECTOR] 🚀 STARTUP CHECK")
            print(f"[REDEEM COLLECTOR] Collecting unredeemed from before restart...")
        else:
            print(f"[REDEEM COLLECTOR] 🔍 PERIODIC CHECK #{self.stats['total_checks'] + 1}")
        print(f"{'='*80}")

        self.stats['total_checks'] += 1
        self.last_check = time.time()

        _has_safety = hasattr(self.executor, 'safety')
        _dry_run_val = self.executor.safety.dry_run if _has_safety else 'NO_SAFETY_ATTR'
        print(f"[REDEEM_CHECK] dry_run={_dry_run_val} has_safety={_has_safety}")

        # DRY_RUN: resolve in-memory positions via price instead of oracle
        if _has_safety and _dry_run_val:
            self._dry_run_resolve_from_memory()
            return

        # STEP 1: Query API
        positions = self._fetch_redeemable_positions()
        
        if positions is None:
            print(f"[REDEEM COLLECTOR] ⚠️ API request failed, skipping this cycle")
            return
        
        print(f"[REDEEM COLLECTOR] Found {len(positions)} redeemable position(s)")
        
        if not positions:
            print(f"[REDEEM COLLECTOR] ✓ Nothing to redeem")
            if check_type == "STARTUP":
                print(f"[REDEEM COLLECTOR] ✓ All positions were already claimed before restart")
            return
        
        # Show summary
        total_size = sum(p.get('size', 0) for p in positions)
        total_value = sum(p.get('currentValue', 0) for p in positions)
        print(f"[REDEEM COLLECTOR] Summary:")
        print(f"  Total contracts: {total_size:.2f}")
        print(f"  Estimated value: ${total_value:.2f}")
        
        if check_type == "STARTUP":
            print(f"[REDEEM COLLECTOR] 💰 These positions accumulated before script restart")
        
        # STEP 2: Redeem each position (sequentially)
        print(f"\n[REDEEM COLLECTOR] Starting redeem process...")
        success_count = 0
        failed_count = 0
        
        for i, pos in enumerate(positions, 1):
            result = self._redeem_one(i, len(positions), pos)
            if result:
                success_count += 1
            else:
                failed_count += 1
            
            # Pause between redeems (from config)
            if i < len(positions):
                time.sleep(self.pause_between)
        
        print(f"\n[REDEEM COLLECTOR] ✅ Check completed")
        print(f"  Successful: {success_count}/{len(positions)}")
        print(f"  Failed: {failed_count}/{len(positions)}")
        print(f"  Total redeemed (session): {self.stats['total_redeemed']}")
        print(f"{'='*80}\n")
    
    def _dry_run_resolve_from_memory(self):
        """
        DRY_RUN only: scan all in-memory positions across multi_trader traders,
        fetch Polymarket prices, determine winner as the side with price closest
        to $1.00, and call close_market() directly — no oracle, no blockchain.

        Logs [DRY_RUN_RESOLVE] for every market processed.
        """
        if not self.multi_trader:
            print(f"[REDEEM COLLECTOR] [DRY_RUN] No multi_trader — skipping dry_run resolve")
            return

        from polymarket_api import get_market_outcome

        closed_count = 0
        skipped_count = 0

        for strategy_name, trader in self.multi_trader.traders.items():
            # snapshot keys so close_market() can safely mutate the dict
            open_slugs = list(trader.positions.keys())
            if not open_slugs:
                continue

            coin = getattr(trader, 'coin', None)
            if not coin:
                parts = strategy_name.rsplit('_', 1)
                coin = parts[-1] if len(parts) > 1 else None

            for slug in open_slugs:
                try:
                    api_result = get_market_outcome(slug)

                    prices = api_result.get('prices', [])
                    price_up = float(prices[0]) if prices and len(prices) >= 1 else 0.5
                    price_down = float(prices[1]) if prices and len(prices) >= 2 else 0.5

                    # Winner = side whose price is closest to $1.00
                    if abs(price_up - 1.0) <= abs(price_down - 1.0):
                        winner = 'UP'
                    else:
                        winner = 'DOWN'

                    print(
                        f"[DRY_RUN_RESOLVE] market={slug} winner={winner} "
                        f"price_up={price_up:.4f} price_down={price_down:.4f}"
                    )

                    result = self.multi_trader.close_market(
                        strategy_name=strategy_name,
                        market_slug=slug,
                        winner=winner,
                        btc_start=0.0,
                        btc_final=0.0
                    )

                    if result:
                        is_arb = result.get('up_invested', 0) > 0 and result.get('down_invested', 0) > 0
                        print(f"[WINDOW_CLOSE] market={slug} winner={winner} arb={is_arb} (dry_run)")
                        print(f"[REDEEM COLLECTOR] ✅ [DRY_RUN] Closed {slug}: PnL ${result.get('pnl', 0):+.2f}")
                        closed_count += 1

                        # Telegram notification
                        if self.notifier and coin:
                            try:
                                session_stats = self.multi_trader.get_session_stats(strategy_name, 0)
                                portfolio_stats = {}
                                for c in ['btc', 'eth', 'sol', 'xrp']:
                                    t = self.multi_trader.traders.get(f'late_v3_{c}')
                                    if t:
                                        perf = t.get_performance_stats()
                                        portfolio_stats[f'{c}_pnl'] = t.current_capital - t.starting_capital
                                        portfolio_stats[f'{c}_wr'] = perf['win_rate']
                                        portfolio_stats[f'{c}_markets_played'] = perf['total_trades']
                                    else:
                                        portfolio_stats[f'{c}_pnl'] = 0
                                        portfolio_stats[f'{c}_wr'] = 0
                                        portfolio_stats[f'{c}_markets_played'] = 0
                                portfolio_stats['total_pnl'] = sum(
                                    portfolio_stats.get(f'{c}_pnl', 0) for c in ['btc', 'eth', 'sol', 'xrp']
                                )
                                portfolio_stats['uptime'] = 0
                                self.notifier.send_market_closed(
                                    coin=coin,
                                    trade=result,
                                    session_stats=session_stats,
                                    portfolio_stats=portfolio_stats
                                )
                            except Exception as notify_err:
                                print(f"[REDEEM COLLECTOR]   ⚠️ [DRY_RUN] Notification failed: {notify_err}")
                    else:
                        # Position not in memory (already closed or never opened)
                        print(
                            f"[REDEEM COLLECTOR] ⚠️ [DRY_RUN] close_market returned None for {slug} "
                            f"(position may already be closed)"
                        )
                        skipped_count += 1

                except Exception as e:
                    print(f"[REDEEM COLLECTOR] ❌ [DRY_RUN] Error resolving {slug}: {e}")
                    import traceback
                    traceback.print_exc()

        print(
            f"[REDEEM COLLECTOR] [DRY_RUN] Resolve complete: "
            f"closed={closed_count} skipped={skipped_count}"
        )

    def _fetch_redeemable_positions(self) -> Optional[List[Dict]]:
        """
        Query Polymarket API to get redeemable positions
        With rate limit handling and retry logic
        """
        url = "https://data-api.polymarket.com/positions"
        params = {
            'user': self.wallet,
            'redeemable': 'true',
            'sizeThreshold': self.size_threshold,
            'limit': 500
        }
        
        print(f"[REDEEM COLLECTOR] Requesting Polymarket API...")
        print(f"  URL: {url}")
        print(f"  Filter: redeemable=true, sizeThreshold={self.size_threshold}")
        
        for attempt in range(1, self.api_max_retries + 1):
            try:
                response = requests.get(url, params=params, timeout=self.api_timeout)
                
                # ✅ SUCCESS
                if response.status_code == 200:
                    positions = response.json()
                    print(f"[REDEEM COLLECTOR] ✓ API response: {len(positions)} position(s)")
                    return positions
                
                # ⚠️ RATE LIMIT
                elif response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', self.api_retry_delay))
                    print(f"[REDEEM COLLECTOR] ⚠️ Rate limit hit (429)")
                    print(f"[REDEEM COLLECTOR]    Retry-After: {retry_after}s")
                    
                    if attempt < self.api_max_retries:
                        print(f"[REDEEM COLLECTOR]    Waiting {retry_after}s before retry...")
                        time.sleep(retry_after)
                        continue
                    else:
                        print(f"[REDEEM COLLECTOR] ❌ Rate limit persists after {self.api_max_retries} attempts")
                        return None
                
                # ❌ OTHER ERROR
                else:
                    print(f"[REDEEM COLLECTOR] ❌ API error: {response.status_code}")
                    print(f"  Response: {response.text[:200]}")
                    
                    if attempt < self.api_max_retries:
                        wait_time = 5 * attempt  # Exponential backoff
                        print(f"[REDEEM COLLECTOR]    Retry {attempt}/{self.api_max_retries} in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    
                    return None
            
            except requests.exceptions.Timeout:
                print(f"[REDEEM COLLECTOR] ⚠️ Request timeout (attempt {attempt})")
                if attempt < self.api_max_retries:
                    time.sleep(5)
                    continue
            
            except Exception as e:
                print(f"[REDEEM COLLECTOR] ❌ Request exception (attempt {attempt}): {e}")
                if attempt < self.api_max_retries:
                    time.sleep(5)
                    continue
        
        return None
    
    def _redeem_one(self, index: int, total: int, position: Dict) -> bool:
        """
        Redeem one position
        
        Returns:
            True if successful, False if failed
        """
        slug = position.get('slug')
        condition_id = position.get('conditionId')
        size = position.get('size', 0)
        neg_risk = position.get('negativeRisk', True)
        current_value = position.get('currentValue', 0)
        outcome = position.get('outcome', '')
        coin = None
        for c in ['btc', 'eth', 'sol', 'xrp']:
            if f'{c}-updown-' in (slug or ''):
                coin = c
                break
        strategy_name = f"late_v3_{coin}" if coin else None
        
        print(f"\n[REDEEM COLLECTOR] [{index}/{total}] Processing: {slug}")
        print(f"  Condition ID: {condition_id[:20]}...")
        print(f"  Size: {size:.2f} contracts")
        print(f"  Value: ${current_value:.2f}")
        print(f"  Outcome: {outcome}")
        
        try:
            # Get token IDs from cache
            token_ids = self.trader.get_token_ids(slug)
            
            if not token_ids:
                print(f"[REDEEM COLLECTOR]   No token IDs in cache, fetching metadata...")
                # Try to fetch metadata
                metadata = self.trader.get_market_metadata(slug)
                token_ids = self.trader.get_token_ids(slug)
            
            if not token_ids or not token_ids.get('UP') or not token_ids.get('DOWN'):
                print(f"[REDEEM COLLECTOR] ⚠️ No token IDs for {slug}, skipping")
                print(f"[REDEEM COLLECTOR]    This position cannot be redeemed without token IDs")
                return False
            
            print(f"[REDEEM COLLECTOR]   UP token: {token_ids['UP'][:10]}...")
            print(f"[REDEEM COLLECTOR]   DOWN token: {token_ids['DOWN'][:10]}...")
            print(f"[REDEEM COLLECTOR]   Calling redeem_position()...")

            position_in_memory = False
            if self.multi_trader and strategy_name:
                trader = self.multi_trader.traders.get(strategy_name)
                position_in_memory = bool(trader and slug in trader.positions)
            print(
                f"[WINDOW_FINALIZE] market={slug} coin={coin or 'unknown'} "
                f"position_in_memory={position_in_memory} stage=before_redeem"
            )
            
            # Call redeem via order_executor
            success, amount = self.executor.redeem_position(
                market_slug=slug,
                condition_id=condition_id,
                up_token_id=token_ids['UP'],
                down_token_id=token_ids['DOWN'],
                neg_risk=neg_risk
            )
            
            if success:
                print(f"[REDEEM COLLECTOR] ✅ Redeemed ${amount:.2f} USDC!")
                self.stats['total_redeemed'] += 1
                print(
                    f"[WINDOW_FINALIZE] market={slug} coin={coin or 'unknown'} "
                    f"position_in_memory={position_in_memory} stage=after_redeem amount={amount:.2f}"
                )
                
                # 🔥 FIX: Create trade record for dashboard (for all 4 coins)
                if self.multi_trader:
                    try:
                        from polymarket_api import get_market_outcome
                        
                        # Get real market outcome from Polymarket API
                        print(f"[REDEEM COLLECTOR]   Fetching market outcome from API...")
                        api_result = get_market_outcome(slug)
                        print(
                            f"[WINDOW_OUTCOME] market={slug} success={api_result.get('success')} "
                            f"resolved={api_result.get('resolved')} closed={api_result.get('closed')} "
                            f"winner={api_result.get('winner')}"
                        )
                        
                        if api_result.get("success"):
                            winner = api_result.get("winner") or 'UNKNOWN'
                            print(f"[REDEEM COLLECTOR]   Winner: {winner}")

                            if coin:
                                print(f"[REDEEM COLLECTOR]   Creating trade record for {strategy_name}...")

                                # Create trade record via multi_trader (works when position is in memory)
                                result = self.multi_trader.close_market(
                                    strategy_name=strategy_name,
                                    market_slug=slug,
                                    winner=winner,
                                    btc_start=0.0,
                                    btc_final=0.0
                                )

                                if result:
                                    is_arb = result.get('up_invested', 0) > 0 and result.get('down_invested', 0) > 0
                                    print(f"[WINDOW_CLOSE] market={slug} winner={winner} arb={is_arb}")
                                    print(f"[REDEEM COLLECTOR]   ✅ Trade record created!")
                                    print(f"[REDEEM COLLECTOR]      PnL: ${result['pnl']:+.2f}")
                                    print(f"[REDEEM COLLECTOR]      ROI: {result['roi_pct']:+.1f}%")

                                else:
                                    print(
                                        f"[WINDOW_FINALIZE] market={slug} coin={coin} "
                                        f"position_in_memory={position_in_memory} "
                                        f"stage=close_market_returned_none"
                                    )
                                    # ═══════════════════════════════════════════════════════════
                                    # 🔥 FALLBACK: Position not in memory (restart cleared it).
                                    # Reconstruct trade record from DB orders so every ARB
                                    # window that completes gets exactly one DB record.
                                    # ═══════════════════════════════════════════════════════════
                                    print(f"[REDEEM COLLECTOR]   ⚠️ Position not in memory — reconstructing from DB orders...")
                                    try:
                                        from db import load_orders_for_market, save_trade
                                        orders = load_orders_for_market(slug)

                                        up_invested = sum(o['total_spent_usd'] for o in orders if o['side'] == 'UP')
                                        down_invested = sum(o['total_spent_usd'] for o in orders if o['side'] == 'DOWN')
                                        up_shares = sum(o['contracts'] for o in orders if o['side'] == 'UP')
                                        down_shares = sum(o['contracts'] for o in orders if o['side'] == 'DOWN')
                                        total_cost = up_invested + down_invested
                                        total_entries = len(orders)
                                        payout = amount  # actual redemption amount from on-chain
                                        pnl = payout - total_cost
                                        roi_pct = (pnl / total_cost * 100) if total_cost > 0 else 0.0
                                        winner_shares = up_shares if winner == 'UP' else down_shares
                                        total_shares = up_shares + down_shares
                                        winner_ratio = (winner_shares / total_shares * 100) if total_shares > 0 else 50.0
                                        is_arb = up_invested > 0 and down_invested > 0

                                        print(f"[WINDOW_CLOSE] market={slug} winner={winner} arb={is_arb}")
                                        print(f"[REDEEM COLLECTOR]   Reconstructed: cost=${total_cost:.2f} payout=${payout:.2f} pnl=${pnl:+.2f} arb={is_arb}")

                                        fallback_trade = {
                                            'market_slug': slug,
                                            'winner': winner,
                                            'exit_type': 'natural_close',
                                            'exit_reason': 'natural_close_reconstructed',
                                            'pnl': pnl,
                                            'roi_pct': roi_pct,
                                            'total_cost': total_cost,
                                            'payout': payout,
                                            'winner_ratio': winner_ratio,
                                            'total_entries': total_entries,
                                            'up_entries': sum(1 for o in orders if o['side'] == 'UP'),
                                            'down_entries': sum(1 for o in orders if o['side'] == 'DOWN'),
                                            'up_invested': up_invested,
                                            'down_invested': down_invested,
                                            'up_shares': up_shares,
                                            'down_shares': down_shares,
                                            'duration': None,
                                            'close_timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                                        }

                                        save_trade(fallback_trade, strategy=strategy_name, coin=coin)
                                        result = fallback_trade
                                        print(f"[REDEEM COLLECTOR]   ✅ Fallback trade record saved (PnL ${pnl:+.2f})")

                                    except Exception as fb_err:
                                        print(f"[REDEEM COLLECTOR]   ❌ Fallback trade record failed: {fb_err}")
                                        import traceback
                                        traceback.print_exc()

                                # Send Telegram notification for any successfully recorded trade
                                if result and self.notifier:
                                    try:
                                        session_stats = self.multi_trader.get_session_stats(strategy_name, 0)

                                        portfolio_stats = {}
                                        for c in ['btc', 'eth', 'sol', 'xrp']:
                                            trader_name = f"late_v3_{c}"
                                            trader = self.multi_trader.traders.get(trader_name)
                                            if trader:
                                                perf = trader.get_performance_stats()
                                                portfolio_stats[f'{c}_pnl'] = trader.current_capital - trader.starting_capital
                                                portfolio_stats[f'{c}_wr'] = perf['win_rate']
                                                portfolio_stats[f'{c}_markets_played'] = perf['total_trades']
                                            else:
                                                portfolio_stats[f'{c}_pnl'] = 0
                                                portfolio_stats[f'{c}_wr'] = 0
                                                portfolio_stats[f'{c}_markets_played'] = 0

                                        portfolio_stats['total_pnl'] = sum(portfolio_stats.get(f'{c}_pnl', 0) for c in ['btc', 'eth', 'sol', 'xrp'])
                                        portfolio_stats['uptime'] = 0

                                        self.notifier.send_market_closed(
                                            coin=coin,
                                            trade=result,
                                            session_stats=session_stats,
                                            portfolio_stats=portfolio_stats
                                        )
                                        print(f"[REDEEM COLLECTOR]      ✅ Telegram notification sent")
                                    except Exception as notify_err:
                                        print(f"[REDEEM COLLECTOR]      ⚠️ Notification failed: {notify_err}")
                                        import traceback
                                        traceback.print_exc()
                            else:
                                print(f"[REDEEM COLLECTOR]   ⚠️ Could not determine coin from slug: {slug}")
                        else:
                            # API call itself failed — still try to write DB record
                            # using 'UNKNOWN' winner so the window is not silently lost.
                            print(f"[REDEEM COLLECTOR]   ⚠️ Market outcome API failed: {api_result.get('error', '?')}")
                            print(
                                f"[WINDOW_OUTCOME_FAIL] market={slug} coin={coin or 'unknown'} "
                                f"position_in_memory={position_in_memory} "
                                f"error={api_result.get('error', '?')}"
                            )
                            print(f"[REDEEM COLLECTOR]   Attempting DB write with winner=UNKNOWN...")
                            try:
                                coin_fallback = coin
                                if coin_fallback:
                                    strategy_name_fb = f"late_v3_{coin_fallback}"
                                    result_fb = self.multi_trader.close_market(
                                        strategy_name=strategy_name_fb,
                                        market_slug=slug,
                                        winner='UNKNOWN',
                                        btc_start=0.0,
                                        btc_final=0.0
                                    )
                                    if result_fb:
                                        print(f"[WINDOW_CLOSE] market={slug} winner=UNKNOWN arb=? (api_failed)")
                                        print(f"[REDEEM COLLECTOR]   ✅ DB record written with winner=UNKNOWN")
                                    else:
                                        from db import load_orders_for_market, save_trade
                                        orders = load_orders_for_market(slug)
                                        if orders:
                                            up_inv = sum(o['total_spent_usd'] for o in orders if o['side'] == 'UP')
                                            dn_inv = sum(o['total_spent_usd'] for o in orders if o['side'] == 'DOWN')
                                            tc = up_inv + dn_inv
                                            pnl = amount - tc
                                            is_arb = up_inv > 0 and dn_inv > 0
                                            fb = {
                                                'market_slug': slug,
                                                'winner': 'UNKNOWN',
                                                'exit_type': 'natural_close',
                                                'exit_reason': 'natural_close_reconstructed_api_fail',
                                                'pnl': pnl,
                                                'roi_pct': (pnl / tc * 100) if tc > 0 else 0.0,
                                                'total_cost': tc,
                                                'payout': amount,
                                                'winner_ratio': 50.0,
                                                'total_entries': len(orders),
                                                'up_entries': sum(1 for o in orders if o['side'] == 'UP'),
                                                'down_entries': sum(1 for o in orders if o['side'] == 'DOWN'),
                                                'up_invested': up_inv,
                                                'down_invested': dn_inv,
                                                'up_shares': sum(o['contracts'] for o in orders if o['side'] == 'UP'),
                                                'down_shares': sum(o['contracts'] for o in orders if o['side'] == 'DOWN'),
                                                'duration': None,
                                                'close_timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                                            }
                                            save_trade(fb, strategy=strategy_name_fb, coin=coin_fallback)
                                            print(f"[WINDOW_CLOSE] market={slug} winner=UNKNOWN arb={is_arb} (api_failed_fallback)")
                                            print(f"[REDEEM COLLECTOR]   ✅ Fallback DB record saved (pnl ${pnl:+.2f})")
                            except Exception as _unk_err:
                                print(f"[REDEEM COLLECTOR]   ❌ UNKNOWN-winner fallback failed: {_unk_err}")
                    
                    except Exception as trade_err:
                        print(f"[REDEEM COLLECTOR]   ⚠️ Failed to create trade record: {trade_err}")
                        import traceback
                        traceback.print_exc()
                
                # Reset market tracking in safety guard
                try:
                    if hasattr(self.trader, 'order_executor') and self.trader.order_executor:
                        self.trader.order_executor.safety.reset_market(slug)
                        print(f"[REDEEM COLLECTOR]   Market tracking reset")
                except Exception as reset_err:
                    print(f"[REDEEM COLLECTOR]   ⚠️ Failed to reset tracking: {reset_err}")
                
                return True
            else:
                print(
                    f"[WINDOW_FINALIZE] market={slug} coin={coin or 'unknown'} "
                    f"position_in_memory={position_in_memory} "
                    f"stage=redeem_failed close_market_called=False"
                )
                print(f"[REDEEM COLLECTOR] ⚠️ Redeem failed")
                print(f"[REDEEM COLLECTOR]    Reason: Oracle not resolved or no tokens")
                return False
        
        except Exception as e:
            print(f"[REDEEM COLLECTOR] ❌ Error processing {slug}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_stats(self) -> Dict:
        """Get collector statistics"""
        return {
            'total_checks': self.stats['total_checks'],
            'total_redeemed': self.stats['total_redeemed'],
            'startup_check_done': self.stats['startup_check_done'],
            'last_check_time': self.last_check,
            'is_running': self.is_running,
            'check_interval_min': self.check_interval // 60
        }
