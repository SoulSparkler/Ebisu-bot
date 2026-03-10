import psycopg2
from collections import defaultdict
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

cur.execute("""
    SELECT coin, winner, pnl, total_cost, exit_reason,
           up_invested, data,
           EXTRACT(HOUR FROM created_at) as uur,
           EXTRACT(DOW FROM created_at) as dag
    FROM trades WHERE pnl IS NOT NULL
""")
rows = cur.fetchall()

losses = [r for r in rows if r[2] < 0]
wins = [r for r in rows if r[2] > 0]

print("=== VERLIES ANALYSE ===")
print(f"Totaal verliezen: {len(losses)} | Gem verlies: {round(sum(r[2] for r in losses)/len(losses), 3)}")
print(f"Grootste verlies: {round(min(r[2] for r in losses), 3)}")
print(f"Kleinste verlies: {round(max(r[2] for r in losses if r[2]<0), 3)}")

print("\n--- Verliezen per coin ---")
for coin in ['sol','xrp','eth','btc']:
    cl = [r for r in losses if r[0]==coin]
    if cl:
        print(f"  {coin.upper()}: {len(cl)} verliezen | gem={round(sum(r[2] for r in cl)/len(cl),3)} | totaal={round(sum(r[2] for r in cl),3)}")

print("\n--- Verlies per uur van de dag ---")
by_hour = defaultdict(list)
for r in rows:
    by_hour[int(r[7])].append(r[2])
for h in sorted(by_hour.keys()):
    p = by_hour[h]
    w = len([x for x in p if x>0])
    pnl = round(sum(p),2)
    print(f"  {h:02d}:00  {w}W/{len(p)-w}L  pnl={pnl}")

print("\n--- Cost van winners vs losers ---")
win_costs = [r[3] for r in wins if r[3]]
loss_costs = [r[3] for r in losses if r[3]]
print(f"  Avg cost bij WIN : {round(sum(win_costs)/len(win_costs),3)}")
print(f"  Avg cost bij LOSS: {round(sum(loss_costs)/len(loss_costs),3)}")

print("\n--- Grootste 10 verliezen ---")
for r in sorted(losses, key=lambda x: x[2])[:10]:
    print(f"  {r[0].upper()} winner={r[1]} pnl={round(r[2],3)} cost={r[3]} exit={r[4]}")

cur.close()
conn.close()
