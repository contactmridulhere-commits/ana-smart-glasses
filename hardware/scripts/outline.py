"""Parametric temple-shaped board outline.

Board-local coordinates (mm): x along the temple from the hinge end (x=0) towards the ear, y downwards
from the straight top edge (KiCad convention).  The straight section runs from x=0 to the bend, then the
ear tip rotates downwards by theta.  All dimensions come from config/temple.json (inside of the cover);
the PCB is that cavity minus the clearances.
"""
import json
import math
import os

from shapely.geometry import Polygon, Point

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, '..', 'config', 'temple.json')


class Temple:
    def __init__(self, cfg=None):
        c = cfg or json.load(open(CFG))
        self.cfg = c
        ct = c['pcb_clearance_top_bottom']
        self.h1 = c['H1_height_at_hinge'] - 2 * ct
        self.h2 = c['H2_height_at_bend'] - 2 * ct
        self.h3 = c['H3_height_at_tip'] - 2 * ct
        self.Lp = c['A_straight_length'] - c['pcb_clearance_front']
        self.Lt = c['B_tip_length'] - c['pcb_clearance_tip']
        self.th = math.radians(c['theta_bend_deg'])
        self.d = (math.cos(self.th), math.sin(self.th))        # tip axis (down-right)
        self.n = (-math.sin(self.th), math.cos(self.th))       # tip normal pointing to the bottom/inner side
        self._build()

    # ---- frames ---------------------------------------------------------------------------------
    def tip(self, s, t):
        """point s mm along the tip's top edge from the bend, t mm in from that edge."""
        return (self.Lp + s * self.d[0] + t * self.n[0], 0 + s * self.d[1] + t * self.n[1])

    @property
    def tip_rot(self):
        return -math.degrees(self.th)          # KiCad orientation for parts aligned with the tip

    def h_straight(self, x):
        return self.h1 + (self.h2 - self.h1) * x / self.Lp

    def h_tip(self, s):
        return self.h2 + (self.h3 - self.h2) * s / self.Lt

    # ---- outline ---------------------------------------------------------------------------------
    def _build(self):
        c = self.cfg
        P0 = (0.0, 0.0)
        P1 = (self.Lp, 0.0)
        P2 = self.tip(self.Lt, 0)
        P3 = self.tip(self.Lt, self.h3)
        Q0 = self.tip(0, self.h2)
        P5 = (0.0, self.h1)
        B1 = (self.Lp, self.h2)
        P4 = _intersect(P5, B1, Q0, P3)
        self.corners = [P0, P1, P2, P3, P4, P5]
        radii = [c['corner_radius_front'], c['bend_outer_radius'], c['corner_radius_tip'], c['corner_radius_tip'],
                 c['bend_inner_radius'], c['corner_radius_front']]
        pts = []
        n = len(self.corners)
        for i in range(n):
            pts += _round(self.corners[i - 1], self.corners[i], self.corners[(i + 1) % n], radii[i])
        self.points = pts
        self.poly = Polygon(pts)

    def inside(self, x, y, margin=0.0):
        return self.poly.buffer(-margin).contains(Point(x, y))

    def bbox(self):
        return self.poly.bounds


def _intersect(a1, a2, b1, b2):
    x1, y1 = a1; x2, y2 = a2; x3, y3 = b1; x4, y4 = b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _round(prev, corner, nxt, r, seg_deg=6.0):
    """Replace a polygon corner by a tangent arc of radius r (works for convex and concave corners)."""
    if r <= 0:
        return [corner]
    ax, ay = prev[0] - corner[0], prev[1] - corner[1]
    bx, by = nxt[0] - corner[0], nxt[1] - corner[1]
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    ax, ay, bx, by = ax / la, ay / la, bx / lb, by / lb
    cosang = max(-1.0, min(1.0, ax * bx + ay * by))
    ang = math.acos(cosang)                       # interior angle between the two edges
    if ang < 1e-6 or abs(ang - math.pi) < 1e-6:
        return [corner]
    tlen = r / math.tan(ang / 2)
    tlen = min(tlen, 0.45 * la, 0.45 * lb)
    r = tlen * math.tan(ang / 2)
    t1 = (corner[0] + ax * tlen, corner[1] + ay * tlen)
    t2 = (corner[0] + bx * tlen, corner[1] + by * tlen)
    bis = (ax + bx, ay + by)
    bl = math.hypot(*bis)
    dist = r / math.sin(ang / 2)
    cx, cy = corner[0] + bis[0] / bl * dist, corner[1] + bis[1] / bl * dist
    a1 = math.atan2(t1[1] - cy, t1[0] - cx)
    a2 = math.atan2(t2[1] - cy, t2[0] - cx)
    da = a2 - a1
    while da > math.pi:
        da -= 2 * math.pi
    while da < -math.pi:
        da += 2 * math.pi
    steps = max(2, int(abs(math.degrees(da)) / seg_deg) + 1)
    return [(cx + r * math.cos(a1 + da * k / steps), cy + r * math.sin(a1 + da * k / steps)) for k in range(steps + 1)]


if __name__ == '__main__':
    t = Temple()
    print('heights', t.h1, t.h2, t.h3, 'Lp', t.Lp, 'Lt', t.Lt)
    print('corners', [(round(x, 2), round(y, 2)) for x, y in t.corners])
    print('bbox', [round(v, 2) for v in t.bbox()], 'area', round(t.poly.area, 1), 'valid', t.poly.is_valid)
