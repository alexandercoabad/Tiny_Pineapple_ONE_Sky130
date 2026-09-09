#!/usr/bin/env python3
"""RV32I mini-assembler for Pineapple-TT, extracted from
tools/build_boot_rom.py's Asm class (same encoding, same finalize()
fixup logic) so it can be imported by other programs instead of only
existing as inline script code. Adds SRLI, which the boot ROM itself
never needed but this LCD driver does (extracting the MSB of a byte
for bit-banged SPI). JAL here is standard RISC-V semantics -- writes
its return address ONLY to the encoded rd (unlike AgilA8's core,
which hardwires JAL's link register to r7 regardless of rd -- no such
quirk here, JAL(0, label) is a genuinely side-effect-free plain jump).
"""

OPC = dict(
    OP_IMM=0b0010011, OP_REG=0b0110011, OP_LOAD=0b0000011,
    OP_STORE=0b0100011, OP_BRANCH=0b1100011, OP_JAL=0b1101111,
    OP_JALR=0b1100111,
)


def _s12(v):
    return v & 0xFFF


class Asm:
    def __init__(self):
        self.instrs = []
        self.labels = {}
        self.fixups = []

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

    def _r(self, funct7, rs2, rs1, funct3, rd, opcode):
        return (funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode

    def ADD(self, rd, rs1, rs2): self.emit(self._r(0, rs2, rs1, 0b000, rd, OPC['OP_REG']))
    def OR(self, rd, rs1, rs2):  self.emit(self._r(0, rs2, rs1, 0b110, rd, OPC['OP_REG']))
    def AND(self, rd, rs1, rs2): self.emit(self._r(0, rs2, rs1, 0b111, rd, OPC['OP_REG']))
    def XOR(self, rd, rs1, rs2): self.emit(self._r(0, rs2, rs1, 0b100, rd, OPC['OP_REG']))

    def _i(self, imm, rs1, funct3, rd, opcode):
        return (_s12(imm) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode

    def ADDI(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b000, rd, OPC['OP_IMM']))
    def ANDI(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b111, rd, OPC['OP_IMM']))
    def ORI(self, rd, rs1, imm):  self.emit(self._i(imm, rs1, 0b110, rd, OPC['OP_IMM']))
    def SLLI(self, rd, rs1, sh):  self.emit(self._i(sh & 0x1F, rs1, 0b001, rd, OPC['OP_IMM']))
    def SRLI(self, rd, rs1, sh):  self.emit(self._i(sh & 0x1F, rs1, 0b101, rd, OPC['OP_IMM']))
    def LW(self, rd, rs1, imm):   self.emit(self._i(imm, rs1, 0b010, rd, OPC['OP_LOAD']))
    def LBU(self, rd, rs1, imm):  self.emit(self._i(imm, rs1, 0b100, rd, OPC['OP_LOAD']))
    def JALR(self, rd, rs1, imm): self.emit(self._i(imm, rs1, 0b000, rd, OPC['OP_JALR']))

    def SB(self, rs2, rs1, imm):
        imm = _s12(imm)
        w = ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (0b000 << 12) | ((imm & 0x1F) << 7) | OPC['OP_STORE']
        self.emit(w)

    def SW(self, rs2, rs1, imm):
        imm = _s12(imm)
        w = ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (0b010 << 12) | ((imm & 0x1F) << 7) | OPC['OP_STORE']
        self.emit(w)

    def _branch(self, funct3, rs1, rs2, target):
        self.emit(None, fixup=('branch', funct3, rs1, rs2, target))

    def BEQ(self, rs1, rs2, target): self._branch(0b000, rs1, rs2, target)
    def BNE(self, rs1, rs2, target): self._branch(0b001, rs1, rs2, target)
    def BLT(self, rs1, rs2, target): self._branch(0b100, rs1, rs2, target)

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
                b12 = (imm >> 12) & 1
                b10_5 = (imm >> 5) & 0x3F
                b4_1 = (imm >> 1) & 0xF
                b11 = (imm >> 11) & 1
                words[idx] = ((b12 << 31) | (b10_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) |
                              (b4_1 << 8) | (b11 << 7) | OPC['OP_BRANCH'])
            elif kind == 'jal':
                _, _, rd, target = entry
                off = self.labels[target] - pc
                assert -1048576 <= off < 1048576 and off % 2 == 0, f"jal offset {off} out of range"
                imm = off & 0x1FFFFF
                b20 = (imm >> 20) & 1
                b10_1 = (imm >> 1) & 0x3FF
                b11 = (imm >> 11) & 1
                b19_12 = (imm >> 12) & 0xFF
                words[idx] = ((b20 << 31) | (b10_1 << 21) | (b11 << 20) | (b19_12 << 12) | (rd << 7) | OPC['OP_JAL'])
            else:
                raise ValueError(kind)
        assert all(w is not None for w in words)
        return words


def words_to_bytes(words):
    out = bytearray()
    for w in words:
        out += w.to_bytes(4, 'little')
    return bytes(out)
