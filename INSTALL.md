# Kalinka Player Installation Guide

## Development Installation

### Prerequisites
- Python 3.8 or higher
- Git
- Build tools (gcc, make, etc.)
- ALSA development libraries

### Development Setup

1. Clone the repository:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
```

2. Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install development dependencies:
```bash
pip install -r requirements-dev.txt
```

4. Install the package in development mode:
```bash
pip install -e .
```

5. Build the native player extension:
```bash
cd native_player
make
cd ..
```

6. Run the server:
```bash
kalinka-server --config kalinka_conf.yaml
```

## Production Installation

### From Source

1. Build the package:
```bash
./build.sh
```

2. Install the wheel:
```bash
pip install dist/kalinka_player-*.whl
```

### From Debian Package

1. Build the Debian package:
```bash
./build.sh --debian
```

2. Install the package:
```bash
sudo dpkg -i kalinka-player-*.deb
sudo apt-get install -f  # Fix any dependency issues
```

## Version Management

The app bundle (server + first-party plugins) shares a single version, determined automatically from one git tag. To create a release, tag once:
```bash
git tag kalinka-v1.2.3
git push origin kalinka-v1.2.3
```

The plugin SDK is versioned independently by its own SemVer (single source of truth: `__version__` in `packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py`, read by `pyproject.toml` via `[tool.setuptools.dynamic]`); it has no git tag. Plugins pin it `kalinka-plugin-sdk>=1,<2`, so backwards-compatible minor/patch bumps don't break them; a major bump is breaking and requires widening the consumers' `<2` bounds.

The resulting version is used automatically in builds and service discovery. See [RELEASING.md](RELEASING.md) for the full release checklist and how to bump the SDK version.

## Configuration

Copy the example configuration and modify as needed:
```bash
cp kalinka_conf_example.yaml kalinka_conf.yaml
# Edit kalinka_conf.yaml with your settings
```

## Running the Service

### Development
```bash
kalinka-server --config kalinka_conf.yaml --debug
```

### Production (systemd service)
```bash
sudo systemctl start kalinka
sudo systemctl enable kalinka  # Start on boot
```
