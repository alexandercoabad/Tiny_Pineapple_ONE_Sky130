# Sample testbench for a Tiny Tapeout project

This is a sample testbench for a Tiny Tapeout project. It uses [cocotb](https://docs.cocotb.org/en/stable/) to drive the DUT and check the outputs.
See below to get started or for more information, check the [website](https://tinytapeout.com/hdl/testing/).

## Setting up

1. Edit [Makefile](Makefile) and modify `PROJECT_SOURCES` to point to your Verilog files.
2. Edit [tb.v](tb.v) and replace `tt_um_example` with your module name.

## How to run

To run the RTL simulation:

```sh
make -B
```

To run gatelevel simulation, first harden your project and copy `../runs/wokwi/results/final/verilog/gl/{your_module_name}.v` to `gate_level_netlist.v`.

Then run:

```sh
make -B GATES=yes
```

If you wish to save the waveform in VCD format instead of FST format, edit tb.v to use `$dumpfile("tb.vcd");` and then run:

```sh
make -B FST=
```

This will generate `tb.vcd` instead of `tb.fst`.

## Additional standalone testbenches

Seven extra testbenches cover the QSPI external-memory addition, the
reprogrammable boot ROM (self-test + demo/listen loop + bootloader),
the FLASH_MODE handoff to external flash, FLASH_PAGE bank-switched
flash execution, and a real ST7789 LCD driver built on top of it.
They're plain Icarus testbenches, not cocotb, so they don't run as
part of `make` above -- run them together with:

```sh
make standalone-tests
```

which is also its own step in `.github/workflows/test.yaml`, so it gates
CI same as the cocotb suite. Individually, that target runs:

```sh
# QSPI engine bit-level protocol check (write bitstream, byte order, CS behavior)
iverilog -g2012 -o /tmp/tb1.vvp ../src/qspi_shared_engine.v tb_qspi_engine.v && vvp /tmp/tb1.vvp

# mem.v integration test: bus signals driven directly, through the real engine + a behavioral SPI RAM model
iverilog -g2012 -I ../src -o /tmp/tb2.vvp ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_mem_ext.v && vvp /tmp/tb2.vvp

# Full core-level test: the real rv32i_core executing actual RV32I load/store
# instructions against the external window (mem_extmem_test.v swaps in a small
# test program in place of the boot ROM)
iverilog -g2012 -I ../src -o /tmp/tb3.vvp ../src/rv32i_core.v ../src/qspi_shared_engine.v mem_extmem_test.v spi_ram_model.v tb_core_ext.v && vvp /tmp/tb3.vvp

# Full top-level test: self-test fail/pass (with and without a simulated QSPI
# slave) and a full bootload-and-run, all against the real tt_um_pineapple_one
iverilog -g2012 -I ../src -o /tmp/tb4.vvp ../src/tt_um_pineapple_one.v ../src/rv32i_core.v ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_check.v && vvp /tmp/tb4.vvp

# FLASH_MODE handoff: bootload the 1-instruction stub, confirm execution
# actually continues from the correct external flash byte afterward
iverilog -g2012 -I ../src -o /tmp/tb5.vvp ../src/tt_um_pineapple_one.v ../src/rv32i_core.v ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_flash_handoff.v && vvp /tmp/tb5.vvp

# FLASH_PAGE bank-switching: a 4-page flash image (built by
# tools/build_flash_pagetest.py) exercises both switch_to() (a
# compile-time-constant page target) and switch_to_computed() (a
# runtime-decided one, via a self-loop), confirming GPIO_OUT visits
# every page's value in the right order
iverilog -g2012 -I ../src -o /tmp/tb6.vvp ../src/tt_um_pineapple_one.v ../src/rv32i_core.v ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_flash_paging.v && vvp /tmp/tb6.vvp

# Real ST7789 LCD driver (tools/build_st7789_flash_image.py), simulation-sized:
# reconstructs the actual bit-banged SPI byte stream from GPIO_OUT and checks
# it against st7789_expected_seq.hex (regenerate that if the driver's init
# sequence or FILL_COLOR/panel constants change)
iverilog -g2012 -I ../src -o /tmp/tb7.vvp ../src/tt_um_pineapple_one.v ../src/rv32i_core.v ../src/mem.v ../src/qspi_shared_engine.v spi_ram_model.v tb_st7789_driver.v && vvp /tmp/tb7.vvp
```

None of these has been checked against a real flash/PSRAM chip or a
vendor-accurate behavioral model -- see `docs/info.md`'s "Known
limitation" note.

All 11 cocotb tests (run via plain `make`) currently pass, including
`test_selftest_passes_with_pmod` and
`test_selftest_passes_again_after_soft_reset`, which drive a *Python*
behavioral QSPI slave rather than the Verilog one the standalone
testbenches above use.

## How to view the waveform file

Using GTKWave

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer

```sh
surfer tb.fst
```
