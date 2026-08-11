@echo off
call "E:\c++\VC\Auxiliary\Build\vcvarsall.bat" x64
cl /O2 /MT /LD win32proxy.cpp /Fe:win32.dll /link user32.lib kernel32.lib gdi32.lib shell32.lib advapi32.lib
del /q win32proxy.obj 2>nul
del /q win32.exp 2>nul
del /q win32.lib 2>nul
del /q win32.ilk 2>nul
del /q win32.pdb 2>nul