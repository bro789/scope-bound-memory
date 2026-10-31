"""Generic, lossy-but-content-preserving compaction of accessibility-tree text for reading by an LLM.

Drops node ids, interaction flags and indentation, glyph-only lines and StaticText lines that repeat the text
of the line above; starts at the page's filter/main/grid region when there is one. Used identically for every
arm of the G experiment, so no arm gets a better rendering than another.
"""
import re

FLAGS = re.compile(r", (?:clickable|visible|focusable|focused|editable|readonly|required|disabled|"
                   r"multiselectable=\w+|expanded=\w+|selected=\w+|checked='?\w+'?|hasPopup='?\w+'?|"
                   r"live='\w+'|relevant='[^']*'|controls='[^']*'|describedby='[^']*'|orientation='\w+'|"
                   r"autocomplete='\w+'|valuemin=\S+|valuemax=\S+|valuetext='[^']*'|level=\d+)")
NODE = re.compile(r"^\s*\[\w+\]\s*")
GLYPH = re.compile(r"^(?:StaticText|image|generic) '(?:\\u[0-9a-f]{4}|[-]|\s)*'$")


def focus_start(ax):
    for key in ("region 'Filter'", " main '", " grid '"):
        i = ax.find(key)
        if i >= 0:
            return max(0, ax.rfind("\n", 0, i) + 1)
    return 0


def compact(ax, limit=None):
    head = ax.split("\n", 1)[0]  # RootWebArea line: page title
    out, prev = [head.strip()], ""
    for ln in ax[focus_start(ax):].split("\n"):
        s = FLAGS.sub("", NODE.sub("", ln.strip()))
        if not s or GLYPH.match(s):
            continue
        m = re.match(r"^StaticText '(.*)'$", s)
        if m and m.group(1) and m.group(1) in prev:
            continue
        out.append(s)
        prev = s
    text = "\n".join(out)
    return text[:limit] if limit else text
