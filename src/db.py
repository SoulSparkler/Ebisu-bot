"""
Database connection and initialization for Railway/PostgreSQL
Replaces all local file storage
"""
import os
import json
import time
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, Any

def get_connection():
    """Get PostgreSQL connection from DATABASE_URL env var"""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable not set")
    return psycopg2.connect(url)

def init_db():
    """Create all required tables if they don't exist"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP DEFAULT NOW(),
            market_slug TEXT,
            strategy TEXT,
            coin TEXT,
            winner TEXT,
            pnl FLOAT,
            roi_pct FLOAT,
            total_cost FLOAT,
            payout FLOAT,
            winner_ratio FLOAT,
            total_entries INT,
            up_invested FLOAT,
            down_invested FLOAT,
            up_shares FLOAT,
            down_shares FLOAT,
            duration FLOAT,
            exit_type TEXT DEFAULT 'natural',
            exit_reason TEXT,
            data JSONB
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP DEFAULT NOW(),
            market_slug TEXT,
            side TEXT,
            order_type TEXT,
            fak_attempt INT,
            contracts FLOAT,
            price FLOAT,
            size_usd FLOAT,
            total_spent_usd FLOAT,
            success BOOLEAN,
            order_id TEXT,
            error TEXT,
            dry_run BOOLEAN,
            elapsed_ms INT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_metadata (
            market_slug TEXT PRIMARY KEY,
            updated_at TIMESTAMP DEFAULT NOW(),
            up_token_id TEXT,
            down_token_id TEXT,
            condition_id TEXT,
            neg_risk BOOLEAN DEFAULT TRUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value JSONB,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("[DB] ✅ Tables initialized")

def save_trade(trade: Dict, strategy: str = "", coin: str = ""):
    """Insert a trade record into PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trades
            (market_slug, strategy, coin, winner, pnl, roi_pct, total_cost, payout,
             winner_ratio, total_entries, up_invested, down_invested, up_shares,
             down_shares, duration, exit_type, exit_reason, data)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            trade.get('market_slug'),
            strategy,
            coin,
            trade.get('winner'),
            trade.get('pnl'),
            trade.get('roi_pct'),
            trade.get('total_cost'),
            trade.get('payout'),
            trade.get('winner_ratio'),
            trade.get('total_entries'),
            trade.get('up_invested'),
            trade.get('down_invested'),
            trade.get('up_shares'),
            trade.get('down_shares'),
            trade.get('duration'),
            trade.get('exit_type', 'natural'),
            trade.get('exit_reason'),
            json.dumps(trade)
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Failed to save trade: {e}")

def save_order(log_entry: Dict):
    """Insert an order log entry into PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orders
            (market_slug, side, order_type, fak_attempt, contracts, price,
             size_usd, total_spent_usd, success, order_id, error, dry_run, elapsed_ms)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            log_entry.get('market_slug'),
            log_entry.get('side'),
            log_entry.get('order_type'),
            log_entry.get('fak_attempt'),
            log_entry.get('contracts'),
            log_entry.get('price'),
            log_entry.get('size_usd'),
            log_entry.get('total_spent_usd'),
            log_entry.get('success'),
            log_entry.get('order_id'),
            log_entry.get('error'),
            log_entry.get('dry_run'),
            log_entry.get('elapsed_ms')
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Failed to save order: {e}")

def save_market_metadata(market_slug: str, up_token_id: str, down_token_id: str,
                          condition_id: str, neg_risk: bool):
    """Upsert market metadata into PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO market_metadata
            (market_slug, up_token_id, down_token_id, condition_id, neg_risk, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (market_slug) DO UPDATE SET
                up_token_id = EXCLUDED.up_token_id,
                down_token_id = EXCLUDED.down_token_id,
                condition_id = EXCLUDED.condition_id,
                neg_risk = EXCLUDED.neg_risk,
                updated_at = NOW()
        """, (market_slug, up_token_id, down_token_id, condition_id, neg_risk))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] ⚠️ Failed to save metadata: {e}")

def load_all_market_metadata() -> Dict:
    """Load all market metadata from PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM market_metadata")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = {}
        for row in rows:
            slug = row['market_slug']
            result[slug] = {
                'token_ids': {
                    'UP': row['up_token_id'],
                    'DOWN': row['down_token_id']
                },
                'metadata': {
                    'condition_id': row['condition_id'],
                    'neg_risk': row['neg_risk']
                }
            }
        return result
    except Exception as e:
        print(f"[DB] ⚠️ Failed to load metadata: {e}")
        return {}

def load_trades_for_strategy(strategy_name: str) -> list:
    """Load previous trades for a strategy from PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT data FROM trades WHERE strategy = %s ORDER BY created_at ASC",
            (strategy_name,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row['data'] for row in rows if row['data']]
    except Exception as e:
        print(f"[DB] ⚠️ Failed to load trades: {e}")
        return []


def save_strategy_config(params: Dict):
    """Persist strategy parameters to bot_state table (key = 'strategy_config')"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bot_state (key, value, updated_at)
            VALUES ('strategy_config', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW()
        """, (json.dumps(params),))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] ✅ Strategy config saved: {params}")
    except Exception as e:
        print(f"[DB] ⚠️ Failed to save strategy config: {e}")


def load_strategy_config() -> Optional[Dict]:
    """Load strategy parameters from bot_state table (key = 'strategy_config')"""
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT value FROM bot_state WHERE key = 'strategy_config'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row['value']) if row else None
    except Exception as e:
        print(f"[DB] ⚠️ Failed to load strategy config: {e}")
        return None


def load_recent_trades(limit: int = 10) -> list:
    """Load the N most recent trades from PostgreSQL"""
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT created_at, coin, winner, pnl, roi_pct, exit_type FROM trades "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return list(rows)
    except Exception as e:
        print(f"[DB] ⚠️ Failed to load recent trades: {e}")
        return []
