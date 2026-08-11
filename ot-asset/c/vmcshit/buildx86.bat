@echo off
call "E:\c++\VC\Auxiliary\Build\vcvarsall.bat" x86

echo ========================================
echo Building x86 DLL...
echo ========================================

set OUTPUT_DIR=..\..\..
set EXDUIR_LIB=assets\wingui\

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

echo Assembling rpn_executor_x86.asm...
ml /c /coff /Fo rpn_executor_x86.obj assets\rpn_executor_x86.asm
if %errorlevel% neq 0 goto error

cl /EHsc /std:c++17 /utf-8 /MT /O2 /Ob2 /Oi /Gy /GF /Gw /arch:IA32 /fp:fast ^
    /D_CRT_SECURE_NO_WARNINGS /D_CRT_NONSTDC_NO_WARNINGS /DBUILDING_VM_DLL ^
    /I"assets\wingui" ^
    /Fe:"%OUTPUT_DIR%\qotrun.dll" /LD ^
    vm_dll_wrapper.cpp vm_executor.cpp ^
    builtin.cpp shellcode_loader.cpp module_loader.cpp http_client.cpp ^
    rpn_executor.cpp gui_module.cpp ^
    rpn_executor_x86.obj ^
    %EXDUIR_LIB%renderer.cpp ^
    %EXDUIR_LIB%modern_window.cpp ^
    user32.lib gdi32.lib ole32.lib oleaut32.lib uuid.lib comctl32.lib ^
    dwrite.lib d2d1.lib windowscodecs.lib imm32.lib winmm.lib ^
    shell32.lib oleacc.lib comdlg32.lib

if %errorlevel% neq 0 goto error

del *.obj 2>nul
del "%OUTPUT_DIR%\qotrun.exp" 2>nul
del "%OUTPUT_DIR%\qotrun.lib" 2>nul

echo.
echo ========================================
echo x86 DLL built successfully!
echo Output: %OUTPUT_DIR%\qotrun.dll
echo ========================================
goto end

:error
echo Build failed with error: %errorlevel%

:end
pause