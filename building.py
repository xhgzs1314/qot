import os
import sys
import subprocess
import shutil
import tempfile
import argparse
from typing import Optional, List

def get_project_root():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def parse_extra_files(extra_files: List[str]) -> List[tuple]:
    result = []
    if not extra_files:
        return result
    
    for file_path in extra_files:
        if not os.path.exists(file_path):
            print(f"警告: 资源文件不存在，已跳过 - {file_path}")
            continue
        dest_name = os.path.basename(file_path)
        result.append((file_path, dest_name))
    if len(result) > 15:
        print(f"警告: 资源文件过多({len(result)}个)，将只保留前15个")
        result = result[:15]
    
    return result

def pack_qtc_to_exe(qtc_path: str, 
                    dll_path: Optional[str] = None,
                    icon_path: Optional[str] = None,
                    extra_files: Optional[List[str]] = None) -> None:
    if not os.path.exists(qtc_path):
        print(f"错误: QTC 文件不存在 - {qtc_path}")
        return
    
    project_root = get_project_root()
    
    if dll_path is None:
        search_paths = [
            os.path.join(project_root, "qotrun.dll"),
            os.path.join(os.path.dirname(qtc_path), "qotrun.dll"),
            os.path.join(os.getcwd(), "qotrun.dll"),
        ]
        for path in search_paths:
            if os.path.exists(path):
                dll_path = path
                break
        
        if dll_path is None:
            print("错误: 找不到 qotrun.dll")
            print("请确保 DLL 在以下位置之一:")
            for p in search_paths:
                print(f"  - {p}")
            return
    
    if not os.path.exists(dll_path):
        print(f"错误: DLL 文件不存在 - {dll_path}")
        return
    extra_resources = parse_extra_files(extra_files) if extra_files else []
    for src, dest in extra_resources:
        print(f"引入额外资源: {src} -> {dest}")
    
    output_name = os.path.splitext(os.path.basename(qtc_path))[0]
    build_dir = os.path.join(os.getcwd(), "build")
    os.makedirs(build_dir, exist_ok=True)
    temp_build_dir = os.path.join(build_dir, "temp_pack")
    if os.path.exists(temp_build_dir):
        shutil.rmtree(temp_build_dir)
    os.makedirs(temp_build_dir)
    
    try:
        print("正在准备打包文件...")
        launcher_code = f'''import sys
import os
import ctypes

def main():
    if sys.platform == 'win32':
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        os.chdir(base_path)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    dll_path = os.path.join(base_path, 'qotrun.dll')
    if not os.path.exists(dll_path):
        print(f"错误: 找不到 qotrun.dll")
        return 1
    
    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        print(f"错误: 无法加载 DLL - {{e}}")
        return 1
    
    dll.vm_init.argtypes = []
    dll.vm_init.restype = ctypes.c_void_p
    
    dll.vm_execute_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.vm_execute_file.restype = ctypes.c_int
    
    dll.vm_get_return_value.argtypes = [ctypes.c_void_p]
    dll.vm_get_return_value.restype = ctypes.c_char_p
    
    dll.vm_destroy.argtypes = [ctypes.c_void_p]
    dll.vm_destroy.restype = None
    
    vm = dll.vm_init()
    if not vm:
        print("错误: QTC 初始化失败")
        return 1
    
    qtc_path = os.path.join(base_path, 'script.qtc')
    result = dll.vm_execute_file(vm, qtc_path.encode('utf-8'))
    
    if result:
        ret = dll.vm_get_return_value(vm)
        if ret:
            ret_str = ret.decode('utf-8', errors='replace')
            if ret_str:
                print(f"程序返回值: {{ret_str}}")
    else:
        print("执行失败")
        dll.vm_destroy(vm)
        return 1
    
    dll.vm_destroy(vm)
    return 0

if __name__ == '__main__':
    sys.exit(main())
'''
        
        launcher_path = os.path.join(temp_build_dir, 'launcher.py')
        with open(launcher_path, 'w', encoding='utf-8') as f:
            f.write(launcher_code)
        
        script_target = os.path.join(temp_build_dir, 'script.qtc')
        dll_target = os.path.join(temp_build_dir, 'qotrun.dll')
        
        shutil.copy2(qtc_path, script_target)
        shutil.copy2(dll_path, dll_target)
        
        if icon_path and os.path.exists(icon_path):
            icon_target = os.path.join(temp_build_dir, 'icon.ico')
            shutil.copy2(icon_path, icon_target)
        add_data_args = [
            f"{script_target}{os.pathsep}.",
            f"{dll_target}{os.pathsep}.",
        ]
        for src, dest in extra_resources:
            temp_dest = os.path.join(temp_build_dir, dest)
            shutil.copy2(src, temp_dest)
            add_data_args.append(f"{temp_dest}{os.pathsep}.")
            print(f"已添加资源: {src}")
        
        print("正在打包...")
        
        python_paths = [
            os.path.join(project_root, "env", "python", "python.exe"),
            os.path.join(project_root, "env", "bin", "python"),
            sys.executable,
        ]
        python_path = next((p for p in python_paths if os.path.exists(p)), "python")
        
        original_cwd = os.getcwd()
        os.chdir(temp_build_dir)
        try:
            cmd = [
                python_path, "-m", "PyInstaller",
                "--onefile",
                "--distpath", build_dir,
                "--workpath", os.path.join(build_dir, "build"),
                "--specpath", build_dir,
                "--log-level", "ERROR",
                "--name", output_name,
                "--console",
                "launcher.py"
            ]
            for add_data in add_data_args:
                cmd.insert(8, "--add-data")
                cmd.insert(9, add_data)
            
            if icon_path and os.path.exists(icon_path):
                icon_abs_path = os.path.join(temp_build_dir, 'icon.ico')
                cmd.insert(4, f"--icon={icon_abs_path}")
            
            result = subprocess.run(cmd)
            if result.returncode == 0:
                exe_path = os.path.join(build_dir, f"{output_name}.exe")
                if os.path.exists(exe_path):
                    print(f"\n编译完成，exe文件已生成到: {exe_path}")
                    if extra_resources:
                        print(f"已打包 {len(extra_resources)} 个额外资源文件")
                else:
                    print(f"\n编译完成，输出目录: {build_dir}")
            else:
                print(f"\n编译失败，返回码: {result.returncode}")
            
        except Exception as e:
            print(f"发生错误: {str(e)}")
        finally:
            os.chdir(original_cwd)
    
    finally:
        if os.path.exists(temp_build_dir):
            shutil.rmtree(temp_build_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit()