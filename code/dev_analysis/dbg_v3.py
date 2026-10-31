import json,sys,os
sys.path.insert(0,".")
import gold_eval as E, page_coverage as C
P=json.load(open("../runs/cc6-34_parse_dev_v2.json",encoding="utf-8"))
CT=json.load(open("../runs/containers_all.json",encoding="utf-8"))
for tag,sd,rd in [("dev9b","../annotation_dev_v1","../runs/cc6-29_gold_dev_9b"),("g1_9b","../annotation_v1","../runs/cc6-30_gold_v1_9b"),("g1_35b","../annotation_v1","../runs/cc6-30_gold_v1_35b"),("g2_9b","../annotation_v2","../runs/cc6-32_gold_v2_9b"),("g2_35b","../annotation_v2","../runs/cc6-32_gold_v2_35b")]:
    gold,pages=E.load_set(sd); G={g["qid"]:g for g in gold}
    R=json.load(open(f"{rd}/preds.json",encoding="utf-8"))
    for r in R:
        g=G[r["qid"]]; sp=pages[g["session"]]["pages"]; p=r["preds"]["Full"]
        if not p or g["determined"]: continue
        sc=(P.get(r["qid"]) or {}).get("parse") or {}
        if not E.gate_v2(p,sp,g["question"])[1] or E.gate_v3b(p,g["question"],sc,CT.get(g["session"],{}),sp)[1]: continue
        if sc.get("scope")!="whole" or sc.get("answer_source")!="enumerated" or sc.get("top_of_sorted"): continue
        pc=CT.get(g["session"],{}); c=E.choose_container(p,sc,pc)
        print("\n##",tag,r["qid"],g["question"][-110:],"| ans",str(p.get("answer"))[:60],"| ev",p.get("evidence_pages"))
        print("  chosen:", c and (c["role"],c["label"][:40],c["n_entries"],c["status"]))
        by={x["pid"]:x for x in sp}
        for pid in p.get("evidence_pages") or []:
            conts=pc.get(pid,[])
            print("  ",pid,"v2:",C.read_v2(by[pid]["text"])["status"] if pid in by else "-", [(x["role"],x["label"][:25],x["n_entries"],x["status"]) for x in sorted(conts,key=lambda x:-x["n_entries"])[:4]])
