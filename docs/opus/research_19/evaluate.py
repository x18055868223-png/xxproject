import json,sys
sys.path.insert(0, r"C:\Users\Xu\Documents\中性回路整合工程\.worktrees\claude-astra\tools")
import astra_inquiry_lens_v1 as L
qs=json.load(open(sys.argv[1],encoding="utf-8")); lab={r["qid"]:r for r in json.load(open(sys.argv[2],encoding="utf-8"))}
cls=ok=okshown=0; miss=[]
for r in qs:
    got=L.classify(r); g=lab[r["qid"]]["primary"]
    if not got: miss.append(g); continue
    cls+=1; ok+=got["card"]==g; okshown+= g in [got["card"]]+got["also"]
n=len(qs)
print(f"n {n} classified {cls} ({cls/n:.0%}) primary {ok/cls:.0%} primary-or-close {okshown/cls:.0%} unclassified-labels {miss}")
