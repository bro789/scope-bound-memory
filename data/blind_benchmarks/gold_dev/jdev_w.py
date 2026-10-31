import json
F={2,6,7,9,10,12,16,17,22,25,27,28,31,32,38,43,44,47,51,60,64,72,80,83,84,89,97,98,99,102,104,106,114,116,117}
L=[json.loads(l) for l in open("judge_items_dev_9b.jsonl",encoding="utf-8")]
with open("judge_out_dev_9b.jsonl","w",encoding="utf-8") as f:
    for i,x in enumerate(L,1):
        f.write(json.dumps({"id":x["id"],"correct":i not in F},ensure_ascii=False)+"\n")
print(len(L),len(L)-len(F))
