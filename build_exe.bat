@echo off
rem Builds dist\AquariaWish - Pulls Tracker\AquariaWish.exe (a folder build). Run it from this folder, with Python 3.10 to 3.13 (64 bit) installed.
rem The build tools go in .venv-build (inside this folder): the Python that is installed on the computer is not changed.
setlocal
cd /d "%~dp0"

rem The build replaces the whole dist\AquariaWish - Pulls Tracker folder (PyInstaller --noconfirm), and the pull database lives in its data folder.
if exist "dist\AquariaWish - Pulls Tracker\data\pulls.sqlite" (
    echo.
    echo dist\AquariaWish - Pulls Tracker\data\pulls.sqlite exists: the build would delete it.
    echo Move the folder dist\AquariaWish - Pulls Tracker\data somewhere else, then run this file again.
    exit /b 3
)

if not exist ".venv-build\Scripts\python.exe" (
    python -m venv .venv-build || goto :fail
)
set "PY=%CD%\.venv-build\Scripts\python.exe"

"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r requirements.txt -r requirements-build.txt || goto :fail

"%PY%" -m PyInstaller --noconfirm --clean pulls_tracker.spec || goto :fail

copy /y dist_README.txt "dist\AquariaWish - Pulls Tracker\README.txt" >nul || goto :fail
copy /y AquariaWish.exe.config "dist\AquariaWish - Pulls Tracker\AquariaWish.exe.config" >nul || goto :fail
copy /y LICENSE "dist\AquariaWish - Pulls Tracker\LICENSE.txt" >nul || goto :fail
copy /y THIRD_PARTY_NOTICES.md "dist\AquariaWish - Pulls Tracker\THIRD_PARTY_NOTICES.md" >nul || goto :fail
echo.
echo Self check of the build (a window shows the result; close it to go on):
rem The program has no console, so cmd does not wait for it unless "start /wait" is used; that also gives its exit code.
start "" /wait "dist\AquariaWish - Pulls Tracker\AquariaWish.exe" --selfcheck
if errorlevel 1 goto :selfcheck_failed
rem The folder is made to be shared: it must not carry the selfcheck.txt of this computer (it lists local paths) or any other data.
rem The program creates "data" at its first start.
if exist "dist\AquariaWish - Pulls Tracker\data" rmdir /s /q "dist\AquariaWish - Pulls Tracker\data"
echo.
echo Built: %CD%\dist\AquariaWish - Pulls Tracker\AquariaWish.exe
echo Copy the whole dist\AquariaWish - Pulls Tracker folder to where you want to keep the program.
exit /b 0

:selfcheck_failed
echo.
echo The program was built but its self check found problems. Read dist\AquariaWish - Pulls Tracker\data\selfcheck.txt.
exit /b 2

:fail
echo.
echo The build failed. Read the messages above.
exit /b 1
