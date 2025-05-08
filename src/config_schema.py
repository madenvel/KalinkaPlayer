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
                    "required": "no",
                    "elements": {
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
                                "auto_volume_correcton": {
                                    "name": "Auto volume correction",
                                    "description": "Automatically adjust loudness using replaygain",
                                    "type": "boolean",
                                    "required": "no",
                                    "default": False,
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
                            "enabled": "no",
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
                        },
                        "localfiles": {
                            "name": "Local Files",
                            "description": "Local music files input module settings",
                            "type": "section",
                            "required": "no",
                            "enabled": "yes",
                            "elements": {
                                "music_folders": {
                                    "name": "Music Folders",
                                    "description": "Paths to scan for music files (comma-separated)",
                                    "type": "string",
                                    "required": "yes",
                                },
                                "db_path": {
                                    "name": "Database Path",
                                    "description": "Path to the SQLite database file",
                                    "type": "string",
                                    "required": "yes",
                                    "default": "/var/lib/kalinka/localfiles.db",
                                },
                                "artwork_path": {
                                    "name": "Artwork Path",
                                    "description": "Path to store extracted artwork images",
                                    "type": "string",
                                    "required": "yes",
                                    "default": "/var/lib/kalinka/artwork",
                                },
                                "scan_interval_minutes": {
                                    "name": "Scan Interval",
                                    "description": "Interval between scans in minutes",
                                    "type": "integer",
                                    "required": "no",
                                    "default": 5,
                                },
                                "file_watch_enabled": {
                                    "name": "File Watching",
                                    "description": "Enable real-time file monitoring for changes",
                                    "type": "boolean",
                                    "required": "no",
                                    "default": True,
                                },
                                "enricher": {
                                    "name": "Enricher",
                                    "description": "Metadata enricher settings",
                                    "type": "section",
                                    "required": "no",
                                    "elements": {
                                        "enabled": {
                                            "name": "Enable Enricher",
                                            "description": "Enable metadata enrichment",
                                            "type": "boolean",
                                            "required": "no",
                                            "default": True,
                                        },
                                        "plugins": {
                                            "name": "Plugins",
                                            "description": "Metadata enrichment plugins",
                                            "type": "section",
                                            "required": "no",
                                            "elements": {
                                                "musicbrainz": {
                                                    "name": "MusicBrainz",
                                                    "description": "MusicBrainz metadata enrichment",
                                                    "type": "section",
                                                    "required": "no",
                                                    "elements": {
                                                        "enabled": {
                                                            "name": "Enable MusicBrainz",
                                                            "description": "Enable MusicBrainz metadata enrichment",
                                                            "type": "boolean",
                                                            "required": "no",
                                                            "default": False,
                                                        },
                                                        "match_threshold": {
                                                            "name": "Match Threshold",
                                                            "description": "Minimum score for accepting a match (0-100)",
                                                            "type": "integer",
                                                            "required": "no",
                                                            "default": 80,
                                                        },
                                                        "user_agent": {
                                                            "name": "User Agent",
                                                            "description": "User agent for MusicBrainz API requests",
                                                            "type": "string",
                                                            "required": "no",
                                                            "default": "Kalinka/1.0 (https://github.com/madenvel/KalinkaPlayer)",
                                                        },
                                                    },
                                                },
                                                "acoustid": {
                                                    "name": "AcoustID",
                                                    "description": "AcoustID audio fingerprinting",
                                                    "type": "section",
                                                    "required": "no",
                                                    "elements": {
                                                        "enabled": {
                                                            "name": "Enable AcoustID",
                                                            "description": "Enable AcoustID audio fingerprinting",
                                                            "type": "boolean",
                                                            "required": "no",
                                                            "default": False,
                                                        },
                                                        "api_key": {
                                                            "name": "API Key",
                                                            "description": "AcoustID API key (get from https://acoustid.org/api-key)",
                                                            "type": "string",
                                                            "required": "no",
                                                            "default": "",
                                                        },
                                                    },
                                                },
                                                "wikidata": {
                                                    "name": "Wikidata",
                                                    "description": "Wikidata enrichment for artist images",
                                                    "type": "section",
                                                    "required": "no",
                                                    "elements": {
                                                        "enabled": {
                                                            "name": "Enable Wikidata",
                                                            "description": "Enable Wikidata enrichment",
                                                            "type": "boolean",
                                                            "required": "no",
                                                            "default": False,
                                                        }
                                                    },
                                                },
                                                "deezer": {
                                                    "name": "Deezer",
                                                    "description": "Deezer enrichment for artist images (for personal use only)",
                                                    "type": "section",
                                                    "required": "no",
                                                    "elements": {
                                                        "enabled": {
                                                            "name": "Enable Deezer",
                                                            "description": "Enable Deezer enrichment for artist images",
                                                            "type": "boolean",
                                                            "required": "no",
                                                            "default": False,
                                                        }
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}
