"""Placement for both temple boards (board-local mm: x from hinge end, y down from top edge)."""
import pcbnew
from shapely.geometry import Polygon

import pcbgen


def tip_poly(t, s0, s1, margin=0.0):
    """polygon covering the ear-tip section between s0 and s1 (full height)."""
    pts = [t.tip(s0, -1.0), t.tip(s1 + 2, -1.0), t.tip(s1 + 2, t.h_tip(s1) + 1.0), t.tip(s0, t.h_tip(s0) + 1.0)]
    return Polygon(pts).buffer(margin)


def layout_main(bb):
    t = bb.t
    # ---- fixed anchors ---------------------------------------------------------------------------
    bb.place('D1', 1.0, 4.7, 180)                      # side LED, light exits the hinge end
    bb.place('J4', 7.1, 1.25, 0)                       # OLED wire pads (top row)
    bb.place('J1', 7.85, 7.85, 0)                      # frame-link wire pads (bottom row)
    bb.place('J3', 5.2, 4.7, 90)                       # external LED pads
    bb.place('J2', 16.8, 4.7, 0)                       # bring-up test pads
    bb.place('E1', 31.5, 4.6, 0, back=True)            # touch electrode on the outer face
    bb.place('U7', 28.0, 4.6, 0)                       # TTP223
    bb.place('Q2', 34.5, 4.6, 0)                       # LED switch
    # SHT40 on a slotted island at the top edge (thermal isolation from the board)
    bb.cutout([(39.6, 2.6), (45.4, 2.6), (45.4, 0.7), (46.2, 0.7), (46.2, 3.4), (39.6, 3.4)])
    bb.place('U6', 42.4, 1.3, 0)
    bb.place('U4', 53.0, 4.3, 90)                      # MAX30102, optical window faces the head
    bb.place('U2', 64.0, 4.2, 0)                       # 3.3 V LDO
    bb.place('U3', 73.5, 4.0, 0)                       # CP2102N (0.5 mm pitch -> on grid)
    # ESP32: pads are on odd multiples of 0.175 mm, so the centre sits on an odd multiple of 0.025 mm
    # to put every pad centre exactly on the 0.05 mm routing grid.
    bb.place('U1', 86.825, 3.875, 270)
    bb.place('Y1', 97.6, 3.875, 180)                   # crystal on the ear-tip side (pins 44/45 face it)
    # antenna at the ear-tip end, along the tip axis
    s_ant = t.Lt - 1.0 - 4.6
    ax, ay = t.tip(s_ant, t.h_tip(s_ant) / 2)
    bb.place('AE1', ax, ay, t.tip_rot)
    for ref, s in (('C17', s_ant - 8.0), ('R8', s_ant - 6.3), ('C18', s_ant - 4.6)):
        x, y = t.tip(s, t.h_tip(s) / 2)
        bb.place(ref, x, y, t.tip_rot + (90 if ref.startswith('C') else 0))
    s_k = s_ant - 1.45 - 0.35 - 2.0
    bb.ant_zone = tip_poly(t, s_k, t.Lt, 0.0)
    bb.keepouts.append(bb.ant_zone)
    bb.s_ant = s_ant
    bb.s_keep = s_k

    # ---- keep-clear rings: leave escape space around fine-pitch chips while placing passives ---------
    rings = [Polygon([(86.825 - 2.85 - 2.2, -1), (86.825 + 2.85 + 2.15, -1), (86.825 + 2.85 + 2.15, 10),
                      (86.825 - 2.85 - 2.2, 10)]),
             Polygon([(73.5 - 2.35 - 0.6, -1), (73.5 + 2.35 + 0.6, -1), (73.5 + 2.35 + 0.6, 10),
                      (73.5 - 2.35 - 0.6, 10)])]
    bb.keepouts += rings

    def near(ref, a, rots=(0, 90), **kw):
        x, y = bb.pad_pos(*a)
        bb.autoplace(ref, (x, y), rots=rots, **kw)

    island = Polygon([(39.3, -0.5), (45.3, -0.5), (45.3, 2.55), (39.3, 2.55)])
    for ref, a in [('C15', ('U1', '2')), ('L2', ('U1', '2')), ('C16', ('U1', '2')),
                   ('C11', ('U1', '47')), ('R1', ('U1', '47')), ('C10', ('U1', '48')),
                   ('C6', ('U1', '46')), ('C5', ('U1', '43')), ('R4', ('U1', '45')),
                   ('C13', ('Y1', '1')), ('C14', ('Y1', '3')),
                   ('L1', ('U1', '3')), ('C4', ('U1', '1')), ('C3', ('U1', '4')), ('C2', ('U1', '3')),
                   ('C1', ('U1', '3')),
                   ('C8', ('U1', '37')), ('C9', ('U1', '26')), ('C7', ('U1', '19')),
                   ('R2', ('U1', '9')), ('C12', ('U1', '9')), ('R3', ('U1', '23')),
                   ('R5', ('U1', '41')), ('R6', ('U1', '24')), ('R7', ('U1', '36')),
                   ('R9', ('U1', '13')), ('R10', ('U1', '13')), ('C23', ('U1', '13')), ('R11', ('U1', '20')),
                   ('R12', ('U1', '11')), ('R13', ('U1', '11')),
                   ('C24', ('U3', '6')), ('C25', ('U3', '7')), ('C26', ('U3', '5')), ('R14', ('U3', '9')),
                   ('Q1', ('U3', '23')),
                   ('C20', ('U2', '1')), ('C21', ('U2', '5')), ('C22', ('U2', '5')), ('C19', ('U2', '1')),
                   ('C28', ('U4', '9')), ('C27', ('U4', '10')), ('C30', ('U4', '11')), ('C29', ('U4', '11')),
                   ('U5', ('U4', '11')), ('R19', ('U4', '13')), ('R17', ('U4', '3')), ('R18', ('U4', '2')),
                   ('C33', ('U7', '5')), ('C34', ('U7', '3')), ('R20', ('Q2', '1')),
                   ('R21', ('D1', '2')), ('R22', ('J3', '1'))]:
        near(ref, a, spacing=0.12, max_r=16.0)
    near('C31', ('U5', '3'), spacing=0.12)
    near('R15', ('Q1', '2'), spacing=0.12)
    near('R16', ('Q1', '5'), spacing=0.12)
    near('C32', ('U6', '3'), region=island)
    for r in rings:
        bb.keepouts.remove(r)


def layout_power(bb):
    t = bb.t
    bb.place('D5', 1.0, 4.7, 180)                      # side LED at hinge end
    bb.place('J3', 7.85, 1.25, 0)                      # frame-link pads, top row
    bb.place('J4', 4.9, 5.4, 90)                       # external LED pads
    # USB-C: opening faces down out of the bottom edge
    xu = 20.5
    bb.place('J1', xu, t.h_straight(xu) - 3.65 + 0.05, 0)
    bb.place('U1', 28.6, 5.4, 90)                      # USBLC6 next to the USB-C
    bb.place('U2', 35.5, 4.4, 90)                      # TP4056 (HSOP-8)
    bb.place('D1', 40.6, 7.2, 0)                       # red: charging (visible window)
    bb.place('D2', 42.6, 7.2, 0)                       # green: full
    # power-path + main switch
    bb.place('Q2', 46.8, 5.0, 0)
    bb.place('D4', 50.2, 7.1, 0)
    bb.place('SW1', 54.0, 1.75, 180)                   # lever sticks out of the top edge
    bb.place('Q3', 61.0, 5.3, 0)
    # protection
    bb.place('U3', 66.2, 4.4, 0)
    bb.place('Q1', 70.8, 4.4, 0)
    # BT headset switch + pads
    bb.place('SW2', 79.5, 1.75, 180)
    bb.place('Q4', 86.2, 5.0, 0)
    bb.place('J2', 91.2, 5.2, 0)
    # battery tab pads just before the bend; the cell lies in the ear tip (no parts there)
    bb.place('BT1', 99.3, 3.7, 0)
    bb.keepouts.append(tip_poly(t, 1.5, t.Lt, 0.0))

    def near(ref, a, rots=(0, 90), **kw):
        x, y = bb.pad_pos(*a)
        bb.autoplace(ref, (x, y), rots=rots, **kw)

    for ref, a in [('R1', ('J1', 'A5')), ('R2', ('J1', 'B5')), ('C1', ('U1', '5')),
                   ('R3', ('U2', '2')), ('C2', ('U2', '4')), ('C3', ('U2', '5')),
                   ('R4', ('D1', '2')), ('R5', ('D2', '2')), ('D3', ('U2', '7')),
                   ('R8', ('Q2', '1')), ('C5', ('Q2', '2')), ('R9', ('Q3', '1')), ('C6', ('Q3', '3')),
                   ('R6', ('U3', '5')), ('C4', ('U3', '5')), ('R7', ('U3', '2')),
                   ('R10', ('Q4', '1')), ('C7', ('Q4', '3')),
                   ('R11', ('D5', '2')), ('R12', ('J4', '1'))]:
        near(ref, a)
