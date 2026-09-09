// mem.v -- byte-addressable memory for Pineapple-TT
//
// Address map (8-bit address space, 256 bytes total):
//   0x00 - 0xAF : BOOT ROM (176 bytes / 44 instructions) -- combinational,
//                   fixed logic at synthesis time, always present with or
//                   without any Pmod/host attached. Holds the self-test +
//                   demo/listen loop + bootloader -- see
//                   tools/build_boot_rom.py and "Reprogrammability" below.
//                   Safe to rely on at power-up on real silicon since it
//                   is NOT flip-flop state.
//   0xB0 - 0xDF : RAM (48 bytes) -- flip-flops, undefined at power-on on
//                   real silicon. Split into two sub-ranges:
//                     0xB0-0xB3 : always on-chip scratch, never affected
//                                 by FLASH_MODE. The boot ROM doesn't use
//                                 these itself; free for whatever gets
//                                 bootloaded (e.g. its own stack).
//                     0xB4-0xDF : the bootloader's load target (44 bytes).
//                                 The CPU's own fetch/data window once a
//                                 program is running there -- same bytes
//                                 serve both roles, no Harvard split
//                                 needed. When FLASH_MODE (0xF8) has been
//                                 set, reads of *this* sub-range are
//                                 transparently sourced from external
//                                 flash (CS0) instead -- see below. Writes
//                                 to it while FLASH_MODE is set are
//                                 dropped (real NOR flash can't be
//                                 written with a plain 0x02 command the
//                                 way PSRAM can).
//   0xE0 - 0xEF : RAM (16 bytes) -- external PSRAM ("RAM A" / CS1) on the
//                   Tiny Tapeout QSPI Pmod, via qspi_shared_engine. Reads/
//                   writes here take multiple clock cycles (the core
//                   stalls on `ready` until the SPI transaction
//                   completes) instead of the single-cycle response
//                   everything else on this bus gets. This is also what
//                   the boot ROM's own power-on self-test probes (write
//                   0xA5, read back, compare) to light uo_out[7] when no
//                   Pmod is attached. Unaffected by FLASH_MODE -- always
//                   PSRAM.
//   0xF0        : LED_OUT   (memory-mapped, write-only, drives uo_out)
//   0xF4        : SW_IN     (memory-mapped, read-only, reflects ui_in --
//                   also the bootloader's DATA/CLOCK/START input)
//   0xF8        : FLASH_MODE (memory-mapped, write-only, write-any-value-
//                   to-set -- see "Reprogrammability" below)
//
// Word accesses (LW/SW) must be 4-byte aligned. Byte/half accesses
// (LB/LH/SB/SH) are supported at any address within a region, including
// a half-word that straddles a word boundary within on-chip RAM or ROM.
//
// ---------------------------------------------------------------------
// Reprogrammability
// ---------------------------------------------------------------------
// 0x00-0xAF used to hold a fixed demo *application* program, baked in as
// synthesized combinational logic -- permanent the instant the chip was
// taped out. It now holds a fixed boot ROM instead (see
// tools/build_boot_rom.py), following the same pattern AgilA8's
// boot_rom/shared_ram split uses, adapted to this core's single unified
// 8-bit address space (PC and mem_addr are the same bus here, so a
// loaded program is immediately both writable AND fetchable at the same
// address -- no separate IMEM/DMEM aliasing trick needed the way
// AgilA8's shared_ram requires).
//
// On every reset the boot ROM runs first, from 0x00. It self-tests the
// external PSRAM window (write/read/compare, result latched into
// uo_out[7]), then enters an indefinite demo/listen loop -- blinking a
// counter into uo_out[3:0] while polling ui_in[2] (START) every
// iteration, forever, not just in a bounded post-reset window. Once
// START is seen, it bit-bangs a length-prefixed program over ui_in[0:2]
// (DATA/CLOCK/START -- see build_boot_rom.py's docstring for the exact
// wire protocol) into the 0xB4-0xDF RAM window using plain SB
// instructions, then jumps to 0xB4 to run it -- no special hardware
// write port, just software issuing ordinary stores.
//
// The boot ROM itself never touches FLASH_MODE. It's there for a
// bootloaded program to opt into: writing FLASH_MODE (0xF8, write-any-
// value-to-set) makes the *same* 0xB4-0xDF window resolve to external
// flash (CS0) instead of on-chip RAM from then on, so a chip with a
// flashed QSPI Pmod attached can be set up (once, by something you
// bootload) to boot straight from flash on subsequent power-cycles
// without the host re-pushing anything over the wire. Reflashing that
// chip afterward is a normal SPI flash write, not a new tapeout.
//
// This is a single-cycle combinational read / synchronous write memory
// for the on-chip regions; the external windows (0xB4-0xDF in
// FLASH_MODE, and 0xE0-0xEF always) hand off to qspi_shared_engine and
// stall the core on `ready` for as many cycles as the SPI transaction
// needs.

`default_nettype none

module mem #(
    parameter ROM_BYTES      = 176,
    parameter RAM_BASE       = 8'hB0,
    parameter RAM_BYTES      = 48,
    parameter LOAD_BASE      = 8'hB4,   // start of the FLASH_MODE-redirectable sub-range
    parameter EXT_PSRAM_BASE = 8'hE0,
    parameter EXT_PSRAM_BYTES= 16
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [7:0]  addr,       // byte address
    input  wire [31:0] wdata,
    input  wire [1:0]  size,       // 0=byte, 1=half, 2=word
    input  wire        we,
    input  wire        valid,      // held high by the core for the whole access
    output wire        ready,      // 1 whenever no external transaction is
                                   // in flight -- on-chip accesses always
                                   // see this high immediately (same 1-cycle
                                   // timing as before this port existed)
    output reg  [31:0] rdata,

    input  wire [7:0]  gpio_in,    // ui_in, mapped at 0xF4 -- also the
                                   // bootloader's bit-bang input
    output reg  [7:0]  gpio_out,   // uo_out, mapped at 0xF0

    // Tiny Tapeout QSPI Pmod pins (single-line mode). CS0/flash backs
    // the FLASH_MODE-redirected 0xB4-0xDF window; CS1/psram backs the
    // 0xE0-0xEF window (and is what the boot ROM's self-test probes).
    output wire        qspi_cs0,   // flash CS
    output wire        qspi_cs1,   // psram CS
    output wire        qspi_sck,
    output wire        qspi_mosi,
    input  wire        qspi_miso
);

    // ---------------------------------------------------------------
    // Boot ROM: fixed self-test + demo/listen loop + bootloader, one
    // 32-bit WORD per case arm (addr[7:2]-indexed), little-endian.
    // Generated by tools/build_boot_rom.py -- regenerate that and
    // re-copy its output here if the boot ROM routine changes; don't
    // hand-edit the case statement.
    //
    // Indexed by word, not byte: a case-statement-in-a-function like
    // this is the standard portable way to describe fixed combinational
    // ROM for ASIC synthesis (a `reg` array with an `initial` isn't
    // reliably synthesizable as permanent logic the way it is for FPGA
    // BRAM), but each instantiation of the case costs real area, and an
    // earlier version instantiated it 4 TIMES per access -- once per
    // byte lane, via `rom_byte(addr)`, `rom_byte(addr+1)`, etc, each
    // with its own adder and its own full ~176-entry decode. Reading
    // one 32-bit word per lookup instead of one byte cuts the case to
    // 1/4 the arms (176 bytes -> 44 words) *and* cuts the number of
    // instantiations from 4 down to 2 (below), which is roughly an
    // order-of-magnitude reduction in the comparator/mux logic this
    // ROM synthesizes to -- confirmed with a generic yosys synth run.
    // ---------------------------------------------------------------
    function [31:0] rom_word_at;
        input [5:0] widx;
        begin
            case (widx)
`include "boot_rom_body.vh"
                default: rom_word_at = 32'h0; // unused ROM space, never fetched
            endcase
        end
    endfunction

    // Byte/half accesses can straddle a word boundary at any address
    // (per the header: "Byte/half accesses are supported at any
    // address within a region"), so fetch the word containing `addr`
    // plus the next word, and byte-select the needed 4 bytes out of
    // that 64-bit pair with a dynamic part-select -- equivalent to the
    // old per-byte-call result for every alignment, but built from just
    // two word lookups instead of four byte lookups.
    wire [31:0] rom_w0   = rom_word_at(addr[7:2]);
    wire [31:0] rom_w1   = rom_word_at(addr[7:2] + 6'd1);
    wire [63:0] rom_pair = {rom_w1, rom_w0};
    wire [31:0] rom_word = rom_pair[(addr[1:0] * 8) +: 32];

    // ---------------------------------------------------------------
    // RAM: RAM_BYTES bytes (default 48) at RAM_BASE (default 0xB0),
    // flip-flop backed. 0xB0-0xB3 is plain scratch; LOAD_BASE-and-up
    // (0xB4-0xDF) is the bootloader's load target AND, once running,
    // the CPU's own fetch/data window -- unless FLASH_MODE has
    // redirected reads of that sub-range to external flash (below).
    //
    // Stored as RAM_BYTES/4 32-bit WORDS, not RAM_BYTES individual
    // bytes -- an earlier version used a flat `reg [7:0] ram [0:47]`
    // byte array and read/wrote it through 4 independently-addressed
    // lanes (ram_addr, +1, +2, +3) every access, each a full dynamic
    // 48-entry array reference. Unlike the ROM fix above (a read-only
    // *constant* lookup, where logic optimization can collapse a lot
    // of the redundancy across lanes), a RAM read/write mux selects
    // among *variable* register values, which doesn't compress the
    // same way -- isolating just this module's old read+write logic
    // in a generic yosys synth measured ~3.3k cells (2.3k of them
    // muxes) for what's logically a 48-byte register file, bigger
    // than the ROM's own footprint. Storing words and reading/writing
    // through the same two-word dynamic part-select trick as the ROM
    // (below) cuts the array depth 4x (48 -> 12) and the number of
    // per-access dynamic array references from 4 down to 1 or 2.
    //
    // Word writes (SW) are handled as a single aligned word store, per
    // the header's documented "word accesses must be 4-byte aligned"
    // contract. Byte/half writes (SB/SH) are still supported at any
    // address, including a half-word that straddles a word boundary
    // (ram_byte_off==3) -- split explicitly into the two words it
    // touches rather than relying on 4 independent byte lanes.
    // ---------------------------------------------------------------
    localparam NWORDS = RAM_BYTES / 4;

    // `mem2reg` tells Yosys up front to treat this as individual
    // flip-flops rather than attempting memory inference first and
    // then falling back -- avoids a "Replacing memory \ram_words with
    // list of registers" lint warning for what was always going to
    // end up as plain DFFs anyway at this size (11 words).
    (* mem2reg *) reg [31:0] ram_words [0:NWORDS-1];
    integer i;

    // `ifndef SYNTHESIS` (which Yosys and other synthesis tools define
    // automatically) is the portable, standards-compliant replacement
    // for the old `synthesis translate_off/on` pragma pair -- same
    // "simulation-only" effect, no lint warning about it.
`ifndef SYNTHESIS
    initial for (i = 0; i < NWORDS; i = i + 1) ram_words[i] = 32'h0;
`endif

    // NWORDS is 12, so a 4-bit index is exactly what's needed (covers
    // 0-15); ram_addr is always < RAM_BYTES(48) whenever these are
    // actually used (gated by in_ram/in_ram_range downstream), so
    // this never actually indexes past entry 11 in practice -- narrows
    // away a WIDTHTRUNC warning (array[11:0] only needs a 4-bit index)
    // along with the UNUSEDSIGNAL warning a wider index's unused top
    // bits would otherwise produce.
    wire [3:0]  ram_widx0    = ram_addr[5:2];
    wire [3:0]  ram_widx1    = ram_addr[5:2] + 4'd1;
    wire [1:0]  ram_byte_off = ram_addr[1:0];
    wire [63:0] ram_pair     = {ram_words[ram_widx1], ram_words[ram_widx0]};

    // ---------------------------------------------------------------
    // FLASH_MODE: write-any-value-to-set, no readback, sticky until
    // reset. Never touched by the boot ROM itself -- opt-in for
    // whatever gets bootloaded (see header).
    // ---------------------------------------------------------------
    reg flash_mode;

    wire in_rom        = (addr < ROM_BYTES);
    wire in_ram_range  = (addr >= RAM_BASE) && (addr < (RAM_BASE + RAM_BYTES));
    wire in_load_range = (addr >= LOAD_BASE) && (addr < (RAM_BASE + RAM_BYTES));
    wire in_ext_flash  = flash_mode && in_load_range;
    wire in_ram        = in_ram_range && !in_ext_flash;
    // Only the low 6 bits of (addr - RAM_BASE) are ever consumed
    // below (ram_widx0/1 need bits[5:2], ram_byte_off needs bits[1:0]).
    // Compute the full 8-bit subtraction first, then take an explicit
    // 6-bit slice of it -- the explicit slice (vs. an implicit
    // width-mismatched assignment) avoids a WIDTHTRUNC warning here
    // while the narrower final wire avoids an UNUSEDSIGNAL warning on
    // bits nothing reads. The 8-bit intermediate itself is
    // intentionally wider than what's consumed (correct wraparound
    // for out-of-range addr needs a full mod-256 subtract, not
    // mod-64), so its own top 2 bits are deliberately unused --
    // suppressed explicitly rather than narrowing the subtraction
    // itself and getting the wrong wraparound.
    /* verilator lint_off UNUSEDSIGNAL */
    wire [7:0] ram_addr_full = addr - RAM_BASE;
    /* verilator lint_on UNUSEDSIGNAL */
    wire [5:0] ram_addr = ram_addr_full[5:0];

    // ---------------------------------------------------------------
    // External windows via qspi_shared_engine: the LOAD_BASE sub-range
    // when FLASH_MODE is set (CS0/flash), and the PSRAM window
    // (CS1/psram) always. Only one is ever selected for a given
    // access, and the CPU only ever has one access outstanding at a
    // time (mem_valid is never asserted for two different addresses
    // in the same cycle), so a single shared request bus is safe --
    // same reasoning qspi_shared_engine's own header documents.
    // ---------------------------------------------------------------
    wire in_ext_psram = (addr >= EXT_PSRAM_BASE) && (addr < (EXT_PSRAM_BASE + EXT_PSRAM_BYTES));
    wire in_ext       = in_ext_flash || in_ext_psram;

    wire [1:0]  req_dev  = in_ext_flash ? 2'd1 : 2'd2;               // 1=flash(CS0) 2=psram(CS1)
    wire [23:0] ext_addr = in_ext_flash ? {16'h0, (addr - LOAD_BASE)} : {16'h0, (addr - EXT_PSRAM_BASE)};

    // Flash is read-only from this port -- real NOR flash needs an
    // erase/program sequence a plain 0x02 command can't provide, so
    // writes to the FLASH_MODE-redirected sub-range are dropped (see
    // the write path below), and the request to the engine is never
    // issued as a write for that case either.
    wire        ext_req_we    = we && !in_ext_flash;
    wire        ext_req_valid = valid && in_ext;
    wire [31:0] ext_rdata;
    wire        ext_ready;

    // 1 whenever no genuine external transaction is outstanding this
    // cycle -- preserves the original single-cycle response for every
    // on-chip address, and only ever actually waits on ext_ready when
    // this access is both `valid` and inside an external window.
    assign ready = ext_req_valid ? ext_ready : 1'b1;

    qspi_shared_engine u_qspi (
        .clk       (clk),
        .rst_n     (rst_n),
        .req_valid (ext_req_valid),
        .req_we    (ext_req_we),
        .req_dev   (req_dev),
        .req_addr  (ext_addr),
        .req_wdata (wdata),
        .req_size  (size),
        .req_rdata (ext_rdata),
        .req_ready (ext_ready),
        .pin_cs0   (qspi_cs0),
        .pin_cs1   (qspi_cs1),
        .pin_sck   (qspi_sck),
        .pin_mosi  (qspi_mosi),
        .pin_miso  (qspi_miso)
    );

    // ---------------------------------------------------------------
    // Read path
    // ---------------------------------------------------------------
    always @(*) begin
        if (in_rom) begin
            rdata = rom_word;
        end else if (in_ram) begin
            rdata = ram_pair[(ram_byte_off * 8) +: 32];
        end else if (in_ext) begin
            rdata = ext_rdata; // only meaningful once `ready` has pulsed -- see header
        end else if (addr == 8'hF0) begin
            rdata = {24'b0, gpio_out};
        end else if (addr == 8'hF4) begin
            rdata = {24'b0, gpio_in};
        end else begin
            rdata = 32'h0;
        end
    end

    // ---------------------------------------------------------------
    // Write path (synchronous)
    // ---------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gpio_out   <= 8'h00;
            flash_mode <= 1'b0;
        end else if (we) begin
            if (in_ram_range && !in_ext_flash) begin
                case (size)
                    2'd0: begin // SB -- always fits in one word, no crossing possible
                        ram_words[ram_widx0][(ram_byte_off * 8) +: 8] <= wdata[7:0];
                    end
                    2'd1: begin // SH -- may straddle a word boundary
                        if (ram_byte_off == 2'd3) begin
                            ram_words[ram_widx0][31:24] <= wdata[7:0];
                            ram_words[ram_widx1][7:0]   <= wdata[15:8];
                        end else begin
                            ram_words[ram_widx0][(ram_byte_off * 8) +: 16] <= wdata[15:0];
                        end
                    end
                    default: begin // SW -- documented to be 4-byte aligned
                        ram_words[ram_widx0] <= wdata;
                    end
                endcase
            end else if (addr == 8'hF0) begin
                gpio_out <= wdata[7:0];
            end else if (addr == 8'hF8) begin
                flash_mode <= 1'b1;   // write-any-value-to-set, sticky until reset
            end
        end
    end

endmodule
