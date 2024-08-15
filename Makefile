VERSION=1.0
TARGET_DIR=kalinka-player-$(VERSION)
TARGET=$(TARGET_DIR).dpkg

all: $(TARGET)

$(TARGET_DIR):
	mkdir -p $(TARGET_DIR)
	cp -r DEBIAN $(TARGET_DIR)

$(TARGET): $(TARGET_DIR)
	cd native_player && make
	cd $(TARGET_DIR) && dpkg-deb --build .

clean:
	rm -rf $(TARGET_DIR)
	cd native_player && make clean
