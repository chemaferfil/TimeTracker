@echo off
REM Activar entorno virtual
call .\venv\Scripts\activate

REM Establecer el archivo principal
set FLASK_APP=src/main.py

REM Ejecutar el servidor Flask
flask run

pause
