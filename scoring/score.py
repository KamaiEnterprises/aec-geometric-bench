#!/usr/bin/env python3
"""Score a folder of predictions against the released 15 sheets.

    python3 score.py --pred path/to/predictions [--gt ../dataset]

Predictions: one JSON per sheet, named after the sheet (sheet_01.json ...), each

    {"sheet": "sheet_01",
     "objects": [{"class": "Single Swing Door", "bbox": [x0, y0, x1, y1]}, ...],
     "areas":   [[[x, y], [x, y], ...], ...],
     "walls":   [[[x, y], [x, y], ...], ...]}

Coordinates are pixels in the frame given by `width`/`height` for that sheet in
`manifest.json`. Classes are the eight in `taxonomy.json`; anything else is
counted as a false positive, which is deliberate: a system that invents a class
should not score for it.

Scoring matches the paper exactly. Objects are matched one-to-one at IoU 0.50,
highest IoU first, with no confidence threshold. Areas are the union of Room,
Shaft, Balcony, Elevator and Stairs with touching instances merged, scored both
as a pixel mask and as instances at IoU 0.50. Walls are Wall and Railing merged
and scored as a pixel mask only. The whole page is scored; no region is masked.
"""
import argparse, json, math, os, sys, xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

OBJECTS = ['Single Swing Door', 'Double Swing Door', 'Window', 'Sink',
           'Toilet', 'Bathtub', 'Shower', 'Cooktops']
RASTER_SHORT_SIDE = 5000   # masks compare at this short side, as in the paper's harness


def _poly(pts):
    try:
        p = Polygon(np.asarray(pts, dtype=float).reshape(-1, 2))
        if not p.is_valid:
            p = p.buffer(0)
        return p if (p.is_valid and not p.is_empty and p.area > 0) else None
    except Exception:
        return None


def merge_touching(polys, gap):
    """Union polygons that touch within `gap`. Annotators split one space across
    several adjoining boxes, so without this the metric counts drawing style."""
    geoms = [g for g in polys if g is not None]
    if not geoms:
        return []
    tree = STRtree(geoms)
    parent = list(range(len(geoms)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, g in enumerate(geoms):
        gb = g.buffer(gap)
        for j in tree.query(gb):
            j = int(j)
            if j != i and geoms[j].intersects(gb):
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    comp = {}
    for i in range(len(geoms)):
        comp.setdefault(find(i), []).append(geoms[i])
    out = []
    for v in comp.values():
        u = unary_union(v)
        out.extend(list(u.geoms) if u.geom_type == 'MultiPolygon' else [u])
    return out


def match(pred, gt, thr=0.50):
    """Unique greedy matching, highest IoU first. Returns (tp, fp, fn)."""
    pairs = []
    for i, p in enumerate(pred):
        for j, g in enumerate(gt):
            if not p.intersects(g):
                continue
            inter = p.intersection(g).area
            if inter <= 0:
                continue
            iou = inter / (p.area + g.area - inter)
            if iou > thr:          # strict: uniqueness holds only for IoU > 0.5
                pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    mp, mg = set(), set()
    for _, i, j in pairs:
        if i in mp or j in mg:
            continue
        mp.add(i); mg.add(j)
    return len(mp), len(pred) - len(mp), len(gt) - len(mg)


def raster_scale(w, h):
    s = min(w, h)
    return 1.0 if s <= RASTER_SHORT_SIDE else RASTER_SHORT_SIDE / float(s)


def rasterise(polys, w, h, k):
    im = Image.new('1', (max(1, int(w * k)), max(1, int(h * k))), 0)
    d = ImageDraw.Draw(im)
    for g in polys:
        for gg in (list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]):
            pts = [(x * k, y * k) for x, y in gg.exterior.coords]
            if len(pts) >= 3:
                d.polygon(pts, fill=1)
    return np.array(im, dtype=bool)


def pixel_counts(pred, gt, w, h):
    k = raster_scale(w, h)
    mp, mg = rasterise(pred, w, h, k), rasterise(gt, w, h, k)
    inter = int((mp & mg).sum())
    return inter, int(mp.sum()) - inter, int(mg.sum()) - inter


def f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def load_gt(gt_dir):
    root = ET.parse(os.path.join(gt_dir, 'annotations_15_scoring_ready.xml')).getroot()
    sheets = {}
    for img in root.findall('image'):
        name = os.path.splitext(img.get('name'))[0]
        w, h = float(img.get('width')), float(img.get('height'))
        objs = {c: [] for c in OBJECTS}
        areas, walls = [], []
        for ch in img:
            lab = ch.get('label')
            if ch.tag == 'box':
                b = [float(ch.get(a)) for a in ('xtl', 'ytl', 'xbr', 'ybr')]
                ring = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
                # CVAT stores an oriented box as an axis-aligned box plus
                # `rotation`, in DEGREES clockwise about the centre. 31 boxes in
                # this release carry one; ignoring it would read them as
                # axis-aligned and silently change their envelopes.
                rot = float(ch.get('rotation', '0') or 0.0)
                if abs(rot % 360.0) > 1e-6:
                    cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                    t = math.radians(rot)
                    ct, st = math.cos(t), math.sin(t)
                    ring = [(cx + (x - cx) * ct - (y - cy) * st,
                             cy + (x - cx) * st + (y - cy) * ct) for x, y in ring]
            elif ch.tag == 'polygon':
                ring = [tuple(map(float, q.split(','))) for q in ch.get('points').split(';')]
            else:
                continue
            g = _poly(ring)
            if g is None:
                continue
            if lab in objs:
                # Objects are matched on the axis-aligned envelope on BOTH sides.
                # All but 31 ground-truth boxes carry zero rotation, so scoring an
                # oriented prediction against the true polygon would penalise it
                # for being more precise than the annotation. The 31 oriented
                # boxes are rotated first, then enveloped, so orientation changes
                # the envelope without changing the matching rule.
                x0, y0, x1, y1 = g.bounds
                objs[lab].append(_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
            elif lab == 'Area':
                areas.append(g)
            elif lab == 'Wall':
                walls.append(g)
        sheets[name] = {'w': w, 'h': h, 'objects': objs, 'areas': areas, 'walls': walls}
    return sheets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred', required=True, help='folder of <sheet>.json predictions')
    ap.add_argument('--gt', default=os.path.join(os.path.dirname(__file__), '..', 'dataset'))
    ap.add_argument('--name', default='system')
    a = ap.parse_args()

    gt = load_gt(a.gt)
    obj = {c: [0, 0, 0] for c in OBJECTS}
    area_px = [0, 0, 0]; area_inst = [0, 0, 0]; wall_px = [0, 0, 0]
    missing = []

    for name in sorted(gt):
        G = gt[name]
        gap = 0.0015 * min(G['w'], G['h'])
        f = os.path.join(a.pred, f'{name}.json')
        if not os.path.exists(f):
            missing.append(name)
            P = {'objects': [], 'areas': [], 'walls': []}
        else:
            P = json.load(open(f))

        by_class = {c: [] for c in OBJECTS}
        extra_fp = 0
        for o in P.get('objects') or []:
            c, b = o.get('class'), o.get('bbox')
            if not b or len(b) != 4:
                continue
            if c in by_class:
                g = _poly([(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])])
                if g is not None:
                    by_class[c].append(g)
            else:
                extra_fp += 1            # a class outside the taxonomy is a false positive
        for c in OBJECTS:
            tp, fp, fn = match(by_class[c], G['objects'][c])
            obj[c][0] += tp; obj[c][1] += fp; obj[c][2] += fn
        obj[OBJECTS[0]][1] += extra_fp

        pa = merge_touching([_poly(r) for r in (P.get('areas') or [])], gap)
        ga = merge_touching(G['areas'], gap)
        tp, fp, fn = match(pa, ga)
        area_inst[0] += tp; area_inst[1] += fp; area_inst[2] += fn
        i, p_, g_ = pixel_counts(pa, ga, G['w'], G['h'])
        area_px[0] += i; area_px[1] += p_; area_px[2] += g_

        pw = [g for g in (_poly(r) for r in (P.get('walls') or [])) if g is not None]
        i, p_, g_ = pixel_counts(pw, G['walls'], G['w'], G['h'])
        wall_px[0] += i; wall_px[1] += p_; wall_px[2] += g_

    if missing:
        print(f'note: no prediction file for {len(missing)} sheet(s): '
              f'{", ".join(missing[:5])}{" ..." if len(missing) > 5 else ""}')
        print('      they are scored as if the system returned nothing.\n')

    print(f'{a.name} on AEC-Geometric-Bench-15\n')
    print(f'{"class":<20}{"TP":>7}{"FP":>7}{"FN":>7}{"P":>8}{"R":>8}{"F1":>8}')
    print('-' * 65)
    T = [0, 0, 0]
    for c in OBJECTS:
        v = obj[c]; p, r, f = f1(*v)
        for k in range(3):
            T[k] += v[k]
        print(f'{c:<20}{v[0]:>7}{v[1]:>7}{v[2]:>7}{p:>8.3f}{r:>8.3f}{f:>8.3f}')
    p, r, f = f1(*T)
    print('-' * 65)
    print(f'{"OBJECT MICRO":<20}{T[0]:>7}{T[1]:>7}{T[2]:>7}{p:>8.3f}{r:>8.3f}{f:>8.3f}')
    print()
    for lab, v in (('wall pixel', wall_px), ('area pixel', area_px), ('area instance', area_inst)):
        p, r, f = f1(*v)
        print(f'{lab:<20}{"":>21}{p:>8.3f}{r:>8.3f}{f:>8.3f}')


if __name__ == '__main__':
    main()
