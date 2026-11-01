#requires -Version 5.1
<#
    QOT VM (qotrun.dll) canonical build script   -    T-01

    Usage:
      powershell -ExecutionPolicy Bypass -File build.ps1
      powershell -ExecutionPolicy Bypass -File build.ps1 -Arch x86
      powershell -ExecutionPolicy Bypass -File build.ps1 -OutFile _verify.dll   # do NOT overwrite the live DLL
      powershell -ExecutionPolicy Bypass -File build.ps1 -KeepObj

    Constraints ( R9 / T-01):
      * No hardcoded VS path - locate via vswhere (old buildx64.bat hardcoded E:\c++\VC)
      * No pause - safe to call from CI
      * Print SHA256 of the artifact for reproducibility tracking
#>
param(
    [ValidateSet('x64', 'x86')]
    [string]$Arch = 'x64',
    [string]$OutFile,
    [switch]$KeepObj,
    #  T-32: extra preprocessor defines, e.g. -ExtraDefines "QOT_VM_PROFILE"
    [string]$ExtraDefines = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path (Join-Path (Join-Path $here '..') '..') '..')).Path
$invocationDir = Get-Location   # relative -OutFile resolves against the CALLER's cwd
Push-Location $here

function Fail {
    param([string]$msg)
    Pop-Location
    Write-Host "[BUILD FAILED] $msg" -ForegroundColor Red
    exit 1
}

# ---------- 1. locate MSVC ----------
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsPath = $null
if (Test-Path $vswhere) {
    $vsPath = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null | Select-Object -First 1)
}
if (-not $vsPath -or -not (Test-Path $vsPath)) {
    foreach ($c in @('C:\Program Files\Microsoft Visual Studio\2022', 'C:\Program Files\Microsoft Visual Studio\2019')) {
        if (Test-Path $c) {
            $d = Get-ChildItem -Path $c -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
            if ($d) { $vsPath = $d.FullName; break }
        }
    }
}
if (-not $vsPath -or -not (Test-Path $vsPath)) { Fail 'Visual Studio not found (vswhere + default paths both missed).' }

$vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvarsall.bat'
if (-not (Test-Path $vcvars)) { Fail "VS found but vcvarsall.bat missing: $vcvars" }
Write-Host "[1/4] VS     : $vsPath"
Write-Host "[1/4] target : $Arch"

$dump = cmd /c "`"$vcvars`" $Arch >nul 2>&1 && set"
if ($LASTEXITCODE -ne 0) { Fail 'vcvarsall.bat failed' }
foreach ($line in $dump) {
    $idx = $line.IndexOf('=')
    if ($idx -gt 0) {
        $k = $line.Substring(0, $idx)
        $v = $line.Substring($idx + 1)
        if ($k -and $k.IndexOf(' ') -lt 0) {
            Set-Item -Path "Env:$k" -Value $v -ErrorAction SilentlyContinue
        }
    }
}

# ---------- 3. compile + link ----------
# NOTE: the RPN super-instructor (rpn_executor.* / assets/rpn_executor_*.asm)
#       was removed per  D-03, so there is no ml64/ml step anymore.
#       Keep this file pure ASCII: PowerShell 5.1 reads BOM-less .ps1 as ANSI.
$sources = @(
    'vm_dll_wrapper.cpp',
    'vm_executor.cpp',
    'vm_profile.cpp',
    'builtin.cpp',
    'shellcode_loader.cpp',
    'module_loader.cpp',
    'http_client.cpp',
    'gui_module.cpp',
    'assets\wingui\renderer.cpp',
    'assets\wingui\modern_window.cpp'
)
foreach ($s in $sources) {
    if (-not (Test-Path $s)) { Fail "missing source: $s (if assets\wingui is empty, run: git checkout -- ot-asset/c/vmcshit/assets/wingui/)" }
}

$libs = @(
    'user32.lib', 'gdi32.lib', 'ole32.lib', 'oleaut32.lib', 'uuid.lib',
    'comctl32.lib', 'dwrite.lib', 'd2d1.lib', 'windowscodecs.lib',
    'imm32.lib', 'winmm.lib', 'shell32.lib', 'oleacc.lib', 'comdlg32.lib'
)

if (-not $OutFile) { $OutFile = Join-Path $root 'qotrun.dll' }
$outAbs = if ([System.IO.Path]::IsPathRooted($OutFile)) { $OutFile } else { Join-Path $invocationDir $OutFile }

$clArgs = @(
    '/nologo', '/EHsc', '/std:c++17', '/utf-8', '/MT', '/GL', '/O2', '/Ob2', '/Oi', '/Oy', '/Ot', '/GT', '/Gy', '/GF', '/Gw',
    '/D_CRT_SECURE_NO_WARNINGS', '/D_CRT_NONSTDC_NO_WARNINGS', '/DBUILDING_VM_DLL', '/DNDEBUG',
    '/Iassets\wingui'
)
if ($Arch -eq 'x64') { $clArgs += '/arch:AVX2' } else { $clArgs += '/arch:IA32' }
$clArgs += '/fp:fast'
if ($ExtraDefines) {
    foreach ($d in ($ExtraDefines -split '[,; ]+' | Where-Object { $_ })) {
        $clArgs += "/D$d"
    }
    Write-Host "[2/4] extra defines: $ExtraDefines"
}
$clArgs += "/Fe`"$outAbs`""
$clArgs += '/LD'
$clArgs += $sources
$clArgs += '/link'
$clArgs += '/LTCG'
$clArgs += $libs

Write-Host "[3/4] compiling -> $outAbs"
& cl @clArgs
if ($LASTEXITCODE -ne 0) { Fail 'cl.exe failed' }

# ---------- 4. cleanup + fingerprint ----------
Remove-Item ([System.IO.Path]::ChangeExtension($outAbs, '.exp')) -ErrorAction SilentlyContinue
Remove-Item ([System.IO.Path]::ChangeExtension($outAbs, '.lib')) -ErrorAction SilentlyContinue
if (-not $KeepObj) { Remove-Item *.obj -ErrorAction SilentlyContinue }

Write-Host "[4/4] done"
if (Test-Path $outAbs) {
    $sha = (Get-FileHash -Algorithm SHA256 -Path $outAbs).Hash.ToLowerInvariant()
    $size = (Get-Item $outAbs).Length
    Write-Host "       size   : $size bytes"
    Write-Host "       sha256 : $sha"
}

Pop-Location
exit 0
