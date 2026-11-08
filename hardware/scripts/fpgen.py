"""Custom footprints for the ANA temple boards (written as .kicad_mod S-expressions)."""
import os
import uuid

from sexpr import Sym, dump

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, '..', 'lib', 'ana.pretty')
NS = uuid.UUID('1d4f0f2e-8f0e-4a55-9a0b-55d2d1c0a0f1')


def U(*p):
    return [Sym('uuid'), str(uuid.uuid5(NS, '/'.join(map(str, p))))]


def font(size=0.6, th=0.1):
    return [Sym('effects'), [Sym('font'), [Sym('size'), size, size], [Sym('thickness'), th]]]


def prop(name, val, x, y, layer, hide=False, size=0.6):
    p = [Sym('property'), name, val, [Sym('at'), x, y, 0], [Sym('layer'), layer], U(name, val, x, y), font(size)]
    if hide:
        p.insert(5, [Sym('hide'), Sym('yes')])
    return p


def rect(x0, y0, x1, y1, layer, w=0.1, fill=False):
    return [Sym('fp_rect'), [Sym('start'), x0, y0], [Sym('end'), x1, y1],
            [Sym('stroke'), [Sym('width'), w], [Sym('type'), Sym('solid')]],
            [Sym('fill'), Sym('yes' if fill else 'no')], [Sym('layer'), layer], U('r', x0, y0, x1, y1, layer)]


def line(x0, y0, x1, y1, layer, w=0.12):
    return [Sym('fp_line'), [Sym('start'), x0, y0], [Sym('end'), x1, y1],
            [Sym('stroke'), [Sym('width'), w], [Sym('type'), Sym('solid')]], [Sym('layer'), layer], U('l', x0, y0, x1, y1, layer)]


def circle(x, y, r, layer, w=0.12, fill=True):
    return [Sym('fp_circle'), [Sym('center'), x, y], [Sym('end'), x + r, y],
            [Sym('stroke'), [Sym('width'), w], [Sym('type'), Sym('solid')]], [Sym('fill'), Sym('yes' if fill else 'no')],
            [Sym('layer'), layer], U('c', x, y, r, layer)]


def text(s, x, y, layer, size=0.5, rot=0):
    return [Sym('fp_text'), Sym('user'), s, [Sym('at'), x, y, rot], [Sym('layer'), layer], U('t', s, x, y, layer),
            font(size, size * 0.15)]


def pad(num, kind, shape, x, y, w, h, layers, rr=None, extra=None):
    p = [Sym('pad'), num, Sym(kind), Sym(shape), [Sym('at'), x, y], [Sym('size'), w, h],
         [Sym('layers')] + list(layers)]
    if rr is not None:
        p.append([Sym('roundrect_rratio'), rr])
    if extra:
        p += extra
    p.append(U('pad', num, x, y))
    return p


def footprint(name, descr, items, attr=('smd',), tags=''):
    fp = [Sym('footprint'), name, [Sym('version'), 20241229], [Sym('generator'), 'ana_gen'],
          [Sym('generator_version'), '1.0'], [Sym('layer'), 'F.Cu'], [Sym('descr'), descr], [Sym('tags'), tags],
          prop('Reference', 'REF**', 0, -1.5, 'F.SilkS'), prop('Value', name, 0, 1.5, 'F.Fab'),
          prop('Footprint', '', 0, 0, 'F.Fab', hide=True), prop('Datasheet', '', 0, 0, 'F.Fab', hide=True),
          prop('Description', descr, 0, 0, 'F.Fab', hide=True),
          [Sym('attr')] + [Sym(a) for a in attr]] + items + [[Sym('embedded_fonts'), Sym('no')]]
    with open(os.path.join(LIB, name + '.kicad_mod'), 'w', encoding='utf-8') as f:
        f.write(dump(fp) + '\n')


SMD = ('F.Cu', 'F.Paste', 'F.Mask')
SMD_NOPASTE = ('F.Cu', 'F.Mask')


def build():
    os.makedirs(LIB, exist_ok=True)
    # --- Yageo ANT3216LL00R2400A chip antenna (feed S1 = pad 1, open end S2 = pad 2 + 2.5 mm stub per Yageo eval board)
    items = [pad('1', 'smd', 'rect', -1.45, 0, 0.7, 1.7, SMD), pad('2', 'smd', 'rect', 1.45, 0, 0.7, 1.7, SMD),
             pad('2', 'smd', 'rect', 3.05, 0, 2.5, 1.0, ('F.Cu',)),
             rect(-1.6, -0.8, 1.6, 0.8, 'F.Fab', 0.1), rect(-2.05, -1.1, 4.55, 1.1, 'F.CrtYd', 0.05),
             line(-1.0, -1.0, 1.0, -1.0, 'F.SilkS'), line(-1.0, 1.0, 1.0, 1.0, 'F.SilkS'),
             text('ANT', 0, 0, 'F.Fab', 0.5)]
    footprint('ANT_Yageo_ANT3216LL00R2400A', 'Yageo ANT3216LL00R2400A 2.4 GHz chip antenna, pad 1 = feed, pad 2 = open end '
              'with 2.5 mm tuning stub (Yageo eval layout). Keep >=2 mm ground clearance around.', items)
    # --- YLED0602WSIDE side-view LED: pad1 = K (datasheet pin 2), pad2 = A (datasheet pin 1); light exits towards +X
    items = [pad('1', 'smd', 'rect', 0, -0.725, 0.75, 0.75, SMD), pad('2', 'smd', 'rect', 0, 0.725, 0.75, 0.75, SMD),
             rect(-0.375, -0.8, 0.725, 0.8, 'F.Fab', 0.08), line(0.725, -0.5, 0.725, 0.5, 'F.Fab', 0.15),
             rect(-0.6, -1.3, 0.95, 1.3, 'F.CrtYd', 0.05), line(-0.55, -1.25, 0.3, -1.25, 'F.SilkS', 0.1),
             text('K', -0.9, -0.725, 'F.Fab', 0.4)]
    footprint('LED_SideView_1.6x0.75mm_YLED0602', 'Side-view LED 1.6x0.75x1.1 mm (Yongyutai YLED0602WSIDE). '
              'Pad 1 cathode, pad 2 anode, light exits at +X edge.', items)
    # --- wire pads
    for n in (2, 7, 8):
        pitch = 1.5
        items = []
        x0 = -(n - 1) * pitch / 2
        for i in range(n):
            items.append(pad(str(i + 1), 'smd', 'roundrect', x0 + i * pitch, 0, 1.0, 1.8, SMD_NOPASTE, rr=0.15))
        items.append(circle(x0, -1.12, 0.12, 'F.SilkS', 0.08))
        items.append(rect(x0 - 0.75, -1.3, -x0 + 0.75, 1.15, 'F.CrtYd', 0.05))
        items.append(rect(x0 - 0.5, -0.9, -x0 + 0.5, 0.9, 'F.Fab', 0.08))
        footprint('WirePads_1x%02d_P1.50mm' % n, '%d solder pads 1.0x1.8 mm at 1.5 mm pitch for hand-soldered '
                  'wires (no paste)' % n, items, attr=('smd', 'exclude_from_pos_files', 'exclude_from_bom'))
    # --- debug test pads: 6 round pads in a row
    items = []
    pitch = 1.3
    for i in range(6):
        items.append(pad(str(i + 1), 'smd', 'circle', (i - 2.5) * pitch, 0, 0.9, 0.9, SMD_NOPASTE))
    items.append(circle(-2.5 * pitch - 0.8, -0.55, 0.12, 'F.SilkS', 0.1))
    items.append(rect(-2.5 * pitch - 0.65, -0.65, 2.5 * pitch + 0.65, 0.65, 'F.CrtYd', 0.05))
    footprint('TestPads_1x06_P1.30mm', 'Bring-up pads 3V3 GND TX RX EN IO0 (pogo or wire)', items,
              attr=('smd', 'exclude_from_pos_files', 'exclude_from_bom'))
    # --- touch electrode (placed on the BACK side so the copper is on the outer face)
    items = [pad('1', 'smd', 'roundrect', 0, 0, 12.0, 5.0, ('F.Cu',), rr=0.2),
             rect(-6.25, -2.75, 6.25, 2.75, 'F.CrtYd', 0.05), text('TOUCH', 0, 0, 'F.Fab', 0.8)]
    footprint('TouchElectrode_5x12mm_Back', 'Capacitive touch electrode 12x5 mm, copper only, covered by solder '
              'mask. Place on the back (outer) side.', items, attr=('smd', 'exclude_from_pos_files', 'exclude_from_bom'))
    # --- battery tab pads
    items = [pad('1', 'smd', 'roundrect', -1.75, 0, 2.0, 2.6, SMD_NOPASTE, rr=0.15),
             pad('2', 'smd', 'roundrect', 1.75, 0, 2.0, 2.6, SMD_NOPASTE, rr=0.15),
             text('+', -1.75, -1.9, 'F.SilkS', 0.8), text('-', 1.75, -1.9, 'F.SilkS', 0.8),
             rect(-3.0, -1.55, 3.0, 1.55, 'F.CrtYd', 0.05)]
    footprint('BatteryPads_2x2.5mm', 'LiPo cell tab/wire pads: 1 = + , 2 = -', items,
              attr=('smd', 'exclude_from_pos_files', 'exclude_from_bom'))


if __name__ == '__main__':
    build()
    print(sorted(os.listdir(LIB)))
