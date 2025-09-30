# Kalinka Plugin Cookiecutter Template

This is a cookiecutter template for creating new Kalinka Music Player plugins. It provides a complete project structure with all necessary files, configuration, and boilerplate code for both input module and device plugins.

## Prerequisites

Before using this template, ensure you have:

- Python 3.10+
- [cookiecutter](https://cookiecutter.readthedocs.io/) installed
- Git (for version management)
- Basic understanding of Kalinka Plugin SDK

### Installing Cookiecutter

```bash
pip install cookiecutter
```

## Using the Template

### 1. Generate a New Plugin

Navigate to where you want to create your new plugin and run:

```bash
cookiecutter /path/to/cookiecutter-kalinka-plugin
```

Or if you're in the same directory as the template:

```bash
cookiecutter cookiecutter-kalinka-plugin
```

### 2. Template Variables

When you run cookiecutter, you'll be prompted to provide values for the following variables:

- **plugin_name**: Your plugin name (e.g., "my-awesome-plugin")
  - Used for: package naming, folder names, git tags
  - Format: kebab-case (lowercase with hyphens)

- **plugin_id**: Python package identifier (auto-generated from plugin_name)
  - Format: snake_case (lowercase with underscores)
  - Example: "my_awesome_plugin"

- **plugin_class_prefix**: Class name prefix (auto-generated from plugin_name)
  - Format: PascalCase (no spaces or hyphens)
  - Example: "MyAwesomePlugin"

- **plugin_display_name**: Human-readable name (auto-generated from plugin_name)
  - Format: Title Case
  - Example: "My Awesome Plugin"

- **plugin_description**: Brief description of your plugin's functionality

- **plugin_type**: Choose between:
  - `input_module`: For music streaming services, local file browsers, etc.
  - `device`: For audio output devices, external players, etc.

- **author_name**: Your name

- **author_email**: Your email address

- **version**: Initial version (default: "1.0.0")

- **python_version**: Minimum Python version (default: "3.10")

- **sdk_version_constraint**: SDK version constraint (default: ">=1.0,<2")

- **license**: Choose from:
  - GPL-3.0-or-later (default)
  - MIT
  - Apache-2.0
  - BSD-3-Clause

### 3. Example Session

```bash
$ cookiecutter cookiecutter-kalinka-plugin
plugin_name [my-awesome-plugin]: spotify-plugin
plugin_id [spotify_plugin]: 
plugin_class_prefix [SpotifyPlugin]: 
plugin_display_name [Spotify Plugin]: 
plugin_description [A Kalinka music player plugin]: Spotify integration for Kalinka
plugin_type [input_module]: 
author_name [Your Name]: John Doe
author_email [your.email@example.com]: john@example.com
version [1.0.0]: 
python_version [3.10]: 
sdk_version_constraint [>=1.0,<2]: 
license [GPL-3.0-or-later]: MIT
year [2025]: 
```

## Generated Project Structure

After generation, you'll have a complete plugin project:

```
spotify-plugin/
├── README.md                    # Project documentation
├── pyproject.toml              # Python package configuration
├── src/
│   └── spotify_plugin/         # Main Python package
│       ├── __init__.py         # Package initialization
│       ├── _version.py         # Auto-generated version file
│       ├── config_model.py     # Plugin configuration schema
│       ├── module_setup.py     # Plugin entry point
│       └── spotify_plugin_input_module.py  # Input module (or device)
├── debian/                     # Debian packaging files
│   ├── control.in             # Package metadata template
│   ├── postinst              # Post-installation script
│   ├── prerm                 # Pre-removal script
│   └── rules                 # Build rules
├── scripts/
│   ├── build_wheel.sh        # Build Python wheel
│   └── build_deb.sh          # Build Debian package
└── tests/
    └── test_smoke.py         # Basic smoke tests
```

## Next Steps After Generation

### 1. Initialize Git Repository

```bash
cd your-new-plugin
git init
git add .
git commit -m "Initial commit from cookiecutter template"
```

### 2. Implement Your Plugin Logic

#### For Input Module Plugins:
Edit `src/your_plugin/your_plugin_input_module.py` and implement:
- `search()` - Search for tracks, albums, artists
- `browse()` - Browse music catalogs
- `get_track_info()` - Get detailed track information
- `list_favorite()` - List user favorites
- `playlist_*()` - Playlist management methods
- `get_resource_path()` - Get cover art URLs

#### For Device Plugins:
Edit `src/your_plugin/your_plugin_device.py` and implement:
- `play()` - Start playing a track
- `pause()` - Pause playback
- `resume()` - Resume playback
- `stop()` - Stop playback
- `set_volume()` - Control volume
- `seek()` - Seek to position
- Other device control methods

### 3. Add Configuration Fields

Edit `src/your_plugin/config_model.py` to add plugin-specific configuration:

```python
class YourPluginConfig(ModuleConfig):
    name: str = Field(default="your_plugin", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module Enabled")
    
    # Add your custom fields
    api_key: str = Field(default="", title="API Key", description="Your service API key")
    server_url: str = Field(default="https://api.example.com", title="Server URL")
    timeout: int = Field(default=30, title="Request Timeout (seconds)")
```

### 4. Update Module Setup

Edit `src/your_plugin/module_setup.py` to add any initialization logic:

```python
def setup(cfg: YourPluginConfig, ctx: "PluginContext") -> InputModule:
    """Entry point used by Kalinka"""
    ctx.logger.info("plugin_setup", plugin=PLUGIN_ID, version=ctx.sdk_version)
    
    # Add any initialization logic here
    if not cfg.api_key:
        ctx.logger.warning("No API key configured")
    
    return YourPluginInputModule(cfg)
```

### 5. Add Tests

Extend `tests/test_smoke.py` with your own tests:

```python
def test_custom_functionality():
    """Test your plugin's custom functionality"""
    config = YourPluginConfig(api_key="test-key")
    module = YourPluginInputModule(config)
    
    # Add your tests here
    assert module.is_configured()
```

### 6. Build and Test

```bash
# Install in development mode
pip install -e .

# Run tests
pytest tests/

# Build wheel
./scripts/build_wheel.sh

# Build Debian package (optional)
./scripts/build_deb.sh
```

### 7. Version Management

The template uses setuptools_scm for automatic versioning:

```bash
# Create a release
git tag kalinka-plugin-your-plugin-v1.0.0
git push origin kalinka-plugin-your-plugin-v1.0.0

# Development versions are automatically generated
```

## Template Features

### Conditional Content

The template includes conditional content based on plugin type:
- Input module plugins get input module implementation stubs
- Device plugins get device implementation stubs
- Tests are customized for the chosen plugin type

### Automatic Naming

Many values are automatically derived from your plugin name:
- Package names are converted to snake_case
- Class names are converted to PascalCase
- Display names are converted to Title Case

### Complete Build System

The generated project includes:
- Python packaging with setuptools
- Automatic version management with setuptools_scm
- Debian packaging support
- Test framework setup
- Development dependency management

### Documentation

Each generated project includes:
- Comprehensive README with usage instructions
- Inline code documentation
- Build and installation instructions

## Best Practices

### Plugin Naming
- Use descriptive, kebab-case names (e.g., "spotify-plugin", "local-files")
- Avoid overly generic names
- Include the service/device name if applicable

### Development
- Start with the smoke tests to ensure basic structure works
- Implement one method at a time and test incrementally
- Use structured logging with the provided context logger
- Handle errors gracefully and provide meaningful messages

### Configuration
- Add validation to your configuration model
- Provide sensible defaults
- Document all configuration options
- Mark sensitive fields appropriately

### Testing
- Write tests for your core functionality
- Test error conditions and edge cases
- Use pytest fixtures for common setup
- Mock external services during testing

## Troubleshooting

### Common Issues

**Template not found**: Ensure you're using the correct path to the template directory.

**Permission errors**: Make sure you have write permissions in the target directory.

**Missing dependencies**: Install cookiecutter and ensure Python 3.10+ is available.

**Build failures**: Check that all template variables were properly substituted and that there are no syntax errors in generated files.

### Getting Help

- Check the Kalinka Plugin SDK documentation
- Review existing plugins for examples
- Ask for help in the Kalinka community

## Contributing to the Template

If you find issues with the template or want to add features:

1. Test your changes with different variable combinations
2. Ensure both plugin types (input_module and device) work correctly
3. Update this documentation as needed
4. Test the generated projects can be built and installed

The template is designed to be a solid starting point for plugin development while remaining flexible enough for various use cases.