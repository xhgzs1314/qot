import os
import subprocess
import sys
from pathlib import Path
def pack_qtc_to_exe_v3(qtc_path: str, icon_path: str = None, extra_files: list = None, output_path: str = None):
    print("=" * 50)
    print("QTC Builder V3")
    print("=" * 50)
    print()
    
    print("[1/5] 检查打包环境...")
    script_dir = Path(__file__).parent.absolute()
    
    qotbuild_path = None
    for path in [
        script_dir / "ot-asset" / "qotbuild.exe",
        script_dir.parent / "qotbuild.exe",
        Path(os.getcwd()) / "qotbuild.exe",
    ]:
        if path.exists():
            qotbuild_path = str(path.absolute())
            break
    
    if not qotbuild_path:
        raise FileNotFoundError("找不到 qotbuild.exe")
    print(f"      ✓ 找到构建工具: {qotbuild_path}")
    template_path = None
    for path in [
        script_dir / "ot-asset" / "template.exe",
        script_dir.parent / "template.exe",
        Path(os.getcwd()) / "template.exe",
    ]:
        if path.exists():
            template_path = str(path.absolute())
            break
    
    if not template_path:
        raise FileNotFoundError("找不到模板文件 template.exe")
    print(f"      ✓ 找到模板文件: {template_path}")
    
    dll_path = None
    for path in [
        script_dir / "qotrun.dll",
        script_dir.parent / "qotrun.dll",
        Path(os.getcwd()) / "qotrun.dll",
    ]:
        if path.exists():
            dll_path = str(path.absolute())
            break
    
    if not dll_path:
        raise FileNotFoundError("找不到 qotrun.dll")
    print(f"      ✓ 找到运行时库: {dll_path}")
    
    print("\n[2/5] 准备输出路径...")
    if output_path is None:
        output_dir = Path(os.getcwd()) / "build"
        base_name = Path(qtc_path).stem
        output_exe = output_dir / f"{base_name}.exe"
    else:
        output_exe = Path(output_path)
        if output_exe.suffix.lower() != '.exe':
            output_exe = output_exe.with_suffix('.exe')
        output_dir = output_exe.parent
    
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"      ✓ 输出文件: {output_exe}")
    
    print("\n[3/5] 验证输入文件...")
    if not os.path.exists(qtc_path):
        raise FileNotFoundError(f"QTC 文件不存在: {qtc_path}")
    print(f"      ✓ 脚本文件: {qtc_path}")
    
    print("\n[4/5] 执行打包...")
    resources = [
        f"{os.path.abspath(qtc_path)}:script.qtc",
        f"{os.path.abspath(dll_path)}:qotrun.dll"
    ]
    
    if extra_files:
        for extra in extra_files:
            if os.path.exists(extra):
                name = os.path.basename(extra)
                resources.append(f"{os.path.abspath(extra)}:{name}")
                print(f"        包含资源: {name}")
            else:
                print(f"        跳过不存在的文件: {extra}")
    
    cmd = [qotbuild_path, template_path, str(output_exe.absolute())]
    cmd.extend(resources)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        if result.stdout:
            print(result.stdout)
        
        if result.returncode != 0:
            if result.stderr:
                print(f"\n错误详情:\n{result.stderr}")
            raise RuntimeError(f"打包失败，返回码: {result.returncode}")
        
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"执行打包程序失败: {e}")
    
    print("\n[5/5] 验证打包结果...")
    
    if not output_exe.exists():
        raise RuntimeError(f"打包完成但未找到输出文件: {output_exe}")
    
    file_size = output_exe.stat().st_size
    print(f"      ✓ 输出文件已生成")
    print(f"      ✓ 文件大小: {file_size / 1024:.2f} KB ({file_size / 1024 / 1024:.2f} MB)")
    
    print("\n" + "=" * 50)
    print(f" 打包成功！")
    print(f"   可执行文件: {output_exe}")
    print("=" * 50)
    return str(output_exe)
if __name__ == "__main__":
    sys.exit(1)