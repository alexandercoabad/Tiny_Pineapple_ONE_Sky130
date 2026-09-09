#!/usr/bin/env python3
"""
ST7789 driver for Pineapple-TT, meant to be pre-programmed onto the
external flash chip on the QSPI Pmod (CS0) and reached via
build_flash_handoff_stub.py's tiny bootloaded stub, which sets
FLASH_MODE and lets the CPU's own next fetch pick this up from flash --
see that file's docstring for why the RAM-bootload path (44 bytes) is
nowhere near big enough for this, and mem.v's "Reprogrammability"
section for the FLASH_MODE mechanism itself.

Byte 0 of the image this script writes is a dead NOP -- see
build_flash_handoff_stub.py's "Why flash byte 0 is dead" docstring
section for the full explanation. Short version: the handoff stub is
one instruction (SW FLASH_MODE), and PC+4 after it is chip address
LOAD_BASE+4, not LOAD_BASE -- so this image's real first instruction
(byte 4 onward) is what actually runs first, and byte 0 is reserved as
an explicit ADDI x0,x0,0 rather than left as an unreachable real
instruction.

Hardware assumptions (confirm before flashing):
  - Panel wiring, bit-banged over GPIO_OUT (0xF0) since Pineapple has
    no dedicated SPI peripheral (unlike AgilA8's CS2 engine):
      GPIO_OUT[0] = SCK
      GPIO_OUT[1] = MOSI
      GPIO_OUT[2] = DC
      GPIO_OUT[3] = CS -- set once at the very start and held low for
                   the program's entire lifetime (single-device bus,
                   no other peripheral to arbitrate with), NOT pulsed
                   per byte or per transaction.
  - RST tied high externally -- this program never drives a reset
    pin, relying on SWRESET over SPI instead.
  - Flash itself is read-only from this design's memory port (real
    NOR flash needs an erase/program sequence a plain SB can't do --
    see mem.v's header), so this program can never modify itself or
    write scratch data into flash; any scratch state it needs lives
    in registers only (no on-chip RAM is reachable once FLASH_MODE
    has redirected 0xB4-0xDF away from it).

What this does, in order:
  1. Sets CS low (once, held for the rest of the program).
  2. SWRESET (0x01) + delay.
  3. SLPOUT (0x11) + delay.
  4. COLMOD (0x3A) = 0x05 (16bpp / RGB565).
  5. MADCTL (0x36) = 0x00 (default orientation/color order -- adjust
     MADCTL_VALUE below if your panel needs mirroring/rotation).
  6. CASET (0x2A): columns 0..(PANEL_WIDTH-1).
  7. RASET (0x2B): rows 0..(PANEL_HEIGHT-1).
  8. DISPON (0x29).
  9. RAMWR (0x2C), then streams FILL_COLOR (RGB565) across the whole
     panel using a single 32-bit down-counter -- no nested-loop
     trick needed the way AgilA8's 8-bit registers required; a plain
     32-bit register comfortably counts PANEL_WIDTH*PANEL_HEIGHT
     pixels directly.
  10. Infinite loop (nothing to fall through to; this ISA has no
      HALT instruction the way AgilA8's does).

NOT included, to keep this focused on a working bring-up rather than
covering every panel variant: INVON (some panels need this for
non-inverted colors -- cheap to add, one command byte, no parameters,
if colors look wrong) and per-pixel/partial-window updates (this
always fills the entire configured window). Flash isn't a tight
budget here, so extending this is mostly a matter of adding more
SEND_BYTE call sites, not fighting for space the way the 44-byte
bootload path would.
"""
import sys
sys.path.insert(0, '.')
from asm_pineapple import Asm, words_to_bytes

GPIO_OUT = 0xF0

PANEL_WIDTH = 240
PANEL_HEIGHT = 240
MADCTL_VALUE = 0x00
FILL_COLOR = 0xF800   # RGB565; e.g. 0xF800=red, 0x07E0=green, 0x001F=blue
DELAY_COUNT = 2_000_000  # tune by eye; see DELAY's docstring below


def load_const32(a, rd, value, scratch):
    """Loads an arbitrary 32-bit constant into rd using scratch as a
    temporary: ADDI rd,x0,low12 ; ADDI scratch,x0,high20's-low12-chunk
    ; SLLI scratch,scratch,12 ; ADD rd,rd,scratch. Values used in this
    file (a pixel count and a delay count) fit in 32 bits with room to
    spare, so this two-chunk split (not a full RV32I LUI+ADDI general
    encoding) is enough -- it's assumed here that value < 2**24."""
    assert 0 <= value < (1 << 24), f"load_const32 only handles <2**24 here, got {value}"
    lo = value & 0xFFF
    hi = value >> 12
    a.ADDI(rd, 0, lo)
    if hi:
        a.ADDI(scratch, 0, hi)
        a.SLLI(scratch, scratch, 12)
        a.ADD(rd, rd, scratch)


a = Asm()

# --- byte 0 is dead, never fetched -- see
# tools/build_flash_handoff_stub.py's "Why flash byte 0 is dead" for
# the full explanation. Short version: the 1-instruction handoff stub
# that redirects LOAD_BASE-0xDF to this flash image lands its own
# PC+4 on flash offset 4, not offset 0, so this reserves offset 0 as
# an explicit NOP rather than leaving a real instruction sitting
# somewhere nothing ever fetches it from. ---
a.ADDI(0, 0, 0)           # dead slot, NOP (writes to x0, hardwired 0)

# --- CS low, held for the rest of the program ---
a.ADDI(2, 0, 0)          # x2 = 0 (SCK=0,MOSI=0,DC=0,CS=0 all at once)
a.SW(2, 0, GPIO_OUT)

# ---------------------------------------------------------------------
# SEND_BYTE(x4=byte to send, x9=dc bit value already shifted into
# position (0 for command, 1<<2 for data)): bit-bangs 8 bits MSB-first
# over GPIO_OUT[1]=MOSI, toggling GPIO_OUT[0]=SCK, sampling intended on
# SCK's rising edge (standard SPI mode 0). Returns via x1 (ra).
#
# Every GPIO_OUT write in this loop is a full 8-bit store, not a
# masked update -- CS (bit 3) is therefore forced to 0 on every single
# write here, which is exactly the "CS held low" behavior this program
# wants, not a bug to route around.
# ---------------------------------------------------------------------
a.label("SEND_BYTE")
a.ADDI(3, 0, 8)                 # x3 = bit counter
a.label("SB_LOOP")
a.BEQ(3, 0, "SB_DONE")
a.SRLI(2, 4, 7)                 # x2 = MSB of x4 (0 or 1)
a.ANDI(2, 2, 1)
a.SLLI(2, 2, 1)                 # move into MOSI's bit position (bit 1)
a.OR(2, 2, 9)                   # combine with DC
a.SW(2, 0, GPIO_OUT)            # SCK=0, MOSI/DC set up
a.ADDI(8, 2, 1)                 # x8 = x2 with bit0 (SCK) set -- safe as
                                 # a plain +1 since x2's bit0 is always 0
a.SW(8, 0, GPIO_OUT)            # SCK=1 (rising edge -- sample point)
a.SW(2, 0, GPIO_OUT)            # SCK=0 again
a.SLLI(4, 4, 1)                 # shift byte left for the next bit
a.ADDI(3, 3, -1)
a.JAL(0, "SB_LOOP")
a.label("SB_DONE")
a.JALR(0, 1, 0)                 # return via x1 (ra)

# ---------------------------------------------------------------------
# DELAY: busy-wait, a single 32-bit down-counter (no 8-bit register
# limit the way AgilA8 has). Tuned by eye rather than against a
# datasheet's exact timing spec -- raise DELAY_COUNT above (or call
# DELAY more than the two places below do) if a panel needs longer
# than SWRESET/SLPOUT's ~120ms spec and this undershoots it.
# ---------------------------------------------------------------------
a.label("DELAY")
load_const32(a, 2, DELAY_COUNT, scratch=8)
a.label("DELAY_LOOP")
a.BEQ(2, 0, "DELAY_DONE")
a.ADDI(2, 2, -1)
a.JAL(0, "DELAY_LOOP")
a.label("DELAY_DONE")
a.JALR(0, 1, 0)

# ---------------------------------------------------------------------
# Main sequence
# ---------------------------------------------------------------------
a.label("MAIN")

a.ADDI(4, 0, 0x01)              # SWRESET
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.JAL(1, "DELAY")

a.ADDI(4, 0, 0x11)              # SLPOUT
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.JAL(1, "DELAY")

a.ADDI(4, 0, 0x3A)              # COLMOD
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, 0x05)
a.ADDI(9, 0, 1 << 2)
a.JAL(1, "SEND_BYTE")

a.ADDI(4, 0, 0x36)              # MADCTL
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, MADCTL_VALUE)
a.ADDI(9, 0, 1 << 2)
a.JAL(1, "SEND_BYTE")

a.ADDI(4, 0, 0x2A)              # CASET
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.ADDI(9, 0, 1 << 2)
a.ADDI(4, 0, 0 >> 8)                       # xs hi
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, 0 & 0xFF)                     # xs lo
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, (PANEL_WIDTH - 1) >> 8)       # xe hi
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, (PANEL_WIDTH - 1) & 0xFF)     # xe lo
a.JAL(1, "SEND_BYTE")

a.ADDI(4, 0, 0x2B)              # RASET
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")
a.ADDI(9, 0, 1 << 2)
a.ADDI(4, 0, 0 >> 8)                       # ys hi
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, 0 & 0xFF)                     # ys lo
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, (PANEL_HEIGHT - 1) >> 8)      # ye hi
a.JAL(1, "SEND_BYTE")
a.ADDI(4, 0, (PANEL_HEIGHT - 1) & 0xFF)    # ye lo
a.JAL(1, "SEND_BYTE")

a.ADDI(4, 0, 0x29)              # DISPON
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")

a.ADDI(4, 0, 0x2C)              # RAMWR
a.ADDI(9, 0, 0)
a.JAL(1, "SEND_BYTE")

# --- fill the whole configured window with FILL_COLOR ---
a.ADDI(6, 0, FILL_COLOR >> 8)     # x6 = color hi (persists across
a.ADDI(7, 0, FILL_COLOR & 0xFF)   # x7 = color lo    SEND_BYTE calls)
load_const32(a, 5, PANEL_WIDTH * PANEL_HEIGHT, scratch=8)  # x5 = pixel count
a.label("FILL_LOOP")
a.BEQ(5, 0, "FILL_DONE")
a.ADD(4, 6, 0)                  # x4 = color hi
a.ADDI(9, 0, 1 << 2)
a.JAL(1, "SEND_BYTE")
a.ADD(4, 7, 0)                  # x4 = color lo
a.ADDI(9, 0, 1 << 2)
a.JAL(1, "SEND_BYTE")
a.ADDI(5, 5, -1)
a.JAL(0, "FILL_LOOP")
a.label("FILL_DONE")

a.label("SPIN")
a.JAL(0, "SPIN")                # nothing to fall through to -- this ISA
                                  # has no HALT, so just park here

words = a.finalize()
prog_bytes = words_to_bytes(words)
print(f"ST7789 flash image: {len(words)} instructions, {len(prog_bytes)} bytes")

for name, addr in sorted(a.labels.items(), key=lambda kv: kv[1]):
    print(f"  {name:16s} 0x{addr:04x}")

with open('st7789_flash_image.bin', 'wb') as f:
    f.write(prog_bytes)
with open('st7789_flash_image.hex', 'w') as f:
    for b in prog_bytes:
        f.write(f"{b:02x}\n")
print("Wrote st7789_flash_image.bin and .hex")
