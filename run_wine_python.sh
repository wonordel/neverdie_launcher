#!/usr/bin/env bash
set -euo pipefail

export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"

PYTHON_EXE='C:\Python311\python.exe'
PROJECT_DIR='Z:\home\wonordel\program\chyzie_projectы\neverdie_launcher'
REQ_FILE='requirements.txt'
MAIN_FILE='main.py'

wine cmd /c "cd /d $PROJECT_DIR && $PYTHON_EXE -m pip install -r $REQ_FILE && $PYTHON_EXE $MAIN_FILE"