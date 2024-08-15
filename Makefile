VERSION=1.0
TARGET_DIR=kalinka-player-$(VERSION)
ARCH:=$(shell dpkg --print-architecture)
TARGET=$(TARGET_DIR).$(ARCH).deb

all: $(TARGET)

$(TARGET_DIR):
	mkdir -p $(TARGET_DIR)
	cp -r DEBIAN $(TARGET_DIR)
	sed 's/@ARCH@/$(ARCH)/; s/@VERSION@/$(VERSION)/' DEBIAN/control.in > $(TARGET_DIR)/DEBIAN/control
	mkdir -p $(TARGET_DIR)/usr/bin
	mkdir -p $(TARGET_DIR)/usr/lib
	mkdir -p $(TARGET_DIR)/usr/share/kalinka
	cp kalinka_server.sh $(TARGET_DIR)/usr/bin/
	cp -r addons $(TARGET_DIR)/usr/share/kalinka/
	cp -r data_model $(TARGET_DIR)/usr/share/kalinka/
	cp -r src $(TARGET_DIR)/usr/share/kalinka/
	cp -r run_server.py $(TARGET_DIR)/usr/share/kalinka/
	find $(TARGET_DIR)/usr/share/kalinka/ -name '__pycache__' -type d -exec rm -r {} +

$(TARGET): $(TARGET_DIR)
	cd native_player && make clean && make
	cp native_player/native_player.*.so $(TARGET_DIR)/usr/lib/
	dpkg-deb --build $(TARGET_DIR)
	mv $(TARGET_DIR).deb $(TARGET)
	rm -rf $(TARGET_DIR)

clean:
	rm -rf $(TARGET_DIR)
	cd native_player && make clean
