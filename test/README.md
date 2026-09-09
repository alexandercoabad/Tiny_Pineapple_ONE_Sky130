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

Four extra testbenches cover the QSPI external-memory addition and the
reprogrammable boot ROM (self-test + demo/listen loop + bootloader).
They're plain Icarus testbenches, not cocotb, so they don't run as part
of `make` above -- run them together with:

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
```

None of these has been checked against a real flash/PSRAM chip or a
vendor-accurate behavioral model -- see `docs/info.md`'s "Known
limitation" note.

Separately, `test.py` (run via plain `make`) includes two cocotb tests
(`test_selftest_passes_with_pmod`, `test_selftest_passes_again_after_soft_reset`)
that drive a *Python* behavioral QSPI slave instead of the Verilog one
above; as of this writing those two currently fail in simulation even
though the equivalent Verilog-driven scenario in `tb_check.v` passes --
this looks like a one-clock-cycle sampling lag specific to that Python
coroutine's `RisingEdge`-polled bit-bang (it reacts to an SCK edge one
system-clock cycle later than the Verilog `negedge sck`-triggered model
does), not a bug in the design itself. Worth fixing in `test.py`, but
left as-is here since the RTL-level correctness is already covered by
`tb_check.v` and the other three tests above.

## How to view the waveform file

Using GTKWave

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer

```sh
surfer tb.fst
```
