"""Container-level coverage from the raw (indented) accessibility tree. CPU only, no model calls.

A page is split into containers (grid, table, list, listbox, tree, feed, dialog). For each container we record its
entries (rows / list items / options with their text), a label (nearest preceding heading or the container's own
name), and the coverage evidence attached to it:
  - a range statement ("Showing rows a to b of N", "Items a to b of N", "a - b of N", "N records found") whose
    position is closest to the container and whose visible size is compatible with the container's entry count;
  - pager controls (Next / page numbers / View All / Load more) closest to the container.
Status per container: complete (stated total == entries shown), partial (stated total > shown, or a pager attached),
or unknown. Navigation menus, banners and toolbars are skipped.

Usage: python container_coverage.py TRAJ_JSONL INDEX_JSON[,INDEX_JSON...] OUT_JSON
  INDEX_JSON: the pages/_index.json of an annotation set (sessions to process); page ids P<state_index> are taken
  from the rendered page files next to it (only distinct pages are processed).
"""
import json
import os
import re
import sys

NODE = re.compile(r"^(\t*)(?:\[(\w+)\] )?(\w+)(?: '((?:[^'\\]|\\.)*)'| \"((?:[^\"\\]|\\.)*)\")?")
CONTAINER_ROLES = {"grid", "table", "list", "listbox", "tree", "feed", "dialog", "LayoutTable"}
ENTRY_ROLES = {"row", "listitem", "option", "treeitem", "article", "LayoutTableRow"}
SKIP_ANCESTORS = {"navigation", "banner", "menubar", "toolbar", "contentinfo", "tablist", "menu"}
NUM = r"(\d[\d,]*)"
RANGES = [re.compile(r"[Ss]howing (?:rows )?" + NUM + r" (?:to|-|–) " + NUM + r" of " + NUM),
          re.compile(r"Items? " + NUM + r"\s*(?:[-–]|to)\s*" + NUM + r" of " + NUM),
          re.compile(r"\b" + NUM + r"\s*[-–]\s*" + NUM + r" of " + NUM + r"\b")]
TOTALS = [re.compile(NUM + r" records? found"), re.compile(NUM + r" (?:results?|items?) (?:found|total)")]
PAGER = re.compile(r"^(?:link|button) '(?:Next(?: page)?|Go to next page|›|»|Load more|Show more|View All|View all|"
                   r"See all|See All|Show all|Show All|Page \d+|\S*\s*Page Next|\d{1,3})'$")


def _i(s):
    return int(s.replace(",", ""))


def parse_nodes(ax):
    out = []
    for ln in (ax or "").split("\n"):
        m = NODE.match(ln)
        if not m:
            continue
        name = m.group(4) if m.group(4) is not None else (m.group(5) or "")
        out.append({"depth": len(m.group(1)), "role": m.group(3), "name": name, "line": ln.strip()})
    return out


def containers(ax):
    nodes = parse_nodes(ax)
    res, stack, last_heading = [], [], ""
    for i, n in enumerate(nodes):
        while stack and stack[-1][0] >= n["depth"]:
            stack.pop()
        anc = {r for _, r in stack}
        if n["role"] == "heading" and n["name"]:
            last_heading = n["name"]
        if n["role"] in CONTAINER_ROLES and not (anc & SKIP_ANCESTORS):
            # subtree
            j = i + 1
            while j < len(nodes) and nodes[j]["depth"] > n["depth"]:
                j += 1
            sub = nodes[i + 1:j]
            entries = []
            k = 0
            while k < len(sub):
                e = sub[k]
                if e["role"] in ENTRY_ROLES and e["depth"] <= n["depth"] + 3:
                    t = k + 1
                    texts = [e["name"]] if e["name"] else []
                    while t < len(sub) and sub[t]["depth"] > e["depth"]:
                        if sub[t]["name"]:
                            texts.append(sub[t]["name"])
                        t += 1
                    entries.append(" | ".join(dict.fromkeys(texts))[:400])
                    k = t
                else:
                    k += 1
            hdr = [e["name"] for e in sub if e["role"] == "columnheader" and e["name"]]
            if hdr and entries and not any(ch in entries[0] for ch in ("Select record", "Open record")) \
                    and all(h.split(" ")[0] in entries[0] for h in hdr[:2]):
                entries = entries[1:]  # header row
            res.append({"start": i, "end": j, "role": n["role"], "name": n["name"][:120], "label": last_heading[:120],
                        "columns": hdr[:20], "entries": entries, "n_entries": len(entries)})
        stack.append((n["depth"], n["role"]))
    # attach range statements and pagers to the nearest compatible container
    joined = []
    for i, n in enumerate(nodes):
        joined.append((i, n["line"]))
    static_run, run_start = [], None
    statements = []
    for i, ln in joined:
        for p in RANGES:
            m = p.search(ln)
            if m:
                a, b, t = (_i(g) for g in m.groups())
                statements.append({"pos": i, "first": a, "last": b, "total": t})
        for p in TOTALS:
            m = p.search(ln)
            if m:
                statements.append({"pos": i, "first": None, "last": None, "total": _i(m.group(1))})
        mm = re.fullmatch(r"StaticText '([^']*)'", ln)
        if mm:
            if run_start is None:
                run_start = i
            static_run.append(mm.group(1))
        else:
            if static_run:
                txt = " ".join(static_run)
                for p in RANGES:
                    m = p.search(txt)
                    if m:
                        a, b, t = (_i(g) for g in m.groups())
                        statements.append({"pos": run_start, "first": a, "last": b, "total": t})
            static_run, run_start = [], None
    pagers = [i for i, ln in joined if PAGER.match(ln)]
    num_pagers = [i for i, ln in joined if re.fullmatch(r"link '\d{1,3}'", ln)]
    for c in res:
        c["range"] = None
        c["pager"] = False
    cands = [c for c in res if c["n_entries"] >= 1]

    def dist(c, pos):
        return 0 if c["start"] <= pos < c["end"] else min(abs(pos - c["start"]), abs(pos - c["end"]))

    for s in statements:
        shown = (s["last"] - s["first"] + 1) if s["first"] is not None else None
        ok = [c for c in cands if shown is None or abs(c["n_entries"] - shown) <= max(2, shown // 5) or c["n_entries"] >= shown]
        if ok:
            best = min(ok, key=lambda c: dist(c, s["pos"]))
            if best["range"] is None or dist(best, s["pos"]) < best.get("_rd", 1e9):
                best["range"] = {k: s[k] for k in ("first", "last", "total")}
                best["_rd"] = dist(best, s["pos"])
    for pos in pagers + (num_pagers if len(num_pagers) >= 2 else []):
        ok = [c for c in cands if c["n_entries"] >= 3]
        if ok:
            best = min(ok, key=lambda c: dist(c, pos))
            if dist(best, pos) <= 60:
                best["pager"] = True
    for c in res:
        c.pop("_rd", None)
        r = c["range"]
        if r and r["total"] is not None:
            shown = (r["last"] - r["first"] + 1) if r["first"] is not None else c["n_entries"]
            c["status"] = "complete" if (r["first"] in (None, 1) and shown >= r["total"]) else "partial"
            c["shown"], c["total"] = shown, r["total"]
        elif c["pager"]:
            c["status"], c["shown"], c["total"] = "partial", c["n_entries"], None
        else:
            c["status"], c["shown"], c["total"] = ("unknown", c["n_entries"], None)
    return [c for c in res if c["n_entries"] >= 1]


def main():
    traj_path, index_paths, out_path = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
    want = {}
    for ip in index_paths:
        pdir = os.path.dirname(ip)
        for m in json.load(open(ip)):
            md = open(os.path.join(pdir, m["trajectory"] + ".md"), encoding="utf-8").read()
            want[m["trajectory"]] = {int(x) for x in re.findall(r"## Page P(\d+)", md)}
    out = {}
    for line in open(traj_path):
        r = json.loads(line)
        if r["id"] not in want:
            continue
        out[r["id"]] = {}
        for s in r["states"]:
            if s["state_index"] in want[r["id"]]:
                out[r["id"]]["P%d" % s["state_index"]] = containers(s["accessibility_tree"])
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    n = sum(len(v) for v in out.values())
    print(json.dumps({"sessions": len(out), "pages": n}))


if __name__ == "__main__":
    main()
