@echo off
REM Ninja Zenshin Tracker - Script de inicio rapido (Windows)
REM Doble clic para ejecutar

cd /d "%~dp0"

echo ==============================================
echo   Ninja Zenshin Tracker v23
echo   Modo Sabio de los Seis Caminos
echo ==============================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
    echo Iniciando tracker en http://localhost:8000 ...
    echo Presiona Ctrl+C para detener.
    echo.
    python zenshin_tracker.py %*
) else (
    echo ERROR: Python no esta instalado.
    echo Descargalo desde https://python.org y marca "Add to PATH"
    pause
)
