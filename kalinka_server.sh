#!/bin/bash
source /opt/kalinka/venv/bin/activate
exec python3 /opt/kalinka/run_server.py --config /opt/kalinka/kalinka_conf.yaml
