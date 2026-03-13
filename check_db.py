import psycopg2, json
from collections import defaultdict
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

from datetime import datetime
cutoff = datetime(2026, 3, 11, 17, 0, 0)

cur.execute("""
    SELECT created_at, coin, winner, pnl, total_cost, exit_reason,
           up_invested, down_invested,
           EXTRACT(HOUR FROM created_at) as uur
    FROM trades
    WHERE created_at >= %s AND pnl IS NOT NULL
    ORDER BY created_at ASC
""", (cutoff,))
rows = cur.fetchall()

pnls = [r[3] for r in rows]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]
arb = [r for r in rows if r[7] and r[7] > 0]
one_sided = [r for r in rows if not r[7] or r[7] == 0]

print(f"Trades totaal  : {len(rows)} ({len(wins)}W/{len(losses)}L)")
print(f"Win rate       : {round(len(wins)/max(len(rows),1)*100,1)}")
print(f"Total PnL      : {round(sum(pnls),3)}")
print(f"Avg win        : {round(sum(wins)/max(len(wins),1),3)}")
print(f"Avg loss       : {round(sum(losses)/max(len(losses),1),3)}")
print(f"Max loss       : {round(min(pnls),3)}")
print(f"Max win        : {round(max(pnls),3)}")
if losses:
    print(f"Profit factor  : {round(sum(wins)/abs(sum(losses)),2)}")

print(f"\nARB (beide kanten): {len(arb)}")
print(f"DIR (one-sided)   : {len(one_sided)}")

if arb:
    ap = [r[3] for r in arb]
    aw = [p for p in ap if p > 0]
    al = [p for p in ap if p < 0]
    print(f"\n=== ARB TRADES ===")
    print(f"{len(aw)}W/{len(al)}L | PnL={round(sum(ap),3)} | avg_win={round(sum(aw)/max(len(aw),1),3)} | avg_loss={round(sum(al)/max(len(al),1),3)}")

print("\n=== PER COIN ===")
for coin in ['sol','xrp']:
    cd = [r for r in rows if r[1]==coin]
    if cd:
        p = [r[3] for r in cd]
        w = len([x for x in p if x>0])
        print(f"  {coin.upper()}: {w}W/{len(p)-w}L | PnL={round(sum(p),3)} | avg_cost={round(sum(r[4] for r in cd if r[4])/len(cd),2)}")

print("\n=== PER UUR ===")
by_hour = defaultdict(list)
for r in rows:
    by_hour[int(r[8])].append(r[3])
for h in sorted(by_hour.keys()):
    p = by_hour[h]
    w = len([x for x in p if x>0])
    print(f"  {h:02d}:00  {w}W/{len(p)-w}L  pnl={round(sum(p),2)}")

print("\n=== GROOTSTE VERLIEZEN ===")
for r in sorted(rows, key=lambda x: x[3])[:5]:
    sided = "ARB" if r[7] and r[7]>0 else "DIR"
    print(f"  [{sided}] {r[1].upper()} pnl={round(r[3],3)} cost={r[4]} exit={r[5]}")

cur.close()
conn.close()
