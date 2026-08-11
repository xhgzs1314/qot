@echo off
call "E:\c++\VC\Auxiliary\Build\vcvarsall.bat" x64
cd /d "%~dp0"

echo ========================================
echo Building x64 DLL with LTO + O3...
echo ========================================
set OUTPUT_DIR=..\..\..
set EXDUIR_LIB=assets\wingui\

echo Assembling rpn_executor_x64.asm...
ml64 /c /Fo rpn_executor_x64.obj assets\rpn_executor_x64.asm
if %errorlevel% neq 0 goto error

cl /nologo /EHsc /std:c++17 /utf-8 /MT /GL /O2 /Ob2 /Oi /Oy /Ot /GT /Gy /GF /Gw /arch:AVX2 /fp:fast ^
    /D_CRT_SECURE_NO_WARNINGS /D_CRT_NONSTDC_NO_WARNINGS /DBUILDING_VM_DLL ^
    /DNDEBUG ^
    /I"assets\wingui" ^
    /Fe"%OUTPUT_DIR%\qotrun.dll" /LD ^
    vm_dll_wrapper.cpp ^
    vm_executor.cpp ^
    builtin.cpp ^
    shellcode_loader.cpp ^
    module_loader.cpp ^
    http_client.cpp ^
    rpn_executor.cpp ^
    gui_module.cpp ^
    %EXDUIR_LIB%renderer.cpp ^
    %EXDUIR_LIB%modern_window.cpp ^
    rpn_executor_x64.obj ^
    /link /LTCG ^
    user32.lib gdi32.lib ole32.lib oleaut32.lib uuid.lib comctl32.lib ^
    dwrite.lib d2d1.lib windowscodecs.lib imm32.lib winmm.lib ^
    shell32.lib oleacc.lib comdlg32.lib

if %errorlevel% neq 0 goto error

echo.
echo ========================================
echo Built successfully
echo DLL output: %OUTPUT_DIR%\qotrun.dll
echo ========================================
del *.obj 2>nul
del %OUTPUT_DIR%\qotrun.exp 2>nul
del %OUTPUT_DIR%\qotrun.lib 2>nul

goto end

:error
echo.
echo Build failed with error: %errorlevel%
echo.

:end
pause