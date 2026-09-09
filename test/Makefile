# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = *.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DUSE_POWER_PINS
COMPILE_ARGS    += -DSIM
COMPILE_ARGS    += -DUNIT_DELAY=\#1
VERILOG_SOURCES += $(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/verilog/primitives.v
VERILOG_SOURCES += $(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/verilog/sky130_fd_sc_hd.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# MODULE is the basename of the Python test file
MODULE = test

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# ---------------------------------------------------------------------
# Standalone (non-cocotb) Icarus testbenches -- cover the QSPI
# external-memory addition, the reprogrammable boot ROM, the
# FLASH_MODE handoff, and FLASH_PAGE bank-switched flash execution
# (including the real ST7789 driver). See test/README.md's
# "Additional standalone testbenches" section for what each one does.
# ---------------------------------------------------------------------
.PHONY: standalone-tests
standalone-tests:
	iverilog -g2012 -o /tmp/tt_tb_qspi_engine.vvp $(SRC_DIR)/qspi_shared_engine.v tb_qspi_engine.v
	vvp /tmp/tt_tb_qspi_engine.vvp | tee /tmp/tt_out_qspi_engine.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_mem_ext.vvp $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_mem_ext.v
	vvp /tmp/tt_tb_mem_ext.vvp | tee /tmp/tt_out_mem_ext.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_core_ext.vvp $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/qspi_shared_engine.v mem_extmem_test.v spi_ram_model.v tb_core_ext.v
	vvp /tmp/tt_tb_core_ext.vvp | tee /tmp/tt_out_core_ext.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_check.vvp $(SRC_DIR)/tt_um_pineapple_one.v $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_check.v
	vvp /tmp/tt_tb_check.vvp | tee /tmp/tt_out_check.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_flash_handoff.vvp $(SRC_DIR)/tt_um_pineapple_one.v $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_flash_handoff.v
	vvp /tmp/tt_tb_flash_handoff.vvp | tee /tmp/tt_out_flash_handoff.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_flash_paging.vvp $(SRC_DIR)/tt_um_pineapple_one.v $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_flash_paging.v
	vvp /tmp/tt_tb_flash_paging.vvp | tee /tmp/tt_out_flash_paging.log
	iverilog -g2012 -I $(SRC_DIR) -o /tmp/tt_tb_st7789_driver.vvp $(SRC_DIR)/tt_um_pineapple_one.v $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_st7789_driver.v
	vvp /tmp/tt_tb_st7789_driver.vvp | tee /tmp/tt_out_st7789_driver.log
	@! grep -l "FAIL" /tmp/tt_out_*.log
