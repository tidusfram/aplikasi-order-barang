@echo off
title Pembuat File EXE - Aplikasi Order Barang
cd /d "%~dp0"

echo ============================================================
echo  1. Memeriksa / Menginstal PyInstaller...
echo ============================================================
python -m pip install pyinstaller

echo.
echo ============================================================
echo  2. Menghentikan AplikasiOrderBarang.exe jika sedang berjalan...
echo ============================================================
taskkill /F /IM AplikasiOrderBarang.exe >nul 2>&1

echo.
echo ============================================================
echo  3. Membuat file AplikasiOrderBarang.exe...
echo ============================================================
python -m PyInstaller --noconfirm --onefile --name "AplikasiOrderBarang" --distpath "%~dp0" --workpath "%~dp0build_temp" --specpath "%~dp0build_temp" "%~dp0app.py"

echo.
echo ============================================================
echo  4. Membersihkan file sementara build...
echo ============================================================
rmdir /S /Q "%~dp0build_temp" >nul 2>&1

echo.
echo ============================================================
echo  SELESAI! File AplikasiOrderBarang.exe sudah siap digunakan.
echo  Data database (.db) dan tampilan (static/index.html) tetap aman!
echo ============================================================
pause
