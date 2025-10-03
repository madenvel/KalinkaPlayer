#!/bin/bash
exec /opt/kalinka/venv/bin/python3 /opt/kalinka/venv/bin/kalinka-server --config /etc/kalinka/kalinka_conf.cfg --state $HOME/.local/share/kalinka/kalinka_state.json
