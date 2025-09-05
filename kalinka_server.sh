#!/bin/bash
source /opt/kalinka/venv/bin/activate
exec kalinka-server --config /etc/kalinka/kalinka_conf.cfg --state $HOME/.local/share/kalinka/kalinka_state.json
