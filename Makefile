## KalinkaPlayer Development Makefile

.PHONY: setup-dev clean build-native run-server test help

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

## Show help
help:
	@echo "KalinkaPlayer Development Commands:"
	@echo ""
	@echo "  setup-dev    Set up development environment (install packages in editable mode)"
	@echo "  build-native Build the native player C++ extension"
	@echo "  run-server   Run the kalinka server (requires kalinka_conf.cfg)"
	@echo "  test         Run all tests"
	@echo "  clean        Clean build artifacts"
	@echo "  help         Show this help message"