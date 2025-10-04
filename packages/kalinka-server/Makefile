all: $(TARGET)

# Wheel-based build (default)
build-deb:
	@./scripts/build_deb.sh
clean:
	rm -rf kalinka-server-*.$(ARCH).deb
	rm -rf kalinka-server-*/
	rm -rf dist/*.whl build/ src/*.egg-info/
