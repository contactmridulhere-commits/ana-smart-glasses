# ANA Smart Glasses · V1

**Project Spectra.** AI-native AR glasses, built one version at a time. This repository holds the V1 firmware: an ESP32 heads-up display with vitals sensing that I wore as a daily driver.

**Build video:** [Watch the short](https://www.youtube.com/shorts/zZ5Lkahvl9M)

<p align="center">
  <img src="Screenshot%202026-02-12%20115845.png" width="560" alt="V1 wiring schematic: ESP32 with OLED, MAX30102, DHT11, TTP223 touch sensor and Bluetooth">
</p>

## What V1 does

- **Heads-up display.** A 128 × 64 SSD1306-class OLED driven over SPI, rendered upside down (`setRotation(2)`) so it reads correctly through the glasses' optic.
- **Heart rate and SpO₂.** A MAX30102 sampled at 400 samples/s. Finger detection, low-pass and high-pass filtering, a differentiator-based beat detector, and SpO₂ from the red/IR ratio through a quadratic calibration curve. Readings are averaged over 5 beats before they are shown.
- **Temperature.** A DHT11 on GPIO 4.
- **Time and date from your phone.** Bluetooth Classic serial (`SerialBT`). Send `T: dd/mm/yyyy hh:mm` once and the glasses keep time from there.

## Hardware

| Part | Role |
|---|---|
| ESP32 devkit | Controller, Bluetooth |
| 128 × 64 OLED (SSD1306 driver, SPI) | Display, seen through the optic |
| MAX30102 | Heart rate and SpO₂ |
| DHT11 | Temperature |

**OLED wiring (SPI):**

| Signal | GPIO |
|---|---|
| MOSI | 23 |
| CLK | 18 |
| DC | 16 |
| CS | 5 |
| RST | 17 |

The MAX30102 uses the default I²C pins (SDA 21, SCL 22). The DHT11 data pin goes to GPIO 4.

## Temple PCBs (V1.1 hardware)

The hand-wired modules are now two custom PCBs shaped like the glasses' temples (straight, then bending down 30° behind the ear). Design files, fabrication outputs and the full write-up are in [`hardware/`](hardware/README.md).

| Board | Temple | What is on it |
|---|---|---|
| `ana-main` | right (display side) | ESP32-U4WDH chip, chip antenna, MAX30102 (faces the skin), SHT40, TTP223 touch, side LED, OLED pads, bring-up pads |
| `ana-power` | left | USB-C on the bottom edge (charging and flashing), CP2102N USB-serial with auto-reset, TP4056 charger, DW01A + FS8205A protection, power path, power and BT-headset switches, side LED, battery pads |

On the temple PCBs the ESP32's in-package flash uses GPIO16/17, and every display and sensor line leaves the chip on the side facing the hinge, so the pins moved. `prototype_toy_.ino` has `#define ANA_TEMPLE_PCB 1` for the PCBs and `0` for the original wiring above.

| Signal | GPIO on the PCB |
|---|---|
| OLED MOSI / CLK / CS | 23 / 4 / 15 |
| OLED DC / RST | 26 / 27 |
| I²C SDA / SCL (MAX30102 + SHT40) | 21 / 22 (unchanged) |
| MAX30102 interrupt | 5 |
| Touch (TTP223 output, also switches the LEDs) | 14 |
| Battery voltage (VSYS / 2; above 4.4 V means USB is plugged in) | 33 |
| Charging (TP4056 status) | 13 |

## Build and flash

1. Install the ESP32 board package in the Arduino IDE.
2. Install the libraries: `Adafruit SSD1306`, `Adafruit GFX`, `MAX3010x` by eepj, and `Adafruit SHT4x` (temple PCBs) or `DHT sensor library` by Adafruit (original wiring).
3. Open `prototype_toy_.ino` with `filters.h` in the same folder, select your ESP32 board and upload. On the temple PCBs, slide the POWER switch on and plug USB-C into the left temple: the CP2102N and auto-reset circuit on the left temple flash the ESP32 over the frame wires, like a devkit.
4. Pair your phone with the Bluetooth device and send the time string once, for example `T: 27/09/2026 18:45`.

## Where it goes next: V2

V2 is in development. It splits the work across two brains: a Raspberry Pi CM5 for vision and on-device AI, and an ESP32-S3 for sensors and power. It swaps the OLED for a reflective-waveguide see-through display. The assistant runs entirely on the frame, with a quantised Phi-3-mini, whisper.cpp speech recognition, Piper TTS and retrieval memory. It also adds continuous two-point ECG through ear-tip electrodes. No cloud anywhere.

More builds: [portfolio](https://portfolio-mridul-six.vercel.app) · [YouTube](https://www.youtube.com/@mridulsharma-martian)

## License

MIT. See [LICENSE](LICENSE).
