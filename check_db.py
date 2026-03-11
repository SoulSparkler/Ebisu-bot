import psycopg2, json
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

cur.execute("""
    SELECT created_at, coin, winner, pnl, total_cost, exit_reason,
           up_invested, down_invested, data
    FROM trades
    WHERE pnl IS NOT NULL
    ORDER BY created_at DESC
    LIMIT 5
""")
for r in cur.fetchall():
    print(f"time={r[0]} {r[1]} winner={r[2]} pnl={r[3]} cost={r[4]} exit={r[5]}")
    print(f"  up={r[6]} down={r[7]}")
    print(f"  data={json.dumps(r[8]) if r[8] else None}")

cur.close()
conn.close()
