@echo off
:: ============================================
:: BuySell365 Pro - Configurar Auto-Inicio
:: Crea una tarea en Windows Task Scheduler
:: para que el bot arranque automaticamente
:: al encender el PC.
:: ============================================
:: Requiere permisos de Administrador
:: ============================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Se necesitan permisos de Administrador.
    echo [!] Haz clic derecho en este archivo y selecciona "Ejecutar como administrador".
    pause
    exit /b 1
)

set "BOT_DIR=%~dp0"
set "TASK_NAME=BuySell365_Pro_AutoStart"
set "PYTHON_EXE="

:: Buscar Python
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('where python') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)
:found_python

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python no encontrado. Instalalo primero.
    pause
    exit /b 1
)

echo =============================================
echo  BuySell365 Pro - Auto-Inicio Windows
echo =============================================
echo.
echo Directorio: %BOT_DIR%
echo Python: %PYTHON_EXE%
echo.

:: Eliminar tarea existente si hay
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Crear tarea: arrancar el launcher al iniciar sesion, con retraso de 30s
schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHON_EXE%\" \"%BOT_DIR%launcher.py\"" /sc onlogon /delay 0000:30 /rl highest /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Tarea creada exitosamente!
    echo [OK] BuySell365 Pro arrancara automaticamente al iniciar sesion.
    echo [OK] Nombre de la tarea: %TASK_NAME%
    echo.
    echo Para desactivar: ejecuta autostart_remove.bat
) else (
    echo.
    echo [ERROR] No se pudo crear la tarea. Verifica permisos.
)

echo.
pause
