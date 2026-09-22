@echo off
setlocal
title Push IQ Android to GitHub

set REPO_URL=https://github.com/smxderxl/iqplay-android.git
set REPO_NAME=iqplay-android

echo ============================================================
echo   IQ Signal Analyzer (Android) - Push to GitHub
echo   Repo: %REPO_URL%
echo ============================================================
echo.

echo [1/4] First, create an EMPTY repo named "%REPO_NAME%"
echo       Open this URL and click "Create repository":
echo.
echo           https://github.com/new
echo.
echo       Do NOT add README / .gitignore / license.
echo.
echo       Then come back here and press any key to continue.
echo.
pause
echo.

cd /d "%~dp0"
echo [2/4] Working dir: %CD%
echo.

echo [3/4] Configuring remote...
git remote remove origin 1>nul 2>nul
git remote add origin %REPO_URL%
git branch -M main
echo.
git remote -v
echo.

echo [4/4] Pushing to GitHub...
echo.
echo   A login window may pop up. Enter:
echo       Username : smxderxl
echo       Password : your Personal Access Token (NOT your account password)
echo.
echo   No token yet? Create one here (tick the "repo" scope):
echo       https://github.com/settings/tokens/new
echo.
pause

git push -u origin main

if errorlevel 1 goto FAILED

echo.
echo ============================================================
echo   PUSH SUCCESSFUL!
echo.
echo   Watch the cloud build here:
echo     https://github.com/smxderxl/%REPO_NAME%/actions
echo.
echo   When it finishes, download the APK from "Artifacts".
echo ============================================================
goto END

:FAILED
echo.
echo ============================================================
echo   PUSH FAILED. Common causes:
echo.
echo   1) Repo not created, or wrong name (must be %REPO_NAME%)
echo   2) Used account password instead of Access Token
echo   3) Token missing the "repo" scope
echo   4) Network glitch - just run this script again
echo ============================================================

:END
echo.
pause
endlocal
