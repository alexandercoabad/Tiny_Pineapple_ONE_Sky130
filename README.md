![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Pineapple ONE (Tiny) — a from-scratch RV32I CPU for Tiny Tapeout (Sky130 shuttle)

Inspired by [Pineapple ONE](https://pineapple-one.github.io/), a 32-bit
RISC-V CPU built entirely out of discrete 7400-series logic chips (no
FPGA, no microcontroller). This project reimplements that "just basic
logic" spirit as a minimal, from-scratch RV32I core in synthesizable
Verilog, sized to fit a single Tiny Tapeout tile on the Sky130 shuttle,
with an optional external QSPI memory expansion.

- [Read the project datasheet](docs/info.md) — how it works, how to test it, pinout
- [Original Pineapple ONE project](https://pineapple-one.github.io/)

**Scope note:** the original design has a 500 kHz clock, 512 kB program
memory, 512 kB RAM, and a VGA card — none of which fits in a TT tile
(~167×108 µm). This project keeps the RV32I instruction set and the
"no FPGA, just logic" philosophy, starting from a 256-byte address
space with no native video output, plus a small external RAM window
over the [Tiny Tapeout QSPI Pmod](https://github.com/mole99/qspi-pmod)
for anyone who wants more headroom than the on-chip memory alone
gives. A bit-banged SPI LCD driver (ST7789) now runs over that same
Pmod's GPIO, and a PS/2 keyboard reader is next to it feeding the same
external address space — see "Status" below.

## Layout

<img width="1305" height="297" alt="Screenshot 2026-09-09 at 7 54 46 PM" src="https://github.com/user-attachments/assets/6945c66d-a44e-43ee-a7ae-b8bbbedcb1e7" />


https://gds-viewer.tinytapeout.com/?model=https://alexandercoabad.github.io/Tiny_Pineapple_ONE_Sky130/tinytapeout.oas&pdk=sky130A



## Status

- [x] Full RV32I base integer ISA (all loads/stores/branches/ALU ops;
      FENCE/ECALL/EBREAK decode as no-ops, no trap support yet)
- [x] Multi-cycle FSM core (fetch/fetch_wait/decode/exec/mem/mem_wait/
      writeback, 7 clock cycles per instruction -- the two `*_wait`
      states let the core stall on a `ready` handshake during external
      QSPI accesses; on-chip accesses see `ready` high immediately)
- [x] `tools/asm_pineapple.py` wraps every opcode the core above
      actually implements, not just the handful each script originally
      needed -- `SUB`, register-register `SLL`/`SRL`/`SRA`, `SLT`/
      `SLTU`, `SLTI`/`SLTIU`, `XORI`, `SRAI`, `LB`/`LH`/`LHU`, `SH`,
      `BGE`/`BLTU`/`BGEU`, and `LUI`/`AUIPC` are all now available to
      every script that builds on `Asm`/`PagedAsm`, confirmed against
      the real core (not just on paper) in `test/tb_alu_test.v`
- [x] Memory: 176 B combinational boot ROM (self-test + demo/listen
      loop + bootloader) + 48 B flip-flop RAM (4 B always on-chip
      scratch + 44 B bootloader-loadable/executable window, the latter
      redirectable to external flash via `FLASH_MODE`, and bank-
      switchable across that flash chip via `FLASH_PAGE`) + 16 B
      external PSRAM window over the QSPI Pmod + memory-mapped LED
      output (`0xF0`) / switch input (`0xF4`) / `FLASH_MODE` (`0xF8`) /
      `FLASH_PAGE` (`0xFC`)
- [x] **Reprogrammable at runtime, no reflash/retapeout needed**: the
      boot ROM listens indefinitely for a bootload request over
      `ui_in[0:2]` (DATA/CLOCK/START) and runs whatever program it
      receives straight out of on-chip RAM -- see
      `tools/build_boot_rom.py` and docs/info.md's "Reprogrammability"
      section
- [x] **Bank-switched flash execution**: a bootloaded 1-instruction
      stub can hand off into external flash (`FLASH_MODE`), and
      `FLASH_PAGE` lets a running program page through a flash image
      far larger than the 44-byte on-chip execute window -- see
      docs/info.md's "Bank-switched flash execution" section and
      `tools/asm_pineapple.py`'s `PagedAsm`
- [x] **Real ST7789 LCD driver** (`tools/build_st7789_flash_image.py`),
      bit-banged SPI over `GPIO_OUT` (no dedicated SPI peripheral),
      built entirely on `PagedAsm` -- init sequence + fill loop
      verified byte-for-byte in `test/tb_st7789_driver.v`
- [x] **PS/2 keyboard reader**, bit-banged over two free `ui_in` pins:
      Step 1 (`tools/build_ps2_reader.py`) reads raw scancodes onto
      `GPIO_OUT`; Step 2 (`tools/build_ps2_ascii.py`) adds scancode-to-
      ASCII translation, including make/break-code and extended-code
      (`0xE0`) handling
- [x] **Hardened successfully on the real Sky130 shuttle CI** at 6x2
      tiles, 53.8% routing utilization, 12,548 cells (excluding
      fill/tap), clean DRC/precheck (15/15 checks) and gate-level tests
      (11/11) -- see `.github/workflows/gds.yaml` run history
- [x] Eleven test suites (see "Testing locally" below): on-chip cocotb
      regression (self-test, demo counter, full bootload-and-run) plus
      ten standalone Icarus testbenches -- QSPI engine bit-level
      protocol, external-window integration via direct bus driving,
      full CPU-driven external load/store, self-test/bootload,
      `FLASH_MODE` handoff to external flash, `FLASH_PAGE`
      bank-switched flash execution, the ST7789 LCD driver, the PS/2
      reader (raw scancodes, then scancode-to-ASCII translation), and
      an instruction-encoding check for every opcode
      `tools/asm_pineapple.py` wraps -- all wired into CI, all gating
      the build, all 11 cocotb tests + all 10 standalone tests
      currently passing
- [ ] **Step 3, in progress:** a bitmap font + terminal renderer tying
      the PS/2 reader to the ST7789 driver, so keystrokes actually
      appear on screen -- the biggest piece yet; no interrupts on this
      core, so keyboard polling and display draws have to be
      interleaved cooperatively by the same program, not preempted
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
  mem.v                   boot ROM + RAM + external QSPI windows + LED/switch/FLASH_MODE/FLASH_PAGE registers
  boot_rom_body.vh        generated boot ROM bytes, `include`d by mem.v -- don't hand-edit
  qspi_shared_engine.v    single-line SPI master shared between flash (CS0) and PSRAM (CS1)
  tt_um_pineapple_one.v   Tiny Tapeout top-level pin mapping (incl. QSPI Pmod pins on uio)
  config.json             LibreLane flow config (clock period, density, etc.)
tools/
  build_boot_rom.py       assembles the boot ROM (self-test + demo/listen loop + bootloader)
                          into src/boot_rom_body.vh -- run this and re-copy its output if you
                          change what the boot ROM itself does
  asm_pineapple.py         RV32I assembler (Asm, wrapping every opcode the core implements) +
                          PagedAsm, the FLASH_PAGE bank-switching helper every *_flash_image.py
                          builder below shares
  build_flash_handoff_stub.py  1-instruction stub bootloaded over ui_in to hand off into flash
  build_flash_canary.py    tiny known-good flash program, for confirming the handoff mechanism alone
  build_flash_pagetest.py  synthetic 4-page program exercising switch_to()/switch_to_computed()
  build_st7789_flash_image.py  real ST7789 LCD driver, PagedAsm-based, bank-switched
  build_ps2_reader.py      PS/2 keyboard reader, Step 1: raw scancode -> GPIO_OUT
  build_ps2_ascii.py       PS/2 keyboard reader, Step 2: scancode -> ASCII translation
  build_alu_test.py        standalone program exercising every asm_pineapple.py opcode, for tb_alu_test.v
test/
  tb.v, test.py           cocotb testbench: self-test pass/fail, demo counter, full bootload-and-run
  tb_check.v              standalone: same three scenarios as a single self-contained Icarus testbench
  tb_qspi_engine.v        standalone: QSPI engine bit-level protocol + byte-order check
  spi_ram_model.v         behavioral single-line SPI RAM model (flash CS0 and PSRAM CS1), for the tests below
  tb_mem_ext.v            standalone: external window via direct bus driving + real engine + spi_ram_model
  mem_extmem_test.v       copy of mem.v with a test program in place of the boot ROM
  tb_core_ext.v           standalone: the real CPU running that test program against the external window
  tb_flash_handoff.v      standalone: bootloaded stub hands off into a small flash-resident program
  tb_flash_paging.v       standalone: FLASH_PAGE bank-switching across a synthetic multi-page program
  tb_st7789_driver.v      standalone: the real ST7789 driver, byte stream reconstructed and checked
  tb_ps2_reader.v         standalone: raw PS/2 frames -> GPIO_OUT (Step 1)
  tb_ps2_ascii.v          standalone: PS/2 frames -> translated ASCII on GPIO_OUT (Step 2)
  alu_test_mem.v          minimal flat ROM+RAM harness (not mem.v) used only by tb_alu_test.v
  tb_alu_test.v           standalone: every asm_pineapple.py opcode, run through the real core, checked
                          against hand-computed register values
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
`FLASH_MODE`/`FLASH_PAGE` opt-in for booting from (and paging through)
external flash are in [docs/info.md](docs/info.md).


## Testing locally

```
cd test
pip install -r requirements.txt
make                    # cocotb: self-test, demo counter, full bootload-and-run
make standalone-tests   # QSPI engine, external-window, full-CPU, self-test/bootload, FLASH_MODE
                         # handoff, FLASH_PAGE bank-switching, ST7789 driver, PS/2 reader/ASCII,
                         # and asm_pineapple.py instruction-encoding tests
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
CS0. From there, `FLASH_PAGE` (`0xFC`) lets a running program bank-
switch through a much larger flash image 44 bytes at a time -- see
`tools/asm_pineapple.py`'s `PagedAsm`, which the ST7789 driver and PS/2
reader both build on. The core's FSM stalls on a `ready` handshake
while an external access is in flight, and picks up exactly where it
left off once the SPI transaction completes — on-chip accesses are
unaffected and still get an immediate response. See `docs/info.md` for
the full address map and pinout.

This currently only uses a fixed handful of bytes of the Pmod's actual
multi-megabyte capacity at any one time (`FLASH_PAGE` reaches further
into flash by paging, not by widening the address space itself), since
the CPU's address bus is still 8 bits wide. Reaching the Pmod's real
capacity as a flat address space means widening `pc`/`mem_addr` and the
jump/branch immediate math throughout `rv32i_core.v` — a bigger
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