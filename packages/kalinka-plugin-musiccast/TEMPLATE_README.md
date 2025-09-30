# Creating a New Kalinka Plugin

This cookiecutter template generates a complete Kalinka music player plugin project.

## Quick Start

1. Install cookiecutter:
   ```bash
   pip install cookiecutter
   ```

2. Generate your plugin:
   ```bash
   cookiecutter /path/to/this/template
   ```

3. Follow the prompts to configure your plugin

4. Implement your plugin logic in the generated files

See the main [README.md](../README.md) for detailed documentation.

## Plugin Types

- **input_module**: For music streaming services, local file browsers
- **device**: For audio output devices, external players

## What Gets Generated

- Complete Python package structure
- Configuration model with Pydantic
- Plugin entry point and setup
- Debian packaging support
- Test framework
- Build scripts
- Documentation