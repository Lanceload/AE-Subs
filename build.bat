@echo off
cd /d "%~dp0"
echo Building AE Subs ...
python tools\make_icon.py || goto :error
python -m PyInstaller --noconfirm --clean --onedir --windowed --name "AE Subs" ^
  --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.png;assets" ^
  --collect-binaries ctranslate2 --collect-data faster_whisper ^
  --exclude-module tkinter ^
  --workpath build --distpath build\out --specpath build main.py || goto :error

rem Copy program files into "AE Subs", keeping models, cuda, presets and projects that live there.
robocopy "build\out\AE Subs" "AE Subs" /MIR /XD models cuda presets projects /NFL /NDL /NJH /NP
if errorlevel 8 goto :error
rem Remove the intermediate copy so only "AE Subs\AE Subs.exe" can be launched.
rmdir /s /q "build\out"
echo.
echo Done: "AE Subs\AE Subs.exe"
pause
exit /b 0

:error
echo BUILD FAILED
pause
exit /b 1
