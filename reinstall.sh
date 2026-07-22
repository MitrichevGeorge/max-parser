#!/usr/bin/env bash

set -euo pipefail
DIR="env/"
[ -d "$DIR" ] && rm -rf "$DIR"

python -m venv env
source env/bin/activate
pip install -r requirements.txt