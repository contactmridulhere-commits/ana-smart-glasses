"""Single source of truth for both ANA V1 temple boards: parts, values, footprints, LCSC numbers and nets.

Board R  = "ana-main"  : right (display-side) temple: ESP32-U4WDH, USB-UART, MAX30102, SHT40, TTP223, LED.
Board L  = "ana-power" : left temple: USB-C, TP4056 charger, DW01A/FS8205A protection, power path, switches,
                         BT-headset power pads, LED, battery pads.
The two boards are joined by an 8-wire frame link (same pin order on both boards):
    1 VSYS  2 GND  3 USB_DP  4 USB_DN  5 GND  6 VBUS  7 CHRG_N  8 LED_K
"""
from dataclasses import dataclass, field

LINK_ORDER = ['VSYS', 'GND', 'USB_DP', 'USB_DN', 'GND', 'VBUS', 'CHRG_N', 'LED_K']
OLED_ORDER = ['GND', '3V3', 'SPI_CLK', 'SPI_MOSI', 'OLED_RST', 'OLED_DC', 'OLED_CS']  # 7-pin SPI OLED module order


@dataclass
class Comp:
    ref: str
    sym: str
    value: str
    fp: str
    pins: dict
    lcsc: str = ''
    mpn: str = ''
    dnp: bool = False
    block: str = ''
    desc: str = ''
    unit_pins: dict = field(default_factory=dict)   # for multi-unit symbols: unit -> list of pins


FP_R = 'Resistor_SMD:R_0402_1005Metric'
FP_C = 'Capacitor_SMD:C_0402_1005Metric'
FP_C0603 = 'Capacitor_SMD:C_0603_1608Metric'
FP_L = 'Inductor_SMD:L_0402_1005Metric'
FP_LED0402 = 'LED_SMD:LED_0402_1005Metric'

# verified LCSC numbers (JLCPCB parts search / LCSC product API, 2026-11)
R_LCSC = {'0': 'C17168', '33': 'C25105', '68': 'C25131', '100': 'C25076', '470': 'C25117', '1k': 'C11702',
          '4.7k': 'C25900', '5.1k': 'C25905', '10k': 'C25744', '20k': 'C25765', '22k': 'C25768',
          '27k': 'C25771', '47k': 'C25792', '100k': 'C25741', '1M': 'C26083'}
C_LCSC = {'1.5pF': 'C1552', '22pF': 'C1555', '100pF': 'C1546', '3.3nF': 'C107028', '10nF': 'C15195',
          '100nF': 'C1525', '1uF': 'C52923', '4.7uF': 'C469468', '10uF': 'C15525', '10uF/16V': 'C380317'}


def R(ref, val, a, b, block, desc='', dnp=False):
    return Comp(ref, 'Device:R', val, FP_R, {'1': a, '2': b}, R_LCSC.get(val, ''), '', dnp, block, desc)


def C(ref, val, a, b, block, desc='', dnp=False):
    fp = FP_C0603 if val == '10uF/16V' else FP_C
    shown = '10uF' if val == '10uF/16V' else val
    return Comp(ref, 'Device:C', shown, fp, {'1': a, '2': b}, C_LCSC.get(val, ''), '', dnp, block, desc)


def L(ref, val, a, b, block, lcsc, mpn, desc=''):
    return Comp(ref, 'Device:L', val, FP_L, {'1': a, '2': b}, lcsc, mpn, False, block, desc)


def LED(ref, val, k, a, block, fp, lcsc, mpn, desc=''):
    return Comp(ref, 'Device:LED', val, fp, {'1': k, '2': a}, lcsc, mpn, False, block, desc)


FP_SIDE_LED = 'ana:LED_SideView_1.6x0.75mm_YLED0602'
FP_LINK = 'ana:WirePads_1x08_P1.50mm'
FP_PADS2 = 'ana:WirePads_1x02_P1.50mm'


# ==============================================================================================
# RIGHT temple: main board
# ==============================================================================================
def main_board():
    c = []
    B = 'ESP32-U4WDH core'
    # ESP32 pin -> net map (U4WDH: GPIO16/17 and SD_* are wired to the in-package flash -> left unconnected)
    # ESP32 pin -> net map.  QFN48 5x5 sides (chip rotated 270 deg on the board):
    #   pins 15-24 face the hinge end, 1-14 the top edge, 25-38 the bottom edge, 39-48 the ear tip.
    # Signals that travel to the hinge end use hinge-side / top-front pins (GPIO matrix routes SPI/I2C anywhere).
    # U4WDH: GPIO16/17 and SD_* are wired to the in-package flash -> left unconnected.
    # Straps: GPIO12 (MTDI) and GPIO2 left free; GPIO15 (MTDO) drives OLED CS (idle high, fine at boot).
    esp = {
        '1': '3V3', '43': '3V3', '46': '3V3', '19': '3V3', '37': '3V3',
        '3': 'VDD3P3', '4': 'VDD3P3', '26': 'VDD_SDIO',
        '2': 'LNA_IN', '45': 'XTAL_P', '44': 'XTAL_N', '48': 'CAP1', '47': 'CAP2', '9': 'EN', '49': 'GND',
        '23': 'IO0', '41': 'U0TXD', '40': 'U0RXD',
        '15': 'OLED_DC',        # GPIO26  (hinge-facing side)
        '16': 'OLED_RST',       # GPIO27
        '17': 'TOUCH',          # GPIO14 (RTC GPIO, can wake from deep sleep)
        '20': 'CHRG_N',         # GPIO13
        '21': 'OLED_CS',        # GPIO15 (MTDO strap; CS idles high)
        '24': 'SPI_CLK_MCU',    # GPIO4  -> OLED D0
        '13': 'BAT_ADC',        # GPIO33 ADC1_CH5 (top edge, next to the hinge-side corner)
        '34': 'MAX_INT',        # GPIO5  (open-drain INT, 10k pull-up keeps the strap high at boot)
        '11': 'VBUS_SENSE',     # GPIO35 (input only; top edge, every other pin -> room for vias)
        '36': 'SPI_MOSI_MCU',   # GPIO23 (VSPI MOSI) -> OLED D1
        '39': 'I2C_SCL',        # GPIO22 (same I2C pins as V1)
        '42': 'I2C_SDA',        # GPIO21
        '5': None, '6': None, '7': None, '8': None, '10': None, '12': None, '14': None, '18': None, '35': None,
        '22': None, '38': None, '25': None, '27': None, '28': None, '29': None, '30': None, '31': None,
        '32': None, '33': None,
    }
    c.append(Comp('U1', 'ana:ESP32-U4WDH', 'ESP32-U4WDH', 'Package_DFN_QFN:QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias',
                  esp, 'C20622217', 'ESP32-U4WDH', block=B, desc='ESP32, 4 MB flash in package, BT Classic'))
    c += [
        C('C1', '10uF', '3V3', 'GND', B, 'bulk at ESP32 supply entry'),
        C('C2', '1uF', '3V3', 'GND', B), C('C3', '100nF', '3V3', 'GND', B),
        L('L1', '2.0nH', '3V3', 'VDD3P3', B, 'C76769', 'MLG1005S2N0BT000', 'VDD3P3 RF supply filter (Espressif ref.)'),
        C('C4', '100nF', '3V3', 'GND', B, 'VDDA pin 1'),
        C('C5', '100pF', '3V3', 'GND', B, 'VDDA pins 43/46'), C('C6', '1uF', '3V3', 'GND', B, 'VDDA pins 43/46'),
        C('C7', '100nF', '3V3', 'GND', B, 'VDD3P3_RTC pin 19'), C('C8', '100nF', '3V3', 'GND', B, 'VDD3P3_CPU pin 37'),
        C('C9', '1uF', 'VDD_SDIO', 'GND', B, 'VDD_SDIO (in-package flash supply)'),
        C('C10', '10nF', 'CAP1', 'GND', B, 'CAP1, 10%'), C('C11', '3.3nF', 'CAP2', 'CAP1', B, 'CAP2-CAP1, 10%'),
        R('R1', '20k', 'CAP2', 'CAP1', B),
        R('R2', '10k', '3V3', 'EN', B, 'CHIP_PU pull-up'), C('C12', '1uF', 'EN', 'GND', B, 'CHIP_PU RC delay'),
        R('R3', '10k', '3V3', 'IO0', B, 'GPIO0 boot strap pull-up'),
        Comp('Y1', 'Device:Crystal_GND24', '40MHz 10ppm', 'Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm',
             {'1': 'XTAL_IN', '2': 'GND', '3': 'XTAL_N', '4': 'GND'}, 'C9010', 'X322540MPB4SI', block=B,
             desc='40 MHz +-10 ppm, CL 15 pF'),
        R('R4', '0', 'XTAL_P', 'XTAL_IN', B, 'series element on XTAL_P (tune after RF test)'),
        C('C13', '22pF', 'XTAL_IN', 'GND', B, 'crystal load'), C('C14', '22pF', 'XTAL_N', 'GND', B, 'crystal load'),
        R('R5', '470', 'U0TXD', 'U0TXD_R', B, 'U0TXD harmonic suppression (Espressif ref. 499R)'),
        R('R6', '33', 'SPI_CLK_MCU', 'SPI_CLK', B, 'SPI clock series R'),
        R('R7', '33', 'SPI_MOSI_MCU', 'SPI_MOSI', B, 'SPI data series R'),
    ]
    B = 'RF'
    c += [
        C('C15', '1.5pF', 'LNA_IN', 'GND', B, 'chip-side CLC match (tune)'),
        L('L2', '2.0nH', 'LNA_IN', 'RF_MID', B, 'C76769', 'MLG1005S2N0BT000', 'chip-side CLC match (tune)'),
        C('C16', '1.5pF', 'RF_MID', 'GND', B, 'chip-side CLC match (tune)'),
        C('C17', '1.5pF', 'RF_MID', 'GND', B, 'antenna-side match, not fitted', dnp=True),
        R('R8', '0', 'RF_MID', 'RF_FEED', B, 'antenna-side match series (0R or L)'),
        C('C18', '1.5pF', 'RF_FEED', 'GND', B, 'antenna-side match, not fitted', dnp=True),
        Comp('AE1', 'Device:Antenna_Chip', 'ANT3216LL00R2400A', 'ana:ANT_Yageo_ANT3216LL00R2400A',
             {'1': 'RF_FEED', '2': None}, 'C293767', 'ANT3216LL00R2400A', block=B,
             desc='2.4 GHz ceramic chip antenna at the ear-tip end'),
    ]
    B = 'Power'
    c += [
        Comp('J1', 'ana:WirePads_8', 'FRAME_LINK', FP_LINK, {str(i + 1): n for i, n in enumerate(LINK_ORDER)},
             block=B, desc='8-wire link to left temple: VSYS GND D+ D- GND VBUS CHRG LED_K'),
        C('C19', '10uF/16V', 'VSYS', 'GND', B, 'VSYS entry bulk'),
        Comp('U2', 'Regulator_Linear:AP2112K-3.3', 'AP2112K-3.3', 'Package_TO_SOT_SMD:SOT-23-5',
             {'1': 'VSYS', '2': 'GND', '3': 'VSYS', '4': None, '5': '3V3'}, 'C51118', 'AP2112K-3.3TRG1', block=B,
             desc='3.3 V 600 mA LDO, 250 mV dropout'),
        C('C20', '1uF', 'VSYS', 'GND', B), C('C21', '10uF', '3V3', 'GND', B), C('C22', '1uF', '3V3', 'GND', B),
        R('R9', '100k', 'VSYS', 'BAT_ADC', B, 'battery/VSYS divider'), R('R10', '100k', 'BAT_ADC', 'GND', B),
        C('C23', '100nF', 'BAT_ADC', 'GND', B, 'ADC filter'),
        R('R11', '100k', '3V3', 'CHRG_N', B, 'charger status pull-up'),
        R('R12', '22k', 'VBUS', 'VBUS_SENSE', B, 'VBUS sense divider (CP2102N + GPIO25)'),
        R('R13', '47k', 'VBUS_SENSE', 'GND', B),
    ]
    B = 'USB-UART + auto-reset'
    cp = {'1': None, '2': 'GND', '3': 'USB_DP', '4': 'USB_DN', '5': '3V3', '6': '3V3', '7': '3V3', '8': 'VBUS_SENSE',
          '9': 'CP_RST', '10': None, '11': None, '12': None, '13': None, '14': None, '15': None, '16': None,
          '17': None, '18': None, '19': 'RTS_N', '20': 'U0TXD_R', '21': 'U0RXD', '22': None, '23': 'DTR_N',
          '24': None, '25': 'GND'}
    c += [
        Comp('U3', 'Interface_USB:CP2102N-Axx-xQFN24', 'CP2102N-A02-GQFN24', 'Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm',
             cp, 'C969151', 'CP2102N-A02-GQFN24R', block=B, desc='USB-UART, self-powered from 3.3 V'),
        C('C24', '4.7uF', '3V3', 'GND', B), C('C25', '100nF', '3V3', 'GND', B), C('C26', '100nF', '3V3', 'GND', B, 'VIO'),
        R('R14', '1k', '3V3', 'CP_RST', B, 'RSTb pull-up (datasheet: 1k)'),
        Comp('Q1', 'Transistor_BJT:MMDT3904', 'MMDT3904', 'Package_TO_SOT_SMD:SOT-363_SC-70-6',
             {'1': 'RTS_N', '2': 'Q1_B1', '6': 'EN', '3': 'IO0', '4': 'DTR_N', '5': 'Q1_B2'},
             'C83572', 'MMDT3904-7-F', block=B, desc='ESP32 auto-reset (DTR/RTS)',
             unit_pins={1: ['1', '2', '6'], 2: ['3', '4', '5']}),
        R('R15', '10k', 'DTR_N', 'Q1_B1', B), R('R16', '10k', 'RTS_N', 'Q1_B2', B),
        Comp('J2', 'ana:WirePads_6', 'DEBUG', 'ana:TestPads_1x06_P1.30mm',
             {'1': '3V3', '2': 'GND', '3': 'U0TXD', '4': 'U0RXD', '5': 'EN', '6': 'IO0'},
             block=B, desc='bring-up pads: 3V3 GND TX RX EN IO0'),
    ]
    B = 'Sensors'
    c += [
        Comp('U4', 'Sensor:MAX30102', 'MAX30102', 'OptoDevice:Maxim_OLGA-14_3.3x5.6mm_P0.8mm',
             {'1': None, '2': 'I2C_SCL', '3': 'I2C_SDA', '4': 'GND', '5': None, '6': None, '7': None, '8': None,
              '9': '3V3', '10': '3V3', '11': '1V8', '12': 'GND', '13': 'MAX_INT', '14': None},
             'C6454833', 'MAX30102EFD+T', block=B, desc='HR/SpO2, faces the skin (inner face)'),
        C('C27', '10uF', '3V3', 'GND', B, 'VLED+ bulk'), C('C28', '100nF', '3V3', 'GND', B, 'VLED+'),
        C('C29', '1uF', '1V8', 'GND', B, 'MAX30102 VDD'), C('C30', '100nF', '1V8', 'GND', B),
        Comp('U5', 'Regulator_Linear:XC6206PxxxMR', 'XC6206P182MR', 'Package_TO_SOT_SMD:SOT-23',
             {'1': 'GND', '2': '1V8', '3': '3V3'}, 'C21659', 'XC6206P182MR', block=B, desc='1.8 V for MAX30102 core'),
        C('C31', '1uF', '3V3', 'GND', B),
        R('R17', '4.7k', '3V3', 'I2C_SDA', B, 'I2C pull-up'), R('R18', '4.7k', '3V3', 'I2C_SCL', B, 'I2C pull-up'),
        R('R19', '10k', '3V3', 'MAX_INT', B, 'INT pull-up'),
        Comp('U6', 'Sensor_Humidity:SHT4x', 'SHT40-AD1B', 'Sensor_Humidity:Sensirion_DFN-4_1.5x1.5mm_P0.8mm_SHT4x_NoCentralPad',
             {'1': 'I2C_SDA', '2': 'I2C_SCL', '3': '3V3', '4': 'GND'}, 'C2909890', 'SHT40-AD1B-R2', block=B,
             desc='temperature + humidity (replaces DHT11), I2C 0x44'),
        C('C32', '100nF', '3V3', 'GND', B),
    ]
    B = 'Touch + LED + display'
    c += [
        Comp('U7', 'ana:TTP223', 'TTP223-BA6', 'Package_TO_SOT_SMD:SOT-23-6',
             {'1': 'TOUCH', '2': 'GND', '3': 'TOUCH_PAD', '4': 'GND', '5': '3V3', '6': '3V3'},
             'C42422127', 'TTP223-BA6-TD', block=B, desc='toggle mode, power-up low (like V1)'),
        C('C33', '100nF', '3V3', 'GND', B),
        C('C34', '22pF', 'TOUCH_PAD', 'GND', B, 'touch sensitivity trim, fit 0-50 pF if needed', dnp=True),
        Comp('E1', 'ana:TouchElectrode', 'TOUCH', 'ana:TouchElectrode_5x12mm_Back', {'1': 'TOUCH_PAD'}, block=B,
             desc='electrode on outer copper, touch through the cover'),
        Comp('Q2', 'Transistor_FET:AO3400A', 'AO3400A', 'Package_TO_SOT_SMD:SOT-23',
             {'1': 'TOUCH', '2': 'GND', '3': 'LED_K'}, 'C20917', 'AO3400A', block=B, desc='low-side LED switch'),
        R('R20', '100k', 'TOUCH', 'GND', B, 'gate pull-down while 3V3 ramps'),
        LED('D1', 'WHITE side', 'LED_K', 'LED1_A', B, FP_SIDE_LED, 'C46635136', 'YLED0602WSIDE',
            'side-emitting white LED at hinge end'),
        R('R21', '100', 'VSYS', 'LED1_A', B, 'LED current ~5-15 mA'),
        Comp('J3', 'ana:WirePads_2', 'EXT_LED', FP_PADS2, {'1': 'LEDX1_A', '2': 'LED_K'}, block=B,
             desc='optional external flashlight LED at the frame corner (+ / -)'),
        R('R22', '68', 'VSYS', 'LEDX1_A', B, 'external LED current'),
        Comp('J4', 'ana:WirePads_7', 'OLED', 'ana:WirePads_1x07_P1.50mm', {str(i + 1): n for i, n in enumerate(OLED_ORDER)},
             block=B, desc='to 7-pin SPI SSD1306 OLED module: GND VCC D0 D1 RES DC CS'),
    ]
    return c


# ==============================================================================================
# LEFT temple: power board
# ==============================================================================================
def power_board():
    c = []
    B = 'USB-C + ESD'
    usb = {'A1': 'GND', 'A12': 'GND', 'B1': 'GND', 'B12': 'GND', 'A4': 'VBUS', 'A9': 'VBUS', 'B4': 'VBUS', 'B9': 'VBUS',
           'A5': 'CC1', 'B5': 'CC2', 'A6': 'USB_DP', 'B6': 'USB_DP', 'A7': 'USB_DN', 'B7': 'USB_DN',
           'A8': None, 'B8': None, 'S1': 'GND'}
    c += [
        Comp('J1', 'Connector:USB_C_Receptacle_USB2.0_16P', 'USB-C', 'Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12',
             usb, 'C165948', 'TYPE-C-31-M-12', block=B, desc='charge + firmware upload, bottom edge near hinge'),
        R('R1', '5.1k', 'CC1', 'GND', B, 'UFP Rd'), R('R2', '5.1k', 'CC2', 'GND', B, 'UFP Rd'),
        Comp('U1', 'Power_Protection:USBLC6-2SC6', 'USBLC6-2SC6', 'Package_TO_SOT_SMD:SOT-23-6',
             {'1': 'USB_DP', '6': 'USB_DP', '3': 'USB_DN', '4': 'USB_DN', '5': 'VBUS', '2': 'GND'},
             'C7519', 'USBLC6-2SC6', block=B, desc='USB ESD'),
        C('C1', '4.7uF', 'VBUS', 'GND', B, 'VBUS input (<10 uF per USB spec)'),
    ]
    B = 'Charger'
    c += [
        Comp('U2', 'Battery_Management:TP4056-42-ESOP8', 'TP4056', 'Package_SO:HSOP-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.1mm',
             {'1': 'GND', '2': 'PROG', '3': 'GND', '4': 'VBUS', '5': 'VBAT', '6': 'STDBY_N', '7': 'CHRG_L', '8': 'VBUS', '9': 'GND'},
             'C16581', 'TP4056-42-ESOP8', block=B, desc='Li-ion linear charger, 4.2 V'),
        R('R3', '27k', 'PROG', 'GND', B, 'Ichg = 1200/27k = 44 mA (0.7C for 60 mAh)'),
        C('C2', '1uF', 'VBUS', 'GND', B), C('C3', '4.7uF', 'VBAT', 'GND', B, 'BAT pin'),
        LED('D1', 'RED charging', 'CHRG_L', 'LED_CHG_A', B, FP_LED0402, 'C130719', 'NCD0402R1'),
        R('R4', '1k', 'VBUS', 'LED_CHG_A', B),
        LED('D2', 'GREEN full', 'STDBY_N', 'LED_FULL_A', B, FP_LED0402, 'C130723', 'NCD0402G1'),
        R('R5', '1k', 'VBUS', 'LED_FULL_A', B),
        Comp('D3', 'Device:D_Schottky', 'RB521S30', 'Diode_SMD:D_SOD-523', {'1': 'CHRG_L', '2': 'CHRG_N'},
             'C145179', 'RB521S30T1G', block=B, desc='isolates CHRG to ESP32 (no back-power when off)'),
    ]
    B = 'Protection'
    c += [
        Comp('U3', 'Battery_Management:DW01A', 'DW01A', 'Package_TO_SOT_SMD:SOT-23-6',
             {'1': 'OD', '2': 'CS', '3': 'OC', '4': None, '5': 'DW_VCC', '6': 'BATN'}, 'C351410', 'DW01A', block=B,
             desc='Li-ion protection (OV 4.3 V, UV 2.4 V, OC)'),
        R('R6', '100', 'VBAT', 'DW_VCC', B), C('C4', '100nF', 'DW_VCC', 'BATN', B),
        R('R7', '1k', 'GND', 'CS', B, 'CS/VM sense'),
        Comp('Q1', 'ana:FS8205A', 'FS8205A', 'Package_TO_SOT_SMD:SOT-23-6',
             {'1': 'BATN', '2': 'PROT_D', '3': 'GND', '4': 'OC', '5': 'PROT_D', '6': 'OD'},
             'C908265', 'FS8205A', block=B, desc='dual N-MOSFET, common drain'),
        Comp('BT1', 'Device:Battery_Cell', 'LiPo 3.7V ~60mAh', 'ana:BatteryPads_2x2.5mm',
             {'1': 'VBAT', '2': 'BATN'}, block=B, desc='cell in ear-bend tip, ~3.5 x 7 x 30 mm'),
    ]
    B = 'Power path + switches'
    c += [
        Comp('D4', 'Device:D_Schottky', 'B5819WS', 'Diode_SMD:D_SOD-323', {'1': 'VSYS_RAW', '2': 'VBUS'},
             'C64886', 'B5819WS', block=B, desc='USB -> system when plugged'),
        Comp('Q2', 'Transistor_FET:AO3401A', 'AO3401A', 'Package_TO_SOT_SMD:SOT-23',
             {'1': 'VBUS', '2': 'VSYS_RAW', '3': 'VBAT'}, 'C15127', 'AO3401A', block=B,
             desc='load sharing: battery -> system, off while USB present'),
        R('R8', '100k', 'VBUS', 'GND', B, 'gate pull-down when USB absent'),
        C('C5', '10uF/16V', 'VSYS_RAW', 'GND', B),
        Comp('Q3', 'Transistor_FET:AO3401A', 'AO3401A', 'Package_TO_SOT_SMD:SOT-23',
             {'1': 'PWR_G', '2': 'VSYS_RAW', '3': 'VSYS'}, 'C15127', 'AO3401A', block=B, desc='main power switch FET'),
        R('R9', '100k', 'VSYS_RAW', 'PWR_G', B, 'off by default'),
        Comp('SW1', 'Switch:SW_SPDT', 'POWER', 'Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02',
             {'1': 'GND', '2': 'PWR_G', '3': None}, 'C431540', 'MSK12C02', block=B, desc='main power slide switch'),
        C('C6', '10uF/16V', 'VSYS', 'GND', B),
        Comp('Q4', 'Transistor_FET:AO3401A', 'AO3401A', 'Package_TO_SOT_SMD:SOT-23',
             {'1': 'BT_G', '2': 'VBAT', '3': 'VBAT_BT'}, 'C15127', 'AO3401A', block=B, desc='BT headset power FET'),
        R('R10', '100k', 'VBAT', 'BT_G', B),
        Comp('SW2', 'Switch:SW_SPDT', 'BT', 'Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02',
             {'1': 'GND', '2': 'BT_G', '3': None}, 'C431540', 'MSK12C02', block=B, desc='BT headset slide switch'),
        C('C7', '1uF', 'VBAT_BT', 'GND', B),
        Comp('J2', 'ana:WirePads_2', 'BT_HEADSET', FP_PADS2, {'1': 'VBAT_BT', '2': 'GND'}, block=B,
             desc='to Bluetooth headset module: + / -'),
    ]
    B = 'Link + LED'
    c += [
        Comp('J3', 'ana:WirePads_8', 'FRAME_LINK', FP_LINK, {str(i + 1): n for i, n in enumerate(LINK_ORDER)},
             block=B, desc='8-wire link to right temple'),
        LED('D5', 'WHITE side', 'LED_K', 'LED2_A', B, FP_SIDE_LED, 'C46635136', 'YLED0602WSIDE',
            'side-emitting white LED at hinge end'),
        R('R11', '100', 'VSYS', 'LED2_A', B),
        Comp('J4', 'ana:WirePads_2', 'EXT_LED', FP_PADS2, {'1': 'LEDX2_A', '2': 'LED_K'}, block=B,
             desc='optional external flashlight LED at the frame corner'),
        R('R12', '68', 'VSYS', 'LEDX2_A', B),
    ]
    return c


BOARDS = {
    'ana-main': dict(title='ANA V1 right temple - ESP32 main board', build=main_board, rev='1.0'),
    'ana-power': dict(title='ANA V1 left temple - power board', build=power_board, rev='1.0'),
}


def nets_of(comps):
    nets = {}
    for cp in comps:
        for pin, net in cp.pins.items():
            if net:
                nets.setdefault(net, []).append((cp.ref, pin))
    return nets


if __name__ == '__main__':
    for name, b in BOARDS.items():
        comps = b['build']()
        nets = nets_of(comps)
        single = {n: v for n, v in nets.items() if len(v) < 2}
        print(name, len(comps), 'parts', len(nets), 'nets; single-pin nets:', single)
