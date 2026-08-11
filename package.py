import os
import shutil
import subprocess
from tqdm import tqdm
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

def copy_file(src_file, dst_file):
    try:
        os.link(src_file, dst_file)
    except OSError:
        shutil.copy2(src_file, dst_file)
    time.sleep(0.001)  

def copy_dir_with_progress(src, dst):
    os.makedirs(dst, exist_ok=True)
    file_list = []
    for root, _, files in os.walk(src):
        rel_path = os.path.relpath(root, src)
        target_dir = os.path.join(dst, rel_path)
        os.makedirs(target_dir, exist_ok=True)
        
        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(target_dir, file)
            file_list.append((src_file, dst_file))
    total_files = len(file_list)
    max_workers = min(os.cpu_count() or 4, total_files)  
    
    with tqdm(total=total_files, unit='file', desc=f"复制 {os.path.basename(src)}") as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(copy_file, src, dst) for src, dst in file_list]
            
        
            for future in as_completed(futures):
                future.result() 
                pbar.update(1)


if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")
if os.path.exists("QGUOAT.spec"):
    os.remove("QGUOAT.spec")

logo_path = "logo.ico" #自填
target_assets_dir = os.path.join("dist", "QGuoat", "assets")


command = [
    "pyinstaller",
    "--onedir",
    "--name", "QGuoat", #可自填
    "--distpath", "dist",
    '--contents-directory', "assets",
    "--icon", logo_path,
    "index.py"
]

try:
    print("开始打包主程序...")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    print("主程序打包成功！")
    
    
    source_dirs = ["env", "plugin", "package"]
    os.makedirs(target_assets_dir, exist_ok=True)
    if os.path.exists(logo_path):
        target_logo_path = os.path.join(target_assets_dir, "logo.ico")
        shutil.copy2(logo_path, target_logo_path)
        print(f"已复制logo.ico到 {target_logo_path}")
    else:
        print(f"警告：{logo_path} 文件不存在，跳过复制")
    for dir_name in source_dirs:
        if os.path.exists(dir_name):
            base_name = os.path.basename(dir_name)
            target_dir = os.path.join(target_assets_dir, base_name)
            
            if os.path.exists(target_dir):
                if os.path.isfile(target_dir):
                    os.remove(target_dir)
                else:
                    shutil.rmtree(target_dir)
            
            copy_dir_with_progress(dir_name, target_dir)
            print(f"\n已完成 {dir_name} 复制到 {target_dir}")
        else:
            print(f"警告：{dir_name} 目录不存在，跳过复制")

    print("\n所有操作完成！结果在 dist 目录下")

except subprocess.CalledProcessError as e:
    print(f"打包失败：{e}")
    print("错误输出:", e.stderr)
except Exception as e:
    print(f"操作时出错：{e}")
