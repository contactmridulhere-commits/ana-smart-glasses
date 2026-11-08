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
        self.edge_dist = ndimage.distance_transform_edt(inside) * RES        # distance to outside
        self.owner = np.zeros((3, self.H, self.W), dtype=np.int32)          # net code per layer
        self.routed = np.zeros((3, self.H, self.W), dtype=np.int32)         # tracks/vias only
        self.novia = np.zeros((self.H, self.W), dtype=bool)                 # pad areas / keepouts
        self.netcode = {}
        self.blocked_for = {}        # net name -> extra blocked mask (keepouts)
        self.vias = []               # (x, y, net)
        self.reservations = {}       # net -> list of reserved escape geometries
        self.track_count = 0
        self.free_code = 100000
        self._load_copper()

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

    # ------------------------------------------------------------------ add copper
    def add_track(self, net, pts, layer_idx, w, keepoff=0.0):
        code = self.code(net)
        for a, b in zip(pts[:-1], pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(pcbnew.VECTOR2I(mm(OX + a[0]), mm(OY + a[1])))
            t.SetEnd(pcbnew.VECTOR2I(mm(OX + b[0]), mm(OY + b[1])))
            t.SetWidth(mm(w))
            t.SetLayer(LAYERS[layer_idx])
            t.SetNet(self.bb.net(net))
            self.b.Add(t)
            self.track_count += 1
        g = LineString(pts).buffer(w / 2, cap_style='round') if len(pts) > 1 else Point(pts[0]).buffer(w / 2)
        r = self._raster(g, layer_idx, code, grow=keepoff)
        r2 = self._raster_mask(g)
        if r2:
            i0, i1, j0, j1, m = r2
            self.routed[layer_idx, i0:i1, j0:j1][m] = code

    def add_via(self, net, x, y):
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(pcbnew.VECTOR2I(mm(OX + x), mm(OY + y)))
        v.SetWidth(mm(VIA_D))
        v.SetDrill(mm(VIA_DRILL))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(self.bb.net(net))
        self.b.Add(v)
        self._raster(Point(x, y).buffer(VIA_D / 2), None, self.code(net))
        r2 = self._raster_mask(Point(x, y).buffer(VIA_D / 2))
        if r2:
            i0, i1, j0, j1, m = r2
            for L in range(3):
                self.routed[L, i0:i1, j0:j1][m] = self.code(net)
        r = self._raster_mask(Point(x, y).buffer(VIA_DRILL / 2 + 0.25 + VIA_DRILL / 2))
        if r:
            i0, i1, j0, j1, m = r
            self.novia[i0:i1, j0:j1] |= m
        self.vias.append((x, y, net))

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
        via_ok = (~self.novia[i0:i1, j0:j1]) & (self.edge_dist[i0:i1, j0:j1] >= EDGE_CLR + VIA_D / 2 + 0.05)
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
            for di, dj, cst in DIRS:
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

    def route_net(self, net, pins, w, clr, layers=(0, 1, 2), allow_via=True, widths=None, keepoff=0.0):
        """pins: list of (ref, pad). Grow a tree; returns list of failed pins."""
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
            tc = self.net_cells(net) + [c for p in tree for c in pc[p]]
            tarr = np.array([[c[1], c[2]] for c in tc])

            def dist(p):
                pa = np.array([[c[1], c[2]] for c in pc[p][:50]])
                return np.min(np.hypot(tarr[:, None, 0] - pa[None, :, 0], tarr[:, None, 1] - pa[None, :, 1])) \
                    if len(tarr) < 4000 else np.min(np.hypot(tarr[::10, None, 0] - pa[None, :, 0],
                                                             tarr[::10, None, 1] - pa[None, :, 1]))
            pend.sort(key=dist)
            p = pend.pop(0)
            done = False
            for ww in widths:
                for margin in (2.5, 6.0, 30.0):
                    path = self.route_pair(net, tc, pc[p], ww, clr, layers, margin=margin, allow_via=allow_via)
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

    # ------------------------------------------------------------------ rip-up
    def ripup(self, net):
        """Remove all tracks and vias of `net` from the board and the grid (pads stay)."""
        code = self.code(net)
        for t in list(self.b.GetTracks()):
            if t.GetNetname().lstrip('/') == net:
                self.b.Remove(t)
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
    def fanout(self, net, ref, num, w=0.25, clr=0.1, max_d=1.6, prefer=None, tol=None):
        cells = self.pad_cells(ref, num)
        f_cells = [c for c in cells if c[0] == 0]
        if not f_cells:
            return False
        ci = np.mean([c[1] for c in f_cells]); cj = np.mean([c[2] for c in f_cells])
        pad_set = set((c[1], c[2]) for c in f_cells)
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
