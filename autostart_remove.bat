@echo off
:: ============================================
:: BuySell365 Pro - Desactivar Auto-Inicio
:: ============================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Se necesitan permisos de Administrador.
    echo [!] Haz clic derecho en este archivo y selecciona "Ejecutar como administrador".
    pause
    exit /b 1
)

set "TASK_NAME=BuySell365_Pro_AutoStart"

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Auto-inicio desactivado exitosamente.
) else (
    echo [!] La tarea no existia o ya fue eliminada.
)

echo.
pause
