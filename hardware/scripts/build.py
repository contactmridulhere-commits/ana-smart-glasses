#!/usr/bin/env python3
"""Regenerate everything for both ANA temple boards:
   custom libs -> schematics -> PCB (outline, placement, routing, zones) -> ERC/DRC reports.

Run with KiCad's Python:   /usr/bin/python3 hardware/scripts/build.py [ana-main|ana-power]
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pcbnew                                     # noqa: E402
from shapely.geometry import Polygon              # noqa: E402

import circuits, schgen, symlib, fpgen, project, outline, pcbgen, layouts, router   # noqa: E402

HW = os.path.abspath(os.path.join(HERE, '..'))
VIA_KEEP = 0.3          # stitching vias keep their whole pad out of keep-out areas

# net -> (preferred widths, layers, allow vias, keep-off halo)
SPECIAL = {
    'ana-main': {
        'LNA_IN': ([0.2], (0,), False, 0.15), 'RF_MID': ([0.2], (0,), False, 0.15),
        'RF_FEED': ([0.2], (0,), False, 0.15),
        '3V3': ([0.3, 0.2, 0.15], (0, 1, 2), True, 0), 'VSYS': ([0.3, 0.2], (0, 1, 2), True, 0),
        'VDD3P3': ([0.3, 0.2], (0, 1, 2), True, 0), '1V8': ([0.25, 0.15], (0, 1, 2), True, 0),
        'USB_DP': ([0.18, 0.15], (0, 1, 2), True, 0), 'USB_DN': ([0.18, 0.15], (0, 1, 2), True, 0),
        'LED_K': ([0.25, 0.15], (0, 1, 2), True, 0),
    },
    'ana-power': {
        'VBUS': ([0.5, 0.35, 0.25], (0, 1, 2), True, 0), 'VBAT': ([0.5, 0.35, 0.25], (0, 1, 2), True, 0),
        'BATN': ([0.6, 0.4], (0, 1, 2), True, 0), 'PROT_D': ([0.6, 0.4], (0, 1, 2), True, 0),
        'VSYS_RAW': ([0.5, 0.35], (0, 1, 2), True, 0), 'VSYS': ([0.5, 0.35, 0.25], (0, 1, 2), True, 0),
        'VBAT_BT': ([0.4, 0.3], (0, 1, 2), True, 0), 'LED_K': ([0.3, 0.2], (0, 1, 2), True, 0),
        'USB_DP': ([0.2, 0.15], (0, 1, 2), True, 0), 'USB_DN': ([0.2, 0.15], (0, 1, 2), True, 0),
    },
}
FIRST = {'ana-main': ['LNA_IN', 'RF_MID', 'RF_FEED', 'XTAL_P', 'XTAL_IN', 'XTAL_N', 'CAP1', 'VDD_SDIO',
                      'VDD3P3', 'EN', 'IO0', 'OLED_DC', 'OLED_RST', 'OLED_CS', 'SPI_CLK_MCU', 'SPI_MOSI_MCU',
                      'I2C_SCL', 'I2C_SDA', 'BAT_ADC', 'MAX_INT', 'VBUS_SENSE', 'TOUCH', 'CHRG_N', 'U0TXD', 'U0RXD',
                      'USB_DP', 'USB_DN', 'CP_RST', 'RTS_N', 'DTR_N', 'U0TXD_R', 'Q1_B1', 'Q1_B2', '3V3', 'VSYS'],
         'ana-power': ['VBUS', 'USB_DP', 'USB_DN', 'CC1', 'CC2', 'PROT_D', 'BATN', 'OD', 'OC', 'CS', 'DW_VCC']}


# other board sets (hardware/v2) register here: name -> dict(outline=f(name), layout=f(bb), escapes=[refs],
# silk=str, notes=[str]); nothing below changes for the V1 boards
EXTRA = {}


def log(*a):
    print(*a, flush=True)


def board_poly(bb):
    poly = bb.t.poly
    for k in bb.keepouts_cut if hasattr(bb, 'keepouts_cut') else []:
        poly = poly.difference(k)
    return poly


def build(name, priority=()):
    t0 = time.time()
    meta = circuits.BOARDS[name]
    comps = meta['build']()
    d = os.path.join(HW, name + os.environ.get('ANA_TRY', ''))
    project.write(d, name)
    notes = [
        'Board: 4-layer 0.8 mm FR-4, JLCPCB rules (0.1/0.1 mm, vias 0.4/0.2 mm). All SMT parts on F (top) side.',
        'Frame link (8 wires, same order on both temples): ' +
        ' '.join('%d %s' % (i + 1, n) for i, n in enumerate(circuits.LINK_ORDER)),
    ]
    if name == 'ana-main':
        notes.append('ESP32 GPIO map: OLED CLK=4 MOSI=23 CS=15 DC=26 RST=27 | I2C SDA=21 SCL=22 | MAX_INT=5 | '
                     'TOUCH=14 | BAT_ADC=33 | CHRG=13 | UART0 TX=1 RX=3 | 3V3 routed, 3V3 pour on In2')
    if name in EXTRA:
        notes = EXTRA[name]['notes']
    schgen.generate(name, meta['title'], meta['rev'], comps, os.path.join(d, name + '.kicad_sch'), notes)
    log(name, 'schematic written')

    # pad nets exactly as KiCad names them (sheet prefix, unconnected-(...) names) -> clean parity check
    xml = os.path.join(d, name + '.net.xml')
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', xml,
                    os.path.join(d, name + '.kicad_sch')], check=True, capture_output=True)
    import xml.etree.ElementTree as ET
    netmap = {}
    for net in ET.parse(xml).getroot().iter('net'):
        nn = net.get('name')
        if nn.startswith('unconnected-('):          # KiCad escapes '/' in pin names inside generated net names
            nn = nn.replace('/', '{slash}')
        for node in net.iter('node'):
            netmap[(node.get('ref'), node.get('pin'))] = nn
    os.remove(xml)
    temple = EXTRA[name]['outline'](name) if name in EXTRA else outline.Temple()
    bb = pcbgen.Builder(name, temple, comps, netmap)
    bb.outline()
    (EXTRA[name]['layout'] if name in EXTRA else layouts.layout_main if name == 'ana-main' else layouts.layout_power)(bb)
    bb.clear_silk_pads()
    log(name, 'placed', len(bb.fps), 'parts; overlaps:', bb.check_overlaps())

    # routing grid: board minus internal slots
    poly = temple.poly
    R = router.Router(bb, poly)
    if name == 'ana-main':
        # two signal layers on a long, narrow board: B.Cu carries the long runs along the temple (sideways steps
        # cost more there), so a net crossing the board does it on F.Cu and never walls off B.Cu
        R.dir_cost = {0: (1.6, 1.0, 1.6), 1: (1.0, 3.0, 2.6), 2: (1.0, 3.0, 2.6)}

        # In2 is a routing layer too (3V3 is routed as a net, with a 3V3 pour filling the free space on In2);
        # nothing runs under the touch electrode on In2
    nets = circuits.nets_of(comps)
    # exit corridors for fine-pitch parts before anything is routed
    if name == 'ana-main':
        for ref in ('U4', 'U7', 'U2', 'U5', 'U6'):     # (not U1: its top/bottom bands are too thin)
            R.reserve_escapes(ref)
    elif name in EXTRA:
        for ref in EXTRA[name]['escapes']:                 # 'U1' or ('J8', dict(length=..., direction=...))
            R.reserve_escapes(*((ref[0],) if isinstance(ref, tuple) else (ref,)), **(ref[1] if isinstance(ref, tuple) else {}))
    else:
        R.reserve_escapes('J1', length=0.7, direction=(0, 1))
        for ref in ('U1', 'U2', 'U3', 'Q1', 'U4', 'Q5'):
            R.reserve_escapes(ref)
    if name == 'ana-main':
        R.block_area(bb.ant_zone, nets_allowed=('RF_FEED', 'RF_MID'), novia=True)
        sx, sy = pcbgen.local(bb.fps['U6'].GetPosition())
        bb.sht_poly = Polygon([(sx - 1.6, -0.5), (sx + 1.6, -0.5), (sx + 1.6, sy + 1.6), (sx - 1.6, sy + 1.6)])
        e_poly = bb.courtyard(bb.fps['E1']).buffer(0.3)
        bb.e_poly = e_poly
    order = [n for n in FIRST[name] if n in nets]
    spec = SPECIAL[name]
    failed = {}
    rank = {}
    sig_layers = (0, 1, 2)                     # main board: In2 only inside its signal channel (see above)

    def do(net, record=True, pins=None):
        rank.setdefault(net, len(rank))
        ws, layers, via, keep = spec.get(net, ([0.15, 0.1], sig_layers, True, 0))
        if name == 'ana-main' and layers != (0,):
            layers = sig_layers
        t1 = time.time()
        f = R.route_net(net, pins or nets[net], ws[0], 0.1, layers=layers, allow_via=via, widths=ws, keepoff=keep)
        log('   %-14s %s %.1fs' % (net, 'FAILED %s' % f if f else 'ok', time.time() - t1))
        if f and record:
            failed[net] = f
            if os.environ.get('ANA_DEBUG_DIR'):
                R.dump_fail(os.path.join(os.environ['ANA_DEBUG_DIR'], '%s_%s.png' % (name, net)), '%s %s' % (net, f))
        return f

    def is_tht(ref, pin):
        # plated through-hole pads reach the inner planes by themselves
        return any(p.GetNumber() == pin and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH for p in bb.fps[ref].Pads())

    def gnd_fanout(max_d):
        nf = 0
        for ref, pin in nets.get('GND', []):
            fp = bb.fps[ref]
            pads = [p for p in fp.Pads() if p.GetNumber() == pin]
            if not pads or pads[0].GetAttribute() != pcbnew.PAD_ATTRIB_SMD or not pads[0].IsOnLayer(pcbnew.F_Cu):
                continue
            if ref == 'U1' and name == 'ana-main' and pin == '49':
                continue                                  # exposed pad already has 16 thermal vias
            if R.pad_connected('GND', ref, pin):
                continue
            if not R.fanout('GND', ref, pin, w=0.2, max_d=max_d):
                nf += 1
                log('  GND fan-out failed', ref, pin)
        log(name, 'GND fan-out done, failures:', nf)

    if name == 'ana-main':
        # 1) RF on the top layer only (CPWG over the In1 GND plane)
        for net in ('LNA_IN', 'RF_MID', 'RF_FEED'):
            do(net)
        R.block_area(bb.courtyard(bb.fps['E1']).buffer(0.1), nets_allowed=('TOUCH_PAD',), novia=False, layers=(2,))
        R.block_area(bb.courtyard(bb.fps['E1']).buffer(0.3), nets_allowed=(), novia=False, layers=(1,))
        # 2) ESP32 escape pattern: stub + via per pin, staggered so neighbours never collide.
        #    Tip side: the two VDDA pins (46, 43) drop into the 3V3 plane first; XTAL/CAP traces then pass
        #    between those vias; the UART/I2C pins on that side are left to the router.
        cx, cy = 86.825, 3.875
        xe = cx + 2.85                                   # tip-side pad ends
        ty = {k: cy + 1.575 - (k - 39) * 0.35 for k in range(39, 49)}
        # nested L-shaped stubs for the lower tip pins (SCL 39, RXD 40, TXD 41, SDA 42): out, then down to a via
        for k, xr, yv in ((39, xe + 0.35, 6.15), (40, xe + 0.95, 6.15), (41, xe + 1.55, 6.15), (42, xe + 2.15, 6.15)):
            net = bb.netmap.get(('U1', str(k)), '').lstrip('/')
            ok_ = R.stub(net, [(xe - 0.2, ty[k]), (xr, ty[k]), (xr, yv)], w=0.15)
            log('  tip stub', k, net, 'ok' if ok_ else 'FAILED')
        # VDDA 46: up-right into the free slot left by unused pin 47; VDDA 43: along, past SDA's corner, then down
        ok_ = R.stub('3V3', [(xe - 0.2, ty[46]), (xe + 0.05, ty[46]), (xe + 0.75, 2.72)], w=0.15)
        log('  tip stub 46 3V3', 'ok' if ok_ else 'FAILED')
        ok_ = R.stub('3V3', [(xe - 0.2, ty[43]), (xe + 2.75, ty[43]), (xe + 2.75, 5.2)], w=0.15)
        log('  tip stub 43 3V3', 'ok' if ok_ else 'FAILED')
        # hinge side: parallel fingers into the free centre lane that end in vias staggered so that every
        # finger still running keeps its clearance to the vias of the ones that already ended
        xp = cx - 2.85 + 0.2                             # inside the pad
        yk = {k: cy - 1.575 + (k - 15) * 0.35 for k in range(15, 25)}
        fingers = {15: [(xp, yk[15]), (83.0, yk[15]), (82.6, 1.95)],
                   16: [(xp, yk[16]), (80.9, yk[16])],
                   17: [(xp, yk[17]), (82.2, yk[17]), (81.95, 3.25)],
                   19: [(xp, yk[19]), (81.45, yk[19]), (81.3, 3.55)],
                   20: [(xp, yk[20]), (80.4, yk[20])],
                   21: [(xp, yk[21]), (82.2, yk[21]), (81.95, 4.65)],
                   23: [(xp, yk[23]), (77.4, yk[23])]}
        for pin, pts in fingers.items():
            net = bb.netmap.get(('U1', str(pin)), '').lstrip('/')
            ok_ = R.stub(net, pts, w=0.15)
            log('  hinge escape', pin, net, 'ok' if ok_ else 'FAILED')
        net24 = bb.netmap.get(('U1', '24'), '').lstrip('/')
        ok_ = R.stub_track(net24, [(xp, yk[24]), (cx - 2.85 - 1.35, yk[24])], w=0.15)
        log('  hinge finger 24', net24, 'ok' if ok_ else 'FAILED')
        plan = []
        for pin, x in ((4, 87.45), (9, 86.3), (13, 84.9)):                                     # top edge
            plan.append((str(pin), (x, 0.58)))
        for pin, x in ((34, 87.35), (36, 88.05), (37, 88.75)):                                     # bottom edge
            plan.append((str(pin), (x, 7.15)))
        plan.append(('1', (89.75, 1.5)))                                                           # 3V3 corner
        nfo = 0
        for pin, pref in plan:
            net = bb.netmap.get(('U1', pin), '').lstrip('/')
            if not net:
                continue
            if not R.fanout(net, 'U1', pin, w=0.15, max_d=2.6, prefer=pref, tol=0.9 if pin not in ('43', '46') else 0.25):
                nfo += 1
                log('  ESP32 escape failed', pin, net)
        log(name, 'ESP32 escape vias placed:', len(plan) - nfo, 'of', len(plan))
        # escape via for the LED switch drain (to the right, off the touch electrode)
        esc = []
        px, py = bb.pad_pos('Q2', '3'); esc.append((('Q2', '3'), [(px + 0.56, py), (px + 1.86, py)]))
        for key, pts in esc:
            net = bb.netmap.get(key, '').lstrip('/')
            ok_ = R.stub(net, pts, w=0.15)
            log('  escape', key, net, 'ok' if ok_ else 'FAILED')
        # EN pull-up and EN capacitor sit side by side in the top row: join their EN pads directly
        ok_ = R.stub_track('EN', [bb.pad_pos('R2', '2'), bb.pad_pos('C12', '1')], w=0.2)
        log('  EN link R2-C12', 'ok' if ok_ else 'FAILED')
        # VSYS trunk, 0.4 mm on B.Cu: from a via at the regulator input along the bottom edge (0.6 mm inside
        # it), over the frame-link pad row and down into J1 pad 1 (VSYS)
        jx1, jy1 = bb.pad_pos('J1', '1')
        xb = 14.6
        trunk = [(60.9, 7.55), (xb, temple.h_straight(xb) - 0.62), (xb, 6.4), (jx1, 6.4), (jx1, jy1 - 0.5)]
        ok_ = R.fixed_path('VSYS', trunk, layer=2, w=0.4, vias=[(60.9, 7.55)])
        log('  VSYS trunk', 'ok' if ok_ is True else 'FAILED at %s' % (ok_,))
        # every GND pad drops its own via into the In1 plane before any signal
        gnd_fanout(1.6)
        # no vias between the ESP32's hinge-side fingers: they leave as parallel tracks and spread out first
        R.no_vias(Polygon([(78.0, 2.0), (84.0, 2.0), (84.0, 5.8), (78.0, 5.8)]))
        # 3) short local nets first, then the long lines (the ESP32's cramped tip side first), power last
        # hinge pad rows are fed from one side, so their nets go in order of pad position (far end first):
        # each takes the lane nearest its row and the next one passes outside it (a staircase)
        main_order = ['XTAL_P', 'XTAL_IN', 'XTAL_N', 'CAP1', 'VDD_SDIO', 'VDD3P3', 'SPI_CLK_MCU', 'SPI_MOSI_MCU',
                      'U0TXD', 'BAT_ADC', 'TOUCH_PAD', 'LED1_A', 'LEDX1_A',
                      '3V3', 'VSYS',
                      'TOUCH',
                      'OLED_CS', 'OLED_DC', 'OLED_RST', 'SPI_MOSI', 'SPI_CLK',          # J4 pads 7, 6, 5, 4, 3
                      'U0TXD_R', 'U0RXD', 'CHRG_N', 'IO0', 'EN', 'LED_K',               # J1 pads 8, 7, 6, 5, 4, 2
                      '1V8', 'I2C_SCL', 'I2C_SDA', 'MAX_INT']
        seed = os.environ.get('ANA_SEED', '205')      # 205: the routing order that gives the committed board
        if seed:
            # exploration: shuffle the long lines (the J4 and J1 staircases keep their pad order)
            import random
            rng = random.Random(int(seed))
            head = main_order[:main_order.index('VSYS') + 1]
            tail = main_order[len(head):]
            rng.shuffle(tail)
            for stair in (['OLED_CS', 'OLED_DC', 'OLED_RST', 'SPI_MOSI', 'SPI_CLK'],
                          ['U0TXD_R', 'U0RXD', 'CHRG_N', 'IO0', 'EN', 'LED_K']):
                slots = sorted(tail.index(n) for n in stair)
                for k, n in zip(slots, stair):
                    tail[k] = n
            main_order = head + tail
            log(name, 'order (seed %s):' % seed, tail)
        rest = [n for n in nets if n not in main_order and n not in ('GND', 'LNA_IN', 'RF_MID', 'RF_FEED')]
        rest.sort(key=lambda n: (0 if n in spec else 1, len(nets[n])))
        seq = [n for n in priority if n in nets and n != 'GND']
        seq += [n for n in main_order + rest if n in nets and n not in seq]
        for net in seq:
            do(net)
    elif name in EXTRA:
        gnd_fanout(1.6)
        rest = [n for n in nets if n not in order and n != 'GND']
        rest.sort(key=lambda n: (0 if n in spec else 1, len(nets[n])))
        for net in order + rest:
            do(net)
    else:
        # CP2102N USB pins (left side): short stubs to staggered vias, so D+ and D- both reach the inner layers
        for pin, dy in (('3', -0.3), ('4', 0.3)):
            px, py = bb.pad_pos('U4', pin)
            net = bb.netmap.get(('U4', pin), '').lstrip('/')
            ok_ = R.stub(net, [(px + 0.2, py), (px - 0.55, py), (px - 0.9, py + dy)], w=0.15)
            log('  CP2102N USB escape', pin, net, 'ok' if ok_ else 'FAILED')
        # CP2102N GND pin 2 joins the exposed pad (its thermal vias reach the In1 plane) with a short F.Cu link
        px, py = bb.pad_pos('U4', '2')
        ux, uy = pcbgen.local(bb.fps['U4'].GetPosition())
        ok_ = R.stub_track('GND', [(px, py), (ux - 0.9 if px < ux else ux + 0.9, py)], w=0.2)
        log('  CP2102N GND pin 2 -> exposed pad', 'ok' if ok_ else 'FAILED')
        jx, jy = pcbgen.local(bb.fps['J1'].GetPosition())
        for dx, sx in ((2.45, 2.15), (-2.45, -2.15)):          # VBUS pads A9/B4 and A4/B9
            ok_ = R.stub('VBUS', [(jx + dx, jy - 3.5), (jx + sx, jy - 3.15), (jx + sx, jy - 1.6)], w=0.15)
            log('  USB-C VBUS escape', dx, 'ok' if ok_ else 'FAILED')
        for net in order:
            if net.startswith('RF') or net == 'LNA_IN':
                do(net)
        gnd_fanout(1.6)
        for net in order:
            if not (net.startswith('RF') or net == 'LNA_IN'):
                do(net)
        rest = [n for n in nets if n not in order and n != 'GND']
        rest.sort(key=lambda n: (0 if n in spec else 1, len(nets[n])))
        for net in rest:
            do(net)
    # rip-up and reroute: for each stuck net, find the nets whose tracks lie on its cheapest path (a search that
    # may cross movable copper at a cost), lift them, route the stuck net, then route the lifted nets again.
    # Each attempt is undone if it leaves more pins unrouted than before.
    def score():
        return sum(len(v) for v in failed.values())

    seen = set()
    for rnd in range(int(os.environ.get('ANA_RIPUP', '4'))):
        sideways = 0
        stuck = list(failed)
        if not stuck:
            break
        log(name, 'rip-up round', rnd + 1, 'failed nets:', {k: len(v) for k, v in failed.items()})
        improved = False
        for net in stuck:
            if net not in failed:
                continue                                   # fixed on the way by an earlier attempt
            ws, layers, via, keep = spec.get(net, ([0.15, 0.1], sig_layers, True, 0))
            if name == 'ana-main' and layers != (0,):
                layers = sig_layers
            blk = set()
            for p in failed[net]:
                blk |= R.blockers(net, p, nets[net], ws[-1], 0.1, layers, via)
            blk -= {'GND', '3V3', 'LNA_IN', 'RF_MID', 'RF_FEED'}
            if not blk:
                log('   rip-up %-12s no path even through other nets' % net)
                continue
            before = score()
            snap, saved = R.snapshot(), {k: list(v) for k, v in failed.items()}
            for m in blk | {net}:
                failed.pop(m, None)
                R.ripup(m)
            f = do(net, record=False)
            if f:
                failed[net] = f
            newly = []
            for m in sorted(blk, key=lambda n: rank.get(n, 999)):
                f2 = do(m, record=False)
                if f2:
                    failed[m] = f2
                    newly.append(m)
            # one level deeper: a displaced net that no longer fits gets its own blockers moved (never the
            # stuck net or the nets already displaced for it)
            for m in newly:
                if m not in failed:
                    continue
                wm = spec.get(m, ([0.15, 0.1], sig_layers, True, 0))
                blk2 = set()
                for p in failed[m]:
                    blk2 |= R.blockers(m, p, nets[m], wm[0][-1], 0.1, sig_layers if wm[1] != (0,) else wm[1], wm[2])
                blk2 -= {net, m, 'GND', '3V3', 'LNA_IN', 'RF_MID', 'RF_FEED'} | blk
                if not blk2:
                    continue
                for q in blk2 | {m}:
                    failed.pop(q, None)
                    R.ripup(q)
                f3 = do(m, record=False)
                if f3:
                    failed[m] = f3
                for q in sorted(blk2, key=lambda n: rank.get(n, 999)):
                    f4 = do(q, record=False)
                    if f4:
                        failed[q] = f4
            after = score()
            state = frozenset((k, tuple(v)) for k, v in failed.items())
            sideways_ok = (after == before and sideways < 3 and state not in seen and
                           state != frozenset((k, tuple(v)) for k, v in saved.items()))
            if after < before or sideways_ok:
                seen.add(state)
                improved = True
                if after == before:
                    sideways += 1
                log('   rip-up %-12s moving %s: %d -> %d unrouted, kept%s' %
                    (net, sorted(blk), before, after, ' (sideways)' if after == before else ''))
            else:
                R.restore(snap)
                failed.clear()
                failed.update(saved)
                log('   rip-up %-12s moving %s: %d -> %d unrouted, undone' % (net, sorted(blk), before, after))
        if not improved:
            break
    if name == 'ana-main':
        gnd_fanout(2.4)
    log(name, 'routing done: tracks', R.track_count, 'vias', len(R.vias), 'failed', failed, '%.0fs' % (time.time() - t0))

    # 2) zones: solid GND plane on In1, GND pours on the other layers
    pts = [(x, y) for x, y in temple.points]
    for layer, prio in ((pcbnew.In1_Cu, 0), (pcbnew.F_Cu, 1), (pcbnew.In2_Cu, 1), (pcbnew.B_Cu, 1)):
        net = '3V3' if (name == 'ana-main' and layer == pcbnew.In2_Cu) else 'GND'
        bb.zone(net, layer, pts, priority=prio, clearance=0.2, min_w=0.15,
                thermal=(layer == pcbnew.F_Cu or layer == pcbnew.B_Cu), name=net)
    if name == 'ana-main':
        ant = list(bb.ant_zone.exterior.coords)
        bb.keepout(ant, [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu], no_tracks=False, no_vias=True,
                   no_pours=True, name='ANTENNA_KEEPOUT')
        ep = list(bb.e_poly.exterior.coords)
        bb.keepout(ep, [pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu], no_tracks=False, no_vias=False, no_pours=True,
                   name='TOUCH_ELECTRODE_NO_GND')
        # SHT40: no copper pour on the outer layers around the sensor (less heat spreading to it)
        bb.keepout(list(bb.sht_poly.exterior.coords), [pcbnew.F_Cu, pcbnew.B_Cu],
                   no_tracks=False, no_vias=False, no_pours=True, name='SHT40_NO_POUR')
    # stitching vias tie the pours to the plane
    avoid = bb.ant_zone if name == 'ana-main' else None
    if name == 'ana-main':
        avoid = bb.ant_zone.union(bb.e_poly).union(bb.sht_poly)
    if avoid is not None:
        avoid = avoid.buffer(VIA_KEEP)
    # GND pads that could not get a via before routing (crowded spots): try again further out
    for ref, pin in nets.get('GND', []):
        pads = [p for p in bb.fps[ref].Pads() if p.GetNumber() == pin]
        if pads and pads[0].GetAttribute() == pcbnew.PAD_ATTRIB_SMD and not R.pad_connected('GND', ref, pin):
            if not (name == 'ana-main' and ref == 'U1') and not R.fanout('GND', ref, pin, w=0.2, max_d=3.5):
                log('  late GND fan-out failed', ref, pin)
    ns = R.stitch('GND', pitch=2.2, avoid=avoid)
    log(name, 'stitching vias', ns)
    pv, ps, pt = R.prune(keep_nets=('GND', '3V3') if name == 'ana-main' else ('GND',))
    log(name, 'pruned dangling copper: vias', pv, 'segments', ps, 'shortened', pt)
    R.flush()                            # routed copper -> KiCad tracks and vias
    # silkscreen labels
    if name in EXTRA:
        bb.text(*EXTRA[name]['silk'], layer=pcbnew.B_SilkS, mirror=True)
    elif name == 'ana-main':
        bb.text('ANA V1 RIGHT - ESP32 - rev 1.0', 70.0, 4.0, 0.8, layer=pcbnew.B_SilkS, mirror=True)
    else:
        bb.text('ANA V1 LEFT - POWER - rev 1.0', 70.0, 4.0, 0.8, layer=pcbnew.B_SilkS, mirror=True)
    out = os.path.join(d, name + '.kicad_pcb')
    bb.save(out)
    project.write(d, name)               # Save() rewrote the .kicad_pro with defaults -> restore our rules
    # zone filling needs a board loaded with its project (in-memory boards crash the filler)
    b2 = pcbnew.LoadBoard(out)
    pcbnew.ZONE_FILLER(b2).Fill(b2.Zones())
    b2.Save(out)
    project.write(d, name)
    log(name, 'saved + zones filled', out, '%.0fs' % (time.time() - t0))
    return failed


def checks(name):
    d = os.path.join(HW, name + os.environ.get('ANA_TRY', ''))
    rep = {}
    erc = os.path.join(d, 'erc.json')
    subprocess.run(['kicad-cli', 'sch', 'erc', '--severity-all', '--format', 'json', '-o', erc,
                    os.path.join(d, name + '.kicad_sch')], capture_output=True)
    e = json.load(open(erc))
    rep['erc'] = sum(len(s.get('violations', [])) for s in e.get('sheets', []))
    drc = os.path.join(d, 'drc.json')
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--schematic-parity', '--severity-all', '--format', 'json',
                    '-o', drc, os.path.join(d, name + '.kicad_pcb')], capture_output=True)
    j = json.load(open(drc))
    rep['drc'] = len(j.get('violations', []))
    rep['unconnected'] = len(j.get('unconnected_items', []))
    rep['parity'] = len(j.get('schematic_parity', []))
    by = {}
    for v in j.get('violations', []):
        by[(v['type'], v['severity'])] = by.get((v['type'], v['severity']), 0) + 1
    rep['by_type'] = {f'{k[0]}/{k[1]}': n for k, n in sorted(by.items())}
    return rep


def build_best(name, attempts=3):
    """Route from scratch several times; nets that failed move to the front of the queue each time.
    Keeps the attempt with the fewest unrouted pins."""
    import shutil
    d = os.path.join(HW, name + os.environ.get('ANA_TRY', ''))
    best, priority = None, []
    for a in range(attempts):
        failed = build(name, priority)
        score = sum(len(v) for v in failed.values())
        log(name, 'ATTEMPT', a + 1, 'unrouted pins:', score, 'priority was', priority)
        if best is None or score < best[0]:
            best = (score, a)
            for ext in ('.kicad_pcb', '.kicad_pro'):
                shutil.copy(os.path.join(d, name + ext), os.path.join(d, name + ext + '.best'))
        if score == 0:
            break
        priority = list(dict.fromkeys([n for n in failed if n != 'GND'] + priority))
    for ext in ('.kicad_pcb', '.kicad_pro'):
        shutil.move(os.path.join(d, name + ext + '.best'), os.path.join(d, name + ext))
    log(name, 'BEST attempt', best[1] + 1, 'unrouted pins:', best[0])


if __name__ == '__main__':
    symlib.build_custom_lib()
    fpgen.build()
    names = sys.argv[1:] or list(circuits.BOARDS)
    for n in names:
        if n == 'ana-main':
            build_best(n)
        else:
            build(n)
        log(n, 'CHECKS', checks(n))
