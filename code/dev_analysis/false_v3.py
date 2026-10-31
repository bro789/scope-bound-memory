import json,sys,os
sys.path.insert(0,".")
import gold_eval as E, page_coverage as C
P=json.load(open("../runs/cc6-34_parse_dev_v2.json",encoding="utf-8"))
CT=json.load(open("../runs/containers_all.json",encoding="utf-8"))
want=set(sys.argv[1].split(","))
for tag,sd,rd in [("dev9b","../annotation_dev_v1","../runs/cc6-29_gold_dev_9b"),("g1_9b","../annotation_v1","../runs/cc6-30_gold_v1_9b"),("g1_35b","../annotation_v1","../runs/cc6-30_gold_v1_35b"),("g2_9b","../annotation_v2","../runs/cc6-32_gold_v2_9b"),("g2_35b","../annotation_v2","../runs/cc6-32_gold_v2_35b")]:
    gold,pages=E.load_set(sd); G={g["qid"]:g for g in gold}
    for r in json.load(open(f"{rd}/preds.json",encoding="utf-8")):
        if r["qid"] not in want: continue
        want.discard(r["qid"])
        g=G[r["qid"]]; p=r["preds"]["Full"]; sc=P[r["qid"]]["parse"]; pc=CT.get(g["session"],{})
        c=E.choose_container(p,sc,pc)
        print("\n##",tag,r["qid"],g["question"]); print("  gold:",str(g.get("answer"))[:150]); print("  pred:",str(p.get("answer"))[:150],p.get("evidence_pages"),p.get("operator")); print("  parse:",sc)
        print("  chosen:",c and (c["role"],c["label"][:40],c["n_entries"],c["status"],c.get("shown"),c.get("total"),c["entries"][:2]))
