import psycopg2
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

print("=== ECHTE VERLIESREEKS ===")
cur.execute("""
    SELECT created_at, coin, winner, pnl, total_cost,
           up_invested, down_invested
    FROM trades
    WHERE pnl IS NOT NULL
    ORDER BY created_at ASC
""")
rows = cur.fetchall()

current_streak = []
max_streak = []
for r in rows:
    if r[3] < 0:
        current_streak.append(r)
        if len(current_streak) > len(max_streak):
            max_streak = current_streak.copy()
    else:
        current_streak = []

print(f"Langste ECHTE verliesreeks: {len(max_streak)} trades")
if max_streak:
    total = sum(r[3] for r in max_streak)
    print(f"Totaal verlies: ")
    for r in max_streak:
        side = 'UP' if (r[5] or 0) > 0 else 'DOWN'
        print(f"  {r[0]} {r[1]} kocht {side} | pnl= | cost=")

print("\n=== PAIR COST ARBITRAGE KANSEN ===")
cur.execute("""
    SELECT 
        created_at, coin,
        CAST(data->'market_prices'->>'up_ask' AS float) as up_ask,
        CAST(data->'market_prices'->>'down_ask' AS float) as down_ask,
        COALESCE(up_invested, 0) as up_inv,
        COALESCE(down_invested, 0) as down_inv
    FROM trades
    WHERE data->'market_prices'->>'up_ask' IS NOT NULL
    AND data->'market_prices'->>'down_ask' IS NOT NULL
    AND CAST(data->'market_prices'->>'up_ask' AS float) > 0.05
    AND CAST(data->'market_prices'->>'down_ask' AS float) > 0.05
    ORDER BY created_at DESC
    LIMIT 40
""")
rows = cur.fetchall()
arb_count = 0
total = 0
print(f"\n{'Tijd':<22} {'Coin':<5} {'UP':<6} {'DN':<6} {'Pair':<7} {'Kans?':<16} {'Bot deed'}")
for r in rows:
    total += 1
    pair = r[2] + r[3]
    arb = "✓ SUB-.99" if pair < 0.99 else "✗"
    if pair < 0.99:
        arb_count += 1
    side = "UP" if r[4] > 0 else "DOWN"
    print(f"{str(r[0]):<22} {r[1]:<5} {r[2]:<6.3f} {r[3]:<6.3f}  {arb:<16} kocht {side}")

print(f"\nArbitrage kansen: {arb_count}/{total} = {arb_count/max(total,1)*100:.0f}%")
print("(dit zijn entry-moment prices — echte kans is hoger want bot logt bij entry)")

cur.close()
conn.close()
