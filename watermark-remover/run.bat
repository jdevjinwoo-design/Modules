@echo off
chcp 65001 >nul
rem 사진 폴더를 이 파일 위로 끌어다 놓으면 "폴더명_cleaned" 에 결과가 저장됩니다.
if "%~1"=="" (
  echo 사진 폴더를 run.bat 아이콘 위로 끌어다 놓으세요.
  pause
  exit /b 1
)
where python >nul 2>nul || (
  echo Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요. 설치 화면에서 "Add python.exe to PATH" 체크.
  pause
  exit /b 1
)
python -m pip install -q -r "%~dp0requirements.txt"
python "%~dp0wmremove.py" "%~1" "%~1_cleaned"
echo.
echo 결과 폴더: %~1_cleaned
pause
