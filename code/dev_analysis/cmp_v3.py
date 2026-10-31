import json,collections,sys
sys.path.insert(0,".")
import gold_eval as E
P=json.load(open(__import__("os").environ.get("PARSE","../runs/cc6-33_parse_dev.json"),encoding="utf-8"))
CT=json.load(open("../runs/containers_all.json",encoding="utf-8"))
res=collections.Counter(); ex=collections.defaultdict(list)
for tag,sd,rd in [("dev9b","../annotation_dev_v1","../runs/cc6-29_gold_dev_9b"),("g1_9b","../annotation_v1","../runs/cc6-30_gold_v1_9b"),("g1_35b","../annotation_v1","../runs/cc6-30_gold_v1_35b"),("g2_9b","../annotation_v2","../runs/cc6-32_gold_v2_9b"),("g2_35b","../annotation_v2","../runs/cc6-32_gold_v2_35b")]:
    gold,pages=E.load_set(sd); G={g["qid"]:g for g in gold}
    R=json.load(open(f"{rd}/preds.json",encoding="utf-8")); J=json.load(open(f"{rd}/judge.json",encoding="utf-8"))
    for r in R:
        g=G[r["qid"]]; sp=pages[g["session"]]["pages"]; p=r["preds"]["Full"]
        if not p: continue
        sc=(P.get(r["qid"]) or {}).get("parse")
        correct=g["determined"] and J.get(f"{r['qid']}|{json.dumps(p.get('answer'),ensure_ascii=False,sort_keys=True)}",False)
        lab="FALSE" if correct else ("GOOD" if not g["determined"] else "neutral")
        for ver,fired in (("v2",E.gate_v2(p,sp,g["question"])[1]),("v3",E.gate_v3b(p,g["question"],sc,CT.get(g["session"],{}),sp)[1])):
            if fired:
                res[(tag,ver,lab)]+=1
                if ver=="v3" and lab=="FALSE": ex[tag].append(r["qid"])
for tag in ("dev9b","g1_9b","g1_35b","g2_9b","g2_35b"):
    print(f"{tag:7s} Full  v2: good {res[(tag,'v2','GOOD')]:2d} false {res[(tag,'v2','FALSE')]:2d}   v3: good {res[(tag,'v3','GOOD')]:2d} false {res[(tag,'v3','FALSE')]:2d}")
print("v3 false:",dict(ex))
