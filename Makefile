## KalinkaPlayer Development Makefile

.PHONY: setup-dev clean build-native run-server test help kalinka-server-deb kalinka-plugins-deb build-all-deb copy-debs

## Set up development environment
setup-dev:
	@echo "Setting up development environment..."
	@cd packages/kalinka-plugin-sdk && pip install -e .
	@cd packages/kalinka-server/src/native_player && python setup.py build_ext --inplace
	@cd packages/kalinka-server && pip install -e .
	@echo "Development environment ready!"

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

## Run the server (requires config file)
run-server:
	@cd packages/kalinka-server && python -m kalinka_server --config ../../kalinka_conf.cfg

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

## Build all plugin deb packages (SDK, local files, musiccast, dummydevice)
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
	@echo "  setup-dev         Set up development environment (install packages in editable mode)"
	@echo "  build-native      Build the native player C++ extension"
	@echo "  kalinka-server-deb  Build kalinka-server deb package"
	@echo "  kalinka-plugins-deb Build all plugin deb packages (including SDK)"
	@echo "  build-all-deb     Build all deb packages (server and plugins) and move to debs/"
	@echo "  copy-debs         Move built deb packages to debs/ directory"
	@echo "  run-server        Run the kalinka server (requires kalinka_conf.cfg)"
	@echo "  test              Run all tests"
	@echo "  clean             Clean build artifacts"
	@echo "  help              Show this help message"