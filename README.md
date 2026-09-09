![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Pineapple ONE (Tiny) — a from-scratch RV32I CPU for Tiny Tapeout (IHP shuttle)

Inspired by [Pineapple ONE](https://pineapple-one.github.io/), a 32-bit
RISC-V CPU built entirely out of discrete 7400-series logic chips (no
FPGA, no microcontroller). This project reimplements that "just basic
logic" spirit as a minimal, from-scratch RV32I core in synthesizable
Verilog, sized to fit a single Tiny Tapeout tile on the IHP `sg13g2`
shuttle, with an optional external QSPI memory expansion.

- [Read the project datasheet](docs/info.md) — how it works, how to test it, pinout
- [Original Pineapple ONE project](https://pineapple-one.github.io/)

**Scope note:** the original design has a 500 kHz clock, 512 kB program
memory, 512 kB RAM, and a VGA card — none of which fits in a TT tile
(~167×108 µm). This project keeps the RV32I instruction set and the
"no FPGA, just logic" philosophy, starting from a 256-byte address
space with no video output, plus a small external RAM window over the
[Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod) for
anyone who wants more headroom than the on-chip memory alone gives.

## Layout

<img width="1321" height="346" alt="Screenshot 2026-09-08 at 5 24 21 PM" src="https://github.com/user-attachments/assets/7eefc661-9beb-4ecb-97c1-ce1736b87f9d" />

https://gds-viewer.tinytapeout.com/?model=https://alexandercoabad.github.io/Tiny_Pineapple_ONE_IHP/tinytapeout.oas&pdk=ihp-sg13g2



## Status

- [x] Full RV32I base integer ISA (all loads/stores/branches/ALU ops;
      FENCE/ECALL/EBREAK decode as no-ops, no trap support yet)
- [x] Multi-cycle FSM core (fetch/fetch_wait/decode/exec/mem/mem_wait/
      writeback, 7 clock cycles per instruction -- the two `*_wait`
      states let the core stall on a `ready` handshake during external
      QSPI accesses; on-chip accesses see `ready` high immediately)
- [x] Memory: 176 B combinational boot ROM (self-test + demo/listen
      loop + bootloader) + 48 B flip-flop RAM (4 B always on-chip
      scratch + 44 B bootloader-loadable/executable window, the latter
      redirectable to external flash via `FLASH_MODE`) + 16 B external
      PSRAM window over the QSPI Pmod + memory-mapped LED output
      (`0xF0`) / switch input (`0xF4`) / `FLASH_MODE` (`0xF8`)
- [x] **Reprogrammable at runtime, no reflash/retapeout needed**: the
      boot ROM listens indefinitely for a bootload request over
      `ui_in[0:2]` (DATA/CLOCK/START) and runs whatever program it
      receives straight out of on-chip RAM -- see
      `tools/build_boot_rom.py` and docs/info.md's "Reprogrammability"
      section
- [x] **Hardened successfully on the real `ttihp26b` shuttle CI** at
      6x2 tiles, 59.3% utilization, clean DRC/precheck/gl_test (see
      `.github/workflows/gds.yaml` run history)
- [x] Five test suites (see "Testing locally" below): on-chip cocotb
      regression (self-test, demo counter, full bootload-and-run),
      standalone self-test/bootload Icarus testbench, QSPI engine
      bit-level protocol, external-window integration via direct bus
      driving, and full CPU-driven external load/store — all wired
      into CI (two separate steps), all gating the build. **Known
      issue:** 2 of the 11 cocotb tests currently fail
      (`test_selftest_passes_with_pmod`,
      `test_selftest_passes_again_after_soft_reset`) -- traced to a
      one-clock-cycle sampling lag in `test.py`'s Python QSPI slave
      coroutine, not the design itself; see `test/README.md`.
- [ ] Validate the external memory path against a real flash/PSRAM chip
      or a vendor-accurate behavioral model (currently only tested
      against a hand-written behavioral model, `test/spi_ram_model.v`)
- [ ] Widen the address bus beyond 8 bits to actually reach the QSPI
      Pmod's real multi-megabyte capacity (current external window is
      a fixed 64 bytes within the existing 256-byte address space)

## Repo layout

```
src/
  rv32i_defs.vh          opcode/state constants
  rv32i_core.v            the CPU: regfile, ALU, decode, control FSM
  mem.v                   boot ROM + RAM + external QSPI windows + LED/switch/FLASH_MODE registers
  boot_rom_body.vh        generated boot ROM bytes, `include`d by mem.v -- don't hand-edit
  qspi_shared_engine.v    single-line SPI master shared between flash (CS0) and PSRAM (CS1)
  tt_um_pineapple_one.v   Tiny Tapeout top-level pin mapping (incl. QSPI Pmod pins on uio)
  config.json             LibreLane flow config (clock period, density, etc.)
tools/
  build_boot_rom.py       assembles the boot ROM (self-test + demo/listen loop + bootloader)
                          into src/boot_rom_body.vh -- run this and re-copy its output if you
                          change what the boot ROM itself does
test/
  tb.v, test.py           cocotb testbench: self-test pass/fail, demo counter, full bootload-and-run
  tb_check.v              standalone: same three scenarios as a single self-contained Icarus testbench
  tb_qspi_engine.v        standalone: QSPI engine bit-level protocol + byte-order check
  spi_ram_model.v         behavioral single-line SPI RAM model, for the tests below
  tb_mem_ext.v            standalone: external window via direct bus driving + real engine + spi_ram_model
  mem_extmem_test.v       copy of mem.v with a test program in place of the boot ROM
  tb_core_ext.v           standalone: the real CPU running that test program against the external window
info.yaml                 Tiny Tapeout project metadata (title, pinout, tiles...)
docs/info.md              project datasheet shown on the Tiny Tapeout site
```

## How the boot ROM works

On every reset, the boot ROM self-tests the external QSPI PSRAM
(result latched into `uo_out[7]`), then loops forever incrementing a
demo counter into `uo_out[3:0]` while listening on `ui_in[0:2]` for a
bootload request -- assert START and stream over a new program at any
time, and the chip runs it immediately out of RAM, no reflash or
retapeout needed. Full protocol, address map, pinout, and the
`FLASH_MODE` opt-in for booting straight from external flash are in
[docs/info.md](docs/info.md).


## Testing locally

```
cd test
pip install -r requirements.txt
make                    # cocotb: self-test, demo counter, full bootload-and-run
make standalone-tests   # QSPI engine + external-window + full-CPU + self-test/bootload tests
```

Both targets are also run automatically by `.github/workflows/test.yaml`
on every push, and both must pass for that workflow to go green.

## External memory over QSPI

`uio[0:7]` are wired to the
[Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod): a
single-line SPI master (`src/qspi_shared_engine.v`) shared between an
external flash chip (CS0) and PSRAM (CS1). PSRAM backs a 16-byte
external RAM window at `0xE0-0xEF` in `mem.v`, always -- this is also
what the boot ROM's own power-on self-test probes. Flash only gets
selected once a *bootloaded* program writes `FLASH_MODE` (`0xF8`),
which redirects the 44-byte loadable window at `0xB4-0xDF` from
on-chip RAM to flash from then on; the boot ROM itself never asserts
CS0. The core's FSM stalls on a `ready` handshake while an external
access is in flight, and picks up exactly where it left off once the
SPI transaction completes — on-chip accesses are unaffected and still
get an immediate response. See `docs/info.md` for the full address map
and pinout.

This currently only uses a fixed handful of bytes of the Pmod's actual
multi-megabyte capacity, since the CPU's address bus is still 8 bits
wide. Reaching the Pmod's real capacity means widening `pc`/`mem_addr`
and the jump/branch immediate math throughout `rv32i_core.v` — a bigger
follow-up change, not yet done here.

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that aims to make it easier and cheaper than ever to get your digital and analog designs manufactured on a real chip.

To learn more and get started, visit https://tinytapeout.com.

## Resources

- [FAQ](https://tinytapeout.com/faq/)
- [Digital design lessons](https://tinytapeout.com/digital_design/)
- [Learn how semiconductors work](https://tinytapeout.com/siliwiz/)
- [Join the community](https://tinytapeout.com/discord)
- [Build your design locally](https://www.tinytapeout.com/guides/local-hardening/)

## What next?

- [Submit your design to the next shuttle](https://app.tinytapeout.com/).
- Share your project on your social network of choice:
  - LinkedIn [#tinytapeout](https://www.linkedin.com/search/results/content/?keywords=%23tinytapeout) [@TinyTapeout](https://www.linkedin.com/company/100708654/)
  - Mastodon [#tinytapeout](https://chaos.social/tags/tinytapeout) [@matthewvenn](https://chaos.social/@matthewvenn)
  - X (formerly Twitter) [#tinytapeout](https://twitter.com/hashtag/tinytapeout) [@tinytapeout](https://twitter.com/tinytapeout)
  - Bluesky [@tinytapeout.com](https://bsky.app/profile/tinytapeout.com)
