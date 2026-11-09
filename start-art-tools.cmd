@echo off
rem Starts the Hearthdelve art tools, each in its own minimized window. Safe to run again: anything already
rem running is left alone.
rem   ComfyUI  http://127.0.0.1:8188   (FLUX / TRELLIS / BiRefNet; needed for generating, not for browsing)
rem   Studio   http://127.0.0.1:4321   every web tool (the links are printed at the end)
rem The Studio window runs the supervisor (hdart/server/supervisor.py): it restarts the server by itself when the code
rem changes, after the running job, and on `uv run --no-sync hdart op call studio.restart`. Log: work\studio.log.
setlocal
cd /d "%~dp0"
if "%COMFYUI%"=="" set "COMFYUI=P:\Tools\ComfyUI"

curl -s -o nul http://127.0.0.1:8188/system_stats
if errorlevel 1 (
    echo Starting ComfyUI from %COMFYUI% ...
    start "ComfyUI :8188" /min cmd /c ""%COMFYUI%\start-comfyui.cmd""
) else (
    echo ComfyUI already running.
)

curl -s -o nul http://127.0.0.1:4321/api/ws/jobs
if errorlevel 1 (
    echo Starting the Studio, supervised: it restarts itself when the code changes ...
    start "Hearthdelve Studio :4321" /min cmd /c "uv run hdart studio --no-browser"
) else (
    echo Studio already running.
)

echo.
echo   Character Workshop  http://127.0.0.1:4321/workshop
echo   Item Workshop       http://127.0.0.1:4321/items
echo   Workshop Editor     http://127.0.0.1:4321/editor
echo   Dungeon Forge       http://127.0.0.1:4321/dungeon
echo   Limner              http://127.0.0.1:4321/limner   (icons, portraits, overnight batches)
echo   ComfyUI         http://127.0.0.1:8188  (first generation after a start loads the models: ~1 min)
