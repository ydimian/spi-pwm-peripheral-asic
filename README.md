![gds](https://github.com/ydimian/spi-pwm-peripheral-asic/workflows/gds/badge.svg)
![docs](https://github.com/ydimian/spi-pwm-peripheral-asic/workflows/docs/badge.svg)
![test](https://github.com/ydimian/spi-pwm-peripheral-asic/workflows/test/badge.svg)
![fpga](https://github.com/ydimian/spi-pwm-peripheral-asic/workflows/fpga/badge.svg)

# SPI-Controlled PWM Peripheral

A digital peripheral written in Verilog and hardened for [Tiny Tapeout](https://tinytapeout.com) on the Sky130 process. An external controller writes configuration registers over SPI; those registers drive 16 outputs, each of which can be a static level or a PWM waveform.

Built as part of the [UW-ASIC](https://uwasic.com) design team onboarding flow.

- **Target:** Tiny Tapeout, 1x1 tile, Sky130
- **Clock:** 10 MHz
- **Top module:** `tt_um_youdim_onboarding`
- **Verification:** cocotb + Icarus Verilog, run in CI on every push

## Architecture

```
SCLK ─┐
COPI ─┼─► spi_peripheral ──► 5 config registers ──► pwm_peripheral ──► uo_out[7:0]
nCS  ─┘   (sync + decode)                            (prescaler +      uio_out[7:0]
                                                      duty compare)
```

`spi_peripheral.v` handles the serial protocol and register decode. `pwm_peripheral.v` turns the register contents into output waveforms. `project.v` wires the two together and maps them onto the Tiny Tapeout pins.

## SPI interface

SPI mode 0 (CPOL = 0, CPHA = 0), 16 bits per transaction:

| Bits | Field |
|---|---|
| `[15]` | R/W — 1 = write, 0 = read |
| `[14:8]` | 7-bit register address |
| `[7:0]` | 8-bit data |

Data shifts in MSB-first on SCLK rising edges while `nCS` is low. The write commits on the `nCS` rising edge, and only if exactly 16 bits were received — a short or overlong transaction is discarded rather than partially applied. Reads and unmapped addresses are decoded and ignored.

SCLK, COPI, and `nCS` are asynchronous to the system clock, so each passes through a two-flop synchronizer before any edge detection happens. All protocol logic then runs entirely in the `clk` domain.

### Pinout

| Pin | Signal |
|---|---|
| `ui_in[0]` | SCLK |
| `ui_in[1]` | COPI |
| `ui_in[2]` | nCS |
| `uo_out[7:0]` | Outputs 0–7 |
| `uio_out[7:0]` | Outputs 8–15 |

### Register map

| Address | Register | Description |
|---|---|---|
| `0x00` | `en_reg_out_7_0` | Output value for `uo_out[7:0]` |
| `0x01` | `en_reg_out_15_8` | Output value for `uio_out[7:0]` |
| `0x02` | `en_reg_pwm_7_0` | Per-bit PWM enable for `uo_out[7:0]` |
| `0x03` | `en_reg_pwm_15_8` | Per-bit PWM enable for `uio_out[7:0]` |
| `0x04` | `pwm_duty_cycle` | Duty cycle, `0x00` (0%) to `0xFF` (100%) |

Each of the 16 output bits is independently switchable: if its PWM-enable bit is set, that output follows the PWM waveform; otherwise it holds the static value from `0x00`/`0x01`. All PWM-enabled bits share one duty cycle.

## PWM generation

A divide-by-13 prescaler feeds a 256-step counter, giving a PWM period of 13 × 256 = 3328 clock cycles — about 3.0 kHz from a 10 MHz clock. The output is high while the counter is below the duty-cycle value.

`0xFF` is special-cased to hold the output continuously high. Without it, a 256-step comparator can only reach 255/256 (99.6%), so the top code would not be a true 100%.

## Testing

```bash
cd test
make          # cocotb + Icarus Verilog
make GATES=yes  # gate-level netlist
```

Three cocotb tests:

- **`test_spi`** — valid writes to `0x00`/`0x01` and confirms the outputs change; writes to an unmapped address and a read transaction, and confirms the registers are left alone.
- **`test_pwm_freq`** — sets 50% duty, measures rising-edge to rising-edge, asserts 3000 Hz ±1%.
- **`test_pwm_duty`** — checks 0% stays low and 100% stays high across a full PWM period rather than at a single sample point, then measures 50% duty to within ±1%.

Note: Icarus Verilog's VPI cannot register an edge callback on one bit of a multi-bit signal, so `wait_for_bit_edge()` in `test/test.py` samples the bit once per clock and detects the transition in software instead.

## Repository layout

```
src/
  project.v           top module, pin mapping
  spi_peripheral.v    SPI receiver, synchronizers, register decode
  pwm_peripheral.v    prescaler and duty-cycle comparator
test/
  test.py             cocotb testbench
  tb.v                Verilog wrapper
docs/info.md          Tiny Tapeout datasheet
info.yaml             project configuration
```

## Attribution

Forked from the [UW-ASIC onboarding template](https://github.com/UW-ASIC/onboarding-start), which supplies the Tiny Tapeout harness, CI workflows, and `pwm_peripheral.v` (© Damir Gazizullin). The SPI peripheral, top-level integration, and the PWM frequency and duty-cycle tests are my own work.

Licensed under Apache 2.0.
