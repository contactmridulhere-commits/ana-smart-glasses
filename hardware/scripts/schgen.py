"""Generate a KiCad 9 schematic (.kicad_sch) from a circuits.py component list.

Style: every component is drawn from its library symbol and each pin gets a short wire stub and a net
label (net-label style schematic), grouped into titled functional blocks.  Unused pins get no-connect
flags, nets with power inputs but no power source get a PWR_FLAG.
"""
import math
import uuid

import symlib
from sexpr import Sym, dump

GRID = 1.27
NS = uuid.UUID('6a0c3a4e-5d1b-4c7e-9a7e-2b1f0c9d8e11')


def uid(*parts):
    return str(uuid.uuid5(NS, '/'.join(str(p) for p in parts)))


def snap(v):
    return round(round(v / GRID) * GRID, 4)


def xform(px, py, rot):
    """symbol coords (y up) -> schematic offset (y down) for rotation rot (deg, CCW on screen)."""
    a = math.radians(rot)
    x = px * math.cos(a) - py * math.sin(a)
    y = px * math.sin(a) + py * math.cos(a)
    return x, -y


def out_dir(angle_deg, rot):
    """direction (unit vector, schematic coords) pointing away from the body for a pin."""
    a = math.radians(angle_deg + 180)
    dx, dy = math.cos(a), math.sin(a)
    x, y = xform(dx, dy, rot)
    return round(x), round(y)


def text_w(s, size=1.27):
    return len(s) * size * 0.72 + 1.0


TWO_PIN_HORIZ = ('Device:R', 'Device:C', 'Device:L')


class Sheet:
    def __init__(self, project, title, rev, paper='A2'):
        self.project = project
        self.title = title
        self.rev = rev
        self.paper = paper
        self.root = uid(project, 'root')
        self.items = []
        self.lib_ids = []
        self.pwr_idx = 0

    def _lib(self, lib_id):
        if lib_id not in self.lib_ids:
            self.lib_ids.append(lib_id)

    def symbol(self, comp, x, y, rot=0, unit=1, ref=None, value=None, power=False, hide_ref=False):
        lib_id = comp if isinstance(comp, str) else comp.sym
        self._lib(lib_id)
        ref = ref or comp.ref
        value = value if value is not None else comp.value
        su = uid(self.project, 'sym', ref, unit)
        pins = symlib.pins(lib_id, unit)
        bb = symlib.body_bbox(lib_id, unit)
        props = []

        fang = rot if rot in (90, 270) else 0   # keep field text horizontal on rotated symbols

        def prop(name, val, dx, dy, hide=False, size=1.27, just=None):
            eff = [Sym('effects'), [Sym('font'), [Sym('size'), size, size]]]
            if just:
                eff.append([Sym('justify'), Sym(just)])
            if hide:
                eff.append([Sym('hide'), Sym('yes')])
            props.append([Sym('property'), name, val, [Sym('at'), round(x + dx, 3), round(y + dy, 3), fang], eff])
        top = -bb[3]
        bot = -bb[1]
        if lib_id in TWO_PIN_HORIZ and rot in (90, 270):
            prop('Reference', ref, 0, -3.0)
            prop('Value', value, 0, 3.0)
        elif power:
            prop('Reference', ref, 0, 3.0, hide=True)
            prop('Value', value, 0, -3.5)
        else:
            prop('Reference', ref, bb[0], top - 1.8, hide=hide_ref, just='left')
            prop('Value', value, bb[0], bot + 1.8, just='left')
        if not isinstance(comp, str):
            prop('Footprint', comp.fp, 0, 0, hide=True)
            prop('Datasheet', '', 0, 0, hide=True)
            prop('Description', comp.desc, 0, 0, hide=True)
            if comp.lcsc:
                prop('LCSC', comp.lcsc, 0, 0, hide=True)
            if comp.mpn:
                prop('MPN', comp.mpn, 0, 0, hide=True)
        else:
            prop('Footprint', '', 0, 0, hide=True)
            prop('Datasheet', '', 0, 0, hide=True)
        dnp = (not isinstance(comp, str)) and comp.dnp
        nobom = (not isinstance(comp, str)) and comp.fp.split(':')[-1].startswith(
            ('WirePads', 'TestPads', 'BatteryPads', 'TouchElectrode'))
        node = [Sym('symbol'), [Sym('lib_id'), lib_id], [Sym('at'), snap(x), snap(y), rot], [Sym('unit'), unit],
                [Sym('exclude_from_sim'), Sym('no')], [Sym('in_bom'), Sym('no' if (power or nobom) else 'yes')],
                [Sym('on_board'), Sym('no' if power else 'yes')], [Sym('dnp'), Sym('yes' if dnp else 'no')],
                [Sym('uuid'), su]] + props
        for num in sorted(pins):
            node.append([Sym('pin'), num, [Sym('uuid'), uid(self.project, 'pin', ref, unit, num)]])
        node.append([Sym('instances'), [Sym('project'), self.project,
                                        [Sym('path'), '/' + self.root, [Sym('reference'), ref], [Sym('unit'), unit]]]])
        self.items.append(node)
        # return absolute pin positions
        res = {}
        for num, p in pins.items():
            ox, oy = xform(p['x'], p['y'], rot)
            res[num] = (snap(x + ox), snap(y + oy), out_dir(p['angle'], rot), p)
        return su, res

    def wire(self, a, b):
        self.items.append([Sym('wire'), [Sym('pts'), [Sym('xy'), a[0], a[1]], [Sym('xy'), b[0], b[1]]],
                           [Sym('stroke'), [Sym('width'), 0], [Sym('type'), Sym('default')]],
                           [Sym('uuid'), uid(self.project, 'w', a, b)]])

    def label(self, net, x, y, direction):
        dx, dy = direction
        if dx > 0:
            ang, just = 0, 'left'
        elif dx < 0:
            ang, just = 180, 'right'
        elif dy < 0:
            ang, just = 90, 'left'
        else:
            ang, just = 270, 'right'
        self.items.append([Sym('label'), net, [Sym('at'), x, y, ang], [Sym('fields_autoplaced'), Sym('yes')],
                           [Sym('effects'), [Sym('font'), [Sym('size'), 1.27, 1.27]], [Sym('justify'), Sym(just), Sym('bottom')]],
                           [Sym('uuid'), uid(self.project, 'lbl', net, x, y)]])

    def noconnect(self, x, y):
        self.items.append([Sym('no_connect'), [Sym('at'), x, y], [Sym('uuid'), uid(self.project, 'nc', x, y)]])

    def text(self, s, x, y, size=2.0, bold=False):
        font = [Sym('font'), [Sym('size'), size, size]]
        if bold:
            font.append([Sym('bold'), Sym('yes')])
        self.items.append([Sym('text'), s, [Sym('exclude_from_sim'), Sym('no')], [Sym('at'), x, y, 0],
                           [Sym('effects'), font, [Sym('justify'), Sym('left'), Sym('bottom')]],
                           [Sym('uuid'), uid(self.project, 'txt', s, x, y)]])

    def rect(self, x0, y0, x1, y1):
        self.items.append([Sym('rectangle'), [Sym('start'), x0, y0], [Sym('end'), x1, y1],
                           [Sym('stroke'), [Sym('width'), 0.2], [Sym('type'), Sym('dash')]],
                           [Sym('fill'), [Sym('type'), Sym('none')]], [Sym('uuid'), uid(self.project, 'rect', x0, y0)]])

    def connect_pins(self, pinpos, netmap, stub=2.54):
        """wire stub + label for each connected pin; no-connect flag on unconnected ones."""
        for num, (px, py, d, p) in pinpos.items():
            net = netmap.get(num)
            if p['type'] == 'no_connect' and not net:
                continue
            if not net:
                self.noconnect(px, py)
                continue
            ex, ey = snap(px + d[0] * stub), snap(py + d[1] * stub)
            self.wire((px, py), (ex, ey))
            self.label(net, ex, ey, d)

    def pwr_flag(self, net, x, y):
        self.pwr_idx += 1
        ref = '#FLG%02d' % self.pwr_idx
        _, pp = self.symbol('power:PWR_FLAG', x, y, ref=ref, value='PWR_FLAG', power=True)
        px, py, d, p = pp['1']
        self.wire((px, py), (px, snap(py + 2.54)))
        self.label(net, px, snap(py + 2.54), (0, 1))

    def render(self):
        lib_symbols = [Sym('lib_symbols')] + [symlib.lib_symbol_entry(l) for l in self.lib_ids]
        tb = [Sym('title_block'), [Sym('title'), self.title], [Sym('date'), '2026-11-07'], [Sym('rev'), self.rev],
              [Sym('company'), 'Project Spectra - ANA smart glasses'],
              [Sym('comment'), 1, 'Generated by hardware/scripts/build.py - edit circuits.py, not this file'],
              [Sym('comment'), 2, 'Net-label style schematic: pins with the same label are connected']]
        doc = [Sym('kicad_sch'), [Sym('version'), 20250114], [Sym('generator'), 'eeschema'],
               [Sym('generator_version'), '9.0'], [Sym('uuid'), self.root], [Sym('paper'), self.paper], tb,
               lib_symbols] + self.items + [
            [Sym('sheet_instances'), [Sym('path'), '/', [Sym('page'), '1']]],
            [Sym('embedded_fonts'), Sym('no')]]
        return dump(doc) + '\n'


def comp_size(comp, unit=1, rot=0):
    pins = symlib.pins(comp.sym, unit)
    bb = symlib.body_bbox(comp.sym, unit)
    # label widths on each side
    lw = {(-1, 0): 0, (1, 0): 0, (0, -1): 0, (0, 1): 0}
    for num, p in pins.items():
        net = comp.pins.get(num) or ''
        d = out_dir(p['angle'], rot)
        lw[d] = max(lw.get(d, 0), text_w(net) + 2.54)
    if rot in (90, 270):
        w = (bb[3] - bb[1]) + lw[(-1, 0)] + lw[(1, 0)]
        h = (bb[2] - bb[0]) + 6
    else:
        w = (bb[2] - bb[0]) + lw[(-1, 0)] + lw[(1, 0)]
        h = (bb[3] - bb[1]) + max(lw[(0, -1)], 2.54) + max(lw[(0, 1)], 2.54) + 4
    return w, h, lw


def generate(project, title, rev, comps, path, notes=()):
    sh = Sheet(project, title, rev, paper='A2')
    blocks = []
    for c in comps:
        if c.block not in blocks:
            blocks.append(c.block)
    # power-pin analysis for PWR_FLAG
    drivers, sinks = set(), set()
    for c in comps:
        units = c.unit_pins or {1: list(c.pins)}
        for u in units:
            for num, p in symlib.pins(c.sym, u).items():
                net = c.pins.get(num)
                if not net:
                    continue
                if p['type'] == 'power_out':
                    drivers.add(net)
                if p['type'] == 'power_in':
                    sinks.add(net)
    flag_nets = sorted(n for n in sinks if n not in drivers)

    x0, y0 = 20.0, 30.0
    page_w = 570.0
    cur_x, cur_y, row_h = x0, y0, 0
    sh.text(title, x0, 18, size=4, bold=True)
    for i, n in enumerate(notes):
        sh.text(n, 400, 14 + i * 4, size=2)
    for b in blocks:
        bcomps = [c for c in comps if c.block == b]
        # lay out block contents in a flow within a block width
        items = []
        for c in bcomps:
            for u in (sorted(c.unit_pins) if c.unit_pins else [1]):
                rot = 90 if c.sym in TWO_PIN_HORIZ else 0
                w, h, lw = comp_size(c, u, rot)
                items.append((c, u, rot, w, h, lw))
        big = [it for it in items if it[3] > 60 or it[4] > 30]
        bw = max([it[3] for it in big] + [0]) + 10
        bw = max(bw, 190.0) if len(items) > 6 else max(bw, 120.0)
        # pack
        px, py, rh = 0.0, 10.0, 0.0
        places = []
        for it in items:
            c, u, rot, w, h, lw = it
            w = max(w, 38.0)
            if px + w > bw and px > 0:
                px, py, rh = 0.0, py + rh + 4, 0.0
            places.append((it, px, py))
            px += w + 4
            rh = max(rh, h)
        bh = py + rh + 6
        if cur_x + bw > page_w and cur_x > x0:
            cur_x, cur_y, row_h = x0, cur_y + row_h + 10, 0
        sh.rect(cur_x, cur_y, cur_x + bw, cur_y + bh)
        sh.text(b, cur_x + 2, cur_y + 6, size=2.5, bold=True)
        for (c, u, rot, w, h, lw), px, py in places:
            bb = symlib.body_bbox(c.sym, u)
            if rot in (90, 270):
                sx = cur_x + px + lw[(-1, 0)] + (bb[3] - bb[1]) / 2
                sy = cur_y + py + h / 2
            else:
                sx = cur_x + px + lw[(-1, 0)] - bb[0]
                sy = cur_y + py + max(lw[(0, -1)], 2.54) + bb[3] + 2
            _, pp = sh.symbol(c, sx, sy, rot=rot, unit=u)
            if c.unit_pins:
                pp = {k: v for k, v in pp.items() if k in c.unit_pins[u]}
            sh.connect_pins(pp, c.pins)
        cur_x += bw + 10
        row_h = max(row_h, bh)
    # PWR_FLAGs in a row at the bottom
    fy = cur_y + row_h + 20
    sh.text('Power flags (tell ERC these nets are supplied from off-sheet: battery, link wires, USB)', x0, fy - 8, size=2)
    for i, n in enumerate(flag_nets):
        sh.pwr_flag(n, x0 + 10 + i * 25, fy)
    if fy + 20 > 400:
        sh.paper = 'A1'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(sh.render())
    return sh
