#!/bin/sh
# collector.py를 백그라운드에서 실행
python collector.py &

# reliability_viewer.py를 포그라운드에서 실행 (컨테이너 유지)
python reliability_viewer.py
