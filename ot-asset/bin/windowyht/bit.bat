cl /c /O2 /MT /Gy- /GS- /GR- /EHa- /utf-8 ^
    /D UNICODE /D _UNICODE ^
    main.cpp /Fo:yhguit.obj
link /DLL /OUT:myapp.dll /FIXED /NXCOMPAT:NO /DYNAMICBASE:NO ^
     kernel32.lib user32.lib gdi32.lib comctl32.lib ^
     ole32.lib shell32.lib ^
     yhguit.obj
donut -i myapp.dll -a 2 -o winyht.bin