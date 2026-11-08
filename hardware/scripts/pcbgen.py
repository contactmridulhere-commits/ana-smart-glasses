"""Build the temple PCBs with the KiCad pcbnew API: outline, stack-up, footprints (linked to the schematic
symbols for parity checking), placement (fixed anchors + automatic near-pin placement of small parts)."""
import math
import os

import pcbnew
from shapely.geometry import Polygon, box
from shapely import affinity

import schgen

HERE = os.path.dirname(os.path.abspath(__file__))
FP_ROOT = '/usr/share/kicad/footprints'
ANA_FP = os.path.join(HERE, '..', 'lib', 'ana.pretty')
OX, OY = 30.0, 60.0          # board-local (0,0) -> KiCad page coordinates


def mm(v):
    return pcbnew.FromMM(v)


def P(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def local(v):
    return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)


def load_fp(fpid):
    lib, name = fpid.split(':', 1)
    path = ANA_FP if lib == 'ana' else os.path.join(FP_ROOT, lib + '.pretty')
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise RuntimeError('footprint not found: ' + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


class Builder:
    def __init__(self, project, temple, comps, netmap=None):
        self.project = project
        self.netmap = netmap or {}                 # (ref, pad) -> net name exactly as in the schematic netlist
        self.full = {v.lstrip('/'): v for v in self.netmap.values()}
        self.t = temple
        self.comps = {c.ref: c for c in comps}
        self.b = pcbnew.BOARD()
        self.b.SetCopperLayerCount(4)
        ds = self.b.GetDesignSettings()
        ds.SetBoardThickness(mm(0.8))
        self.netinfo = {}
        self.fps = {}
        self.keepouts = []          # shapely polygons (board-local) where parts may not go
        for c in comps:
            for n in c.pins.values():
                if n:
                    self.net(n)

    # ------------------------------------------------------------------ nets / outline
    def net(self, name):
        if name not in self.netinfo:
            full = self.full.get(name, '/' + name)            # schematic local-label nets live in sheet '/'
            ni = pcbnew.NETINFO_ITEM(self.b, full)
            self.b.Add(ni)
            self.netinfo[name] = ni
        return self.netinfo[name]

    def outline(self):
        pts = self.t.points
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            s = pcbnew.PCB_SHAPE(self.b)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(P(*a))
            s.SetEnd(P(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.05))
            self.b.Add(s)

    def cutout(self, pts):
        """internal routed slot (closed polygon) on Edge.Cuts."""
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            s = pcbnew.PCB_SHAPE(self.b)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(P(*a))
            s.SetEnd(P(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.05))
            self.b.Add(s)
        self.keepouts.append(Polygon(pts).buffer(0.3))

    # ------------------------------------------------------------------ footprints
    def place(self, ref, x, y, rot=0, back=False):
        c = self.comps[ref]
        fp = load_fp(c.fp)
        fp.SetReference(ref)
        fp.SetValue(c.value)
        self.b.Add(fp)                      # must belong to the board before Flip()
        fp.SetPosition(P(x, y))
        if back:
            fp.Flip(P(x, y), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        fp.SetOrientationDegrees(rot)
        unit = min(c.unit_pins) if c.unit_pins else 1
        fp.SetPath(pcbnew.KIID_PATH('/' + schgen.uid(self.project, 'sym', ref, unit)))
        fp.SetSheetname('/')
        fp.SetSheetfile(self.project + '.kicad_sch')
        for k, v in (('LCSC', c.lcsc), ('MPN', c.mpn)):
            if v:
                fld = pcbnew.PCB_FIELD(fp, fp.GetNextFieldId(), k)
                fld.SetText(v)
                fld.SetVisible(False)
                fld.SetLayer(pcbnew.F_Fab)
                fp.AddField(fld)
        if c.dnp:
            fp.SetDNP(True)
            fp.SetExcludedFromPosFiles(True)
        for pad in fp.Pads():
            n = self.netmap.get((ref, pad.GetNumber()))
            if n is None:
                n = c.pins.get(pad.GetNumber())
            if n:
                pad.SetNet(self.net(n.lstrip('/')))
        # small, tidy reference text on silk
        r = fp.Reference()
        r.SetTextSize(pcbnew.VECTOR2I(mm(0.5), mm(0.5)))
        r.SetTextThickness(mm(0.08))
        r.SetVisible(False)
        fp.Value().SetVisible(False)
        self.fps[ref] = fp
        return fp

    def courtyard(self, fp):
        layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
        fp.BuildCourtyardCaches()
        ps = fp.GetCourtyard(layer)
        if ps.OutlineCount() == 0:
            bb = fp.GetBoundingBox(False)
            x0, y0 = local(bb.GetOrigin())
            x1, y1 = local(bb.GetEnd())
            return box(x0, y0, x1, y1)
        ol = ps.Outline(0)
        pts = [local(ol.CPoint(i)) for i in range(ol.PointCount())]
        return Polygon(pts)

    def pad_pos(self, ref, num):
        for p in self.fps[ref].Pads():
            if p.GetNumber() == num:
                return local(p.GetPosition())
        raise KeyError((ref, num))

    def front_polys(self, exclude=None):
        out = []
        for r, fp in self.fps.items():
            if r == exclude or fp.IsFlipped():
                continue
            out.append(self.courtyard(fp))
        return out

    def autoplace(self, ref, anchor, rots=(0, 90), max_r=9.0, step=0.1, spacing=0.05, region=None):
        """Place `ref` as close as possible to `anchor` (x, y) without courtyard overlaps."""
        c = self.comps[ref]
        others = self.front_polys()
        inner = self.t.poly.buffer(-0.05)
        best = None
        ax, ay = anchor
        nsteps = int(max_r / step)
        cands = []
        for i in range(-nsteps, nsteps + 1):
            for j in range(-nsteps, nsteps + 1):
                dx, dy = i * step, j * step
                d = math.hypot(dx, dy)
                if d <= max_r:
                    cands.append((d, ax + dx, ay + dy))
        cands.sort()
        fp = self.place(ref, 0.0, 0.0, rots[0])
        shapes, padsh = {}, {}
        for rot in rots:                      # courtyard and copper relative to the footprint origin, per rotation
            fp.SetPosition(P(0, 0))
            fp.SetOrientationDegrees(rot)
            shapes[rot] = self.courtyard(fp)
            bbs = [p.GetBoundingBox() for p in fp.Pads()]
            padsh[rot] = box(min(pcbnew.ToMM(b.GetX()) for b in bbs) - OX, min(pcbnew.ToMM(b.GetY()) for b in bbs) - OY,
                             max(pcbnew.ToMM(b.GetRight()) for b in bbs) - OX,
                             max(pcbnew.ToMM(b.GetBottom()) for b in bbs) - OY)
        copper_ok = self.t.poly.buffer(-0.3)  # pads keep 0.3 mm from the edge (JLC copper-to-edge 0.25 mm)
        from shapely.strtree import STRtree
        tree = STRtree(others) if others else None
        for d, x, y in cands:
            for rot in rots:
                cy = affinity.translate(shapes[rot], x, y)
                if not inner.contains(cy) or not copper_ok.contains(affinity.translate(padsh[rot], x, y)):
                    continue
                if tree is not None and len(tree.query(cy.buffer(spacing), predicate='intersects')):
                    continue
                    continue
                if region is not None and not region.contains(cy):
                    continue
                if any(cy.intersects(k) for k in self.keepouts):
                    continue
                best = (x, y, rot)
                break
            if best:
                break
        if not best:
            raise RuntimeError('autoplace failed for %s near %s' % (ref, anchor))
        fp.SetPosition(P(best[0], best[1]))
        fp.SetOrientationDegrees(best[2])
        return best

    def check_overlaps(self):
        bad = []
        refs = [r for r, f in self.fps.items() if not f.IsFlipped()]
        polys = {r: self.courtyard(self.fps[r]) for r in refs}
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if polys[a].intersection(polys[b]).area > 1e-4:
                    bad.append((a, b))
        return bad

    # ------------------------------------------------------------------ zones
    def zone(self, net, layer, poly_pts, priority=0, clearance=0.2, min_w=0.15, thermal=True, name=''):
        z = pcbnew.ZONE(self.b)
        z.SetLayer(layer)
        if net:
            z.SetNet(self.net(net))
        ol = z.Outline()
        ol.NewOutline()
        for x, y in poly_pts:
            ol.Append(mm(OX + x), mm(OY + y))
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(min_w))
        z.SetThermalReliefGap(mm(0.2))
        z.SetThermalReliefSpokeWidth(mm(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if thermal else pcbnew.ZONE_CONNECTION_FULL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def keepout(self, poly_pts, layers, no_tracks=False, no_vias=True, no_pours=True, no_pads=False,
                no_footprints=False, name=''):
        z = pcbnew.ZONE(self.b)
        z.SetIsRuleArea(True)
        ls = pcbnew.LSET()
        for l in layers:
            ls.AddLayer(l)
        z.SetLayerSet(ls)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in poly_pts:
            ol.Append(mm(OX + x), mm(OY + y))
        z.SetDoNotAllowTracks(no_tracks)
        z.SetDoNotAllowVias(no_vias)
        z.SetDoNotAllowCopperPour(no_pours)
        z.SetDoNotAllowPads(no_pads)
        z.SetDoNotAllowFootprints(no_footprints)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def text(self, s, x, y, size=0.8, layer=pcbnew.F_SilkS, rot=0, mirror=False):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetPosition(P(x, y))
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(size * 0.15))
        t.SetTextAngleDegrees(rot)
        if mirror:
            t.SetMirrored(True)
        self.b.Add(t)
        return t

    def save(self, path):
        self.b.Save(path)
