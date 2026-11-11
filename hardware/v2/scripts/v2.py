"""ANA V2 custom boards: parts, nets, placement and build.  Uses the V1 generator in hardware/scripts as a library.

    /usr/bin/python3 hardware/v2/scripts/v2.py [ana2-right|ana2-left|ana2-pod] [--no-export|--export-only]

ana2-right  right temple: 9-axis IMU, heart rate/SpO2, skin temperature, microphone, speaker amplifier, touch,
            IR-illuminator and flashlight drivers.  Everything is an I2C / I2S device of the Raspberry Pi.
ana2-left   left temple: second microphone, speaker amplifier, IR-illuminator and flashlight drivers.
ana2-pod    hub board in the rear pod, next to the Raspberry Pi CM5 carrier: joins both temple harnesses to the
            Pi's GPIO pins, ESP32-S3 hub (power button, battery level, always-on BLE) and 4-channel EEG front end.
See hardware/v2/README.md for the whole system.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(V2, '..', 'scripts')))
import pcbnew  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
import build, circuits, export, fpgen, outline, pcbgen, symlib  # noqa: E402
from circuits import Comp, R, C, LED, FP_C0603, FP_LED0402, FP_LINK, FP_PADS2  # noqa: E402

# the temple harness: two rows of 8 wire holes, same pad order on the temple and on the pod
LINK_A = ['5V', 'GND', 'SDA', 'SCL', 'IMU_INT', 'PPG_INT', 'TOUCH', 'IR_PWM']
LINK_B = ['5V', 'GND', 'I2S_BCLK', 'I2S_LRCLK', 'MIC_SD', 'SPK_SD', 'GND', 'LED_PWM']
PI_A = ['SDA', 'SCL', 'IMU_INT', 'PPG_INT', 'TOUCH', 'IR_PWM', 'LED_PWM', 'GND']          # GPIO 2 3 4 5 17 12 13
PI_B = ['I2S_BCLK', 'I2S_LRCLK', 'MIC_SD', 'SPK_SD', 'HUB_TX', 'HUB_RX', 'PI_SHDN', 'GND']  # GPIO 18 19 20 21 15 14 6
ELECTRODES = ['E1', 'E2', 'E3', 'E4', 'E_REF', 'E_BIAS', 'GND']
C22 = dict(fp=FP_C0603, lcsc='C109448')


def link(ref, order, desc, keep=None):
    return Comp(ref, 'ana:WirePads_8', 'LINK', FP_LINK,
                {str(i + 1): (n if keep is None or n in keep else None) for i, n in enumerate(order)},
                block='Harness', desc=desc)


def pads2(ref, value, a, b, block, desc):
    return Comp(ref, 'ana:WirePads_2', value, FP_PADS2, {'1': a, '2': b}, block=block, desc=desc)


def ldo(c, B):
    c += [Comp('U1', 'Regulator_Linear:AP2112K-3.3', 'AP2112K-3.3', 'Package_TO_SOT_SMD:SOT-23-5',
               {'1': '5V', '2': 'GND', '3': '5V', '4': None, '5': '3V3'}, 'C51118', 'AP2112K-3.3TRG1', block=B,
               desc='3.3 V for the sensors and microphone'),
          C('C1', '10uF/16V', '5V', 'GND', B, '5 V entry bulk'), C('C2', '1uF', '5V', 'GND', B),
          C('C3', '10uF', '3V3', 'GND', B), C('C4', '1uF', '3V3', 'GND', B)]


def audio(c, right):
    B = 'Audio'
    c += [Comp('U6', 'Sensor_Audio:ICS-43434', 'ICS-43434', 'Sensor_Audio:InvenSense_ICS-43434-6_3.5x2.65mm',
               {'1': 'I2S_LRCLK', '2': '3V3' if right else 'GND', '3': 'GND', '4': 'I2S_BCLK', '5': '3V3', '6': 'MIC_SD'},
               'C5656610', 'ICS-43434', block=B,
               desc='I2S MEMS microphone, sound port through the board; this one is the %s channel'
                    % ('right' if right else 'left')),
          C('C10', '100nF', '3V3', 'GND', B),
          Comp('U7', 'Audio:MAX98357A', 'MAX98357A', 'Package_DFN_QFN:TQFN-16-1EP_3x3mm_P0.5mm_EP1.23x1.23mm',
               {'1': 'SPK_SD', '2': None, '3': 'GND', '4': 'AMP_MODE', '5': None, '6': None, '7': '5V', '8': '5V',
                '9': 'SPK_P', '10': 'SPK_N', '11': 'GND', '12': None, '13': None, '14': 'I2S_LRCLK', '15': 'GND',
                '16': 'I2S_BCLK', '17': 'GND'}, 'C910544', 'MAX98357AETE+T', block=B,
               desc='I2S class-D speaker amplifier, 9 dB gain (GAIN pin open)'),
          R('R10', '1M', '5V', 'AMP_MODE', B, 'SD_MODE = 0.45 V: plays (left + right) / 2'),
          C('C11', '10uF/16V', '5V', 'GND', B), C('C12', '100nF', '5V', 'GND', B),
          pads2('J5', 'SPEAKER', 'SPK_P', 'SPK_N', B, '8 ohm micro speaker next to the ear (+ / -)')]


def lights(c):
    B = 'IR + flashlight'
    c += [Comp('Q1', 'Transistor_FET:AO3400A', 'AO3400A', 'Package_TO_SOT_SMD:SOT-23',
               {'1': 'IR_PWM', '2': 'GND', '3': 'IR_K'}, 'C20917', 'AO3400A', block=B, desc='IR illuminator switch (PWM)'),
          R('R20', '100k', 'IR_PWM', 'GND', B, 'off while the Pi boots'),
          R('R21', '100', '5V', 'IR_A', B, '4 x 100R = 25R: about 80 mA through two 850 nm LEDs in series'),
          R('R22', '100', '5V', 'IR_A', B), R('R23', '100', '5V', 'IR_A', B), R('R24', '100', '5V', 'IR_A', B),
          pads2('J3', 'IR_LED', 'IR_A', 'IR_K', B, 'two 850 nm IR LEDs in series beside the camera (+ / -)'),
          Comp('Q2', 'Transistor_FET:AO3400A', 'AO3400A', 'Package_TO_SOT_SMD:SOT-23',
               {'1': 'LED_PWM', '2': 'GND', '3': 'LED_K'}, 'C20917', 'AO3400A', block=B, desc='flashlight switch (PWM)'),
          R('R25', '100k', 'LED_PWM', 'GND', B), R('R26', '68', '5V', 'LED_A', B, 'white LED, about 28 mA'),
          pads2('J4', 'WHITE_LED', 'LED_A', 'LED_K', B, 'white flashlight LED at the frame corner (+ / -)')]


def right_board():
    c = []
    B = 'Harness'
    c += [link('J1', LINK_A, 'to the pod, row A: 5V GND SDA SCL IMU_INT PPG_INT TOUCH IR_PWM'),
          link('J2', LINK_B, 'to the pod, row B: 5V GND BCLK LRCLK MIC_SD SPK_SD GND LED_PWM')]
    ldo(c, 'Power')
    B = 'Motion'
    bno = {str(i): None for i in range(1, 29)}
    bno.update({'2': 'GND', '3': '3V3', '28': '3V3', '25': 'GND', '9': 'BNO_CAP', '4': 'BNO_BOOT', '5': 'GND', '6': 'GND',
                '11': 'BNO_RST', '14': 'IMU_INT', '17': 'GND', '18': 'GND', '19': 'SCL',
                '20': 'SDA'})
    c += [Comp('U2', 'Sensor_Motion:BNO055', 'BNO055', 'Package_LGA:LGA-28_5.2x3.8mm_P0.5mm', bno, 'C93216', 'BNO055',
               block=B, desc='9-axis IMU with on-chip sensor fusion (head orientation), I2C 0x28'),
          C('C20', '100nF', 'BNO_CAP', 'GND', B, 'CAP pin'), C('C21', '100nF', '3V3', 'GND', B),
          C('C22', '100nF', '3V3', 'GND', B),
          R('R1', '10k', '3V3', 'BNO_BOOT', B, 'normal boot'), R('R2', '10k', '3V3', 'BNO_RST', B, 'reset pull-up')]
    B = 'Vitals'
    c += [Comp('U3', 'Sensor:MAX30102', 'MAX30102', 'OptoDevice:Maxim_OLGA-14_3.3x5.6mm_P0.8mm',
               {'1': None, '2': 'SCL', '3': 'SDA', '4': 'GND', '5': None, '6': None, '7': None, '8': None,
                '9': '3V3', '10': '3V3', '11': '1V8', '12': 'GND', '13': 'PPG_INT', '14': None},
               'C6454833', 'MAX30102EFD+T', block=B, desc='heart rate + SpO2, faces the skin, I2C 0x57'),
          C('C30', '10uF', '3V3', 'GND', B, 'VLED+ bulk'), C('C31', '100nF', '3V3', 'GND', B),
          C('C32', '1uF', '1V8', 'GND', B), C('C33', '100nF', '1V8', 'GND', B),
          Comp('U4', 'Regulator_Linear:XC6206PxxxMR', 'XC6206P182MR', 'Package_TO_SOT_SMD:SOT-23',
               {'1': 'GND', '2': '1V8', '3': '3V3'}, 'C21659', 'XC6206P182MR', block=B, desc='1.8 V for the MAX30102 core'),
          C('C34', '1uF', '3V3', 'GND', B),
          R('R3', '10k', '3V3', 'PPG_INT', B, 'INT pull-up'),
          R('R4', '4.7k', '3V3', 'SDA', B, 'I2C pull-up'), R('R5', '4.7k', '3V3', 'SCL', B, 'I2C pull-up'),
          Comp('U5', 'Sensor_Temperature:TMP117xxDRV', 'TMP117', 'Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm',
               {'1': 'SCL', '2': 'GND', '3': None, '4': 'GND', '5': '3V3', '6': 'SDA'}, 'C2803242', 'TMP117MAIDRVR',
               block=B, desc='skin temperature, +-0.1 C, I2C 0x48; couple it to the skin with a metal tab or thermal pad'),
          C('C35', '100nF', '3V3', 'GND', B)]
    audio(c, True)
    B = 'Touch'
    c += [Comp('U8', 'ana:TTP223', 'TTP223-BA6', 'Package_TO_SOT_SMD:SOT-23-6',
               {'1': 'TOUCH', '2': 'GND', '3': 'TOUCH_PAD', '4': 'GND', '5': '3V3', '6': 'GND'},
               'C42422127', 'TTP223-BA6-TD', block=B, desc='touch key: output is high while touched'),
          C('C40', '100nF', '3V3', 'GND', B),
          Comp('E1', 'ana:TouchElectrode', 'TOUCH', 'ana:TouchElectrode_4x10mm_Back', {'1': 'TOUCH_PAD'}, block=B,
               desc='electrode on the outer copper, touch through the cover')]
    lights(c)
    return c


def left_board():
    c = []
    keep = {'5V', 'GND', 'IR_PWM', 'I2S_BCLK', 'I2S_LRCLK', 'MIC_SD', 'SPK_SD', 'LED_PWM'}
    c += [link('J1', LINK_A, 'to the pod, row A (same order as the right temple; only 5V GND IR_PWM used)', keep),
          link('J2', LINK_B, 'to the pod, row B: 5V GND BCLK LRCLK MIC_SD SPK_SD GND LED_PWM', keep)]
    ldo(c, 'Power')
    audio(c, False)
    lights(c)
    return c


def pod_board():
    c = []
    B = 'Harness'
    c += [link('J1', LINK_A, 'right temple, row A'), link('J2', LINK_B, 'right temple, row B'),
          link('J3', LINK_A, 'left temple, row A', {'5V', 'GND', 'IR_PWM'}), link('J4', LINK_B, 'left temple, row B'),
          link('J5', PI_A, 'Raspberry Pi GPIO 2, 3, 4, 5, 17, 12, 13 and GND'),
          link('J6', PI_B, 'Raspberry Pi GPIO 18, 19, 20, 21, 15 (RXD), 14 (TXD), 6 and GND'),
          R('R1', '100k', 'MIC_SD', 'GND', B, 'microphone data line pull-down (two mics share it)')]
    B = 'Power'
    usb = {'A1': 'GND', 'A12': 'GND', 'B1': 'GND', 'B12': 'GND', 'A4': 'VBUS', 'A9': 'VBUS', 'B4': 'VBUS', 'B9': 'VBUS',
           'A5': 'CC1', 'B5': 'CC2', 'A6': 'USB_DP', 'B6': 'USB_DP', 'A7': 'USB_DN', 'B7': 'USB_DN',
           'A8': None, 'B8': None, 'S1': 'GND'}
    c += [pads2('J7', '5V_IN', '5V', 'GND', B, '5 V from the battery pack (+ / -)'),
          Comp('J8', 'Connector:USB_C_Receptacle_USB2.0_16P', 'USB-C', 'Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12',
               usb, 'C165948', 'TYPE-C-31-M-12', block=B, desc='hub USB: to a Pi USB port, or to a PC for flashing'),
          R('R2', '5.1k', 'CC1', 'GND', B), R('R3', '5.1k', 'CC2', 'GND', B),
          Comp('U5', 'Power_Protection:USBLC6-2SC6', 'USBLC6-2SC6', 'Package_TO_SOT_SMD:SOT-23-6',
               {'1': 'USB_DP', '6': 'USB_DP', '3': 'USB_DN', '4': 'USB_DN', '5': 'VBUS', '2': 'GND'},
               'C7519', 'USBLC6-2SC6', block=B, desc='USB ESD'),
          Comp('D1', 'Device:D_Schottky', 'B5819WS', 'Diode_SMD:D_SOD-323', {'1': '5V', '2': 'VBUS'},
               'C64886', 'B5819WS', block=B, desc='USB can power the hub when the battery pack is off'),
          Comp('U2', 'Regulator_Linear:AP2112K-3.3', 'AP2112K-3.3', 'Package_TO_SOT_SMD:SOT-23-5',
               {'1': '5V', '2': 'GND', '3': '5V', '4': None, '5': '3V3'}, 'C51118', 'AP2112K-3.3TRG1', block=B),
          C('C1', '10uF/16V', '5V', 'GND', B), C('C2', '1uF', '5V', 'GND', B),
          C('C3', '10uF', '3V3', 'GND', B), C('C4', '1uF', '3V3', 'GND', B),
          pads2('J12', 'BAT/EN', 'BAT_SENSE', 'PWR_EN', B, '1: cell voltage for the gauge  2: enable output for the pack'),
          R('R4', '100k', 'BAT_SENSE', 'BAT_ADC', B, 'cell voltage / 2'), R('R5', '100k', 'BAT_ADC', 'GND', B),
          C('C5', '100nF', 'BAT_ADC', 'GND', B),
          pads2('J11', 'BUTTON', 'BTN', 'GND', B, 'power / wake button'), R('R6', '10k', '3V3', 'BTN', B)]
    B = 'Hub MCU'
    esp = {}
    for num, p in symlib.pins('RF_Module:ESP32-S3-MINI-1').items():
        esp[num] = 'GND' if p['name'] == 'GND' else None
    esp.update({'3': '3V3', '45': 'HUB_EN', '4': 'HUB_IO0', '23': 'USB_DN', '24': 'USB_DP', '39': 'HUB_TX', '40': 'HUB_RX',
                '8': 'BAT_ADC', '9': 'BTN', '10': 'PWR_EN', '25': 'PI_SHDN', '19': 'HUB_LED',
                '11': 'EEG_PWDN', '12': 'EEG_RST', '13': 'EEG_START', '14': 'EEG_CS', '15': 'EEG_MOSI',
                '16': 'EEG_SCLK', '17': 'EEG_MISO', '18': 'EEG_DRDY'})
    c += [Comp('U1', 'RF_Module:ESP32-S3-MINI-1', 'ESP32-S3-MINI-1-N8', 'RF_Module:ESP32-S2-MINI-1', esp, 'C2913206',
               'ESP32-S3-MINI-1-N8', block=B, desc='always-on hub: power button, battery level, EEG stream, BLE'),
          C('C6', '10uF', '3V3', 'GND', B), C('C7', '100nF', '3V3', 'GND', B),
          R('R7', '10k', '3V3', 'HUB_EN', B), C('C8', '1uF', 'HUB_EN', 'GND', B, 'EN delay'),
          pads2('J9', 'BOOT', 'HUB_IO0', 'GND', B, 'short while plugging USB to force the bootloader'),
          LED('D2', 'GREEN', 'GND', 'HUB_LED_A', B, FP_LED0402, 'C130723', 'NCD0402G1', 'status'),
          R('R8', '1k', 'HUB_LED', 'HUB_LED_A', B)]
    B = 'EEG'
    ads = {str(i): None for i in range(1, 65)}
    for p in (19, 21, 22, 56, 59, 54):
        ads[str(p)] = 'AVDD'
    for p in (20, 23, 32, 57, 58, 53, 33, 49, 51, 25, 31, 41):       # AVSS, DGND, VREFN, RESV1, DAISY_IN
        ads[str(p)] = 'GND'
    for p in (48, 50, 52):                                           # DVDD, CLKSEL = 1 (internal clock)
        ads[str(p)] = '3V3'
    for p in (1, 2, 3, 4, 5, 6, 7, 8, 18, 62):                       # unused inputs (ch 5-8, SRB2, BIASIN) -> AVDD
        ads[str(p)] = 'AVDD'
    ads.update({'16': 'IN1P', '14': 'IN2P', '12': 'IN3P', '10': 'IN4P', '17': 'SRB1', '24': 'VREFP', '28': 'VCAP1',
                '30': 'VCAP2', '55': 'VCAP3', '26': 'VCAP4', '34': 'EEG_MOSI', '35': 'EEG_PWDN', '36': 'EEG_RST',
                '37': 'EEG_CLK', '38': 'EEG_START', '39': 'EEG_CS', '40': 'EEG_SCLK', '43': 'EEG_MISO',
                '47': 'EEG_DRDY', '42': 'EEG_GPIO', '44': 'EEG_GPIO', '45': 'EEG_GPIO', '46': 'EEG_GPIO',
                '60': 'BIASREF', '61': 'BIASINV', '63': 'BIASOUT'})
    c += [Comp('U3', 'Analog_ADC:ADS1299', 'ADS1299-4', 'Package_QFP:TQFP-64_10x10mm_P0.5mm', ads, 'C2651574',
               'ADS1299-4PAGR', block=B, desc='4-channel 24-bit EEG front end; inputs 1-4 against the reference on SRB1'),
          R('R10', '33', '5V', 'AVDD', B, 'analog supply filter (2 x 33R with 12 uF)'), R('R11', '33', '5V', 'AVDD', B),
          C('C10', '10uF/16V', 'AVDD', 'GND', B), C('C11', '1uF', 'AVDD', 'GND', B), C('C12', '100nF', 'AVDD', 'GND', B),
          C('C13', '100nF', 'AVDD', 'GND', B), C('C14', '1uF', 'AVDD', 'GND', B, 'AVDD1'),
          C('C15', '1uF', '3V3', 'GND', B, 'DVDD'), C('C16', '100nF', '3V3', 'GND', B),
          C('C17', '10uF', 'VREFP', 'GND', B, 'reference'), C('C18', '100nF', 'VREFP', 'GND', B),
          C('C19', '1uF', 'VCAP2', 'GND', B), C('C20', '1uF', 'VCAP3', 'GND', B), C('C21', '100nF', 'VCAP3', 'GND', B),
          C('C22', '1uF', 'VCAP4', 'GND', B)]
    for i in range(5):                                               # VCAP1: 100 uF (datasheet) = 5 x 22 uF
        c.append(Comp('C%d' % (23 + i), 'Device:C', '22uF', C22['fp'], {'1': 'VCAP1', '2': 'GND'}, C22['lcsc'], '',
                      False, B, 'VCAP1, 5 x 22 uF' if i == 0 else ''))
    c += [R('R12', '10k', 'EEG_GPIO', 'GND', B, 'unused GPIO1-4'), R('R13', '10k', 'EEG_CLK', 'GND', B, 'CLK unused'),
          R('R14', '10k', '3V3', 'EEG_PWDN', B), R('R15', '10k', '3V3', 'EEG_RST', B),
          R('R16', '100k', 'AVDD', 'BIASREF', B, 'mid-supply bias reference'), R('R17', '100k', 'BIASREF', 'GND', B),
          C('C28', '100nF', 'BIASREF', 'GND', B),
          R('R18', '1M', 'BIASINV', 'BIASOUT', B, 'bias amplifier feedback (datasheet: 1M || 1.5 nF)'),
          Comp('C29', 'Device:C', '1.5nF', circuits.FP_C, {'1': 'BIASINV', '2': 'BIASOUT'}, 'C107022', '', False, B),
          Comp('J10', 'ana:WirePads_7', 'ELECTRODES', 'ana:WirePads_1x07_P1.50mm',
               {str(i + 1): n for i, n in enumerate(ELECTRODES)}, block=B,
               desc='dry electrodes: 1 left ear tip, 2 right ear tip, 3 left temple, 4 right temple, 5 reference '
                    '(nose pad), 6 bias (other nose pad), 7 cable shield')]
    for i, (e, n) in enumerate((('E1', 'IN1P'), ('E2', 'IN2P'), ('E3', 'IN3P'), ('E4', 'IN4P'), ('E_REF', 'SRB1'))):
        c += [R('R%d' % (20 + i), '4.7k', e, n, B, 'input protection + RF filter' if i == 0 else ''),
              C('C%d' % (30 + i), '3.3nF', n, 'GND', B)]
    c.append(R('R25', '4.7k', 'BIASOUT', 'E_BIAS', B, 'bias electrode drive'))
    return c


BOARDS = {
    'ana2-right': dict(title='ANA V2 right temple - sensors + audio', build=right_board, rev='2.0'),
    'ana2-left': dict(title='ANA V2 left temple - audio + lights', build=left_board, rev='2.0'),
    'ana2-pod': dict(title='ANA V2 rear pod - harness hub, ESP32-S3, EEG', build=pod_board, rev='2.0'),
}
TEMPLE_CFG = json.load(open(os.path.join(V2, 'config', 'temple.json')))
POD_W, POD_H, NOTCH = 62.0, 42.0, (21.5, 38.5, 5.6)      # antenna notch: x0, x1, depth


class Pod:
    """Rectangular hub board with a notch in the top edge under the ESP32 module's antenna."""
    def __init__(self):
        x0, x1, d = NOTCH
        self.points = [(0, 0), (x0, 0), (x0, d), (x1, d), (x1, 0), (POD_W, 0), (POD_W, POD_H), (0, POD_H)]
        self.poly = Polygon(self.points)


# ------------------------------------------------------------------------------------------------ placement
def near(bb, ref, anchor, **kw):
    kw.setdefault('spacing', 0.3)
    kw.setdefault('max_r', 14.0)
    bb.autoplace(ref, bb.pad_pos(*anchor) if isinstance(anchor[0], str) else anchor, **kw)


def temple_common(bb):
    t = bb.t
    bb.place('J3', 2.3, 3.2, 90)                       # IR LED pads at the hinge end, beside the camera
    bb.place('J4', 2.3, 10.2, 90)                      # white LED pads
    bb.place('U6', 13.0, 3.2, 0)                       # microphone near the front, port on the outer face
    bb.place('U7', 93.0, 6.0, 0)                       # speaker amplifier next to the ear
    bb.place('J5', 99.0, 9.2, 0)
    s = 14.0
    for ref, tt in (('J1', 2.4), ('J2', t.h_tip(s) - 2.4)):          # two wire rows along the ear tip
        x, y = t.tip(s, tt)
        bb.place(ref, x, y, t.tip_rot)
    for ref, a in (('Q1', ('J3', '2')), ('R20', ('Q1', '1')), ('R21', ('J3', '1')), ('R22', ('J3', '1')),
                   ('R23', ('J3', '1')), ('R24', ('J3', '1')), ('Q2', ('J4', '2')), ('R25', ('Q2', '1')),
                   ('R26', ('J4', '1')), ('C10', ('U6', '5')), ('C12', ('U7', '7')), ('C11', ('U7', '8')),
                   ('R10', ('U7', '4'))):
        near(bb, ref, a)


def layout_right(bb):
    bb.place('E1', 32.5, 6.7, 0, back=True)            # touch electrode on the outer face
    bb.place('U8', 26.5, 6.7, 0)
    bb.place('U5', 44.0, 3.0, 0)                       # skin temperature
    bb.place('U3', 56.0, 6.5, 90)                      # MAX30102, window faces the skin
    bb.place('U2', 70.0, 6.3, 0)                       # IMU
    bb.place('U1', 82.0, 6.0, 0)
    temple_common(bb)
    for ref, a in (('C40', ('U8', '5')), ('C35', ('U5', '5')), ('C31', ('U3', '9')), ('C30', ('U3', '10')),
                   ('C33', ('U3', '11')), ('C32', ('U3', '11')), ('U4', ('U3', '11')), ('C34', ('U4', '3')),
                   ('R3', ('U3', '13')), ('R4', ('U3', '3')), ('R5', ('U3', '2')),
                   ('C20', ('U2', '9')), ('C21', ('U2', '3')), ('C22', ('U2', '28')), ('R1', ('U2', '4')),
                   ('R2', ('U2', '11')), ('C2', ('U1', '1')), ('C1', ('U1', '1')), ('C4', ('U1', '5')),
                   ('C3', ('U1', '5'))):
        near(bb, ref, a)


def layout_left(bb):
    bb.place('U1', 40.0, 6.0, 0)
    temple_common(bb)
    for ref, a in (('C2', ('U1', '1')), ('C1', ('U1', '1')), ('C4', ('U1', '5')), ('C3', ('U1', '5'))):
        near(bb, ref, a)


def layout_pod(bb):
    cx, cy = 30.0, NOTCH[2] + 0.4 + 4.85
    bb.place('U1', cx, cy, 0)
    fp = bb.fps['U1']                                   # library courtyard includes the antenna keep-out: use the body
    for g in list(fp.GraphicalItems()):
        if g.GetLayer() == pcbnew.F_CrtYd:
            fp.Remove(g)
    for z in list(fp.Zones()):
        fp.Remove(z)
    s = pcbnew.PCB_SHAPE(fp)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(pcbgen.P(cx - 7.9, cy - 10.4))
    s.SetEnd(pcbgen.P(cx + 7.9, cy + 10.4))
    s.SetLayer(pcbnew.F_CrtYd)
    s.SetWidth(pcbgen.mm(0.05))
    fp.Add(s)
    fp.BuildCourtyardCaches()
    bb.place('J5', 9.5, 2.0, 0)                        # Pi GPIO rows on the top edge, either side of the antenna
    bb.place('J6', 50.5, 2.0, 0)
    bb.place('J1', 2.0, 13.5, 90)                      # right temple rows on the left edge
    bb.place('J2', 2.0, 27.0, 90)
    bb.place('J3', 34.0, 39.9, 0)                      # left temple rows on the bottom edge
    bb.place('J4', 48.0, 39.9, 0)
    bb.place('J8', 11.5, 38.4, 0)                      # USB-C, opening on the bottom edge
    bb.place('J7', 22.5, 39.9, 0)
    bb.place('U2', 21.0, 33.5, 0)
    bb.place('U3', 46.5, 23.5, 180)                    # EEG front end: inputs face the electrode pads
    bb.place('J10', 59.9, 23.5, 90)
    bb.place('J12', 59.9, 9.5, 90)
    bb.place('J11', 59.9, 35.0, 90)
    bb.place('J9', 15.0, 9.0, 90)
    order = [('R2', ('J8', 'A5')), ('R3', ('J8', 'B5')), ('U5', ('J8', 'A6')), ('D1', ('J7', '1')),
             ('C2', ('U2', '1')), ('C1', ('U2', '1')), ('C4', ('U2', '5')), ('C3', ('U2', '5')),
             ('C7', ('U1', '3')), ('C6', ('U1', '3')), ('R7', ('U1', '45')), ('C8', ('U1', '45')),
             ('R8', ('U1', '19')), ('D2', ('U1', '19')), ('R6', ('J11', '1')), ('R4', ('J12', '1')),
             ('R5', ('U1', '8')), ('C5', ('U1', '8')), ('R1', ('J6', '3')),
             ('C18', ('U3', '24')), ('C17', ('U3', '24')), ('C22', ('U3', '26')), ('C19', ('U3', '30')),
             ('C23', ('U3', '28')), ('C24', ('U3', '28')), ('C25', ('U3', '28')), ('C26', ('U3', '28')),
             ('C27', ('U3', '28')), ('C12', ('U3', '19')), ('C13', ('U3', '56')), ('C11', ('U3', '21')),
             ('C14', ('U3', '54')), ('C21', ('U3', '55')), ('C20', ('U3', '55')), ('C16', ('U3', '48')),
             ('C15', ('U3', '50')), ('C10', ('U3', '59')), ('R10', ('U3', '59')), ('R11', ('U3', '59')),
             ('R12', ('U3', '44')), ('R13', ('U3', '37')), ('R14', ('U3', '35')), ('R15', ('U3', '36')),
             ('R18', ('U3', '61')), ('C29', ('U3', '63')), ('R16', ('U3', '60')), ('R17', ('U3', '60')),
             ('C28', ('U3', '60')), ('R25', ('J10', '6'))]
    for i, pin in enumerate(('16', '14', '12', '10', '17')):
        order += [('C%d' % (30 + i), ('U3', pin)), ('R%d' % (20 + i), ('J10', str(i + 1)))]
    for ref, a in order:
        near(bb, ref, a, max_r=18.0)


SETUP = {
    'ana2-right': dict(layout=layout_right, escapes=['U2', 'U3', 'U5', 'U6', 'U7', 'U8', 'U1', 'U4'],
                       silk=('ANA V2 RIGHT - SENSE - rev 2.0', 60.0, 6.0, 0.8)),
    'ana2-left': dict(layout=layout_left, escapes=['U6', 'U7', 'U1'],
                      silk=('ANA V2 LEFT - AUDIO - rev 2.0', 60.0, 6.0, 0.8)),
    'ana2-pod': dict(layout=layout_pod, escapes=[('J8', dict(length=0.7, direction=(0, 1))), 'U1', 'U3', 'U5', 'U2'],
                     silk=('ANA V2 POD - HUB + EEG - rev 2.0', 31.0, 34.0, 1.0)),
}
POWER = {'5V': ([0.4, 0.3, 0.25, 0.15], (0, 1, 2), True, 0), '3V3': ([0.3, 0.2, 0.15], (0, 1, 2), True, 0),
         'AVDD': ([0.3, 0.2, 0.15], (0, 1, 2), True, 0), 'VBUS': ([0.4, 0.25], (0, 1, 2), True, 0),
         'SPK_P': ([0.3, 0.2], (0, 1, 2), True, 0), 'SPK_N': ([0.3, 0.2], (0, 1, 2), True, 0),
         'IR_A': ([0.3, 0.2], (0, 1, 2), True, 0), 'IR_K': ([0.3, 0.2], (0, 1, 2), True, 0),
         'LED_A': ([0.25, 0.15], (0, 1, 2), True, 0), 'LED_K': ([0.25, 0.15], (0, 1, 2), True, 0),
         'VCAP1': ([0.25, 0.15], (0, 1, 2), True, 0)}
NOTES = [
    'Board: 4-layer 0.8 mm FR-4, JLCPCB rules (0.1/0.1 mm, vias 0.4/0.2 mm). All SMT parts on F (top) side.',
    'Temple harness (two rows of 8 wires, same order on temple and pod): A = ' + ' '.join(LINK_A) + ' | B = ' +
    ' '.join(LINK_B),
    'Pi GPIO: SDA 2, SCL 3, IMU_INT 4, PPG_INT 5, PI_SHDN 6, IR_PWM 12, LED_PWM 13, HUB_RX 14 (Pi TXD), HUB_TX 15 '
    '(Pi RXD), TOUCH 17, I2S BCLK 18, LRCLK 19, MIC_SD 20, SPK_SD 21',
]


def setup():
    """Point the V1 generator at hardware/v2 (own lib, own output folders) and register the V2 boards."""
    lib = os.path.join(V2, 'lib')
    symlib.CUSTOM_LIB = os.path.join(lib, 'ana.kicad_sym')
    fpgen.LIB = os.path.join(lib, 'ana.pretty')
    pcbgen.ANA_FP = fpgen.LIB
    build.HW = V2
    export.HW, export.FAB, export.DOCS = V2, os.path.join(V2, 'fab'), os.path.join(V2, 'docs')
    if not os.environ.get('ANA_V2_KEEP_LIB'):          # parallel runs share the library written by the first
        symlib.build_custom_lib()
        fpgen.build()
    circuits.BOARDS.update(BOARDS)
    for name, st in SETUP.items():
        st = dict(st)
        st['outline'] = (lambda n: Pod()) if name == 'ana2-pod' else (lambda n: outline.Temple(TEMPLE_CFG))
        st['notes'] = NOTES
        build.EXTRA[name] = st
        build.SPECIAL[name] = POWER
        build.FIRST[name] = ['VBUS', 'USB_DP', 'USB_DN', 'CC1', 'CC2'] if name == 'ana2-pod' else []


if __name__ == '__main__':
    setup()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    for n in args or list(BOARDS):
        if '--export-only' not in sys.argv:
            failed = build.build(n)
            build.log(n, 'UNROUTED', sum(len(v) for v in failed.values()), failed)
        build.log(n, 'CHECKS', build.checks(n))
        if '--no-export' not in sys.argv:
            os.makedirs(os.path.join(export.FAB, n), exist_ok=True)
            export.gerbers(n)
            export.bom_cpl(n)
            export.drawings(n)
