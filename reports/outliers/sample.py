import sqlite3, sys, random
outlet, axis, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
partner = sys.argv[4] if len(sys.argv) > 4 else None
v = sqlite3.connect("reports/outliers/v.sqlite")
h = sqlite3.connect("file:/Users/axelsolhall/dev/BasedNews/data/history/history.sqlite?mode=ro", uri=True, timeout=60)
q = f"SELECT day, a, b, fa, fb, dir, {axis} FROM v WHERE (a=? OR b=?) AND {axis} IS NOT NULL"
args = [outlet, outlet]
if partner:
    q += " AND (a=? OR b=?)"; args += [partner, partner]
rows = v.execute(q, args).fetchall()
random.seed(1); rows = random.sample(rows, min(n, len(rows)))
pole = {"econ": "more economically RIGHT", "social": "more CONSERVATIVE"}[axis]
for day, a, b, fa, fb, d, fw in rows:
    ta = h.execute("SELECT title, summary FROM articles WHERE id=?", (fa,)).fetchone()
    tb = h.execute("SELECT title, summary FROM articles WHERE id=?", (fb,)).fetchone()
    win = a if fw == 1 else b
    print(f"--- {day} [{d}] judged {pole}: {win}")
    print(f"  {a}: {ta[0]} — {(ta[1] or '')[:220]}")
    print(f"  {b}: {tb[0]} — {(tb[1] or '')[:220]}")
