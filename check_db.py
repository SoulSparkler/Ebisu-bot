import psycopg2
from datetime import datetime
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

cutoff = datetime(2026, 3, 13, 0, 0, 0)

print("=== ORDERS NA CUTOFF ===")
cur.execute("""
    SELECT DATE_TRUNC('hour', created_at) as uur,
           UPPER(SPLIT_PART(market_slug, '-', 1)) as coin,
           COUNT(*) as n
    FROM orders
    WHERE created_at >= %s
    GROUP BY uur, coin
    ORDER BY uur DESC
    LIMIT 20
""", (cutoff,))
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]}: {r[2]} orders")

print("\n=== TRADES MET UNKNOWN WINNER ===")
cur.execute("""
    SELECT COUNT(*) FROM trades
    WHERE created_at >= %s AND winner = 'UNKNOWN'
""", (cutoff,))
r = cur.fetchone()
print(f"  UNKNOWN winners: {r[0]}")

print("\n=== ALLE TRADES INCLUSIEF NULL PNL ===")
cur.execute("""
    SELECT 
        SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) as met_pnl,
        SUM(CASE WHEN pnl IS NULL THEN 1 ELSE 0 END) as zonder_pnl,
        COUNT(*) as totaal
    FROM trades WHERE created_at >= %s
""", (cutoff,))
r = cur.fetchone()
print(f"  Met PnL    : {r[0]}")
print(f"  Zonder PnL : {r[1]}")
print(f"  Totaal     : {r[2]}")

cur.close()
conn.close()
