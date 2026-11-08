@echo off
chcp 65001 >nul
title PlaylistTransfer
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo No encontre Python. Instalalo desde https://www.python.org/downloads/ y volve a abrir este archivo.
    pause
    exit /b 1
)

py -c "import tidalapi, ytmusicapi, webview" >nul 2>nul
if errorlevel 1 (
    echo Instalando lo necesario. Solo pasa la primera vez, puede tardar un minuto...
    py -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r requirements.txt
    if errorlevel 1 (
        echo.
        echo No se pudo instalar. Revisa tu conexion a internet y volve a intentar.
        pause
        exit /b 1
    )
)

start "" pyw -m app
