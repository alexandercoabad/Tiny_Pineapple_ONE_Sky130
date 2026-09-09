# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = tt_um_pineapple_one.v rv32i_core.v mem.v qspi_shared_engine.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# ---------------------------------------------------------------------
# Standalone testbenches (plain iverilog, not cocotb) covering the QSPI
# external-memory addition. Not part of cocotb's own `all`/default
# target above -- run explicitly via `make standalone-tests`, which CI
# calls as a separate step. Each one must print "ALL TESTS PASSED" or
# this target fails the build.
# ---------------------------------------------------------------------
.PHONY: standalone-tests
standalone-tests:
	@echo "--- tb_qspi_engine.v ---"
	iverilog -g2012 -o /tmp/tb_qspi_engine.vvp $(SRC_DIR)/qspi_shared_engine.v tb_qspi_engine.v
	vvp /tmp/tb_qspi_engine.vvp | tee /tmp/tb_qspi_engine.log
	grep -q "ALL TESTS PASSED" /tmp/tb_qspi_engine.log
	! grep -q "^FAIL" /tmp/tb_qspi_engine.log
	@echo "--- tb_mem_ext.v ---"
	iverilog -g2012 -I$(SRC_DIR) -o /tmp/tb_mem_ext.vvp $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_mem_ext.v
	vvp /tmp/tb_mem_ext.vvp | tee /tmp/tb_mem_ext.log
	grep -q "ALL TESTS PASSED" /tmp/tb_mem_ext.log
	! grep -q "^FAIL" /tmp/tb_mem_ext.log
	@echo "--- tb_core_ext.v ---"
	iverilog -g2012 -I$(SRC_DIR) -o /tmp/tb_core_ext.vvp $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/qspi_shared_engine.v mem_extmem_test.v spi_ram_model.v tb_core_ext.v
	vvp /tmp/tb_core_ext.vvp | tee /tmp/tb_core_ext.log
	grep -q "ALL TESTS PASSED" /tmp/tb_core_ext.log
	! grep -q "^FAIL" /tmp/tb_core_ext.log
	@echo "--- tb_check.v ---"
	iverilog -g2012 -I$(SRC_DIR) -o /tmp/tb_check.vvp $(SRC_DIR)/tt_um_pineapple_one.v $(SRC_DIR)/rv32i_core.v $(SRC_DIR)/mem.v $(SRC_DIR)/qspi_shared_engine.v spi_ram_model.v tb_check.v
	vvp /tmp/tb_check.vvp | tee /tmp/tb_check.log
	grep -q "^PASS scenario1" /tmp/tb_check.log
	grep -q "^PASS scenario2:" /tmp/tb_check.log
	grep -q "^PASS scenario2b" /tmp/tb_check.log
	grep -q "^PASS scenario3" /tmp/tb_check.log
	! grep -q "^FAIL" /tmp/tb_check.log
	@echo "--- all standalone tests passed ---"
