"""Site-independent coverage reading of one rendered page (ax_compact text). No model calls.

Returns what the page itself says about how much of a listing is visible:
  total     : size of the whole listing when the page states it ("Showing rows a to b of N", "Items a-b of N",
              "a - b of N", "N records found", tab counts are NOT used as listing size)
  first/last: visible range when stated
  shown     : visible rows (stated range, else counted table/grid rows, else list items)
  pager     : True when the page offers a next page / further page numbers / "load more"
  status    : "complete" | "partial" | "unknown"
Rules are generic text patterns tuned only on the development annotation set (annotation_dev_v1).
"""
import re

NUM = r"(\d[\d,]*)"
RANGE_PATTERNS = [
    re.compile(r"[Ss]howing (?:rows )?" + NUM + r" (?:to|-|–) " + NUM + r" of " + NUM),
    re.compile(r"Items? " + NUM + r"\s*(?:[-–]|to)\s*" + NUM + r" of " + NUM),  # "Items 1 to 12 of 24 total"
    re.compile(r"\b" + NUM + r"\s*[-–]\s*" + NUM + r" of " + NUM + r"\b"),
]
TOTAL_PATTERNS = [
    re.compile(NUM + r" records? found"),
    re.compile(r"\bof " + NUM + r" (?:records|results|items|entries)\b"),
]
PAGER = re.compile(r"link '(?:Next(?: page)?|Go to next page|›|»|Page [2-9]\d*|[2-9]\d*|Load more|Show more)'"
                   r"|button '(?:Next(?: page)?|Load more|Show more)'"
                   r"|Page " + NUM + r" of " + NUM)


def _i(s):
    return int(s.replace(",", ""))


def _rows(text):
    n = sum(1 for ln in text.split("\n") if ln.startswith("row ") and not ln.startswith("row 'Select All"))
    return max(0, n - (1 if "columnheader" in text else 0))


def read(text):
    first = last = total = None
    for p in RANGE_PATTERNS:
        m = p.search(text)
        if m:
            first, last, total = (_i(g) for g in m.groups())
            break
    if total is None:
        for p in TOTAL_PATTERNS:
            m = p.search(text)
            if m:
                total = _i(m.group(1))
                break
    rows = _rows(text)
    shown = (last - first + 1) if first is not None else (rows or None)
    pg = PAGER.search(text)
    pager = bool(pg) and not (pg.group(0).startswith("Page") and pg.group(1) == pg.group(2))
    if total is not None and shown is not None:
        status = "complete" if (first in (None, 1)) and shown >= total else "partial"
    elif pager:
        status = "partial"
    else:
        status = "unknown"
    return {"total": total, "first": first, "last": last, "shown": shown, "rows_counted": rows,
            "pager": pager, "status": status}


# ---- v2 (2026-10-24): tightened pager evidence after error analysis on annotation_dev_v1 + gold_v1 (now dev) ----
NUM_LINK = re.compile(r"^link '(\d{1,3})'$", re.M)
NEXT_LINK = re.compile(r"^(?:link|button) '(?:Next(?: page)?|Go to next page|›|»|Load more|Show more|Page \d+|"
                       r"\S*\s*Page Next)'$", re.M)


# a block that shows a subset and links to the full list ("Recent Orders ... View All")
VIEW_ALL = re.compile(r"^(?:link|button) '(?:View All|View all|See all|See All|Show all|Show All|View All \w+)'", re.M)
COUNT_NOUNS = (r"results?|items?|records?|products?|submissions?|issues?|merge requests?|projects?|members?|reviews?"
               r"|comments?|users?|entries|rows|orders?|posts?|threads?|commits?|branches|tags|stars?|forks?")


def stated_counts(text):
    """Numbers the page states as a count: 'N results', 'of N', 'N total', 'Open N' tab labels."""
    out = set()
    text = text + "\n" + joined_static(text)
    for m in re.finditer(r"(\d[\d,]*)\s+(?:" + COUNT_NOUNS + r")\b", text, re.I):
        n = int(m.group(1).replace(",", ""))
        if n >= 10:  # small numbers next to a noun ("4 Reviews" on a product card) are too often coincidental
            out.add(n)
    for m in re.finditer(r"\bof (\d[\d,]*)\b|\b(\d[\d,]*) total\b|\b(?:Open|Closed|Merged|All)\s+(\d[\d,]*)\b", text):
        out.add(int(next(g for g in m.groups() if g).replace(",", "")))
    # a number alone on a line next to a count noun (e.g. "StaticText '31190'" followed by "items")
    lines = text.split("\n")
    noun = re.compile(r"\b(?:" + COUNT_NOUNS + r")\b", re.I)
    for i, ln in enumerate(lines):
        m = re.fullmatch(r"StaticText '(\d[\d,]*)'", ln.strip())
        if m and any(noun.search(lines[j]) for j in range(max(0, i - 2), min(len(lines), i + 3)) if j != i):
            out.add(int(m.group(1).replace(",", "")))
    return out


EXPLICIT_NONE = re.compile(r"\b(?:No (?:records|results|items|entries|reviews|comments|orders|data)\b|nothing found"
                           r"|There are no|You have no|Be the first to review|0 (?:results|items|records|reviews|comments)\b"
                           r"|No records to display|no matching)", re.I)


def states_none(text):
    """The page itself says the (relevant) listing is empty."""
    return bool(EXPLICIT_NONE.search(text))


def listing_size(text):
    """Rough count of listing entries on the page: table rows, list items or articles."""
    rows = _rows(text)
    items = sum(1 for ln in text.split("\n") if ln.startswith(("listitem", "article ")))
    return max(rows, items)


def joined_static(text):
    """Consecutive StaticText values joined with spaces (pages often split '1 - 12 of 31190' over several lines)."""
    vals = re.findall(r"^StaticText '([^']*)'$", text, re.M)
    return " ".join(vals)


def read_v2(text):
    """Like read(), but a page counts as paged only if it is a listing (>=3 entries) AND it shows a next-page
    control, at least two small page-number links, or a "View All" link. Phone numbers, record navigation
    ("Next" on an edit form) and image carousels no longer count. Range statements split over several
    StaticText lines are recognised."""
    cov = read(text)
    if cov["total"] is None:
        cov2 = read(joined_static(text))
        if cov2["total"] is not None:
            cov = dict(cov, total=cov2["total"], first=cov2["first"], last=cov2["last"],
                       shown=cov2["shown"] if cov2["first"] is not None else cov["shown"])
            if cov["shown"] is not None:
                cov["status"] = "complete" if (cov["first"] in (None, 1)) and cov["shown"] >= cov["total"] else "partial"
    nums = {int(x) for x in NUM_LINK.findall(text)}
    paged = listing_size(text) >= 3 and (bool(NEXT_LINK.search(text)) or len(nums) >= 2 or bool(VIEW_ALL.search(text)))
    if cov["total"] is not None and cov["shown"] is not None:
        status = cov["status"]
    elif paged:
        status = "partial"
    else:
        status = "unknown"
    return dict(cov, pager=paged, status=status)


def printed_numbers(text):
    """Every integer printed on the page (commas removed)."""
    return {int(x.replace(",", "")) for x in re.findall(r"(?<![\w.])\d[\d,]*(?![\w])", text)}


def note(cov):
    """One-line coverage note shown with a page (same wording style as the frozen G v2 notes)."""
    if cov["status"] == "complete":
        return f"[Coverage note: this page shows the whole listing ({cov['total']} of {cov['total']}).]"
    if cov["status"] == "partial":
        if cov["total"] is not None:
            return (f"[Coverage note: INCOMPLETE listing: only {cov['shown']} of {cov['total']} entries are visible on "
                    f"this page; counts, lists, negatives and extremes over the whole listing cannot be read from it.]")
        return ("[Coverage note: INCOMPLETE listing: more pages exist beyond this one; counts, lists, negatives and "
                "extremes over the whole listing cannot be read from it.]")
    return ""


def split_pages(md):
    """Parse a rendered session file into {page_id: text}."""
    out = {}
    for blk in re.split(r"\n## Page ", md)[1:]:
        pid = blk.split(" ", 1)[0]
        m = re.search(r"```\n(.*)\n```", blk, re.S)
        out[pid] = m.group(1) if m else ""
    return out
