import psycopg2
conn = psycopg2.connect("postgresql://postgres:lctkVmKONdNhOgyCcoWMXpUfjDPIhzhD@yamabiko.proxy.rlwy.net:12657/railway")
cur = conn.cursor()

# Hoeveel trades hebben ooit een winner gekregen?
cur.execute("""
    SELECT 
        COUNT(*) as totaal,
        SUM(CASE WHEN winner IS NOT NULL THEN 1 ELSE 0 END) as met_winner,
        SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) as met_pnl
    FROM trades
""")
r = cur.fetchone()
print(f"Totaal trade records : {r[0]}")
print(f"Met winner (afgesloten): {r[1]}")
print(f"Met pnl (afgesloten)  : {r[2]}")
print(f"Nooit afgesloten      : {r[0]-r[1]}")

cur.close()
conn.close()
