ARCH:=$(shell dpkg --print-architecture)
PYTHON_VERSION=$(shell python3 -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))")
WHEEL_PATH ?= $(shell ls dist/*.whl 2>/dev/null | head -1)

all: $(TARGET)

# Wheel-based build (default)
build-deb:
	@echo "Building native player first..."
	cd src/native_player && make && cd ..
	@echo "Building wheel with native player included..."
	@if command -v python3 > /dev/null && [ -f setup.py ]; then \
		python3 -m pip install --upgrade build >/dev/null 2>&1 || true; \
		rm -rf dist/*.whl; \
		python3 -m build --wheel || python3 setup.py bdist_wheel; \
	else \
		echo "Error: Python build tools not found."; \
		exit 1; \
	fi
	@WHEEL_PATH=$$(ls dist/*.whl 2>/dev/null | head -1); \
	if [ -z "$$WHEEL_PATH" ] || [ ! -f "$$WHEEL_PATH" ]; then \
		echo "Error: No wheel could be built."; \
		exit 1; \
	fi; \
	WHEEL_VERSION=$$(basename "$$WHEEL_PATH" | sed 's/kalinka_server-\(.*\)-py3-none-any\.whl/\1/'); \
	TARGET_DIR="kalinka-server-$$WHEEL_VERSION"; \
	TARGET_FILE="$$TARGET_DIR.$(ARCH).deb"; \
	echo "Building Debian package with version: $$WHEEL_VERSION"; \
	echo "Target directory: $$TARGET_DIR"; \
	echo "Target file: $$TARGET_FILE"; \
	mkdir -p "$$TARGET_DIR"; \
	cp -r DEBIAN "$$TARGET_DIR"; \
	sed "s/@ARCH@/$(ARCH)/; s/@VERSION@/$$WHEEL_VERSION/; s/PYTHON_VERSION/$(PYTHON_VERSION)/g" DEBIAN/control.in > "$$TARGET_DIR/DEBIAN/control"; \
	rm "$$TARGET_DIR/DEBIAN/control.in"; \
	mkdir -p "$$TARGET_DIR/usr/bin"; \
	mkdir -p "$$TARGET_DIR/opt/kalinka/wheels"; \
	mkdir -p "$$TARGET_DIR/etc/systemd/system/"; \
	cp "$$WHEEL_PATH" "$$TARGET_DIR/opt/kalinka/wheels/"; \
	cp kalinka_server.sh "$$TARGET_DIR/usr/bin/"; \
	cp scripts/kalinka.service "$$TARGET_DIR/etc/systemd/system/"; \
	cp requirements.txt "$$TARGET_DIR/opt/kalinka/"; \
	cp ../../README.md "$$TARGET_DIR/opt/kalinka/"; \
	cp LICENSE "$$TARGET_DIR/opt/kalinka/"; \
	dpkg-deb --root-owner-group --build "$$TARGET_DIR"; \
	mv "$$TARGET_DIR.deb" "$$TARGET_FILE"; \
	rm -rf "$$TARGET_DIR"; \
	echo "Successfully built: $$TARGET_FILE"

# Legacy target for compatibility
$(TARGET): build-deb

clean:
	rm -rf kalinka-server-*.$(ARCH).deb
	rm -rf kalinka-server-*/
	cd src/native_player && make clean
