@echo off
chcp 65001 > nul
call "E:\c++\VC\Auxiliary\Build\vcvarsall.bat" x64

cl /EHsc /MD /utf-8 ^
    main.cpp ^
    renderer.cpp ^
    modern_window.cpp ^
    user32.lib kernel32.lib gdi32.lib ^
    d2d1.lib dwrite.lib dxguid.lib comctl32.lib ^
    /Fe:ModernGUI.exe

if %errorlevel% == 0 (
    echo Build successful!
    del *.obj 2>nul
) else (
    echo Build failed!
    pause
)