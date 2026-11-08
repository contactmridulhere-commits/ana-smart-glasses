"""Build the data for the 3D assembly viewer (hardware/3d/index.html).

Run with KiCad's Python:   /usr/bin/python3 hardware/3d/make_assembly.py

1. Exports both temple boards from KiCad as GLB (board, copper, mask, silkscreen and the KiCad 3D models).
2. KiCad's GLB keeps every face of every part as its own mesh (13 000+ draw calls per board) and drops the
   part colours, so the faces are sorted into materials (tin terminals, black IC bodies, ceramic capacitors,
   crystal lid, ...), merged into one primitive per material and stored quantised (KHR_mesh_quantization):
   about 4 MB per board instead of 12 MB, and fast to draw.
3. Writes assembly-data.json: the temple outline, every part with its position, value and job (for the
   viewer's hover labels) and the parts that have no KiCad 3D model (drawn by the viewer).
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HW = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HW, 'scripts'))
import circuits  # noqa: E402
import outline  # noqa: E402

OX, OY = 30.0, 60.0          # board-local origin in KiCad coordinates (see pcbgen.local)
MODEL_DIR = '/usr/share/kicad/3dmodels'

# ---------------------------------------------------------------------------------------------- materials
MATS = {
    'copper':  dict(c=(0.83, 0.68, 0.24, 1), m=1.0, r=0.30),     # ENIG pads, tracks (seen through the mask)
    'via':     dict(c=(0.78, 0.78, 0.80, 1), m=1.0, r=0.35),
    'silk':    dict(c=(0.96, 0.96, 0.94, 1), m=0.0, r=0.80),
    'mask':    dict(c=(0.05, 0.30, 0.16, 0.86), m=0.0, r=0.35, blend=True),
    'fr4':     dict(c=(0.78, 0.71, 0.48, 1), m=0.0, r=0.85),
    'tin':     dict(c=(0.86, 0.87, 0.89, 1), m=1.0, r=0.32),
    'ic':      dict(c=(0.07, 0.07, 0.08, 1), m=0.0, r=0.55),
    'res':     dict(c=(0.09, 0.09, 0.09, 1), m=0.0, r=0.50),
    'cap':     dict(c=(0.66, 0.53, 0.35, 1), m=0.0, r=0.60),
    'ind':     dict(c=(0.86, 0.85, 0.80, 1), m=0.0, r=0.60),
    'lid':     dict(c=(0.82, 0.82, 0.84, 1), m=1.0, r=0.22),
    'ceramic': dict(c=(0.93, 0.91, 0.86, 1), m=0.0, r=0.70),
    'led_red': dict(c=(0.95, 0.20, 0.12, 1), m=0.0, r=0.20),
    'led_grn': dict(c=(0.25, 0.90, 0.35, 1), m=0.0, r=0.20),
}
BOARD_MAT = {0: 'copper', 1: 'via', 2: 'silk', 3: 'silk', 4: 'mask', 5: 'mask', 6: 'fr4'}


def read_glb(path):
    d = open(path, 'rb').read()
    n0 = struct.unpack('<I', d[12:16])[0]
    j = json.loads(d[20:20 + n0])
    o = 20 + n0
    n1 = struct.unpack('<I', d[o:o + 4])[0]
    return j, d[o + 8:o + 8 + n1]


def accessor(j, binary, k):
    a = j['accessors'][k]
    bv = j['bufferViews'][a['bufferView']]
    dt = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[a['componentType']]
    nc = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[a['type']]
    off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    stride = bv.get('byteStride', 0)
    isz = np.dtype(dt).itemsize
    if stride and stride != nc * isz:
        raw = np.frombuffer(binary, np.uint8, a['count'] * stride, off).reshape(a['count'], stride)
        arr = raw[:, :nc * isz].copy().view(dt)
    else:
        arr = np.frombuffer(binary, dt, a['count'] * nc, off).copy()
    return arr.reshape(a['count'], nc) if nc > 1 else arr


def node_matrix(n):
    if 'matrix' in n:
        return np.array(n['matrix'], dtype=float).reshape(4, 4).T
    m = np.eye(4)
    if 'scale' in n:
        m = np.diag(list(n['scale']) + [1.0]) @ m
    if 'rotation' in n:
        x, y, z, w = n['rotation']
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        rm = np.eye(4)
        rm[:3, :3] = r
        m = rm @ m
    if 'translation' in n:
        t = np.eye(4)
        t[:3, 3] = n['translation']
        m = t @ m
    return m


def classify(ref, value, prims):
    """prims: list of (bbox_min, bbox_max) in the part's own frame (x, y=height, z).  Returns a material per
    primitive: terminals/leads in tin, bodies in the colour of the part type."""
    lo = np.min([p[0] for p in prims], axis=0)
    hi = np.max([p[1] for p in prims], axis=0)
    H = max(hi[1] - lo[1], 1e-6)
    out = []
    pre = ''.join(ch for ch in ref if ch.isalpha())
    two_term = pre in ('R', 'C', 'L') or (pre == 'D' and ('RED' in value.upper() or 'GREEN' in value.upper()))
    if two_term:
        ax = 0 if (hi[0] - lo[0]) >= (hi[2] - lo[2]) else 2
        cen = (hi[ax] + lo[ax]) / 2
        half = (hi[ax] - lo[ax]) / 2
        body = {'R': 'res', 'C': 'cap', 'L': 'ind'}.get(pre, 'led_red' if 'RED' in value.upper() else 'led_grn')
        for a, b in prims:
            near = min(abs(a[ax] - cen), abs(b[ax] - cen))
            straddles = (a[ax] - cen) * (b[ax] - cen) < 0
            out.append('tin' if (not straddles and near >= 0.55 * half) else body)
        return out
    if pre == 'Y':
        for a, b in prims:
            ch = (a[1] + b[1]) / 2 - lo[1]
            out.append('lid' if ch >= 0.75 * H else ('tin' if b[1] - lo[1] <= 0.12 * H else 'ceramic'))
        return out
    # packages with leads: the body is the box of the tall faces; faces centred outside it are leads
    tall = [p for p in prims if p[1][1] - lo[1] > 0.5 * H]
    blo = np.min([p[0] for p in tall], axis=0) if tall else lo
    bhi = np.max([p[1] for p in tall], axis=0) if tall else hi
    for a, b in prims:
        c = (a + b) / 2
        outside = (c[0] < blo[0] - 0.01 or c[0] > bhi[0] + 0.01 or c[2] < blo[2] - 0.01 or c[2] > bhi[2] + 0.01)
        out.append('tin' if outside else 'ic')
    return out


def process(src, dst, values):
    j, binary = read_glb(src)
    groups = {}                                     # material -> [positions, normals, indices]

    def add(mat, pos, nrm, idx):
        g = groups.setdefault(mat, [[], [], [], 0])
        g[0].append(pos)
        g[1].append(nrm)
        g[2].append(idx.astype(np.uint32) + g[3])
        g[3] += len(pos)

    def walk(ni, parent):
        n = j['nodes'][ni]
        m = parent @ node_matrix(n)
        if 'mesh' in n:
            name = n.get('name', '')
            prims = j['meshes'][n['mesh']]['primitives']
            if name.startswith('=>'):
                mats = [BOARD_MAT.get(p.get('material'), 'fr4') for p in prims]
            else:
                bbs = []
                for p in prims:
                    a = j['accessors'][p['attributes']['POSITION']]
                    bbs.append((np.array(a['min']), np.array(a['max'])))
                mats = classify(name, values.get(name, ''), bbs)
            r = m[:3, :3]
            for p, mat in zip(prims, mats):
                pos = accessor(j, binary, p['attributes']['POSITION']).astype(np.float64)
                nrm = accessor(j, binary, p['attributes']['NORMAL']).astype(np.float64) \
                    if 'NORMAL' in p['attributes'] else np.zeros_like(pos)
                idx = accessor(j, binary, p['indices']) if 'indices' in p else np.arange(len(pos))
                pos = pos @ r.T + m[:3, 3]
                nrm = nrm @ r.T
                ln = np.linalg.norm(nrm, axis=1, keepdims=True)
                nrm = np.where(ln > 0, nrm / np.maximum(ln, 1e-12), 0)
                add(mat, pos, nrm, idx)
        for c in n.get('children', []):
            walk(c, m)

    for ni in j['scenes'][j.get('scene', 0)]['nodes']:
        walk(ni, np.eye(4))

    allpos = np.concatenate([np.concatenate(g[0]) for g in groups.values()])
    lo, hi = allpos.min(axis=0), allpos.max(axis=0)
    scale = float((hi - lo).max()) / 65534.0
    origin = lo + 32767 * scale
    blob = bytearray()
    views, accs, prims, mats = [], [], [], []

    def view(data, stride=None, target=None):
        while len(blob) % 4:
            blob.append(0)
        v = {'buffer': 0, 'byteOffset': len(blob), 'byteLength': len(data)}
        if stride:
            v['byteStride'] = stride
        if target:
            v['target'] = target
        blob.extend(data)
        views.append(v)
        return len(views) - 1

    for mat, (pl, nl, il, _) in groups.items():
        pos = np.concatenate(pl)
        nrm = np.concatenate(nl)
        idx = np.concatenate(il)
        q = np.clip(np.round((pos - origin) / scale), -32767, 32767).astype(np.int16)
        qp = np.zeros((len(q), 4), np.int16)
        qp[:, :3] = q
        qn = np.zeros((len(nrm), 4), np.int8)
        qn[:, :3] = np.clip(np.round(nrm * 127), -127, 127).astype(np.int8)
        vp = view(qp.tobytes(), 8, 34962)
        accs.append({'bufferView': vp, 'componentType': 5122, 'count': len(q), 'type': 'VEC3',
                     'min': q.min(axis=0).tolist(), 'max': q.max(axis=0).tolist()})
        ap = len(accs) - 1
        vn = view(qn.tobytes(), 4, 34962)
        accs.append({'bufferView': vn, 'componentType': 5120, 'normalized': True, 'count': len(qn), 'type': 'VEC3'})
        an = len(accs) - 1
        vi = view(idx.astype(np.uint32).tobytes(), None, 34963)
        accs.append({'bufferView': vi, 'componentType': 5125, 'count': len(idx), 'type': 'SCALAR'})
        ai = len(accs) - 1
        spec = MATS[mat]
        md = {'name': mat, 'pbrMetallicRoughness': {'baseColorFactor': list(spec['c']), 'metallicFactor': spec['m'],
                                                    'roughnessFactor': spec['r']}, 'doubleSided': True}
        if spec.get('blend'):
            md['alphaMode'] = 'BLEND'
        mats.append(md)
        prims.append({'attributes': {'POSITION': ap, 'NORMAL': an}, 'indices': ai, 'material': len(mats) - 1})
    while len(blob) % 4:
        blob.append(0)
    g = {'asset': {'version': '2.0', 'generator': 'ANA make_assembly.py from KiCad 9 GLB'},
         'extensionsUsed': ['KHR_mesh_quantization'], 'extensionsRequired': ['KHR_mesh_quantization'],
         'scene': 0, 'scenes': [{'nodes': [0]}],
         'nodes': [{'name': os.path.basename(dst).split('.')[0], 'mesh': 0,
                    'translation': origin.tolist(), 'scale': [scale] * 3}],
         'meshes': [{'primitives': prims}], 'materials': mats, 'accessors': accs, 'bufferViews': views,
         'buffers': [{'byteLength': len(blob)}]}
    js = json.dumps(g, separators=(',', ':')).encode()
    js += b' ' * ((4 - len(js) % 4) % 4)
    with open(dst, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(struct.pack('<II', len(js), 0x4E4F534A) + js)
        f.write(struct.pack('<II', len(blob), 0x004E4942) + bytes(blob))
    tris = sum(a['count'] for a in accs if a['type'] == 'SCALAR') // 3
    print(dst, '%.1f MB' % (os.path.getsize(dst) / 1e6), 'materials', sorted(groups), 'triangles', tris)


def model_exists(fp):
    for m in fp.Models():
        f = m.m_Filename
        for v in ('${KICAD9_3DMODEL_DIR}', '${KICAD8_3DMODEL_DIR}', '${KICAD7_3DMODEL_DIR}', '${KICAD6_3DMODEL_DIR}'):
            f = f.replace(v, MODEL_DIR)
        base = os.path.splitext(f)[0]
        if any(os.path.exists(base + e) for e in ('.step', '.stp', '.wrl')):
            return True
    return False


def parts(name):
    import pcbnew
    b = pcbnew.LoadBoard(os.path.join(HW, name, name + '.kicad_pcb'))
    comps = {c.ref: c for c in circuits.BOARDS[name]['build']()}
    out = []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        c = comps.get(ref)
        pos = fp.GetPosition()
        cy = fp.GetCourtyard(pcbnew.F_CrtYd if not fp.IsFlipped() else pcbnew.B_CrtYd)
        bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
        pads = []
        for p in fp.Pads():
            q = p.GetPosition()
            pads.append([p.GetNumber(), round(pcbnew.ToMM(q.x) - OX, 3), round(pcbnew.ToMM(q.y) - OY, 3),
                         (p.GetNetname() or '').lstrip('/')])
        out.append({
            'ref': ref, 'value': c.value if c else fp.GetValue(), 'desc': c.desc if c else '',
            'block': c.block if c else '', 'mpn': c.mpn if c else '', 'lcsc': c.lcsc if c else '',
            'dnp': bool(c.dnp) if c else False,
            'fp': str(fp.GetFPID().GetLibItemName()),
            'x': round(pcbnew.ToMM(pos.x) - OX, 3), 'y': round(pcbnew.ToMM(pos.y) - OY, 3),
            'rot': round(fp.GetOrientationDegrees(), 1), 'back': fp.IsFlipped(),
            'bbox': [round(pcbnew.ToMM(bb.GetX()) - OX, 3), round(pcbnew.ToMM(bb.GetY()) - OY, 3),
                     round(pcbnew.ToMM(bb.GetRight()) - OX, 3), round(pcbnew.ToMM(bb.GetBottom()) - OY, 3)],
            'model': model_exists(fp), 'pads': pads,
        })
    return out


def main():
    t = outline.Temple()
    # temple cover: 0.3 mm clearance round the board plus a 1.0 mm wall; the viewer adds a 0.6 mm bevel to this
    # profile, so it is exported 0.6 mm smaller
    cover = t.poly.buffer(1.3 - 0.6, join_style=1).simplify(0.02)
    data = {'outline': [list(map(lambda v: round(v, 3), p)) for p in t.poly.exterior.coords],
            'cover': [list(map(lambda v: round(v, 3), p)) for p in cover.exterior.coords],
            'temple': {'Lp': t.Lp, 'Lt': t.Lt, 'h1': t.h1, 'h2': t.h2, 'h3': t.h3, 'tip_rot': t.tip_rot,
                       'cfg': json.load(open(outline.CFG))},
            'boards': {}}
    # LiPo cell (3.5 x 7 x 30 mm) on the component side of the left board's ear tip
    s0, s1 = 3.0, 33.0
    cx, cy = t.tip((s0 + s1) / 2, t.h_tip((s0 + s1) / 2) / 2)
    data['battery'] = {'x': round(cx, 3), 'y': round(cy, 3), 'rot': t.tip_rot, 'len': 30.0, 'h': 6.4, 't': 3.5}
    data['tip_points'] = {str(s): [round(v, 3) for v in t.tip(s, t.h_tip(s) / 2)] for s in range(0, 41, 5)}
    with tempfile.TemporaryDirectory() as tmp:
        for name in ('ana-main', 'ana-power'):
            raw = os.path.join(tmp, name + '.glb')
            subprocess.run(['kicad-cli', 'pcb', 'export', 'glb', '--force', '--subst-models', '--no-dnp',
                            '--include-tracks', '--include-pads', '--include-zones', '--include-silkscreen',
                            '--include-soldermask', '--user-origin', '%gx%gmm' % (OX, OY), '-o', raw,
                            os.path.join(HW, name, name + '.kicad_pcb')], check=True, capture_output=True)
            pl = parts(name)
            process(raw, os.path.join(HERE, name + '.glb'), {p['ref']: p['value'] for p in pl})
            data['boards'][name] = pl
    with open(os.path.join(HERE, 'assembly-data.json'), 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    print('wrote', os.path.join(HERE, 'assembly-data.json'))


if __name__ == '__main__':
    main()
