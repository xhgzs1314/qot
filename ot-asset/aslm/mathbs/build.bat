@echo off
uasm -win64 math_ops.asm -Fo:math_ops.obj
if errorlevel 1 (
    echo 编译失败！
    pause
    exit /b 1
)
link /DLL /ENTRY:DllMain /NODEFAULTLIB /DEF:math_ops.def /OUT:math_ops.dll math_ops.obj

if errorlevel 1 (
    echo 链接失败！
    pause
    exit /b 1
)
dir math_ops.dll
pause