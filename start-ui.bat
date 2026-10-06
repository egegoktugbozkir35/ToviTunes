@echo off
setlocal
cd /d "%~dp0"
set "TOVITUNES_UI_CONFIG=config.yaml"
if not exist "%TOVITUNES_UI_CONFIG%" set "TOVITUNES_UI_CONFIG=config.example.yaml"
uv run --locked --extra web --extra youtube --extra video-render python -m tovitunes.web.launcher --config "%TOVITUNES_UI_CONFIG%" %*
