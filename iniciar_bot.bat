@echo off
title Bot ArgenMU - Control de Puntos y Darks
chcp 65001 > nul
cd /d "%~dp0"
echo ======================================================
echo    JINETE BOT - ARGENMU (MODO AUTO-REINICIO)
echo ======================================================
echo.
:loop
echo [%date% %time%] Iniciando Jinete Bot...
python bot.py
echo.
echo [AVISO] El bot se detuvo o se desconecto. Reiniciando en 5 segundos...
timeout /t 5 /nobreak > nul
goto loop
