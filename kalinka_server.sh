#!/bin/bash
source /opt/kalinka/venv/bin/activate
exec python3 /opt/kalinka/run_server.py --config /opt/kalinka/kalinka_conf.yaml --state $HOME/.local/share/kalinka/kalinka_state.json
