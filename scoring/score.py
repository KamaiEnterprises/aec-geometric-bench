#!/usr/bin/env python3
"""Score a folder of predictions against the fifteen released sheets.

    python3 score.py --pred path/to/predictions [--gt ../dataset] [--name "My system"]
                     [--json per_sheet.json] [--not-supported Elevator,Stairs]

Reads three things:

  * the annotation    <gt>/annotations_15_scoring_ready.xml   (or --annotations FILE;
                      annotations_15.xml works as well and gives the same result)
  * the review ledger <gt>/annotation_review.json             (or --review FILE; --no-review
                      scores against the annotation exactly as exported)
  * the predictions   one JSON per sheet, named after the sheet (sheet_01.json ...)

Prediction format, one file per sheet:

    {"sheet": "sheet_01",
     "not_supported": ["Elevator", "Stairs"],                      (optional, see below)
     "objects": [{"class": "Single Swing Door", "bbox": [x0, y0, x1, y1]},
                 {"class": "Window", "polygon": [[x, y], [x, y], [x, y], [x, y]]}, ...],
     "areas":   [[[x, y], [x, y], ...], ...],
     "walls":   [[[x, y], [x, y], ...], {"outer": [[x, y], ...], "holes": [[[x, y], ...]]}, ...]}

Coordinates are pixels in the frame given by `width` / `height` for that sheet in
`manifest.json` (the frame of the annotation). An object carries `polygon` (its real
outline, which may be rotated), or `bbox`, or both; the polygon is used when present. A
sheet without a file is scored as if the system returned nothing.

The rules, applied identically to every system
----------------------------------------------
Objects, ten classes: Single Swing Door, Double Swing Door, Window, Sink, Toilet, Bathtub,
Shower, Cooktops, Elevator, Stairs.

  * A delivered object matches an annotated object of the same class when their shapes
    overlap by more than half (IoU > 0.5), or when at least half of the smaller shape lies
    inside the other and the larger is at most five times the smaller. Matching is one to
    one, best overlap first, so a second box on the same object is a false one. Shapes are
    the real outlines on both sides; nothing is reduced to an upright box.
  * Windows are matched by position along the wall, not by box size. An opening that is
    annotated several times counts once. A run of annotated panes under one long delivered
    window counts as found, and that long window is not false. An annotated window with a
    delivered `Opening` (an opening whose type the system did not decide) on it counts as
    found; an `Opening` on nothing is not scored. `Opening` is an output class of Kamai
    Nov26 and is not in the benchmark vocabulary; --no-opening-credit scores without it.
  * A delivered class outside the vocabulary is a false positive. Classes that are in the
    annotation vocabulary but not scored as objects (Sliding Door, Closet Door, Shower Door,
    Urinals, Legend, Floor plan, Railing, Wall, Room, Shaft, Balcony) are ignored.
  * The review ledger (`annotation_review.json`) is applied to the object annotation:
    listed shapes are removed or relabelled, listed windows are added, and an unmatched
    delivered object whose centre lies in a listed not-scored box is neither found nor false.
  * A system that has no Elevator or Stairs class is "not supported" for it: the class is
    left out of that system's total. Say so with --not-supported or with a `not_supported`
    list in the prediction files.

Areas: rooms, shafts, balconies, stairs and elevators count as area, on both sides. A stairs
or elevator shape that touches another area shape is merged with it; ordinary rooms are
never merged with each other. A delivered object of class Stairs, Elevator, Room, Shaft or
Balcony counts as area too. Scored as a pixel mask and as instances at IoU > 0.5. The ledger
does not change the area annotation.

Walls: Wall and Railing merged, scored as a pixel mask only.

The whole page is scored, no confidence threshold is used, and counts are pooled over the
sheets, not averaged per sheet.

Keep the full precision of your coordinates. Mask vertices are cut to whole pixels before
filling, so rounding a coordinate that sits on a pixel boundary moves that vertex by a pixel.

Requires Python 3.8+, numpy and shapely >= 2.0. The masks are filled by the routine in this
file, which reproduces the reference harness's rasteriser pixel for pixel; Pillow is no
longer needed.
"""
import argparse, json, math, os, sys, xml.etree.ElementTree as ET
import numpy as np
import shapely
from shapely.geometry import Polygon, box as sbox
from shapely.ops import unary_union
from shapely.strtree import STRtree

OBJECTS = ['Single Swing Door', 'Double Swing Door', 'Window', 'Sink', 'Toilet', 'Bathtub',
           'Shower', 'Cooktops', 'Elevator', 'Stairs']
WINDOW_SUBTYPES = {'Casement Window', 'Sliding Window', 'Swing Window', 'Double Window',
                   'Safe Room Window'}
AREA_PLAIN = ('Room', 'Shaft', 'Balcony')
AREA_CONNECTORS = ('Stairs', 'Elevator')        # merged with any area shape they touch
WALL = ('Wall', 'Railing')
OPENING = 'Opening'                             # delivered opening, type unknown
OPENING_CREDIT = True                           # --no-opening-credit turns it off
NOT_SCORED_CLASSES = {'Sliding Door', 'Closet Door', 'Shower Door', 'Urinals', 'Legend',
                      'Floor plan', 'Railing', 'Wall', 'Room', 'Shaft', 'Balcony'}
MAY_BE_UNSUPPORTED = ('Elevator', 'Stairs')
SIZE_CAP = 5.0              # "found" by containment only when the two areas are within 5x
RASTER_SHORT_SIDE = 5000    # masks are compared at this short side
AREA_GAP = 0.0015           # touching distance for the area rule, as a share of the short side
RULES = 'AEC-Geometric-Bench-15, rules of 23 November 2026'


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

def _dedupe(pts, eps=1e-9):
    """Drop consecutive duplicate vertices and a repeated closing vertex."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if p.shape[0] == 0:
        return p
    keep = np.ones(p.shape[0], dtype=bool)
    keep[1:] = ~np.all(np.abs(np.diff(p, axis=0)) <= eps, axis=1)
    p = p[keep]
    while p.shape[0] >= 2 and np.all(np.abs(p[0] - p[-1]) <= eps):
        p = p[:-1]
    return p


def _shoelace(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _label(lab):
    if lab in WINDOW_SUBTYPES:
        return 'Window'
    if lab == 'Railing':
        return 'Wall'
    return lab


def load_annotation(path):
    """{sheet: {'w', 'h', 'shapes': [[label, ring (N,2), bbox (4,)], ...]}} in file order."""
    keep = set(OBJECTS) | set(AREA_PLAIN) | {'Wall'}
    sheets = {}
    for img in ET.parse(path).getroot().iter('image'):
        name = os.path.splitext(os.path.basename(img.get('name')))[0]
        shapes = []
        for ch in img:
            if ch.get('source', 'manual') != 'manual':
                continue
            if ch.get('label') == 'Area':
                raise SystemExit(f'{path}: this is the scoring-ready file of the earlier rules (areas already '
                                 f'merged, no Elevator or Stairs objects). Use the current one, or annotations_15.xml.')
            lab = _label(ch.get('label', ''))
            if lab not in keep:
                continue
            drawn = ch.tag == 'polygon'
            if ch.tag == 'box':
                b = [float(ch.get(a)) for a in ('xtl', 'ytl', 'xbr', 'ybr')]
                x0, y0, x1, y1 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
                ring = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
                # CVAT stores an oriented box as an upright box plus `rotation`, in degrees
                # clockwise about the centre. 31 boxes in this release carry one.
                rot = float(ch.get('rotation', '0') or 0.0)
                if abs(rot % 360.0) > 1e-6:
                    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                    t = math.radians(rot); ct, st = math.cos(t), math.sin(t)
                    d = ring - np.array([cx, cy])
                    ring = np.column_stack([cx + d[:, 0] * ct - d[:, 1] * st,
                                            cy + d[:, 0] * st + d[:, 1] * ct])
                    drawn = True
                elif (x1 - x0) <= 1.0 or (y1 - y0) <= 1.0:
                    continue                    # degenerate box (none in this release)
            elif ch.tag == 'polygon':
                ring = _dedupe([tuple(map(float, q.split(','))) for q in ch.get('points').split(';') if q.strip()])
            else:
                continue
            if ring.shape[0] < 3 or (drawn and _shoelace(ring) <= 4.0):
                continue                        # degenerate polygon (none in this release)
            bbox = np.array([ring[:, 0].min(), ring[:, 1].min(), ring[:, 0].max(), ring[:, 1].max()])
            shapes.append([lab, ring, bbox])
        sheets[name] = {'w': int(img.get('width')), 'h': int(img.get('height')), 'shapes': shapes}
    return sheets


def apply_review(shapes, review, sheet):
    """The object annotation after the ledger: (shapes, not-scored boxes per class).

    Every fix must hit exactly one annotated shape (same class, box within one pixel);
    anything else stops the run, so a ledger that does not fit the annotation cannot be
    applied silently."""
    shapes = list(shapes)
    for c in review.get('fixes', []):
        if c['sheet'] != sheet:
            continue
        lab = _label(c['annotated_as'])
        m = [k for k, s in enumerate(shapes) if s[0] == lab
             and max(abs(a - b) for a, b in zip(s[2], c['box'])) <= 1.0]
        if len(m) != 1:
            raise SystemExit(f'review ledger: fix does not match exactly one annotated shape '
                             f'({len(m)} matches): {sheet} {c["annotated_as"]} {c["box"]}')
        if c['action'] == 'relabel':
            shapes[m[0]] = [c['to'], shapes[m[0]][1], shapes[m[0]][2]]
        elif c['action'] == 'remove':
            shapes.pop(m[0])
        else:
            raise SystemExit(f'review ledger: unknown action {c["action"]!r}')
    for a in review.get('added', []):
        if a['sheet'] != sheet:
            continue
        b = np.asarray(a['box'], dtype=np.float64)
        shapes.append([_label(a['class']), np.array([[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]), b])
    boxes = {}
    for r in review.get('not_scored', []):
        if r['sheet'] == sheet:
            boxes.setdefault(_label(r['class']), []).extend(sbox(*b) for b in r['boxes'])
    return shapes, boxes


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------

def poly(ring):
    p = Polygon(np.asarray(ring, dtype=float).reshape(-1, 2))
    return p if p.is_valid else p.buffer(0)


def iou_matrix(P, G):
    out = np.zeros((len(P), len(G)))
    if not P or not G:
        return out
    tree = STRtree(G)
    for i, p in enumerate(P):
        if p.is_empty:
            continue
        for j in tree.query(p):
            j = int(j); g = G[j]
            inter = p.intersection(g).area
            if inter > 0:
                out[i, j] = inter / (p.area + g.area - inter)
    return out


def found_matrix(P, G):
    """IoU, or 0.51 where at least half of the smaller shape lies inside the other and the
    two areas are within SIZE_CAP of each other."""
    out = iou_matrix(P, G)
    if not P or not G:
        return out
    tree = STRtree(G)
    for i, p in enumerate(P):
        if p.is_empty or p.area <= 0:
            continue
        for j in tree.query(p):
            j = int(j); g = G[j]
            if out[i, j] > 0.5 or g.area <= 0:
                continue
            small, big = min(p.area, g.area), max(p.area, g.area)
            if big <= SIZE_CAP * small and p.intersection(g).area >= 0.5 * small:
                out[i, j] = 0.51
    return out


def greedy(score):
    """One-to-one matching, best score first, kept only above 0.5."""
    tp = 0; up, ug = set(), set()
    hp = np.zeros(score.shape[0], int); hg = np.zeros(score.shape[1], int)
    pairs = sorted(((score[i, j], i, j) for i, j in zip(*np.nonzero(score > 0.5))), reverse=True)
    for _, i, j in pairs:
        if i not in up and j not in ug:
            up.add(i); ug.add(j); tp += 1; hp[i] = hg[j] = 1
    return tp, hp, hg


def axis_of(g):
    """Unit vectors along a shape's long and short side (minimum rotated rectangle)."""
    r = np.asarray(g.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = r[1] - r[0], r[2] - r[1]
    u = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
    u = u / max(np.linalg.norm(u), 1e-9)
    return u, np.array([-u[1], u[0]])


def _near(tree, geom, pad):
    return sorted(int(k) for k in tree.query(geom.buffer(pad)))


def window_oriented(P, G):
    """Overlap of a delivered window with an annotated one along the annotated window's own
    long direction (1-D IoU), counted only when the two also meet across its thickness."""
    out = np.zeros((len(P), len(G)))
    if not P or not G:
        return out
    tree = STRtree(G); cache = {}
    for i, p in enumerate(P):
        pc = np.asarray(p.exterior.coords) if hasattr(p, 'exterior') else np.zeros((0, 2))
        if not len(pc):
            continue
        for j in _near(tree, p, max(p.length, 1) * 0.05):
            g = G[j]
            if j not in cache:
                u, v = axis_of(g); gc = np.asarray(g.exterior.coords)
                cache[j] = (u, v, (gc @ u).min(), (gc @ u).max(), (gc @ v).min(), (gc @ v).max())
            u, v, a0, a1, b0, b1 = cache[j]
            pa, pb = pc @ u, pc @ v
            inter = max(0.0, min(pa.max(), a1) - max(pa.min(), a0))
            union = max(pa.max(), a1) - min(pa.min(), a0)
            touch = min(pb.max(), b1) - max(pb.min(), b0) >= 0
            if union > 0 and touch:
                out[i, j] = inter / union
    return out


def _thickness(g):
    r = np.asarray(g.minimum_rotated_rectangle.exterior.coords)[:4]
    return min(np.linalg.norm(r[1] - r[0]), np.linalg.norm(r[2] - r[1]))


def _span(g, p):
    """How p sits on g, in g's own two directions: (share of g covered along it, share of p
    inside g along it, whether they meet across it)."""
    u, v = axis_of(g); gc = np.asarray(g.exterior.coords); pc = np.asarray(p.exterior.coords)
    a0, a1 = (gc @ u).min(), (gc @ u).max(); pa = pc @ u
    inter = min(pa.max(), a1) - max(pa.min(), a0)
    b0, b1 = (gc @ v).min(), (gc @ v).max(); pb = pc @ v
    across = min(pb.max(), b1) - max(pb.min(), b0)
    return (max(inter, 0.0) / max(a1 - a0, 1e-9), max(inter, 0.0) / max(pa.max() - pa.min(), 1e-9),
            (inter >= 0 and across >= 0))


def lies_on(P, G):
    """1 where delivered window p lies on annotated window g: they meet, and at least half of
    p's extent along g's long direction is inside g."""
    out = np.zeros((len(P), len(G)))
    if not P or not G:
        return out
    tree = STRtree(G)
    for i, p in enumerate(P):
        if not hasattr(p, 'exterior') or p.is_empty:
            continue
        for j in _near(tree, p, max(p.length, 1) * 0.05):
            _, inside, touch = _span(G[j], p)
            if touch and inside >= 0.5:
                out[i, j] = 1
    return out


def openings(G):
    """Group annotated windows that mark the same opening: they touch, and one covers at
    least half of the other along its own long direction. One group index per annotation."""
    par = list(range(len(G)))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    if len(G) > 1:
        tree = STRtree(G)
        for j, g in enumerate(G):
            pad = max(g.length * 0.03, 4); gb = g.buffer(pad)
            for k in _near(tree, g, pad):
                if k <= j or not gb.intersects(G[k]):
                    continue
                if max(_span(g, G[k])[0], _span(G[k], g)[0]) >= 0.5:
                    par[find(k)] = find(j)
    return [find(j) for j in range(len(G))]


def window_found(P, G, O):
    """(openings found, false windows, openings missed, delivered hits, annotation hits).

    One-to-one matching of delivered windows to annotations (position along the wall first,
    "lies on" second); then an annotated window lying on a delivered window strip is found,
    and an annotated window with a delivered Opening on it is found. An opening counts once
    however many annotations mark it."""
    rel = window_oriented(P, G); on = lies_on(P, G)
    _, hp, hg = greedy(np.maximum(rel, 0.51 * on * (rel <= 0.5)))
    used = np.zeros(len(P), int)
    if P and G:
        tree = STRtree(P)
        for j, g in enumerate(G):
            if hg[j]:
                continue
            for i in _near(tree, g, max(g.length, 1) * 0.05):
                p = P[i]
                if not hasattr(p, 'exterior') or p.is_empty:
                    continue
                _, g_inside_p, touch = _span(p, g)            # g measured in p's own directions
                if touch and g_inside_p >= 0.8 and _thickness(p) <= 2.5 * max(_thickness(g), 1e-9):
                    hg[j] = 1; used[i] = 1; break
    if O and G:
        tree = STRtree(O)
        for j, g in enumerate(G):
            if hg[j]:
                continue
            for i in _near(tree, g, max(g.length, 1) * 0.05):
                o = O[i]
                if not hasattr(o, 'exterior') or o.is_empty:
                    continue
                a = _span(g, o); b = _span(o, g)
                if (a[2] and a[1] >= 0.5) or (b[2] and b[1] >= 0.5):
                    hg[j] = 1; break
    grp = openings(G); found = {grp[j] for j in range(len(G)) if hg[j]}
    fp = int(((hp == 0) & (used == 0)).sum())
    return len(found), fp, len(set(grp)) - len(found), np.maximum(hp, used), hg


def in_boxes(P, boxes):
    """1 where the delivered shape's centre lies in one of the not-scored boxes."""
    return np.array([1 if (not p.is_empty and any(b.contains(p.representative_point()) for b in boxes)) else 0
                     for p in P], int)


def score_objects(pred_objects, gt_shapes, ns_boxes):
    """One sheet: ({class: [tp, fp, fn]}, false positives outside the vocabulary, delivered
    objects left unscored by the ledger, objects skipped for unusable geometry)."""
    G = {c: [] for c in OBJECTS}
    for lab, ring, _ in gt_shapes:
        if lab in G:
            G[lab].append(poly(ring))
    P = {c: [] for c in OBJECTS}; O = []; extra_fp = skipped = 0
    for cls, shape in pred_objects:
        if shape is None:
            skipped += 1
        elif cls in P:
            P[cls].append(shape)
        elif cls == OPENING:
            if OPENING_CREDIT:
                O.append(shape)
        elif cls not in NOT_SCORED_CLASSES:
            extra_fp += 1                       # a class outside the vocabulary
    counts = {}; unscored = 0
    for c in OBJECTS:
        if c == 'Window':
            tp, fp, fn, hp, _ = window_found(P[c], G[c], O)
        else:
            tp, hp, _ = greedy(found_matrix(P[c], G[c])); fp, fn = len(P[c]) - tp, len(G[c]) - tp
        boxes = ns_boxes.get(c, [])
        if boxes and len(P[c]):
            drop = int(((np.asarray(hp) == 0) & (in_boxes(P[c], boxes) == 1)).sum())
            fp -= drop; unscored += drop
        counts[c] = [int(tp), int(fp), int(fn)]
    return counts, extra_fp, unscored, skipped


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

def _area_poly(ring):
    a = np.asarray(ring, dtype=np.float64).reshape(-1, 2)
    if a.shape[0] < 3:
        return None
    g = Polygon(a)
    if not g.is_valid:
        g = g.buffer(0)
    return g if (g.is_valid and not g.is_empty and g.area > 0) else None


def merge_connectors(rings, is_connector, gap):
    """Area instances as outer rings. A stairs / elevator shape (connector) is merged with
    every area shape it touches within `gap`; ordinary rooms are never merged together."""
    geoms, conn = [], []
    for r, c in zip(rings, is_connector):
        g = _area_poly(r)
        if g is not None:
            geoms.append(g); conn.append(bool(c))
    if not geoms:
        return []
    parent = list(range(len(geoms)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    tree = STRtree(geoms)
    for i, g in enumerate(geoms):
        if not conn[i]:
            continue                            # a link needs a stairs / elevator shape at one end
        gb = g.buffer(gap)
        for j in sorted(int(k) for k in tree.query(gb)):
            if j != i and geoms[j].intersects(gb):
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    comp = {}
    for i in range(len(geoms)):
        comp.setdefault(find(i), []).append(geoms[i])
    out = []
    for gs in comp.values():
        if len(gs) == 1:
            u = gs[0]
        else:                                   # shapes that only touch still become one
            u = unary_union([g.buffer(gap / 2) for g in gs]).buffer(-gap / 2)
            if u.is_empty:
                u = unary_union(gs)
        for g in (list(u.geoms) if u.geom_type == 'MultiPolygon' else [u]):
            if not g.is_empty and g.area > 0:
                out.append(np.asarray(g.exterior.coords, dtype=np.float64))
    return out


# ---------------------------------------------------------------------------
# Masks. The reference harness fills polygons with OpenCV's fillPoly on vertices truncated
# to whole pixels. `fill_contours` reproduces that fill exactly without OpenCV: the 8-connected
# outline, plus every scanline between ceil(left edge) and floor(right edge), edges walked
# in 16-bit fixed point. (Checked pixel for pixel against OpenCV 4.13 on every shape of the
# release and on random polygons.)
# ---------------------------------------------------------------------------

_ONE = 1 << 16


def _line(x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx < 0:
        x1, y1, dx, dy = x2, y2, -dx, -dy
    sy = 1
    if dy < 0:
        dy, sy = -dy, -1
    if dy > dx:
        i = np.arange(dy + 1, dtype=np.int64)
        return x1 - ((dy - 2 * dx * i) // (2 * dy)), y1 + sy * i
    if dx == 0:
        return np.array([x1], dtype=np.int64), np.array([y1], dtype=np.int64)
    i = np.arange(dx + 1, dtype=np.int64)
    return x1 + i, y1 - sy * ((dx - 2 * dy * i) // (2 * dx))


def fill_contours(contours):
    """(mask, x0, y0): the pixels OpenCV's fillPoly sets for these integer contours, as a
    boolean array whose top-left pixel is (x0, y0). Even-odd across the contours."""
    cs = [np.asarray(c, dtype=np.int64).reshape(-1, 2) for c in contours]
    allp = np.vstack(cs)
    x0, y0 = int(allp[:, 0].min()), int(allp[:, 1].min())
    W, H = int(allp[:, 0].max()) - x0 + 1, int(allp[:, 1].max()) - y0 + 1
    mask = np.zeros((H, W), dtype=bool)
    rows, xs = [], []
    for c in cs:
        q = c - (x0, y0)
        for a in range(len(q)):
            xa, ya, xb, yb = int(q[a - 1][0]), int(q[a - 1][1]), int(q[a][0]), int(q[a][1])
            lx, ly = _line(xa, ya, xb, yb); mask[ly, lx] = True
            if ya == yb:
                continue
            num, den = (xb - xa) * _ONE, yb - ya
            d = abs(num) // abs(den) * (1 if (num >= 0) == (den > 0) else -1)
            ys, xstart = (np.arange(ya, yb, dtype=np.int64), xa * _ONE) if ya < yb else \
                         (np.arange(yb, ya, dtype=np.int64), xb * _ONE)
            rows.append(ys); xs.append(xstart + (ys - ys[0]) * d)
    if rows:
        r = np.concatenate(rows); x = np.concatenate(xs)
        o = np.lexsort((x, r)); r, x = r[o], x[o]
        ra = r[0::2]; xa = np.maximum((x[0::2] + _ONE - 1) >> 16, 0); xb = np.minimum(x[1::2] >> 16, W - 1)
        ok = xa <= xb
        diff = np.zeros(H * (W + 1), dtype=np.int32)
        np.add.at(diff, ra[ok] * (W + 1) + xa[ok], 1)
        np.add.at(diff, ra[ok] * (W + 1) + xb[ok] + 1, -1)
        mask |= np.cumsum(diff.reshape(H, W + 1), axis=1, dtype=np.int32)[:, :W] > 0
    return mask, x0, y0


def _paste(canvas, mask, x0, y0, value):
    """Write `value` where mask is set, clipped to the canvas."""
    H, W = canvas.shape
    ax0, ay0 = max(x0, 0), max(y0, 0); ax1, ay1 = min(x0 + mask.shape[1], W), min(y0 + mask.shape[0], H)
    if ax1 <= ax0 or ay1 <= ay0:
        return
    canvas[ay0:ay1, ax0:ax1][mask[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]] = value


def raster_shape(w, h):
    s = min(w, h)
    k = 1.0 if s <= RASTER_SHORT_SIDE else RASTER_SHORT_SIDE / float(s)
    return k, max(1, int(round(h * k))), max(1, int(round(w * k)))


def wall_mask(walls, w, h):
    """Union of wall shapes; a shape is (outer ring, [hole rings])."""
    k, H, W = raster_shape(w, h)
    out = np.zeros((H, W), dtype=bool)
    for outer, holes in walls:
        o = (np.asarray(outer, dtype=np.float64).reshape(-1, 2) * k).astype(np.int32)
        if o.shape[0] < 3:
            continue
        m, x0, y0 = fill_contours([o])
        hs = [(np.asarray(r, dtype=np.float64).reshape(-1, 2) * k).astype(np.int32) for r in holes]
        hs = [r for r in hs if r.shape[0] >= 3]
        if hs:
            hm, hx, hy = fill_contours(hs)
            _paste(m, hm, hx - x0, hy - y0, False)
        _paste(out, m, x0, y0, True)
    return out


def instance_map(rings, w, h):
    """Instance ids (1-based, 0 = background), larger instances painted first so a shape
    drawn inside another survives."""
    k, H, W = raster_shape(w, h)
    ids = np.zeros((H, W), dtype=np.int32)
    order = []
    for idx, r in enumerate(rings):
        o = (np.asarray(r, dtype=np.float32).reshape(-1, 2) * k).astype(np.int32)
        if o.shape[0] >= 3:
            order.append((_shoelace(o.astype(np.float64)), idx, o))
    order.sort(key=lambda a: -a[0])
    for _, idx, o in order:
        m, x0, y0 = fill_contours([o])
        _paste(ids, m, x0, y0, idx + 1)
    return ids


def _compact(ids, n):
    counts = np.bincount(ids.ravel(), minlength=n + 1)
    kept = [i for i in range(1, n + 1) if counts[i] > 0]
    lut = np.zeros(n + 1, dtype=np.int32)
    for new, old in enumerate(kept, start=1):
        lut[old] = new
    return lut[ids], len(kept)


def score_areas(pred, gt, w, h):
    """(pixel [inter, pred only, annotation only], instance [tp, fp, fn]) for one sheet.
    Instance IoU is measured on the painted instance maps."""
    g_ids, ng = _compact(instance_map(gt, w, h), len(gt))
    p_ids, npr = _compact(instance_map(pred, w, h), len(pred))
    cols = ng + 1
    joint = np.bincount((p_ids.astype(np.int64) * cols + g_ids.astype(np.int64)).ravel(),
                        minlength=(npr + 1) * cols).reshape(npr + 1, cols)
    inter = joint[1:, 1:].astype(np.float64)
    ap = joint[1:, :].sum(axis=1, dtype=np.float64); ag = joint[:, 1:].sum(axis=0, dtype=np.float64)
    union = ap[:, None] + ag[None, :] - inter
    with np.errstate(divide='ignore', invalid='ignore'):
        iou = np.where(union > 0, inter / union, 0.0)
    tp = 0
    if iou.size:
        pi, gi = np.nonzero(iou > 0.5)
        up, ug = set(), set()
        for q in np.argsort(-iou[pi, gi], kind='stable'):
            r, c = int(pi[q]), int(gi[q])
            if r not in up and c not in ug:
                up.add(r); ug.add(c); tp += 1
    pm, gm = p_ids > 0, g_ids > 0
    both = int((pm & gm).sum())
    return [both, int(pm.sum()) - both, int(gm.sum()) - both], [tp, len(pred) - tp, len(gt) - tp]


def score_walls(pred, gt, w, h):
    pm, gm = wall_mask(pred, w, h), wall_mask(gt, w, h)
    both = int((pm & gm).sum())
    return [both, int(pm.sum()) - both, int(gm.sum()) - both]


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

def _ring(x):
    """A ring from [[x, y], ...] or from {"outer": ..., "holes": ...}; holes returned apart."""
    if isinstance(x, dict):
        return x.get('outer') or [], list(x.get('holes') or [])
    return x, []


def load_prediction(path):
    """(objects [(class, shape or None)], area rings, area connector flags, walls, not_supported)."""
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding='utf-8'))
    objects, areas, conn = [], [], []
    for r in d.get('areas') or []:
        outer, _ = _ring(r)
        areas.append(np.asarray(outer, dtype=np.float64).reshape(-1, 2)); conn.append(False)
    for o in d.get('objects') or []:
        cls = o.get('class') or ''
        cls = 'Window' if cls in WINDOW_SUBTYPES else cls
        pg, b = o.get('polygon'), o.get('bbox')
        shape = ring = None
        try:
            if pg and len(pg) >= 3:
                ring = np.asarray(pg, dtype=np.float64).reshape(-1, 2); shape = poly(ring)
            elif b and len(b) == 4:
                b = [float(v) for v in b]
                x0, y0, x1, y1 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
                shape = sbox(x0, y0, x1, y1)
                ring = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        except Exception:
            shape = ring = None
        objects.append((cls, shape))
        if ring is not None and (cls in AREA_PLAIN or cls in AREA_CONNECTORS):
            areas.append(ring); conn.append(cls in AREA_CONNECTORS)
    walls = [_ring(r) for r in d.get('walls') or []]
    return objects, areas, conn, walls, list(d.get('not_supported') or [])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def annotation_as_prediction(S, obj_shapes):
    """The annotation fed back as a prediction (the --self-test): a perfect system."""
    objects = [(lab, poly(ring)) for lab, ring, _ in obj_shapes if lab in OBJECTS]
    areas = [ring for lab, ring, _ in S['shapes'] if lab in AREA_PLAIN + AREA_CONNECTORS]
    conn = [lab in AREA_CONNECTORS for lab, ring, _ in S['shapes'] if lab in AREA_PLAIN + AREA_CONNECTORS]
    walls = [(ring, []) for lab, ring, _ in S['shapes'] if lab == 'Wall']
    return objects, areas, conn, walls, []


def main():
    global OPENING_CREDIT
    ap = argparse.ArgumentParser(description='Score predictions on AEC-Geometric-Bench-15.')
    ap.add_argument('--pred', help='folder of <sheet>.json predictions')
    ap.add_argument('--gt', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dataset'))
    ap.add_argument('--annotations', help='annotation XML (default <gt>/annotations_15_scoring_ready.xml)')
    ap.add_argument('--review', help='review ledger (default <gt>/annotation_review.json)')
    ap.add_argument('--no-review', action='store_true', help='score against the annotation as exported')
    ap.add_argument('--not-supported', default='', help='comma list of Elevator,Stairs the system has no class for')
    ap.add_argument('--no-opening-credit', action='store_true',
                    help='ignore delivered Opening objects: an annotated window is found only by a delivered window')
    ap.add_argument('--name', default='system')
    ap.add_argument('--json', help='write per-sheet results to this file')
    ap.add_argument('--self-test', action='store_true', help='score the annotation against itself')
    a = ap.parse_args()
    if a.no_opening_credit:
        OPENING_CREDIT = False
    if not a.pred and not a.self_test:
        ap.error('--pred is required (or --self-test)')
    if int(shapely.__version__.split('.')[0]) < 2:
        raise SystemExit('shapely >= 2.0 is required')

    gt = load_annotation(a.annotations or os.path.join(a.gt, 'annotations_15_scoring_ready.xml'))
    review = {} if a.no_review else json.load(open(a.review or os.path.join(a.gt, 'annotation_review.json'), encoding='utf-8'))
    unknown = sorted({e['sheet'] for k in ('fixes', 'added', 'not_scored') for e in review.get(k, [])} - set(gt))
    if unknown:
        raise SystemExit(f'review ledger names sheets that are not in the annotation: {unknown}')

    not_supported = {c.strip() for c in a.not_supported.split(',') if c.strip()}
    per_sheet = {}; missing = []; skipped = 0
    for name in sorted(gt):
        S = gt[name]
        obj_shapes, ns_boxes = apply_review(S['shapes'], review, name)
        pr = annotation_as_prediction(S, obj_shapes) if a.self_test else \
            load_prediction(os.path.join(a.pred, f'{name}.json'))
        if pr is None:
            missing.append(name); pr = ([], [], [], [], [])
        objects, p_areas, p_conn, p_walls, nosup = pr
        not_supported |= set(nosup)
        counts, extra_fp, unscored, sk = score_objects(objects, obj_shapes, ns_boxes)
        skipped += sk

        gap = AREA_GAP * min(S['w'], S['h'])
        g_rings = [ring for lab in AREA_PLAIN + AREA_CONNECTORS for l2, ring, _ in S['shapes'] if l2 == lab]
        g_conn = [lab in AREA_CONNECTORS for lab in AREA_PLAIN + AREA_CONNECTORS for l2, _, _ in S['shapes'] if l2 == lab]
        area_px, area_inst = score_areas(merge_connectors(p_areas, p_conn, gap),
                                         merge_connectors(g_rings, g_conn, gap), S['w'], S['h'])
        wall_px = score_walls(p_walls, [(ring, []) for lab, ring, _ in S['shapes'] if lab == 'Wall'], S['w'], S['h'])
        per_sheet[name] = {'file': name not in missing, 'objects': counts, 'outside_vocabulary_fp': extra_fp,
                           'not_scored_by_ledger': unscored, 'wall_pixel': wall_px, 'area_pixel': area_px,
                           'area_instance': area_inst}

    bad = sorted(not_supported - set(MAY_BE_UNSUPPORTED))
    if bad:
        raise SystemExit(f'only {", ".join(MAY_BE_UNSUPPORTED)} can be marked not supported, got {bad}')
    scored = [c for c in OBJECTS if c not in not_supported]

    def sheet_total(r):
        t = [sum(r['objects'][c][i] for c in scored) for i in range(3)]
        t[1] += r['outside_vocabulary_fp']
        return t

    cls = {c: [sum(r['objects'][c][i] for r in per_sheet.values()) for i in range(3)] for c in OBJECTS}
    T = [sum(sheet_total(r)[i] for r in per_sheet.values()) for i in range(3)]
    extra = sum(r['outside_vocabulary_fp'] for r in per_sheet.values())
    unscored = sum(r['not_scored_by_ledger'] for r in per_sheet.values())
    pooled = {k: [sum(r[k][i] for r in per_sheet.values()) for i in range(3)]
              for k in ('wall_pixel', 'area_pixel', 'area_instance')}

    if missing:
        print(f'note: no prediction file for {len(missing)} sheet(s): '
              f'{", ".join(missing[:5])}{" ..." if len(missing) > 5 else ""}')
        print('      they are scored as if the system returned nothing.\n')
    if skipped:
        print(f'note: {skipped} delivered object(s) had neither a usable polygon nor a bbox and were skipped.\n')
    title = 'annotation scored against itself' if a.self_test else a.name
    print(f'{title} on {RULES}' + ('' if review else '  [review ledger NOT applied]') + '\n')
    print(f'{"class":<20}{"TP":>7}{"FP":>7}{"FN":>7}{"P":>8}{"R":>8}{"F1":>8}')
    print('-' * 65)
    for c in OBJECTS:
        v = cls[c]; p, r, f = prf(*v)
        tail = '   not supported, left out of the total' if c in not_supported else f'{p:>8.3f}{r:>8.3f}{f:>8.3f}'
        print(f'{c:<20}{v[0]:>7}{v[1]:>7}{v[2]:>7}{tail}')
    if extra:
        print(f'{"(outside vocabulary)":<20}{"":>7}{extra:>7}')
    p, r, f = prf(*T)
    print('-' * 65)
    print(f'{"OBJECT MICRO":<20}{T[0]:>7}{T[1]:>7}{T[2]:>7}{p:>8.3f}{r:>8.3f}{f:>8.3f}')
    if unscored:
        print(f'{"":<20}{unscored} unmatched delivered object(s) lie in a not-scored box of the ledger: neither TP nor FP')
    print()
    for lab, k in (('wall pixel', 'wall_pixel'), ('area pixel', 'area_pixel')):
        p, r, f = prf(*pooled[k])
        print(f'{lab:<20}{"":>21}{p:>8.3f}{r:>8.3f}{f:>8.3f}')
    v = pooled['area_instance']; p, r, f = prf(*v)
    print(f'{"area instance":<20}{v[0]:>7}{v[1]:>7}{v[2]:>7}{p:>8.3f}{r:>8.3f}{f:>8.3f}')

    if a.json:
        f4 = lambda v: round(prf(*v)[2], 4)
        f6 = lambda v: round(prf(*v)[2], 6)          # pooled figures keep six decimals, so that they round correctly to three
        doc = {'system': title, 'rules': RULES, 'review_ledger_applied': bool(review),
               'not_supported': sorted(not_supported),
               '_fields': 'objects: {class: [tp, fp, fn]}; object_tp/fp/fn: the supported classes plus '
                          'outside_vocabulary_fp; wall_pixel and area_pixel: [pixels in both, delivered only, '
                          'annotation only] at short side 5000; area_instance: [tp, fp, fn].',
               'sheets': {}, 'total': {'objects': cls, 'object_tp': T[0], 'object_fp': T[1], 'object_fn': T[2],
                                       'object_precision': round(prf(*T)[0], 6), 'object_recall': round(prf(*T)[1], 6),
                                       'object_f1': f6(T), 'outside_vocabulary_fp': extra,
                                       'not_scored_by_ledger': unscored,
                                       **{k: v for k, v in pooled.items()},
                                       **{k + '_f1': f6(v) for k, v in pooled.items()}}}
        for name, r in per_sheet.items():
            t = sheet_total(r)
            doc['sheets'][name] = {'prediction_file': r['file'], 'objects': r['objects'],
                                   'outside_vocabulary_fp': r['outside_vocabulary_fp'],
                                   'not_scored_by_ledger': r['not_scored_by_ledger'],
                                   'object_tp': t[0], 'object_fp': t[1], 'object_fn': t[2], 'object_f1': f4(t),
                                   'wall_pixel': r['wall_pixel'], 'wall_pixel_f1': f4(r['wall_pixel']),
                                   'area_pixel': r['area_pixel'], 'area_pixel_f1': f4(r['area_pixel']),
                                   'area_instance': r['area_instance'], 'area_instance_f1': f4(r['area_instance'])}
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, indent=1)
        print(f'\nwrote {a.json}')


if __name__ == '__main__':
    main()
