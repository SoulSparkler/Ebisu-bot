"""
Telegram Notification System for Trading Bot
Sends detailed market updates after each trade - NO SPAM!
"""
import os
import time
import requests
from datetime import timedelta
from threading import Thread, Lock
from queue import Queue, Empty
from typing import Dict, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
from pathlib import Path as _Path
_env_path = _Path(__file__).parent.parent / ".env"
load_dotenv(str(_env_path))


def _first_env(*names: str) -> str:
    """Return the first non-empty environment variable from a list of names."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


class TelegramNotifier:
    """
    Non-blocking Telegram notification sender with rate limiting
    
    Features:
    - Background thread for sending
    - Rate limiting (2 msg/sec max to avoid spam)
    - Graceful error handling (never crashes main process)
    - Queue-based with drop counter
    - ONLY market close/skip notifications (no startup spam)
    """
    
    def __init__(self, bot_token: str = None, chat_id: str = None, rate_limit: float = 2.0, event_callback=None):
        """
        Initialize Telegram notifier
        
        Args:
            bot_token: Telegram bot token (from @BotFather)
            chat_id: Telegram chat ID (your user ID)
            rate_limit: Max messages per second (default: 2)
            event_callback: Callback function(message, event_type) for logging events
        """
        # Get from env if not provided. Legacy aliases are accepted so
        # older deployments can still answer /start and show the missing chat ID.
        self.bot_token = (bot_token or _first_env(
            "TELEGRAM_BOT_TOKEN",
            "bottoken",
            "BOT_TOKEN",
        )).strip()
        self.chat_id = (chat_id or _first_env(
            "TELEGRAM_CHAT_ID",
            "chatid",
            "CHAT_ID",
        )).strip()
        self.event_callback = event_callback
        self.has_bot_token = bool(self.bot_token)
        
        # Configuration
        self.rate_limit = rate_limit
        self.min_interval = 1.0 / rate_limit
        self.last_send_time = 0.0
        
        # Queue for messages
        self.queue = Queue(maxsize=30)  # Small queue - only market notifications
        self.running = True
        self.enabled = bool(self.has_bot_token and self.chat_id)
        self.thread = None
        
        # Statistics
        self.dropped_count = 0
        self.sent_count = 0
        self.error_count = 0
        self.last_error_time = 0.0
        
        # Session tracking
        self.session_start_time = time.time()
        
        # Start worker thread if enabled
        if self.enabled:
            self.thread = Thread(target=self._worker, daemon=True, name="TelegramNotifier")
            self.thread.start()
            if self.event_callback:
                self.event_callback("Notifier started", 'telegram')
        else:
            if self.event_callback:
                if self.has_bot_token:
                    self.event_callback("Telegram setup mode (missing TELEGRAM_CHAT_ID)", 'info')
                else:
                    self.event_callback("Telegram disabled (missing TELEGRAM_BOT_TOKEN)", 'info')

    def _build_setup_message(self, chat_id: str) -> str:
        """Explain the missing environment variable and show the user's chat ID."""
        return (
            "<b>Telegram setup required</b>\n\n"
            "The bot token is configured, but <code>TELEGRAM_CHAT_ID</code> is missing.\n\n"
            f"Your current chat ID is:\n<code>{chat_id}</code>\n\n"
            "Add this environment variable and restart the bot:\n"
            f"<code>TELEGRAM_CHAT_ID={chat_id}</code>\n\n"
            "After the restart finishes, send /start again."
        )
    
    def _worker(self):
        """Background worker that sends messages from queue"""
        while self.running:
            try:
                # Get message with timeout
                msg = self.queue.get(timeout=1.0)
                if msg is None:
                    continue
                
                # Rate limiting
                now = time.time()
                elapsed = now - self.last_send_time
                if elapsed < self.min_interval:
                    time.sleep(self.min_interval - elapsed)
                
                # Send message
                if self._send(msg):
                    self.sent_count += 1
                else:
                    self.error_count += 1
                
                self.last_send_time = time.time()
                
            except Empty:
                continue
            except Exception:
                # Silent error handling
                self.error_count += 1
                pass
    
    def _send(self, message: str) -> bool:
        """
        Send message to Telegram (with timeout)
        
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            response = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }, timeout=5.0)
            
            return response.status_code == 200
            
        except Exception as e:
            # Only log error once per minute to avoid spam
            now = time.time()
            if now - self.last_error_time > 60:
                if self.event_callback:
                    self.event_callback(f"Send error: {str(e)[:40]}", 'error')
                self.last_error_time = now
            return False
    
    def notify(self, message: str):
        """
        Queue a notification (non-blocking)
        
        Args:
            message: Message text (HTML formatting supported)
        """
        if not self.enabled:
            return
        
        try:
            self.queue.put_nowait(message)
        except:
            self.dropped_count += 1
    
    def send_market_closed(self, coin: str, trade: Dict, session_stats: Dict, portfolio_stats: Dict = None):
        """
        Send compact notification when a market closes with trade
        
        Args:
            coin: Coin name ('btc', 'eth', 'sol', 'xrp')
            trade: Trade result dict from trader
            session_stats: Session statistics for this coin
            portfolio_stats: Optional portfolio stats for all coins
        """
        # Extract trade data
        market_slug = trade.get('market_slug', 'unknown')
        pnl = trade.get('pnl', 0)
        roi_pct = trade.get('roi_pct', 0)
        winner = trade.get('winner', '?')
        
        # Determine result emoji
        if pnl > 0:
            result_emoji = "🟢"
            result_text = "WIN"
        else:
            result_emoji = "🔴"
            result_text = "LOSS"
        
        # Format PnL
        pnl_str = f"${pnl:+.2f}"
        roi_str = f"{roi_pct:+.1f}%"
        
        # Market ID (short)
        market_id = market_slug.split('-')[-1][:10] if '-' in market_slug else market_slug[-10:]
        
        # Build compact message
        message = f"""<b>{coin.upper()}</b> {result_emoji} {result_text}
━━━━━━━━━━━━━━━
Market: ...{market_id}
PnL: {pnl_str} ({roi_str})
Winner: {winner}"""
        
        # Session summary (compact)
        total_pnl = session_stats.get('total_pnl', 0)
        win_rate = session_stats.get('win_rate', 0)
        
        message += f"\nTotal: ${total_pnl:+.2f} | WR: {win_rate:.0f}%"
        
        # Portfolio stats (all coins)
        if portfolio_stats:
            message += "\n\n━━━━━━━━━━━━━━━\n<b>🏦 PORTFOLIO</b>"
            
            coins = ['btc', 'eth', 'sol', 'xrp']
            for c in coins:
                c_pnl = portfolio_stats.get(f'{c}_pnl', 0)
                c_wr = portfolio_stats.get(f'{c}_wr', 0)
                c_markets = portfolio_stats.get(f'{c}_markets_played', 0)
                
                # Emoji for PnL
                pnl_emoji = "🟢" if c_pnl > 0 else "🔴" if c_pnl < 0 else "⚪"
                
                message += f"\n{c.upper()}: {pnl_emoji} ${c_pnl:+.2f} ({c_wr:.0f}% WR, {c_markets}m)"
            
            # Total
            total_portfolio_pnl = portfolio_stats.get('total_pnl', 0)
            total_emoji = "🟢" if total_portfolio_pnl > 0 else "🔴" if total_portfolio_pnl < 0 else "⚪"
            uptime = portfolio_stats.get('uptime', 0)
            uptime_str = self._format_uptime(uptime)
            
            message += f"\n<b>Total: {total_emoji} ${total_portfolio_pnl:+.2f}</b> | {uptime_str}"
        
        # Send notification
        self.notify(message)
    
    def send_market_skipped(self, coin: str, market_slug: str, skip_reason: str, session_stats: Dict, portfolio_stats: Dict = None):
        """
        Send minimal notification when a market is skipped (no trades)
        
        Args:
            coin: Coin name ('btc', 'eth', 'sol', 'xrp')
            market_slug: Market identifier (UNUSED)
            skip_reason: Reason for skipping (UNUSED)
            session_stats: Session statistics (UNUSED)
            portfolio_stats: Portfolio stats (UNUSED)
        """
        # Ultra-minimal message: just coin + skipped
        message = f"<b>{coin.upper()}</b> ⏭️ SKIPPED"
        
        # Send notification
        self.notify(message)
    
    def send_photo(self, photo_path: str, caption: str = ""):
        """
        Send photo to Telegram
        
        Args:
            photo_path: Path to image file
            caption: Optional caption (HTML supported)
        
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
            
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                data = {
                    'chat_id': self.chat_id,
                    'caption': caption,
                    'parse_mode': 'HTML'
                }
                
                response = requests.post(url, data=data, files=files, timeout=30)
                
                if response.status_code == 200:
                    self.sent_count += 1
                    return True
                else:
                    self.error_count += 1
                    if self.event_callback:
                        self.event_callback(f"Photo send failed: {response.status_code}", 'error')
                    return False
                    
        except Exception as e:
            self.error_count += 1
            if self.event_callback:
                self.event_callback(f"Photo error: {str(e)[:40]}", 'error')
            return False
    
    def _format_uptime(self, seconds: float) -> str:
        """Format uptime in human-readable format"""
        delta = timedelta(seconds=int(seconds))
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        if delta.days > 0:
            return f"{delta.days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    
    def get_stats(self) -> Dict:
        """Get notifier statistics"""
        return {
            'enabled': self.enabled,
            'sent_count': self.sent_count,
            'dropped_count': self.dropped_count,
            'error_count': self.error_count,
            'queue_size': self.queue.qsize()
        }
    
    def stop(self):
        """Stop the notifier"""
        self.running = False
        if self.enabled and self.event_callback:
            self.event_callback(f"Stopped (sent:{self.sent_count} drop:{self.dropped_count} err:{self.error_count})", 'telegram')
    
    def start_command_listener(self, on_chart_command, on_balance_command=None,
                               on_positions_command=None, on_redeem_command=None, on_redeem_callbacks=None,
                               on_shutdown_command=None, on_shutdown_callbacks=None,
                               on_setparam_command=None, on_showparams_command=None,
                               on_showlogs_command=None):
        """
        Start background thread to listen for Telegram commands
        THREAD-SAFE: Runs in separate daemon thread with full error handling
        
        Args:
            on_chart_command: Callback function to call when /chart or /pnl command received
            on_balance_command: Callback function to call when /balance command received
            on_positions_command: Callback function to call when /t or /positions command received
            on_redeem_command: Callback function to call when /r or /redeem command received
            on_redeem_callbacks: Dict with callback functions for redeem buttons
                                 {'redeem_all': func, 'redeem_position': func, 'redeem_cancel': func}
            on_shutdown_command: Callback function to call when /off or /stop command received
            on_shutdown_callbacks: Dict with callback functions for shutdown buttons
                                   {'shutdown_confirm': func, 'shutdown_cancel': func}
        """
        if not self.has_bot_token:
            if self.event_callback:
                self.event_callback("Command listener disabled (missing TELEGRAM_BOT_TOKEN)", 'info')
            return None
        
        def listener_thread():
            last_update_id = 0
            consecutive_errors = 0
            max_consecutive_errors = 10
            
            if self.event_callback:
                if self.chat_id:
                    self.event_callback("Command listener started", 'telegram')
                else:
                    self.event_callback("Command listener started in setup mode", 'info')
            
            while self.running:
                try:
                    # Long polling for updates (30s timeout)
                    url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                    params = {
                        'offset': last_update_id + 1,
                        'timeout': 30,  # Long polling - wait up to 30s for updates
                        'allowed_updates': ['message', 'callback_query']  # Messages and button clicks
                    }
                    
                    response = requests.get(url, params=params, timeout=35)
                    
                    # Reset error counter on successful connection
                    consecutive_errors = 0
                    
                    if response.status_code != 200:
                        if self.event_callback:
                            self.event_callback(f"API status {response.status_code}", 'error')
                        time.sleep(5)
                        continue
                    
                    data = response.json()
                    
                    if not data.get('ok'):
                        if self.event_callback:
                            self.event_callback(f"API error: {data.get('description', 'unknown')[:30]}", 'error')
                        time.sleep(5)
                        continue
                    
                    updates = data.get('result', [])
                    
                    # Process all updates
                    for update in updates:
                        try:
                            last_update_id = update['update_id']
                            
                            # Handle callback queries (button clicks)
                            if 'callback_query' in update and on_redeem_callbacks:
                                callback_query = update['callback_query']
                                callback_data = callback_query.get('data', '')
                                callback_id = callback_query['id']
                                message_id = callback_query['message']['message_id']
                                from_chat_id = str(callback_query['from']['id'])
                                
                                # SECURITY: Only respond to callbacks from our chat_id
                                if from_chat_id != self.chat_id:
                                    continue
                                
                                print(f"[TELEGRAM] Callback received: {callback_data}")
                                
                                try:
                                    # Redeem callbacks
                                    if callback_data == "redeem_all":
                                        on_redeem_callbacks['redeem_all'](callback_id, message_id)
                                    
                                    elif callback_data.startswith("redeem_pos_"):
                                        index = int(callback_data.split("_")[-1])
                                        on_redeem_callbacks['redeem_position'](callback_id, message_id, index)
                                    
                                    elif callback_data == "redeem_cancel":
                                        on_redeem_callbacks['redeem_cancel'](callback_id, message_id)
                                    
                                    # Shutdown callbacks
                                    elif on_shutdown_callbacks:
                                        if callback_data.startswith("shutdown_confirm_"):
                                            pid = callback_data.split("_")[-1]
                                            on_shutdown_callbacks['shutdown_confirm'](callback_id, message_id, pid)
                                        
                                        elif callback_data == "shutdown_cancel":
                                            on_shutdown_callbacks['shutdown_cancel'](callback_id, message_id)
                                
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    print(f"[TELEGRAM] Callback error: {error_msg}")
                                    self.answer_callback_query(callback_id, f"Error: {error_msg[:50]}", show_alert=True)
                                
                                continue
                            
                            # Handle regular messages
                            if 'message' not in update:
                                continue
                            
                            message = update['message']
                            
                            if 'text' not in message:
                                continue
                            
                            text = message['text'].strip().lower()
                            from_chat_id = str(message['chat']['id'])
                            from_user = message.get('from', {}).get('username', 'unknown')
                            
                            if not self.chat_id:
                                if text.startswith('/'):
                                    if self.event_callback:
                                        self.event_callback(
                                            f"Telegram setup request from {from_user} ({from_chat_id})",
                                            'telegram'
                                        )
                                    self.send_message(
                                        self._build_setup_message(from_chat_id),
                                        chat_id=from_chat_id
                                    )
                                continue

                            # SECURITY: Only respond to messages from our chat_id
                            if from_chat_id != self.chat_id:
                                if self.event_callback:
                                    self.event_callback(f"Unauthorized msg from {from_user}", 'error')
                                continue
                            
                            # Handle commands
                            if text in ['/chart', '/pnl', '/график']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    # Call the callback (should be thread-safe!)
                                    on_chart_command()
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    if self.event_callback:
                                        self.event_callback(f"Chart cmd error: {error_msg[:40]}", 'error')
                                    self.send_message(f"❌ Error generating chart:\n<code>{error_msg}</code>")
                            
                            elif text in ['/balance', '/b']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    if on_balance_command:
                                        on_balance_command()
                                    else:
                                        self.send_message("❌ Balance command not available")
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    if self.event_callback:
                                        self.event_callback(f"Balance cmd error: {error_msg[:40]}", 'error')
                                    self.send_message(f"❌ Error getting balance:\n<code>{error_msg}</code>")
                            
                            elif text in ['/t', '/positions']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    if on_positions_command:
                                        on_positions_command()
                                    else:
                                        self.send_message("❌ Positions command not available")
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    if self.event_callback:
                                        self.event_callback(f"Positions cmd error: {error_msg[:40]}", 'error')
                                    self.send_message(f"❌ Error getting positions:\n<code>{error_msg}</code>")
                            
                            elif text in ['/r', '/redeem']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    if on_redeem_command:
                                        on_redeem_command()
                                    else:
                                        self.send_message("❌ Redeem command not available")
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    if self.event_callback:
                                        self.event_callback(f"Redeem cmd error: {error_msg[:40]}", 'error')
                                    self.send_message(f"❌ Error getting redeemable positions:\n<code>{error_msg}</code>")
                            
                            elif text in ['/off', '/shutdown', '/stop']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    if on_shutdown_command:
                                        on_shutdown_command()
                                    else:
                                        self.send_message("❌ Shutdown command not available")
                                except Exception as e:
                                    error_msg = str(e)[:200]
                                    if self.event_callback:
                                        self.event_callback(f"Shutdown cmd error: {error_msg[:40]}", 'error')
                                    self.send_message(f"❌ Error executing shutdown:\n<code>{error_msg}</code>")
                            
                            elif text.startswith('/setparam '):
                                if self.event_callback:
                                    self.event_callback(f"Received {text[:40]}", 'telegram')
                                try:
                                    parts = text.split()
                                    if len(parts) != 3:
                                        self.send_message("❌ Usage: /setparam <key> <value>\nExample: /setparam pair_cost_ceiling 0.97")
                                    elif on_setparam_command:
                                        on_setparam_command(parts[1], parts[2])
                                    else:
                                        self.send_message("❌ setparam not available")
                                except Exception as e:
                                    self.send_message(f"❌ Error: <code>{str(e)[:100]}</code>")

                            elif text in ['/showparams', '/params']:
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    if on_showparams_command:
                                        on_showparams_command()
                                    else:
                                        self.send_message("❌ showparams not available")
                                except Exception as e:
                                    self.send_message(f"❌ Error: <code>{str(e)[:100]}</code>")

                            elif text.startswith('/showlogs') or text.startswith('/logs'):
                                if self.event_callback:
                                    self.event_callback(f"Received {text}", 'telegram')
                                try:
                                    parts = text.split()
                                    n = int(parts[1]) if len(parts) > 1 else 10
                                    n = max(1, min(n, 50))  # clamp 1-50
                                    if on_showlogs_command:
                                        on_showlogs_command(n)
                                    else:
                                        self.send_message("❌ showlogs not available")
                                except Exception as e:
                                    self.send_message(f"❌ Error: <code>{str(e)[:100]}</code>")

                            elif text in ['/help', '/start']:
                                help_text = """<b>📊 Trading Bot Commands:</b>

/chart or /pnl - Generate current PnL chart
/b or /balance - Show wallet balance (USDC + POL)
/t or /positions - Show active positions
/r or /redeem - Redeem completed markets (interactive)
/off or /stop - Emergency shutdown (with confirmation)

<b>⚙️ Strategy Tuning:</b>
/setparam &lt;key&gt; &lt;value&gt; - Update a strategy parameter
/showparams - Show current strategy parameters
/showlogs [n] - Show last N trades (default 10)

<b>Available keys:</b> pair_cost_ceiling, entry_window_sec, entry_frequency_sec, min_confidence, price_max, max_investment_per_market, sizing_above_180, sizing_above_120, sizing_below_120, flip_stop_price

<b>💡 Tip:</b> Charts are sent automatically every 10 markets.
<b>🔒 Security:</b> Commands only work from authorized chat ID."""
                                self.send_message(help_text)
                            
                            elif text.startswith('/'):
                                # Unknown command
                                self.send_message(f"❌ Unknown command: {text}\nSend /help for available commands")
                        
                        except Exception as e:
                            # Error processing individual update - log and continue
                            if self.event_callback:
                                self.event_callback(f"Update error: {str(e)[:40]}", 'error')
                            continue
                        
                except requests.exceptions.Timeout:
                    # Timeout is NORMAL for long polling - just continue
                    continue
                
                except requests.exceptions.ConnectionError as e:
                    consecutive_errors += 1
                    if self.event_callback and consecutive_errors % 5 == 1:  # Log every 5th error
                        self.event_callback(f"Connection error ({consecutive_errors})", 'error')
                    
                    if consecutive_errors >= max_consecutive_errors:
                        if self.event_callback:
                            self.event_callback("Too many errors, stopping listener", 'error')
                        break
                    
                    time.sleep(min(10 * consecutive_errors, 60))  # Exponential backoff
                    
                except Exception as e:
                    consecutive_errors += 1
                    if self.event_callback and consecutive_errors % 5 == 1:  # Log every 5th error
                        self.event_callback(f"Listener error ({consecutive_errors})", 'error')
                    
                    if consecutive_errors >= max_consecutive_errors:
                        if self.event_callback:
                            self.event_callback("Too many errors, stopping listener", 'error')
                        break
                    
                    time.sleep(10)
            
            if self.event_callback:
                self.event_callback("Command listener stopped", 'telegram')
        
        # Start listener in background daemon thread
        # Daemon=True means thread will be killed when main program exits
        thread = Thread(target=listener_thread, daemon=True, name="TelegramCommandListener")
        thread.start()
        
        if self.event_callback:
            self.event_callback("Command listener thread started", 'telegram')
        return thread
    
    def send_message_with_buttons(self, text: str, buttons: list) -> int:
        """
        Send message with Inline Keyboard buttons
        
        Args:
            text: Message text (supports HTML)
            buttons: List of buttons [[{text, callback_data}, ...], ...]
        
        Returns:
            message_id if successful, None on error
        """
        if not self.enabled:
            return None
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": buttons
                }
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                message_id = data['result']['message_id']
                print(f"[TELEGRAM] ✅ Message with buttons sent (ID: {message_id})")
                return message_id
            else:
                print(f"[TELEGRAM] ⚠️ Failed to send message with buttons: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"[TELEGRAM] ⚠️ Error sending message with buttons: {e}")
            return None
    
    def edit_message_text(self, message_id: int, text: str, buttons: list = None) -> bool:
        """
        Edit text of existing message
        
        Args:
            message_id: Message ID to edit
            text: New text (supports HTML)
            buttons: New buttons (optional)
        
        Returns:
            True if successful
        """
        if not self.enabled:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
            payload = {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML"
            }
            
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                print(f"[TELEGRAM] ✅ Message edited (ID: {message_id})")
                return True
            else:
                print(f"[TELEGRAM] ⚠️ Failed to edit message: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"[TELEGRAM] ⚠️ Error editing message: {e}")
            return False
    
    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> bool:
        """
        Answer callback query (show popup notification)
        
        Args:
            callback_query_id: ID callback query
            text: Notification text
            show_alert: Show as alert (True) or toast (False)
        
        Returns:
            True if successful
        """
        if not self.enabled:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
            payload = {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
                
        except Exception as e:
            print(f"[TELEGRAM] ⚠️ Error answering callback: {e}")
            return False
    
    def send_message(self, message: str, chat_id: Optional[str] = None):
        """
        Send plain text message to Telegram (for command responses)
        Sends directly (not queued) since this is for immediate command responses
        
        Args:
            message: Text message to send
        """
        target_chat_id = str(chat_id or self.chat_id or "").strip()
        if not self.has_bot_token or not target_chat_id:
            return False
        
        # Send directly for immediate response (not queued)
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': target_chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            response = requests.post(url, json=data, timeout=10)
            
            if response.status_code == 200:
                self.sent_count += 1
                return True
            else:
                self.error_count += 1
                if self.event_callback:
                    self.event_callback(f"Send msg failed: {response.status_code}", 'error')
                return False
            
        except Exception as e:
            self.error_count += 1
            if self.event_callback:
                self.event_callback(f"Send msg error: {str(e)[:40]}", 'error')
            return False


# Global notifier instance (singleton)
_notifier = None
_notifier_lock = Lock()


def get_notifier() -> TelegramNotifier:
    """Get or create the global Telegram notifier (singleton)"""
    global _notifier
    if _notifier is None:
        with _notifier_lock:
            if _notifier is None:  # Double-check
                _notifier = TelegramNotifier()
    return _notifier



