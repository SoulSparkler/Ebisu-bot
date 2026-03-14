import psycopg2
from datetime import datetime
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

print("=== MEEST RECENTE ORDERS ===")
cur.execute("""
    SELECT created_at, market_slug, side
    FROM orders
    ORDER BY created_at DESC
    LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1][-20:]} {r[2]}")

print("\n=== MEEST RECENTE TRADE ===")
cur.execute("""
    SELECT created_at, coin, winner, pnl
    FROM trades
    ORDER BY created_at DESC
    LIMIT 3
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} winner={r[2]} pnl={r[3]}")

print(f"\nHuidige tijd UTC: {datetime.utcnow()}")

cur.close()
conn.close()
