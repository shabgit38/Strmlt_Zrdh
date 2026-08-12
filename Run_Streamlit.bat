@echo off
setlocal

cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo ERROR: uv was not found on PATH.
    echo Install uv, then try again.
    pause
    exit /b 1
)

echo Starting the Streamlit app...
uv run --frozen streamlit run "getHoldings.py"

if errorlevel 1 (
    echo.
    echo Streamlit stopped with an error.
    pause
)

endlocal
