@echo off
cd /d "%~dp0"
title Sync Profiles from Multilogin
python sync_profiles.py
pause
