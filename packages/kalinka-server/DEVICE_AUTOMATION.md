# Device Automation Module

The Device Automation module is an **internal server feature** that provides automatic device management for the Kalinka player. Unlike plugins, this is a built-in module that integrates directly with the server core.

> **Note**: This is not a plugin and is not part of the plugin ecosystem. It's a first-class internal feature of kalinka-server.

## Features

### 1. Auto Power On
Automatically turns on the external device when playback starts if the device is currently off.

### 2. Auto Power Off
Automatically turns off the external device when playback stops.

### 3. Pause Timeout
Automatically stops playback if it has been paused for more than a configured duration (default: 60 seconds).

## Configuration

The device automation module is configured in the main `kalinka_conf.cfg` file under the `device_automation` section:

```json
{
  "device_automation": {
    "auto_power_on": true,
    "auto_power_off": true,
    "pause_timeout_seconds": 60
  }
}
```

### Configuration Options

- **`auto_power_on`** (boolean, default: `true`)
  - When `true`, automatically turns on the device when playback starts
  - Requires the device to support the `POWER_ON` and optionally `IS_POWER_ON` functions
  
- **`auto_power_off`** (boolean, default: `true`)
  - When `true`, automatically turns off the device when playback stops
  - Requires the device to support the `POWER_OFF` and optionally `IS_POWER_ON` functions

- **`pause_timeout_seconds`** (integer, default: `60`)
  - Number of seconds to wait before automatically stopping playback when paused
  - Set to `0` to disable this feature
  - Useful to prevent devices from staying on indefinitely when playback is paused

## How It Works

The module integrates into the Kalinka server and:

1. **Subscribes to playback events** from the playqueue event bus
2. **Monitors playback state changes** (playing, paused, stopped, etc.)
3. **Controls the first available external device** that implements the `ExternalOutputDevice` protocol
4. **Checks device capabilities** to ensure it supports the required functions before attempting to control it

### Event Flow

```
Playback starts (PLAYING)
    └─> Auto power on enabled?
        └─> Device supports power on?
            └─> Check if device is already on
                └─> Turn on device if needed

Playback paused (PAUSED)
    └─> Pause timeout enabled (> 0)?
        └─> Start timer for configured duration
            └─> If still paused when timer expires
                └─> Stop playback

Playback stopped (STOPPED)
    └─> Auto power off enabled?
        └─> Device supports power off?
            └─> Check if device is on
                └─> Turn off device if needed
```

## Device Requirements

For the device automation to work, the external output device must:

1. Implement the `ExternalOutputDevice` protocol
2. Report its capabilities via the `supported_functions()` method
3. Implement the relevant methods:
   - `power_on()` - for auto power on feature
   - `power_off()` - for auto power off feature
   - `is_power_on()` - optional, for checking device state before power commands

### Example Device Capabilities

```python
def supported_functions(self) -> set[SupportedFunction]:
    return {
        SupportedFunction.POWER_ON,
        SupportedFunction.POWER_OFF,
        SupportedFunction.IS_POWER_ON,
        SupportedFunction.GET_VOLUME,
        SupportedFunction.SET_VOLUME,
    }
```

## Integration

The module is managed by the internal modules system and is completely separate from the plugin ecosystem:

1. **Server starts** and loads configuration
2. **Plugins are scanned** and external devices are discovered (via plugin system)
3. **Internal modules are initialized** (including device automation)
4. **Device automation starts** and begins listening to playback events
5. **Module shuts down gracefully** when server stops (before plugins shutdown)

### Architecture

```
kalinka-server/
├── src/kalinka_server/
│   ├── server.py              # Main server, orchestrates startup/shutdown
│   ├── player_setup.py        # Plugin discovery and setup
│   ├── internal_modules.py    # Internal features manager (NEW)
│   ├── device_automation.py   # Device automation implementation
│   └── ...
```

The `internal_modules.py` module manages all internal server features:
- Device automation
- (Future: other internal features can be added here)

This separation ensures that internal features are:
- ✅ Always available (not dependent on plugin ecosystem)
- ✅ Initialized after plugins (can use discovered devices)
- ✅ Properly managed with their own lifecycle
- ✅ Clearly distinguished from external plugins

## Logging

The module logs its activity at various levels:

- **INFO**: Configuration, startup/shutdown, and automation actions
- **DEBUG**: State changes, timer events, and detailed flow
- **ERROR**: Failed automation attempts or configuration issues

Example log output:

```
INFO:player_setup:Device automation will use device: musiccast
INFO:device_automation:Starting device automation module
INFO:device_automation:Configuration: auto_power_on=True, auto_power_off=True, pause_timeout_seconds=60
INFO:device_automation:Device automation capabilities: power_on=True, power_off=True, check_power=True
INFO:device_automation:Auto power on: Turning on device
INFO:device_automation:Pause timeout reached (60s), stopping playback
INFO:device_automation:Auto power off: Turning off device
```

## Example Use Cases

### 1. Home Theater Setup
- Automatically turn on receiver when starting music playback
- Automatically turn off receiver when playback is stopped
- Stop playback if paused for more than 2 minutes to save power

```json
{
  "device_automation": {
    "auto_power_on": true,
    "auto_power_off": true,
    "pause_timeout_seconds": 120
  }
}
```

### 2. Minimize Power Consumption
- Only use auto power off, manual power on
- Short pause timeout to quickly stop idle playback

```json
{
  "device_automation": {
    "auto_power_on": false,
    "auto_power_off": true,
    "pause_timeout_seconds": 30
  }
}
```

### 3. Manual Control Only
- Disable all automation features

```json
{
  "device_automation": {
    "auto_power_on": false,
    "auto_power_off": false,
    "pause_timeout_seconds": 0
  }
}
```

## Limitations

- Only controls the **first available device** in the system
- Requires device to support the power control functions
- Cannot control multiple devices simultaneously
- Pause timeout stops playback but doesn't power off the device directly (power off happens when playback stops)

## Future Enhancements

Potential improvements for future versions:

- Support for multiple devices
- Configurable device selection
- Volume fade-out before power off
- Configurable actions for different playback states
- Integration with power-saving schedules
- Device-specific automation rules
