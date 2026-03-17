@echo off
cd /d C:\Users\hpint\Desktop\BuySell365_Bot

:loop
echo [%date% %time%] Starting bot.py >> restart_log.txt
python bot.py
echo [%date% %time%] bot.py exited with code %errorlevel%. Restarting in 10 seconds... >> restart_log.txt
timeout /t 10 /nobreak
goto loop
