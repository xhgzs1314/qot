@echo off
rem ============================================================
rem  QOT frontend Cython extension build (FE3, 2026-11-04)
rem  1) vcvarsall x64  - MSVC env for Python 3.9 official build
rem  2) setup_frontend.py build_ext --inplace (Cython -> .pyd)
rem  3) _frontend_manifest.py - write freshness manifest
rem  NOTE: ASCII-only comments in this file (GBK cmd may split
rem        non-ASCII bytes into garbage commands).
rem ============================================================
setlocal
cd /d "%~dp0"

set VCVARS=E:\c++\VC\Auxiliary\Build\vcvarsall.bat
if not exist "%VCVARS%" (
  echo [frontend-build] vcvarsall.bat not found: %VCVARS%
  exit /b 1
)

call "%VCVARS%" x64
if errorlevel 1 (
  echo [frontend-build] vcvarsall failed
  exit /b 1
)

python -c "import Cython; print('[frontend-build] Cython', Cython.__version__)"
python setup_frontend.py build_ext --inplace
if errorlevel 1 (
  echo [frontend-build] build_ext FAILED - manifest NOT written
  exit /b 1
)

python _frontend_manifest.py
if errorlevel 1 (
  echo [frontend-build] manifest step FAILED
  exit /b 1
)

echo [frontend-build] done.
endlocal
