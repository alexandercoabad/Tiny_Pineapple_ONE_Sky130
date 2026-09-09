// tt_um_pineapple_one.v -- Tiny Tapeout top level
//
// Pin mapping (v2, QSPI Pmod added):
//   ui_in[7:0]  -> memory-mapped input register at address 0xF4 (switches)
//   uo_out[7:0] -> memory-mapped output register at address 0xF0 (LEDs)
//   uio[0] -> QSPI Pmod CS0  (flash -- backs the 0xB4-0xDF program
//              window when a bootloaded program sets FLASH_MODE; see
//              mem.v's header. Never asserted by the boot ROM itself.)
//   uio[1] -> QSPI Pmod SD0/MOSI
//   uio[2] -> QSPI Pmod SD1/MISO (input)
//   uio[3] -> QSPI Pmod SCK
//   uio[4] -> held high (SD2, unused in single-line mode)
//   uio[5] -> held high (SD3, unused in single-line mode)
//   uio[6] -> QSPI Pmod CS1  (PSRAM "RAM A", backs mem.v's 0xB0-0xEF window)
//   uio[7] -> unused, left as input
//
// `ena` is ignored (always active) per TT convention for simple designs.

`default_nettype none

module tt_um_pineapple_one (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

    wire [7:0]  mem_addr;
    wire [31:0] mem_wdata;
    wire [1:0]  mem_size;
    wire        mem_we;
    wire        mem_valid;
    wire        mem_ready;
    wire [31:0] mem_rdata;
    wire [7:0]  led_out;

    wire        qspi_cs0, qspi_cs1, qspi_sck, qspi_mosi, qspi_miso;

    rv32i_core u_core (
        .clk       (clk),
        .rst_n     (rst_n),
        .mem_addr  (mem_addr),
        .mem_wdata (mem_wdata),
        .mem_size  (mem_size),
        .mem_we    (mem_we),
        .mem_valid (mem_valid),
        .mem_ready (mem_ready),
        .mem_rdata (mem_rdata)
    );

    mem u_mem (
        .clk      (clk),
        .rst_n    (rst_n),
        .addr     (mem_addr),
        .wdata    (mem_wdata),
        .size     (mem_size),
        .we       (mem_we),
        .valid    (mem_valid),
        .ready    (mem_ready),
        .rdata    (mem_rdata),
        .gpio_in  (ui_in),
        .gpio_out (led_out),
        .qspi_cs0 (qspi_cs0),
        .qspi_cs1 (qspi_cs1),
        .qspi_sck (qspi_sck),
        .qspi_mosi(qspi_mosi),
        .qspi_miso(qspi_miso)
    );

    assign uo_out  = led_out;

    // uio[2] (MISO) is the only bidirectional pin actually used as an
    // input; everything else this project drives is an output.
    assign qspi_miso = uio_in[2];

    assign uio_out = {1'b0,       // uio[7] unused
                       qspi_cs1,  // uio[6]
                       1'b1,      // uio[5] SD3, held high (unused)
                       1'b1,      // uio[4] SD2, held high (unused)
                       qspi_sck,  // uio[3]
                       1'b0,      // uio[2] MISO -- input, value here is don't-care (oe=0 below)
                       qspi_mosi, // uio[1]
                       qspi_cs0}; // uio[0]

    assign uio_oe  = 8'b1111_1011; // all outputs except uio[2] (MISO, input)

    // Silence unused-signal lint warnings without affecting synthesis
    wire _unused = &{ena, uio_in[7:3], uio_in[1:0], 1'b0};

endmodule
