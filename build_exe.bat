@echo off
echo ============================================
echo   BuySell365 Pro — Build Windows Executable
echo ============================================
echo.

cd /d %~dp0

REM === Step 1: Install dependencies ===
echo [1/4] Installing build dependencies...
pip install pyinstaller pystray Pillow python-dotenv --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM === Step 2: Create icon from PNG ===
echo [2/4] Creating application icon...
python create_icon.py
if errorlevel 1 (
    echo WARNING: Could not create .ico file, using default icon.
)

REM === Step 3: Build with PyInstaller ===
echo [3/4] Building executable with PyInstaller...
echo     This may take several minutes...

pyinstaller --noconfirm ^
    --name "BuySell365 Pro" ^
    --windowed ^
    --icon "static\bull-logo.ico" ^
    --add-data "bot.py;." ^
    --add-data ".env;." ^
    --add-data "web_sync.py;." ^
    --add-data "cot_module.py;." ^
    --add-data "finbert_module.py;." ^
    --add-data "pandas_ta.py;." ^
    --add-data "translations.json;." ^
    --add-data "static;static" ^
    --add-data "templates;templates" ^
    --add-data "estado.json;." ^
    --hidden-import pystray ^
    --hidden-import PIL ^
    --hidden-import tkinter ^
    --hidden-import requests ^
    --hidden-import yfinance ^
    --hidden-import pandas ^
    --hidden-import numpy ^
    --hidden-import ta ^
    --hidden-import sklearn ^
    --hidden-import flask ^
    --hidden-import waitress ^
    --hidden-import dotenv ^
    --hidden-import MetaTrader5 ^
    --hidden-import psutil ^
    --collect-all ta ^
    --collect-all sklearn ^
    launcher.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. Check the output above.
    pause
    exit /b 1
)

REM === Step 4: Copy additional files to dist ===
echo [4/4] Copying configuration files...
if not exist "dist\BuySell365 Pro\logs" mkdir "dist\BuySell365 Pro\logs"

copy /y .env "dist\BuySell365 Pro\.env" >nul 2>&1
copy /y estado.json "dist\BuySell365 Pro\estado.json" >nul 2>&1
copy /y translations.json "dist\BuySell365 Pro\translations.json" >nul 2>&1
copy /y winning_trades.json "dist\BuySell365 Pro\winning_trades.json" >nul 2>&1
copy /y mt5_trades_sync.json "dist\BuySell365 Pro\mt5_trades_sync.json" >nul 2>&1
xcopy /y /e /i static "dist\BuySell365 Pro\static" >nul 2>&1
xcopy /y /e /i templates "dist\BuySell365 Pro\templates" >nul 2>&1

echo.
echo ============================================
echo   BUILD SUCCESSFUL!
echo   Output: dist\BuySell365 Pro\BuySell365 Pro.exe
echo ============================================
echo.
pause
