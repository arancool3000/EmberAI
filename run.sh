#!/bin/bash
# Launch Ember. Prefers the uv-managed .venv created by Ember.command/install.sh;
# falls back to the system python3.
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
    exec .venv/bin/python main.py
fi
exec python3 main.py
