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
    # USB-serial bridge next to the frame-link pads: USB pins (left side) face the hinge pads, UART/DTR/RTS
    # (top side) face the top edge, where they turn towards the ESP32 together with the bring-up pads.
    bb.place('U3', 19.5, 5.0, 0)
    bb.place('J2', 27.9, 0.85, 0)                      # bring-up pads (top edge, next to the CP2102N)
    # CP2102N support parts kept off its hinge side, so the OLED and frame-link lines can fan in to J4/J1:
    # 3V3 decoupling at its lower-left corner, VBUS divider and reset pull-up underneath, auto-reset pair right
    bb.place('C26', 15.9, 5.75, 180)                   # VIO (pin 5)
    bb.place('C24', 15.9, 6.75, 180)                   # VDD (pin 6)
    bb.place('C25', 17.85, 8.35, 0)                    # VREGIN (pin 7)
    bb.place('R12', 19.85, 8.35, 0)                    # VBUS -> VBUS_SENSE (pin 8)
    bb.place('R13', 21.85, 8.35, 0)                    # VBUS_SENSE -> GND
    bb.place('R14', 24.0, 8.3, 0)                      # RSTb pull-up (pin 9)
    # DTR/RTS auto-reset pair, turned so both base pins (2 and 5) face their resistors directly
    bb.place('Q1', 23.9, 4.2, 90)
    bb.place('R16', 23.9, 1.93, 0)                     # RTS -> base 2 (pin 5, top middle)
    bb.place('R15', 23.9, 6.47, 0)                     # DTR -> base 1 (pin 2, bottom middle)
    bb.place('E1', 31.5, 4.6, 0, back=True)            # touch electrode on the outer face
    bb.place('U7', 28.0, 4.6, 0)                       # TTP223
    bb.place('Q2', 34.5, 4.6, 0)                       # LED switch
    # SHT40 at the top edge, far from the regulator and the ESP32; no copper pour around it (see build.py)
    bb.place('U6', 42.4, 1.3, 0)
    bb.place('U4', 53.0, 4.3, 90)                      # MAX30102, optical window faces the head
    bb.place('U2', 63.0, 6.35, 0)                      # 3.3 V LDO on the bottom edge (centre lane stays free)
    # ESP32: pads are on odd multiples of 0.175 mm, so the centre sits on an odd multiple of 0.025 mm
    # to put every pad centre exactly on the 0.05 mm routing grid.
    bb.place('U1', 86.825, 3.875, 270)
    bb.place('Y1', 97.6, 3.875, 180)                   # crystal on the ear-tip side (pins 44/45 face it)
    # 0R in XTAL_P, in line with pin 45; XTAL_N (pin 44) dips under it to the crystal's lower-left pad and
    # XTAL_IN passes between the crystal's pads to its upper-right pad
    bb.place('R4', 94.0, 3.35, 0)
    bb.place('C13', 100.6, 3.0, 90)                    # XTAL_IN load cap, right of the crystal (pad 1 below)
    bb.place('C14', 96.5, 6.35, 0)                     # XTAL_N load cap, under the crystal's lower-left pad
    bb.place('C10', 91.6, 2.7, 0)                      # CAP1, right of pin 48 (above the XTAL lines)
    bb.place('R5', 93.0, 6.75, 0)                      # U0TXD series R, next to pin 41's escape via
    bb.place('C9', 82.9, 6.95, 180)                    # VDD_SDIO, below the ESP32's bottom-left corner
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
             Polygon([(89.3, 3.85), (93.1, 3.85), (93.1, 6.7), (89.3, 6.7)])]   # ESP32 tip-side escape stubs
    bb.keepouts += rings

    def near(ref, a, rots=(0, 90), **kw):
        x, y = bb.pad_pos(*a)
        bb.autoplace(ref, (x, y), rots=rots, **kw)

    # RF chain in a straight line along the top edge, right of the ESP32 (LNA_IN = pin 2 at the top-right)
    bb.place('C15', 91.95, 1.3, 90)      # shunt at LNA_IN (pad 1 on the RF line)
    bb.place('L2', 93.42, 0.8, 0)        # series
    bb.place('C16', 94.9, 1.3, 90)       # shunt at RF_MID
    rf_band = Polygon([(91.5, -1), (t.Lp + 2, -1), (t.Lp + 2, 1.9), (91.5, 1.9)])
    bb.keepouts.append(rf_band)

    # placement-only keepouts: crystal corridor and the centre lane between the LDO and the ESP32, so the
    # ESP32's support parts form two rows (top edge / bottom edge) with gaps for vias and tracks
    xtal = Polygon([(89.3, 2.95), (99.6, 2.95), (99.6, 4.55), (89.3, 4.55)])
    lane = Polygon([(56.5, 2.3), (81.7, 2.3), (81.7, 5.5), (56.5, 5.5)])
    bb.keepouts += [xtal, lane]
    bb.lane = lane
    tip_area = (95.0, 5.6)                             # free area right of the tip-side escape stubs
    for ref in ('C6', 'C5', 'C4'):
        bb.autoplace(ref, tip_area, rots=(0, 90), spacing=0.45, max_r=20.0)
    # two rows left of the ESP32 (1.4 mm pitch, so a via fits between neighbours): parts of the ESP32's top-side
    # pins along the top edge, the rest along the bottom edge; signal pads face the centre lane
    for i, (ref, rot) in enumerate((('L1', 90), ('C2', 90), ('C3', 90), ('C12', 90), ('R2', 270), ('C23', 90),
                                    ('R10', 90), ('R9', 90))):
        bb.place(ref, 81.3 - 1.4 * i, 1.05, rot)
    for i, (ref, rot) in enumerate((('C8', 90), ('R7', 90), ('R6', 90), ('C7', 90), ('R11', 90), ('R3', 90),
                                    ('C1', 90))):
        bb.place(ref, 81.1 - 1.4 * i, 6.75, rot)
    # LDO input caps on its VIN side (left), output caps on its VOUT side (right)
    bb.place('C19', 59.9, 6.4, 90)
    bb.place('C20', 58.4, 6.4, 90)
    bb.place('C21', 66.1, 6.4, 90)
    bb.place('C22', 67.5, 6.4, 90)
    for ref, a in [('C28', ('U4', '9')), ('C27', ('U4', '10')), ('C30', ('U4', '11')), ('C29', ('U4', '11')),
                   ('U5', ('U4', '11')), ('R19', ('U4', '13')), ('R17', ('U4', '3')), ('R18', ('U4', '2')),
                   ('C33', ('U7', '5')), ('C34', ('U7', '3')), ('R20', ('Q2', '1')),
                   ('R21', ('D1', '2')), ('R22', ('J3', '1'))]:
        near(ref, a, spacing=0.3, max_r=16.0)
    near('C31', ('U5', '3'), spacing=0.3)
    near('C32', ('U6', '3'), spacing=0.3)
    for r in rings + [xtal, lane, rf_band]:
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
