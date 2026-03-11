import psycopg2, json
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

from datetime import datetime
cutoff = datetime(2026, 3, 11, 17, 0, 0)

cur.execute("""
    SELECT created_at, coin, winner, pnl, total_cost, exit_reason,
           up_invested, down_invested
    FROM trades
    WHERE created_at >= %s
    ORDER BY created_at DESC
    LIMIT 20
""", (cutoff,))
rows = cur.fetchall()
print(f"Trades na 17:00: {len(rows)}")
for r in rows:
    arb = "ARB" if r[7] and r[7] > 0 else "DIR"
    print(f"[{arb}] {r[0]} {r[1]} winner={r[2]} pnl={r[3]} cost={r[4]} up={r[6]} dn={r[7]}")

cur.close()
conn.close()
