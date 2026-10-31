import json,collections,sys
sys.path.insert(0,".")
import gold_eval as E
P=json.load(open(__import__("os").environ.get("PARSE","../runs/cc6-33_parse_dev.json"),encoding="utf-8"))
CT=json.load(open("../runs/containers_all.json",encoding="utf-8"))
why=collections.Counter(); ex=collections.defaultdict(list)
for tag,sd,rd in [("dev9b","../annotation_dev_v1","../runs/cc6-29_gold_dev_9b"),("g1_9b","../annotation_v1","../runs/cc6-30_gold_v1_9b"),("g1_35b","../annotation_v1","../runs/cc6-30_gold_v1_35b"),("g2_9b","../annotation_v2","../runs/cc6-32_gold_v2_9b"),("g2_35b","../annotation_v2","../runs/cc6-32_gold_v2_35b")]:
    gold,pages=E.load_set(sd); G={g["qid"]:g for g in gold}
    R=json.load(open(f"{rd}/preds.json",encoding="utf-8"))
    for r in R:
        g=G[r["qid"]]; p=r["preds"]["Full"]
        if not p or g["determined"]: continue
        sp=pages[g["session"]]["pages"]
        if not E.gate_v2(p,sp,g["question"])[1] or E.gate_v3(p,g["question"],(P.get(r["qid"]) or {}).get("parse"),CT.get(g["session"],{}))[1]: continue
        sc=(P.get(r["qid"]) or {}).get("parse") or {}
        if sc.get("scope")!="whole": w="scope=seen"
        elif sc.get("answer_source")!="enumerated": w="source="+str(sc.get("answer_source"))
        elif sc.get("top_of_sorted"): w="top_of_sorted"
        else:
            c=E.choose_container(p,sc,CT.get(g["session"],{}))
            w="no container" if not c else "container "+c["status"]
        why[w]+=1
        if len(ex[w])<4: ex[w].append((r["qid"],g["question"][-110:],sc.get("collection"), str(p.get("answer"))[:40]))
print(why)
for w,l in ex.items():
    print("==",w)
    for x in l: print("  ",x)
