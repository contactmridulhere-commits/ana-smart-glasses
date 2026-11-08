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

# net -> (preferred widths, layers, allow vias, keep-off halo)
SPECIAL = {
    'ana-main': {
        'LNA_IN': ([0.2], (0,), False, 0.15), 'RF_MID': ([0.2], (0,), False, 0.15),
        'RF_FEED': ([0.2], (0,), False, 0.15),
        '3V3': ([0.3, 0.2, 0.15], (0, 1, 2), True, 0), 'VSYS': ([0.4, 0.3, 0.2], (0, 1, 2), True, 0),
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
FIRST = {'ana-main': ['LNA_IN', 'RF_MID', 'RF_FEED', 'XTAL_P', 'XTAL_IN', 'XTAL_N', 'CAP1', 'CAP2', 'VDD_SDIO',
                      'VDD3P3', 'EN', 'IO0', 'OLED_DC', 'OLED_RST', 'OLED_CS', 'SPI_CLK_MCU', 'SPI_MOSI_MCU',
                      'I2C_SCL', 'I2C_SDA', 'BAT_ADC', 'MAX_INT', 'VBUS_SENSE', 'TOUCH', 'CHRG_N', 'U0TXD', 'U0RXD',
                      'USB_DP', 'USB_DN', 'CP_RST', 'RTS_N', 'DTR_N', 'U0TXD_R', 'Q1_B1', 'Q1_B2', '3V3', 'VSYS'],
         'ana-power': ['VBUS', 'USB_DP', 'USB_DN', 'CC1', 'CC2', 'PROT_D', 'BATN', 'OD', 'OC', 'CS', 'DW_VCC']}


def log(*a):
    print(*a, flush=True)


def board_poly(bb):
    poly = bb.t.poly
    for k in bb.keepouts_cut if hasattr(bb, 'keepouts_cut') else []:
        poly = poly.difference(k)
    return poly


def build(name):
    t0 = time.time()
    meta = circuits.BOARDS[name]
    comps = meta['build']()
    d = os.path.join(HW, name)
    project.write(d, name)
    notes = [
        'Board: 4-layer 0.8 mm FR-4, JLCPCB rules (0.1/0.1 mm, vias 0.4/0.2 mm). All SMT parts on F (top) side.',
        'Frame link (8 wires, same order on both temples): 1 VSYS 2 GND 3 USB_DP 4 USB_DN 5 GND 6 VBUS 7 CHRG_N 8 LED_K',
    ]
    if name == 'ana-main':
        notes.append('ESP32 GPIO map: OLED CLK=4 MOSI=23 CS=15 DC=26 RST=27 | I2C SDA=21 SCL=22 | MAX_INT=5 | '
                     'TOUCH=14 | BAT_ADC=33 | VBUS=35 | CHRG=13 | 3V3 plane on In2')
    schgen.generate(name, meta['title'], meta['rev'], comps, os.path.join(d, name + '.kicad_sch'), notes)
    log(name, 'schematic written')

    # pad nets exactly as KiCad names them (sheet prefix, unconnected-(...) names) -> clean parity check
    xml = os.path.join(d, name + '.net.xml')
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', xml,
                    os.path.join(d, name + '.kicad_sch')], check=True, capture_output=True)
    import xml.etree.ElementTree as ET
    netmap = {}
    for net in ET.parse(xml).getroot().iter('net'):
        for node in net.iter('node'):
            netmap[(node.get('ref'), node.get('pin'))] = net.get('name')
    os.remove(xml)
    temple = outline.Temple()
    bb = pcbgen.Builder(name, temple, comps, netmap)
    bb.outline()
    (layouts.layout_main if name == 'ana-main' else layouts.layout_power)(bb)
    log(name, 'placed', len(bb.fps), 'parts; overlaps:', bb.check_overlaps())

    # routing grid: board minus internal slots
    poly = temple.poly
    if name == 'ana-main':
        slot = Polygon([(39.6, 2.6), (45.4, 2.6), (45.4, 0.7), (46.2, 0.7), (46.2, 3.4), (39.6, 3.4)])
        poly = poly.difference(slot)
    R = router.Router(bb, poly)
    nets = circuits.nets_of(comps)
    # exit corridors for fine-pitch parts before anything is routed
    if name == 'ana-main':
        for ref in ('U3', 'U4', 'U7', 'Q1', 'U2', 'U5', 'U6'):     # (not U1: its top/bottom bands are too thin)
            R.reserve_escapes(ref)
    else:
        R.reserve_escapes('J1', length=0.7, direction=(0, 1))
        for ref in ('U1', 'U2', 'U3', 'Q1'):
            R.reserve_escapes(ref)
    if name == 'ana-main':
        R.block_area(bb.ant_zone, nets_allowed=('RF_FEED', 'RF_MID'), novia=True)
        e_poly = bb.courtyard(bb.fps['E1']).buffer(0.3)
        bb.e_poly = e_poly
    order = [n for n in FIRST[name] if n in nets]
    spec = SPECIAL[name]
    failed = {}
    sig_layers = (0, 2) if name == 'ana-main' else (0, 1, 2)     # main board: In2 is the 3V3 plane

    def do(net, record=True, pins=None):
        ws, layers, via, keep = spec.get(net, ([0.15, 0.1], sig_layers, True, 0))
        if name == 'ana-main' and layers != (0,):
            layers = sig_layers
        t1 = time.time()
        f = R.route_net(net, pins or nets[net], ws[0], 0.1, layers=layers, allow_via=via, widths=ws, keepoff=keep)
        log('   %-14s %s %.1fs' % (net, 'FAILED %s' % f if f else 'ok', time.time() - t1))
        if f and record:
            failed[net] = f
        return f

    def gnd_fanout(max_d):
        nf = 0
        for ref, pin in nets.get('GND', []):
            fp = bb.fps[ref]
            pads = [p for p in fp.Pads() if p.GetNumber() == pin]
            if not pads or pads[0].GetAttribute() != pcbnew.PAD_ATTRIB_SMD or not pads[0].IsOnLayer(pcbnew.F_Cu):
                continue
            if ref == 'U1' and name == 'ana-main' and pin == '49':
                continue                                  # exposed pad already has 16 thermal vias
            if not R.fanout('GND', ref, pin, w=0.2, max_d=max_d):
                nf += 1
                log('  GND fan-out failed', ref, pin)
        log(name, 'GND fan-out done, failures:', nf)

    if name == 'ana-main':
        # 1) RF on the top layer only (CPWG over the In1 GND plane)
        for net in ('LNA_IN', 'RF_MID', 'RF_FEED'):
            do(net)
        R.block_area(bb.e_poly.buffer(0.5), nets_allowed=('TOUCH_PAD',), novia=True, layers=(2,))
        # 2) ESP32 escape pattern: stub + via per signal pin, staggered rows so neighbours never collide
        cx, cy = 86.825, 3.875
        plan = []
        for pin, row in ((15, 1), (16, 2), (17, 3), (19, 1), (20, 2), (21, 3), (23, 1), (24, 2)):   # hinge side
            plan.append((str(pin), (cx - 2.85 - 0.45 - 0.6 * (row - 1), cy - 1.575 + (pin - 15) * 0.35)))
        for pin, row in ((39, 1), (40, 2), (41, 3), (42, 1)):                                       # ear-tip side
            plan.append((str(pin), (cx + 2.85 + 0.45 + 0.6 * (row - 1), cy + 1.575 - (pin - 39) * 0.35)))
        for pin in (9, 11, 13):                                                                     # top edge
            plan.append((str(pin), (cx + 2.275 - (pin - 1) * 0.35, 0.58)))
        plan.append(('4', (87.45, 0.58)))                                                          # VDD3P3
        for pin in (34, 36):                                                                        # bottom edge
            plan.append((str(pin), (cx - 2.275 + (pin - 25) * 0.35, 7.15)))
        plan.append(('37', (89.75, 6.45)))                                                         # 3V3 corner
        plan.append(('1', (89.75, 1.5)))                                                           # 3V3 corner
        nfo = 0
        for pin, pref in plan:
            net = bb.netmap.get(('U1', pin), '').lstrip('/')
            if not net:
                continue
            if not R.fanout(net, 'U1', pin, w=0.15, max_d=2.4, prefer=pref, tol=0.45):
                nfo += 1
                log('  ESP32 escape failed', pin, net)
        log(name, 'ESP32 escape vias placed:', len(plan) - nfo, 'of', len(plan))
        # 3) every other 3V3 pad drops a via into the In2 3V3 plane
        nf3 = []
        for ref, pin in nets['3V3']:
            if ref == 'U1':
                continue
            if not R.fanout('3V3', ref, pin, w=0.25, max_d=1.8):
                nf3.append((ref, pin))
        log(name, '3V3 plane vias done, failures:', nf3)
        # 4) local ESP32 nets, then connect remaining 3V3 pins to the nearest 3V3 copper
        for net in ('XTAL_P', 'XTAL_IN', 'XTAL_N', 'CAP1', 'CAP2', 'VDD_SDIO', 'VDD3P3'):
            do(net)
        for p in [('U1', '43'), ('U1', '46'), ('U1', '19')] + nf3:
            f = R.route_net('3V3', [p] + [q for q in nets['3V3'] if q != p and q not in nf3], 0.2, 0.1,
                            layers=sig_layers, widths=[0.2, 0.15])
            log('   3V3 %-10s %s' % (p, 'FAILED' if f else 'ok'))
            if f:
                failed.setdefault('3V3', []).extend(f)
        # 5) all signals (ESP32 nets first), GND stubs last
        for net in order:
            if net not in ('LNA_IN', 'RF_MID', 'RF_FEED', '3V3', 'XTAL_P', 'XTAL_IN', 'XTAL_N', 'CAP1', 'CAP2',
                           'VDD_SDIO', 'VDD3P3'):
                do(net)
        rest = [n for n in nets if n not in order and n not in ('GND', '3V3')]
        rest.sort(key=lambda n: (0 if n in spec else 1, len(nets[n])))
        for net in rest:
            do(net)
    else:
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
    # rip-up and reroute: lift the nets around each stuck pin, route the stuck net, re-route the lifted ones
    for rnd in range(3):
        if not failed or '3V3' in failed and len(failed) == 1:
            break
        log(name, 'rip-up round', rnd + 1, 'failed nets:', {k: len(v) for k, v in failed.items()})
        stuck = {k: v for k, v in failed.items() if k != '3V3'}
        for k in stuck:
            failed.pop(k)
        for net, pins in stuck.items():
            cells = [c for p in pins for c in R.pad_cells(*p)]
            lifted = R.nets_near(cells, radius=1.0 + 0.5 * rnd,
                                 exclude=(net, 'LNA_IN', 'RF_MID', 'RF_FEED', '3V3'))
            R.ripup(net)
            for m in lifted:
                R.ripup(m)
            f = do(net, record=False)
            if f:
                failed[net] = f
            for m in lifted:
                f2 = do(m, record=False)
                if f2:
                    failed[m] = f2
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
    # stitching vias tie the pours to the plane
    avoid = bb.ant_zone if name == 'ana-main' else None
    if name == 'ana-main':
        avoid = bb.ant_zone.union(bb.e_poly)
    ns = R.stitch('GND', pitch=2.2, avoid=avoid)
    log(name, 'stitching vias', ns)
    # silkscreen labels
    if name == 'ana-main':
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
    d = os.path.join(HW, name)
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


if __name__ == '__main__':
    symlib.build_custom_lib()
    fpgen.build()
    names = sys.argv[1:] or list(circuits.BOARDS)
    for n in names:
        build(n)
        log(n, 'CHECKS', checks(n))
