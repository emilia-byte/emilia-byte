@echo off
cd /d "%~dp0"
title Facebook Ad Booster - Batch Mode
python boost_batch.py --workers 2 %*
pause
