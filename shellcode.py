import ctypes
import ctypes.wintypes
import os
import struct
import hashlib
from datetime import datetime

QOT_META_MAGIC = b'QOTM'  
QOT_META_FORMAT = '<II32s32s128s16sI32s'
QOT_META_SIZE = struct.calcsize(QOT_META_FORMAT)  
SIGN_MAGIC = b'SIGN' 
SIGN_TAIL_SIZE = 512

PERM_LABELS = {
    0x00000000: "无特殊权限",
    0x00000001: "文件读写",
    0x00000002: "网络访问",
    0x00000004: "进程操作",
    0x00000008: "关机/重启",
    0x00000010: "注册表操作",
    0x00000020: "内存操作",
    0x00000040: "代码注入(高风险)",
    0xFFFFFFFF: "全部权限(极度危险)"
}

def decode_bytes(b):
    try:
        return b.decode('utf-8').rstrip('\x00')
    except UnicodeDecodeError:
        return b.decode('latin-1').rstrip('\x00')

def format_permissions(perm_flags):
    if perm_flags == 0:
        return "无"
    if perm_flags == 0xFFFFFFFF:
        return PERM_LABELS[0xFFFFFFFF]
    
    parts = []
    for flag, label in PERM_LABELS.items():
        if flag in (0, 0xFFFFFFFF):
            continue
        if perm_flags & flag:
            parts.append(label)
    return ", ".join(parts) if parts else "未知"

class ShellcodeInstance:
    def __init__(self, path: str, address: int, size: int,
                 code_offset: int = 0, metadata: dict = None, signature: dict = None):
        self.path = path
        self.address = address
        self.size = size
        self.code_offset = code_offset    
        self.metadata = metadata or {}     
        self.signature = signature or {}   

class ShellcodeLoader:
    MEM_COMMIT = 0x00001000
    MEM_RESERVE = 0x00002000
    MEM_RELEASE = 0x00008000
    PAGE_EXECUTE_READWRITE = 0x40
    INFINITE = 0xFFFFFFFF

    def __init__(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._setup_api_types()

    def _setup_api_types(self):
        self.kernel32.VirtualAlloc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_uint32
        ]
        self.kernel32.VirtualAlloc.restype = ctypes.c_void_p
        self.kernel32.VirtualFree.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32
        ]
        self.kernel32.VirtualFree.restype = ctypes.c_bool
        self.kernel32.CreateThread.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32)
        ]
        self.kernel32.CreateThread.restype = ctypes.c_void_p
        self.kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32
        ]
        self.kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_bool

    def _parse_signature_tail(self, data: bytes) -> dict:
        if len(data) < SIGN_TAIL_SIZE:
            return None
        tail = data[-SIGN_TAIL_SIZE:]
        if tail[0:4] != SIGN_MAGIC:
            return None
        try:
            version = struct.unpack_from('<I', tail, 4)[0]
            fingerprint_id = tail[8:40].decode('utf-8', errors='replace').rstrip('\x00')
            timestamp = struct.unpack_from('<Q', tail, 40)[0]
            try:
                timestamp_str = datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S UTC')
            except (ValueError, OSError):
                timestamp_str = f"无效时间戳 ({timestamp})"
            bin_hash = tail[48:80].hex()
            hw_hash = tail[80:112].hex()
            sys_hash = tail[112:144].hex()
            user_info = decode_bytes(tail[144:272])

            return {
                'has_signature': True,
                'version': version,
                'fingerprint_id': fingerprint_id,
                'timestamp': timestamp,
                'timestamp_str': timestamp_str,
                'bin_hash': bin_hash,
                'hw_fingerprint': hw_hash,
                'sys_fingerprint': sys_hash,
                'user_info': user_info
            }

        except (struct.error, UnicodeDecodeError):
            return {'has_signature': True, 'parse_error': True}

    def _print_report(self, metadata: dict, signature: dict):
        pass

    def load_shellcode(self, shellcode_path: str) -> ShellcodeInstance:
        if not os.path.exists(shellcode_path):
            print(f"BIN文件未找到: {shellcode_path}")
            return None
        file_size = os.path.getsize(shellcode_path)
        if file_size == 0:
            print("文件为空")
            return None
        with open(shellcode_path, 'rb') as f:
            full_data = f.read()
        signature = self._parse_signature_tail(full_data)
        if signature and not signature.get('parse_error'):
            sig = signature
        if signature and not signature.get('parse_error'):
            code_data = full_data[:-SIGN_TAIL_SIZE] if len(full_data) > SIGN_TAIL_SIZE else full_data
            code_size = len(code_data)
        else:
            code_data = full_data
            code_size = file_size
        exec_mem = self.kernel32.VirtualAlloc(
            None,
            code_size,
            self.MEM_COMMIT | self.MEM_RESERVE,
            self.PAGE_EXECUTE_READWRITE
        )
        if not exec_mem:
            raise ValueError(f"内存分配失败, 错误码: {ctypes.GetLastError()}")
        CHUNK_SIZE = 4096
        try:
            offset = 0
            while offset < code_size:
                chunk = code_data[offset:offset + CHUNK_SIZE]
                if not chunk:
                    break
                dest = ctypes.c_void_p(exec_mem + offset)
                ctypes.memmove(dest, chunk, len(chunk))
                offset += len(chunk)

            if offset != code_size:
                self.kernel32.VirtualFree(exec_mem, 0, self.MEM_RELEASE)
                raise ValueError(f"读取大小不匹配: {offset}/{code_size}")

            return ShellcodeInstance(
                shellcode_path 
                , exec_mem, code_size,
                code_offset=0,
                signature=signature
            )
        except Exception as e:
            self.kernel32.VirtualFree(exec_mem, 0, self.MEM_RELEASE)
            raise ValueError(f"读取文件失败: {e}")

    def execute_shellcode(self, shellcode_inst: ShellcodeInstance, timeout_ms: int = 5000, force: bool = False,wait_forever: bool = False) -> bool:
        if not shellcode_inst or not shellcode_inst.address:
            print("[错误]无效的 shellcode 实例（可能因为缺少数据令牌导致加载失败）")
            return False
        has_sig = shellcode_inst.signature and shellcode_inst.signature.get('has_signature') and not shellcode_inst.signature.get('parse_error')   
        if not has_sig and not force:
            print(f"[执行拒绝] 未签名的 shellcode文件 ->  {shellcode_inst.path}")
            return False
        if not has_sig and force:
            pass
        hThread = self.kernel32.CreateThread(None, 0, shellcode_inst.address, None, 0, None)
        if not hThread:
            raise ValueError("线程创建失败")
        if wait_forever:
            result = self.kernel32.WaitForSingleObject(hThread, self.INFINITE)
        else:
            result = self.kernel32.WaitForSingleObject(hThread, timeout_ms)
            if result != 0:
                print(f"[执行终止] 线程超时 -> {shellcode_inst.path}")
                self.kernel32.CloseHandle(hThread)
                return False
    
        self.kernel32.CloseHandle(hThread)
        return result == 0

    def cleanup_shellcode(self, shellcode_inst: ShellcodeInstance) -> bool:
        if not shellcode_inst or not shellcode_inst.address:
            print("[警告]尝试清理无效的 shellcode 实例")
            return False
        result = self.kernel32.VirtualFree(shellcode_inst.address, 0, self.MEM_RELEASE)
        if result:
            shellcode_inst.address = 0
        else:
            raise ValueError("清理失败")

        return bool(result)