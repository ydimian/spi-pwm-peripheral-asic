# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.triggers import ClockCycles
from cocotb.types import Logic
from cocotb.types import LogicArray
from cocotb.utils import get_sim_time

# One PWM period is (12+1)*256 clk cycles @ 100 ns/cycle ~= 332.8 us.
# Give the edge-wait a healthy margin above that before declaring the
# signal "stuck" (used to detect the 0%/100% duty cycle edge cases,
# where no edge will ever occur).
PWM_TIMEOUT_NS = 400_000

async def await_half_sclk(dut):
    """Wait for the SCLK signal to go high or low."""
    start_time = cocotb.utils.get_sim_time(units="ns")
    while True:
        await ClockCycles(dut.clk, 1)
        # Wait for half of the SCLK period (10 us)
        if (start_time + 100*100*0.5) < cocotb.utils.get_sim_time(units="ns"):
            break
    return

def ui_in_logicarray(ncs, bit, sclk):
    """Setup the ui_in value as a LogicArray."""
    return LogicArray(f"00000{ncs}{bit}{sclk}")

async def send_spi_transaction(dut, r_w, address, data):
    """
    Send an SPI transaction with format:
    - 1 bit for Read/Write
    - 7 bits for address
    - 8 bits for data
    
    Parameters:
    - r_w: boolean, True for write, False for read
    - address: int, 7-bit address (0-127)
    - data: LogicArray or int, 8-bit data
    """
    # Convert data to int if it's a LogicArray
    if isinstance(data, LogicArray):
        data_int = int(data)
    else:
        data_int = data
    # Validate inputs
    if address < 0 or address > 127:
        raise ValueError("Address must be 7-bit (0-127)")
    if data_int < 0 or data_int > 255:
        raise ValueError("Data must be 8-bit (0-255)")
    # Combine RW and address into first byte
    first_byte = (int(r_w) << 7) | address
    # Start transaction - pull CS low
    sclk = 0
    ncs = 0
    bit = 0
    # Set initial state with CS low
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 1)
    # Send first byte (RW + Address)
    for i in range(8):
        bit = (first_byte >> (7-i)) & 0x1
        # SCLK low, set COPI
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        # SCLK high, keep COPI
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
    # Send second byte (Data)
    for i in range(8):
        bit = (data_int >> (7-i)) & 0x1
        # SCLK low, set COPI
        sclk = 0
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
        # SCLK high, keep COPI
        sclk = 1
        dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
        await await_half_sclk(dut)
    # End transaction - return CS high
    sclk = 0
    ncs = 1
    bit = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    await ClockCycles(dut.clk, 600)
    return ui_in_logicarray(ncs, bit, sclk)

async def configure_pwm(dut, duty_cycle, out_mask=0x01, pwm_mask=0x01):
    """Configure the PWM peripheral on uo_out[7:0].

    - out_mask:  written to register 0x00 (enable output on uo_out bits)
    - pwm_mask:  written to register 0x02 (enable PWM mode on those bits)
    - duty_cycle: written to register 0x04 (0-255 -> 0-100%)
    """
    await send_spi_transaction(dut, 1, 0x00, out_mask)
    await send_spi_transaction(dut, 1, 0x02, pwm_mask)
    await send_spi_transaction(dut, 1, 0x04, duty_cycle)


async def wait_for_bit_edge(dut, signal, bit, rising, timeout_ns):
    """Wait for a single bit of a (possibly multi-bit) signal to transition.

    Icarus Verilog's VPI can't watch a single bit of a multi-bit signal
    directly (RisingEdge/FallingEdge on e.g. uo_out[0] raises "cannot
    callback values on type code=37"), so instead we just check the bit's
    value once per clock cycle and see if it changed the way we want.

    Returns the sim time (ns) the edge happened, or None on timeout.
    """
    mask = 1 << bit
    prev = 1 if (int(signal.value) & mask) else 0
    start_time = get_sim_time(units="ns")

    while (get_sim_time(units="ns") - start_time) < timeout_ns:
        await ClockCycles(dut.clk, 1)
        cur = 1 if (int(signal.value) & mask) else 0

        if rising and prev == 0 and cur == 1:
            return get_sim_time(units="ns")
        if not rising and prev == 1 and cur == 0:
            return get_sim_time(units="ns")

        prev = cur

    return None


async def measure_pwm(dut, bit=0):
    """Measure one period of the PWM signal on uo_out[bit].

    Returns (freq_hz, duty_pct), or None if the signal never toggles
    within PWM_TIMEOUT_NS (i.e. it is stuck low/high, as expected at the
    0% / 100% duty cycle extremes).
    """
    signal = dut.uo_out

    t_rise1 = await wait_for_bit_edge(dut, signal, bit, True, PWM_TIMEOUT_NS)
    if t_rise1 is None:
        return None

    t_fall = await wait_for_bit_edge(dut, signal, bit, False, PWM_TIMEOUT_NS)
    if t_fall is None:
        return None

    t_rise2 = await wait_for_bit_edge(dut, signal, bit, True, PWM_TIMEOUT_NS)
    if t_rise2 is None:
        return None

    period_ns = t_rise2 - t_rise1
    high_ns = t_fall - t_rise1

    freq_hz = 1e9 / period_ns
    duty_pct = (high_ns / period_ns) * 100
    return freq_hz, duty_pct


async def reset_dut(dut):
    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = ui_in_logicarray(1, 0, 0)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_spi(dut):
    dut._log.info("Start SPI test")

    # Set the clock period to 100 ns (10 MHz)
    clock = Clock(dut.clk, 100, units="ns")
    cocotb.start_soon(clock.start())

    # Reset
    dut._log.info("Reset")
    dut.ena.value = 1
    ncs = 1
    bit = 0
    sclk = 0
    dut.ui_in.value = ui_in_logicarray(ncs, bit, sclk)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    dut._log.info("Test project behavior")
    dut._log.info("Write transaction, address 0x00, data 0xF0")
    ui_in_val = await send_spi_transaction(dut, 1, 0x00, 0xF0)  # Write transaction
    assert dut.uo_out.value == 0xF0, f"Expected 0xF0, got {dut.uo_out.value}"
    await ClockCycles(dut.clk, 1000) 

    dut._log.info("Write transaction, address 0x01, data 0xCC")
    ui_in_val = await send_spi_transaction(dut, 1, 0x01, 0xCC)  # Write transaction
    assert dut.uio_out.value == 0xCC, f"Expected 0xCC, got {dut.uio_out.value}"
    await ClockCycles(dut.clk, 100)

    dut._log.info("Write transaction, address 0x30 (invalid), data 0xAA")
    ui_in_val = await send_spi_transaction(dut, 1, 0x30, 0xAA)
    await ClockCycles(dut.clk, 100)

    dut._log.info("Read transaction (invalid), address 0x00, data 0xBE")
    ui_in_val = await send_spi_transaction(dut, 0, 0x30, 0xBE)
    assert dut.uo_out.value == 0xF0, f"Expected 0xF0, got {dut.uo_out.value}"
    await ClockCycles(dut.clk, 100)
    
    dut._log.info("Read transaction (invalid), address 0x41 (invalid), data 0xEF")
    ui_in_val = await send_spi_transaction(dut, 0, 0x41, 0xEF)
    await ClockCycles(dut.clk, 100)

    dut._log.info("Write transaction, address 0x02, data 0xFF")
    ui_in_val = await send_spi_transaction(dut, 1, 0x02, 0xFF)  # Write transaction
    await ClockCycles(dut.clk, 100)

    dut._log.info("Write transaction, address 0x04, data 0xCF")
    ui_in_val = await send_spi_transaction(dut, 1, 0x04, 0xCF)  # Write transaction
    await ClockCycles(dut.clk, 30000)

    dut._log.info("Write transaction, address 0x04, data 0xFF")
    ui_in_val = await send_spi_transaction(dut, 1, 0x04, 0xFF)  # Write transaction
    await ClockCycles(dut.clk, 30000)

    dut._log.info("Write transaction, address 0x04, data 0x00")
    ui_in_val = await send_spi_transaction(dut, 1, 0x04, 0x00)  # Write transaction
    await ClockCycles(dut.clk, 30000)

    dut._log.info("Write transaction, address 0x04, data 0x01")
    ui_in_val = await send_spi_transaction(dut, 1, 0x04, 0x01)  # Write transaction
    await ClockCycles(dut.clk, 30000)

    dut._log.info("SPI test completed successfully")

@cocotb.test()
async def test_pwm_freq(dut):
    dut._log.info("Start PWM Frequency test")
    await reset_dut(dut)

    # 50% duty cycle is guaranteed to toggle, so frequency is measurable.
    await configure_pwm(dut, duty_cycle=0x80)
    await ClockCycles(dut.clk, 100)

    result = await measure_pwm(dut, bit=0)
    assert result is not None, "PWM signal on uo_out[0] never toggled"
    freq_hz, _ = result

    dut._log.info(f"Measured PWM frequency: {freq_hz:.2f} Hz")
    assert 2970 <= freq_hz <= 3030, f"Expected 3000 Hz +/-1%, got {freq_hz:.2f} Hz"

    dut._log.info("PWM Frequency test completed successfully")


@cocotb.test()
async def test_pwm_duty(dut):
    dut._log.info("Start PWM Duty Cycle test")
    await reset_dut(dut)

    # 0% duty cycle: output should stay low (never toggles).
    await configure_pwm(dut, duty_cycle=0x00)
    await ClockCycles(dut.clk, 100)
    result = await measure_pwm(dut, bit=0)
    assert result is None, "Expected uo_out[0] to stay low at 0% duty cycle"
    assert dut.uo_out[0].value == 0

    # 100% duty cycle: output should stay high (never toggles).
    await send_spi_transaction(dut, 1, 0x04, 0xFF)
    await ClockCycles(dut.clk, 100)
    result = await measure_pwm(dut, bit=0)
    assert result is None, "Expected uo_out[0] to stay high at 100% duty cycle"
    assert dut.uo_out[0].value == 1

    # 50% duty cycle: should measure ~50% high time.
    await send_spi_transaction(dut, 1, 0x04, 0x80)
    await ClockCycles(dut.clk, 100)
    result = await measure_pwm(dut, bit=0)
    assert result is not None, "PWM signal on uo_out[0] never toggled at 50% duty cycle"
    _, duty_pct = result
    expected_pct = (0x80 / 256) * 100
    dut._log.info(f"Measured duty cycle: {duty_pct:.2f}% (expected ~{expected_pct:.2f}%)")
    assert abs(duty_pct - expected_pct) <= 1, (
        f"Expected ~{expected_pct:.2f}% duty cycle +/-1%, got {duty_pct:.2f}%"
    )

    dut._log.info("PWM Duty Cycle test completed successfully")
