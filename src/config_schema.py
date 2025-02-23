schema = """
{
    "type": "section",
    "description": "Kalinka configuration",
    "required": "yes",
    "elements": {
        "server": {
            "description": "Server configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "interface": {
                    "description": "Network interface to bind",
                    "type": "string",
                    "required": "yes"
                },
                "port": {
                    "description": "Port number to listen on",
                    "type": "integer",
                    "required": "yes"
                },
                "service_name": {
                    "description": "Name of the service",
                    "type": "string",
                    "required": "yes"
                },
                "log_level": {
                    "description": "Logging level",
                    "type": "enum",
                    "values": ["debug", "info", "warning", "error"],
                    "required": "no",
                    "default": "info"
                }
            }
        },
        "output": {
            "description": "Output configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "alsa": {
                    "description": "ALSA output settings",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "device": {
                            "description": "ALSA device name",
                            "type": "string",
                            "required": "yes"
                        },
                        "latency_ms": {
                            "description": "Output latency in milliseconds",
                            "type": "integer",
                            "required": "no",
                            "default": 160
                        },
                        "period_ms": {
                            "description": "Output period in milliseconds",
                            "type": "integer",
                            "required": "no",
                            "default": 40
                        }
                    }
                }
            }
        },
        "input": {
            "description": "Input configuration",
            "type": "section",
            "elements": {
                "http": {
                    "description": "HTTP input settings",
                    "type": "section",
                    "elements": {
                        "buffer_size": {
                            "description": "HTTP buffer size",
                            "type": "integer",
                            "required": "no",
                            "default": 384000
                        },
                        "chunk_size": {
                            "description": "HTTP chunk size",
                            "type": "integer",
                            "required": "no",
                            "default": 768000
                        }
                    }
                }
            }
        },
        "decoder": {
            "description": "Decoder configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "flac": {
                    "description": "FLAC decoder settings",
                    "type": "section",
                    "required": "no",
                    "elements": {
                        "buffer_size": {
                            "description": "FLAC buffer size",
                            "type": "integer",
                            "required": "no"
                        }
                    }
                }
            }
        },
        "fixups": {
            "description": "Fixup settings",
            "type": "section",
            "required": "no",
            "elements": {
                "alsa_sleep_after_format_setup_ms": {
                    "description": "ALSA sleep time after format setup in milliseconds",
                    "type": "integer",
                    "required": "no",
                    "default": 0
                },
                "alsa_reopen_device_with_new_format": {
                    "description": "Reopen ALSA device with new format",
                    "type": "boolean",
                    "required": "no",
                    "default": false
                }
            }
        },
        "addons": {
            "description": "Addons configuration",
            "type": "section",
            "required": "yes",
            "elements": {
                "device": {
                    "description": "Device-specific addons",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "alsamixer": {
                            "description": "ALSA mixer settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "name": {
                                    "description": "Mixer control name",
                                    "type": "string",
                                    "required": "yes"
                                },
                                "volume_step_to_db": {
                                    "description": "Volume step in dB",
                                    "type": "number",
                                    "required": "yes"
                                }
                            }
                        },
                        "musiccast": {
                            "description": "MusicCast device settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "device_addr": {
                                    "description": "MusicCast device address",
                                    "type": "string",
                                    "required": "yes"
                                },
                                "device_port": {
                                    "description": "MusicCast device port",
                                    "type": "integer",
                                    "required": "yes"
                                },
                                "connected_input": {
                                    "description": "Connected input type",
                                    "type": "string",
                                    "required": "yes"
                                }
                            }
                        }
                    }
                },
                "input_module": {
                    "description": "Input module settings",
                    "type": "section",
                    "required": "yes",
                    "elements": {
                        "qobuz": {
                            "description": "Qobuz input module settings",
                            "type": "section",
                            "required": "no",
                            "elements": {
                                "email": {
                                    "description": "Qobuz account email",
                                    "type": "string",
                                    "required": "yes"
                                },
                                "password_hash": {
                                    "description": "Qobuz account password hash",
                                    "type": "password",
                                    "required": "yes"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
"""
