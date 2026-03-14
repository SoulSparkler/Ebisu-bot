import psycopg2
from datetime import datetime
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

cutoff = datetime(2026, 3, 14, 20, 25, 0)

cur.execute("""
    SELECT created_at, coin, winner, pnl, up_invested, down_invested
    FROM trades
    WHERE created_at >= %s AND pnl IS NOT NULL
    ORDER BY created_at DESC
""", (cutoff,))
rows = cur.fetchall()
print(f"Trades na 20:25: {len(rows)}")
for r in rows:
    arb = "ARB" if r[5] and r[5] > 0 else "DIR"
    print(f"  [{arb}] {r[0]} {r[1]} winner={r[2]} pnl={r[3]}")

cur.close()
conn.close()
