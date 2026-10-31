"""Select held-out trajectories for blind question annotation and render their pages as text. CPU only.

v1 (gold_v1, evaluation): 20 WorkArena sessions from confirmation families never used in any question set or
haystack + 20 WebArena sessions outside every benchmark haystack, stratified by site (port).
v2 (--tag annot-dev-v1, development): same procedure with other sessions (WorkArena dev families, WebArena
sessions not used by any earlier annotation set).
Selection is SHA-ordered and does not look at page content, so it is not biased towards list views. Each
distinct page (same compacted AX = same page) is rendered once with ax_compact, i.e. the text the methods read.

Usage:
  python select_annotation_pages.py CANDIDATES_JSON TRAJ_JSONL OUT_DIR                      (v1, as frozen)
  python select_annotation_pages.py --build TRAJ HAY SPLIT_JSON USED_GLOB EXCL_INDEX OUT_DIR TAG N_ENT N_WEB ENT_SPLIT
"""
import collections
import glob
import hashlib
import json
import os
import sys

from ax_compact import compact

TAG = "annot-v1"


def h(s):
    return hashlib.sha256((TAG + "|" + s).encode()).hexdigest()


def port(url):
    url = url or ""
    return url.split(":")[2].split("/")[0] if url.count(":") >= 2 else "?"


def render_selected(traj_path, sel, ports, out):
    meta = []
    for line in open(traj_path):
        r = json.loads(line)
        if r["id"] not in sel:
            continue
        seen = {}
        parts = ["# Session " + r["id"] + "\n\nTask the agent was doing: " + r["goal"].strip() + "\n"]
        for s in r["states"]:
            c = compact(s["accessibility_tree"] or "", 16000)
            if c in seen:
                continue
            seen[c] = s["state_index"]
            act = str(s.get("action") or "(start)")[:200]
            parts.append("\n## Page P%d (step %d; reached by action: %s)\nURL: %s\n```\n%s\n```\n"
                         % (s["state_index"], s["step"], act, s["url"], c))
        open(os.path.join(out, r["id"] + ".md"), "w").write("".join(parts))
        meta.append({"trajectory": r["id"], "domain": r["domain"], "site_port": ports.get(r["id"]),
                     "pages": len(seen)})
    json.dump(meta, open(os.path.join(out, "_index.json"), "w"), indent=1)
    print(len(meta), collections.Counter(m["domain"] for m in meta),
          collections.Counter(m["site_port"] for m in meta if m["domain"] == "web"))


def pick(cands, n_ent, n_web, ports):
    ok = [x for x in cands if x[3] <= 160000 and x[2] >= 4]
    ent = sorted([x for x in ok if x[0] == "enterprise"], key=lambda x: h(x[1]))[:n_ent]
    by = collections.defaultdict(list)
    for x in ok:
        if x[0] == "web":
            by[ports.get(x[1], "?")].append(x)
    for k in by:
        by[k].sort(key=lambda x: h(x[1]))
    web, i, keys = [], 0, sorted(by)
    while len(web) < n_web:
        k = keys[i % len(keys)]
        n = sum(ports.get(p[1]) == k for p in web)
        if n < len(by[k]):
            web.append(by[k][n])
        i += 1
    return {x[1] for x in ent + web}


def main():
    global TAG
    if sys.argv[1] != "--build":  # v1, frozen behaviour
        cand_path, traj_path, out = sys.argv[1:4]
        os.makedirs(out, exist_ok=True)
        ports = {}
        for line in open(traj_path):
            r = json.loads(line)
            if r["domain"] == "web":
                ports[r["id"]] = port(r["start_url"])
        render_selected(traj_path, pick(json.load(open(cand_path)), 20, 20, ports), ports, out)
        return
    traj_path, hay_path, split_path, used_glob, excl_index, out, TAG, n_ent, n_web, ent_split = sys.argv[2:12]
    os.makedirs(out, exist_ok=True)
    used = {t for v in json.load(open(hay_path)).values() for t in v}
    split = json.load(open(split_path))["trajectories"]
    for p in glob.glob(used_glob):  # every session already used by a question set (anchor or distractor)
        for line in open(p):
            x = json.loads(line)
            used |= set(x.get("haystack", [])) | {x.get("anchor_trajectory")}
    for p in excl_index.split(","):  # sessions of earlier annotation sets
        used |= {m["trajectory"] for m in json.load(open(p))}
    cands, ports = [], {}
    for line in open(traj_path):
        r = json.loads(line)
        if r["domain"] == "web":
            ports[r["id"]] = port(r["start_url"])
        # ent_split "any": any family (gold_v2: the gate was never tuned on templated question sets)
        if r["id"] in used or (r["domain"] == "enterprise" and ent_split != "any" and split.get(r["id"]) != ent_split):
            continue
        pages = {compact(s["accessibility_tree"] or "", 16000) for s in r["states"]}
        cands.append((r["domain"], r["id"], len(pages), sum(len(c) for c in pages)))
    render_selected(traj_path, pick(cands, int(n_ent), int(n_web), ports), ports, out)


if __name__ == "__main__":
    main()
