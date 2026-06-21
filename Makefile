## KalinkaPlayer Development Makefile

.PHONY: clean build-native test help kalinka-server-deb kalinka-plugins-deb build-all-deb copy-debs dev-setup dev-run dev-rebuild-native

## --- Local-from-source dev environment (no root, no systemd) ------------------
## Everything lands in a per-user fakeroot under $(KALINKA_PREFIX) instead of the
## system FHS paths. Override the venv or prefix on the command line, e.g.
##   make dev-run KALINKA_PREFIX=$$HOME/kalinka VENV=.venv
##
## If a venv is already active ($VIRTUAL_ENV set) it is reused as-is — no second
## venv is created. Otherwise we fall back to ./.venv (created on first setup).
VENV ?= $(if $(VIRTUAL_ENV),$(VIRTUAL_ENV),.venv)
KALINKA_PREFIX ?= $(HOME)/kalinka
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
## Interpreter used to create a fresh venv. Pinned to 3.11 to match production
## (the optional AI packages ship wheels for the prod Python). Override if your
## 3.11 lives elsewhere, e.g. PYTHON=/opt/python3.11/bin/python3.
PYTHON ?= python3.11

## One-shot setup: create venv (unless one is active/exists), install sdk +
## server + all plugins (editable), build the native extension, and seed the
## fakeroot directory tree + config.
dev-setup:
	@if [ -n "$(VIRTUAL_ENV)" ]; then \
		echo "Reusing active venv: $(VIRTUAL_ENV)"; \
	elif [ -d $(VENV) ]; then \
		echo "Reusing existing venv: $(VENV)"; \
	else \
		command -v $(PYTHON) >/dev/null 2>&1 || { \
			echo "ERROR: '$(PYTHON)' not found on PATH."; \
			echo "  Install Python 3.11 (e.g. 'sudo apt install python3.11 python3.11-venv')"; \
			echo "  or point PYTHON at it: make dev-setup PYTHON=/path/to/python3.11"; \
			exit 1; }; \
		echo "Creating venv at $(VENV) with $$($(PYTHON) --version)"; $(PYTHON) -m venv $(VENV); \
	fi
	@ver=$$($(PY) -c 'import sys; print("%d.%d" % sys.version_info[:2])'); \
	if [ "$$ver" != "3.11" ]; then \
		echo "ERROR: venv Python is $$ver, but 3.11 is required (to match production)."; \
		echo "  Recreate it with 3.11, or point VENV at an existing 3.11 venv:"; \
		echo "    rm -rf $(VENV) && make dev-setup            # recreate with $(PYTHON)"; \
		echo "    make dev-setup VENV=/path/to/py311-venv"; \
		exit 1; \
	fi
	@echo "Using $$($(PY) --version)"
	@command -v g++ >/dev/null 2>&1 || { \
		echo "ERROR: g++ not found. The native player needs a C++ toolchain and"; \
		echo "  the ALSA/FLAC/curlpp/spdlog/fmt dev headers. Install the system"; \
		echo "  prerequisites listed in README.md (Running from source) first."; \
		exit 1; }
	@$(PIP) install --upgrade pip
	@echo "Installing kalinka-plugin-sdk (editable)..."
	@$(PIP) install -e packages/kalinka-plugin-sdk
# The server's editable install compiles the native player in pip's isolated
# PEP-517 build env (pybind11 etc. come from its [build-system] requires) and
# places the .so in-place under src/native_player — no manual build step or
# hand-installed pybind11. `make dev-rebuild-native` rebuilds it for fast C++
# iteration (pybind11 is pulled in as a runtime dep, so that works too).
	@echo "Installing kalinka-server (editable; builds the native player)..."
	@$(PIP) install -e packages/kalinka-server
	@echo "Installing plugins (editable)..."
	@for dir in packages/kalinka-plugin-*; do \
		[ "$$dir" = "packages/kalinka-plugin-sdk" ] && continue; \
		echo "  - $$dir"; \
		$(PIP) install -e "$$dir" || exit 1; \
	done
	@echo "Verifying the server imports (catches missing runtime deps)..."
	@$(PY) -c "import kalinka_server.__main__" || { \
		echo "ERROR: kalinka_server failed to import after install — likely an"; \
		echo "  undeclared runtime dependency. See the traceback above."; \
		exit 1; }
	@echo "Seeding fakeroot under $(KALINKA_PREFIX)..."
	@mkdir -p \
		$(KALINKA_PREFIX)/etc/kalinka \
		$(KALINKA_PREFIX)/var/lib/kalinka \
		$(KALINKA_PREFIX)/srv/kalinka/music \
		$(KALINKA_PREFIX)/var/log/kalinka \
		$(KALINKA_PREFIX)/run/kalinka \
		$(KALINKA_PREFIX)/var/cache/kalinka/numba \
		$(KALINKA_PREFIX)/var/cache/kalinka/artwork
	@test -f $(KALINKA_PREFIX)/etc/kalinka/kalinka_conf.cfg \
		|| cp kalinka_conf.cfg $(KALINKA_PREFIX)/etc/kalinka/kalinka_conf.cfg
	@echo ""
	@echo "Dev environment ready. Start the server with:  make dev-run"
	@echo "  config: $(KALINKA_PREFIX)/etc/kalinka/kalinka_conf.cfg"
	@echo "  logs:   $(KALINKA_PREFIX)/var/log/kalinka/server.log"

## Run the server in the foreground against the fakeroot (Ctrl-C to stop).
## Forward args via ARGS, e.g.  make dev-run ARGS=--debug
dev-run:
	@KALINKA_PREFIX=$(KALINKA_PREFIX) VENV=$(abspath $(VENV)) scripts/dev_run.sh $(ARGS)

## Rebuild the native C++ extension after editing native_player sources, then
## restart the server (Ctrl-C the running dev-run and re-run it, or click
## Restart in the app) to load it.
dev-rebuild-native:
	@cd packages/kalinka-server/src/native_player && $(abspath $(PY)) setup.py build_ext --inplace
	@echo "Native extension rebuilt. Restart the server to load it."
## -----------------------------------------------------------------------------

## Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null || true
	@cd packages/kalinka-server/src/native_player && make clean

## Build native player module
build-native:
	@echo "Building native player module..."
	@cd packages/kalinka-server/src/native_player && python setup.py build_ext --inplace

## Run tests
test:
	@echo "Running tests..."
	@cd packages/kalinka-plugin-sdk && python -m pytest tests/ -v
	@cd packages/kalinka-server && python -m pytest ../../tests/ -v

## Helper function to move debs to debs directory
copy-debs:
	@rm -rf debs
	@mkdir -p debs
	@for dir in packages/*/; do \
		for deb in "$$dir"/*.deb; do \
			if [ -f "$$deb" ]; then \
				echo "Moving $$(basename $$deb) to debs/"; \
				mv "$$deb" debs/; \
			fi; \
		done; \
	done

## Build kalinka-server deb package
kalinka-server-deb:
	@echo "Building kalinka-server deb package..."
	@cd packages/kalinka-server && ./scripts/build_deb.sh

## Build all plugin deb packages (SDK, local files, musiccast, dummydevice, jamendo)
kalinka-plugins-deb:
	@echo "Building plugin deb packages..."
	@for dir in packages/kalinka-plugin-*; do \
		if [ -f "$$dir/scripts/build_deb.sh" ]; then \
			echo "Building $$(basename $$dir)..."; \
			(cd "$$dir" && ./scripts/build_deb.sh) || exit 1; \
		fi; \
	done

## Build all deb packages (server and plugins)
build-all-deb: kalinka-server-deb kalinka-plugins-deb copy-debs
	@echo "All deb packages built successfully!"
	@echo "Debs moved to debs/ directory"

## Show help
help:
	@echo "KalinkaPlayer Development Commands:"
	@echo ""
	@echo "  dev-setup         One-shot local setup: venv + editable installs + native build + ~/kalinka fakeroot"
	@echo "  dev-run           Run the server in the foreground against the fakeroot (Ctrl-C to stop)"
	@echo "  dev-rebuild-native  Rebuild the native C++ extension, then restart to load it"
	@echo ""
	@echo "  build-native      Build the native player C++ extension"
	@echo "  kalinka-server-deb  Build kalinka-server deb package"
	@echo "  kalinka-plugins-deb Build all plugin deb packages (including SDK)"
	@echo "  build-all-deb     Build all deb packages (server and plugins) and move to debs/"
	@echo "  copy-debs         Move built deb packages to debs/ directory"
	@echo "  test              Run all tests"
	@echo "  clean             Clean build artifacts"
	@echo "  help              Show this help message"