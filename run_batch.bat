@echo off
cd /d "%~dp0"
title Facebook Full Pipeline - Post + Boost
python run_batch.py --posts posts.txt --workers 2 %*
pause
