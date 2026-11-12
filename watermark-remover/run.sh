#!/bin/sh
# 사용법: ./run.sh 사진폴더
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 wmremove.py "$1" "${1%/}_cleaned"
