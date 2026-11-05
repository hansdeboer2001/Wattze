@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_wattze.ps1" %*
