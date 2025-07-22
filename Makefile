VERSION := $(shell scripts/get_latest_version.sh)
RELEASE_TAG := $(shell scripts/get_release_tag.sh)
TARGET_DIR=kalinka-player-$(RELEASE_TAG)
ARCH:=$(shell dpkg --print-architecture)
TARGET=$(TARGET_DIR).$(ARCH).deb
PYTHON_VERSION=$(shell python3 -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))")
WHEEL_PATH ?= $(shell ls dist/*.whl 2>/dev/null | head -1)

all: $(TARGET)

# Traditional source-based build (kept for backward compatibility)
$(TARGET_DIR):
	mkdir -p $(TARGET_DIR)
	cp -r DEBIAN $(TARGET_DIR)
	sed "s/@ARCH@/$(ARCH)/; s/@VERSION@/$(VERSION)/; s/PYTHON_VERSION/$(PYTHON_VERSION)/g" DEBIAN/control.in > $(TARGET_DIR)/DEBIAN/control
	rm $(TARGET_DIR)/DEBIAN/control.in
	mkdir -p $(TARGET_DIR)/usr/bin
	mkdir -p $(TARGET_DIR)/opt/kalinka
	mkdir -p $(TARGET_DIR)/opt/kalinka/native_player
	mkdir -p $(TARGET_DIR)/etc/systemd/system/
	cp kalinka_server.sh $(TARGET_DIR)/usr/bin/
	cp -r addons $(TARGET_DIR)/opt/kalinka/
	cp -r data_model $(TARGET_DIR)/opt/kalinka/
	cp -r src $(TARGET_DIR)/opt/kalinka/
	cp run_server.py $(TARGET_DIR)/opt/kalinka/
	cp pyproject.toml $(TARGET_DIR)/opt/kalinka/
	cp setup.py $(TARGET_DIR)/opt/kalinka/
	cp MANIFEST.in $(TARGET_DIR)/opt/kalinka/
	cp requirements.txt $(TARGET_DIR)/opt/kalinka/
	cp README.md $(TARGET_DIR)/opt/kalinka/
	cp LICENSE $(TARGET_DIR)/opt/kalinka/
	cp scripts/kalinka.service $(TARGET_DIR)/etc/systemd/system/
	find $(TARGET_DIR)/opt/kalinka/ -name '__pycache__' -type d -exec rm -r {} +
	cp kalinka_conf.cfg $(TARGET_DIR)/opt/kalinka/kalinka_conf.cfg

# New wheel-based build target
$(TARGET_DIR)-wheel:
	@if [ -z "$(WHEEL_PATH)" ] || [ ! -f "$(WHEEL_PATH)" ]; then \
		echo "Error: No wheel found. Please build wheel first or specify WHEEL_PATH."; \
		exit 1; \
	fi
	mkdir -p $(TARGET_DIR)
	cp -r DEBIAN $(TARGET_DIR)
	sed "s/@ARCH@/$(ARCH)/; s/@VERSION@/$(VERSION)/; s/PYTHON_VERSION/$(PYTHON_VERSION)/g" DEBIAN/control.in > $(TARGET_DIR)/DEBIAN/control
	rm $(TARGET_DIR)/DEBIAN/control.in
	mkdir -p $(TARGET_DIR)/usr/bin
	mkdir -p $(TARGET_DIR)/opt/kalinka/wheels
	mkdir -p $(TARGET_DIR)/opt/kalinka/native_player
	mkdir -p $(TARGET_DIR)/etc/systemd/system/
	# Copy the wheel and essential files
	cp $(WHEEL_PATH) $(TARGET_DIR)/opt/kalinka/wheels/
	cp kalinka_server.sh $(TARGET_DIR)/usr/bin/
	cp scripts/kalinka.service $(TARGET_DIR)/etc/systemd/system/
	cp kalinka_conf.cfg $(TARGET_DIR)/opt/kalinka/kalinka_conf.cfg
	# Copy requirements for offline installation
	cp requirements.txt $(TARGET_DIR)/opt/kalinka/
	cp README.md $(TARGET_DIR)/opt/kalinka/
	cp LICENSE $(TARGET_DIR)/opt/kalinka/

$(TARGET): $(TARGET_DIR)
	cd native_player && make
	cp native_player/native_player.*.so $(TARGET_DIR)/opt/kalinka/native_player/
	dpkg-deb --build $(TARGET_DIR)
	mv $(TARGET_DIR).deb $(TARGET)
	rm -rf $(TARGET_DIR)

# New wheel-based Debian package target
debian-from-wheel: $(TARGET_DIR)-wheel
	cd native_player && make
	cp native_player/native_player.*.so $(TARGET_DIR)/opt/kalinka/native_player/
	dpkg-deb --build $(TARGET_DIR)
	mv $(TARGET_DIR).deb $(TARGET)
	rm -rf $(TARGET_DIR)

clean:
	rm -rf $(TARGET_DIR)
	cd native_player && make clean
