VERSION=1.0
TARGET_DIR=kalinka-player-$(VERSION)
ARCH:=$(shell dpkg --print-architecture)
TARGET=$(TARGET_DIR).$(ARCH).deb

all: $(TARGET)

$(TARGET_DIR):
	mkdir -p $(TARGET_DIR)
	cp -r DEBIAN $(TARGET_DIR)
	sed 's/@ARCH@/$(ARCH)/; s/@VERSION@/$(VERSION)/' DEBIAN/control.in > $(TARGET_DIR)/DEBIAN/control
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
	cp scripts/kalinka.service $(TARGET_DIR)/etc/systemd/system/
	find $(TARGET_DIR)/opt/kalinka/ -name '__pycache__' -type d -exec rm -r {} +

$(TARGET): $(TARGET_DIR)
	cd native_player && make
	cp native_player/native_player.*.so $(TARGET_DIR)/opt/kalinka/native_player/
	dpkg-deb --build $(TARGET_DIR)
	mv $(TARGET_DIR).deb $(TARGET)
	rm -rf $(TARGET_DIR)

clean:
	rm -rf $(TARGET_DIR)
	cd native_player && make clean
