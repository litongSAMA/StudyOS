@echo off
chcp 65001 >nul
"%~dp0StudyOS.exe" --self-test
set "STUDYOS_TEST_RESULT=%ERRORLEVEL%"
echo.
if "%STUDYOS_TEST_RESULT%"=="0" (echo StudyOS self-test PASSED.) else (echo StudyOS self-test FAILED. Copy the error above.)
pause
exit /b %STUDYOS_TEST_RESULT%
