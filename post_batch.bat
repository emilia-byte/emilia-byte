@echo off
cd /d "%~dp0"
title Facebook Page Publisher - Batch Mode
python post_batch.py --posts posts.txt --workers 2 %*
pause
