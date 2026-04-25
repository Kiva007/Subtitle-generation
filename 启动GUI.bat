@echo off
echo Startup Subtitle Generator GUI...
echo.

python gui_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Startup failed. Please check your Python environment and dependencies.
    echo.
    pause
)