"""Build scope-sensitive development questions from parsed list-view observations.

CPU only, deterministic (SHA-based selection), no model calls. Gold answers are
derived from the raw AX observation itself, before any method is run.

1. Split coarse task families into dev / confirmation by SHA(seed+family).
   Only dev-family trajectories are used here; confirmation families stay untouched.
2. Group observations by (trajectory, table, filter conditions).
3. Emit candidate questions of six operator types, each labelled with whether the
   saved observations are sufficient to answer it, then sample a balanced set.

Usage: python make_dev_questions.py LIST_VIEWS_JSONL OUT_DIR [N_QUESTIONS]
"""
import collections
import hashlib
import json
import os
import re
import sys

SEED = "scope02-dev-v1"  # kept for v2 so the dev/confirm family split does not change
VERSION = "v2"
KEY_COLS = ("Number", "Name", "User ID", "SLA definition", "Display name", "Short description", "Title")
MAX_ENUM = 15


def h(*parts):
    return hashlib.sha256("|".join([SEED, *map(str, parts)]).encode()).hexdigest()


FAMILY_RULES = [
    ("sort", r"^Sort the"), ("filter", r"^(Create a filter|Filter the)"), ("create", r"^Create a new"),
    ("order", r"^Order "), ("onboard", r"(Onboard|Offboard)"), ("chart", r"(chart|retrieve some)"),
    ("investment", r"(investment|Allocate investments)"), ("problem_cleanup", r"(Problem List Cleanup|duplicate problems)"),
    ("assign", r"(Assign|Work Assignment|Agent Workload|Balance the workload)"), ("expenses", r"Expenses"),
    ("change_schedule", r"Scheduling Your Change"), ("training", r"training"), ("warranty", r"warranty"),
    ("same_item", r"has "),
]


def family(goal):
    g = goal.split("\n")[0]
    for name, pat in FAMILY_RULES:
        if re.search(pat, g, re.I):
            return name
    return "other"


def is_dev_family(fam):
    # roughly 40% of families go to development, the rest are reserved for confirmation
    return int(h("family", fam)[:8], 16) % 5 < 2


def clean(v):
    if v is None:
        return v
    v = v.split(" - Open record: ")[0]
    return v


def usable(v):
    return v not in (None, "", "(empty)") and len(v) <= 40 and "..." not in v


TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")


def unique_keys(o, kc):
    ks = [r["values"].get(kc) for r in o["rows"]]
    return len(ks) == len(set(ks))


def key_of(row, cols):
    for c in KEY_COLS:
        if c in row["values"] and row["values"][c] not in ("", "(empty)"):
            return c, row["values"][c]
    return None, row["key"]


def value_cols(rows, key_col):
    cols = collections.Counter()
    for r in rows:
        for c, v in r["values"].items():
            if c != key_col and usable(v):
                cols[c] += 1
    return [c for c, _ in cols.most_common()]


def complete(o):
    p = o["page"]
    return p["first"] == 1 and p["last"] >= p["total"] and len(o["rows"]) == p["total"]


def conds_text(conds):
    return "with no filter conditions (the full list)" if not conds else "matching the filter " + " AND ".join(f"[{c}]" for c in conds)


def goal_key(goal):
    return " ".join(goal.split())[:300]


def anchor_text(goal):
    # v2: the full task text (not only its first line), so that the named session is unique in the haystack
    return f'In my earlier ServiceNow session for the task "{goal_key(goal)}"'


def main():
    src, out_dir = sys.argv[1], sys.argv[2]
    n_target = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    use_split = sys.argv[4] if len(sys.argv) > 4 else "dev"  # "confirm" builds the confirmation set
    scale = max(1, round(n_target / 40))
    prefix, stem = ("S02D", f"dev_questions_{VERSION}") if use_split == "dev" else ("S02C", "confirm_questions_v1")
    # G route: a new selection seed (family split unchanged) and anchors already used elsewhere excluded
    sel_tag = sys.argv[5] if len(sys.argv) > 5 else ""
    excluded, excluded_groups = set(), set()
    for path in (sys.argv[6].split(",") if len(sys.argv) > 6 else []):
        if path.endswith("@groups"):  # exclude only the exact lists (session, table, filter) already asked about
            for l in open(path[:-len("@groups")]):
                x = json.loads(l)
                excluded_groups.add((x["anchor_trajectory"], x["scope"]["table"], tuple(x["scope"]["conditions"])))
        else:
            excluded |= {json.loads(l)["anchor_trajectory"] for l in open(path)}
    if sel_tag:
        prefix = {"dev": "S02G", "confirm": "S02H"}[use_split]
        stem = f"{use_split}_questions_{sel_tag}"

    def hs(*parts):
        return h(sel_tag, *parts) if sel_tag else h(*parts)
    os.makedirs(out_dir, exist_ok=True)
    views = [json.loads(l) for l in open(src)]
    for v in views:
        for r in v["rows"]:
            r["values"] = {c: clean(x) for c, x in r["values"].items()}
    fam_of = {v["trajectory"]: family(v["goal"]) for v in views}
    goal_of = {v["trajectory"]: v["goal"] for v in views}
    fams = sorted(set(fam_of.values()))
    split = {f: ("dev" if is_dev_family(f) else "confirm") for f in fams}
    json.dump({"seed": SEED, "rules": FAMILY_RULES, "split": split,
               "trajectories": {t: split[f] for t, f in sorted(fam_of.items())}},
              open(os.path.join(out_dir, "family_split.json"), "w"), indent=1)
    dev_views = [v for v in views if split[fam_of[v["trajectory"]]] == use_split and v["table"] and v["rows"]]

    groups = collections.defaultdict(list)
    for v in dev_views:
        groups[(v["trajectory"], v["table"], tuple(v["conditions"]))].append(v)
    by_table = collections.defaultdict(set)
    for (t, tab, _c) in groups:
        by_table[tab].add(t)
    # values seen for (table, column) anywhere in the dev pool: source of plausible negatives
    seen_vals = collections.defaultdict(set)
    for v in dev_views:
        for r in v["rows"]:
            for c, x in r["values"].items():
                if usable(x):
                    seen_vals[(v["table"], c)].add(x)

    # confirmation set only (dev v2 stays byte-identical): a list shown several times whose content changed is
    # version-ambiguous for questions that do not say "most recent", so those use only stable groups; groups whose
    # latest observation has no parsed rows are skipped. All observations count here, including row-less ones.
    strict = use_split == "confirm" or bool(sel_tag)
    all_groups = collections.defaultdict(list)
    for v in views:
        if split[fam_of[v["trajectory"]]] == use_split and v["table"]:
            all_groups[(v["trajectory"], v["table"], tuple(v["conditions"]))].append(v)
    cur = {"stable": True}

    cands = []

    def add(qtype, op, g, q, sufficient, answer, evidence, naive=None, lower=None, extra=None):
        if strict and qtype not in ("version", "set_difference") and not cur["stable"]:
            return
        traj, tab, conds = g
        base = {"type": qtype, "operator": op, "anchor_trajectory": traj, "family": fam_of[traj],
                "question": q, "scope": {"table": tab, "conditions": list(conds)},
                "gold": {"source_sufficient": sufficient, "answer": answer, "observed_lower_bound": lower,
                         "required_evidence": evidence},
                "naive_unscoped_answer": naive}
        if extra:
            base["scope"].update(extra)
        base["cid"] = hs("cand", qtype, traj, tab, conds, q)[:12]
        cands.append(base)

    for g, obs in groups.items():
        traj, tab, conds = g
        if traj in excluded or g in excluded_groups:
            continue
        obs.sort(key=lambda o: o["state"])
        last = obs[-1]
        if strict:
            full = sorted(all_groups[g], key=lambda o: o["state"])
            if full[-1]["state"] != last["state"]:
                continue
            sigs = {json.dumps([o["page"], [r["values"] for r in o["rows"]]], sort_keys=True) for o in full}
            cur["stable"] = len(sigs) == 1
        kc, _ = key_of(last["rows"][0], last["columns"])
        if kc is None or not unique_keys(last, kc):
            continue
        rows = last["rows"]
        vcols = value_cols(rows, kc)
        full = complete(last)
        anchor = anchor_text(last["goal"])
        ev_last = [{"trajectory": traj, "state": last["state"], "page": last["page"]}]
        n = last["page"]["total"]
        shown = len(rows)
        sel = int(hs("pick", *g)[:8], 16)

        # A. single fact (always sufficient)
        if vcols:
            r = rows[sel % len(rows)]
            c = vcols[sel % len(vcols)]
            if r["values"].get(c) not in (None, "", "(empty)"):
                add("single_fact", "lookup", g,
                    f"{anchor}, the {tab} list was shown {conds_text(conds)}. As last shown there, what was the '{c}' of the record with {kc} '{r['values'][kc]}'?",
                    True, r["values"][c], ev_last + [{"row_node": r["node"]}])

        # B. existence: positive witness / negative needing full coverage
        if vcols:
            c = vcols[(sel >> 4) % len(vcols)]
            present = sorted({r["values"].get(c) for r in rows} - {None, "", "(empty)"})
            if present:
                v = present[sel % len(present)]
                add("existence", "exists", g,
                    f"{anchor}, considering the {tab} records {conds_text(conds)}, was there at least one record whose '{c}' is '{v}'?",
                    True, "yes", ev_last, extra={"column": c, "value": v})
            absent = sorted(seen_vals[(tab, c)] - set(present))
            if absent:
                v = absent[sel % len(absent)]
                add("existence", "exists", g,
                    f"{anchor}, considering the {tab} records {conds_text(conds)}, was there at least one record whose '{c}' is '{v}'?",
                    full, "no" if full else None, ev_last, naive="no", lower=0, extra={"column": c, "value": v})

        # C. counting
        add("count", "count", g,
            f"{anchor}, how many {tab} records were there {conds_text(conds)}?",
            True, n, ev_last, naive=shown if shown != n else None)
        if vcols:
            c = vcols[(sel >> 8) % len(vcols)]
            vals = collections.Counter(r["values"].get(c) for r in rows)
            vals.pop(None, None); vals.pop("", None); vals.pop("(empty)", None)
            if vals:
                v = sorted(vals)[sel % len(vals)]
                k = vals[v]
                add("count", "count", g,
                    f"{anchor}, among the {tab} records {conds_text(conds)}, how many had '{c}' equal to '{v}'?",
                    full, k if full else None, ev_last, naive=k, lower=k, extra={"column": c, "value": v})
        if conds:
            sub = conds[:-1]
            if (traj, tab, sub) not in groups:
                add("count", "count", g,
                    f"{anchor}, how many {tab} records were there {conds_text(sub)}?",
                    False, None, ev_last, naive=n, lower=n,
                    extra={"asked_conditions": list(sub), "note": "only a narrower filter was observed"})

        # D. enumeration
        if vcols:
            c = vcols[(sel >> 12) % len(vcols)]
            vals = collections.Counter(r["values"].get(c) for r in rows)
            vals.pop(None, None); vals.pop("", None); vals.pop("(empty)", None)
            if vals:
                v = sorted(vals)[(sel >> 3) % len(vals)]
                keys = sorted(r["values"][kc] for r in rows if r["values"].get(c) == v)
                if len(keys) <= MAX_ENUM:
                    add("enumeration", "enumerate", g,
                        f"{anchor}, list the {kc} of every {tab} record {conds_text(conds)} whose '{c}' is '{v}'.",
                        full, keys if full else None, ev_last, naive=keys, lower=len(keys),
                        extra={"column": c, "value": v})
        if n <= MAX_ENUM or not full:
            keys = sorted(r["values"][kc] for r in rows)
            add("enumeration", "enumerate", g,
                f"{anchor}, list the {kc} of every {tab} record {conds_text(conds)}.",
                full, keys if full else None, ev_last, naive=keys, lower=len(keys))

        # E. version: same table+filter observed at several states with different contents
        if len(obs) >= 2 and unique_keys(obs[0], kc):
            first = obs[0]
            fk = {r["values"].get(kc) for r in first["rows"]}
            lk = {r["values"].get(kc) for r in rows}
            if fk != lk and len(fk ^ lk) <= MAX_ENUM:
                both = complete(first) and full
                gone = sorted(x for x in fk - lk if x)
                add("set_difference", "difference", g,
                    f"{anchor}, the {tab} list {conds_text(conds)} was shown more than once. Which records ({kc}) appeared the first time but no longer appeared the last time it was shown?",
                    both, gone if both else None,
                    [{"trajectory": traj, "state": first["state"], "page": first["page"]}] + ev_last,
                    naive=gone, extra={"first_state": first["state"], "last_state": last["state"]})
            changed = []
            fmap = {r["values"].get(kc): r["values"] for r in first["rows"]}
            for r in rows:
                old = fmap.get(r["values"].get(kc))
                if old:
                    for c in vcols:
                        if old.get(c) != r["values"].get(c) and usable(r["values"].get(c)) and usable(old.get(c))                                 and not TS_RE.match(old.get(c)):
                            changed.append((r["values"][kc], c, old.get(c), r["values"][c]))
            if changed:
                key, c, ov, nv = changed[sel % len(changed)]
                add("version", "lookup_latest", g,
                    f"{anchor}, the {tab} list {conds_text(conds)} was shown more than once. In the most recent view, what was the '{c}' of {kc} '{key}'?",
                    True, nv, ev_last, naive=ov,
                    extra={"first_state": first["state"], "last_state": last["state"], "column": c})

    # one candidate per (anchor trajectory, type) at most twice, then balanced sampling
    cands.sort(key=lambda x: hs("order", x["cid"]))
    quota = {k: v * scale for k, v in
             {"single_fact": 5, "existence": 8, "count": 10, "enumeration": 8, "set_difference": 4, "version": 5}.items()}
    per_traj = collections.Counter()
    per_table = collections.Counter()
    per_table_type = collections.Counter()
    chosen = []
    for want_suff in (False, True, None):
        for x in cands:
            if x in chosen or per_traj[x["anchor_trajectory"]] >= 2 or per_table[x["scope"]["table"]] >= 10 * scale \
                    or per_table_type[(x["scope"]["table"], x["type"])] >= 3 * scale:
                continue
            t = x["type"]
            have = [y for y in chosen if y["type"] == t]
            if len(have) >= quota[t]:
                continue
            # first pass fills about half of each quota with insufficient items where they exist
            if want_suff is False and (x["gold"]["source_sufficient"] or len(have) >= quota[t] // 2):
                continue
            if want_suff is True and not x["gold"]["source_sufficient"]:
                continue
            chosen.append(x)
            per_traj[x["anchor_trajectory"]] += 1
            per_table[x["scope"]["table"]] += 1
            per_table_type[(x["scope"]["table"], x["type"])] += 1
    if strict and len(chosen) < n_target:
        # some operator types are scarce in the confirmation families: fill up to n_target, caps still apply
        for x in cands:
            if len(chosen) >= n_target:
                break
            if x in chosen or per_traj[x["anchor_trajectory"]] >= 2 or per_table[x["scope"]["table"]] >= 10 * scale:
                continue
            chosen.append(x)
            per_traj[x["anchor_trajectory"]] += 1
            per_table[x["scope"]["table"]] += 1
    chosen = chosen[:n_target]

    # haystack: anchor plus up to 3 other dev trajectories showing the same table (cross-source distractors)
    for i, x in enumerate(sorted(chosen, key=lambda y: (y["type"], y["cid"]))):
        ak = goal_key(goal_of[x["anchor_trajectory"]])
        others = sorted((t for t in by_table[x["scope"]["table"]] - {x["anchor_trajectory"]} if goal_key(goal_of[t]) != ak),
                        key=lambda t: hs("hay", x["cid"], t))
        x["haystack"] = [x["anchor_trajectory"]] + others[:3]
        x["qid"] = f"{prefix}{i + 1:03d}"
    chosen.sort(key=lambda y: y["qid"])
    with open(os.path.join(out_dir, f"{stem}.jsonl"), "w") as f:
        for x in chosen:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    stats = {"candidates": len(cands), "chosen": len(chosen),
             "by_type": collections.Counter(x["type"] for x in chosen),
             "insufficient": sum(not x["gold"]["source_sufficient"] for x in chosen),
             "tables": collections.Counter(x["scope"]["table"] for x in chosen),
             "families": collections.Counter(x["family"] for x in chosen),
             "split": collections.Counter(split.values()),
             "candidates_by_type_sufficiency": collections.Counter(f'{x["type"]}:{x["gold"]["source_sufficient"]}' for x in cands)}
    json.dump(stats, open(os.path.join(out_dir, f"{stem}_stats.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
