schema = {
    "type": "section",
    "name": "Kalinka",
    "description": "Kalinka configuration",
    "required": "yes",
    "elements": {
        "server": {
            "name": "Server",
            "description": "Name, interface and port of the server",
            "type": "section",
            "required": "yes",
            "elements": {
                "interface": {
                    "name": "Interface",
                    "description": "Network interface to bind",
                    "type": "string",
                    "required": "yes",
                    "readonly": "yes",
                },
                "port": {
                    "name": "Port",
                    "description": "Port number to listen on",
                    "type": "integer",
                    "required": "yes",
                    "readonly": "yes",
                },
                "service_name": {
                    "name": "Service name",
                    "description": "Name of the service",
                    "type": "string",
                    "required": "yes",
                },
                "log_level": {
                    "name": "Log level",
                    "description": "Logging level",
                    "type": "enum",
                    "values": ["debug", "info", "warning", "error"],
                    "required": "no",
                    "default": "info",
                },
            },
        },
        "output": {
            "name": "Output",
            "description": "Output configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "alsa": {
                    "name": "ALSA",
                    "description": "ALSA output settings",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "device": {
                            "name": "Device",
                            "description": "ALSA device name",
                            "type": "string",
                            "required": "yes",
                        },
                        "latency_ms": {
                            "name": "Latency",
                            "description": "Output latency in milliseconds",
                            "type": "integer",
                            "required": "no",
                            "default": 160,
                        },
                        "period_ms": {
                            "name": "Period",
                            "description": "Output period in milliseconds",
                            "type": "integer",
                            "required": "no",
                            "default": 40,
                        },
                    },
                }
            },
        },
        "input": {
            "name": "Input",
            "description": "Input configuration",
            "type": "section",
            "elements": {
                "http": {
                    "name": "HTTP",
                    "description": "HTTP input settings",
                    "type": "section",
                    "elements": {
                        "buffer_size": {
                            "name": "Buffer size",
                            "description": "HTTP buffer size in bytes",
                            "type": "integer",
                            "required": "no",
                            "default": 384000,
                        },
                        "chunk_size": {
                            "name": "Chunk size",
                            "description": "HTTP chunk size in bytes",
                            "type": "integer",
                            "required": "no",
                            "default": 768000,
                        },
                    },
                }
            },
        },
        "decoder": {
            "name": "Decoder",
            "description": "Decoders configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "flac": {
                    "name": "FLAC",
                    "description": "FLAC decoder settings",
                    "type": "section",
                    "required": "no",
                    "elements": {
                        "buffer_size": {
                            "name": "Buffer size",
                            "description": "FLAC buffer size in bytes",
                            "type": "integer",
                            "required": "no",
                            "default": 1536000,
                        }
                    },
                },
                "mpeg": {
                    "name": "MPEG",
                    "description": "MPEG decoder settings",
                    "type": "section",
                    "required": "no",
                    "elements": {
                        "buffer_size": {
                            "name": "Buffer size",
                            "description": "Buffer size in bytes",
                            "type": "integer",
                            "default": 176400,
                            "required": "no",
                        }
                    },
                },
            },
        },
        "fixups": {
            "name": "Fixups",
            "description": "Hacks to work around hardware issues",
            "type": "section",
            "required": "no",
            "elements": {
                "alsa_sleep_after_format_setup_ms": {
                    "name": "Sleep after new format",
                    "description": "ALSA sleep time after format setup in milliseconds",
                    "type": "integer",
                    "required": "no",
                    "default": 0,
                },
                "alsa_reopen_device_with_new_format": {
                    "name": "Always reopen device",
                    "description": "Reopen ALSA device with new format",
                    "type": "boolean",
                    "required": "no",
                    "default": False,
                },
            },
        },
        "addons": {
            "name": "Addons",
            "description": "Addons configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "device": {
                    "name": "Devices",
                    "description": "Device-specific addons",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "alsamixer": {
                            "name": "ALSA mixer",
                            "description": "ALSA mixer settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "name": {
                                    "name": "Name",
                                    "description": "Mixer control name",
                                    "type": "string",
                                    "required": "yes",
                                },
                                "volume_step_to_db": {
                                    "name": "Volume step",
                                    "description": "Volume step in dB",
                                    "type": "number",
                                    "required": "yes",
                                },
                            },
                        },
                        "musiccast": {
                            "name": "MusicCast",
                            "description": "MusicCast device settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "device_addr": {
                                    "name": "Address",
                                    "description": "MusicCast device address",
                                    "type": "string",
                                    "required": "yes",
                                },
                                "device_port": {
                                    "name": "Port",
                                    "description": "MusicCast device port",
                                    "type": "integer",
                                    "required": "yes",
                                },
                                "connected_input": {
                                    "name": "Connected input",
                                    "description": "Connected input name",
                                    "type": "string",
                                    "required": "yes",
                                },
                            },
                        },
                    },
                },
                "input_module": {
                    "name": "Input",
                    "description": "Input module settings",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "qobuz": {
                            "name": "Qobuz",
                            "description": "Qobuz input module settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "email": {
                                    "name": "Email",
                                    "description": "Qobuz login",
                                    "type": "string",
                                    "required": "yes",
                                },
                                "password_hash": {
                                    "name": "Password",
                                    "description": "Qobuz password",
                                    "type": "password",
                                    "required": "yes",
                                },
                                "format": {
                                    "name": "Quality",
                                    "description": "Audio quality",
                                    "type": "enum",
                                    "values": [
                                        "MP3 320kbps",
                                        "CD 16-bit 44.1KHz",
                                        "Hi-Res 24-bit 96KHz",
                                        "Hi-Res 24-bit 192KHz",
                                    ],
                                    "required": "no",
                                    "default": "Hi-Res 24-bit 192KHz",
                                },
                            },
                        }
                    },
                },
            },
        },
    },
}
