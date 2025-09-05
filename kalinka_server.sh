#!/bin/bash
source /opt/kalinka/venv/bin/activate
exec python3 /opt/kalinka/run_server.py --config /etc/kalinka/kalinka_conf.cfg --state $HOME/.local/share/kalinka/kalinka_state.json
