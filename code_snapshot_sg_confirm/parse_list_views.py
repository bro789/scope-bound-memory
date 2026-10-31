"""Extract scoped list-view observations from WorkArena (ServiceNow) AX trees.

CPU only, no model calls. Each output record is one observation O bound to its
scope: source (trajectory, state), entity (table), page (rows a..b of N),
filter (breadcrumb conditions + sysparm_query), container (grid), version
(step index / incoming action), position (AX node ids of the rows).

Usage:
  python parse_list_views.py TRAJ_JSONL HAYSTACK_JSON OUT_JSONL [--include-haystack]
By default trajectories referenced by any benchmark haystack are skipped, so the
development pool stays disjoint from the benchmark evaluation pool.
"""
import json
import re
import sys
import urllib.parse

ROWS_RE = re.compile(r"Showing rows (\d[\d,]*) to (\d[\d,]*) of (\d[\d,]*)")
NODE_RE = re.compile(r"^(\t*)\[(\w+)\] (\w+) '((?:[^'\\]|\\.)*)'")
HEADER_RE = re.compile(r"^(?P<n>.+?) (?:[^ ]+ )?(?P=n) column options$")  # "Number <glyph> Number column options"
COND_SUFFIX = " Press enter to remove all subsequent conditions."


def _int(s):
    return int(s.replace(",", ""))


def parse_nodes(ax):
    for ln in ax.split("\n"):
        m = NODE_RE.match(ln)
        if m:
            yield len(m.group(1)), m.group(2), m.group(3), m.group(4)


def parse_view(ax, url):
    m = ROWS_RE.search(ax)
    if not m or " grid '" not in ax:
        return None
    nodes = list(parse_nodes(ax))
    conds, table, grid_i = [], None, None
    for i, (dep, nid, role, name) in enumerate(nodes):
        if role == "link" and COND_SUFFIX in name:
            c = name.split(COND_SUFFIX)[0]
            if c != "All":
                conds.append(c)
        elif role == "grid" and grid_i is None:
            grid_i = i
            table = name.rstrip(".")
    if grid_i is None:
        return None
    gdep = nodes[grid_i][0]
    cols, rows, cur = [], [], None
    row_dep = None
    for dep, nid, role, name in nodes[grid_i + 1:]:
        if dep <= gdep:
            break
        if role == "columnheader":
            hm = HEADER_RE.match(name)
            cols.append(hm.group(1) if hm else name)
        elif role == "row":
            cur = {"node": nid, "cells": []}
            row_dep = dep
            rows.append(cur)
        elif role == "gridcell" and cur is not None and dep == row_dep + 1:
            cur["cells"].append(name)
    # Data columns start at the first column after the two control columns.
    data_cols = [c for c in cols if c not in ("Select All",) and "column search row" not in c]
    recs = []
    for r in rows:
        cells = r["cells"]
        if not cells or not cells[0].startswith("Select record for action: "):
            continue
        vals = [c for c in cells if not c.startswith(("Select record for action: ", "Preview record: "))]
        vals = [v[len("Open record: "):] if v.startswith("Open record: ") else v for v in vals]
        recs.append({"node": r["node"], "key": cells[0][len("Select record for action: "):],
                     "values": dict(zip(data_cols, vals))})
    q = urllib.parse.parse_qs(urllib.parse.urlparse(urllib.parse.unquote(url)).query)
    a, b, n = (_int(x) for x in m.groups())
    return {"table": table, "conditions": conds,
            "sysparm_query": (q.get("sysparm_query") or [None])[0],
            "page": {"first": a, "last": b, "total": n},
            "columns": data_cols, "rows": recs}


def main():
    traj_path, hay_path, out_path = sys.argv[1:4]
    include = "--include-haystack" in sys.argv
    used = set()
    if not include:
        used = {t for v in json.load(open(hay_path)).values() for t in v}
    n_tr = n_obs = 0
    with open(out_path, "w") as out:
        for line in open(traj_path):
            r = json.loads(line)
            if r["domain"] != "enterprise" or r["id"] in used:
                continue
            n_tr += 1
            for s in r["states"]:
                v = parse_view(s["accessibility_tree"] or "", s["url"] or "")
                if v is None:
                    continue
                v.update({"trajectory": r["id"], "state": s["state_index"], "step": s["step"],
                          "url": s["url"], "incoming_action": s["action"],
                          "goal": r["goal"], "outcome": r["outcome"]})
                out.write(json.dumps(v, ensure_ascii=False) + "\n")
                n_obs += 1
    print(json.dumps({"trajectories": n_tr, "observations": n_obs}))


if __name__ == "__main__":
    main()
