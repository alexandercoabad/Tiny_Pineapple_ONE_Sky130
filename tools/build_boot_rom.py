#!/usr/bin/env python3
"""
Boot ROM assembler for Tiny Pineapple ONE (Pineapple-TT).

Mirrors AgilA8's boot_rom approach, adapted to this core's plain RV32I
ISA and single unified 8-bit address space (PC and mem_addr are the
same 8-bit bus here, unlike AgilA8's split IMEM/DMEM) -- so, unlike
AgilA8, no separate "DMEM write pointer that happens to alias IMEM"
trick is needed: the RAM window this loads into (0x84-0xAF) is
directly executable the moment the CPU jumps to it, because it's the
same byte-addressed memory the CPU already fetches instructions from.

This boot ROM does three things, in order, every power-on/reset:

  1. SELF-TEST: probes the external QSPI PSRAM window (0xB0-0xEF,
     backed by "RAM A" / CS1) by writing 0xA5 to its first byte and
     reading it back. Result (0=pass, 1=fail) is latched into bit 7
     of a status register that gets OR'd into every LED_OUT write
     from here on -- so uo_out[7] tells you at a glance whether the
     QSPI Pmod is actually attached, without disturbing the demo
     counter in uo_out[3:0].
  2. DEMO / WAIT_START: loops forever incrementing a 4-bit counter
     into uo_out[3:0] (same visible behavior as the original plain
     counter demo), while polling ui_in[2] (START) every iteration.
     Unlike AgilA8's bounded-timeout WAIT_START, this poll never
     gives up on its own -- the chip is always "listening" for a
     bootload, indefinitely, which is what makes it reprogrammable
     on demand rather than only at a fixed window after reset.
  3. BOOTLOAD: once START is seen, receives a length-prefixed program
     over the same 3-wire handshake protocol AgilA8 uses (all on
     GPIO_IN = 0xF4):
       ui_in[0] = DATA  (host drives)
       ui_in[1] = CLOCK (host drives, one pulse per bit; chip samples
                  DATA on the high phase and waits for the low phase
                  before the next bit -- a full handshake, no baud
                  matching needed)
       ui_in[2] = START (host asserts high to request a bootload)
     Wire format: 1 length byte (MSB first) then that many program
     bytes (MSB first each). Bytes are written with plain SB
     instructions straight into the 0x84-0xAF RAM window -- that
     window IS what the CPU fetches instructions from once the loader
     jumps there, so a received byte is executable immediately, no
     separate write-control registers. On completion, jumps to 0x84
     (RAM_BASE) to run the loaded program.

FLASH_MODE (0xF8, write-any-value-to-set, see mem.v) lets a *loaded*
program hand the 0x84-0xAF window over to external flash (CS0)
instead of on-chip RAM -- e.g. a bootloaded program that wants
subsequent reboots to run straight from flash without needing the
host to re-push it over the wire every power-cycle. This boot ROM
itself never touches FLASH_MODE (it always loads into on-chip RAM),
so that behavior is opt-in and left to whatever gets bootloaded.

Registers:
  x1 = link register (ra) for RECV_BYTE calls
  x2 = GPIO_IN scratch / mask scratch / self-test pattern scratch
  x3 = small constants scratch (bit-counter threshold in RECV_BYTE)
  x4 = length remaining (BYTE_LOOP)
  x5 = RAM write pointer (BYTE_LOOP)
  x6 = self-test readback / RECV_BYTE accumulator -- zeroed on every
       RECV_BYTE entry. (An earlier version skipped this, reasoning
       that after 8 shift-and-maybe-OR steps the low 8 bits are fully
       determined by the received bits regardless of x6's incoming
       garbage -- true, but START_SEEN's `ADD x4,x6,x0` copies the
       *whole* 32-bit register, not just its low byte, so garbage left
       in bits[31:8] (e.g. the self-test's own LBU result, shifted up
       by RECV_BYTE's own SLLIs) silently became part of the "length"
       value, making BYTE_LOOP's BEQ x4,x0 comparison never trip.
       Confirmed in simulation: x4 came out as e.g. 0x0000a50c instead
       of 0x14, decrementing one at a time on every subsequent byte
       forever instead of stopping after 20. The explicit zero costs
       one instruction and removes the whole hazard.)
  x7 = bit counter (RECV_BYTE only)
  x8 = self-test status, bit 7 set on failure, else 0 -- persists for
       the life of the program, OR'd into every LED_OUT write
  x9 = demo counter, kept masked to 4 bits every iteration so it
       wraps 0..15 without a separate masking step at display time
"""

RAM_BASE   = 0xB4       # 0xB4-0xDF: on-chip RAM window, loadable via
                         # this bootloader OR (if a loaded program sets
                         # FLASH_MODE) backed by external flash -- see
                         # mem.v's FLASH_MODE mux. NOTE: this constant
                         # is what mem.v calls LOAD_BASE, 4 bytes after
                         # mem.v's own RAM_BASE (0xB0, the start of the
                         # full 48-byte physical array) -- the first 4
                         # bytes (0xB0-0xB3) are always-on-chip scratch,
                         # never affected by FLASH_MODE, and this boot
                         # ROM doesn't use them for anything itself.
GPIO_IN    = 0xF4
LED_OUT    = 0xF0
EXT_PSRAM  = 0xE0       # first byte of the external PSRAM window (CS1),
                         # used only for the self-test probe here.

DATA_MASK  = 1
CLOCK_MASK = 2
START_MASK = 4

SELFTEST_PATTERN = 0xA5

OPC = dict(
    OP_IMM   =0b0010011, OP_REG   =0b0110011, OP_LOAD  =0b0000011,
    OP_STORE =0b0100011, OP_BRANCH=0b1100011, OP_JAL   =0b1101111,
    OP_JALR  =0b1100111,
)

def _s12(v):
    v &= 0xFFF
    return v

class Asm:
    def __init__(self):
        self.instrs = []   # list of encoded words or None (pending fixup)
        self.labels = {}
        self.fixups = []   # (index, kind, ...)

    def addr(self):
        return len(self.instrs) * 4

    def label(self, name):
        assert name not in self.labels, name
        self.labels[name] = self.addr()

    def emit(self, word_or_none, fixup=None):
        idx = len(self.instrs)
        self.instrs.append(word_or_none)
        if fixup:
            self.fixups.append((idx,) + fixup)
        return idx

    # ---- R-type ----
    def _r(self, funct7, rs2, rs1, funct3, rd, opcode):
        return (funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode

    def ADD(self, rd, rs1, rs2):  self.emit(self._r(0,rs2,rs1,0b000,rd,OPC['OP_REG']))
    def OR (self, rd, rs1, rs2):  self.emit(self._r(0,rs2,rs1,0b110,rd,OPC['OP_REG']))

    # ---- I-type ----
    def _i(self, imm, rs1, funct3, rd, opcode):
        return (_s12(imm) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode

    def ADDI(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b000, rd, OPC['OP_IMM']))
    def ANDI(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b111, rd, OPC['OP_IMM']))
    def ORI (self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b110, rd, OPC['OP_IMM']))
    def SLLI(self, rd, rs1, sh):  self.emit(self._i(sh & 0x1F, rs1, 0b001, rd, OPC['OP_IMM']))
    def LW  (self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b010, rd, OPC['OP_LOAD']))
    def LBU (self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b100, rd, OPC['OP_LOAD']))
    def JALR(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b000, rd, OPC['OP_JALR']))

    # ---- S-type ----
    def SB(self, rs2, rs1, imm):
        imm = _s12(imm)
        w = ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (0b000 << 12) | ((imm & 0x1F) << 7) | OPC['OP_STORE']
        self.emit(w)

    def SW(self, rs2, rs1, imm):
        imm = _s12(imm)
        w = ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (0b010 << 12) | ((imm & 0x1F) << 7) | OPC['OP_STORE']
        self.emit(w)

    # ---- B-type (branches, PC-relative, resolved at finalize()) ----
    def _branch(self, funct3, rs1, rs2, target):
        self.emit(None, fixup=('branch', funct3, rs1, rs2, target))

    def BEQ(self, rs1, rs2, target): self._branch(0b000, rs1, rs2, target)
    def BNE(self, rs1, rs2, target): self._branch(0b001, rs1, rs2, target)
    def BLT(self, rs1, rs2, target): self._branch(0b100, rs1, rs2, target)

    # ---- J-type (JAL, PC-relative, resolved at finalize()) ----
    def JAL(self, rd, target):
        self.emit(None, fixup=('jal', rd, target))

    def finalize(self):
        words = list(self.instrs)
        for entry in self.fixups:
            idx = entry[0]
            kind = entry[1]
            pc = idx * 4
            if kind == 'branch':
                _, _, funct3, rs1, rs2, target = entry
                off = self.labels[target] - pc
                assert -4096 <= off < 4096 and off % 2 == 0, f"branch offset {off} out of range"
                imm = off & 0x1FFF
                b12   = (imm >> 12) & 1
                b10_5 = (imm >> 5) & 0x3F
                b4_1  = (imm >> 1) & 0xF
                b11   = (imm >> 11) & 1
                words[idx] = ((b12<<31)|(b10_5<<25)|(rs2<<20)|(rs1<<15)|(funct3<<12)|
                              (b4_1<<8)|(b11<<7)|OPC['OP_BRANCH'])
            elif kind == 'jal':
                _, _, rd, target = entry
                off = self.labels[target] - pc
                assert -1048576 <= off < 1048576 and off % 2 == 0, f"jal offset {off} out of range"
                imm = off & 0x1FFFFF
                b20    = (imm >> 20) & 1
                b10_1  = (imm >> 1) & 0x3FF
                b11    = (imm >> 11) & 1
                b19_12 = (imm >> 12) & 0xFF
                words[idx] = ((b20<<31)|(b10_1<<21)|(b11<<20)|(b19_12<<12)|(rd<<7)|OPC['OP_JAL'])
            else:
                raise ValueError(kind)
        assert all(w is not None for w in words)
        return words


a = Asm()

# --- SELF-TEST: probe the external PSRAM window ---
a.label("SELFTEST")
a.ADDI(2, 0, SELFTEST_PATTERN)
a.SB  (2, 0, EXT_PSRAM)          # write 0xA5 to ext addr 0
a.LBU (6, 0, EXT_PSRAM)          # read it back (zero-extended)
a.BEQ (6, 2, "SELFTEST_PASS")
a.ADDI(8, 0, 1)
a.SLLI(8, 8, 7)                  # x8 = 0x80 (fail flag in bit 7)
a.JAL (0, "MAIN_INIT")
a.label("SELFTEST_PASS")
a.ADDI(8, 0, 0)

a.label("MAIN_INIT")
a.ADDI(9, 0, 0)                  # demo counter = 0

# --- DEMO / WAIT_START: count forever, polling START every loop ---
a.label("MAIN_LOOP")
a.LW  (2, 0, GPIO_IN)
a.ANDI(2, 2, START_MASK)
a.BNE (2, 0, "START_SEEN")
a.ADDI(9, 9, 1)
a.ANDI(9, 9, 0xF)                # wrap 0..15
a.OR  (2, 8, 9)                  # display = selftest_fail<<7 | counter
a.SW  (2, 0, LED_OUT)
a.JAL (0, "MAIN_LOOP")

# --- BOOTLOAD: length-prefixed program over the DATA/CLOCK handshake ---
a.label("START_SEEN")
a.JAL (1, "RECV_BYTE")           # x6 = length byte
a.ADD (4, 6, 0)                  # x4 = length remaining
a.ADDI(5, 0, RAM_BASE)           # x5 = RAM write pointer

a.label("BYTE_LOOP")
a.BEQ (4, 0, "RUN")
a.JAL (1, "RECV_BYTE")           # x6 = next data byte
a.SB  (6, 5, 0)
a.ADDI(5, 5, 1)
a.ADDI(4, 4, -1)
a.JAL (0, "BYTE_LOOP")

# RUN itself just needs to land the PC at RAM_BASE -- BEQ x4,x0,RUN above
# already lands exactly here, so this JAL is never dead code. JAL's
# immediate is PC-relative, so the absolute-address patch below computes
# it directly against RUN's own address rather than via another label.
a.label("RUN")
a.JAL (0, "RUN")                 # placeholder; patched to an absolute
                                  # jump to RAM_BASE after finalize()

a.label("RECV_BYTE")
a.ADDI(6, 0, 0)                  # x6 = 0 (accumulator) -- see module
                                  # docstring for why this must not be skipped
a.ADDI(7, 0, 0)                  # bit counter
a.label("BIT_WAIT_HIGH")
a.LW  (2, 0, GPIO_IN)
a.ANDI(2, 2, CLOCK_MASK)
a.BEQ (2, 0, "BIT_WAIT_HIGH")
a.LW  (2, 0, GPIO_IN)
a.ANDI(2, 2, DATA_MASK)
a.SLLI(6, 6, 1)
a.BEQ (2, 0, "SKIP_OR")
a.ORI (6, 6, 1)
a.label("SKIP_OR")
a.label("BIT_WAIT_LOW")
a.LW  (2, 0, GPIO_IN)
a.ANDI(2, 2, CLOCK_MASK)
a.BNE (2, 0, "BIT_WAIT_LOW")
a.ADDI(7, 7, 1)
a.ADDI(3, 0, 8)
a.BLT (7, 3, "BIT_WAIT_HIGH")
a.JALR(0, 1, 0)                  # return via ra (x1)

words = a.finalize()

# Patch RUN's placeholder JAL into an absolute jump to RAM_BASE. RAM_BASE
# is a fixed address, not another label, so this is computed directly
# rather than via the normal label-relative fixup path.
pc = a.labels["RUN"]        # a.labels stores byte addresses already
run_idx = pc // 4
off = RAM_BASE - pc
assert -1048576 <= off < 1048576 and off % 2 == 0
imm = off & 0x1FFFFF
b20    = (imm >> 20) & 1
b10_1  = (imm >> 1) & 0x3FF
b11    = (imm >> 11) & 1
b19_12 = (imm >> 12) & 0xFF
words[run_idx] = ((b20<<31)|(b10_1<<21)|(b11<<20)|(b19_12<<12)|(0<<7)|OPC['OP_JAL'])

nbytes = len(words) * 4
print(f"Boot ROM is {nbytes} bytes ({len(words)} instructions)")

with open('boot_rom_body.vh', 'w') as f:
    # One case arm per 32-bit WORD (addr[7:2] indexed), not per byte.
    # An earlier version emitted one arm per BYTE address and read the
    # ROM through a `function` called 4 separate times per access (once
    # per byte lane, each with its own +1/+2/+3 adder) -- that pattern
    # doesn't get recognized as a lookup table by synthesis the way a
    # `reg` array does; each call became its own ~176-entry chain of
    # per-byte equality comparators, x4, and dominated mem.v's cell
    # count (confirmed with a generic yosys `synth`: ~9.5k cells for
    # what should be a small decode module, almost all $_MUX_/$_OR_/
    # $_ANDNOT_ contributed by this ROM). That's very likely what was
    # behind the RTL-GDS placement/routing convergence trouble --
    # replicated random logic that size is a lot for the router to
    # place and wire on a 6x2 tile. Indexing by word instead of byte
    # cuts both the number of case arms (176 -> 44) and the number of
    # times the case is instantiated (4 -> 2, see mem.v's rom_word
    # logic) to a fraction of the original size.
    for i, w in enumerate(words):
        f.write(f"                6'd{i}: rom_word_at = 32'h{w:08x};\n")

with open('boot_rom.hex', 'w') as f:
    for w in words:
        for b in range(4):
            f.write(f"{(w >> (8*b)) & 0xFF:02x}\n")

print("Wrote boot_rom_body.vh and boot_rom.hex")
for name, addr_ in sorted(a.labels.items(), key=lambda kv: kv[1]):
    print(f"  {name:16s} 0x{addr_:02x}")
print(f"RAM_BASE=0x{RAM_BASE:02x}  LED_OUT=0x{LED_OUT:02x}  GPIO_IN=0x{GPIO_IN:02x}  EXT_PSRAM=0x{EXT_PSRAM:02x}")
