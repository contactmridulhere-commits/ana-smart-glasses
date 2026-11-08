"""Symbol handling: load KiCad library symbols (flattening `extends`),
build the project's custom symbols, and report pin geometry."""
import copy
import os

from sexpr import Sym, parse, dump, find, find_all

KICAD_SYM_DIR = '/usr/share/kicad/symbols'
HERE = os.path.dirname(os.path.abspath(__file__))
CUSTOM_LIB = os.path.join(HERE, '..', 'lib', 'ana.kicad_sym')

_lib_cache = {}


def _load_lib(path):
    if path not in _lib_cache:
        tree = parse(open(path, encoding='utf-8').read())
        _lib_cache[path] = {s[1]: s for s in find_all(tree, 'symbol')}
    return _lib_cache[path]


def _lib_path(lib):
    if lib == 'ana':
        return CUSTOM_LIB
    return os.path.join(KICAD_SYM_DIR, lib + '.kicad_sym')


def _flatten(lib_syms, name):
    sym = copy.deepcopy(lib_syms[name])
    ext = find(sym, 'extends')
    if not ext:
        return sym
    parent = _flatten(lib_syms, ext[1])
    out = copy.deepcopy(parent)
    out[1] = name
    # child properties override parent ones
    child_props = {p[1]: p for p in find_all(sym, 'property')}
    for i, item in enumerate(out):
        if isinstance(item, list) and item and item[0] == 'property' and item[1] in child_props:
            out[i] = child_props.pop(item[1])
    insert_at = max(i for i, it in enumerate(out) if isinstance(it, list) and it and it[0] == 'property') + 1
    for p in child_props.values():
        out.insert(insert_at, p)
        insert_at += 1
    for item in out:
        if isinstance(item, list) and item and item[0] == 'symbol':
            item[1] = item[1].replace(parent[1] + '_', name + '_', 1)
    return out


def get_symbol(lib_id):
    lib, name = lib_id.split(':', 1)
    syms = _load_lib(_lib_path(lib))
    if name not in syms:
        raise KeyError('symbol %s not found' % lib_id)
    return _flatten(syms, name)


def lib_symbol_entry(lib_id):
    """Return the flattened symbol renamed to the full lib_id (schematic lib_symbols form)."""
    s = get_symbol(lib_id)
    s[1] = lib_id
    return s


def pins(lib_id, unit=1):
    """Return {number: dict(x, y, angle, length, type, name)} in symbol coordinates (y up)."""
    s = get_symbol(lib_id)
    out = {}
    for sub in find_all(s, 'symbol'):
        # sub-symbol names end with _<unit>_<style>; unit 0 = common to all units
        parts = sub[1].rsplit('_', 2)
        u = int(parts[-2])
        if u not in (0, unit):
            continue
        for p in find_all(sub, 'pin'):
            at = find(p, 'at')
            ln = find(p, 'length')
            nm = find(p, 'name')
            num = find(p, 'number')
            out[str(num[1])] = dict(x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0,
                                   length=float(ln[1]) if ln else 2.54, type=str(p[1]), name=str(nm[1]))
    return out


def body_bbox(lib_id, unit=1):
    """Rough bounding box (xmin, ymin, xmax, ymax) in symbol coords of graphics+pins."""
    s = get_symbol(lib_id)
    xs, ys = [], []

    def walk(n):
        if not isinstance(n, list):
            return
        if n and n[0] in ('xy', 'start', 'end', 'at', 'center', 'mid') and len(n) >= 3:
            try:
                xs.append(float(n[1])); ys.append(float(n[2]))
            except (TypeError, ValueError):
                pass
        for c in n:
            if isinstance(c, list) and not (c and c[0] == 'property'):
                walk(c)
    for sub in find_all(s, 'symbol'):
        parts = sub[1].rsplit('_', 2)
        if int(parts[-2]) in (0, unit):
            walk(sub)
    if not xs:
        return (-2.54, -2.54, 2.54, 2.54)
    return (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------------------------------------------
# custom symbols
# ----------------------------------------------------------------------------------------------

def _prop(name, value, x, y, hide=False, size=1.27):
    p = [Sym('property'), name, value, [Sym('at'), x, y, 0],
         [Sym('effects'), [Sym('font'), [Sym('size'), size, size]]]]
    if hide:
        p[-1].append([Sym('hide'), Sym('yes')])
    return p


def _pin(ptype, x, y, angle, name, number, length=2.54):
    return [Sym('pin'), Sym(ptype), Sym('line'), [Sym('at'), x, y, angle], [Sym('length'), length],
            [Sym('name'), name, [Sym('effects'), [Sym('font'), [Sym('size'), 1.016, 1.016]]]],
            [Sym('number'), number, [Sym('effects'), [Sym('font'), [Sym('size'), 1.016, 1.016]]]]]


def box_symbol(name, left, right, ref='U', value=None, footprint='', datasheet='', desc='', width=15.24,
               top=None, bottom=None):
    """left/right: lists of (number, name, type) from top to bottom; None entries leave a gap."""
    pitch = 2.54
    n = max(len(left), len(right), 1)
    h = (n + 1) * pitch
    y0 = h / 2
    w2 = width / 2
    gfx = [Sym('symbol'), name + '_0_1',
           [Sym('rectangle'), [Sym('start'), -w2, y0], [Sym('end'), w2, -y0],
            [Sym('stroke'), [Sym('width'), 0.254], [Sym('type'), Sym('default')]],
            [Sym('fill'), [Sym('type'), Sym('background')]]]]
    pinsym = [Sym('symbol'), name + '_1_1']
    for i, p in enumerate(left):
        if p:
            pinsym.append(_pin(p[2], -w2 - 2.54, y0 - pitch * (i + 1), 0, p[1], p[0]))
    for i, p in enumerate(right):
        if p:
            pinsym.append(_pin(p[2], w2 + 2.54, y0 - pitch * (i + 1), 180, p[1], p[0]))
    for i, p in enumerate(top or []):
        if p:
            pinsym.append(_pin(p[2], -w2 + pitch * (i + 1), y0 + 2.54, 270, p[1], p[0]))
    for i, p in enumerate(bottom or []):
        if p:
            pinsym.append(_pin(p[2], -w2 + pitch * (i + 1), -y0 - 2.54, 90, p[1], p[0]))
    sym = [Sym('symbol'), name,
           [Sym('pin_names'), [Sym('offset'), 1.016]],
           [Sym('exclude_from_sim'), Sym('no')], [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')],
           _prop('Reference', ref, -w2, y0 + 1.5),
           _prop('Value', value or name, w2, y0 + 1.5),
           _prop('Footprint', footprint, 0, -y0 - 2, hide=True),
           _prop('Datasheet', datasheet, 0, -y0 - 4, hide=True),
           _prop('Description', desc, 0, -y0 - 6, hide=True),
           gfx, pinsym]
    return sym


def build_custom_lib():
    syms = []
    # ESP32-U4WDH, QFN48 5x5 (pin numbers per Espressif ESP32 Series Datasheet v5.3, Table 2-1)
    left = [
        ('1', 'VDDA', 'power_in'), ('43', 'VDDA', 'power_in'), ('46', 'VDDA', 'power_in'),
        ('3', 'VDD3P3', 'power_in'), ('4', 'VDD3P3', 'power_in'), ('19', 'VDD3P3_RTC', 'power_in'),
        ('37', 'VDD3P3_CPU', 'power_in'), ('26', 'VDD_SDIO', 'power_out'), None,
        ('2', 'LNA_IN', 'bidirectional'), None,
        ('45', 'XTAL_P', 'input'), ('44', 'XTAL_N', 'output'), None,
        ('48', 'CAP1', 'passive'), ('47', 'CAP2', 'passive'), None,
        ('9', 'CHIP_PU', 'input'), None,
        ('49', 'GND', 'power_in'),
    ]
    right = [
        ('23', 'GPIO0/BOOT', 'bidirectional'), ('41', 'U0TXD/GPIO1', 'bidirectional'),
        ('22', 'GPIO2', 'bidirectional'), ('40', 'U0RXD/GPIO3', 'bidirectional'),
        ('24', 'GPIO4', 'bidirectional'), ('34', 'GPIO5', 'bidirectional'),
        ('18', 'MTDI/GPIO12', 'bidirectional'), ('20', 'MTCK/GPIO13', 'bidirectional'),
        ('17', 'MTMS/GPIO14', 'bidirectional'), ('21', 'MTDO/GPIO15', 'bidirectional'),
        ('35', 'GPIO18', 'bidirectional'), ('38', 'GPIO19', 'bidirectional'),
        ('42', 'GPIO21', 'bidirectional'), ('39', 'GPIO22', 'bidirectional'),
        ('36', 'GPIO23', 'bidirectional'), ('14', 'GPIO25', 'bidirectional'),
        ('15', 'GPIO26', 'bidirectional'), ('16', 'GPIO27', 'bidirectional'),
        ('12', 'GPIO32', 'bidirectional'), ('13', 'GPIO33', 'bidirectional'),
        ('10', 'GPIO34/VDET1', 'input'), ('11', 'GPIO35/VDET2', 'input'),
        ('5', 'GPIO36/SENSOR_VP', 'input'), ('6', 'GPIO37/SENSOR_CAPP', 'input'),
        ('7', 'GPIO38/SENSOR_CAPN', 'input'), ('8', 'GPIO39/SENSOR_VN', 'input'),
        None,
        ('25', 'GPIO16/FLASH_CS', 'no_connect'), ('27', 'GPIO17/FLASH_DO', 'no_connect'),
        ('31', 'SD_CLK/FLASH', 'no_connect'), ('30', 'SD_CMD/FLASH', 'no_connect'),
        ('32', 'SD_DATA0/FLASH', 'no_connect'), ('33', 'SD_DATA1/FLASH', 'no_connect'),
        ('28', 'SD_DATA2/GPIO9', 'no_connect'), ('29', 'SD_DATA3/GPIO10', 'no_connect'),
    ]
    syms.append(box_symbol('ESP32-U4WDH', left, right, ref='U', width=30.48,
                           footprint='Package_DFN_QFN:QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias',
                           datasheet='https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf',
                           desc='ESP32 dual-core 240 MHz, Wi-Fi + Bluetooth Classic/LE, 4 MB in-package flash (QFN48 5x5)'))
    # FS8205A dual N-MOSFET, SOT-23-6L, FUXINSEMI pinout (1 S1, 2 D, 3 S2, 4 G2, 5 D, 6 G1)
    syms.append(box_symbol('FS8205A', [('6', 'G1', 'input'), ('1', 'S1', 'passive'), ('2', 'D12', 'passive')],
                           [('4', 'G2', 'input'), ('3', 'S2', 'passive'), ('5', 'D12', 'passive')],
                           ref='Q', width=10.16, footprint='Package_TO_SOT_SMD:SOT-23-6',
                           desc='Dual N-MOSFET with common drain for Li-ion protection'))
    # TTP223 single-key capacitive touch (TTP223-BA6 / TTP223-BA6-TD pinout)
    syms.append(box_symbol('TTP223', [('3', 'SENSE', 'input'), ('6', 'TOG/STG', 'input'),
                                      ('4', 'AHLB/SLH', 'input')],
                           [('5', 'VDD', 'power_in'), ('1', 'Q/OUT', 'output'), ('2', 'GND', 'power_in')],
                           ref='U', width=12.7, footprint='Package_TO_SOT_SMD:SOT-23-6',
                           desc='1-key capacitive touch controller; TOG=1, AHLB=0 -> toggle mode, active high'))
    # wire pads / electrodes
    for n in (2, 4, 7, 8):
        syms.append(box_symbol('WirePads_%d' % n, [(str(i + 1), 'P%d' % (i + 1), 'passive') for i in range(n)], [],
                               ref='J', width=7.62, desc='Solder pads for hand-soldered wires'))
    syms.append(box_symbol('TouchElectrode', [('1', 'ELECTRODE', 'passive')], [], ref='E', width=10.16,
                           desc='Capacitive touch electrode on the outer (bottom) copper layer'))
    lib = [Sym('kicad_symbol_lib'), [Sym('version'), 20241209], [Sym('generator'), 'ana_gen'],
           [Sym('generator_version'), '1.0']] + syms
    os.makedirs(os.path.dirname(CUSTOM_LIB), exist_ok=True)
    with open(CUSTOM_LIB, 'w', encoding='utf-8') as f:
        f.write(dump(lib) + '\n')
    _lib_cache.pop(CUSTOM_LIB, None)


if __name__ == '__main__':
    build_custom_lib()
    print(sorted(pins('ana:ESP32-U4WDH').keys(), key=int))
