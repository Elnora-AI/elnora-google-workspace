@echo off
rem Resolve a Python interpreter that has the deps, then run gw.py.
rem Order: plugin-local .venv (created by /gw-setup) -> python on PATH.
setlocal
set "PLUGIN_ROOT=%~dp0.."
if exist "%PLUGIN_ROOT%\.venv\Scripts\python.exe" (
  "%PLUGIN_ROOT%\.venv\Scripts\python.exe" "%PLUGIN_ROOT%\cli\gw.py" %*
) else (
  python "%PLUGIN_ROOT%\cli\gw.py" %*
)
exit /b %ERRORLEVEL%
