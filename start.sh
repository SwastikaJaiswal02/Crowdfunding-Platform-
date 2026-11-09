#!/bin/sh
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then python3 server.py
elif command -v python >/dev/null 2>&1; then python server.py
else echo "Python is not installed. Get it from https://www.python.org/downloads/"; fi
