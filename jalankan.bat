@echo off
title Server Aplikasi Input dan Order Barang (Port 8090)
echo Menghentikan proses server lama (jika masih berjalan)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8090 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo Menjalankan Aplikasi Input dan Order Barang versi terbaru...
echo Buka browser di: http://127.0.0.1:8090
start http://127.0.0.1:8090
python "%~dp0app.py"
pause
