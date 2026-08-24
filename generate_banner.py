"""
Generate RadicalMap README banner.
Dark archival OSINT aesthetic — matches the frontend exactly.
Run: venv/bin/python generate_banner.py
"""

import math
import random
from PIL import Image, ImageDraw, ImageFont

# ── Palette ────────────────────────────────────────────────────────────────
PAPER   = (19,  21,  16)
PANEL   = (25,  27,  22)
INK     = (216, 212, 198)
MID     = (150, 146, 127)
FAINT   = (95,  97,  86)
RULE_HI = (61,  63,  55)

CAT_COLORS = {
    "white_nationalist":  (192,  80,  56),
    "antisemitic":        (201, 162,  39),
    "conspiracy":         (150, 146, 127),
    "radicalisation":     (210, 100,  42),
    "dehumanisation":     (158,  58,  58),
    "esoteric":           (107,  88, 138),
    "islamophobic":       ( 74, 122, 138),
    "incel":              (122,  92,  58),
    "accelerationist":    (175,  52,  38),
}
CATS = list(CAT_COLORS.keys())

W, H = 1400, 480
rng = random.Random(19)

img = Image.new("RGB", (W, H), PANEL)
d   = ImageDraw.Draw(img)

# ── Fonts ──────────────────────────────────────────────────────────────────
try:
    serif_lg = ImageFont.truetype("/System/Library/Fonts/Supplemental/PTSerif.ttc",  68, index=1)
    serif_it = ImageFont.truetype("/System/Library/Fonts/Supplemental/PTSerif.ttc",  18, index=2)
    mono_md  = ImageFont.truetype("/System/Library/Fonts/Supplemental/PTMono.ttc",   12, index=0)
    mono_sm  = ImageFont.truetype("/System/Library/Fonts/Supplemental/PTMono.ttc",   10, index=0)
except Exception:
    serif_lg = serif_it = mono_md = mono_sm = ImageFont.load_default()

# ── Header band ───────────────────────────────────────────────────────────
HEADER_H = 122                 # taller — gives breathing room below subtitle
d.rectangle([0, 0, W, HEADER_H], fill=PAPER)
d.line([(0, 3),  (W, 3)],  fill=RULE_HI, width=1)
d.line([(0, 7),  (W, 7)],  fill=RULE_HI, width=1)
d.line([(0, HEADER_H), (W, HEADER_H)], fill=RULE_HI, width=1)

d.text((48, 14), "RADICALMAP", font=serif_lg, fill=INK)
d.text((51, 88), "Extremist Content Network Mapping Instrument", font=serif_it, fill=MID)

for i, line in enumerate(["REF: RM-001", "Independent Academic Research", "v1.0 · 2026"]):
    tw = d.textlength(line, font=mono_sm)
    d.text((W - tw - 44, 18 + i * 15), line, font=mono_sm, fill=FAINT)

# ── Left metadata panel ───────────────────────────────────────────────────
LEFT_X    = 48
BODY_TOP  = HEADER_H + 18
LINE_H    = 21
DIVIDER_X = 475                # well clear of the longest value text

LABEL_W   = 100                # fixed label column width
VALUE_X   = LEFT_X + LABEL_W

meta = [
    ("TYPE",         "Methodological Proof-of-Concept"),
    ("DOMAIN",       "Online Radicalisation · Extremist Networks"),
    ("PLATFORM",     "Short-Form Video (TikTok)"),
    ("BASIS",        "DSA Art.40 · GDPR Art.89 · CDSM Art.3"),
    ("JURISDICTION", "European Union / Ireland"),
    ("RUNTIME",      "Python 3.11+  ·  SQLite  ·  FastAPI"),
]
for i, (label, value) in enumerate(meta):
    y = BODY_TOP + i * LINE_H
    d.text((LEFT_X,  y), label, font=mono_md, fill=FAINT)
    d.text((VALUE_X, y), value, font=mono_md, fill=INK)

d.line([(DIVIDER_X, HEADER_H + 10), (DIVIDER_X, H - 10)], fill=RULE_HI, width=1)

# ── Network canvas bounds — hard right of divider ─────────────────────────
NL = DIVIDER_X + 20          # left edge of network area
NR = W - 18                  # right edge
NT = HEADER_H + 8            # top edge
NB = H - 20                  # bottom edge
NW = NR - NL
NH = NB - NT

# Hub sits at ~62 % from left of net area — pushes the whole graph rightward
CX = NL + int(NW * 0.62)
CY = NT + int(NH * 0.50)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def dimmed(col, scale):
    return tuple(clamp(int(c * scale), 0, 255) for c in col)

# ── Build network ─────────────────────────────────────────────────────────
# Each node: dict with px, py, radius, color
# Each edge: dict with two node indices, color, width, alpha

nodes = []   # list of dicts
edges = []   # list of dicts

def add_node(px, py, r, col):
    nodes.append({"px": px, "py": py, "r": r, "col": col})
    return len(nodes) - 1

def add_edge(i, j, col, w, alpha):
    edges.append({"i": i, "j": j, "col": col, "w": w, "alpha": alpha})

# Central hub
hub = add_node(CX, CY, 14, INK)

# Per-category secondary nodes — varied radius and angular scatter
n_cats = len(CATS)
sec_ids = []

for idx, cat in enumerate(CATS):
    col = CAT_COLORS[cat]

    # Spread base angles around the full circle with irregular gaps
    base_ang = (idx / n_cats) * 2 * math.pi - math.pi * 0.6
    ang = base_ang + rng.uniform(-0.18, 0.18)

    # Varied first-ring radius — some close, some far
    r1 = rng.randint(68, 115)
    sx = CX + int(r1 * math.cos(ang))
    sy = CY + int(r1 * math.sin(ang))
    sx = clamp(sx, NL + 14, NR - 14)
    sy = clamp(sy, NT + 14, NB - 14)

    # Secondary node size — weighted toward medium but with outliers
    sec_size = rng.choices([5, 6, 7, 8, 9, 11, 13], weights=[2, 3, 4, 4, 3, 2, 1])[0]
    sid = add_node(sx, sy, sec_size, col)
    sec_ids.append((sid, sx, sy, ang, col))

    # Edge: hub → secondary, width proportional to secondary size
    ew = max(1, sec_size // 4)
    add_edge(hub, sid, col, ew, 0.50)

    # Tertiary nodes
    n_tert = rng.randint(2, 4)
    tert_ids = []

    for t in range(n_tert):
        spread = rng.uniform(0.18, 0.42)
        side   = rng.choice([-1, 1])
        ang2   = ang + side * spread * (t + 1) / n_tert + rng.uniform(-0.08, 0.08)
        r2     = r1 + rng.randint(52, 105)
        tx     = CX + int(r2 * math.cos(ang2))
        ty     = CY + int(r2 * math.sin(ang2))
        tx     = clamp(tx, NL + 14, NR - 14)
        ty     = clamp(ty, NT + 14, NB - 14)

        tert_size = rng.choices([2, 3, 4, 5, 6, 8], weights=[2, 4, 5, 4, 2, 1])[0]
        tid = add_node(tx, ty, tert_size, col)
        tert_ids.append((tid, tx, ty, ang2))
        add_edge(sid, tid, col, 1, 0.32)

        # Quaternary nodes — sparse, small, far out
        n_quat = rng.choices([0, 1, 2], weights=[3, 4, 2])[0]
        for q in range(n_quat):
            ang3 = ang2 + rng.uniform(-0.30, 0.30)
            r3   = r2 + rng.randint(35, 70)
            qx   = CX + int(r3 * math.cos(ang3))
            qy   = CY + int(r3 * math.sin(ang3))
            qx   = clamp(qx, NL + 14, NR - 14)
            qy   = clamp(qy, NT + 14, NB - 14)

            qsize = rng.choices([1, 2, 3], weights=[3, 4, 2])[0]
            qid = add_node(qx, qy, qsize, col)
            add_edge(tid, qid, col, 1, 0.18)

    # Occasional cross-link between two tertiaries of the same branch
    if len(tert_ids) >= 2 and rng.random() < 0.45:
        a, b = rng.sample(tert_ids, 2)
        add_edge(a[0], b[0], col, 1, 0.15)

# Cross-links between adjacent secondary branches
for k in range(n_cats):
    sid_a, sx_a, sy_a, ang_a, col_a = sec_ids[k]
    sid_b, sx_b, sy_b, ang_b, col_b = sec_ids[(k + 1) % n_cats]
    blend = tuple((a + b) // 2 for a, b in zip(col_a, col_b))
    add_edge(sid_a, sid_b, blend, 1, 0.14)

# A few random long-distance links for organic chaos
for _ in range(6):
    a, b = rng.sample(range(len(nodes)), 2)
    if a == hub or b == hub:
        continue
    col_a = nodes[a]["col"]
    col_b = nodes[b]["col"]
    blend = tuple((x + y) // 2 for x, y in zip(col_a, col_b))
    add_edge(a, b, blend, 1, 0.10)

# ── Draw edges ────────────────────────────────────────────────────────────
for e in edges:
    n1, n2 = nodes[e["i"]], nodes[e["j"]]
    ec = dimmed(e["col"], e["alpha"])
    d.line([(n1["px"], n1["py"]), (n2["px"], n2["py"])], fill=ec, width=e["w"])

# ── Draw nodes ────────────────────────────────────────────────────────────
for n in nodes:
    px, py, r, col = n["px"], n["py"], n["r"], n["col"]
    # Soft halo
    d.ellipse([px-r-5, py-r-5, px+r+5, py+r+5], fill=dimmed(col, 0.10))
    # Body
    d.ellipse([px-r,   py-r,   px+r,   py+r  ], fill=dimmed(col, 0.65))
    # Bright core
    cr = max(1, r - 3)
    d.ellipse([px-cr, py-cr, px+cr, py+cr],       fill=dimmed(col, 0.95))
    # Pin centre
    if r >= 5:
        d.ellipse([px-1, py-1, px+1, py+1], fill=INK)

# ── Footer ────────────────────────────────────────────────────────────────
d.line([(0, H - 22), (W, H - 22)], fill=RULE_HI, width=1)
footer = "github.com/EuanSmith2/radicalmap  ·  euansmith.net  ·  MIT License"
fw = d.textlength(footer, font=mono_sm)
d.text(((W - fw) // 2, H - 15), footer, font=mono_sm, fill=FAINT)

img.save("banner.png", "PNG", optimize=True)
print(f"Saved {W}×{H} → banner.png")
