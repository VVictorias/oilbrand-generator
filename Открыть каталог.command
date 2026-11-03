#!/bin/zsh
cd "$(dirname "$0")" || exit 1
(sleep 2; open 'http://127.0.0.1:8765') &
python3 brandgen.py serve
