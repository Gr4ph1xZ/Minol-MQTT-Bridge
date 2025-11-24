#!/bin/bash
set -e

echo "Starting Minol MQTT Bridge..."

# Start the python script (unbuffered output to see logs in HA)
python3 -u main.py