ARCH:=$(shell dpkg --print-architecture)
PYTHON_VERSION=$(shell python3 -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))")
WHEEL_PATH ?= $(shell ls dist/*.whl 2>/dev/null | head -1)

all: build-deb

# Wheel-based build
build-deb:
	@echo "Building Kalinka Plugin SDK wheel..."
	@if command -v python3 > /dev/null && [ -f pyproject.toml ]; then \
		python3 -m pip install --upgrade build >/dev/null 2>&1 || true; \
		rm -rf dist/*.whl; \
		python3 -m build --wheel || python3 -m build; \
	else \
		echo "Error: Python build tools not found."; \
		exit 1; \
	fi
	@WHEEL_PATH=$$(ls dist/*.whl 2>/dev/null | head -1); \
	if [ -z "$$WHEEL_PATH" ] || [ ! -f "$$WHEEL_PATH" ]; then \
		echo "Error: No wheel could be built."; \
		exit 1; \
	fi; \
	WHEEL_VERSION=$$(basename "$$WHEEL_PATH" | sed 's/kalinka_plugin_sdk-\(.*\)-py3-none-any\.whl/\1/'); \
	TARGET_DIR="kalinka-plugin-sdk-$$WHEEL_VERSION"; \
	TARGET_FILE="$$TARGET_DIR.$(ARCH).deb"; \
	echo "Building Debian package with version: $$WHEEL_VERSION"; \
	echo "Target directory: $$TARGET_DIR"; \
	echo "Target file: $$TARGET_FILE"; \
	mkdir -p "$$TARGET_DIR"; \
	cp -r debian "$$TARGET_DIR/DEBIAN"; \
	sed "s/@ARCH@/$(ARCH)/; s/@VERSION@/$$WHEEL_VERSION/; s/PYTHON_VERSION/$(PYTHON_VERSION)/g" debian/control.in > "$$TARGET_DIR/DEBIAN/control"; \
	rm "$$TARGET_DIR/DEBIAN/control.in"; \
	mkdir -p "$$TARGET_DIR/opt/kalinka/wheels"; \
	mkdir -p "$$TARGET_DIR/opt/kalinka/sdk"; \
	cp "$$WHEEL_PATH" "$$TARGET_DIR/opt/kalinka/wheels/"; \
	cp README.md "$$TARGET_DIR/opt/kalinka/sdk/"; \
	if [ -f "LICENSE" ]; then cp LICENSE "$$TARGET_DIR/opt/kalinka/sdk/"; fi; \
	dpkg-deb --root-owner-group --build "$$TARGET_DIR"; \
	mv "$$TARGET_DIR.deb" "$$TARGET_FILE"; \
	rm -rf "$$TARGET_DIR"; \
	echo "Successfully built: $$TARGET_FILE"

clean:
	rm -rf kalinka-plugin-sdk-*.$(ARCH).deb
	rm -rf kalinka-plugin-sdk-*/
	rm -rf build/ dist/ src/*.egg-info/

.PHONY: all build-deb clean