"""Grid maze router for the temple boards (own code, no external autorouter).

* 0.05 mm grid, routing layers F.Cu / In2.Cu / B.Cu (In1.Cu is a solid GND plane).
* Clearance is enforced with Euclidean distance transforms of every other net's copper, so each cell
  centre a track passes through keeps (clearance + width/2 + eps) from foreign copper and the edge.
* Multi-pin nets are grown as a tree (nearest pad first) with multi-source A*; vias have a cost.
* GND pads get a short fan-out via to the In1 plane; free space later gets GND pours + stitching vias.
KiCad's own DRC is the final judge (build.py runs it).
"""
import heapq
import math

import numpy as np
import pcbnew
import shapely
from scipy import ndimage
from shapely.geometry import Polygon, Point, LineString

from pcbgen import OX, OY, mm, local

RES = 0.05
EPS = 0.065
LAYERS = [pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
LNAME = ['F.Cu', 'In2.Cu', 'B.Cu']
VIA_D, VIA_DRILL = 0.4, 0.2
EDGE_CLR = 0.25
DIRS = [(0, 1, 1.0), (1, 0, 1.0), (0, -1, 1.0), (-1, 0, 1.0),
        (1, 1, 1.41421), (1, -1, 1.41421), (-1, 1, 1.41421), (-1, -1, 1.41421)]


class Router:
    def __init__(self, bb, board_poly):
        self.bb = bb
        self.b = bb.b
        x0, y0, x1, y1 = board_poly.bounds
        self.x0, self.y0 = x0 - 0.5, y0 - 0.5
        self.W = int((x1 - self.x0 + 0.5) / RES) + 1
        self.H = int((y1 - self.y0 + 0.5) / RES) + 1
        self.poly = board_poly
        ys = self.y0 + np.arange(self.H) * RES
        xs = self.x0 + np.arange(self.W) * RES
        self.X, self.Y = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(board_poly, self.X, self.Y)
        # distance to the board edge; the nearest outside cell centre can be up to one cell beyond the real edge
        self.edge_dist = ndimage.distance_transform_edt(inside) * RES - RES
        self.owner = np.zeros((3, self.H, self.W), dtype=np.int32)          # net code per layer
        self.routed = np.zeros((3, self.H, self.W), dtype=np.int32)         # tracks/vias only
        self.novia = np.zeros((self.H, self.W), dtype=bool)                 # pad areas / keepouts
        self.netcode = {}
        self.blocked_for = {}        # net name -> extra blocked mask (keepouts)
        self.vias = []               # (x, y, net)
        self.reservations = {}       # net -> list of reserved escape geometries
        self.fixed = {}              # net -> escape stubs / plane vias that rip-up must keep
        self.items = []              # routed copper, turned into KiCad objects by flush()
        self.dir_cost = {}           # layer -> (horizontal, vertical, diagonal) step costs (default 1, 1, 1.414)
        self.via_block = np.zeros((self.H, self.W), dtype=np.int16)        # same-net via spacing
        self.track_count = 0
        self.free_code = 100000
        self._load_copper()
        self._load_rule_areas()

    # ------------------------------------------------------------------ geometry helpers
    def code(self, net):
        if net not in self.netcode:
            self.netcode[net] = len(self.netcode) + 1
        return self.netcode[net]

    def cell(self, x, y):
        return int(round((y - self.y0) / RES)), int(round((x - self.x0) / RES))

    def xy(self, i, j):
        return self.x0 + j * RES, self.y0 + i * RES

    def _raster(self, geom, layer_idx, code, grow=0.0):
        g = geom.buffer(grow) if grow else geom
        minx, miny, maxx, maxy = g.bounds
        i0, j0 = self.cell(minx, miny)
        i1, j1 = self.cell(maxx, maxy)
        i0, j0 = max(i0 - 1, 0), max(j0 - 1, 0)
        i1, j1 = min(i1 + 2, self.H), min(j1 + 2, self.W)
        if i1 <= i0 or j1 <= j0:
            return
        m = shapely.contains_xy(g, self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1])
        for L in (range(3) if layer_idx is None else [layer_idx]):
            view = self.owner[L, i0:i1, j0:j1]
            if grow:
                inner = shapely.contains_xy(geom, self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1])
                view[inner] = code
                view[m & (view == 0)] = code          # keep-off halo only on free cells
            else:
                view[m] = code
        return (i0, i1, j0, j1, m)

    def _pad_poly(self, pad, layer):
        ps = pad.GetEffectivePolygon(layer, pcbnew.ERROR_INSIDE)
        polys = []
        for k in range(ps.OutlineCount()):
            ol = ps.Outline(k)
            pts = [local(ol.CPoint(i)) for i in range(ol.PointCount())]
            if len(pts) >= 3:
                polys.append(Polygon(pts))
        return shapely.union_all(polys) if polys else None

    def _load_copper(self):
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                net = pad.GetNetname().lstrip('/')
                code = self.code(net) if net else self._free()
                lset = pad.GetLayerSet()
                for li, L in enumerate(LAYERS):
                    if lset.Contains(L):
                        g = self._pad_poly(pad, L)
                        if g is not None and not g.is_empty:
                            self._raster(g, li, code)
                            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                                r = self._raster_mask(g.buffer(0.05))
                                if r:
                                    i0, i1, j0, j1, m = r
                                    self.novia[i0:i1, j0:j1] |= m
                if pad.GetAttribute() in (pcbnew.PAD_ATTRIB_NPTH, pcbnew.PAD_ATTRIB_PTH):
                    d = pcbnew.ToMM(pad.GetDrillSize().x)
                    x, y = local(pad.GetPosition())
                    hole = Point(x, y).buffer(d / 2 + 0.1)         # hole clearance 0.2 incl. 0.1 netclass
                    if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                        self._raster(hole, None, self._free())
                    r = self._raster_mask(Point(x, y).buffer(d / 2 + 0.25 + VIA_DRILL / 2))
                    if r:
                        i0, i1, j0, j1, m = r
                        self.novia[i0:i1, j0:j1] |= m

    def _load_rule_areas(self):
        """Keep-out areas that come inside footprints (e.g. the SHT4x sensor window) block tracks/vias too."""
        for fp in self.b.GetFootprints():
            for z in fp.Zones():
                if not z.GetIsRuleArea():
                    continue
                ol = z.Outline()
                for k in range(ol.OutlineCount()):
                    o = ol.Outline(k)
                    poly = Polygon([local(o.CPoint(i)) for i in range(o.PointCount())])
                    layers = [li for li, L in enumerate(LAYERS) if z.GetLayerSet().Contains(L)]
                    if z.GetDoNotAllowTracks() and layers:
                        self.block_area(poly.buffer(0.17), nets_allowed=(), novia=False, layers=tuple(layers))
                    if z.GetDoNotAllowVias():
                        self.no_vias(poly.buffer(VIA_D / 2 + 0.03))

    def _free(self):
        self.free_code += 1
        return self.free_code

    def _raster_mask(self, geom):
        minx, miny, maxx, maxy = geom.bounds
        i0, j0 = self.cell(minx, miny)
        i1, j1 = self.cell(maxx, maxy)
        i0, j0 = max(i0 - 1, 0), max(j0 - 1, 0)
        i1, j1 = min(i1 + 2, self.H), min(j1 + 2, self.W)
        if i1 <= i0 or j1 <= j0:
            return None
        return (i0, i1, j0, j1, shapely.contains_xy(geom, self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1]))

    def reserve_escapes(self, ref, length=0.45, direction=None, skip_nets=('GND',)):
        """Reserve a short exit corridor from every pad of `ref` (F.Cu) for that pad's own net, so other
        nets cannot box a pin in.  direction: None = radially outward from the footprint centre, or a
        (dx, dy) unit vector in board coordinates."""
        fp = self.bb.fps[ref]
        cx, cy = local(fp.GetPosition())
        for pad in fp.Pads():
            net = pad.GetNetname().lstrip('/')
            if not net or net in skip_nets or not pad.IsOnLayer(pcbnew.F_Cu):
                continue
            px, py = local(pad.GetPosition())
            if direction is None:
                dx, dy = px - cx, py - cy
                if abs(dx) > abs(dy):
                    dx, dy = (1 if dx > 0 else -1), 0
                else:
                    dx, dy = 0, (1 if dy > 0 else -1)
            else:
                dx, dy = direction
            g = self._pad_poly(pad, pcbnew.F_Cu)
            if g is None:
                continue
            # start at the pad edge along the escape direction
            minx, miny, maxx, maxy = g.bounds
            ex = maxx if dx > 0 else minx if dx < 0 else px
            ey = maxy if dy > 0 else miny if dy < 0 else py
            seg = LineString([(ex - dx * 0.05, ey - dy * 0.05), (ex + dx * length, ey + dy * length)]).buffer(0.05)
            self._raster(seg, 0, self.code(net), grow=0.0001)
            self.reservations.setdefault(net, []).append(seg)

    def block_area(self, poly, nets_allowed=(), novia=True, layers=(0, 1, 2)):
        """forbid tracks of all nets except nets_allowed inside poly on `layers` (and vias if novia)."""
        r = self._raster_mask(poly)
        if not r:
            return
        i0, i1, j0, j1, m = r
        mask = np.zeros((self.H, self.W), dtype=bool)
        mask[i0:i1, j0:j1] = m
        self.blocked_for.setdefault('*', []).append((mask, set(nets_allowed), tuple(layers)))
        if novia:
            self.novia |= mask

    def no_vias(self, poly):
        """forbid vias inside poly (tracks still allowed)."""
        r = self._raster_mask(poly)
        if r:
            i0, i1, j0, j1, m = r
            self.novia[i0:i1, j0:j1] |= m

    # ------------------------------------------------------------------ add copper
    def add_track(self, net, pts, layer_idx, w, keepoff=0.0):
        """Copper is kept as data (self.items) until flush(), so routing rounds can be undone cheaply."""
        code = self.code(net)
        self.items.append(('T', net, [tuple(p) for p in pts], layer_idx, w))
        self.track_count += max(len(pts) - 1, 0)
        g = LineString(pts).buffer(w / 2, cap_style='round') if len(pts) > 1 else Point(pts[0]).buffer(w / 2)
        r = self._raster(g, layer_idx, code, grow=keepoff)
        r2 = self._raster_mask(g)
        if r2:
            i0, i1, j0, j1, m = r2
            self.routed[layer_idx, i0:i1, j0:j1][m] = code

    def _via_block(self, x, y, delta):
        # same-net via spacing (hole to hole 0.25 mm); other nets are kept away by the copper clearance
        r = self._raster_mask(Point(x, y).buffer(VIA_DRILL / 2 + 0.25 + VIA_DRILL / 2))
        if r:
            i0, i1, j0, j1, m = r
            self.via_block[i0:i1, j0:j1][m] += delta

    def add_via(self, net, x, y):
        self.items.append(('V', net, x, y))
        self._raster(Point(x, y).buffer(VIA_D / 2), None, self.code(net))
        r2 = self._raster_mask(Point(x, y).buffer(VIA_D / 2))
        if r2:
            i0, i1, j0, j1, m = r2
            for L in range(3):
                self.routed[L, i0:i1, j0:j1][m] = self.code(net)
        self._via_block(x, y, 1)
        self.vias.append((x, y, net))

    def flush(self):
        """Create the KiCad tracks and vias for everything routed."""
        for it in self.items:
            if it[0] == 'T':
                _, net, pts, layer_idx, w = it
                for a, b in zip(pts[:-1], pts[1:]):
                    t = pcbnew.PCB_TRACK(self.b)
                    t.SetStart(pcbnew.VECTOR2I(mm(OX + a[0]), mm(OY + a[1])))
                    t.SetEnd(pcbnew.VECTOR2I(mm(OX + b[0]), mm(OY + b[1])))
                    t.SetWidth(mm(w))
                    t.SetLayer(LAYERS[layer_idx])
                    t.SetNet(self.bb.net(net))
                    self.b.Add(t)
            else:
                _, net, x, y = it
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(pcbnew.VECTOR2I(mm(OX + x), mm(OY + y)))
                v.SetWidth(mm(VIA_D))
                v.SetDrill(mm(VIA_DRILL))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                v.SetNet(self.bb.net(net))
                self.b.Add(v)
        self.items = []

    def prune(self, keep_nets=('GND',)):
        """Delete copper that leads nowhere: vias that join fewer than two layers, and track segments with an end
        that touches no other copper of their net (repeated until nothing changes).  Nets with planes/pours
        (keep_nets) are left alone.  Returns (vias removed, segments removed)."""
        tol = 0.005
        items = []
        for it in self.items:                               # one item per straight segment
            if it[0] == 'T':
                _, net, pts, L, w = it
                for a, b in zip(pts[:-1], pts[1:]):
                    if a != b:
                        items.append(('T', net, [a, b], L, w))
            else:
                items.append(it)
        pads = {}
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                net = pad.GetNetname().lstrip('/')
                if not net or net in keep_nets:
                    continue
                for li, L in enumerate(LAYERS):
                    if pad.GetLayerSet().Contains(L):
                        g = self._pad_poly(pad, L)
                        if g is not None and not g.is_empty:
                            pads.setdefault((net, li), []).append(g)
        nv = ns = 0
        while True:
            segs, vias = {}, {}
            for k, it in enumerate(items):
                if it[1] in keep_nets:
                    continue
                if it[0] == 'T':
                    segs.setdefault((it[1], it[3]), []).append((k, LineString(it[2]), it[4]))
                else:
                    vias.setdefault(it[1], []).append((k, Point(it[2], it[3])))
            drop = set()
            for k, it in enumerate(items):
                if it[1] in keep_nets:
                    continue
                net = it[1]
                if it[0] == 'V':
                    pt = Point(it[2], it[3])
                    nl = 0
                    for li in range(3):
                        hit = any(g.distance(pt) <= tol for g in pads.get((net, li), []))
                        hit = hit or any(ls.distance(pt) <= w / 2 + VIA_D / 2 + tol
                                         for _, ls, w in segs.get((net, li), []))
                        nl += hit
                    if nl < 2:
                        drop.add(k)
                    continue
                _, _, (a, b), li, w = it
                for end in (a, b):
                    pt = Point(end)
                    ok = any(g.distance(pt) <= tol for g in pads.get((net, li), []))
                    ok = ok or any(vp.distance(pt) <= VIA_D / 2 + tol for _, vp in vias.get(net, []))
                    ok = ok or any(k2 != k and ls.distance(pt) <= w2 / 2 + tol
                                   for k2, ls, w2 in segs.get((net, li), []))
                    if not ok:
                        drop.add(k)
                        break
            if not drop:
                break
            for k in drop:
                if items[k][0] == 'V':
                    nv += 1
                    self.vias = [v for v in self.vias if (v[0], v[1], v[2]) != (items[k][2], items[k][3], items[k][1])]
                else:
                    ns += 1
            items = [it for k, it in enumerate(items) if k not in drop]
        self.items = items
        return nv, ns

    def snapshot(self):
        return (list(self.items), self.owner.copy(), self.routed.copy(), self.via_block.copy(), list(self.vias),
                self.track_count, {k: list(v) for k, v in self.fixed.items()})

    def restore(self, snap):
        items, owner, routed, vb, vias, tc, fixed = snap
        self.items = list(items)
        self.owner[...] = owner
        self.routed[...] = routed
        self.via_block[...] = vb
        self.vias = list(vias)
        self.track_count = tc
        self.fixed = {k: list(v) for k, v in fixed.items()}

    # ------------------------------------------------------------------ masks
    def masks(self, net, w, clr, window, layers=(0, 1, 2)):
        # compute distances on a window grown by 1.2 mm so copper just outside the window is respected
        W0 = window
        pad = int(1.2 / RES)
        i0, i1, j0, j1 = max(W0[0] - pad, 0), min(W0[1] + pad, self.H), max(W0[2] - pad, 0), min(W0[3] + pad, self.W)
        ci0, ci1, cj0, cj1 = W0[0] - i0, W0[1] - i0, W0[2] - j0, W0[3] - j0
        code = self.code(net)
        ok = []
        need = clr + w / 2 + EPS
        need_v = clr + VIA_D / 2 + EPS
        via_ok = (~self.novia[i0:i1, j0:j1]) & (self.via_block[i0:i1, j0:j1] == 0) & \
            (self.edge_dist[i0:i1, j0:j1] >= EDGE_CLR + VIA_D / 2 + 0.05)
        extra = [np.zeros((i1 - i0, j1 - j0), dtype=bool) for _ in range(3)]
        for mask, allowed, lys in self.blocked_for.get('*', []):
            if net not in allowed:
                for L in lys:
                    extra[L] |= mask[i0:i1, j0:j1]
        for L in range(3):
            own = self.owner[L, i0:i1, j0:j1]
            other = (own != 0) & (own != code)
            dist = ndimage.distance_transform_edt(~other) * RES if other.any() else np.full(own.shape, 99.0)
            m = (dist >= need) & (self.edge_dist[i0:i1, j0:j1] >= EDGE_CLR + w / 2 + EPS) & ~extra[L]
            if L not in layers:
                m[:] = False
            ok.append(m[ci0:ci1, cj0:cj1])
            via_ok &= dist >= need_v
        if len(layers) == 3:
            via_ok &= ~(extra[0] & extra[1] & extra[2])
        return ok, via_ok[ci0:ci1, cj0:cj1]

    # ------------------------------------------------------------------ search
    def route_pair(self, net, src_cells, dst_cells, w, clr, layers=(0, 1, 2), via_cost=24.0, margin=3.0,
                   allow_via=True):
        """src/dst: lists of (L, i, j). Returns list of (L, i, j) or None."""
        allc = src_cells + dst_cells
        imin = min(c[1] for c in allc); imax = max(c[1] for c in allc)
        jmin = min(c[2] for c in allc); jmax = max(c[2] for c in allc)
        m = int(margin / RES)
        win = (max(imin - m, 0), min(imax + m + 1, self.H), max(jmin - m, 0), min(jmax + m + 1, self.W))
        i0, i1, j0, j1 = win
        ok, via_ok = self.masks(net, w, clr, win, layers)
        h, wd = i1 - i0, j1 - j0
        okb = [bytearray(np.ascontiguousarray(o, dtype=np.uint8).tobytes()) for o in ok]
        vob = bytearray(np.ascontiguousarray(via_ok, dtype=np.uint8).tobytes())
        code = self.code(net)
        dset = set()
        for (L, i, j) in dst_cells:
            if i0 <= i < i1 and j0 <= j < j1:
                dset.add((L, (i - i0) * wd + (j - j0)))
        if not dset:
            return None
        dpts = np.array([[(k // wd), (k % wd)] for _, k in dset])
        tci, tcj = dpts[:, 0].mean(), dpts[:, 1].mean()
        rad = math.hypot(dpts[:, 0].max() - dpts[:, 0].min(), dpts[:, 1].max() - dpts[:, 1].min()) / 2

        def hfun(k):
            di = abs(k // wd - tci); dj = abs(k % wd - tcj)
            return max(0.0, (max(di, dj) + 0.41421 * min(di, dj)) - rad)
        INF = float('inf')
        g = {}
        prev = {}
        heap = []
        for (L, i, j) in src_cells:
            if not (i0 <= i < i1 and j0 <= j < j1) or L not in layers:
                continue
            k = (i - i0) * wd + (j - j0)
            st = (L, k)
            if g.get(st, INF) > 0:
                g[st] = 0.0
                prev[st] = None
                heapq.heappush(heap, (hfun(k), 0.0, L, k))
        found = None
        exp = 0
        while heap:
            f, gc, L, k = heapq.heappop(heap)
            if gc > g.get((L, k), INF):
                continue
            if (L, k) in dset:
                found = (L, k)
                break
            exp += 1
            if exp > 2_500_000:
                break
            ci, cj = divmod(k, wd)
            row = okb[L]
            dc = self.dir_cost.get(L)
            for di, dj, cst in DIRS:
                if dc is not None:
                    cst = dc[0] if di == 0 else dc[1] if dj == 0 else dc[2]
                ni, nj = ci + di, cj + dj
                if ni < 0 or nj < 0 or ni >= h or nj >= wd:
                    continue
                nk = ni * wd + nj
                if not row[nk] and (L, nk) not in dset:
                    continue
                ng = gc + cst
                if ng < g.get((L, nk), INF):
                    g[(L, nk)] = ng
                    prev[(L, nk)] = (L, k)
                    heapq.heappush(heap, (ng + hfun(nk), ng, L, nk))
            if allow_via and vob[k]:
                for L2 in layers:
                    if L2 == L or not okb[L2][k]:
                        continue
                    ng = gc + via_cost
                    if ng < g.get((L2, k), INF):
                        g[(L2, k)] = ng
                        prev[(L2, k)] = (L, k)
                        heapq.heappush(heap, (ng + hfun(k), ng, L2, k))
        if not found:
            self.last_fail = (win, wd, list(g.keys()), dst_cells, src_cells)
            return None
        path = []
        st = found
        while st is not None:
            L, k = st
            path.append((L, k // wd + i0, k % wd + j0))
            st = prev[st]
        path.reverse()
        return path

    # ------------------------------------------------------------------ commit
    def commit(self, net, path, w, keepoff=0.0):
        """turn a cell path into tracks + vias (merging collinear steps)."""
        segs = []
        cur = [path[0]]
        for c in path[1:]:
            if c[0] != cur[-1][0]:
                segs.append(cur)
                cur = [c]
            else:
                cur.append(c)
        segs.append(cur)
        for si, s in enumerate(segs):
            L = s[0][0]
            pts = [self.xy(s[0][1], s[0][2])]
            for a, b, c in zip(s[:-2], s[1:-1], s[2:]):
                if (b[1] - a[1], b[2] - a[2]) != (c[1] - b[1], c[2] - b[2]):
                    pts.append(self.xy(b[1], b[2]))
            if len(s) > 1:
                pts.append(self.xy(s[-1][1], s[-1][2]))
            if len(pts) > 1:
                self.add_track(net, pts, L, w, keepoff)
            if si + 1 < len(segs):
                x, y = self.xy(s[-1][1], s[-1][2])
                self.add_via(net, x, y)

    def net_cells(self, net):
        code = self.code(net)
        cells = []
        for L in range(3):
            ii, jj = np.nonzero(self.routed[L] == code)
            cells += [(L, int(i), int(j)) for i, j in zip(ii, jj)]
        return cells

    def pad_cells(self, ref, num):
        fp = self.bb.fps[ref]
        out = []
        for pad in fp.Pads():
            if pad.GetNumber() != num:
                continue
            lset = pad.GetLayerSet()
            for li, L in enumerate(LAYERS):
                if lset.Contains(L):
                    g = self._pad_poly(pad, L)
                    if g is None:
                        continue
                    r = self._raster_mask(g.buffer(-0.02) if g.area > 0.05 else g)
                    if r:
                        i0, i1, j0, j1, m = r
                        ii, jj = np.nonzero(m)
                        out += [(li, int(i + i0), int(j + j0)) for i, j in zip(ii, jj)]
        return out

    def islands(self, net, pc):
        """Connected copper of `net` (tracks, vias and the pads in pc = {pin: cells}).
        Layers are labelled separately and only joined at this net's vias and at plated through-hole pads,
        so a track crossing another one of the same net on a different layer does not count as a joint.
        Returns ({pin: island id}, {island id: [(L, i, j), ...]})."""
        code = self.code(net)
        allc = [c for cs in pc.values() for c in cs]
        rc = [np.nonzero(self.routed[L] == code) for L in range(3)]
        ii = np.concatenate([r[0] for r in rc] + [np.array([c[1] for c in allc], dtype=np.int64)])
        jj = np.concatenate([r[1] for r in rc] + [np.array([c[2] for c in allc], dtype=np.int64)])
        if not len(ii):
            return {}, {}
        i0, i1 = max(int(ii.min()) - 2, 0), min(int(ii.max()) + 3, self.H)
        j0, j1 = max(int(jj.min()) - 2, 0), min(int(jj.max()) + 3, self.W)
        labs, off = [], 0
        for L in range(3):
            m = self.routed[L, i0:i1, j0:j1] == code
            for c in allc:
                if c[0] == L:
                    m[c[1] - i0, c[2] - j0] = True
            lab, n = ndimage.label(m, structure=np.ones((3, 3), dtype=int))
            lab[lab > 0] += off
            labs.append(lab)
            off += n
        parent = list(range(off + 1))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(ls):
            ls = [find(int(x)) for x in ls if x]
            for x in ls[1:]:
                parent[x] = ls[0]
        for (x, y, n) in self.vias:
            if n != net:
                continue
            i, j = self.cell(x, y)
            if i0 <= i < i1 and j0 <= j < j1:
                union([labs[L][i - i0, j - j0] for L in range(3)])
        for p, cs in pc.items():
            first = {}
            for c in cs:
                first.setdefault(c[0], c)
            if len(first) > 1:                               # through-hole pad: joins its layers
                union([labs[L][c[1] - i0, c[2] - j0] for L, c in first.items()])
        comp_of = {}
        for p, cs in pc.items():
            if cs:
                c = cs[0]
                comp_of[p] = find(int(labs[c[0]][c[1] - i0, c[2] - j0]))
        cells = {}
        for L in range(3):
            li, lj = np.nonzero(labs[L])
            roots = [find(int(v)) for v in labs[L][li, lj]]
            for r, a, b in zip(roots, li, lj):
                cells.setdefault(r, []).append((L, int(a) + i0, int(b) + j0))
        return comp_of, cells

    def route_net(self, net, pins, w, clr, layers=(0, 1, 2), allow_via=True, widths=None, keepoff=0.0):
        """pins: list of (ref, pad). Grow a tree; returns list of failed pins.
        Copper placed earlier (escape stubs, fan-out vias) only counts as part of the tree once it is really
        joined to it; a pin that already has its own stub is reached through that stub's copper."""
        widths = widths or [w]
        pend = list(dict.fromkeys(pins))
        pc = {p: self.pad_cells(*p) for p in pend}
        pend = [p for p in pend if pc[p]]
        if len(pend) < 2:
            return []
        # start with the pin closest to the centroid
        cen = np.mean([[np.mean([c[1] for c in pc[p]]), np.mean([c[2] for c in pc[p]])] for p in pend], axis=0)
        pend.sort(key=lambda p: math.hypot(np.mean([c[1] for c in pc[p]]) - cen[0],
                                           np.mean([c[2] for c in pc[p]]) - cen[1]))
        tree = [pend.pop(0)]
        failed = []
        while pend:
            comp_of, ccells = self.islands(net, {p: pc[p] for p in tree + pend})
            tcomp = {comp_of[p] for p in tree if p in comp_of}
            for p in [q for q in pend if comp_of.get(q) in tcomp]:
                pend.remove(p)                                 # already joined by earlier copper
                tree.append(p)
            if not pend:
                break
            tc = [c for k in tcomp for c in ccells.get(k, [])]
            tarr = np.array([[c[1], c[2]] for c in tc])

            def dist(p):
                pa = np.array([[c[1], c[2]] for c in pc[p][:50]])
                return np.min(np.hypot(tarr[:, None, 0] - pa[None, :, 0], tarr[:, None, 1] - pa[None, :, 1])) \
                    if len(tarr) < 4000 else np.min(np.hypot(tarr[::10, None, 0] - pa[None, :, 0],
                                                             tarr[::10, None, 1] - pa[None, :, 1]))
            pend.sort(key=dist)
            p = pend.pop(0)
            dst = ccells.get(comp_of.get(p), pc[p])            # the pin plus any stub/via already on it
            done = False
            for ww in widths:
                for margin in (2.5, 6.0, 30.0):
                    path = self.route_pair(net, tc, dst, ww, clr, layers, margin=margin, allow_via=allow_via)
                    if path:
                        self.commit(net, path, ww, keepoff)
                        done = True
                        break
                if done:
                    break
            if done:
                tree.append(p)
            else:
                failed.append(p)
        return failed

    def dump_fail(self, path, title=''):
        """Debug picture of the last failed search: explored cells per layer (F red, In2 green, B blue)."""
        from PIL import Image, ImageDraw
        if not getattr(self, 'last_fail', None):
            return
        (i0, i1, j0, j1), wd, keys, dst, src = self.last_fail
        h = i1 - i0
        img = Image.new('RGB', (wd, h * 3 + 20), (255, 255, 255))
        px = img.load()
        for L in range(3):
            own = self.owner[L, i0:i1, j0:j1]
            ii, jj = np.nonzero(own != 0)
            for a, b in zip(ii, jj):
                px[int(b), int(a) + L * h] = (200, 200, 200)
        col = [(230, 60, 60), (40, 170, 60), (60, 90, 230)]
        for L, k in keys:
            a, b = divmod(k, wd)
            px[b, a + L * h] = col[L]
        for (L, i, j) in dst:
            if i0 <= i < i1 and j0 <= j < j1:
                px[j - j0, i - i0 + L * h] = (250, 200, 0)
        ImageDraw.Draw(img).text((2, 3 * h + 4), title, fill=(0, 0, 0))
        img.save(path)

    def stub(self, net, pts, w=0.15, clr=0.1):
        """Place an explicit F.Cu polyline from a pad (first point inside it) ending in a via at the last point,
        after checking every cell along it and the via against the clearance masks."""
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        i0, j0 = self.cell(min(xs), min(ys)); i1, j1 = self.cell(max(xs), max(ys))
        m = int(1.0 / RES)
        win = (max(i0 - m, 0), min(i1 + m + 1, self.H), max(j0 - m, 0), min(j1 + m + 1, self.W))
        ok, via_ok = self.masks(net, w, clr, win)
        code = self.code(net)
        for a, b in zip(pts[:-1], pts[1:]):
            n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / (RES / 2)) + 1
            for t in range(n + 1):
                x = a[0] + (b[0] - a[0]) * t / n; y = a[1] + (b[1] - a[1]) * t / n
                i, j = self.cell(x, y)
                if self.owner[0, i, j] == code:      # inside own pad / own copper
                    continue
                if not ok[0][i - win[0], j - win[2]]:
                    return False
        vi, vj = self.cell(*pts[-1])
        if not via_ok[vi - win[0], vj - win[2]]:
            return False
        self.add_track(net, pts, 0, w)
        self.add_via(net, *pts[-1])
        self.fixed.setdefault(net, []).append((pts, w))
        return True

    def pad_connected(self, net, ref, num):
        """True if routed copper of `net` already touches this pad."""
        code = self.code(net)
        for (L, i, j) in self.pad_cells(ref, num):
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < self.H and 0 <= jj < self.W and self.routed[L, ii, jj] == code:
                        return True
        return False

    def connect_pin(self, net, ref, num, w=0.2, clr=0.1, layers=(0, 2), radius=6.0):
        """Connect one pad to the nearest already-routed copper (tracks/vias) of its own net."""
        src = self.pad_cells(ref, num)
        if not src:
            return False
        ci = np.mean([c[1] for c in src]); cj = np.mean([c[2] for c in src])
        own = set((c[1], c[2]) for c in src)
        for rad in (radius, radius * 2.5):
            r = int(rad / RES)
            dst = [c for c in self.net_cells(net)
                   if abs(c[1] - ci) <= r and abs(c[2] - cj) <= r and (c[1], c[2]) not in own]
            if not dst:
                continue
            for ww in (w, 0.15):
                path = self.route_pair(net, src, dst, ww, clr, layers, margin=1.5)
                if path:
                    self.commit(net, path, ww)
                    return True
        return False

    # ------------------------------------------------------------------ rip-up
    def ripup(self, net):
        """Remove the tracks and vias of `net` from the grid (pads, reservations and fixed escapes stay)."""
        code = self.code(net)
        for it in self.items:
            if it[0] == 'V' and it[1] == net:
                self._via_block(it[2], it[3], -1)
        self.items = [it for it in self.items if it[1] != net]
        self.owner[self.owner == code] = 0
        self.routed[self.routed == code] = 0
        self.vias = [v for v in self.vias if v[2] != net]
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetname().lstrip('/') != net:
                    continue
                lset = pad.GetLayerSet()
                for li, L in enumerate(LAYERS):
                    if lset.Contains(L):
                        g = self._pad_poly(pad, L)
                        if g is not None and not g.is_empty:
                            self._raster(g, li, code)
        for g in self.reservations.get(net, []):
            self._raster(g, 0, code, grow=0.0001)
        for pts, w in self.fixed.get(net, []):              # escape stubs and plane vias stay
            self.add_track(net, pts, 0, w)
            self.add_via(net, *pts[-1])

    def nets_near(self, cells, radius=1.5, exclude=()):
        """Routed nets whose tracks/vias come within `radius` mm of any of the given cells."""
        if not cells:
            return []
        r = int(radius / RES)
        ii = [c[1] for c in cells]; jj = [c[2] for c in cells]
        i0, i1 = max(min(ii) - r, 0), min(max(ii) + r + 1, self.H)
        j0, j1 = max(min(jj) - r, 0), min(max(jj) + r + 1, self.W)
        codes = np.unique(self.routed[:, i0:i1, j0:j1])
        inv = {v: k for k, v in self.netcode.items()}
        return [inv[c] for c in codes if c in inv and inv[c] not in exclude and inv[c] != 'GND']

    # ------------------------------------------------------------------ GND fan-out and stitching
    def fanout(self, net, ref, num, w=0.25, clr=0.1, max_d=1.6, prefer=None, tol=None, fixed=True):
        cells = self.pad_cells(ref, num)
        f_cells = [c for c in cells if c[0] == 0]
        if not f_cells:
            return False
        ci = np.mean([c[1] for c in f_cells]); cj = np.mean([c[2] for c in f_cells])
        pad_set = set((c[1], c[2]) for c in f_cells)
        pads = [p for p in self.bb.fps[ref].Pads() if p.GetNumber() == num and p.IsOnLayer(pcbnew.F_Cu)]
        pad_g = shapely.union_all([self._pad_poly(p, pcbnew.F_Cu) for p in pads])
        m = int((max_d + 0.5) / RES)
        win = (max(int(ci) - m, 0), min(int(ci) + m, self.H), max(int(cj) - m, 0), min(int(cj) + m, self.W))
        ok, via_ok = self.masks(net, w, clr, win)
        i0, i1, j0, j1 = win
        cands = []
        if prefer is not None:
            pi_, pj_ = self.cell(*prefer)
        for i in range(i1 - i0):
            for j in range(j1 - j0):
                if via_ok[i, j]:
                    d = math.hypot(i + i0 - ci, j + j0 - cj)
                    if d * RES <= max_d:
                        if prefer is not None:
                            dp = math.hypot(i + i0 - pi_, j + j0 - pj_)
                            if tol is not None and dp * RES > tol:
                                continue
                            cands.append((dp, i, j))
                        else:
                            cands.append((d, i, j))
        cands.sort()
        for d, i, j in cands[:400]:
            # straight path from via to nearest pad cell on F.Cu must be clear
            gi, gj = i + i0, j + j0
            if pad_g.distance(Point(*self.xy(gi, gj))) < VIA_D / 2 + 0.05:
                continue
            best = min(pad_set, key=lambda c: (c[0] - gi) ** 2 + (c[1] - gj) ** 2)
            n = max(abs(best[0] - gi), abs(best[1] - gj))
            good = True
            for t in range(n + 1):
                pi = round(gi + (best[0] - gi) * t / max(n, 1)); pj = round(gj + (best[1] - gj) * t / max(n, 1))
                if (pi, pj) in pad_set:
                    break
                if not ok[0][pi - i0, pj - j0]:
                    good = False
                    break
            if not good:
                continue
            x, y = self.xy(gi, gj)
            bx, by = self.xy(*best)
            self.add_track(net, [(bx, by), (x, y)], 0, w)
            self.add_via(net, x, y)
            if fixed:
                self.fixed.setdefault(net, []).append(([(bx, by), (x, y)], w))
            return True
        return False

    def stitch(self, net, pitch=2.0, avoid=None, clr=0.15):
        """drop GND vias on a lattice wherever they fit (ties all GND pours to the In1 plane)."""
        n = 0
        code = self.code(net)
        win = (0, self.H, 0, self.W)
        _, via_ok = self.masks(net, 0.2, clr, win)
        step = int(pitch / RES)
        for i in range(step // 2, self.H, step):
            for j in range(step // 2, self.W, step):
                # search a small neighbourhood for a legal spot
                placed = False
                for di in range(0, step // 2, 2):
                    for dd in ((0, 0), (di, 0), (-di, 0), (0, di), (0, -di)):
                        ii, jj = i + dd[0], j + dd[1]
                        if 0 <= ii < self.H and 0 <= jj < self.W and via_ok[ii, jj]:
                            x, y = self.xy(ii, jj)
                            if avoid is not None and avoid.contains(Point(x, y)):
                                continue
                            self.add_via(net, x, y)
                            _, via_ok = self.masks(net, 0.2, clr, win) if n % 25 == 24 else (None, via_ok)
                            r = self._raster_mask(Point(x, y).buffer(1.2))
                            if r:
                                a0, a1, b0, b1, mm_ = r
                                via_ok[a0:a1, b0:b1] &= ~mm_
                            n += 1
                            placed = True
                            break
                    if placed:
                        break
        return n
