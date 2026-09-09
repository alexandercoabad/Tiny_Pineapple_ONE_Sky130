// qspi_shared_engine.v -- minimal single-line SPI master shared between
// external flash (CS0) and PSRAM (CS1) on the Tiny Tapeout QSPI Pmod.
//
// Deliberately uses only plain single-line SPI (standard 0x03 READ /
// 0x02 WRITE commands, 24-bit address), NOT flash's continuous-read
// mode or PSRAM's QPI mode -- both need an extra mode-byte/setup
// sequence that's easy to get subtly wrong without real hardware to
// verify against. Same reasoning AgilA8's shared engine uses.
//
// One requester at a time only: req_valid must be held high for the
// whole transaction and mem.v must guarantee only one device is ever
// selected (see mem.v's req_dev decode) -- there's no arbitration
// logic here because the CPU's own FSM only ever has one outstanding
// access.
//
// SCK is generated at clk/2 (500 kHz at a 1 MHz system clock, well
// inside both chips' timing budget), SPI mode 0: MOSI changes on the
// SCK falling edge (held stable through the whole low half), MISO is
// sampled on the SCK rising edge.
//
// *** THIS HAS NOT BEEN VALIDATED AGAINST A REAL FLASH/PSRAM        ***
// *** BEHAVIORAL MODEL YET. Simulate against one (see AgilA8's      ***
// *** test/ directory for the kind of testbench this needs) before  ***
// *** trusting this for tapeout.                                    ***
//
// Byte-order convention: bytes are transferred in ADDRESS order (the
// byte at req_addr goes out first, then req_addr+1, etc.), matching
// how a real flash/PSRAM auto-increments its own address on
// consecutive clocked bytes after the address phase. Combined with
// this project's little-endian convention (wdata[7:0]/rdata[7:0] is
// always the byte at the lowest address), the first byte transferred
// is wdata[7:0] on a write and becomes rdata[7:0] on a read. Because
// the command+address phase is a fixed 32 bits but the data phase is
// variable width (8/16/32 bits), the data bytes must sit at the TOP
// of the data field (immediately after the address bits), not at a
// fixed low position -- see build_preload() below, this is the exact
// bug this version fixes relative to an earlier draft.

`default_nettype none

module qspi_shared_engine (
    input  wire        clk,
    input  wire        rst_n,

    // request interface (mem.v side)
    input  wire        req_valid,   // held high for the whole transaction
    input  wire        req_we,      // 0 = read, 1 = write
    input  wire [1:0]  req_dev,     // 2'd1 = flash (CS0), 2'd2 = psram (CS1)
    input  wire [23:0] req_addr,    // byte address within the selected device
    input  wire [31:0] req_wdata,
    input  wire [1:0]  req_size,    // 0=byte 1=half 2=word (same as mem.v's `size`)
    output reg  [31:0] req_rdata,
    output reg         req_ready,   // pulses high for exactly 1 cycle when done

    // Pmod pins (subset used in single-line mode)
    output reg         pin_cs0,     // flash CS, active low
    output reg         pin_cs1,     // psram "RAM A" CS, active low
    output reg         pin_sck,
    output reg         pin_mosi,    // SD0, engine-driven
    input  wire        pin_miso     // SD1, engine-sampled
);

    localparam [7:0] CMD_READ  = 8'h03;
    localparam [7:0] CMD_WRITE = 8'h02;

    // Builds the full 64-bit shift preload: {CMD(8), ADDR(24), DATA(32)}.
    // Only the top (32 + nbytes*8) bits of this ever actually get
    // shifted out, so the data field is packed against the top of its
    // 32-bit region (right after the address), not against the bottom.
    function [63:0] build_preload;
        input        we;
        input [1:0]  size;
        input [23:0] addr;
        input [31:0] wd;
        reg [31:0] data_field;
        begin
            if (!we) begin
                data_field = 32'h0; // don't-care during a read's data phase
            end else begin
                case (size)
                    2'd0:    data_field = {wd[7:0], 24'h0};
                    2'd1:    data_field = {wd[7:0], wd[15:8], 16'h0};
                    default: data_field = {wd[7:0], wd[15:8], wd[23:16], wd[31:24]};
                endcase
            end
            build_preload = {(we ? CMD_WRITE : CMD_READ), addr, data_field};
        end
    endfunction

    // total bits in this transaction: 8 (cmd) + 24 (addr) + data bits
    reg  [6:0] nbits_total;
    reg  [6:0] bits_done;

    // One big shift register: MSB shifted out on pin_mosi, new bit
    // from pin_miso shifted in at the bottom every bit. After the
    // whole transaction, the low `data_bits` bits hold whatever was
    // clocked in during the data phase (cmd/addr bits have been
    // shifted fully out the top by then).
    reg [63:0] sreg;
    reg [5:0]  data_bits; // nbytes*8 for this transfer

    localparam ST_IDLE     = 3'd0;
    localparam ST_SHIFT_LO = 3'd1; // SCK low half of a bit period
    localparam ST_SHIFT_HI = 3'd2; // SCK high half (sample + shift)
    localparam ST_DONE     = 3'd3;
    localparam ST_POST     = 3'd4; // one-cycle buffer after DONE, before IDLE
                                    // re-checks req_valid -- without this,
                                    // req_ready becomes visible on the exact
                                    // same edge the engine re-enters IDLE, so
                                    // a consumer that hasn't dropped req_valid
                                    // by then causes an immediate spurious
                                    // re-trigger with stale address/data.
                                    // Caught by test/tb_mem_ext.v.

    reg [2:0] state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= ST_IDLE;
            pin_cs0   <= 1'b1;
            pin_cs1   <= 1'b1;
            pin_sck   <= 1'b0;
            pin_mosi  <= 1'b1;
            req_ready <= 1'b0;
            req_rdata <= 32'h0;
            sreg      <= 64'h0;
        end else begin
            req_ready <= 1'b0; // default: single-cycle pulse only

            case (state)
                ST_IDLE: begin
                    pin_sck <= 1'b0;
                    if (req_valid) begin
                        pin_cs0 <= (req_dev == 2'd1) ? 1'b0 : 1'b1;
                        pin_cs1 <= (req_dev == 2'd2) ? 1'b0 : 1'b1;

                        data_bits   <= (req_size == 2'd0) ? 6'd8  :
                                       (req_size == 2'd1) ? 6'd16 : 6'd32;
                        nbits_total <= 7'd32 +
                                       ((req_size == 2'd0) ? 7'd8  :
                                        (req_size == 2'd1) ? 7'd16 : 7'd32);
                        bits_done   <= 7'd0;

                        sreg  <= build_preload(req_we, req_size, req_addr, req_wdata);
                        state <= ST_SHIFT_LO;
                        // pin_mosi is intentionally NOT set here -- ST_SHIFT_LO
                        // (below) sets it from sreg[63] on its very first pass
                        // too, using the preload just written above, so the
                        // first bit gets exactly the same settle time before
                        // its rising edge as every subsequent bit.
                    end
                end

                ST_SHIFT_LO: begin
                    pin_sck  <= 1'b0;
                    // Set up MOSI here (SCK low half) so it's stable for a
                    // full half-period before the next rising edge -- doing
                    // this in ST_SHIFT_HI instead (as an earlier version of
                    // this file did) changes MOSI on the SAME edge SCK rises,
                    // a same-edge race that shifts every bit one position
                    // early. Caught by test/tb_qspi_engine.v's bitstream check.
                    pin_mosi <= sreg[63];
                    state    <= ST_SHIFT_HI;
                end

                ST_SHIFT_HI: begin
                    pin_sck <= 1'b1;
                    sreg    <= {sreg[62:0], pin_miso};
                    if (bits_done + 7'd1 == nbits_total) begin
                        state <= ST_DONE;
                    end else begin
                        bits_done <= bits_done + 7'd1;
                        state     <= ST_SHIFT_LO;
                    end
                end

                ST_DONE: begin
                    pin_cs0 <= 1'b1;
                    pin_cs1 <= 1'b1;
                    pin_sck <= 1'b0;
                    // Low `data_bits` bits of sreg = data phase content, in
                    // address order (first byte transferred ends up most
                    // significant within this field). Reverse byte order
                    // here to land back in rdata[7:0]-is-lowest-address form.
                    case (data_bits)
                        6'd8:    req_rdata <= {24'h0, sreg[7:0]};
                        6'd16:   req_rdata <= {16'h0, sreg[7:0], sreg[15:8]};
                        default: req_rdata <= {sreg[7:0], sreg[15:8], sreg[23:16], sreg[31:24]};
                    endcase
                    req_ready <= 1'b1;
                    state     <= ST_POST;
                end

                ST_POST: begin
                    // req_ready has now been visible for a full cycle;
                    // safe to check req_valid again starting next cycle.
                    state <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
