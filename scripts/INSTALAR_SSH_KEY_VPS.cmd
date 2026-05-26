@echo off
REM ============================================================
REM Instala la SSH key local en el VPS BuySell365
REM Doble-click para ejecutar. Te pedira la password root UNA vez.
REM ============================================================
title Instalar SSH Key en VPS BuySell365
color 0B

echo.
echo ============================================================
echo  Instalar SSH key en VPS root@208.73.204.188
echo ============================================================
echo.
echo Este script va a copiar tu clave publica al VPS para que
echo Claude pueda hacer deploys SIN contrasena en el futuro.
echo.
echo Cuando te lo pida, escribe la password root del VPS
echo  (la que diste a InterServer en el ticket).
echo Si la primera vez te pregunta "Are you sure...", escribe: yes
echo.
echo La password NO se guarda en ningun sitio, solo va al VPS.
echo.
pause

if not exist "%USERPROFILE%\.ssh\id_ed25519_buysell365.pub" (
  echo.
  echo ERROR: no encuentro la clave en %USERPROFILE%\.ssh\id_ed25519_buysell365.pub
  echo Avisa a Claude para regenerarla.
  pause
  exit /b 1
)

echo.
echo Conectando...
type "%USERPROFILE%\.ssh\id_ed25519_buysell365.pub" | ssh -o StrictHostKeyChecking=accept-new root@208.73.204.188 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && echo INSTALL_OK"

set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo ============================================================
  echo  LISTO. La clave esta instalada en el VPS.
  echo ============================================================
  echo.
  echo Probando conexion SIN password...
  ssh -i "%USERPROFILE%\.ssh\id_ed25519_buysell365" -o BatchMode=yes -o StrictHostKeyChecking=accept-new root@208.73.204.188 "echo OK_PASSWORDLESS_SSH && hostname && uptime"
  if "%ERRORLEVEL%"=="0" (
    echo.
    echo TODO OK. Ya puedes cerrar esta ventana y volver a Claude.
  ) else (
    echo.
    echo La clave se subio pero el test sin password fallo. Avisa a Claude.
  )
) else (
  echo ============================================================
  echo  FALLO la instalacion (codigo %RC%)
  echo ============================================================
  echo  Verifica:
  echo   - Que la password sea la correcta
  echo   - Que tienes internet
  echo   - Que el VPS este encendido en my.interserver.net
)
echo.
pause
