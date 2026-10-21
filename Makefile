MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
PROJECT_ROOT := $(dir $(MKFILE_PATH))

SMOKE_SCRIPT := $(PROJECT_ROOT)scripts/smoke.sh
ABUSE_WRAPPER_SCRIPT := $(PROJECT_ROOT)tests/abuse/run.sh

.PHONY: smoke abuse

smoke:
	@echo "Running smoke tests..."
	@ $(SMOKE_SCRIPT)

abuse:
	@echo "Running abuse tests..."
	@ $(ABUSE_WRAPPER_SCRIPT)
