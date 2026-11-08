#!/usr/bin/env python3
"""Fabrication + documentation outputs for both boards:
   Gerbers + drill (zipped for JLCPCB), JLCPCB BOM/CPL, schematic PDF, board plots, 3D render,
   and a dimensioned outline drawing (SVG) for designing the temple cover."""
import csv
import math
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import circuits, outline   # noqa: E402

HW = os.path.abspath(os.path.join(HERE, '..'))
FAB = os.path.join(HW, 'fab')
DOCS = os.path.join(HW, 'docs')
OX, OY = 30.0, 60.0
LAYERS = 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts'


def run(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print('FAILED', ' '.join(cmd), r.stdout[-500:], r.stderr[-500:])
    return r


def gerbers(name):
    pcb = os.path.join(HW, name, name + '.kicad_pcb')
    out = os.path.join(FAB, name, 'gerbers')
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    run('kicad-cli', 'pcb', 'export', 'gerbers', '-o', out, '-l', LAYERS, '--subtract-soldermask', pcb)
    run('kicad-cli', 'pcb', 'export', 'drill', '-o', out + '/', '--format', 'excellon', '--excellon-separate-th',
        '--generate-map', '--map-format', 'gerberx2', pcb)
    z = os.path.join(FAB, name, name + '-gerbers-jlcpcb.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(out)):
            zf.write(os.path.join(out, f), f)
    return z


def bom_cpl(name):
    comps = circuits.BOARDS[name]['build']()
    pcb = os.path.join(HW, name, name + '.kicad_pcb')
    # BOM in JLCPCB format (assembled parts only)
    groups = {}
    for c in comps:
        if c.dnp or not c.lcsc:
            continue
        key = (c.value, c.fp.split(':')[-1], c.lcsc)
        groups.setdefault(key, []).append(c.ref)
    bom = os.path.join(FAB, name, name + '-BOM-jlcpcb.csv')
    with open(bom, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        for (val, fp, lcsc), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            w.writerow([val, ','.join(sorted(refs, key=lambda r: (r.rstrip('0123456789'), int(''.join(ch for ch in r if ch.isdigit()) or 0)))), fp, lcsc])
    # full BOM with MPN / notes, including hand-soldered and not-fitted items
    full = os.path.join(FAB, name, name + '-BOM-full.csv')
    with open(full, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Ref', 'Value', 'Footprint', 'LCSC', 'MPN', 'Fitted by', 'Note'])
        for c in comps:
            who = 'DNP (tuning option)' if c.dnp else ('JLCPCB' if c.lcsc else 'you (wire/pad)')
            w.writerow([c.ref, c.value, c.fp, c.lcsc, c.mpn, who, c.desc])
    # placement (CPL) in JLCPCB format
    raw = os.path.join(FAB, name, name + '-pos-raw.csv')
    run('kicad-cli', 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'front',
        '--exclude-dnp', '-o', raw, pcb)
    lc = {c.ref for c in comps if c.lcsc and not c.dnp}
    cpl = os.path.join(FAB, name, name + '-CPL-jlcpcb.csv')
    with open(raw) as fi, open(cpl, 'w', newline='') as fo:
        rd = csv.DictReader(fi)
        w = csv.writer(fo)
        w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for row in rd:
            if row['Ref'] not in lc:
                continue
            w.writerow([row['Ref'], '%.3fmm' % float(row['PosX']), '%.3fmm' % float(row['PosY']),
                        'Top' if row['Side'] == 'top' else 'Bottom', '%.1f' % (float(row['Rot']) % 360)])
    os.remove(raw)
    return bom, cpl


def drawings(name):
    pcb = os.path.join(HW, name, name + '.kicad_pcb')
    sch = os.path.join(HW, name, name + '.kicad_sch')
    os.makedirs(DOCS, exist_ok=True)
    run('kicad-cli', 'sch', 'export', 'pdf', '-o', os.path.join(DOCS, name + '-schematic.pdf'), sch)
    run('kicad-cli', 'pcb', 'export', 'pdf', '--mode-single', '-l',
        'F.Cu,F.Silkscreen,F.Fab,Edge.Cuts', '-o', os.path.join(DOCS, name + '-assembly-top.pdf'), pcb)
    for side in ('top', 'bottom'):
        run('kicad-cli', 'pcb', 'render', '--side', side, '--quality', 'high', '-w', '2400', '-h', '700',
            '--zoom', '1.0', '-o', os.path.join(DOCS, '%s-render-%s.png' % (name, side)), pcb)


def outline_svg():
    """Dimensioned outline for the temple cover (same outline for both boards)."""
    t = outline.Temple()
    c = t.cfg
    s = 6.0                          # px per mm
    pad = 30
    x0, y0, x1, y1 = t.bbox()
    W, H = (x1 - x0) * s + 2 * pad + 60, (y1 - y0) * s + 2 * pad + 120

    def P(x, y):
        return (x - x0) * s + pad, (y - y0) * s + pad + 40
    pts = ' '.join('%.1f,%.1f' % P(x, y) for x, y in t.points)
    el = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
          'font-family="Helvetica,Arial,sans-serif" font-size="12">' % (W, H, W, H),
          '<rect width="100%" height="100%" fill="white"/>',
          '<text x="%d" y="22" font-size="15" font-weight="bold">ANA V1 temple PCB outline (both temples, '
          'PCB = cover inside minus %.1f mm each side)</text>' % (pad, c['pcb_clearance_top_bottom']),
          '<polygon points="%s" fill="#dfeedd" stroke="#1b5e20" stroke-width="1.5"/>' % pts]

    def dim(a, b, label, off=(0, 0)):
        (ax, ay), (bx, by) = P(*a), P(*b)
        ax += off[0]; bx += off[0]; ay += off[1]; by += off[1]
        el.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c62828" stroke-width="1" '
                  'marker-start="url(#a)" marker-end="url(#a)"/>' % (ax, ay, bx, by))
        el.append('<text x="%.1f" y="%.1f" fill="#c62828" text-anchor="middle">%s</text>' %
                  ((ax + bx) / 2 + (8 if ax == bx else 0), (ay + by) / 2 - 4, label))
    el.insert(1, '<defs><marker id="a" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">'
                 '<circle cx="3" cy="3" r="1.6" fill="#c62828"/></marker></defs>')
    dim((0, -2), (t.Lp, -2), 'straight %.1f mm (cover A = %.0f)' % (t.Lp, c['A_straight_length']))
    dim((-2, 0), (-2, t.h1), '%.1f' % t.h1, off=(-8, 0))
    dim((t.Lp - 4, 0), (t.Lp - 4, t.h_straight(t.Lp - 4)), '%.1f' % t.h_straight(t.Lp - 4))
    a, b = t.tip(0, -2.5), t.tip(t.Lt, -2.5)
    dim(a, b, 'tip %.1f mm @ %.0f deg (cover B = %.0f)' % (t.Lt, c['theta_bend_deg'], c['B_tip_length']))
    tx, ty = P(*t.tip(t.Lt - 1, t.h3 / 2))
    el.append('<text x="%.1f" y="%.1f" fill="#c62828">%.1f</text>' % (tx + 14, ty + 18, t.h3))
    notes = ['Board thickness 0.8 mm; tallest part 1.55 mm (MAX30102) on the main board, 3.3 mm (USB-C) on the power board.',
             'Main (right) board: parts face the head; MAX30102 window at x = 50-56 mm; touch electrode (10 x 4 mm) on the outer face at x = 26.5-36.5 mm;',
             '   SHT40 vent at x = 41-44 mm top edge; antenna zone = last 9.5 mm of the tip (keep metal/wires away).',
             'Power (left) board: USB-C opening on the bottom edge at x = 16-25 mm; slide switches on the top edge at x = 54 mm (POWER)',
             '   and x = 79.5 mm (BT); charge LEDs at x = 40-43 mm; LiPo (~3.5 x 7 x 30 mm) sits in the ear tip, pads at x = 97-101 mm.',
             'x is measured from the hinge end of the PCB along the straight top edge.']
    for i, n in enumerate(notes):
        el.append('<text x="%d" y="%.1f">%s</text>' % (pad, H - 100 + i * 16, n))
    el.append('</svg>')
    os.makedirs(DOCS, exist_ok=True)
    p = os.path.join(DOCS, 'temple-outline-dimensions.svg')
    open(p, 'w').write('\n'.join(el))
    return p


if __name__ == '__main__':
    names = sys.argv[1:] or list(circuits.BOARDS)
    for n in names:
        os.makedirs(os.path.join(FAB, n), exist_ok=True)
        print(n, gerbers(n))
        print(n, bom_cpl(n))
        drawings(n)
    print(outline_svg())
