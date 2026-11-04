# -*- coding: utf-8 -*-
"""解释器 oracle 侧实现
"""
import math
import os
import re
import struct
import sys
import datetime
import types

_U64 = (1 << 64) - 1
_U53 = (1 << 53) - 1
_WS = ' \t\n\v\f\r'
_WSB = _WS.encode('ascii')
_INT64_MAX = (1 << 63) - 1
_INT64_MIN = -(1 << 63)


# ---------------------------------------------------------------- 归一助手

def _prim_bytes(v):
    """native qotrt_prim_bytes 的镜像：字符串取原字节；其余 tostring。"""
    if isinstance(v, str):
        return v.encode('utf-8', 'surrogateescape')
    return _tostring(v).encode('utf-8', 'surrogateescape')


def _is_closure(v):
    from builtin_func import QotClosure
    return isinstance(v, QotClosure)


def _fmt_f(v):
    """qotrt_num_to_buf 的镜像：整数值（|v|<9e15 且非 -0.0）→ 整数位 +
    ".000000"；否则 %f（6 位定点）。"""
    if isinstance(v, int) and not isinstance(v, bool):
        if -9 * 10 ** 15 < v < 9 * 10 ** 15:
            return '%d.000000' % v
        return '%f' % float(v)
    f = float(v)
    if f == int(f) and abs(f) < 9e15 and not (f == 0.0 and math.copysign(1.0, f) < 0):
        return '%d.000000' % int(f)
    return '%f' % f


def _tostring(v):
    """qotrt_tostring 的逐分支镜像（容器是字面 "[...]"/"{...}"）。"""
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, (int, float)):
        return _fmt_f(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return '[...]'
    if isinstance(v, dict):
        return '{...}'
    if _is_closure(v):
        return 'closure'
    return 'unknown'


_NUM_RE = re.compile(r'[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)')


def _strtod(s):
    """C strtod 的最长前缀解析（空前缀/无数字 → 0.0）。"""
    t = s.strip(_WS)
    if not t:
        return 0.0
    m = _NUM_RE.match(t)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def _prim_num(v):
    """qotrt_prim_num 的镜像：bool 真=1.0；数字原样；其余 tostring 后 strtod。"""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return _strtod(_tostring(v))


def _prim_i64(v):
    """qotrt_prim_i64 的镜像：向零截断；超 int64 域 → (None, False)。"""
    d = _prim_num(v)
    if d != d or d < -9.2e18 or d > 9.2e18:
        return None, False
    return int(d), True  # int() 向零截断 = C 强转


def _qot_eq(a, b):
    """qotrt_veq 的镜像（语言 == 的值语义）：STR 比字节；NUM/NUM 比数值；
    NUM/BOOL 混合按真值；null 互等；其余 false。"""
    if a is None and b is None:
        return True
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        return float(a) == float(b)
    if (a_num or isinstance(a, bool)) and (b_num or isinstance(b, bool)):
        return bool(a) == bool(b)
    return False


def _truthy(v):
    """qrt_truthy_of 的镜像（filter 回调的判定口径）。"""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return len(v) > 0
    return True


def _need_args(args, n):
    if len(args) < n:
        raise TypeError('原语需要至少 %d 个参数，收到 %d 个' % (n, len(args)))


# ---------------------------------------------------------------- 随机（splitmix64）

_rand_state = 0x853C49E6748FEA9B


def _splitmix64():
    global _rand_state
    _rand_state = (_rand_state + 0x9E3779B97F4A7C15) & _U64
    z = _rand_state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _U64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _U64
    z = z ^ (z >> 31)
    return z


# ---------------------------------------------------------------- 哈希（qotrt_hash 镜像）

def _qot_hash(data):
    h = (0x9E3779B97F4A7C15 ^ len(data)) & _U64
    n = len(data)
    i = 0
    while n - i >= 8:
        blk = struct.unpack_from('<Q', data, i)[0]
        h ^= blk
        h = (h * 0xC2B2AE3D27D4EB4F) & _U64
        h ^= h >> 29
        i += 8
    if n - i > 0:
        tail = 0
        for k in range(n - i):
            tail |= data[i + k] << (8 * k)
        h ^= tail
        h = (h * 0x165667B19E3779F9) & _U64
        h ^= h >> 32
    h = (h * 0x27D4EB2F165667C5) & _U64
    h ^= h >> 32
    return h if h else 1


# ---------------------------------------------------------------- 文件句柄表

_file_tab = {}          # handle(int, 1 起) → 二进制 file 对象
_file_next = 1


def _file_of(h):
    if isinstance(h, bool) or not isinstance(h, (int, float)):
        return None
    if isinstance(h, float):
        if h != int(h) or h < 1:
            return None
        h = int(h)
    if h < 1:
        return None
    return _file_tab.get(h)


_FMODE = {
    'r': 'rb', 'rb': 'rb',
    'w': 'wb', 'wb': 'wb',
    'a': 'ab', 'ab': 'ab',
    'rw': 'r+b',
}


def _os_path(v):
    return os.fsdecode(_path_bytes(v))


def _path_bytes(v):
    """路径参数 → OS 用字节串。"""
    if isinstance(v, str):
        return v.encode('utf-8', 'surrogateescape')
    return _tostring(v).encode('utf-8', 'surrogateescape')


# ================================================================ P0-A 文件与路径

def file_open_prim_handler(*args):
    _need_args(args, 2)
    mode = _FMODE.get(_prim_bytes(args[1]).decode('ascii', 'replace'))
    if mode is None:
        return None
    try:
        f = open(_os_path(args[0]), mode)
    except (OSError, ValueError):
        return None
    global _file_next
    h = _file_next
    _file_next += 1
    _file_tab[h] = f
    return h


def _file_handle_key(h):
    if isinstance(h, float) and h == int(h):
        return int(h)
    return h


def file_close_prim_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return False
    try:
        f.close()
    finally:
        _file_tab.pop(_file_handle_key(args[0]), None)
    return True


def file_read_prim_handler(*args):
    _need_args(args, 2)
    f = _file_of(args[0])
    if f is None:
        return ''
    n, ok = _prim_i64(args[1])
    if not ok or n <= 0:
        return ''
    data = f.read(min(n, 0x7FFFFF00))
    return data.decode('utf-8', 'surrogateescape')


def file_read_all_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return ''
    return f.read().decode('utf-8', 'surrogateescape')


def file_write_prim_handler(*args):
    _need_args(args, 2)
    f = _file_of(args[0])
    if f is None:
        return -1
    try:
        return f.write(_prim_bytes(args[1]))
    except OSError:
        return -1


def file_write_at_handler(*args):
    _need_args(args, 3)
    f = _file_of(args[0])
    if f is None:
        return -1
    off, ok = _prim_i64(args[1])
    if not ok or off < 0:
        return -1
    try:
        f.seek(off)
        return f.write(_prim_bytes(args[2]))
    except (OSError, ValueError):
        return -1


def file_seek_handler(*args):
    _need_args(args, 3)
    f = _file_of(args[0])
    if f is None:
        return -1
    off, ok = _prim_i64(args[1])
    w, ok2 = _prim_i64(args[2])
    if not ok or not ok2 or w not in (0, 1, 2):
        return -1
    try:
        f.seek(off, w)
        return f.tell()
    except (OSError, ValueError):
        return -1


def file_tell_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return -1
    try:
        return f.tell()
    except (OSError, ValueError):
        return -1


def file_size_prim_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return -1
    try:
        cur = f.tell()
        f.seek(0, 2)
        size = f.tell()
        f.seek(cur)
        return size
    except (OSError, ValueError):
        return -1


def file_flush_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return False
    try:
        f.flush()
        return True
    except OSError:
        return False


def file_truncate_handler(*args):
    _need_args(args, 2)
    f = _file_of(args[0])
    if f is None:
        return False
    n, ok = _prim_i64(args[1])
    if not ok or n < 0:
        return False
    try:
        f.truncate(n)
        return True
    except OSError:
        return False


def file_stat_handler(*args):
    _need_args(args, 1)
    p = _os_path(args[0])
    try:
        st = os.stat(p)
    except OSError:
        return None
    return {
        'size': st.st_size,
        'mtime': float(int(st.st_mtime)),
        'is_dir': os.path.isdir(p),
        'is_file': os.path.isfile(p),
        'readonly': not os.access(p, os.W_OK),
    }


def file_exists_prim_handler(*args):
    _need_args(args, 1)
    return os.path.exists(_os_path(args[0]))


def file_remove_prim_handler(*args):
    _need_args(args, 1)
    try:
        os.remove(_os_path(args[0]))
        return True
    except OSError:
        return False


def file_rename_handler(*args):
    _need_args(args, 2)
    try:
        os.rename(_os_path(args[0]), _os_path(args[1]))
        return True
    except OSError:
        return False


def file_copy_handler(*args):
    _need_args(args, 2)
    src, dst = _os_path(args[0]), _os_path(args[1])
    try:
        with open(src, 'rb') as fa, open(dst, 'wb') as fb:
            while True:
                chunk = fa.read(1 << 16)
                if not chunk:
                    break
                fb.write(chunk)
        return True
    except OSError:
        return False


def file_mtime_handler(*args):
    _need_args(args, 1)
    try:
        return float(int(os.path.getmtime(_os_path(args[0]))))
    except OSError:
        return -1


def file_lock_handler(*args):
    _need_args(args, 2)
    # oracle 简化项（登记在案）：锁语义的裁定侧是 native（LockFileEx/flock）；
    # 解释器侧对「有句柄」主干返回 true，锁冲突路径不在差分面内。
    return _file_of(args[0]) is not None


def file_sync_handler(*args):
    _need_args(args, 1)
    f = _file_of(args[0])
    if f is None:
        return False
    try:
        f.flush()
        os.fsync(f.fileno())
        return True
    except OSError:
        return False


def handle_valid_handler(*args):
    _need_args(args, 1)
    return _file_of(args[0]) is not None


def path_join_handler(*args):
    if not args:
        return ''
    parts = [_path_bytes(a) for a in args]
    parts = [p for p in parts if p]
    joined = b'/'.join(parts)
    # 轻量归一：分隔符统一 / + 折叠重复（不做 .. 解析，那是 path_normalize）
    out = bytearray()
    prev_sep = False
    for byte in joined:
        if byte in (47, 92):  # '/' 或 '\'
            if prev_sep:
                continue
            out.append(47)
            prev_sep = True
        else:
            out.append(byte)
            prev_sep = False
    return bytes(out).decode('utf-8', 'surrogateescape')


def _split_path_bytes(v):
    """按 / 与 \\ 切段。返回 (原始字节, 段列表)。"""
    b = _path_bytes(v)
    segs, cur = [], bytearray()
    for byte in b:
        if byte in (47, 92):
            segs.append(bytes(cur))
            cur = bytearray()
        else:
            cur.append(byte)
    segs.append(bytes(cur))
    return b, segs


def _trim_tail(b):
    """剥尾部分隔符（根 "/" → (0, True) 表示根）。"""
    end = len(b)
    while end > 1 and b[end - 1] in (47, 92):
        end -= 1
    if end == 1 and b[0] in (47, 92):
        return 0, True
    return end, False


def path_basename_handler(*args):
    _need_args(args, 1)
    b, _segs = _split_path_bytes(args[0])
    end, is_root = _trim_tail(b)
    if is_root:
        return '/'
    if end == 0:
        return '/' if (b and b[0] in (47, 92)) else ''
    i = end
    while i > 0 and b[i - 1] not in (47, 92):
        i -= 1
    return bytes(b[i:end]).decode('utf-8', 'surrogateescape')


def path_dirname_handler(*args):
    _need_args(args, 1)
    b, _segs = _split_path_bytes(args[0])
    end, is_root = _trim_tail(b)
    if is_root:
        return '/'
    if end == 0:
        return '/' if (b and b[0] in (47, 92)) else ''
    i = end
    while i > 0 and b[i - 1] not in (47, 92):
        i -= 1
    if i == 0:
        return ''
    i -= 1
    while i > 0 and b[i - 1] in (47, 92):
        i -= 1
    return bytes(b[:i]).decode('utf-8', 'surrogateescape')


def _name_and_ext(v):
    """(文件名字节, 扩展点位置; 无扩展 → -1)。隐藏文件（.git）无扩展。"""
    b, _segs = _split_path_bytes(v)
    end, is_root = _trim_tail(b)
    if is_root:
        end = 0
    i = end
    while i > 0 and b[i - 1] not in (47, 92):
        i -= 1
    name = b[i:end]
    dot = name.rfind(b'.')
    if dot <= 0:
        dot = -1
    return name, dot


def path_ext_handler(*args):
    _need_args(args, 1)
    name, dot = _name_and_ext(args[0])
    if dot < 0:
        return ''
    return name[dot:].decode('utf-8', 'surrogateescape')


def path_stem_handler(*args):
    _need_args(args, 1)
    name, dot = _name_and_ext(args[0])
    if dot < 0:
        return name.decode('utf-8', 'surrogateescape')
    return name[:dot].decode('utf-8', 'surrogateescape')


def path_normalize_handler(*args):
    _need_args(args, 1)
    b = _path_bytes(args[0])
    if not b:
        return ''
    out = bytearray()
    i = 0
    anchored = False
    # 前缀：盘符 / UNC(//) / 根(/)
    if len(b) >= 2 and b[1] == 58 and (65 <= b[0] <= 90 or 97 <= b[0] <= 122):
        out += b[:2]
        i = 2
        anchored = True
    elif len(b) >= 2 and b[0] in (47, 92) and b[1] in (47, 92):
        out += b'//'
        i = 2
        anchored = True
    elif b[0] in (47, 92):
        out += b'/'
        i = 1
        anchored = True
    # 段处理：'.' 跳过；'..' 回退（无锚可退时保留，相对语义）
    segs = []
    cur = bytearray()
    idx = i
    while idx < len(b):
        c = b[idx]
        if c in (47, 92):
            segs.append(bytes(cur))
            cur = bytearray()
        else:
            cur.append(c)
        idx += 1
    segs.append(bytes(cur))
    keep = []
    for s in segs:
        if s == b'.':
            continue
        if s == b'..':
            if keep and keep[-1] != b'..':
                keep.pop()
            elif not anchored:
                keep.append(b'..')
            # 锚定且无可退：越根的 .. 丢弃（/.. == /）
            continue
        if s:
            keep.append(s)
    unc = len(out) >= 2 and out[0] == 47 and out[1] == 47
    if not keep:
        if not anchored:
            return ''
        if unc:
            return '//'
        if len(out) >= 2 and out[1] == 58:
            return out.decode('utf-8', 'surrogateescape')  # 盘符头（"C:"）
        return '/'
    res = bytearray(out)
    for s in keep:
        if res and not (res.endswith(b'/') or (unc and res == b'//')):
            res += b'/'
        res += s
    return bytes(res).decode('utf-8', 'surrogateescape')


def path_is_absolute_handler(*args):
    _need_args(args, 1)
    b = _path_bytes(args[0])
    if b and b[0] in (47, 92):
        return True
    if len(b) >= 2 and b[1] == 58 and (65 <= b[0] <= 90 or 97 <= b[0] <= 122):
        return True
    return False


def path_is_dir_handler(*args):
    _need_args(args, 1)
    return os.path.isdir(_os_path(args[0]))


def path_is_file_handler(*args):
    _need_args(args, 1)
    return os.path.isfile(_os_path(args[0]))


def dir_list_handler(*args):
    _need_args(args, 1)
    try:
        names = os.listdir(_os_path(args[0]))
    except OSError:
        return None
    raw = sorted(n.encode('utf-8', 'surrogateescape') for n in names
                 if n not in ('.', '..'))
    return [r.decode('utf-8', 'surrogateescape') for r in raw]


def dir_mkdir_handler(*args):
    _need_args(args, 1)
    try:
        os.mkdir(_os_path(args[0]))
        return True
    except OSError:
        return False


def dir_mkdirs_handler(*args):
    _need_args(args, 1)
    try:
        os.makedirs(_os_path(args[0]), exist_ok=True)
        return True
    except OSError:
        return False


def dir_remove_handler(*args):
    _need_args(args, 1)
    try:
        os.rmdir(_os_path(args[0]))
        return True
    except OSError:
        return False


def dir_size_handler(*args):
    _need_args(args, 1)
    root = _os_path(args[0])
    if not os.path.exists(root):
        return -1
    if os.path.isfile(root):
        try:
            return os.path.getsize(root)
        except OSError:
            return -1
    total = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


# ================================================================ P0-B 字符串

def str_len_prim_handler(*args):
    _need_args(args, 1)
    return len(_prim_bytes(args[0]))


def str_empty_handler(*args):
    _need_args(args, 1)
    return len(_prim_bytes(args[0])) == 0


def str_slice_handler(*args):
    _need_args(args, 3)
    b = _prim_bytes(args[0])
    frm, ok = _prim_i64(args[1])
    to, ok2 = _prim_i64(args[2])
    if not ok or not ok2:
        return ''
    frm = max(0, frm)
    to = min(len(b), to)
    if frm >= to:
        return ''
    return b[frm:to].decode('utf-8', 'surrogateescape')


def str_byte_at_handler(*args):
    _need_args(args, 2)
    b = _prim_bytes(args[0])
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(b):
        return -1
    return b[i]


def str_find_handler(*args):
    _need_args(args, 3)
    hay = _prim_bytes(args[0])
    nd = _prim_bytes(args[1])
    frm, ok = _prim_i64(args[2])
    if not ok:
        return -1
    frm = max(0, frm)
    if frm > len(hay):
        return -1
    return hay.find(nd, frm)


def str_rfind_handler(*args):
    _need_args(args, 2)
    return _prim_bytes(args[0]).rfind(_prim_bytes(args[1]))


def str_contains_handler(*args):
    _need_args(args, 2)
    return _prim_bytes(args[1]) in _prim_bytes(args[0])


def str_starts_with_handler(*args):
    _need_args(args, 2)
    return _prim_bytes(args[0]).startswith(_prim_bytes(args[1]))


def str_ends_with_handler(*args):
    _need_args(args, 2)
    return _prim_bytes(args[0]).endswith(_prim_bytes(args[1]))


def str_count_handler(*args):
    _need_args(args, 2)
    hay = _prim_bytes(args[0])
    nd = _prim_bytes(args[1])
    if not nd:
        return 0
    return hay.count(nd)  # bytes.count 非重叠 ✓


def str_split_prim_handler(*args):
    _need_args(args, 2)
    if args[0] is None:
        return None
    hay = _prim_bytes(args[0])
    sep = _prim_bytes(args[1])
    if not sep:
        return [bytes([c]).decode('utf-8', 'surrogateescape') for c in hay]
    return [p.decode('utf-8', 'surrogateescape') for p in hay.split(sep)]


def str_split_lines_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    out = []
    start = 0
    i = 0
    while i <= len(b):
        if i == len(b) or b[i:i + 1] == b'\n':
            end = i
            if end > start and b[end - 1:end] == b'\r':
                end -= 1
            out.append(b[start:end].decode('utf-8', 'surrogateescape'))
            start = i + 1
        i += 1
    return out


def str_split_n_handler(*args):
    _need_args(args, 3)
    hay = _prim_bytes(args[0])
    sep = _prim_bytes(args[1])
    n, ok = _prim_i64(args[2])
    if not ok or n <= 0 or not sep:
        return [hay.decode('utf-8', 'surrogateescape')]
    parts = []
    start = 0
    cuts = 0
    while cuts < n:
        hit = hay.find(sep, start)
        if hit < 0:
            break
        parts.append(hay[start:hit].decode('utf-8', 'surrogateescape'))
        start = hit + len(sep)
        cuts += 1
    parts.append(hay[start:].decode('utf-8', 'surrogateescape'))
    return parts


def str_join_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return ''
    sep = _prim_bytes(args[1])
    pieces = [_prim_bytes(e) for e in args[0]]
    return sep.join(pieces).decode('utf-8', 'surrogateescape')


def str_replace_prim_handler(*args):
    _need_args(args, 3)
    hay = _prim_bytes(args[0])
    old = _prim_bytes(args[1])
    new = _prim_bytes(args[2])
    if not old:
        return hay.decode('utf-8', 'surrogateescape')
    return hay.replace(old, new).decode('utf-8', 'surrogateescape')


def str_replace_n_handler(*args):
    _need_args(args, 4)
    hay = _prim_bytes(args[0])
    old = _prim_bytes(args[1])
    new = _prim_bytes(args[2])
    n, ok = _prim_i64(args[3])
    if not ok or n < 0:
        return ''
    if not old or n == 0:
        return hay.decode('utf-8', 'surrogateescape')
    out = bytearray()
    start = 0
    done = 0
    while done < n:
        hit = hay.find(old, start)
        if hit < 0:
            break
        out += hay[start:hit]
        out += new
        start = hit + len(old)
        done += 1
    out += hay[start:]
    return bytes(out).decode('utf-8', 'surrogateescape')


def _ascii_case_bytes(v, upper):
    b = bytearray(_prim_bytes(v))
    if upper:
        for i, c in enumerate(b):
            if 97 <= c <= 122:
                b[i] = c - 32
    else:
        for i, c in enumerate(b):
            if 65 <= c <= 90:
                b[i] = c + 32
    return bytes(b)


def str_upper_handler(*args):
    _need_args(args, 1)
    return _ascii_case_bytes(args[0], True).decode('utf-8', 'surrogateescape')


def str_lower_handler(*args):
    _need_args(args, 1)
    return _ascii_case_bytes(args[0], False).decode('utf-8', 'surrogateescape')


def str_trim_prim_handler(*args):
    _need_args(args, 1)
    return _prim_bytes(args[0]).strip(_WSB).decode('utf-8', 'surrogateescape')


def str_ltrim_handler(*args):
    _need_args(args, 1)
    return _prim_bytes(args[0]).lstrip(_WSB).decode('utf-8', 'surrogateescape')


def str_rtrim_handler(*args):
    _need_args(args, 1)
    return _prim_bytes(args[0]).rstrip(_WSB).decode('utf-8', 'surrogateescape')


def _str_pad(args, left):
    s = _prim_bytes(args[0])
    n, ok = _prim_i64(args[1])
    pad = _prim_bytes(args[2])
    if not ok or n <= len(s) or n > 0x7FFFFF00:
        return s.decode('utf-8', 'surrogateescape')
    pc = pad[:1] if pad else b' '
    fill = n - len(s)
    out = (pc * fill + s) if left else (s + pc * fill)
    return out.decode('utf-8', 'surrogateescape')


def str_pad_left_handler(*args):
    _need_args(args, 3)
    return _str_pad(args, True)


def str_pad_right_handler(*args):
    _need_args(args, 3)
    return _str_pad(args, False)


def str_repeat_handler(*args):
    _need_args(args, 2)
    s = _prim_bytes(args[0])
    n, ok = _prim_i64(args[1])
    if not ok or n <= 0 or not s:
        return ''
    if n > 0x7FFFFF00 // max(1, len(s)):
        return ''
    return (s * n).decode('utf-8', 'surrogateescape')


def str_reverse_handler(*args):
    _need_args(args, 1)
    return _prim_bytes(args[0])[::-1].decode('utf-8', 'surrogateescape')


def str_cmp_handler(*args):
    _need_args(args, 2)
    a = _prim_bytes(args[0])
    b = _prim_bytes(args[1])
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def str_eq_ignore_case_handler(*args):
    _need_args(args, 2)
    return _ascii_case_bytes(args[0], False) == _ascii_case_bytes(args[1], False)


def str_index_of_any_handler(*args):
    _need_args(args, 2)
    hay = _prim_bytes(args[0])
    chars = set(_prim_bytes(args[1]))
    if not chars:
        return -1
    for i, c in enumerate(hay):
        if c in chars:
            return i
    return -1


def str_starts_with_any_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[1], list) or not isinstance(args[0], str):
        return False
    s = _prim_bytes(args[0])
    for p in args[1]:
        if s.startswith(_prim_bytes(p)):
            return True
    return False


def str_format_handler(*args):
    _need_args(args, 1)
    fmt = _prim_bytes(args[0])
    out = bytearray()
    ai = 1
    i = 0
    n = len(fmt)
    while i < n:
        c = fmt[i]
        if c != 0x25:  # '%'
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            out += b'%'
            break
        d = fmt[i + 1]
        i += 2
        if d == 0x25:
            out += b'%'
        elif d == 0x73:  # 's'
            if ai < len(args):
                out += _prim_bytes(args[ai])
                ai += 1
            else:
                out += b'%s'
        elif d == 0x64:  # 'd'
            if ai < len(args):
                v, ok = _prim_i64(args[ai])
                ai += 1
                out += str(v).encode() if ok else b'%d'
            else:
                out += b'%d'
        elif d == 0x66:  # 'f'
            if ai < len(args):
                out += _fmt_f(_prim_num(args[ai])).encode()
                ai += 1
            else:
                out += b'%f'
        else:
            out += b'%'
            out.append(d)
    return bytes(out).decode('utf-8', 'surrogateescape')


# ---- char_*（UTF-8 码点级；Python str 的码点切分与 qrt_char_* 同口径）----

def char_len_handler(*args):
    _need_args(args, 1)
    return len(args[0]) if isinstance(args[0], str) else len(_tostring(args[0]))


def char_slice_handler(*args):
    _need_args(args, 3)
    s = args[0] if isinstance(args[0], str) else _tostring(args[0])
    frm, ok = _prim_i64(args[1])
    to, ok2 = _prim_i64(args[2])
    if not ok or not ok2:
        return ''
    frm = max(0, frm)
    to = min(len(s), to)
    if frm >= to:
        return ''
    return s[frm:to]


def char_at_handler(*args):
    _need_args(args, 2)
    s = args[0] if isinstance(args[0], str) else _tostring(args[0])
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(s):
        return None
    return s[i]


def char_index_byte_handler(*args):
    _need_args(args, 2)
    s = args[0] if isinstance(args[0], str) else _tostring(args[0])
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(s):
        return -1
    return len(s[:i].encode('utf-8', 'surrogateescape'))


def char_upper_handler(*args):
    _need_args(args, 1)
    s = args[0] if isinstance(args[0], str) else _tostring(args[0])
    if not s:
        return ''
    c = s[0]
    if 'a' <= c <= 'z':
        return chr(ord(c) - 32) + s[1:]
    return s


def char_lower_handler(*args):
    _need_args(args, 1)
    s = args[0] if isinstance(args[0], str) else _tostring(args[0])
    if not s:
        return ''
    c = s[0]
    if 'A' <= c <= 'Z':
        return chr(ord(c) + 32) + s[1:]
    return s


def str_utf8_valid_handler(*args):
    _need_args(args, 1)
    try:
        _prim_bytes(args[0]).decode('utf-8', 'strict')
        return True
    except UnicodeDecodeError:
        return False


def str_hash_handler(*args):
    _need_args(args, 1)
    return _qot_hash(_prim_bytes(args[0])) & _U53


# ================================================================ P0-D 数字

def num_to_str_handler(*args):
    _need_args(args, 1)
    if len(args) < 2:
        if isinstance(args[0], str):
            return args[0]  # native strings 的 STR 直通
        return _fmt_f(_prim_num(args[0]))
    d = _prim_num(args[0])
    dec = _prim_num(args[1])
    if dec < 0:
        return '%e' % d
    prec = max(0, min(340, int(dec)))
    return '%.*f' % (prec, d)


def num_to_str_int_handler(*args):
    _need_args(args, 1)
    v, ok = _prim_i64(args[0])
    if ok:
        return str(v)
    return _fmt_f(_prim_num(args[0]))


_FULL_NUM_RE = re.compile(
    r'[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)')


def _parse_full_num(v):
    """qrt_parse_num 镜像：裁空白 + 全串消耗。返回 (value, ok)。"""
    t = _prim_bytes(v).decode('ascii', 'ignore').strip(_WS)
    if not t or not _FULL_NUM_RE.fullmatch(t):
        return 0.0, False
    try:
        return float(t), True
    except ValueError:
        return 0.0, False


def str_to_num_handler(*args):
    _need_args(args, 1)
    v, ok = _parse_full_num(args[0])
    return v if ok else -1


def num_is_valid_handler(*args):
    _need_args(args, 1)
    _v, ok = _parse_full_num(args[0])
    return ok


def int_to_str_handler(*args):
    _need_args(args, 1)
    v, ok = _prim_i64(args[0])
    if not ok:
        return ''
    return str(v)


def str_to_int_handler(*args):
    _need_args(args, 1)
    s = _prim_bytes(args[0]).decode('ascii', 'ignore').strip(_WS)
    if not s or not re.fullmatch(r'[+-]?\d+', s):
        return -1
    v = int(s)
    if v < _INT64_MIN or v > _INT64_MAX:
        return -1
    return v


def int_div_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or b == 0 or (a == _INT64_MIN and b == -1):
        return -1
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q  # 向零截断（C /）


# ---- P0-D 补：位运算（int64 域，失败哨兵 -1 与 int_div 同族定价）----
# 口径（全登记，builtins.toml desc 同文）：
#   * 参数 = 严格 int64 化（_prim_i64，与 int_div 同源）；非整/越域 → -1；
#   * bit_shl = **uint64 承载**左移：高位截断、无符号溢出（C unsigned 语义，
#     定义行为——绝不用有符号左移，1<<63 在 C 是 UB）；
#   * bit_shr = **算术右移**（符号跟随，C/Go 的 >> 同律；逻辑右移 bit_shr_u
#     不做，等触发）；移位数 s ∈ [0,63]，越域 → -1（拒绝而非钳制，可预测）；
#   * -1 哨兵与合法结果同形（~0 = -1、and 的 -1 掩码）= int_div 同款既定
#     定价：位运算输入域由调用方保证，失败面只兜非整/越移位。


def _bit_wrap64(x):
    """Python 无限位 int → int64 补码回绕。"""
    x &= 0xFFFFFFFFFFFFFFFF
    return x - 0x10000000000000000 if x >= 0x8000000000000000 else x


def bit_and_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2:
        return -1
    return a & b


def bit_or_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2:
        return -1
    return a | b


def bit_xor_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2:
        return -1
    return a ^ b


def bit_not_handler(*args):
    _need_args(args, 1)
    a, ok = _prim_i64(args[0])
    if not ok:
        return -1
    return ~a


def bit_shl_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    s, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or s < 0 or s > 63:
        return -1
    return _bit_wrap64(a << s)


def bit_shr_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    s, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or s < 0 or s > 63:
        return -1
    return a >> s  # Python >> 本就是算术右移（符号跟随）


def int_mod_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or b == 0 or (a == _INT64_MIN and b == -1):
        return -1
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return a - q * b  # C %：符号随被除数


def int_floor_div_handler(*args):
    _need_args(args, 2)
    a, ok = _prim_i64(args[0])
    b, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or b == 0 or (a == _INT64_MIN and b == -1):
        return -1
    return a // b  # Python // = 向 -∞（与 C 修正式一致）


def num_abs_handler(*args):
    _need_args(args, 1)
    return abs(_prim_num(args[0]))


def num_sign_handler(*args):
    _need_args(args, 1)
    d = _prim_num(args[0])
    if d != d:
        return 0
    return 1 if d > 0 else (-1 if d < 0 else 0)


def num_min_handler(*args):
    _need_args(args, 2)
    a, b = _prim_num(args[0]), _prim_num(args[1])
    return a if a < b else b


def num_max_handler(*args):
    _need_args(args, 2)
    a, b = _prim_num(args[0]), _prim_num(args[1])
    return a if a > b else b


def num_clamp_handler(*args):
    _need_args(args, 3)
    v, lo, hi = _prim_num(args[0]), _prim_num(args[1]), _prim_num(args[2])
    if lo > hi:
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def num_floor_handler(*args):
    _need_args(args, 1)
    return float(math.floor(_prim_num(args[0])))


def num_ceil_handler(*args):
    _need_args(args, 1)
    return float(math.ceil(_prim_num(args[0])))


def num_round_handler(*args):
    _need_args(args, 1)
    a = _prim_num(args[0])
    dec = _prim_num(args[1]) if len(args) >= 2 else 0.0
    p = math.pow(10.0, dec)
    if not (p > 0) or math.isinf(p) or math.isnan(p):
        return a
    x = a * p
    r = math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)  # 半数远离零
    return float(r) / p


def num_trunc_handler(*args):
    _need_args(args, 1)
    return float(math.trunc(_prim_num(args[0])))


def num_is_nan_handler(*args):
    _need_args(args, 1)
    return math.isnan(_prim_num(args[0]))


def num_is_inf_handler(*args):
    _need_args(args, 1)
    return math.isinf(_prim_num(args[0]))


def num_eq_handler(*args):
    _need_args(args, 2)
    return _prim_num(args[0]) == _prim_num(args[1])


def _math1(fn, name):
    # name 必须与 builtins.toml 的 interp 字段逐字一致（清单机械校验）
    def h(*args):
        _need_args(args, 1)
        return float(fn(_prim_num(args[0])))
    h.__name__ = name
    return h


exp_prim_handler = _math1(math.exp, 'exp_prim_handler')
log_prim_handler = _math1(math.log, 'log_prim_handler')
log2_prim_handler = _math1(math.log2, 'log2_prim_handler')
log10_prim_handler = _math1(math.log10, 'log10_prim_handler')
sin_prim_handler = _math1(math.sin, 'sin_prim_handler')
cos_prim_handler = _math1(math.cos, 'cos_prim_handler')
tan_prim_handler = _math1(math.tan, 'tan_prim_handler')
asin_prim_handler = _math1(math.asin, 'asin_prim_handler')
acos_prim_handler = _math1(math.acos, 'acos_prim_handler')
atan_prim_handler = _math1(math.atan, 'atan_prim_handler')


def atan2_prim_handler(*args):
    _need_args(args, 2)
    return float(math.atan2(_prim_num(args[0]), _prim_num(args[1])))


def hypot_prim_handler(*args):
    _need_args(args, 2)
    return float(math.hypot(_prim_num(args[0]), _prim_num(args[1])))


def rand_seed_handler(*args):
    _need_args(args, 1)
    s, ok = _prim_i64(args[0])
    if not ok:
        return False
    global _rand_state
    _rand_state = s & _U64
    return True


def rand_int_handler(*args):
    _need_args(args, 2)
    lo, ok = _prim_i64(args[0])
    hi, ok2 = _prim_i64(args[1])
    if not ok or not ok2 or lo > hi:
        return -1
    return lo + (_splitmix64() % ((hi - lo) + 1))


def rand_range_handler(*args):
    _need_args(args, 1)
    n, ok = _prim_i64(args[0])
    if not ok or n <= 0:
        return -1
    return _splitmix64() % n


def num_pi_handler(*args):
    return 3.141592653589793


def num_e_handler(*args):
    return 2.718281828459045


# ================================================================ P0-E 数组

def arr_new_handler(*args):
    return []


def arr_new_n_handler(*args):
    _need_args(args, 1)
    n, ok = _prim_i64(args[0])
    if not ok or n < 0 or n > 0x7FFFFF0:
        return None
    return [None] * n


def arr_from_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return None
    return list(args[0])


def arr_len_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return 0
    return len(args[0])


def arr_get_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return None
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(args[0]):
        return None
    return args[0][i]


def arr_set_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], list):
        return False
    i, ok = _prim_i64(args[1])
    if not ok or i < 0:
        return False
    if i >= len(args[0]):
        return True  # 越界写忽略=true（发布面定价）
    args[0][i] = args[2]
    return True


def arr_push_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return -1
    args[0].append(args[1])
    return len(args[0])


def arr_pop_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list) or not args[0]:
        return None
    return args[0].pop()


def arr_insert_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], list):
        return -1
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i > len(args[0]):
        return -1
    args[0].insert(i, args[2])
    return len(args[0])


def arr_remove_at_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return None
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(args[0]):
        return None
    return args[0].pop(i)


def arr_remove_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return False
    for i, e in enumerate(args[0]):
        if _qot_eq(e, args[1]):
            del args[0][i]
            return True
    return False


def arr_clear_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return False
    del args[0][:]
    return True


def arr_index_of_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return -1
    for i, e in enumerate(args[0]):
        if _qot_eq(e, args[1]):
            return i
    return -1


def arr_contains_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return False
    return any(_qot_eq(e, args[1]) for e in args[0])


def arr_count_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list):
        return 0
    return sum(1 for e in args[0] if _qot_eq(e, args[1]))


def arr_slice_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], list):
        return None
    frm, ok = _prim_i64(args[1])
    to, ok2 = _prim_i64(args[2])
    if not ok or not ok2:
        return None
    a = args[0]
    frm = max(0, frm)
    to = min(len(a), to)
    if frm >= to:
        return []
    return list(a[frm:to])


def arr_concat_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list) or not isinstance(args[1], list):
        return None
    return list(args[0]) + list(args[1])


def arr_reverse_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return False
    args[0].reverse()
    return True


def _sort_key(v):
    """native qrt_rank_of/qrt_val_less 的键化：数字(0) < 字符串(1) < 其它(2)。"""
    if isinstance(v, bool):
        return (0, 1.0 if v else 0.0)
    if isinstance(v, (int, float)):
        return (0, float(v))
    if isinstance(v, str):
        return (1, v.encode('utf-8', 'surrogateescape'))
    return (2,)


def arr_sort_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return False
    args[0].sort(key=_sort_key)  # Python sort 稳定 = native 归并并列取左
    return True


def arr_sort_by_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list) or not _is_closure(args[1]):
        return False
    ev = _current_evaluator()
    if ev is None:
        raise TypeError('arr_sort_by 的回调只在解释器主回路内可用')
    keys = [ev._call_closure(args[1], [e]) for e in args[0]]
    order = sorted(range(len(args[0])), key=lambda i: _sort_key(keys[i]))
    args[0][:] = [args[0][i] for i in order]
    return True


def arr_unique_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return None
    out = []
    for e in args[0]:
        if not any(_qot_eq(k, e) for k in out):
            out.append(e)
    return out


def arr_flatten_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return None
    out = []
    for e in args[0]:
        if isinstance(e, list):
            out.extend(e)  # 展平一层
        else:
            out.append(e)
    return out


def _call_fn(fn, call_args):
    ev = _current_evaluator()
    if ev is None:
        raise TypeError('高阶原语的回调只在解释器主回路内可用')
    return ev._call_closure(fn, call_args)


def arr_map_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list) or not _is_closure(args[1]):
        return None
    return [_call_fn(args[1], [e]) for e in args[0]]


def arr_filter_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list) or not _is_closure(args[1]):
        return None
    return [e for e in args[0] if _truthy(_call_fn(args[1], [e]))]


def arr_reduce_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], list) or not _is_closure(args[1]):
        return None
    acc = args[2]
    for e in args[0]:
        acc = _call_fn(args[1], [acc, e])
    return acc


def _arr_num_fold(a, mode):
    if not isinstance(a, list) or not a:
        return 0.0
    vals = [_prim_num(e) for e in a]
    if mode == 0:
        acc = vals[0]
        for v in vals[1:]:
            acc += v
        return acc
    if mode == 1:
        acc = vals[0]
        for v in vals[1:]:
            acc = v if v < acc else acc
        return acc
    acc = vals[0]
    for v in vals[1:]:
        acc = v if v > acc else acc
    return acc


def arr_sum_handler(*args):
    _need_args(args, 1)
    return _arr_num_fold(args[0], 0)


def arr_min_handler(*args):
    _need_args(args, 1)
    return _arr_num_fold(args[0], 1)


def arr_max_handler(*args):
    _need_args(args, 1)
    return _arr_num_fold(args[0], 2)


def arr_join_handler(*args):
    return str_join_handler(*args)


def arr_copy_handler(*args):
    return arr_from_handler(*args)


def arr_eq_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], list) or not isinstance(args[1], list):
        return False
    if len(args[0]) != len(args[1]):
        return False
    return all(_qot_eq(a, b) for a, b in zip(args[0], args[1]))


# ================================================================ P0-F 字典

def dict_new_handler(*args):
    return {}


def dict_from_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], list):
        return None
    for e in args[0]:
        if not isinstance(e, list) or len(e) < 2:
            return None
    return {_tostring(e[0]): e[1] for e in args[0]}


def dict_len_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return 0
    return len(args[0])


def dict_get_or_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], dict):
        return args[2]
    return args[0].get(_tostring(args[1]), args[2])


def dict_has_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], dict):
        return False
    return _tostring(args[1]) in args[0]


def dict_del_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], dict):
        return False
    k = _tostring(args[1])
    if k not in args[0]:
        return False
    del args[0][k]
    return True


def dict_values_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return None
    return list(args[0].values())


def dict_items_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return None
    return [[k, v] for k, v in args[0].items()]


def dict_clear_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return False
    args[0].clear()
    return True


def dict_copy_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return None
    return dict(args[0])


def dict_merge_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], dict) or not isinstance(args[1], dict):
        return None
    out = dict(args[0])
    out.update(args[1])
    return out


def dict_update_handler(*args):
    _need_args(args, 2)
    if not isinstance(args[0], dict) or not isinstance(args[1], dict):
        return -1
    args[0].update(args[1])
    return len(args[0])


def dict_invert_handler(*args):
    _need_args(args, 1)
    if not isinstance(args[0], dict):
        return None
    return {_tostring(v): k for k, v in args[0].items()}


def dict_get_path_handler(*args):
    _need_args(args, 2)
    cur = args[0]
    for seg in _tostring(args[1]).split('.'):
        if not seg:
            continue
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def dict_set_path_handler(*args):
    _need_args(args, 3)
    if not isinstance(args[0], dict):
        return False
    segs = [s for s in _tostring(args[1]).split('.') if s]
    if not segs:
        return False
    cur = args[0]
    for seg in segs[:-1]:
        nxt = cur.get(seg)
        if nxt is None:
            nxt = {}
            cur[seg] = nxt
        elif not isinstance(nxt, dict):
            return False
        cur = nxt
    cur[segs[-1]] = args[2]
    return True


# ================================================================ P0.2 原语
# （0 · CP-2+ · docs/ §4.3/§4.7/§4.8/§4.11）
# 定位与 P0.1 相同：薄壳 oracle，逐位镜像 qot_aot_runtime.h 的 P0.2 节。
# 已知口径备注：
#   * env_get 的键长 native 侧截 511 字节（缓冲上限）——本侧不截；>511 键
#     属病态输入，差分面不覆盖（登记）；
#   * proc 退出码：Windows 原始 DWORD（sys.exit(-1) → 4294967295，与
#     subprocess 口径一致）；POSIX 信号终止 = 128+sig；
#   * timer_after 缓议（需事件循环）；error_new 系留标准库期（§4.11 自注）。

import subprocess
import threading

_SCRIPT_ARGS = []


def set_script_args(args):
    """index.py 在 execute_script 前注入脚本参数（args_get 的数据源）。

    native 侧 = main(argc, argv) 的 argv[1..]；本侧 = index.py run x.qot
    之后的透传 token。两侧口径一致：不含程序名与脚本名。
    """
    global _SCRIPT_ARGS
    _SCRIPT_ARGS = list(args)


def _script_args():
    return list(_SCRIPT_ARGS)


# ---------------------------------------------------------------- C 组镜像

def _u8_step_mirror(b, i):
    """qrt_u8_step 的逐行镜像：返回 (消费字节, 码点或 None)。"""
    n = len(b)
    c = b[i]
    if c < 0x80:
        return 1, c
    if 0xC2 <= c <= 0xDF:
        if i + 1 < n and 0x80 <= b[i + 1] <= 0xBF:
            return 2, ((c & 0x1F) << 6) | (b[i + 1] & 0x3F)
        return 1, None
    if 0xE0 <= c <= 0xEF:
        lo = 0xA0 if c == 0xE0 else 0x80
        hi = 0x9F if c == 0xED else 0xBF
        if i + 2 < n and lo <= b[i + 1] <= hi and 0x80 <= b[i + 2] <= 0xBF:
            return 3, ((c & 0x0F) << 12) | ((b[i + 1] & 0x3F) << 6) | (b[i + 2] & 0x3F)
        return 1, None
    if 0xF0 <= c <= 0xF4:
        lo = 0x90 if c == 0xF0 else 0x80
        hi = 0x8F if c == 0xF4 else 0xBF
        if (i + 3 < n and lo <= b[i + 1] <= hi and 0x80 <= b[i + 2] <= 0xBF
                and 0x80 <= b[i + 3] <= 0xBF):
            return 4, ((c & 0x07) << 18) | ((b[i + 1] & 0x3F) << 12) | \
                      ((b[i + 2] & 0x3F) << 6) | (b[i + 3] & 0x3F)
        return 1, None
    return 1, None


def bytes_len_prim_handler(*args):
    _need_args(args, 1)
    return len(_prim_bytes(args[0]))


def bytes_slice_handler(*args):
    _need_args(args, 3)
    b = _prim_bytes(args[0])
    frm, ok = _prim_i64(args[1])
    to, ok2 = _prim_i64(args[2])
    if not ok or not ok2:
        return ''
    frm = max(0, frm)
    to = min(len(b), to)
    if frm >= to:
        return ''
    return b[frm:to].decode('utf-8', 'surrogateescape')


def bytes_byte_at_handler(*args):
    _need_args(args, 2)
    b = _prim_bytes(args[0])
    i, ok = _prim_i64(args[1])
    if not ok or i < 0 or i >= len(b):
        return -1
    return b[i]


def bytes_find_handler(*args):
    _need_args(args, 3)
    hay = _prim_bytes(args[0])
    nd = _prim_bytes(args[1])
    frm, ok = _prim_i64(args[2])
    if not ok:
        return -1
    frm = max(0, frm)
    if frm > len(hay):
        return -1
    if not nd:
        return frm  # 空 sub 命中于 from（与 str_find 同口径）
    lim = len(hay) - len(nd)
    i = frm
    while i <= lim:
        if hay[i] == nd[0] and hay[i:i + len(nd)] == nd:
            return i
        i += 1
    return -1


def bytes_concat_handler(*args):
    _need_args(args, 2)
    return (_prim_bytes(args[0]) + _prim_bytes(args[1])).decode(
        'utf-8', 'surrogateescape')


def bytes_eq_handler(*args):
    _need_args(args, 2)
    return _prim_bytes(args[0]) == _prim_bytes(args[1])


def bytes_to_hex_handler(*args):
    _need_args(args, 1)
    return _prim_bytes(args[0]).hex()


def bytes_from_hex_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    if len(b) % 2 != 0:
        return None
    try:
        return bytes.fromhex(b.decode('ascii', 'replace')).decode(
            'utf-8', 'surrogateescape')
    except ValueError:
        return None


_B64_ALPHABET = (b'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                 b'abcdefghijklmnopqrstuvwxyz0123456789+/')


def bytes_to_base64_handler(*args):
    _need_args(args, 1)
    import base64 as _b64
    return _b64.b64encode(_prim_bytes(args[0])).decode('ascii')


def bytes_from_base64_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    data = bytearray()
    pad = 0
    nv = 0
    vals = [0, 0, 0, 0]
    for c in b:
        if c in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
            continue
        if c == 0x3D:  # '='
            pad += 1
            if pad > 2:
                return None
            continue
        if pad > 0:
            return None
        idx = _B64_ALPHABET.find(bytes([c]))
        # 与 native 同式：'-'(62) 与 '_'(63) 也收（decode 侧宽容）
        if idx < 0:
            if c == 0x2D:
                idx = 62
            elif c == 0x5F:
                idx = 63
            else:
                return None
        if nv >= 4:
            return None
        vals[nv] = idx
        nv += 1
        if nv == 4:
            x = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6) | vals[3]
            data += bytes([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF])
            nv = 0
    if nv == 1 or (nv == 2 and pad != 2) or (nv == 3 and pad != 1) or \
            (nv == 0 and pad > 0):
        return None
    if nv == 2:
        x = (vals[0] << 18) | (vals[1] << 12)
        data += bytes([(x >> 16) & 0xFF])
    elif nv == 3:
        x = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6)
        data += bytes([(x >> 16) & 0xFF, (x >> 8) & 0xFF])
    return bytes(data).decode('utf-8', 'surrogateescape')


def utf8_encode_prim_handler(*args):
    _need_args(args, 1)
    if isinstance(args[0], str):
        return args[0]  # QOT 串即 UTF-8 字节（直通口径，与 native 一致）
    return _tostring(args[0])


def utf8_decode_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    out = []
    i = 0
    while i < len(b):
        k, cp = _u8_step_mirror(b, i)
        i += k
        out.append(chr(cp) if cp is not None else '\ufffd')
    return ''.join(out)


def utf8_char_count_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    i = 0
    cnt = 0
    while i < len(b):
        k, _cp = _u8_step_mirror(b, i)
        i += k
        cnt += 1
    return cnt


def url_encode_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    out = bytearray()
    for c in b:
        if (65 <= c <= 90 or 97 <= c <= 122 or 48 <= c <= 57 or
                c in (45, 95, 46, 126)):
            out.append(c)
        else:
            out += ('%%%02X' % c).encode('ascii')
    return out.decode('utf-8', 'surrogateescape')


def url_decode_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c == 0x25 and i + 2 < n:
            hi = _hexval(b[i + 1])
            lo = _hexval(b[i + 2])
            if hi >= 0 and lo >= 0:
                out.append((hi << 4) | lo)
                i += 3
                continue
        out.append(c)
        i += 1
    return out.decode('utf-8', 'surrogateescape')


def _hexval(c):
    if 48 <= c <= 57:
        return c - 48
    if 65 <= c <= 70:
        return c - 55
    if 97 <= c <= 102:
        return c - 87
    return -1


# ---------------------------------------------------------------- G 组镜像

def time_now_handler(*args):
    import time as _time
    return float(int(_time.time()))  # 整秒 F64（native = time(nullptr) 口径）


def time_monotonic_handler(*args):
    import time as _time
    return float(_time.monotonic())


def time_now_ms_handler(*args):
    import time as _time
    return float(int(_time.time() * 1000))


def time_monotonic_ms_handler(*args):
    import time as _time
    return float(int(_time.monotonic() * 1000))


def _date_parts_mirror(t, gmtime_fn):
    """qrt_fill_date_parts 的值镜像：weekday 周一=0；yday 1 起。

    Python struct_time 的 tm_mon（1 起）/tm_wday（周一=0）/tm_yday（1 起）
    与目标口径同向，直接取用（native 是 tm_mon+1 / (tm_wday+6)%7 /
    tm_yday+1 —— C 的月份 0 起、周日=0、yday 0 起，换算后与本侧值一致）。
    """
    st = gmtime_fn(t)
    return {
        'year': float(st.tm_year),
        'month': float(st.tm_mon),
        'day': float(st.tm_mday),
        'hour': float(st.tm_hour),
        'minute': float(st.tm_min),
        'second': float(st.tm_sec),
        'weekday': float(st.tm_wday),
        'yday': float(st.tm_yday),
    }


def time_date_parts_handler(*args):
    _need_args(args, 1)
    import time as _time
    d = _prim_num(args[0])
    try:
        t = int(d)
        return _date_parts_mirror(t, _time.localtime)
    except (OverflowError, OSError, ValueError):
        return None


def time_utc_parts_handler(*args):
    _need_args(args, 1)
    import time as _time
    d = _prim_num(args[0])
    try:
        t = int(d)
        return _date_parts_mirror(t, _time.gmtime)
    except (OverflowError, OSError, ValueError):
        return None


def time_make_handler(*args):
    _need_args(args, 6)
    import time as _time
    try:
        y = int(_prim_num(args[0]))
        mo = int(_prim_num(args[1]))
        d = int(_prim_num(args[2]))
        hh = int(_prim_num(args[3]))
        mi = int(_prim_num(args[4]))
        ss = int(_prim_num(args[5]))
        return float(_time.mktime((y, mo, d, hh, mi, ss, 0, 1, -1)))
    except (OverflowError, OSError, ValueError):
        return -1


def time_strftime_handler(*args):
    _need_args(args, 2)
    import time as _time
    try:
        t = int(_prim_num(args[0]))
        fmt = _prim_bytes(args[1]).decode('utf-8', 'surrogateescape')
        if len(fmt) > 255:
            fmt = fmt[:255]
        return _time.strftime(fmt, _time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return ''


def time_parse_handler(*args):
    """qrt time_parse 的镜像：同款极简扫描（%Y4/%m%d%H%M%S2 + 字面 +
    ' ' 精确匹配一个空白），前缀匹配，默认 0 00:00:00 本地时区。"""
    _need_args(args, 2)
    import time as _time
    sb = _prim_bytes(args[0])
    fb = _prim_bytes(args[1])
    si = 0
    fi = 0
    y, mo, dd, hh, mi, ss = 1900, 1, 1, 0, 0, 0
    while fi < len(fb):
        f = fb[fi]
        if f == 0x25 and fi + 1 < len(fb):  # '%'
            d = fb[fi + 1]
            fi += 2
            if d == 0x59:  # 'Y'
                width = 4
            elif d in (0x6D, 0x64, 0x48, 0x4D, 0x53):  # m d H M S
                width = 2
            else:
                return -1
            v = 0
            for _ in range(width):
                if si >= len(sb) or not (0x30 <= sb[si] <= 0x39):
                    return -1
                v = v * 10 + (sb[si] - 0x30)
                si += 1
            if d == 0x59:
                y = v
            elif d == 0x6D:
                mo = v
            elif d == 0x64:
                dd = v
            elif d == 0x48:
                hh = v
            elif d == 0x4D:
                mi = v
            else:
                ss = v
        elif f == 0x20:  # ' ' 精确匹配一个空白
            if si >= len(sb) or sb[si] != 0x20:
                return -1
            si += 1
            fi += 1
        else:
            if si >= len(sb) or sb[si] != f:
                return -1
            si += 1
            fi += 1
    try:
        return float(_time.mktime((y, mo, dd, hh, mi, ss, 0, 1, -1)))
    except (OverflowError, OSError, ValueError):
        return -1


def time_sleep_ms_handler(*args):
    _need_args(args, 1)
    import time as _time
    ms, ok = _prim_i64(args[0])
    if not ok or ms < 0:
        return False
    _time.sleep(ms / 1000.0)
    return True


def time_zone_offset_handler(*args):
    _need_args(args, 1)
    import calendar
    import time as _time
    try:
        t = int(_prim_num(args[0]))
        l = _time.mktime(_time.localtime(t))
        g = calendar.timegm(_time.gmtime(t))
        return (l - g) / 3600.0
    except (OverflowError, OSError, ValueError):
        return 0.0


# ---------------------------------------------------------------- H 组镜像

def env_get_handler(*args):
    _need_args(args, 1)
    key = _prim_bytes(args[0]).decode('utf-8', 'surrogateescape')
    v = os.environ.get(key)
    if v is None:
        return ''
    return v


def env_set_handler(*args):
    _need_args(args, 2)
    key = _prim_bytes(args[0]).decode('utf-8', 'surrogateescape')
    val = _prim_bytes(args[1]).decode('utf-8', 'surrogateescape')
    try:
        os.environ[key] = val
        return True
    except (OSError, ValueError, TypeError):
        return False


def env_unset_handler(*args):
    _need_args(args, 1)
    key = _prim_bytes(args[0]).decode('utf-8', 'surrogateescape')
    try:
        os.environ.pop(key, None)
        return True
    except (OSError, ValueError, TypeError):
        return False


def env_list_handler(*args):
    pairs = []
    for k, v in os.environ.items():
        pairs.append((k.encode('utf-8', 'surrogateescape'),
                      v.encode('utf-8', 'surrogateescape')))
    pairs.sort(key=lambda p: p[0])  # 按键字节序（与 native qsort 同口径）
    return [[k.decode('utf-8', 'surrogateescape'),
             v.decode('utf-8', 'surrogateescape')] for k, v in pairs]


def env_expand_handler(*args):
    _need_args(args, 1)
    b = _prim_bytes(args[0])
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c in (0x24, 0x25) and i + 1 < n:  # '$' 或 '%'
            braced = False
            j = i + 1
            if c == 0x24 and b[j] == 0x7B:  # '${'
                braced = True
                j += 1
            ns = j
            while j < n:
                d = b[j]
                if not (65 <= d <= 90 or 97 <= d <= 122 or
                        48 <= d <= 57 or d == 0x5F):
                    break
                j += 1
            ne = j
            closed = True
            if braced:
                closed = j < n and b[j] == 0x7D
                ne = j if closed else ne
            elif c == 0x25:
                closed = j < n and b[j] == 0x25
            if closed and ne > ns:
                key = b[ns:ne].decode('utf-8', 'surrogateescape')
                syn_end = j + 1 if (braced or c == 0x25) else j
                if syn_end > n:
                    syn_end = n
                v = os.environ.get(key)
                if v is not None:
                    out += v.encode('utf-8', 'surrogateescape')
                else:
                    out += b[i:syn_end]
                i = syn_end
                continue
        out.append(c)
        i += 1
    return out.decode('utf-8', 'surrogateescape')


# ---- 进程表（native qotrt_ptab 的镜像；读侧用守护线程排水）----
_proc_tab = {}
_proc_next = 1


class _ProcRec:
    __slots__ = ('p', 'out', 'err', 'exited', 'exit_code')

    def __init__(self, p):
        self.p = p
        self.out = bytearray()
        self.err = bytearray()
        self.exited = False
        self.exit_code = -1


def _proc_drain(rec):
    try:
        while True:
            chunk = rec.p.stdout.read(4096)
            if not chunk:
                break
            rec.out += chunk
    except (OSError, ValueError):
        pass


def _proc_drain_err(rec):
    try:
        while True:
            chunk = rec.p.stderr.read(4096)
            if not chunk:
                break
            rec.err += chunk
    except (OSError, ValueError):
        pass


def _proc_of(h):
    if isinstance(h, bool) or not isinstance(h, (int, float)):
        return None
    if isinstance(h, float):
        if h != int(h) or h < 1:
            return None
        h = int(h)
    if h < 1:
        return None
    rec = _proc_tab.get(h)
    return rec


def _spawn_argv(cmd, argv):
    """native qrt_proc_spawn_impl 的镜像：argv 数组、stdin=/dev/null、
    双管道收集。失败 None（OSError 全族）。"""
    try:
        p = subprocess.Popen([_tostring_cmd(cmd)] + [_tostring_cmd(a) for a in argv],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL)
    except OSError:
        return None
    rec = _ProcRec(p)
    threading.Thread(target=_proc_drain, args=(rec,), daemon=True).start()
    threading.Thread(target=_proc_drain_err, args=(rec,), daemon=True).start()
    global _proc_next
    h = _proc_next
    _proc_next += 1
    _proc_tab[h] = rec
    return h


def _tostring_cmd(v):
    return _prim_bytes(v).decode('utf-8', 'surrogateescape')


def _proc_wait_rec(rec, timeout_ms):
    """native qrt_proc_wait_impl 的镜像。返回 True=已退出。"""
    if rec.exited:
        return True
    try:
        if timeout_ms < 0:
            rec.p.wait()
        else:
            rec.p.wait(timeout=timeout_ms / 1000.0)
    except subprocess.TimeoutExpired:
        return False
    rec.exited = True
    rec.exit_code = rec.p.returncode
    # 排水线程在 EOF 后自然结束；给它们一个收尾窗口
    try:
        rec.p.stdout.close()
        rec.p.stderr.close()
    except (OSError, ValueError):
        pass
    return True


def proc_run_handler(*args):
    _need_args(args, 3)
    h = _spawn_argv(args[0], args[1] if isinstance(args[1], list) else [])
    if h is None:
        return None
    rec = _proc_tab[h]
    _proc_wait_rec(rec, -1)
    del _proc_tab[h]
    return {
        'code': float(rec.exit_code),
        'stdout': bytes(rec.out).decode('utf-8', 'surrogateescape'),
        'stderr': bytes(rec.err).decode('utf-8', 'surrogateescape'),
    }


def proc_shell_handler(*args):
    _need_args(args, 1)
    cmd = _tostring_cmd(args[0])
    if os.name == 'nt':
        comspec = os.environ.get('ComSpec') or 'cmd.exe'
        argv = [comspec, '/c', cmd]
    else:
        argv = ['/bin/sh', '-c', cmd]
    h = _spawn_argv(argv[0], argv[1:])
    if h is None:
        return None
    rec = _proc_tab[h]
    _proc_wait_rec(rec, -1)
    del _proc_tab[h]
    return {
        'code': float(rec.exit_code),
        'stdout': bytes(rec.out).decode('utf-8', 'surrogateescape'),
        'stderr': bytes(rec.err).decode('utf-8', 'surrogateescape'),
    }


def proc_spawn_handler(*args):
    _need_args(args, 2)
    h = _spawn_argv(args[0], args[1] if isinstance(args[1], list) else [])
    return None if h is None else h


def proc_wait_handler(*args):
    _need_args(args, 2)
    rec = _proc_of(args[0])
    if rec is None:
        return None
    tmo, ok = _prim_i64(args[1])
    if not ok:
        return None
    if not _proc_wait_rec(rec, tmo):
        return None
    for h, r in list(_proc_tab.items()):
        if r is rec:
            del _proc_tab[h]
            break
    return {
        'code': float(rec.exit_code),
        'stdout': bytes(rec.out).decode('utf-8', 'surrogateescape'),
        'stderr': bytes(rec.err).decode('utf-8', 'surrogateescape'),
    }


def proc_kill_handler(*args):
    _need_args(args, 1)
    rec = _proc_of(args[0])
    if rec is None or rec.exited:
        return False
    try:
        rec.p.kill()
        return True
    except OSError:
        return False


def proc_pid_handler(*args):
    _need_args(args, 1)
    rec = _proc_of(args[0])
    if rec is None:
        return -1
    return float(rec.p.pid)


def proc_is_running_handler(*args):
    _need_args(args, 1)
    rec = _proc_of(args[0])
    if rec is None:
        return False
    if rec.exited:
        return False
    rc = rec.p.poll()
    if rc is None:
        return True
    rec.exited = True
    rec.exit_code = rc
    return False


def proc_exit_code_handler(*args):
    _need_args(args, 1)
    rec = _proc_of(args[0])
    if rec is None:
        return -1
    if not rec.exited:
        rc = rec.p.poll()
        if rc is not None:
            rec.exited = True
            rec.exit_code = rc
    return float(rec.exit_code) if rec.exited else -1


def cwd_handler(*args):
    return os.getcwd()


def cwd_set_handler(*args):
    _need_args(args, 1)
    try:
        os.chdir(_os_path(args[0]))
        return True
    except OSError:
        return False


def hostname_handler(*args):
    try:
        import socket
        return socket.gethostname()
    except (OSError, ImportError):
        return ''


def username_handler(*args):
    """native 镜像：Windows 先 GetUserNameW（advapi32），回落 %USERNAME%；
    POSIX 先 $USER/$LOGNAME，回落 getpwuid。"""
    if os.name == 'nt':
        try:
            import ctypes
            adv = ctypes.windll.advapi32
            buf = ctypes.create_unicode_buffer(257)
            size = ctypes.c_ulong(257)
            if adv.GetUserNameW(buf, ctypes.byref(size)):
                return buf.value
        except (OSError, AttributeError):
            pass
        return os.environ.get('USERNAME', '')
    u = os.environ.get('USER') or os.environ.get('LOGNAME')
    if u:
        return u
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, ImportError, OSError):
        return ''


def args_get_handler(*args):
    return _script_args()


def stdout_write_handler(*args):
    _need_args(args, 1)
    data = _prim_bytes(args[0])
    try:
        # 走文本层（与 output/print 同一缓冲）：直接写 buffer 层会撕裂行序
        # （oracle 差分实证：直写+flush 让 "P17:" 跑到全部缓冲行之前）。
        # 字节保真 = surrogateescape 往返（与文件读口径一致）。
        sys.stdout.write(data.decode('utf-8', 'surrogateescape'))
        return len(data)
    except (OSError, ValueError, AttributeError):
        return -1


def stderr_write_handler(*args):
    _need_args(args, 1)
    data = _prim_bytes(args[0])
    try:
        sys.stderr.write(data.decode('utf-8', 'surrogateescape'))
        sys.stderr.flush()
        return len(data)
    except (OSError, ValueError, AttributeError):
        return -1


def stdin_read_line_handler(*args):
    try:
        line = sys.stdin.buffer.readline()
    except (OSError, ValueError):
        return ''
    if not line:
        return ''
    if line.endswith(b'\n'):
        line = line[:-1]
    if line.endswith(b'\r'):
        line = line[:-1]
    return line.decode('utf-8', 'surrogateescape')


# ---------------------------------------------------------------- K 组镜像

def _handle_alive(v):
    if _file_of(v) is not None:
        return True
    if _proc_of(v) is not None:
        return True
    if _ffi_lib_of(v) is not None:
        return True
    if _ffi_sym_of(v) is not None:
        return True
    if _sock_of(v) is not None:
        return True
    if _plug_of(v) is not None:
        return True
    return False


def type_of_handler(*args):
    _need_args(args, 1)
    v = args[0]
    if isinstance(v, bool):
        return 'boolean'
    if isinstance(v, (int, float)):
        return 'handle' if _handle_alive(v) else 'number'
    if isinstance(v, str):
        return 'string'
    if isinstance(v, list):
        return 'array'
    if isinstance(v, dict):
        return 'dict'
    if v is None:
        return 'null'
    if _is_closure(v):
        return 'function'
    from builtin_func import QotError
    if isinstance(v, QotError):
        return 'error'
    from builtin_func import QotObject
    if isinstance(v, QotObject):
        return 'object'   # M4：与 native qotrt_type(VT_OBJ) 同律
    return 'unknown'


def type_is_handler(*args):
    _need_args(args, 2)
    return type_of_handler(args[0]) == _tostring(args[1])


def is_null_handler(*args):
    _need_args(args, 1)
    return args[0] is None


def is_array_handler(*args):
    _need_args(args, 1)
    return isinstance(args[0], list)


def is_dict_handler(*args):
    _need_args(args, 1)
    return isinstance(args[0], dict)


def is_string_handler(*args):
    _need_args(args, 1)
    return isinstance(args[0], str)


def is_bool_handler(*args):
    _need_args(args, 1)
    return isinstance(args[0], bool)


def is_handle_handler(*args):
    _need_args(args, 1)
    return _handle_alive(args[0])


def to_num_handler(*args):
    _need_args(args, 1)
    v = args[0]
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    val, ok = _parse_full_num(v)
    return val if ok else -1


def to_bool_handler(*args):
    _need_args(args, 1)
    return _truthy(args[0])


def to_array_handler(*args):
    _need_args(args, 1)
    v = args[0]
    if isinstance(v, list):
        return v
    if v is None:
        return None
    return [v]


# ---------------------------------------------------------------- L 组 FFI 镜像
# native qotrt L 组（CP-4 · 0；M7 冻结 v2 · 0）的 ctypes 薄壳。
# v2 口径两侧同律：
#   * ffi_call = 全 f64 实参 + f64 返回（libm 类）；ffi_call_v 类型码表
#     [返回码, 实参码...]，码 = void/i32/i64/f64/str/ptr；v2 混族分派器：
#     实参逐码定类（f64→XMM，i32/i64/str/ptr→GP），同族限制废除；
#   * 句柄 = 数字（本进程注册表下标，1 起，两侧各自计数 —— 差分用例不打印
#     裸句柄）；指针 = 数字地址（x64 < 2^53 精确）；
#   * 失败 → null/false/""；错误文本两侧各自持有（差分只断言非空，S-13）。

_FFI_LIBS = {}    # int → CDLL
_FFI_SYMS = {}    # int → (fn, lib_id)
_FFI_PTRS = []    # ffi_ptr 缓冲（进程生命周期持有，镜像 native 的 malloc 账面）
_FFI_NEXT = [1]
_FFI_ERR = ['']


def _ffi_err_set(msg):
    _FFI_ERR[0] = msg


def _ffi_err_clear():
    _FFI_ERR[0] = ''


def _ffi_lib_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    h = int(v)
    if h < 1:
        return None
    return _FFI_LIBS.get(h)


def _ffi_sym_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    h = int(v)
    if h < 1:
        return None
    return _FFI_SYMS.get(h)


def ffi_abi_version_handler(*args):
    return 2


def ffi_load_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        _ffi_err_set('ffi_load: missing path')
        return None
    path = _prim_bytes(args[0]).decode('utf-8', 'surrogateescape')
    if not path:
        _ffi_err_set('ffi_load: empty path')
        return None
    try:
        lib = ctypes.CDLL(path)
    except Exception:
        if sys.platform == 'win32':
            _ffi_err_set('ffi_load: LoadLibrary failed')
        else:
            _ffi_err_set('ffi_load: dlopen failed')
        return None
    h = _FFI_NEXT[0]
    _FFI_NEXT[0] += 1
    _FFI_LIBS[h] = lib
    return float(h)


def ffi_sym_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 2:
        _ffi_err_set('ffi_sym: missing args')
        return None
    lib = _ffi_lib_of(args[0])
    if lib is None:
        _ffi_err_set('ffi_sym: bad lib handle')
        return None
    name = _prim_bytes(args[1]).decode('utf-8', 'surrogateescape')
    if not name:
        _ffi_err_set('ffi_sym: empty symbol')
        return None
    try:
        fn = getattr(lib, name)
    except AttributeError:
        _ffi_err_set('ffi_sym: symbol not found')
        return None
    h = _FFI_NEXT[0]
    _FFI_NEXT[0] += 1
    _FFI_SYMS[h] = (fn, int(args[0]) if not isinstance(args[0], bool)
                    and isinstance(args[0], (int, float)) else 0)
    return float(h)


_FFI_ARGTYPES = {'i32', 'i64', 'f64', 'str', 'ptr'}
_FFI_RETTYPES = {'void', 'i32', 'i64', 'f64', 'str', 'ptr'}


def _ffi_family(code):
    if code == 'f64':
        return 0
    if code in ('i32', 'i64', 'str', 'ptr'):
        return 1
    return -1


def _ffi_int_val(v, want_str=False):
    """native qrt_ffi_int_arg 的镜像：数值取整；字符串→字节；null→NULL；
    bool→0/1；其余失败。want_str（"str" 码）：只收字符串/null —— 数字值
    当指针传 = 必崩（S-14：数字样字符串字面量在语言层就是数字）。"""
    if v is None:
        return True, None
    if isinstance(v, str):
        if not want_str:
            return False, None
        return True, v.encode('utf-8', 'surrogateescape')
    if want_str:
        return False, None
    if isinstance(v, bool):
        return True, (1 if v else 0)
    if isinstance(v, (int, float)):
        d = float(v)
        if d != int(d) or abs(d) > 9.2e18:
            return False, None
        return True, int(d)
    return False, None


def _ffi_call_impl(args, typed):
    import ctypes
    _ffi_err_clear()
    if len(args) < (3 if typed else 2):
        _ffi_err_set('ffi_call: missing args')
        return None
    sym = _ffi_sym_of(args[0])
    if sym is None:
        _ffi_err_set('ffi_call: bad symbol handle')
        return None
    fn = sym[0]
    vals = args[2] if typed else args[1]
    if not isinstance(vals, list):
        _ffi_err_set('ffi_call: args must be array')
        return None
    if not typed:
        n = len(vals)
        if n > 8:
            _ffi_err_set('ffi_call: max 8 args')
            return None
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                _ffi_err_set('ffi_call: numeric args only '
                             '(strings/pointers use ffi_call_v)')
                return None
        ctypes_args = [ctypes.c_double] * n
        ctypes_vals = [float(v) for v in vals]
        restype = ctypes.c_double
        ret_code = 'f64'
    else:
        types = args[1]
        if not isinstance(types, list) or not types or \
                not all(isinstance(t, str) for t in types):
            _ffi_err_set('ffi_call_v: bad types array')
            return None
        ret_code = types[0]
        if ret_code not in _FFI_RETTYPES:
            _ffi_err_set('ffi_call_v: bad types array')
            return None
        arg_codes = types[1:]
        n = len(arg_codes)
        if len(vals) != n:
            _ffi_err_set('ffi_call_v: args/types length mismatch')
            return None
        if n > 8:
            _ffi_err_set('ffi_call: max 8 args')
            return None
        # v2 混族：实参逐码定类（f64→XMM 类，其余→GP 类），不再要求同族
        ctypes_args = []
        ctypes_vals = []
        for code, v in zip(arg_codes, vals):
            if code not in _FFI_ARGTYPES:
                _ffi_err_set('ffi_call_v: bad types array')
                return None
            if _ffi_family(code) == 1:
                ok, x = _ffi_int_val(v, want_str=(code == 'str'))
                if not ok:
                    _ffi_err_set('ffi_call_v: argument does not match '
                                 'its type code')
                    return None
                if code == 'i32':
                    ctypes_args.append(ctypes.c_int32)
                    ctypes_vals.append(int(x) & 0xFFFFFFFF)
                elif code == 'i64':
                    ctypes_args.append(ctypes.c_int64)
                    ctypes_vals.append(int(x))
                elif code == 'str':
                    ctypes_args.append(ctypes.c_char_p)
                    ctypes_vals.append(x if x is not None else None)
                else:
                    ctypes_args.append(ctypes.c_void_p)
                    ctypes_vals.append(None if x is None else int(x) & 0xFFFFFFFFFFFFFFFF)
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    _ffi_err_set('ffi_call_v: argument does not match '
                                 'its type code')
                    return None
                ctypes_args.append(ctypes.c_double)
                ctypes_vals.append(float(v))
        restype = {'void': None, 'i32': ctypes.c_int32, 'i64': ctypes.c_int64,
                   'f64': ctypes.c_double, 'str': ctypes.c_char_p,
                   'ptr': ctypes.c_void_p}[ret_code]
    fn.restype = restype
    fn.argtypes = ctypes_args
    try:
        r = fn(*ctypes_vals)
    except Exception:
        _ffi_err_set('ffi_call: call failed')
        return None
    if not typed:
        return float(r)
    if ret_code == 'void':
        return None
    if ret_code == 'str':
        return (r or b'').decode('utf-8', 'surrogateescape')
    if ret_code == 'ptr':
        return float(r) if r is not None else 0.0
    if ret_code == 'i32':
        v = int(r)
        return float(v - 0x100000000 if v >= 0x80000000 else v)
    return float(r)


def ffi_call_handler(*args):
    return _ffi_call_impl(args, False)


def ffi_call_v_handler(*args):
    return _ffi_call_impl(args, True)


def ffi_free_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        return False
    lib = _ffi_lib_of(args[0])
    if lib is None:
        return False
    h = int(args[0])
    done = True
    if sys.platform == 'win32':
        try:
            # FreeLibrary 句柄是 64 位 HMODULE —— 必须显式 c_void_p
            # （windll 默认 c_int，句柄高位截断/类型错 → 异常 → 假 false）
            k32 = ctypes.windll.kernel32
            k32.FreeLibrary.argtypes = [ctypes.c_void_p]
            k32.FreeLibrary.restype = ctypes.c_int
            done = bool(k32.FreeLibrary(ctypes.c_void_p(lib._handle)))
        except Exception:
            done = False
    else:
        try:
            ctypes.CDLL(None).dlclose(lib._handle)
        except Exception:
            done = False
    for k in [k for k, (_, lid) in _FFI_SYMS.items() if lid == h]:
        del _FFI_SYMS[k]
    _FFI_LIBS.pop(h, None)
    if not done:
        _ffi_err_set('ffi_free: unload failed')
    return done


def ffi_last_error_handler(*args):
    return _FFI_ERR[0]


def ffi_str_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        _ffi_err_set('ffi_str: missing ptr')
        return ''
    v = args[0]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _ffi_err_set('ffi_str: bad ptr')
        return ''
    addr = int(v)
    if addr == 0:
        return ''
    # 有界读（镜像 native 1MB 上限）：分段试探，防未终止串拖垮进程
    try:
        buf = b''
        for k in (64, 4096, 1 << 20):
            buf = ctypes.string_at(addr, k)
            z = buf.find(b'\0')
            if z >= 0:
                return buf[:z].decode('utf-8', 'surrogateescape')
        _ffi_err_set('ffi_str: unterminated (1MB bound)')
        return buf[:1 << 20].decode('utf-8', 'surrogateescape')
    except Exception:
        return ''


def ffi_ptr_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        _ffi_err_set('ffi_ptr: missing value')
        return 0.0
    v = args[0]
    if isinstance(v, str):
        buf = ctypes.create_string_buffer(
            v.encode('utf-8', 'surrogateescape') + b'\0')
        _FFI_PTRS.append(buf)  # 进程生命周期持有（镜像 native malloc 账面）
        return float(ctypes.cast(buf, ctypes.c_void_p).value or 0)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _ffi_err_set('ffi_ptr: numeric or string expected')
        return 0.0
    return float(v)


# ---------------------------------------------------------------- L2 组 原生插件宿主
# native qotrt L2 组（M7 冻结 v2 · 0，docs/）的 ctypes 宿主。
# 公共头 ot-asset/c/plugin/qot_plugin_abi.h 的 Python 镜像：字段序逐项一致，
# sizeof(qot_plugin_api)==144 与 C 侧编译期探针双钉。
# 宿主私有表示（合法性的根据 = 公共头契约「可移植插件只经 api 访问值」）：
#   * shell qot_value 的 tag 放 t、标量放 u.d、非标量在 pad 7 字节里存
#     调用池下标（_PlugCtx.pool：str/list/dict 的 Python 对象）；
#   * 插件函数调用的全程 _PLUG_CUR[0] = 当前 ctx（单线程假设同全运行时）。
_PLUGIN_LIBS = {}    # int → {'cdll': CDLL, 'fns': {name: {'fn','argc','doc'}}}
_PLUGIN_NEXT = [1]
_PLUG_LOADING = [None]  # init 期“正在装载的插件”（镜像 native qotrt_pfn_loading）
_PLUG_CUR = [None]      # 当前调用 ctx


class _PlugCtx(object):
    __slots__ = ('pool', 'keep')

    def __init__(self):
        self.pool = []
        self.keep = []  # 调用期内保活的 ctypes 缓冲/对象


_PLUGIN_CTYPES = []
_PLUGIN_API_HOLDER = [None]


def _plugin_types():
    """公共头镜像（懒装配一次）：(QotV, PLUGIN_FN, API)。"""
    import ctypes
    if _PLUGIN_CTYPES:
        return _PLUGIN_CTYPES[0]

    class QotUP(ctypes.Union):
        # 公共头 qot_value 的载荷 union（d|p 同占 8 字节）—— 必须是 Union
        # 而非顺序字段：顺序写法把 shell 撑成 24B，插件按 16B 步长读
        # args[i] 即错位（首日冒烟实锤：add 的第二实参读成 0）。
        _fields_ = [('d', ctypes.c_double), ('p', ctypes.c_void_p)]

    class QotV(ctypes.Structure):
        _fields_ = [('t', ctypes.c_uint8), ('pad', ctypes.c_uint8 * 7),
                    ('u', QotUP)]

    if ctypes.sizeof(QotV) != 16:
        raise AssertionError('qot_value 镜像尺寸漂移（公共头布局 = 16B）')

    PLUGIN_FN = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV),
                                 ctypes.c_void_p, ctypes.POINTER(QotV),
                                 ctypes.c_int)

    class API(ctypes.Structure):
        _fields_ = [
            ('abi_version', ctypes.c_uint32),
            ('reserved', ctypes.c_uint32),
            ('set_null', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))),
            ('set_bool', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.c_int)),
            ('set_num', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.c_double)),
            ('set_str', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV),
                                         ctypes.POINTER(ctypes.c_char), ctypes.c_int64)),
            ('tag_of', ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV))),
            ('num_of', ctypes.CFUNCTYPE(ctypes.c_double, ctypes.POINTER(QotV))),
            ('bool_of', ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV))),
            ('str_of', ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.POINTER(QotV),
                                        ctypes.POINTER(ctypes.c_int64))),
            ('arr_len', ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.POINTER(QotV))),
            ('arr_at', ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.POINTER(QotV),
                                        ctypes.c_int64)),
            ('arr_new', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))),
            ('arr_push', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV),
                                          ctypes.POINTER(QotV))),
            ('dict_new', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))),
            ('dict_set', ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV),
                                          ctypes.c_char_p, ctypes.POINTER(QotV))),
            ('dict_get', ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV),
                                          ctypes.c_char_p, ctypes.POINTER(QotV))),
            ('set_error', ctypes.CFUNCTYPE(None, ctypes.c_char_p)),
            ('reg', ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, PLUGIN_FN,
                                     ctypes.c_int, ctypes.c_char_p)),
        ]

    if ctypes.sizeof(API) != 144:
        raise AssertionError('qot_plugin_api 镜像尺寸漂移（公共头探针 = 144）')
    _PLUGIN_CTYPES.append((QotV, PLUGIN_FN, API))
    return _PLUGIN_CTYPES[0]


def _plug_pad_set(sh, idx):
    b = idx.to_bytes(7, 'little')
    for k in range(7):
        sh.pad[k] = b[k]


def _plug_pad_get(sh):
    return int.from_bytes(bytes(sh.pad), 'little')


def _plug_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    h = int(v)
    if h < 1:
        return None
    return _PLUGIN_LIBS.get(h)


def _plug_fill(sh, v, ctx):
    """Python 值 → shell qot_value（宿主私有表示）。"""
    if v is None:
        sh.t = 1  # QOT_VT_NULL
        sh.u.d = 0.0
    elif isinstance(v, bool):
        sh.t = 2  # QOT_VT_BOOL
        sh.u.d = 1.0 if v else 0.0
    elif isinstance(v, (int, float)):
        sh.t = 3  # QOT_VT_NUM
        sh.u.d = float(v)
    elif isinstance(v, (str, list, dict)):
        sh.t = 4 if isinstance(v, str) else (5 if isinstance(v, list) else 6)
        _plug_pad_set(sh, len(ctx.pool))
        ctx.pool.append(v)
    else:
        sh.t = 1
        sh.u.d = 0.0


def _plug_unshell(sh, ctx):
    t = sh.t
    if t in (0, 1):
        return None
    if t == 2:
        return sh.u.d != 0.0
    if t == 3:
        return float(sh.u.d)
    if t in (4, 5, 6):
        return ctx.pool[_plug_pad_get(sh)]
    return None


def _plugin_api():
    """宿主 api 单例（回调常驻进程；创建即持引用）。"""
    import ctypes
    if _PLUGIN_API_HOLDER[0] is not None:
        return _PLUGIN_API_HOLDER[0]
    QotV, PLUGIN_FN, API = _plugin_types()
    c_int64_p = ctypes.POINTER(ctypes.c_int64)
    c_char_p_ = ctypes.POINTER(ctypes.c_char)

    def _set_null(v):
        try:
            v.contents.t = 1
            v.contents.u.d = 0.0
        except Exception:
            pass

    def _set_bool(v, b):
        try:
            v.contents.t = 2
            v.contents.u.d = 1.0 if b else 0.0
        except Exception:
            pass

    def _set_num(v, d):
        try:
            v.contents.t = 3
            v.contents.u.d = float(d)
        except Exception:
            pass

    def _set_str(v, s, ln):
        # 镜像 native：len<0 → NUL 测定；len≥0 → 精确读 len 字节（可含内嵌 NUL）
        try:
            ctx = _PLUG_CUR[0]
            if s is None or ln == 0 or ctx is None:
                text = ''
            elif ln < 0:
                text = ctypes.string_at(s).decode('utf-8', 'surrogateescape')
            else:
                text = ctypes.string_at(s, ln).decode('utf-8', 'surrogateescape')
            sh = v.contents
            sh.t = 4
            _plug_pad_set(sh, len(ctx.pool))
            ctx.pool.append(text)
        except Exception:
            pass

    def _tag_of(v):
        try:
            return int(v.contents.t)
        except Exception:
            return 0

    def _num_of(v):
        try:
            return float(v.contents.u.d) if v.contents.t == 3 else 0.0
        except Exception:
            return 0.0

    def _bool_of(v):
        try:
            return 1 if (v.contents.t == 2 and v.contents.u.d != 0.0) else 0
        except Exception:
            return 0

    def _str_of(v, lenp):
        try:
            ctx = _PLUG_CUR[0]
            if v.contents.t != 4 or ctx is None:
                if lenp:
                    lenp.contents.value = -1
                return None
            s = ctx.pool[_plug_pad_get(v.contents)]
            raw = s.encode('utf-8', 'surrogateescape')
            buf = ctypes.create_string_buffer(raw + b'\0')
            ctx.keep.append(buf)
            if lenp:
                lenp.contents.value = len(raw)  # 非 NUL 结尾口径，以 len 为准
            return ctypes.cast(buf, ctypes.c_void_p).value
        except Exception:
            return None

    def _arr_len(v):
        try:
            ctx = _PLUG_CUR[0]
            if v.contents.t != 5 or ctx is None:
                return -1
            return len(ctx.pool[_plug_pad_get(v.contents)])
        except Exception:
            return -1

    def _arr_at(v, i):
        try:
            ctx = _PLUG_CUR[0]
            if v.contents.t != 5 or ctx is None:
                return None
            arr = ctx.pool[_plug_pad_get(v.contents)]
            i = int(i)
            if i < 0 or i >= len(arr):
                return None
            sh = QotV()
            _plug_fill(sh, arr[i], ctx)
            ctx.keep.append(sh)
            return ctypes.addressof(sh)
        except Exception:
            return None

    def _arr_new(out):
        try:
            ctx = _PLUG_CUR[0]
            if ctx is None:
                return
            out.contents.t = 5
            _plug_pad_set(out.contents, len(ctx.pool))
            ctx.pool.append([])
        except Exception:
            pass

    def _arr_push(arr, v):
        try:
            ctx = _PLUG_CUR[0]
            if ctx is None:
                return
            arr.contents.t = 5  # 容错：未初始化的 out 当新数组
            a = ctx.pool[_plug_pad_get(arr.contents)]
            if not isinstance(a, list):
                return
            a.append(_plug_unshell(v.contents, ctx))
        except Exception:
            pass

    def _dict_new(out):
        try:
            ctx = _PLUG_CUR[0]
            if ctx is None:
                return
            out.contents.t = 6
            _plug_pad_set(out.contents, len(ctx.pool))
            ctx.pool.append({})
        except Exception:
            pass

    def _dict_set(d, k, v):
        try:
            ctx = _PLUG_CUR[0]
            if ctx is None or k is None:
                return
            d.contents.t = 6
            dd = ctx.pool[_plug_pad_get(d.contents)]
            if not isinstance(dd, dict):
                return
            key = bytes(k).decode('utf-8', 'surrogateescape')
            dd[key] = _plug_unshell(v.contents, ctx)
        except Exception:
            pass

    def _dict_get(d, k, out):
        try:
            ctx = _PLUG_CUR[0]
            if ctx is None or k is None or d.contents.t != 6:
                _set_null(out)
                return 0
            dd = ctx.pool[_plug_pad_get(d.contents)]
            key = bytes(k).decode('utf-8', 'surrogateescape')
            if not isinstance(dd, dict) or key not in dd:
                _set_null(out)
                return 0
            sh = QotV()
            _plug_fill(sh, dd[key], ctx)
            out.contents.t = sh.t
            out.contents.u.d = sh.u.d
            _plug_pad_set(out.contents, _plug_pad_get(sh))
            return 1
        except Exception:
            return 0

    def _set_error(msg):
        try:
            _FFI_ERR[0] = msg.decode('utf-8', 'surrogateescape') if msg else ''
        except Exception:
            pass

    def _reg(name, fn, argc, doc):
        # 契约镜像（native qrt_plug_reg）：空名/重名/越界 argc → 0；
        # fn 必须存进注册表保活（CFUNCTYPE 对象被 C 持有期间不可回收）。
        try:
            lib = _PLUG_LOADING[0]
            if lib is None or not name or fn is None or argc < 0 or argc > 64:
                return 0
            nm = bytes(name).decode('utf-8', 'surrogateescape')
            if not nm:
                return 0
            for other in _PLUGIN_LIBS.values():
                if nm in other['fns']:
                    return 0  # 名字跨插件全局唯一
            lib['fns'][nm] = {
                'fn': fn,
                'argc': int(argc),
                'doc': bytes(doc).decode('utf-8', 'surrogateescape') if doc else '',
            }
            return 1
        except Exception:
            return 0

    api = API()
    api.abi_version = 2
    api.reserved = 0
    api.set_null = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))(_set_null)
    api.set_bool = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.c_int)(_set_bool)
    api.set_num = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.c_double)(_set_num)
    api.set_str = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), c_char_p_, ctypes.c_int64)(_set_str)
    api.tag_of = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV))(_tag_of)
    api.num_of = ctypes.CFUNCTYPE(ctypes.c_double, ctypes.POINTER(QotV))(_num_of)
    api.bool_of = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV))(_bool_of)
    api.str_of = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.POINTER(QotV), c_int64_p)(_str_of)
    api.arr_len = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.POINTER(QotV))(_arr_len)
    api.arr_at = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.POINTER(QotV), ctypes.c_int64)(_arr_at)
    api.arr_new = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))(_arr_new)
    api.arr_push = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.POINTER(QotV))(_arr_push)
    api.dict_new = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV))(_dict_new)
    api.dict_set = ctypes.CFUNCTYPE(None, ctypes.POINTER(QotV), ctypes.c_char_p, ctypes.POINTER(QotV))(_dict_set)
    api.dict_get = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(QotV), ctypes.c_char_p, ctypes.POINTER(QotV))(_dict_get)
    api.set_error = ctypes.CFUNCTYPE(None, ctypes.c_char_p)(_set_error)
    api.reg = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, PLUGIN_FN,
                               ctypes.c_int, ctypes.c_char_p)(_reg)
    _PLUGIN_API_HOLDER[0] = api
    return api


def plugin_load_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        _ffi_err_set('plugin_load: missing path')
        return None
    path = _prim_bytes(args[0]).decode('utf-8', 'surrogateescape')
    if not path:
        _ffi_err_set('plugin_load: empty path')
        return None
    QotV, PLUGIN_FN, API = _plugin_types()
    api = _plugin_api()
    try:
        lib = ctypes.CDLL(path)
    except Exception:
        _ffi_err_set('plugin_load: LoadLibrary failed' if sys.platform == 'win32'
                     else 'plugin_load: dlopen failed')
        return None
    try:
        init = lib.qot_plugin_init
    except AttributeError:
        _ffi_err_set('plugin_load: qot_plugin_init not found')
        return None
    init.restype = ctypes.c_int
    init.argtypes = [ctypes.POINTER(API)]
    h = _PLUGIN_NEXT[0]
    entry = {'cdll': lib, 'fns': {}}
    _PLUGIN_LIBS[h] = entry
    _PLUG_LOADING[0] = entry
    base = len(entry['fns'])
    try:
        rv = init(ctypes.byref(api))
    except Exception:
        rv = -1
    _PLUG_LOADING[0] = None
    added = len(entry['fns']) - base
    if rv < 0 or rv != added:
        # init 契约违反 = 明确失败：回滚注册，不留半装载（与 native 同律）
        _PLUGIN_LIBS.pop(h, None)
        _ffi_err_set('plugin_load: plugin init failed' if rv < 0
                     else 'plugin_load: init count mismatch')
        return None
    _PLUGIN_NEXT[0] += 1
    return float(h)


def plugin_call_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 3:
        _ffi_err_set('plugin_call: missing args')
        return None
    entry = _plug_of(args[0])
    if entry is None:
        _ffi_err_set('plugin_call: bad plugin handle')
        return None
    if not isinstance(args[1], str) or not args[1]:
        _ffi_err_set('plugin_call: bad function name')
        return None
    rec = entry['fns'].get(args[1])
    if rec is None:
        _ffi_err_set('plugin_call: bad function name')
        return None
    vals = args[2]
    if not isinstance(vals, list):
        _ffi_err_set('plugin_call: bad args array')
        return None
    if len(vals) != rec['argc']:
        _ffi_err_set('plugin_call: arity mismatch')
        return None
    QotV, PLUGIN_FN, API = _plugin_types()
    ctx = _PlugCtx()
    _PLUG_CUR[0] = ctx
    try:
        n = len(vals)
        arrbuf = (QotV * n)() if n else (QotV * 1)()
        for i, v in enumerate(vals):
            _plug_fill(arrbuf[i], v, ctx)
        ret = QotV()
        ret.t = 1
        ret.u.d = 0.0
        api_addr = ctypes.addressof(_PLUGIN_API_HOLDER[0])
        fn = ctypes.cast(rec['fn'], PLUGIN_FN)
        fn(ctypes.byref(ret), api_addr,
           ctypes.cast(arrbuf, ctypes.POINTER(QotV)), n)
        if _FFI_ERR[0]:
            return None  # set_error 通道（文本已在 ffi_last_error）
        return _plug_unshell(ret, ctx)
    finally:
        _PLUG_CUR[0] = None


def plugin_free_handler(*args):
    import ctypes
    _ffi_err_clear()
    if len(args) < 1:
        return False
    entry = _plug_of(args[0])
    if entry is None:
        return False
    h = int(args[0])
    lib = entry['cdll']
    done = True
    if sys.platform == 'win32':
        try:
            # FreeLibrary 句柄必须显式 c_void_p（ffi_free 同款教训）
            k32 = ctypes.windll.kernel32
            k32.FreeLibrary.argtypes = [ctypes.c_void_p]
            k32.FreeLibrary.restype = ctypes.c_int
            done = bool(k32.FreeLibrary(ctypes.c_void_p(lib._handle)))
        except Exception:
            done = False
    else:
        try:
            ctypes.CDLL(None).dlclose(lib._handle)
        except Exception:
            done = False
    _PLUGIN_LIBS.pop(h, None)
    if not done:
        _ffi_err_set('plugin_free: unload failed')
    return done


def plugin_names_handler(*args):
    _ffi_err_clear()
    if len(args) < 1:
        return None
    entry = _plug_of(args[0])
    if entry is None:
        return None
    return list(entry['fns'].keys())


# ---------------------------------------------------------------- O 组 网络镜像
# native qotrt 网络节（P0.3 · 0 §4.9）的 socket 模块薄壳。v0 = AF_INET。
# 失败契约与 native 同律：建/收 null、连接类 false、发送 -1、接收 ""；
# 字符串 = 字节序列（D-12，utf-8/surrogateescape 往返）；句柄 = 数字
# （本进程注册表，1 起；差分用例不打印裸句柄）；UDP 对端在 recvfrom 后
# 记入 _SOCK_PEER（remote_addr 同律）。
_SOCKS = {}       # int → socket.socket
_SOCK_PEER = {}   # int → "ip:port"（connect/accept/recvfrom 填充）
_SOCK_NEXT = [1]


def _sock_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    h = int(v)
    if h < 1:
        return None
    return _SOCKS.get(h)


def _sock_install(s):
    h = _SOCK_NEXT[0]
    _SOCK_NEXT[0] += 1
    _SOCKS[h] = s
    return float(h)


def _sock_pair_of(args):
    """(handle, host_bytes, port) → (socket, addr_tuple) | None。"""
    import socket as _s
    if len(args) < 3:
        return None
    p = _sock_of(args[0])
    if p is None:
        return None
    host = args[1] if isinstance(args[1], str) else ''
    if isinstance(args[2], bool) or not isinstance(args[2], (int, float)):
        return None
    port = int(args[2])
    if port < 0 or port > 65535:
        return None
    if not host or host in ('*', '0.0.0.0'):
        addr = ('0.0.0.0', port)
    elif all(c.isdigit() or c == '.' for c in host) and host.count('.') == 3:
        addr = (host, port)
    else:
        try:
            ai = _s.getaddrinfo(host, port, _s.AF_INET, _s.SOCK_STREAM)
            addr = ai[0][4] if ai else None
        except OSError:
            addr = None
    if addr is None:
        return None
    return p, addr


def socket_new_handler(*args):
    import socket as _s
    try:
        s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    except OSError:
        return None
    return _sock_install(s)


def socket_connect_handler(*args):
    import socket as _s
    pair = _sock_pair_of(args)
    if pair is None or pair[0].type == _s.SOCK_DGRAM:
        return False
    p, addr = pair
    try:
        p.connect(addr)
    except OSError:
        return False
    _SOCK_PEER[int(args[0])] = '%s:%d' % (addr[0], addr[1])
    return True


def socket_bind_handler(*args):
    import socket as _s
    pair = _sock_pair_of(args)
    if pair is None:
        return False
    p, addr = pair
    try:
        p.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
        p.bind(addr)
    except OSError:
        return False
    return True


def socket_listen_handler(*args):
    if len(args) < 2:
        return False
    p = _sock_of(args[0])
    if p is None:
        return False
    backlog = int(args[1]) if not isinstance(args[1], bool) \
        and isinstance(args[1], (int, float)) else 8
    if backlog < 1:
        backlog = 8
    try:
        p.listen(backlog)
    except OSError:
        return False
    return True


def socket_accept_handler(*args):
    if len(args) < 1:
        return None
    p = _sock_of(args[0])
    if p is None:
        return None
    try:
        c, addr = p.accept()
    except OSError:
        return None  # 超时/失败同 null
    peer = '%s:%d' % (addr[0], addr[1])
    h = _sock_install(c)
    _SOCK_PEER[int(h)] = peer
    return h


def socket_send_handler(*args):
    if len(args) < 2:
        return -1.0
    p = _sock_of(args[0])
    if p is None:
        return -1.0
    data = args[1].encode('utf-8', 'surrogateescape') \
        if isinstance(args[1], str) else b''
    try:
        return float(p.send(data))
    except OSError:
        return -1.0


def socket_send_all_handler(*args):
    if len(args) < 2:
        return -1.0
    p = _sock_of(args[0])
    if p is None:
        return -1.0
    data = args[1].encode('utf-8', 'surrogateescape') \
        if isinstance(args[1], str) else b''
    try:
        p.sendall(data)
    except OSError:
        return -1.0
    return float(len(data))


def socket_recv_handler(*args):
    if len(args) < 2:
        return ''
    p = _sock_of(args[0])
    if p is None:
        return ''
    want = int(args[1]) if not isinstance(args[1], bool) \
        and isinstance(args[1], (int, float)) else 0
    if want < 0:
        want = 0
    if want > 1 << 20:
        want = 1 << 20
    if want == 0:
        return ''
    try:
        b = p.recv(want)
    except OSError:
        return ''  # EOF/失败/超时同 ""（与 native 同律）
    return b.decode('utf-8', 'surrogateescape')


def socket_recv_line_handler(*args):
    if len(args) < 1:
        return ''
    p = _sock_of(args[0])
    if p is None:
        return ''
    buf = bytearray()
    while len(buf) < 65536:
        try:
            b = p.recv(1)
        except OSError:
            break
        if not b:
            break  # EOF：返回已收到的部分
        if b == b'\n':
            if buf.endswith(b'\r'):
                del buf[-1]
            break
        buf += b
    return bytes(buf).decode('utf-8', 'surrogateescape')


def socket_close_handler(*args):
    if len(args) < 1:
        return False
    p = _sock_of(args[0])
    if p is None:
        return False
    h = int(args[0])
    try:
        p.close()
    except OSError:
        _SOCKS.pop(h, None)
        _SOCK_PEER.pop(h, None)
        return False
    _SOCKS.pop(h, None)
    _SOCK_PEER.pop(h, None)
    return True


def socket_set_timeout_handler(*args):
    if len(args) < 2:
        return False
    p = _sock_of(args[0])
    if p is None:
        return False
    if isinstance(args[1], bool) or not isinstance(args[1], (int, float)):
        return False
    ms = int(args[1])
    if ms < 0:
        return False
    try:
        p.settimeout(ms / 1000.0 if ms > 0 else None)
    except OSError:
        return False
    return True


def socket_set_nodelay_handler(*args):
    import socket as _s
    if len(args) < 2:
        return False
    p = _sock_of(args[0])
    if p is None:
        return False
    on = args[1] if isinstance(args[1], bool) else True
    try:
        p.setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1 if on else 0)
    except OSError:
        return False
    return True


def _sock_addr_str(pair):
    return '%s:%d' % (pair[0], pair[1])


def socket_local_addr_handler(*args):
    if len(args) < 1:
        return ''
    p = _sock_of(args[0])
    if p is None:
        return ''
    try:
        return _sock_addr_str(p.getsockname())
    except OSError:
        return ''


def socket_remote_addr_handler(*args):
    import socket as _s
    if len(args) < 1:
        return ''
    p = _sock_of(args[0])
    if p is None:
        return ''
    if p.type == _s.SOCK_DGRAM:
        return _SOCK_PEER.get(int(args[0]), '')
    try:
        return _sock_addr_str(p.getpeername())
    except OSError:
        return ''


def socket_resolve_handler(*args):
    import socket as _s
    if len(args) < 1 or not isinstance(args[0], str):
        return None
    try:
        ai = _s.getaddrinfo(args[0], None, _s.AF_INET, _s.SOCK_STREAM)
    except OSError:
        return None
    out = []
    for item in ai:
        ip = item[4][0]
        if ip not in out:
            out.append(ip)
    return out if out else None


def socket_udp_new_handler(*args):
    import socket as _s
    try:
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    except OSError:
        return None
    return _sock_install(s)


def socket_udp_send_handler(*args):
    if len(args) < 4:
        return -1.0
    p = _sock_of(args[0])
    if p is None:
        return -1.0
    pair = _sock_pair_of(args)
    if pair is None:
        return -1.0
    data = args[3].encode('utf-8', 'surrogateescape') \
        if isinstance(args[3], str) else b''
    try:
        return float(p.sendto(data, pair[1]))
    except OSError:
        return -1.0


def socket_udp_recv_handler(*args):
    if len(args) < 2:
        return ''
    p = _sock_of(args[0])
    if p is None:
        return ''
    want = int(args[1]) if not isinstance(args[1], bool) \
        and isinstance(args[1], (int, float)) else 0
    if want < 0:
        want = 0
    if want > 1 << 20:
        want = 1 << 20
    if want == 0:
        return ''
    try:
        data, addr = p.recvfrom(want)
    except OSError:
        return ''  # 超时/失败同 ""
    _SOCK_PEER[int(args[0])] = _sock_addr_str(addr)
    return data.decode('utf-8', 'surrogateescape')


def socket_select_handler(*args):
    """select 多路复用：句柄数组 + 超时 ms → 就绪句柄数组（新数组）。

    与 native 侧 qotrt_socket_select 同契约：读就绪面；非法句柄跳过；
    超时/错误 → 空数组；入参非数组 → None。
    """
    import select as _sel
    if len(args) < 2 or not isinstance(args[0], list):
        return None
    ms = int(args[1]) if not isinstance(args[1], bool) \
        and isinstance(args[1], (int, float)) else 0
    if ms < 0:
        ms = 0
    pairs = []
    for h in args[0]:
        p = _sock_of(h)
        if p is None:
            continue
        pairs.append((int(h), p))
    if not pairs:
        return []
    try:
        ready, _, _ = _sel.select([p for (_, p) in pairs], [], [],
                                  ms / 1000.0)
    except (OSError, ValueError):
        return []
    ready_set = {id(r) for r in ready}
    return [float(h) for (h, p) in pairs if id(p) in ready_set]


# ---------------------------------------------------------------- 登记表

# 解释器回调宿主（Evaluator 在 index.py 侧经 set_evaluator 注册；
# 高阶原语 arr_map/filter/reduce/sort_by 需要它回调 QotClosure）
_evaluator = None


def set_evaluator(ev):
    global _evaluator
    _evaluator = ev


def _current_evaluator():
    return _evaluator


# 名字 → handler。键 = builtins.toml 的 name；值对象的 __name__ 与清单
# interp 字段逐字一致（_apply_builtin_manifest 机械校验，错一个字即失败）。
def json_encode_handler(*args):
    """JSON 编码。数字走 **f64 规范形**（整数值无小数）—— QOT 数值是单一
    f64 类型（native V16 无 int/float 标记），解释器侧的 Python int/float
    之分若泄漏进 JSON 就会与 native 产物差一个 ".0"（native json 化时对齐）。"""
    if not args:
        raise ValueError("json_encode()需要至少一个参数")
    obj = args[0]
    indent = args[1] if len(args) > 1 and isinstance(args[1], int) else None
    ensure_ascii = args[2] if len(args) > 2 and isinstance(args[2], bool) else False
    try:
        converted_obj = _convert_to_json_serializable(obj)
        converted_obj = _json_canon_numbers(converted_obj)
        if indent is not None:
            return json.dumps(converted_obj, ensure_ascii=ensure_ascii, indent=indent)
        return json.dumps(converted_obj, ensure_ascii=ensure_ascii)
    except TypeError as e:
        raise TypeError(f"无法将对象转换为JSON: {str(e)}")
    except Exception as e:
        raise ValueError(f"JSON编码失败: {str(e)}")


def _json_canon_numbers(obj):
    """整数值的 float 归一为 int（json 数字规范形 = native qotrt_json_num）。"""
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, float):
        if obj == int(obj) and abs(obj) < 9.2e18:
            return int(obj)
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, list):
        return [_json_canon_numbers(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_canon_numbers(v) for k, v in obj.items()}
    return obj


def _convert_to_json_serializable(obj):
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_convert_to_json_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {_convert_to_json_serializable(k): _convert_to_json_serializable(v)
                for k, v in obj.items()}
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, datetime.datetime):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, types.GeneratorType):
        return [_convert_to_json_serializable(item) for item in obj]
    if hasattr(obj, '__dict__'):
        return _convert_to_json_serializable(obj.__dict__)
    return str(obj)


def json_decode_handler(*args):
    """JSON 解码。失败抛错（开发面严格 —— native 侧得 null，登记定价）。"""
    if not args:
        raise ValueError("json_decode()需要至少一个参数")
    json_str = args[0]
    if not isinstance(json_str, str):
        raise TypeError(f"json_decode()第一个参数必须是字符串，而非{type(json_str).__name__}")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON解码失败: {str(e)}")


def json_pretty_handler(*args):
    if not args:
        raise ValueError("json_pretty()需要至少一个参数")
    obj = args[0]
    indent = args[1] if len(args) > 1 and isinstance(args[1], int) else 2
    return json_encode_handler(obj, indent, True)


def json_validate_handler(*args):
    if not args:
        raise ValueError("json_valid()需要至少一个参数")
    json_str = args[0]
    if not isinstance(json_str, str):
        return False
    try:
        json.loads(json_str)
        return True
    except Exception:
        return False



_QG_DLL = None
_QG_ERR = ['']


def _qg_err_set(msg):
    _QG_ERR[0] = msg


def _qg_dll():
    """装载 qotgui.dll"""
    global _QG_DLL
    if _QG_DLL is not None:
        return _QG_DLL
    import ctypes
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(here, 'build', 'qotgui.dll'),
        os.environ.get('QOT_GUI_DLL', ''),
    ]
    for path in cands:
        if path and os.path.exists(path):
            try:
                dll = ctypes.CDLL(path)
            except Exception as e:
                _qg_err_set('qotgui 装载失败: %s' % e)
                return None
            dll.qg_init.restype = ctypes.c_int
            dll.qg_run.restype = ctypes.c_int
            dll.qg_create_window.restype = ctypes.c_int
            dll.qg_create_window.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
            for nm in ('qg_create_label', 'qg_create_button', 'qg_create_checkbox',
                       'qg_create_toggle', 'qg_create_textbox',
                       'qg_create_card'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            # 无 text 参数的控件族（C ABI 5 参）——v2 曾误挂 6 参声明，
            # 首次在 QOT 里调用即 ctypes TypeError（性能探针实测踩出）
            for nm in ('qg_create_slider', 'qg_create_progressbar',
                       'qg_create_combobox', 'qg_create_listbox',
                       'qg_create_tabcontrol', 'qg_create_spinner'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_int] * 4
            for nm in ('qg_show_window', 'qg_close_window', 'qg_destroy'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int]
            for nm in ('qg_set_text', 'qg_add_item', 'qg_set_font',
                       'qg_set_window_title'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
            dll.qg_set_window_size.restype = ctypes.c_int
            dll.qg_set_window_size.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.qg_set_visible.restype = ctypes.c_int
            dll.qg_set_visible.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_set_enabled.restype = ctypes.c_int
            dll.qg_set_enabled.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_set_pos.restype = ctypes.c_int
            dll.qg_set_pos.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.qg_set_size.restype = ctypes.c_int
            dll.qg_set_size.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.qg_set_value.restype = ctypes.c_int
            dll.qg_set_value.argtypes = [ctypes.c_int, ctypes.c_double]
            dll.qg_get_value.restype = ctypes.c_double
            dll.qg_get_value.argtypes = [ctypes.c_int]
            dll.qg_set_color.restype = ctypes.c_int
            dll.qg_set_color.argtypes = [ctypes.c_int, ctypes.c_float, ctypes.c_float,
                                         ctypes.c_float, ctypes.c_float]
            dll.qg_set_text_color.argtypes = dll.qg_set_color.argtypes
            dll.qg_set_text_color.restype = ctypes.c_int
            dll.qg_set_font_size.restype = ctypes.c_int
            dll.qg_set_font_size.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_set_radius.restype = ctypes.c_int
            dll.qg_set_radius.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_get_text.restype = ctypes.c_int
            dll.qg_get_text.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            dll.qg_poll_event.restype = ctypes.c_int
            dll.qg_poll_event.argtypes = [ctypes.POINTER(ctypes.c_int),
                                          ctypes.POINTER(ctypes.c_int),
                                          ctypes.POINTER(ctypes.c_double)]
            # ---- v2：回调 / 帧原语 / 窗口能力 / Canvas 绘制 ----
            dll.qg_bind.restype = ctypes.c_int
            dll.qg_bind.argtypes = [ctypes.c_int, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p]
            dll.qg_unbind.restype = ctypes.c_int
            dll.qg_unbind.argtypes = [ctypes.c_int]
            dll.qg_on_frame.restype = ctypes.c_int
            dll.qg_on_frame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            dll.qg_off_frame.restype = ctypes.c_int
            dll.qg_off_frame.argtypes = [ctypes.c_int]
            dll.qg_request_frame.restype = None
            dll.qg_easing.restype = ctypes.c_float
            dll.qg_easing.argtypes = [ctypes.c_int, ctypes.c_float]
            for nm in ('qg_set_window_transparent', 'qg_set_window_backdrop',
                       'qg_set_window_round', 'qg_set_window_borderless',
                       'qg_set_window_topmost', 'qg_prevent_close',
                       'qg_window_drag', 'qg_window_minimize',
                       'qg_window_maximize', 'qg_window_restore'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_set_window_backdrop.argtypes = [ctypes.c_int, ctypes.c_int,
                                                   ctypes.c_float]
            dll.qg_set_window_bg_rgba.restype = ctypes.c_int
            dll.qg_set_window_bg_rgba.argtypes = [ctypes.c_int] + [ctypes.c_float] * 8
            dll.qg_set_theme.restype = ctypes.c_int
            dll.qg_set_theme.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_set_opacity.restype = ctypes.c_int
            dll.qg_set_opacity.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_animate.restype = ctypes.c_int
            dll.qg_animate.argtypes = [ctypes.c_int, ctypes.c_int,
                                       ctypes.c_float, ctypes.c_float,
                                       ctypes.c_float, ctypes.c_int]
            dll.qg_animate_stop.restype = ctypes.c_int
            dll.qg_animate_stop.argtypes = [ctypes.c_int]
            dll.qg_create_canvas.restype = ctypes.c_int
            dll.qg_create_canvas.argtypes = [ctypes.c_int] + [ctypes.c_int] * 4
            dll.qg_canvas_on_draw.restype = ctypes.c_int
            dll.qg_canvas_on_draw.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
            dll.qg_create_image.restype = ctypes.c_int
            dll.qg_create_image.argtypes = [ctypes.c_int, ctypes.c_char_p] + [ctypes.c_int] * 4
            dll.qg_image_load.restype = ctypes.c_int
            dll.qg_image_load.argtypes = [ctypes.c_char_p]
            dll.qg_image_free.restype = None
            dll.qg_image_free.argtypes = [ctypes.c_int]
            dll.qg_image_size.restype = ctypes.c_int
            dll.qg_image_size.argtypes = [ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_int),
                                          ctypes.POINTER(ctypes.c_int)]
            for nm in ('qg_wait_event',):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
            # 绘制原语一族（float 参数必须显式声明，ctypes 默认按 double 传）
            dll.qg_draw_measure_text.restype = None
            dll.qg_draw_measure_text.argtypes = [ctypes.c_char_p, ctypes.c_float,
                                                 ctypes.POINTER(ctypes.c_float),
                                                 ctypes.POINTER(ctypes.c_float)]
            _draw_fns = {
                'qg_draw_fill_rect': [ctypes.c_float] * 8,
                'qg_draw_stroke_rect': [ctypes.c_float] * 9,
                'qg_draw_fill_round_rect': [ctypes.c_float] * 9,
                'qg_draw_stroke_round_rect': [ctypes.c_float] * 10,
                'qg_draw_fill_ellipse': [ctypes.c_float] * 8,
                'qg_draw_stroke_ellipse': [ctypes.c_float] * 9,
                'qg_draw_line': [ctypes.c_float] * 9,
                'qg_draw_gradient_rect': [ctypes.c_float] * 12 + [ctypes.c_int],
            }
            for nm, spec in _draw_fns.items():
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = None
                    f.argtypes = spec
            dll.qg_draw_text.restype = None
            dll.qg_draw_text.argtypes = [ctypes.c_char_p] + [ctypes.c_float] * 7
            dll.qg_draw_push_clip.restype = None
            dll.qg_draw_push_clip.argtypes = [ctypes.c_float] * 4
            dll.qg_draw_pop_clip.restype = None
            dll.qg_draw_pop_clip.argtypes = []
            dll.qg_draw_push_translate.restype = None
            dll.qg_draw_push_translate.argtypes = [ctypes.c_float] * 2
            dll.qg_draw_push_rotate.restype = None
            dll.qg_draw_push_rotate.argtypes = [ctypes.c_float] * 3
            dll.qg_draw_push_scale.restype = None
            dll.qg_draw_push_scale.argtypes = [ctypes.c_float] * 4
            dll.qg_draw_pop_transform.restype = None
            dll.qg_draw_pop_transform.argtypes = []
            dll.qg_draw_image.restype = None
            dll.qg_draw_image.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5
            dll.qg_draw_push_blur.restype = None
            dll.qg_draw_push_blur.argtypes = [ctypes.c_float] * 5
            dll.qg_draw_pop_blur.restype = None
            dll.qg_draw_pop_blur.argtypes = []
            # ---- v3：布局系统 ----
            dll.qg_layout_begin.restype = ctypes.c_int
            dll.qg_layout_begin.argtypes = [ctypes.c_int, ctypes.c_char_p]
            dll.qg_layout_add.restype = ctypes.c_int
            dll.qg_layout_add.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_layout_spacing.restype = ctypes.c_int
            dll.qg_layout_spacing.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_layout_padding.restype = ctypes.c_int
            dll.qg_layout_padding.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_layout_cols.restype = ctypes.c_int
            dll.qg_layout_cols.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_layout_props.restype = ctypes.c_int
            dll.qg_layout_props.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4 + \
                                           [ctypes.c_int, ctypes.c_int, ctypes.c_float]
            dll.qg_layout_end.restype = ctypes.c_int
            dll.qg_layout_end.argtypes = [ctypes.c_int]
            # v3：位置/尺寸读回（布局重排断言用）
            dll.qg_get_pos.restype = ctypes.c_int
            dll.qg_get_pos.argtypes = [ctypes.c_int,
                                       ctypes.POINTER(ctypes.c_int),
                                       ctypes.POINTER(ctypes.c_int)]
            dll.qg_get_size.restype = ctypes.c_int
            dll.qg_get_size.argtypes = [ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int),
                                        ctypes.POINTER(ctypes.c_int)]
            dll.qg_window_client_size.restype = ctypes.c_int
            dll.qg_window_client_size.argtypes = [ctypes.c_int,
                                                  ctypes.POINTER(ctypes.c_int),
                                                  ctypes.POINTER(ctypes.c_int)]
            # ---- v3：滚动容器与虚拟化列表 ----
            for nm in ('qg_create_scrollpane', 'qg_create_vlist'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_int] * 4
            dll.qg_scroll_content.restype = ctypes.c_int
            dll.qg_scroll_content.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_scroll_attach.restype = ctypes.c_int
            dll.qg_scroll_attach.argtypes = [ctypes.c_int] + [ctypes.c_int] * 5
            dll.qg_scroll_offset.restype = ctypes.c_double
            dll.qg_scroll_offset.argtypes = [ctypes.c_int]
            dll.qg_scroll_to.restype = ctypes.c_int
            dll.qg_scroll_to.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_list_set_count.restype = ctypes.c_int
            dll.qg_list_set_count.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_list_add_item.restype = ctypes.c_int
            dll.qg_list_add_item.argtypes = [ctypes.c_int, ctypes.c_char_p]
            dll.qg_list_draw_count.restype = ctypes.c_int
            dll.qg_list_draw_count.argtypes = [ctypes.c_int]
            dll.qg_list_on_data.restype = ctypes.c_int
            dll.qg_list_on_data.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
            # ---- v3：模态对话框 / 菜单 / toast ----
            dll.qg_show_dialog.restype = ctypes.c_int
            dll.qg_show_dialog.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_set_dialog_result.restype = ctypes.c_int
            dll.qg_set_dialog_result.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_create_menu.restype = ctypes.c_int
            dll.qg_create_menu.argtypes = []
            dll.qg_menu_add_item.restype = ctypes.c_int
            dll.qg_menu_add_item.argtypes = [ctypes.c_int, ctypes.c_char_p]
            dll.qg_menu_separator.restype = ctypes.c_int
            dll.qg_menu_separator.argtypes = [ctypes.c_int]
            dll.qg_menu_popup.restype = ctypes.c_int
            dll.qg_menu_popup.argtypes = [ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int]
            dll.qg_menu_destroy.restype = ctypes.c_int
            dll.qg_menu_destroy.argtypes = [ctypes.c_int]
            dll.qg_toast.restype = ctypes.c_int
            dll.qg_toast.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            # ---- v3：文本富能力 ----
            dll.qg_draw_text_ex.restype = None
            dll.qg_draw_text_ex.argtypes = [ctypes.c_char_p] + [ctypes.c_float] * 9 + \
                                           [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int]
            dll.qg_draw_measure_text_ex.restype = None
            dll.qg_draw_measure_text_ex.argtypes = [ctypes.c_char_p, ctypes.c_float,
                                                    ctypes.c_int, ctypes.c_int,
                                                    ctypes.c_float,
                                                    ctypes.POINTER(ctypes.c_float),
                                                    ctypes.POINTER(ctypes.c_float)]
            # ---- v3：系统集成 ----
            dll.qg_clipboard_set.restype = ctypes.c_int
            dll.qg_clipboard_set.argtypes = [ctypes.c_char_p]
            dll.qg_clipboard_get.restype = ctypes.c_int
            dll.qg_clipboard_get.argtypes = [ctypes.c_char_p, ctypes.c_int]
            dll.qg_window_accept_files.restype = ctypes.c_int
            dll.qg_window_accept_files.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_take_drop_file.restype = ctypes.c_int
            dll.qg_take_drop_file.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            dll.qg_taskbar_progress.restype = ctypes.c_int
            dll.qg_taskbar_progress.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.qg_taskbar_state.restype = ctypes.c_int
            dll.qg_taskbar_state.argtypes = [ctypes.c_int, ctypes.c_int]
            # ---- v4：样式原语 ----
            for nm in ('qg_style_bg', 'qg_menu_bg', 'qg_menu_hover_bg',
                       'qg_menu_text_color'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_style_radius.restype = ctypes.c_int
            dll.qg_style_radius.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_style_reset.restype = ctypes.c_int
            dll.qg_style_reset.argtypes = [ctypes.c_int]
            dll.qg_style_bg_gradient.restype = ctypes.c_int
            dll.qg_style_bg_gradient.argtypes = [ctypes.c_int] + [ctypes.c_float] * 8 + [ctypes.c_int]
            dll.qg_style_border.restype = ctypes.c_int
            dll.qg_style_border.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5
            dll.qg_style_padding.restype = ctypes.c_int
            dll.qg_style_padding.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_style_text.restype = ctypes.c_int
            dll.qg_style_text.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5 + [ctypes.c_int]
            for nm in ('qg_style_state_bg', 'qg_style_state_text'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_style_flags.restype = ctypes.c_uint
            dll.qg_style_flags.argtypes = [ctypes.c_int]
            dll.qg_on_paint.restype = ctypes.c_int
            dll.qg_on_paint.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
            dll.qg_toast_style.restype = ctypes.c_int
            dll.qg_toast_style.argtypes = [ctypes.c_int] + [ctypes.c_float] * 13
            dll.qg_scroll_style.restype = ctypes.c_int
            dll.qg_scroll_style.argtypes = [ctypes.c_int] + [ctypes.c_float] * 9
            dll.qg_menu_border.restype = ctypes.c_int
            dll.qg_menu_border.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5
            # ---- 绘制原语（路径/画刷/富文本/效果层/离屏面） ----
            for nm, ret in (('qg_path_reset', ctypes.c_int),
                            ('qg_path_close', ctypes.c_int),
                            ('qg_brush_end', ctypes.c_int),
                            ('qg_rich_begin', ctypes.c_int),
                            ('qg_draw_pop_blend', ctypes.c_int),
                            ('qg_draw_pop_shadow', ctypes.c_int),
                            ('qg_draw_pop_mask', ctypes.c_int),
                            ('qg_draw_push_clip_path', ctypes.c_int),
                            ('qg_surface_end', ctypes.c_int),
                            ('qg_backend_info', ctypes.c_int)):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ret
                    f.argtypes = []
            for nm in ('qg_path_move_to', 'qg_path_line_to'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_float] * 2
            for nm in ('qg_path_quad_to',):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_float] * 4
            dll.qg_path_bezier_to.restype = ctypes.c_int
            dll.qg_path_bezier_to.argtypes = [ctypes.c_float] * 6
            dll.qg_path_arc.restype = ctypes.c_int
            dll.qg_path_arc.argtypes = [ctypes.c_float] * 6
            dll.qg_path_svg_arc.restype = ctypes.c_int
            dll.qg_path_svg_arc.argtypes = [ctypes.c_float] * 3 + \
                                           [ctypes.c_int] * 2 + [ctypes.c_float] * 2
            for nm in ('qg_path_rect', 'qg_draw_fill_rect_brush'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4 \
                        if nm.startswith('qg_draw') else [ctypes.c_float] * 4
            dll.qg_path_round_rect.restype = ctypes.c_int
            dll.qg_path_round_rect.argtypes = [ctypes.c_float] * 6
            dll.qg_path_ellipse.restype = ctypes.c_int
            dll.qg_path_ellipse.argtypes = [ctypes.c_float] * 4
            dll.qg_path_fill.restype = ctypes.c_int
            dll.qg_path_fill.argtypes = [ctypes.c_float] * 4
            dll.qg_path_fill_brush.restype = ctypes.c_int
            dll.qg_path_fill_brush.argtypes = [ctypes.c_int]
            dll.qg_path_stroke.restype = ctypes.c_int
            dll.qg_path_stroke.argtypes = [ctypes.c_float] * 5
            dll.qg_path_stroke_brush.restype = ctypes.c_int
            dll.qg_path_stroke_brush.argtypes = [ctypes.c_int, ctypes.c_float]
            dll.qg_path_bounds.restype = ctypes.c_int
            dll.qg_path_bounds.argtypes = [ctypes.POINTER(ctypes.c_float)] * 4
            dll.qg_path_hit.restype = ctypes.c_int
            dll.qg_path_hit.argtypes = [ctypes.c_float, ctypes.c_float]
            dll.qg_path_stroke_hit.restype = ctypes.c_int
            dll.qg_path_stroke_hit.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
            for nm in ('qg_path_line_cap', 'qg_path_line_join'):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int]
            dll.qg_path_miter_limit.restype = ctypes.c_int
            dll.qg_path_miter_limit.argtypes = [ctypes.c_float]
            dll.qg_path_dash.restype = ctypes.c_int
            dll.qg_path_dash.argtypes = [ctypes.POINTER(ctypes.c_float),
                                         ctypes.c_int, ctypes.c_float]
            dll.qg_brush_solid.restype = ctypes.c_int
            dll.qg_brush_solid.argtypes = [ctypes.c_float] * 4
            dll.qg_brush_linear_begin.restype = ctypes.c_int
            dll.qg_brush_linear_begin.argtypes = [ctypes.c_float] * 4
            dll.qg_brush_radial_begin.restype = ctypes.c_int
            dll.qg_brush_radial_begin.argtypes = [ctypes.c_float] * 6
            dll.qg_brush_add_stop.restype = ctypes.c_int
            dll.qg_brush_add_stop.argtypes = [ctypes.c_float] * 5
            dll.qg_brush_free.restype = None
            dll.qg_brush_free.argtypes = [ctypes.c_int]
            dll.qg_brush_extend.restype = ctypes.c_int
            dll.qg_brush_extend.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            dll.qg_brush_transform.restype = ctypes.c_int
            dll.qg_brush_transform.argtypes = [ctypes.c_int] + [ctypes.c_float] * 6
            dll.qg_brush_bitmap.restype = ctypes.c_int
            dll.qg_brush_bitmap.argtypes = [ctypes.c_int]
            for nm, n in (('qg_draw_stroke_rect_brush', 5),
                          ('qg_draw_fill_round_rect_brush', 5),
                          ('qg_draw_stroke_round_rect_brush', 6),
                          ('qg_draw_stroke_ellipse_brush', 5)):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_float] * n
            for nm in ('qg_draw_fill_ellipse_brush',):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_rich_add.restype = ctypes.c_int
            dll.qg_rich_add.argtypes = [ctypes.c_char_p, ctypes.c_float] + \
                                       [ctypes.c_float] * 4 + [ctypes.c_int] * 4
            dll.qg_rich_add_brush.restype = ctypes.c_int
            dll.qg_rich_add_brush.argtypes = [ctypes.c_char_p, ctypes.c_float,
                                              ctypes.c_int] + [ctypes.c_int] * 4
            dll.qg_rich_line_height.restype = ctypes.c_int
            dll.qg_rich_line_height.argtypes = [ctypes.c_float, ctypes.c_float]
            dll.qg_rich_spacing.restype = ctypes.c_int
            dll.qg_rich_spacing.argtypes = [ctypes.c_float] * 4
            dll.qg_rich_draw.restype = ctypes.c_int
            dll.qg_rich_draw.argtypes = [ctypes.c_float] * 4 + [ctypes.c_int] * 3
            dll.qg_rich_measure.restype = ctypes.c_int
            dll.qg_rich_measure.argtypes = [ctypes.c_int, ctypes.c_float,
                                            ctypes.POINTER(ctypes.c_float),
                                            ctypes.POINTER(ctypes.c_float)]
            dll.qg_draw_push_blend.restype = ctypes.c_int
            dll.qg_draw_push_blend.argtypes = [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_draw_push_shadow.restype = ctypes.c_int
            dll.qg_draw_push_shadow.argtypes = [ctypes.c_float] * 10
            dll.qg_draw_push_mask.restype = ctypes.c_int
            dll.qg_draw_push_mask.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5
            for nm in ('qg_surface_create',):
                f = getattr(dll, nm, None)
                if f is not None:
                    f.restype = ctypes.c_int
                    f.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.qg_surface_begin.restype = ctypes.c_int
            dll.qg_surface_begin.argtypes = [ctypes.c_int]
            dll.qg_surface_draw.restype = ctypes.c_int
            dll.qg_surface_draw.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5 + \
                                           [ctypes.c_int]
            dll.qg_surface_pixel.restype = ctypes.c_int
            dll.qg_surface_pixel.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int] + \
                                            [ctypes.POINTER(ctypes.c_float)] * 4
            dll.qg_surface_free.restype = None
            dll.qg_surface_free.argtypes = [ctypes.c_int]
            dll.qg_draw_image_ex.restype = ctypes.c_int
            dll.qg_draw_image_ex.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5 + \
                                            [ctypes.c_int] + [ctypes.c_float] * 4
            dll.qg_draw_image_round.restype = ctypes.c_int
            dll.qg_draw_image_round.argtypes = [ctypes.c_int] + [ctypes.c_float] * 5 + \
                                               [ctypes.c_int] + [ctypes.c_float]
            dll.qg_backend_info.restype = ctypes.c_int
            dll.qg_backend_info.argtypes = [ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int)]
            _QG_DLL = dll
            return dll
    _qg_err_set('找不到 qotgui.dll —— 请先运行 '
                'python ot-asset/c/wingui/build_qotgui.py 构建')
    return None


def _qg_bytes(v):
    if v is None:
        return b''
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode('utf-8')


def _qg_num(v, default=0.0):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except Exception:
        return default


def _qg_color(args, offset):
    """颜色参数：[r,g,b,a]（0~1）或四个数；取不到返回 None。"""
    if len(args) > offset and isinstance(args[offset], (list, tuple)):
        a = args[offset]
        vals = [_qg_num(x, 0.0) for x in a[:4]]
        while len(vals) < 4:
            vals.append(1.0 if len(vals) == 3 else 0.0)
        return vals
    if len(args) >= offset + 4:
        return [_qg_num(args[offset]), _qg_num(args[offset + 1]),
                _qg_num(args[offset + 2]), _qg_num(args[offset + 3])]
    return None
def _named(fn, nm):
    """给共享实现包一层具名包装：清单校验要求 interp 字段 == 实现函数名，
    多个内置共用同一实现时各要有自己的名字。"""
    def g(*a, **k):
        return fn(*a, **k)
    g.__name__ = nm
    return g


def _vg_create_named(builtin_name, kind):
    def h(*args):
        dll = _qg_dll()
        if not dll:
            return None
        win = int(_qg_num(args[0], 0)) if args else 0
        fn = getattr(dll, 'qg_create_' + kind, None)
        if fn is None:
            return None
        # 无 text 参数的控件族（C ABI 5 参）；v6 修正：combobox/listbox/
        # tabcontrol 此前误走 6 参分支（参数错位 + args[5] 越界）
        if kind in ('slider', 'progressbar', 'spinner',
                    'combobox', 'listbox', 'tabcontrol',
                    'table', 'colorpicker', 'datepicker'):
            x = int(_qg_num(args[1], 50)); y = int(_qg_num(args[2], 50))
            w = int(_qg_num(args[3], 160)); hh = int(_qg_num(args[4], 28))
            r = fn(win, x, y, w, hh)
        else:
            text = _qg_bytes(args[1]) if len(args) > 1 else b''
            x = int(_qg_num(args[2], 50)); y = int(_qg_num(args[3], 50))
            w = int(_qg_num(args[4], 160)); hh = int(_qg_num(args[5], 32))
            r = fn(win, text, x, y, w, hh)
        return r if r else None
    h.__name__ = builtin_name + '_handler'
    return h


def _vg_set_pos(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    dll.qg_set_pos(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)), int(_qg_num(args[2], 0)))
    return True


def _vg_clear(*args):
    """清空条目：wingui 当前没有清空接口，明确返回 false（不静默无效）。"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_clear_items(int(_qg_num(args[0], 0))) == 1


def _vg_noop_true(*args):
    """仅影响观感的设置（阴影强度 / 转圈启停）：wingui 侧内部处理。"""
    return True


def _vg_create(kind):
    def h(*args):
        dll = _qg_dll()
        if not dll:
            return None
        win = int(_qg_num(args[0], 0)) if args else 0
        fn = getattr(dll, 'qg_create_' + kind, None)
        if fn is None:
            return None
        # 无 text 参数的控件族（C ABI 5 参）；v6 修正同 _vg_create_named
        if kind in ('slider', 'progressbar', 'spinner',
                    'combobox', 'listbox', 'tabcontrol',
                    'table', 'colorpicker', 'datepicker'):
            x = int(_qg_num(args[1], 50)); y = int(_qg_num(args[2], 50))
            w = int(_qg_num(args[3], 160)); hh = int(_qg_num(args[4], 28))
            r = fn(win, x, y, w, hh)
        else:
            text = _qg_bytes(args[1]) if len(args) > 1 else b''
            x = int(_qg_num(args[2], 50)); y = int(_qg_num(args[3], 50))
            w = int(_qg_num(args[4], 160)); hh = int(_qg_num(args[5], 32))
            r = fn(win, text, x, y, w, hh)
        return r if r else None
    return h


def vg_init_handler(*args):
    dll = _qg_dll()
    return bool(dll) and dll.qg_init() == 1


def vg_uninit_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_uninit()
    return True


def vg_create_window_handler(*args):
    dll = _qg_dll()
    if not dll:
        return None
    title = _qg_bytes(args[0]) if args else b'QOT Window'
    w = int(_qg_num(args[1], 800)) if len(args) > 1 else 800
    h = int(_qg_num(args[2], 600)) if len(args) > 2 else 600
    r = dll.qg_create_window(title, w, h)
    return r if r else None


def vg_show_window_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_show_window(int(_qg_num(args[0], 0))) == 1


def vg_close_window_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_close_window(int(_qg_num(args[0], 0))) == 1


def vg_run_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_run()
    return True


def vg_set_window_title_handler(*args):
    dll = _qg_dll()
    return bool(dll) and len(args) >= 2 and \
        dll.qg_set_window_title(int(_qg_num(args[0], 0)), _qg_bytes(args[1])) == 1


def vg_set_window_size_handler(*args):
    dll = _qg_dll()
    return bool(dll) and len(args) >= 3 and \
        dll.qg_set_window_size(int(_qg_num(args[0], 0)),
                               int(_qg_num(args[1], 800)),
                               int(_qg_num(args[2], 600))) == 1


def vg_set_window_bg_gradient_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return False
    c0 = _qg_color(args, 1) or [0.05, 0.05, 0.12, 1.0]
    c1 = _qg_color(args, 2) or [0.12, 0.08, 0.22, 1.0]
    return dll.qg_set_window_bg(int(_qg_num(args[0], 0)),
                                _c.c_float(c0[0]), _c.c_float(c0[1]), _c.c_float(c0[2]),
                                _c.c_float(c1[0]), _c.c_float(c1[1]), _c.c_float(c1[2])) == 1


def vg_poll_event_handler(*args):
    """取一条界面事件 → [控件 id, kind, value]；无事件返回 null。"""
    dll = _qg_dll()
    if not dll:
        return None
    import ctypes as _c
    cid = _c.c_int(0)
    kind = _c.c_int(0)
    val = _c.c_double(0.0)
    if dll.qg_poll_event(_c.byref(cid), _c.byref(kind), _c.byref(val)) == 0:
        return None
    return [cid.value, kind.value, val.value]


def vg_wait_event_handler(*args):
    """等一条界面事件（内部泵消息，空闲近零 CPU）→ [id, kind, value]；
    超时 null；kind 9 = 窗口已关闭。推荐主循环用它而不是 poll。"""
    dll = _qg_dll()
    if not dll:
        return None
    import ctypes as _c
    tmo = int(_qg_num(args[0], 15)) if args else 15
    if tmo <= 0:
        tmo = 15
    dll.qg_wait_event.restype = _c.c_int
    dll.qg_wait_event.argtypes = [_c.POINTER(_c.c_int), _c.POINTER(_c.c_int),
                                  _c.POINTER(_c.c_double), _c.c_int]
    cid = _c.c_int(0)
    kind = _c.c_int(0)
    val = _c.c_double(0.0)
    if dll.qg_wait_event(_c.byref(cid), _c.byref(kind), _c.byref(val), tmo) == 0:
        return None
    return [cid.value, kind.value, val.value]


def vg_last_error_handler(*args):
    return _QG_ERR[0]


# ===========================================================================
# v2 · 事件回调 / 帧原语 / Canvas 绘制（原语化）
#
# ctypes CFUNCTYPE 生成 Python 函数的 C 指针；cookie（整数）映射回 QOT 闭包，
# 事件触发时经 evaluator 调用。回调线程 = 消息循环线程（vg_run 阻塞处 =
# 本线程），QOT 闭包内可直接调用任何 vg_* 接口。
# ===========================================================================
_QG_BINDS = {}          # cookie(int) -> QotClosure
_QG_CB_REF = ['']       # CFUNCTYPE 实例保活槽（防 GC 回收回调函数）
_QG_DRAW_CB_REF = ['']

_QG_EVT_CB_TYPE = None


def _qg_evt_cb_type():
    """事件回调的 C 签名：fn(id, kind, value, x, y, userdata)"""
    global _QG_EVT_CB_TYPE
    if _QG_EVT_CB_TYPE is None:
        import ctypes as _c
        _QG_EVT_CB_TYPE = _c.CFUNCTYPE(
            None, _c.c_int, _c.c_int, _c.c_double, _c.c_int, _c.c_int, _c.c_void_p)
    return _QG_EVT_CB_TYPE


def _qg_evt_trampoline():
    """模块级单例回调（保活）：cookie → QOT 闭包 → evaluator 调用。
    统一 QOT 侧签名：fn(id, value, x, y)。"""
    if _QG_CB_REF[0]:
        return _QG_CB_REF[0]
    import ctypes as _c
    ty = _qg_evt_cb_type()

    def _cb(cid, kind, value, x, y, ud):
        clo = _QG_BINDS.get(ud or 0)
        if clo is None:
            return
        ev = _current_evaluator()
        if ev is None:
            return
        try:
            ev._call_closure(clo, [cid, value, x, y])
        except Exception:
            pass   # 回调异常不炸 C 消息循环（与 AOT 侧口径一致）

    _QG_CB_REF[0] = ty(_cb)
    return _QG_CB_REF[0]


def _qg_frame_trampoline():
    """帧回调单例：fn(dt)。"""
    if _QG_DRAW_CB_REF[0]:
        return _QG_DRAW_CB_REF[0]
    import ctypes as _c
    ty = _c.CFUNCTYPE(None, _c.c_double, _c.c_void_p)

    def _cb(dt, ud):
        clo = _QG_BINDS.get(ud or 0)
        if clo is None:
            return
        ev = _current_evaluator()
        if ev is None:
            return
        try:
            ev._call_closure(clo, [dt])
        except Exception:
            pass

    _QG_DRAW_CB_REF[0] = ty(_cb)
    return _QG_DRAW_CB_REF[0]


def _qg_canvas_cb():
    """Canvas 绘制回调单例：fn(w, h)（与帧回调分开保活）。"""
    global _QG_EVT_CB_TYPE
    slot = _QG_CANVAS_CB_REF
    if slot[0]:
        return slot[0]
    import ctypes as _c
    ty = _c.CFUNCTYPE(None, _c.c_float, _c.c_float, _c.c_void_p)

    def _cb(w, h, ud):
        clo = _QG_BINDS.get(ud or 0)
        if clo is None:
            return
        ev = _current_evaluator()
        if ev is None:
            return
        try:
            ev._call_closure(clo, [w, h])
        except Exception:
            pass

    slot[0] = ty(_cb)
    return slot[0]


def _qg_paint_cb():
    """owner-draw 回调单例（v4）：fn(w, h, state)（与 canvas 回调分开保活）。"""
    slot = _QG_PAINT_CB_REF
    if slot[0]:
        return slot[0]
    import ctypes as _c
    ty = _c.CFUNCTYPE(None, _c.c_float, _c.c_float, _c.c_uint, _c.c_void_p)

    def _cb(w, h, state, ud):
        clo = _QG_BINDS.get(ud or 0)
        if clo is None:
            return
        ev = _current_evaluator()
        if ev is None:
            return
        try:
            ev._call_closure(clo, [w, h, float(state)])
        except Exception:
            pass

    slot[0] = ty(_cb)
    return slot[0]


_QG_CANVAS_CB_REF = ['']
_QG_PAINT_CB_REF = ['']
_QG_FRAME_MARK = object()
_QG_NEXT_COOKIE = [1]


def _qg_cookie(clo):
    """闭包登记：cookie → QOT 闭包（回调触发时反查）。"""
    cookie = _QG_NEXT_COOKIE[0]
    _QG_NEXT_COOKIE[0] += 1
    _QG_BINDS[cookie] = clo
    return cookie


def vg_bind_handler(*args):
    """通用绑定：vg_bind(id, kinds_mask, fn) → token | null"""
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return None
    clo = args[2]
    if not _is_closure(clo):
        return None
    cookie = _qg_cookie(clo)
    import ctypes as _c
    token = dll.qg_bind(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                        _c.cast(_qg_evt_trampoline(), _c.c_void_p),
                        _c.c_void_p(cookie))
    return token if token else None


def vg_unbind_handler(*args):
    dll = _qg_dll()
    if not dll or not args:
        return False
    ok = dll.qg_unbind(int(_qg_num(args[0], 0))) == 1
    if ok:
        # 注册表项生命周期与 token 解耦（cookie 槽留待进程末清理，量级可忽略）
        pass
    return ok


def _vg_on_kind(mask):
    def h(*args):
        dll = _qg_dll()
        if not dll or len(args) < 2:
            return None
        clo = args[-1]
        if not _is_closure(clo):
            return None
        cookie = _qg_cookie(clo)
        import ctypes as _c
        token = dll.qg_bind(int(_qg_num(args[0], 0)), mask,
                            _c.cast(_qg_evt_trampoline(), _c.c_void_p),
                            _c.c_void_p(cookie))
        return token if token else None
    return h


def vg_on_click_handler(*args):
    return _vg_on_kind(0x00001)(*args)


def vg_on_change_handler(*args):
    return _vg_on_kind(0x00002)(*args)


def vg_on_select_handler(*args):
    return _vg_on_kind(0x00004)(*args)


def vg_on_mouse_handler(*args):
    """vg_on_mouse(id, kind, fn)：kind = 10 按下 11 抬起 12 移动 13 进入 14 离开 15 双击"""
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return None
    clo = args[2]
    if not _is_closure(clo):
        return None
    cookie = _qg_cookie(clo)
    import ctypes as _c
    token = dll.qg_bind(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                        _c.cast(_qg_evt_trampoline(), _c.c_void_p),
                        _c.c_void_p(cookie))
    return token if token else None


def vg_on_key_handler(*args):
    return _vg_on_kind(0x00800)(*args)


def vg_on_text_handler(*args):
    return _vg_on_kind(0x02000)(*args)


def vg_on_close_handler(*args):
    return _vg_on_kind(0x04000)(*args)


def vg_on_resize_handler(*args):
    return _vg_on_kind(0x08000)(*args)


def vg_on_load_handler(*args):
    return _vg_on_kind(0x40000)(*args)


def vg_on_scroll_handler(*args):
    """滚动事件绑定（kind=16，value=偏移）：vg_on_scroll(id, fn) → token"""
    return _vg_on_kind(0x80000)(*args)


def vg_on_menu_handler(*args):
    """菜单命令绑定（kind=50，value=选中项 id）：vg_on_menu(win, fn) → token"""
    return _vg_on_kind(0x100000)(*args)


def vg_on_drop_handler(*args):
    """拖放文件绑定（kind=51，value=文件数，x/y=落点）：vg_on_drop(win, fn) → token"""
    return _vg_on_kind(0x200000)(*args)


def vg_on_frame_handler(*args):
    """帧原语：vg_on_frame(fn) → token；回调 fn(dt)，需要下一帧再 vg_request_frame()"""
    dll = _qg_dll()
    if not dll or not args:
        return None
    clo = args[0]
    if not _is_closure(clo):
        return None
    cookie = _qg_cookie(clo)
    import ctypes as _c
    token = dll.qg_on_frame(_c.cast(_qg_frame_trampoline(), _c.c_void_p),
                            _c.c_void_p(cookie))
    return token if token else None


def vg_off_frame_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_off_frame(int(_qg_num(args[0], 0))) == 1


def vg_request_frame_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_request_frame()
    return True


def vg_easing_handler(*args):
    """缓动纯函数：vg_easing(t, kind) → 0..1（kind 0..11）"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return 0.0
    return float(dll.qg_easing(int(_qg_num(args[1], 0)), float(_qg_num(args[0], 0.0))))


def vg_window_transparent_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_window_transparent(int(_qg_num(args[0], 0)),
                                         1 if _qg_num(args[1], 0) else 0) == 1


def vg_window_backdrop_handler(*args):
    """系统合成器背景模糊原语：vg_window_backdrop(win, on[, tint_alpha])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_window_backdrop(int(_qg_num(args[0], 0)),
                                      1 if _qg_num(args[1], 0) else 0,
                                      _c.c_float(float(_qg_num(args[2], 0.5)))) == 1


def vg_set_window_round_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_window_round(int(_qg_num(args[0], 0)),
                                   1 if _qg_num(args[1], 0) else 0) == 1


def vg_set_window_borderless_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_window_borderless(int(_qg_num(args[0], 0)),
                                        1 if _qg_num(args[1], 0) else 0) == 1


def vg_set_window_topmost_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_window_topmost(int(_qg_num(args[0], 0)),
                                     1 if _qg_num(args[1], 0) else 0) == 1


def vg_prevent_close_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_prevent_close(int(_qg_num(args[0], 0)),
                                1 if _qg_num(args[1], 0) else 0) == 1


def vg_window_drag_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_window_drag(int(_qg_num(args[0], 0))) == 1


def vg_window_minimize_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_window_minimize(int(_qg_num(args[0], 0))) == 1


def vg_window_maximize_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_window_maximize(int(_qg_num(args[0], 0))) == 1


def vg_window_restore_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_window_restore(int(_qg_num(args[0], 0))) == 1


def vg_set_window_bg_rgba_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c0 = _qg_color(args, 1) or [0.05, 0.05, 0.12, 1.0]
    c1 = _qg_color(args, 2) or c0
    return dll.qg_set_window_bg_rgba(
        int(_qg_num(args[0], 0)),
        _c.c_float(c0[0]), _c.c_float(c0[1]), _c.c_float(c0[2]), _c.c_float(c0[3]),
        _c.c_float(c1[0]), _c.c_float(c1[1]), _c.c_float(c1[2]), _c.c_float(c1[3])) == 1


def vg_set_theme_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_theme(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0))) == 1


def vg_set_font_handler(*args):
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_set_font(_qg_bytes(args[0])) == 1


def vg_set_opacity_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_opacity(int(_qg_num(args[0], 0)),
                              _c.c_float(float(_qg_num(args[1], 1.0)))) == 1


def vg_animate_handler(*args):
    """动画糖：vg_animate(id, prop, from, to, ms, easing) → 动画 id | null
    prop: 0 x 1 y 2 w 3 h 4 opacity；完成发 kind=8 事件。"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return None
    anim = dll.qg_animate(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                          _c.c_float(float(_qg_num(args[2], 0.0))),
                          _c.c_float(float(_qg_num(args[3], 0.0))),
                          _c.c_float(float(_qg_num(args[4], 200.0))),
                          _c.c_int(int(_qg_num(args[5], 2))))
    return anim if anim else None


def vg_animate_stop_handler(*args):
    dll = _qg_dll()
    return bool(dll) and args and dll.qg_animate_stop(int(_qg_num(args[0], 0))) == 1


def vg_create_canvas_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll:
        return None
    win = int(_qg_num(args[0], 0)) if args else 0
    x = int(_qg_num(args[1], 20)) if len(args) > 1 else 20
    y = int(_qg_num(args[2], 20)) if len(args) > 2 else 20
    w = int(_qg_num(args[3], 200)) if len(args) > 3 else 200
    hh = int(_qg_num(args[4], 200)) if len(args) > 4 else 200
    r = dll.qg_create_canvas(win, x, y, w, hh)
    return r if r else None


def vg_canvas_on_draw_handler(*args):
    """vg_canvas_on_draw(id, fn)：每帧绘制本画布时调 fn(w, h)，
    回调内用 vg_draw_* 原语自由作画（坐标相对画布原点）。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    clo = args[1]
    if not _is_closure(clo):
        return False
    cookie = _qg_cookie(clo)
    import ctypes as _c
    cb = _qg_canvas_cb()
    return dll.qg_canvas_on_draw(int(_qg_num(args[0], 0)),
                                 _c.cast(cb, _c.c_void_p),
                                 _c.c_void_p(cookie)) == 1


def vg_create_image_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    win = int(_qg_num(args[0], 0))
    path = _qg_bytes(args[1])
    x = int(_qg_num(args[2], 20)) if len(args) > 2 else 20
    y = int(_qg_num(args[3], 20)) if len(args) > 3 else 20
    w = int(_qg_num(args[4], 120)) if len(args) > 4 else 120
    hh = int(_qg_num(args[5], 120)) if len(args) > 5 else 120
    r = dll.qg_create_image(win, path, x, y, w, hh)
    return r if r else None


def vg_image_load_handler(*args):
    dll = _qg_dll()
    if not dll or not args:
        return None
    r = dll.qg_image_load(_qg_bytes(args[0]))
    return r if r else None


def vg_image_free_handler(*args):
    dll = _qg_dll()
    if not dll or not args:
        return False
    dll.qg_image_free(int(_qg_num(args[0], 0)))
    return True


def vg_image_size_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    w = _c.c_int(0)
    h = _c.c_int(0)
    if dll.qg_image_size(int(_qg_num(args[0], 0)), _c.byref(w), _c.byref(h)) != 1:
        return None
    return [w.value, h.value]


# ---- 绘制原语（vg_draw_*：Canvas 绘制回调期间可用） ----

def _vg_color_at(args, idx):
    c = _qg_color(args, idx)
    return c if c else [0.0, 0.0, 0.0, 1.0]


def vg_draw_fill_rect_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_fill_rect(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                          float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                          _c.c_float(c[0]), _c.c_float(c[1]),
                          _c.c_float(c[2]), _c.c_float(c[3]))
    return True


def vg_draw_stroke_rect_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_stroke_rect(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                            float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                            _c.c_float(c[0]), _c.c_float(c[1]),
                            _c.c_float(c[2]), _c.c_float(c[3]),
                            _c.c_float(float(_qg_num(args[5], 1.0))))
    return True


def vg_draw_fill_round_rect_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    c = _vg_color_at(args, 5)
    dll.qg_draw_fill_round_rect(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                                float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                                _c.c_float(float(_qg_num(args[4], 4.0))),
                                _c.c_float(c[0]), _c.c_float(c[1]),
                                _c.c_float(c[2]), _c.c_float(c[3]))
    return True


def vg_draw_stroke_round_rect_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    c = _vg_color_at(args, 5)
    dll.qg_draw_stroke_round_rect(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                                  float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                                  _c.c_float(float(_qg_num(args[4], 4.0))),
                                  _c.c_float(c[0]), _c.c_float(c[1]),
                                  _c.c_float(c[2]), _c.c_float(c[3]),
                                  _c.c_float(float(_qg_num(args[6], 1.0))))
    return True


def vg_draw_fill_ellipse_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_fill_ellipse(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                             float(_qg_num(args[2], 10.0)), float(_qg_num(args[3], 10.0)),
                             _c.c_float(c[0]), _c.c_float(c[1]),
                             _c.c_float(c[2]), _c.c_float(c[3]))
    return True


def vg_draw_stroke_ellipse_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_stroke_ellipse(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                               float(_qg_num(args[2], 10.0)), float(_qg_num(args[3], 10.0)),
                               _c.c_float(c[0]), _c.c_float(c[1]),
                               _c.c_float(c[2]), _c.c_float(c[3]),
                               _c.c_float(float(_qg_num(args[5], 1.0))))
    return True


def vg_draw_line_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_line(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                     float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                     _c.c_float(c[0]), _c.c_float(c[1]),
                     _c.c_float(c[2]), _c.c_float(c[3]),
                     _c.c_float(float(_qg_num(args[5], 1.0))))
    return True


def vg_draw_text_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    c = _vg_color_at(args, 4)
    dll.qg_draw_text(_qg_bytes(args[0]),
                     float(_qg_num(args[1], 0)), float(_qg_num(args[2], 0)),
                     _c.c_float(float(_qg_num(args[3], 14.0))),
                     _c.c_float(c[0]), _c.c_float(c[1]),
                     _c.c_float(c[2]), _c.c_float(c[3]))
    return True


def vg_draw_measure_text_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    w = _c.c_float(0)
    h = _c.c_float(0)
    dll.qg_draw_measure_text(_qg_bytes(args[0]),
                             _c.c_float(float(_qg_num(args[1], 14.0))),
                             _c.byref(w), _c.byref(h))
    return [w.value, h.value]


def vg_draw_gradient_rect_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    c0 = _vg_color_at(args, 4)
    c1 = _vg_color_at(args, 5)
    dll.qg_draw_gradient_rect(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                              float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)),
                              _c.c_float(c0[0]), _c.c_float(c0[1]),
                              _c.c_float(c0[2]), _c.c_float(c0[3]),
                              _c.c_float(c1[0]), _c.c_float(c1[1]),
                              _c.c_float(c1[2]), _c.c_float(c1[3]),
                              _c.c_int(int(_qg_num(args[6], 1))))
    return True


def vg_draw_push_clip_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    dll.qg_draw_push_clip(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                          float(_qg_num(args[2], 0)), float(_qg_num(args[3], 0)))
    return True


def vg_draw_pop_clip_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_draw_pop_clip()
    return True


def vg_draw_push_translate_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    dll.qg_draw_push_translate(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)))
    return True


def vg_draw_push_rotate_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    dll.qg_draw_push_rotate(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                            float(_qg_num(args[2], 0)))
    return True


def vg_draw_push_scale_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    dll.qg_draw_push_scale(float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)),
                           float(_qg_num(args[2], 1.0)), float(_qg_num(args[3], 1.0)))
    return True


def vg_draw_pop_transform_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_draw_pop_transform()
    return True


def vg_draw_image_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    dll.qg_draw_image(int(_qg_num(args[0], 0)),
                      float(_qg_num(args[1], 0)), float(_qg_num(args[2], 0)),
                      float(_qg_num(args[3], 0)), float(_qg_num(args[4], 0)),
                      _c.c_float(float(_qg_num(args[5], 1.0))))
    return True


def vg_draw_push_blur_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    dll.qg_draw_push_blur(float(_qg_num(args[0], 5.0)),
                          float(_qg_num(args[1], 0)), float(_qg_num(args[2], 0)),
                          float(_qg_num(args[3], 0)), float(_qg_num(args[4], 0)))
    return True


def vg_draw_pop_blur_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    dll.qg_draw_pop_blur()
    return True


# ---- v5 · Fluent 级绘制原语（路径/画刷/富文本/效果层/离屏面/图片精控） ----

def _vg_f(v, d=0.0):
    return _qg_cfloat(_qg_num(v, d))


def _qg_cfloat(x):
    import ctypes as _c
    return _c.c_float(float(x))


def _vg_stops_pairs(arr):
    """渐变停靠点 [[pos, [r,g,b,a]], ...] → (pos 列表, 颜色列表)；非法返回 None"""
    if not isinstance(arr, (list, tuple)) or len(arr) < 2:
        return None
    pos, colors = [], []
    for ent in arr:
        if not isinstance(ent, (list, tuple)) or len(ent) < 2:
            return None
        pos.append(float(_qg_num(ent[0], 0)))
        c = ent[1]
        if not isinstance(c, (list, tuple)) or len(c) < 3:
            return None
        a = float(_qg_num(c[3], 1)) if len(c) > 3 else 1.0
        colors.append((float(_qg_num(c[0], 0)), float(_qg_num(c[1], 0)),
                       float(_qg_num(c[2], 0)), a))
    return pos, colors


def _vg_brush_from_stops(dll, radial, params, stops):
    sp = _vg_stops_pairs(stops)
    if not sp:
        return None
    if radial:
        if dll.qg_brush_radial_begin(*[_qg_cfloat(v) for v in params]) != 1:
            return None
    else:
        if dll.qg_brush_linear_begin(*[_qg_cfloat(v) for v in params]) != 1:
            return None
    for p, c in zip(sp[0], sp[1]):
        dll.qg_brush_add_stop(_qg_cfloat(p), _qg_cfloat(c[0]), _qg_cfloat(c[1]),
                              _qg_cfloat(c[2]), _qg_cfloat(c[3]))
    bid = dll.qg_brush_end()
    return bid if bid > 0 else None


def vg_path_begin_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_path_reset() == 1


def vg_path_move_to_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_path_move_to(_vg_f(args[0]), _vg_f(args[1])) == 1


def vg_path_line_to_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_path_line_to(_vg_f(args[0]), _vg_f(args[1])) == 1


def vg_path_quad_to_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_path_quad_to(*[_vg_f(a) for a in args[:4]]) == 1


def vg_path_curve_to_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_path_bezier_to(*[_vg_f(a) for a in args[:6]]) == 1


def vg_path_arc_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_path_arc(*[_vg_f(a) for a in args[:6]]) == 1


def vg_path_svg_arc_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    return dll.qg_path_svg_arc(_vg_f(args[0]), _vg_f(args[1]), _vg_f(args[2]),
                               int(_qg_num(args[3], 0)), int(_qg_num(args[4], 0)),
                               _vg_f(args[5]), _vg_f(args[6])) == 1


def vg_path_rect_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_path_rect(*[_vg_f(a) for a in args[:4]]) == 1


def vg_path_round_rect_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    rr = _vg_f(args[4], 4.0)
    return dll.qg_path_round_rect(_vg_f(args[0]), _vg_f(args[1]), _vg_f(args[2]),
                                  _vg_f(args[3]), rr, rr) == 1


def vg_path_ellipse_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_path_ellipse(*[_vg_f(a) for a in args[:4]]) == 1


def vg_path_close_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_path_close() == 1


def vg_path_fill_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    c = _vg_color_at(args, 0)
    return dll.qg_path_fill(*[_qg_cfloat(v) for v in c]) == 1


def vg_path_fill_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_path_fill_brush(int(_qg_num(args[0], 0))) == 1


def vg_path_stroke_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c = _vg_color_at(args, 0)
    return dll.qg_path_stroke(*[_qg_cfloat(v) for v in c] + [_vg_f(args[1], 1.0)]) == 1


def vg_path_stroke_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_path_stroke_brush(int(_qg_num(args[0], 0)), _vg_f(args[1], 1.0)) == 1


def vg_path_bounds_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll:
        return None
    x = _c.c_float(); y = _c.c_float(); w = _c.c_float(); h = _c.c_float()
    if dll.qg_path_bounds(_c.byref(x), _c.byref(y), _c.byref(w), _c.byref(h)) != 1:
        return None
    return [x.value, y.value, w.value, h.value]


def vg_path_hit_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_path_hit(_vg_f(args[0]), _vg_f(args[1])) == 1


def vg_path_stroke_hit_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    return dll.qg_path_stroke_hit(_vg_f(args[0]), _vg_f(args[1]), _vg_f(args[2], 1.0)) == 1


def vg_path_line_cap_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_path_line_cap(int(_qg_num(args[0], 0))) == 1


def vg_path_line_join_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_path_line_join(int(_qg_num(args[0], 0))) == 1


def vg_path_miter_limit_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_path_miter_limit(_vg_f(args[0], 10.0)) == 1


def vg_path_dash_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    arr = args[0]
    if not isinstance(arr, (list, tuple)):
        return False
    vals = [float(_qg_num(v, 0)) for v in arr]
    phase = _vg_f(args[1], 0.0) if len(args) > 1 else _qg_cfloat(0.0)
    n = len(vals)
    carr = (_c.c_float * max(n, 1))(*vals) if n > 0 else (_c.c_float * 1)(0.0)
    return dll.qg_path_dash(carr if n > 0 else None, n, phase) == 1


def vg_brush_solid_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return None
    c = _vg_color_at(args, 0)
    bid = dll.qg_brush_solid(*[_qg_cfloat(v) for v in c])
    return bid if bid > 0 else None


def vg_brush_linear_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return None
    return _vg_brush_from_stops(dll, False, args[:4], args[4])


def vg_brush_radial_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return None
    cx, cy, r = float(_qg_num(args[0], 0)), float(_qg_num(args[1], 0)), float(_qg_num(args[2], 1))
    return _vg_brush_from_stops(dll, True, [cx, cy, cx, cy, r, r], args[3])


def vg_brush_radial_focus_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return None
    return _vg_brush_from_stops(dll, True, [float(_qg_num(a, 0)) for a in args[:6]], args[6])


def vg_brush_bitmap_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return None
    bid = dll.qg_brush_bitmap(int(_qg_num(args[0], 0)))
    return bid if bid > 0 else None


def vg_brush_extend_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    return dll.qg_brush_extend(int(_qg_num(args[0], 0)),
                               int(_qg_num(args[1], 0)),
                               int(_qg_num(args[2], 0))) == 1


def vg_brush_transform_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    return dll.qg_brush_transform(int(_qg_num(args[0], 0)),
                                  *[_vg_f(a) for a in args[1:7]]) == 1


def vg_brush_free_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    dll.qg_brush_free(int(_qg_num(args[0], 0)))
    return True


def vg_draw_fill_rect_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    return dll.qg_draw_fill_rect_brush(int(_qg_num(args[0], 0)),
                                       *[_vg_f(a) for a in args[1:5]]) == 1


def vg_draw_stroke_rect_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_draw_stroke_rect_brush(int(_qg_num(args[0], 0)),
                                         *[_vg_f(a) for a in args[1:6]]) == 1


def vg_draw_fill_round_rect_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_draw_fill_round_rect_brush(int(_qg_num(args[0], 0)),
                                             *[_vg_f(a) for a in args[1:6]]) == 1


def vg_draw_stroke_round_rect_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    return dll.qg_draw_stroke_round_rect_brush(int(_qg_num(args[0], 0)),
                                               *[_vg_f(a) for a in args[1:7]]) == 1


def vg_draw_fill_ellipse_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    return dll.qg_draw_fill_ellipse_brush(int(_qg_num(args[0], 0)),
                                          *[_vg_f(a) for a in args[1:5]]) == 1


def vg_draw_stroke_ellipse_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_draw_stroke_ellipse_brush(int(_qg_num(args[0], 0)),
                                            *[_vg_f(a) for a in args[1:6]]) == 1


def _vg_rich_flags(args, base):
    """bold/italic/underline/strike 四标志（默认 0）"""
    return [int(_qg_num(args[base + i], 0)) if len(args) > base + i else 0
            for i in range(4)]


def vg_rich_begin_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_rich_begin() == 1


def vg_rich_add_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _vg_color_at(args, 2)
    flags = _vg_rich_flags(args, 3)
    weight = 700 if flags[0] else 400
    return dll.qg_rich_add(_qg_bytes(args[0]), _vg_f(args[1], 16.0),
                           *[_qg_cfloat(v) for v in c],
                           weight, flags[1], flags[2], flags[3]) == 1


def vg_rich_add_weight_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    c = _vg_color_at(args, 2)
    flags = _vg_rich_flags(args, 4)
    weight = int(_qg_num(args[3], 400))
    return dll.qg_rich_add(_qg_bytes(args[0]), _vg_f(args[1], 16.0),
                           *[_qg_cfloat(v) for v in c],
                           weight, flags[1], flags[2], flags[3]) == 1


def vg_rich_add_brush_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    flags = _vg_rich_flags(args, 4)
    weight = int(_qg_num(args[3], 400)) if len(args) > 3 else 400
    return dll.qg_rich_add_brush(_qg_bytes(args[0]), _vg_f(args[1], 16.0),
                                 int(_qg_num(args[2], 0)),
                                 weight, flags[1], flags[2], flags[3]) == 1


def vg_rich_line_height_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    baseline = _vg_f(args[1], 0.0) if len(args) > 1 else _qg_cfloat(0.0)
    return dll.qg_rich_line_height(_vg_f(args[0], 0.0), baseline) == 1


def vg_rich_spacing_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_rich_spacing(*[_vg_f(a) for a in args[:4]]) == 1


def vg_rich_draw_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 7:
        return False
    return dll.qg_rich_draw(*[_vg_f(a) for a in args[:4]] +
                            [int(_qg_num(args[4], 0)), int(_qg_num(args[5], 0)),
                             int(_qg_num(args[6], 0))]) == 1


def vg_rich_measure_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    w = _c.c_float(); h = _c.c_float()
    if dll.qg_rich_measure(int(_qg_num(args[0], 0)), _vg_f(args[1], 0.0),
                           _c.byref(w), _c.byref(h)) != 1:
        return None
    return [w.value, h.value]


def vg_draw_push_blend_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    b = [float(_qg_num(args[i], 0)) for i in range(1, 5)] if len(args) >= 5 else [0.0] * 4
    return dll.qg_draw_push_blend(int(_qg_num(args[0], 0)),
                                  *[_qg_cfloat(v) for v in b]) == 1


def vg_draw_pop_blend_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_draw_pop_blend() == 1


def vg_draw_push_shadow_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    c = _vg_color_at(args, 0)
    b = [float(_qg_num(args[i], 0)) for i in range(4, 8)] if len(args) >= 8 else [0.0] * 4
    return dll.qg_draw_push_shadow(*[_qg_cfloat(v) for v in c] +
                                   [_vg_f(args[1], 8.0), _vg_f(args[2], 0.0),
                                    _vg_f(args[3], 0.0)] +
                                   [_qg_cfloat(v) for v in b]) == 1


def vg_draw_pop_shadow_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_draw_pop_shadow() == 1


def vg_draw_push_mask_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    opacity = _vg_f(args[5], 1.0) if len(args) > 5 else _qg_cfloat(1.0)
    return dll.qg_draw_push_mask(int(_qg_num(args[0], 0)),
                                 *[_vg_f(a) for a in args[1:5]], opacity) == 1


def vg_draw_pop_mask_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_draw_pop_mask() == 1


def vg_draw_push_clip_path_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_draw_push_clip_path() == 1


def vg_surface_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    sid = dll.qg_surface_create(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)))
    return sid if sid > 0 else None


def vg_surface_begin_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_surface_begin(int(_qg_num(args[0], 0))) == 1


def vg_surface_end_handler(*args):
    dll = _qg_dll()
    if not dll:
        return False
    return dll.qg_surface_end() == 1


def vg_surface_draw_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    alpha = _vg_f(args[5], 1.0) if len(args) > 5 else _qg_cfloat(1.0)
    interp = int(_qg_num(args[6], 1)) if len(args) > 6 else 1
    return dll.qg_surface_draw(int(_qg_num(args[0], 0)),
                               *[_vg_f(a) for a in args[1:5]], alpha, interp) == 1


def vg_surface_pixel_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return None
    r = _c.c_float(); g = _c.c_float(); b = _c.c_float(); a = _c.c_float()
    if dll.qg_surface_pixel(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                            int(_qg_num(args[2], 0)),
                            _c.byref(r), _c.byref(g), _c.byref(b),
                            _c.byref(a)) != 1:
        return None
    return [r.value, g.value, b.value, a.value]


def vg_surface_free_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    dll.qg_surface_free(int(_qg_num(args[0], 0)))
    return True


def vg_draw_image_ex_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    alpha = _vg_f(args[5], 1.0)
    interp = int(_qg_num(args[6], 1)) if len(args) > 6 else 1
    src = [float(_qg_num(args[i], 0)) for i in range(7, 11)] if len(args) > 7 else [0, 0, 0, 0]
    return dll.qg_draw_image_ex(int(_qg_num(args[0], 0)),
                                *[_vg_f(a) for a in args[1:5]], alpha, interp,
                                *[_qg_cfloat(v) for v in src]) == 1


def vg_draw_image_round_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    alpha = _vg_f(args[5], 1.0)
    interp = int(_qg_num(args[6], 1)) if len(args) > 6 else 1
    radius = _vg_f(args[7], 0.0) if len(args) > 7 else _qg_cfloat(0.0)
    return dll.qg_draw_image_round(int(_qg_num(args[0], 0)),
                                   *[_vg_f(a) for a in args[1:5]], alpha, interp,
                                   radius) == 1


def vg_backend_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll:
        return None
    composed = _c.c_int(); dpi = _c.c_int()
    if dll.qg_backend_info(_c.byref(composed), _c.byref(dpi)) != 1:
        return None
    return [composed.value, dpi.value]


# ---- v6 · 声明式视觉模板（路径句柄/文字样式/模板 part-state-transition） ----

def vg_path_store_handler(*args):
    """vg_path_store() 把当前构建路径固化为几何句柄（部件形状/命中/裁剪共用）"""
    dll = _qg_dll()
    if not dll:
        return None
    gid = dll.qg_path_store()
    return gid if gid > 0 else None


def vg_path_free_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    dll.qg_path_free(int(_qg_num(args[0], 0)))
    return True


def vg_geo_hit_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    return dll.qg_geo_hit(int(_qg_num(args[0], 0)),
                          _vg_f(args[1]), _vg_f(args[2])) == 1


def vg_textstyle_handler(*args):
    """vg_textstyle(size, weight, italic, halign, wrap, family) → ts id"""
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return None
    fam = _qg_bytes(args[5]) if len(args) > 5 else b''
    ts = dll.qg_textstyle_create(
        _vg_f(args[0], 14.0),
        int(_qg_num(args[1], 400)) if len(args) > 1 else 400,
        int(_qg_num(args[2], 0)) if len(args) > 2 else 0,
        int(_qg_num(args[3], 1)) if len(args) > 3 else 1,
        int(_qg_num(args[4], 0)) if len(args) > 4 else 0,
        fam)
    return ts if ts > 0 else None


def vg_textstyle_free_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    dll.qg_textstyle_free(int(_qg_num(args[0], 0)))
    return True


def vg_tpl_create_handler(*args):
    dll = _qg_dll()
    if not dll:
        return None
    tid = dll.qg_tpl_create()
    return tid if tid > 0 else None


def _vg_tpl_arr(v, n, dflt):
    """数组参数归一：[v0..vn-1]（None/缺项补默认）"""
    out = list(dflt)
    if isinstance(v, (list, tuple)):
        for i in range(min(n, len(v))):
            out[i] = float(_qg_num(v[i], out[i]))
    return out


def vg_tpl_part_handler(*args):
    """vg_tpl_part(tpl, name, shape, a, b, c, path_id, fill_kind, fill色,
    fill_brush, stroke[色,宽], inset, textslot, anchor)
    shape：0 矩形 1 圆角(a=radius) 2 椭圆 3 路径(a>0 定尺寸框) 4 定尺寸圆角(a=w,b=h,c=r)
    5 定尺寸椭圆(a=w,b=h) 6 水平条(a=高,b=radius)；anchor 0 左 1 中 2 右"""
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    tpl = int(_qg_num(args[0], 0))
    name = _qg_bytes(args[1])
    shape = int(_qg_num(args[2], 0))

    def num(i, d):
        return _qg_num(args[i], d) if len(args) > i else d

    a = _vg_f(num(3, 0.0))
    b = _vg_f(num(4, 0.0))
    c = _vg_f(num(5, 0.0))
    path_id = int(num(6, 0))
    fill_kind = int(num(7, 0))
    fill = _vg_tpl_arr(args[8] if len(args) > 8 else None, 4, [0.0, 0.0, 0.0, 1.0])
    fill_brush = int(num(9, 0))
    stroke = args[10] if len(args) > 10 else None
    stroke_set = 1 if isinstance(stroke, (list, tuple)) and len(stroke) >= 1 else 0
    sc = _vg_tpl_arr(stroke[0] if stroke_set else None, 4, [0.0, 0.0, 0.0, 1.0])
    sw = _vg_f(stroke[1], 1.0) if (stroke_set and len(stroke) > 1) else _qg_cfloat(1.0)
    inset = _vg_tpl_arr(args[11] if len(args) > 11 else None, 4, [0.0, 0.0, 0.0, 0.0])
    textslot = int(num(12, 0))
    anchor = int(num(13, 0))
    return dll.qg_tpl_part(tpl, name, shape, a, b, c, path_id, anchor,
                           fill_kind, *[_qg_cfloat(v) for v in fill], fill_brush,
                           stroke_set, *[_qg_cfloat(v) for v in sc], sw,
                           *[_qg_cfloat(v) for v in inset], textslot) == 1


def vg_tpl_part_text_handler(*args):
    """vg_tpl_part_text(tpl, part, ts, halign, valign, margin, color) 文字槽精调"""
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    ha = int(_qg_num(args[3], -1)) if len(args) > 3 else -1
    va = int(_qg_num(args[4], -1)) if len(args) > 4 else -1
    margin = _vg_tpl_arr(args[5] if len(args) > 5 else None, 4, [0.0, 0.0, 0.0, 0.0])
    color = args[6] if len(args) > 6 else None
    cset = 1 if isinstance(color, (list, tuple)) else 0
    cc = _vg_tpl_arr(color if cset else None, 4, [0.0, 0.0, 0.0, 1.0])
    return dll.qg_tpl_part_text(int(_qg_num(args[0], 0)), _qg_bytes(args[1]),
                                int(_qg_num(args[2], 0)), ha, va,
                                *[_qg_cfloat(v) for v in margin],
                                cset, *[_qg_cfloat(v) for v in cc]) == 1


def vg_tpl_part_dyn_handler(*args):
    """vg_tpl_part_dyn(tpl, part, mode, src, range, c2) 值驱动几何
    mode：0 无 1 宽按值 2 位移 3 缩放(c2[0]=最小) 4 透明 5 高按值 6 色lerp(c2=第二色/渐变尾色)"""
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    c2 = _vg_tpl_arr(args[5] if len(args) > 5 else None, 4, [0.0, 0.0, 0.0, 1.0])
    rng = _vg_f(args[4], 0.0) if len(args) > 4 else _qg_cfloat(0.0)
    return dll.qg_tpl_part_dyn(int(_qg_num(args[0], 0)), _qg_bytes(args[1]),
                               int(_qg_num(args[2], 0)), int(_qg_num(args[3], 0)),
                               rng, *[_qg_cfloat(v) for v in c2]) == 1


def vg_tpl_state_handler(*args):
    """vg_tpl_state(tpl, mask, part, prop, [v0,v1,v2,v3])（或尾参展开）
    mask：1 悬停 2 按下 4 禁用 8 焦点 16 勾选 32 展开；prop 0 填充色 1 填充刷
    2 描边色 3 描边宽 4 平移 5 缩放 6 旋转 7 透明 8 圆角"""
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    v = _vg_tpl_arr(args[4] if (len(args) > 4 and isinstance(args[4], (list, tuple)))
                    else None, 4, [0.0, 0.0, 0.0, 0.0])
    if len(args) > 4 and not isinstance(args[4], (list, tuple)):
        for i in range(4):
            if len(args) > 4 + i:
                v[i] = float(_qg_num(args[4 + i], 0.0))
    return dll.qg_tpl_state(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                            _qg_bytes(args[2]) if len(args) > 2 else b'',
                            int(_qg_num(args[3], 0)),
                            *[_qg_cfloat(x) for x in v]) == 1


def vg_tpl_transition_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_tpl_transition(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                                 _vg_f(args[2], 200.0), int(_qg_num(args[3], 0))) == 1


def vg_tpl_free_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    dll.qg_tpl_free(int(_qg_num(args[0], 0)))
    return True


def vg_apply_tpl_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_apply_template(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0))) == 1


def vg_clear_tpl_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return False
    return dll.qg_clear_template(int(_qg_num(args[0], 0))) == 1


def vg_tpl_default_handler(*args):
    """vg_tpl_default(type) → tpl id；type：0 按钮 1 勾选 2 开关 3 滑块 4 进度 5 组合 6 选项卡"""
    dll = _qg_dll()
    if not dll or len(args) < 1:
        return None
    tid = dll.qg_tpl_default(int(_qg_num(args[0], 0)))
    return tid if tid > 0 else None


# ---- v6 配套 · 表格 / 颜色选择器 / 日期选择器 ----

def vg_table_add_column_handler(*args):
    """vg_table_add_column(id, title, width)；width<=0 均分剩余"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    w = _qg_cfloat(_qg_num(args[2], 0.0)) if len(args) > 2 else _qg_cfloat(0.0)
    return dll.qg_table_add_column(int(_qg_num(args[0], 0)),
                                   _qg_bytes(args[1]), w) == 1


def vg_table_add_row_handler(*args):
    """vg_table_add_row(id, "a;b;c")——';' 分隔单元格"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_table_add_row(int(_qg_num(args[0], 0)),
                                _qg_bytes(args[1])) == 1


def vg_table_set_cell_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    return dll.qg_table_set_cell(int(_qg_num(args[0], 0)),
                                 int(_qg_num(args[1], 0)),
                                 int(_qg_num(args[2], 0)),
                                 _qg_bytes(args[3])) == 1


def vg_table_get_selected_handler(*args):
    """vg_table_get_selected(id) → [row, col]（无选中 [-1, -1]）"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    row = _c.c_int(-1); col = _c.c_int(-1)
    if dll.qg_table_get_selected(int(_qg_num(args[0], 0)),
                                 _c.byref(row), _c.byref(col)) != 1:
        return None
    return [row.value, col.value]


def vg_table_set_selected_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    return dll.qg_table_set_selected(int(_qg_num(args[0], 0)),
                                     int(_qg_num(args[1], 0)),
                                     int(_qg_num(args[2], 0))) == 1


def vg_colorpicker_set_rgb_handler(*args):
    """vg_colorpicker_set_rgb(id, [r,g,b]) 或 (id, r, g, b)；分量 0~1"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    if isinstance(args[1], (list, tuple)) and len(args[1]) >= 3:
        r = _qg_num(args[1][0], 0); g = _qg_num(args[1][1], 0); b = _qg_num(args[1][2], 0)
    elif len(args) >= 4:
        r = _qg_num(args[1], 0); g = _qg_num(args[2], 0); b = _qg_num(args[3], 0)
    else:
        return False
    return dll.qg_colorpicker_set_rgb(int(_qg_num(args[0], 0)),
                                      _qg_cfloat(r), _qg_cfloat(g),
                                      _qg_cfloat(b)) == 1


def vg_colorpicker_get_rgb_handler(*args):
    """vg_colorpicker_get_rgb(id) → [r,g,b]（0~1）"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    r = _c.c_float(); g = _c.c_float(); b = _c.c_float()
    if dll.qg_colorpicker_get_rgb(int(_qg_num(args[0], 0)),
                                  _c.byref(r), _c.byref(g), _c.byref(b)) != 1:
        return None
    return [r.value, g.value, b.value]


def vg_datepicker_set_date_handler(*args):
    """vg_datepicker_set_date(id, y, m, d) 或 (id, [y,m,d])"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    if isinstance(args[1], (list, tuple)) and len(args[1]) >= 3:
        y = _qg_num(args[1][0], 0); m = _qg_num(args[1][1], 0); d = _qg_num(args[1][2], 0)
    elif len(args) >= 4:
        y = _qg_num(args[1], 0); m = _qg_num(args[2], 0); d = _qg_num(args[3], 0)
    else:
        return False
    return dll.qg_datepicker_set_date(int(_qg_num(args[0], 0)),
                                      int(y), int(m), int(d)) == 1


def vg_datepicker_get_date_handler(*args):
    """vg_datepicker_get_date(id) → [y, m, d]"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    y = _c.c_int(); m = _c.c_int(); d = _c.c_int()
    if dll.qg_datepicker_get_date(int(_qg_num(args[0], 0)),
                                  _c.byref(y), _c.byref(m), _c.byref(d)) != 1:
        return None
    return [y.value, m.value, d.value]


# ---- v3 · 布局系统 ----

def vg_layout_begin_handler(*args):
    """声明窗口布局：vg_layout_begin(win, "vbox"|"hbox"|"grid"|"absolute")。
    absolute = 关闭布局恢复绝对坐标；随后 vg_layout_add 登记条目。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_layout_begin(int(_qg_num(args[0], 0)), _qg_bytes(args[1])) == 1


def vg_layout_add_handler(*args):
    """按布局顺序登记控件：vg_layout_add(win, id)"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_layout_add(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0))) == 1


def vg_layout_spacing_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    import ctypes as _c
    return dll.qg_layout_spacing(int(_qg_num(args[0], 0)),
                                 _c.c_float(float(_qg_num(args[1], 8.0)))) == 1


def vg_layout_padding_handler(*args):
    """内容区内边距：vg_layout_padding(win, all) / (win, lr, tb) / (win, l, t, r, b)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return False
    a = float(_qg_num(args[1], 0.0)) if len(args) > 1 else 0.0
    b = float(_qg_num(args[2], a)) if len(args) > 2 else a
    c = float(_qg_num(args[3], a)) if len(args) > 3 else a
    d = float(_qg_num(args[4], b)) if len(args) > 4 else b
    return dll.qg_layout_padding(int(_qg_num(args[0], 0)),
                                 _c.c_float(a), _c.c_float(b),
                                 _c.c_float(c), _c.c_float(d)) == 1


def vg_layout_cols_handler(*args):
    """网格列数（grid 模式）：vg_layout_cols(win, n)"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_layout_cols(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 2))) == 1


def vg_layout_props_handler(*args):
    """控件布局属性：vg_layout_props(id, margin, [fill], [align], [weight])
    margin = [l,t,r,b] 或标量全边；fill 0 自然 1 交叉轴拉伸 2 全拉伸；
    align 0 起 1 中 2 末；weight 主轴伸展权重（vbox 高度 / hbox 宽度均分）。"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    m = args[1]
    if isinstance(m, (list, tuple)):
        vals = [_qg_num(x, 0.0) for x in m[:4]]
        while len(vals) < 4:
            vals.append(0.0)
        ml, mt, mr, mb = vals
    else:
        ml = mt = mr = mb = float(_qg_num(m, 0.0))
    fill = int(_qg_num(args[2], 0)) if len(args) > 2 else 0
    align = int(_qg_num(args[3], 0)) if len(args) > 3 else 0
    weight = float(_qg_num(args[4], 0.0)) if len(args) > 4 else 0.0
    return dll.qg_layout_props(
        int(_qg_num(args[0], 0)),
        _c.c_float(ml), _c.c_float(mt), _c.c_float(mr), _c.c_float(mb),
        fill, align, _c.c_float(weight)) == 1


def vg_layout_end_handler(*args):
    """收口并立即重排：vg_layout_end(win)；此后窗口 resize 自动重排。"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_layout_end(int(_qg_num(args[0], 0))) == 1


def vg_get_pos_handler(*args):
    """读回控件左上角：vg_get_pos(id) → [x, y] | null"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    x = _c.c_int(0)
    y = _c.c_int(0)
    if dll.qg_get_pos(int(_qg_num(args[0], 0)), _c.byref(x), _c.byref(y)) != 1:
        return None
    return [x.value, y.value]


def vg_get_size_handler(*args):
    """读回控件尺寸：vg_get_size(id) → [w, h] | null"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    w = _c.c_int(0)
    h = _c.c_int(0)
    if dll.qg_get_size(int(_qg_num(args[0], 0)), _c.byref(w), _c.byref(h)) != 1:
        return None
    return [w.value, h.value]


def vg_window_client_size_handler(*args):
    """读回窗口客户区：vg_window_client_size(win) → [w, h] | null"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or not args:
        return None
    w = _c.c_int(0)
    h = _c.c_int(0)
    if dll.qg_window_client_size(int(_qg_num(args[0], 0)),
                                 _c.byref(w), _c.byref(h)) != 1:
        return None
    return [w.value, h.value]


# ---- v3 · 滚动容器与虚拟化列表 ----

_QG_LISTDATA_CB_REF = ['']


def _qg_listdata_trampoline():
    """虚拟列表行数据回调单例：fn(row) → 文本（经 evaluator 调 QOT 闭包）。"""
    if _QG_LISTDATA_CB_REF[0]:
        return _QG_LISTDATA_CB_REF[0]
    import ctypes as _c
    ty = _c.CFUNCTYPE(_c.c_char_p, _c.c_int, _c.c_void_p)

    def _cb(row, ud):
        clo = _QG_BINDS.get(ud or 0)
        if clo is None:
            return b''
        ev = _current_evaluator()
        if ev is None:
            return b''
        try:
            r = ev._call_closure(clo, [row])
            if r is None:
                return b''
            return str(r).encode('utf-8')
        except Exception:
            return b''

    _QG_LISTDATA_CB_REF[0] = ty(_cb)
    return _QG_LISTDATA_CB_REF[0]


def vg_create_scrollpane_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll:
        return None
    win = int(_qg_num(args[0], 0)) if args else 0
    x = int(_qg_num(args[1], 20)) if len(args) > 1 else 20
    y = int(_qg_num(args[2], 20)) if len(args) > 2 else 20
    w = int(_qg_num(args[3], 200)) if len(args) > 3 else 200
    hh = int(_qg_num(args[4], 160)) if len(args) > 4 else 160
    r = dll.qg_create_scrollpane(win, x, y, w, hh)
    return r if r else None


def vg_scroll_content_handler(*args):
    """内容虚高：vg_scroll_content(id, content_h)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_scroll_content(int(_qg_num(args[0], 0)),
                                 _c.c_float(float(_qg_num(args[1], 0)))) == 1


def vg_scroll_attach_handler(*args):
    """把已有控件装进滚动区：vg_scroll_attach(sp, id, x, y, w, h)；
    x/y = 内容坐标（相对内容左上）。attach 后控件由滚动区托管。"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 6:
        return False
    return dll.qg_scroll_attach(
        int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
        int(_qg_num(args[2], 0)), int(_qg_num(args[3], 0)),
        int(_qg_num(args[4], 0)), int(_qg_num(args[5], 0))) == 1


def vg_scroll_offset_handler(*args):
    """当前滚动偏移：vg_scroll_offset(id) → 数值"""
    dll = _qg_dll()
    if not dll or not args:
        return 0.0
    return float(dll.qg_scroll_offset(int(_qg_num(args[0], 0))))


def vg_scroll_to_handler(*args):
    """滚动到指定偏移：vg_scroll_to(id, offset)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_scroll_to(int(_qg_num(args[0], 0)),
                            _c.c_float(float(_qg_num(args[1], 0)))) == 1


def vg_create_vlist_handler(*args):
    import ctypes as _c
    dll = _qg_dll()
    if not dll:
        return None
    win = int(_qg_num(args[0], 0)) if args else 0
    x = int(_qg_num(args[1], 20)) if len(args) > 1 else 20
    y = int(_qg_num(args[2], 20)) if len(args) > 2 else 20
    w = int(_qg_num(args[3], 200)) if len(args) > 3 else 200
    hh = int(_qg_num(args[4], 160)) if len(args) > 4 else 160
    r = dll.qg_create_vlist(win, x, y, w, hh)
    return r if r else None


def vg_list_set_count_handler(*args):
    """虚拟列表数据条数：vg_list_set_count(id, n)——万行级无压力。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_list_set_count(int(_qg_num(args[0], 0)),
                                 int(_qg_num(args[1], 0))) == 1


def vg_list_add_item_handler(*args):
    """数组模式加行：vg_list_add_item(id, text)"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_list_add_item(int(_qg_num(args[0], 0)), _qg_bytes(args[1])) == 1


def vg_list_draw_count_handler(*args):
    """累计已绘制行数（虚拟化自证：只随可视区滚动量增长）"""
    dll = _qg_dll()
    if not dll or not args:
        return 0
    return dll.qg_list_draw_count(int(_qg_num(args[0], 0)))


def vg_list_on_data_handler(*args):
    """行数据回调：vg_list_on_data(id, fn)；fn(row) → 行文本（回调模式，
    大数据零拷贝——绘制时逐行取）。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    clo = args[1]
    if not _is_closure(clo):
        return False
    cookie = _qg_cookie(clo)
    import ctypes as _c
    cb = _qg_listdata_trampoline()
    return dll.qg_list_on_data(int(_qg_num(args[0], 0)),
                               _c.cast(cb, _c.c_void_p),
                               _c.c_void_p(cookie)) == 1


# ---- v3 · 模态对话框 / 菜单 / toast ----

def vg_show_dialog_handler(*args):
    """模态对话框：vg_show_dialog(win, parent) → 结果码（阻塞至关闭，
    父窗冻结；回调内 vg_set_dialog_result 设置返回值）。"""
    dll = _qg_dll()
    if not dll or not args:
        return -1
    parent = int(_qg_num(args[1], 0)) if len(args) > 1 else 0
    return dll.qg_show_dialog(int(_qg_num(args[0], 0)), parent)


def vg_set_dialog_result_handler(*args):
    """回调内设置对话框返回值：vg_set_dialog_result(win, code)"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_dialog_result(int(_qg_num(args[0], 0)),
                                    int(_qg_num(args[1], 0))) == 1


def vg_create_menu_handler(*args):
    """右键菜单：vg_create_menu() → 菜单 id | null"""
    dll = _qg_dll()
    if not dll:
        return None
    r = dll.qg_create_menu()
    return r if r else None


def vg_menu_add_item_handler(*args):
    """加菜单项：vg_menu_add_item(mid, text) → 项 id | null"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    r = dll.qg_menu_add_item(int(_qg_num(args[0], 0)), _qg_bytes(args[1]))
    return r if r else None


def vg_menu_separator_handler(*args):
    """加分隔线：vg_menu_separator(mid)"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_menu_separator(int(_qg_num(args[0], 0))) == 1


def vg_menu_popup_handler(*args):
    """弹出菜单（同步阻塞）：vg_menu_popup(mid, win, x, y) → 选中项 id
    （0 = 取消）；同时发 kind=50 事件（value = 项 id）。"""
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return 0
    return dll.qg_menu_popup(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)),
                             int(_qg_num(args[2], 0)), int(_qg_num(args[3], 0)))


def vg_menu_destroy_handler(*args):
    """销毁菜单：vg_menu_destroy(mid)"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_menu_destroy(int(_qg_num(args[0], 0))) == 1


def vg_toast_handler(*args):
    """轻提示：vg_toast(win, text[, ms=2200])——底部居中卡片自动淡出。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_toast(int(_qg_num(args[0], 0)), _qg_bytes(args[1]),
                        int(_qg_num(args[2], 2200)) if len(args) > 2 else 2200) == 1


# ---- v3 · 文本富能力 ----

def vg_draw_text_ex_handler(*args):
    """富文本：vg_draw_text_ex(text, x, y, w, h, size, color, bold, halign,
    valign[, wrap[, ellipsis]])——halign/valign 0 起 1 中 2 末；
    wrap=自动换行；ellipsis=超宽省略号。Canvas 回调期间可用。"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 10:
        return False
    c = _vg_color_at(args, 6)
    dll.qg_draw_text_ex(
        _qg_bytes(args[0]),
        _c.c_float(float(_qg_num(args[1], 0))), _c.c_float(float(_qg_num(args[2], 0))),
        _c.c_float(float(_qg_num(args[3], 0))), _c.c_float(float(_qg_num(args[4], 0))),
        _c.c_float(float(_qg_num(args[5], 14.0))),
        _c.c_float(c[0]), _c.c_float(c[1]), _c.c_float(c[2]), _c.c_float(c[3]),
        int(_qg_num(args[7], 0)), int(_qg_num(args[8], 0)), int(_qg_num(args[9], 0)),
        int(_qg_num(args[10], 0)) if len(args) > 10 else 0,
        int(_qg_num(args[11], 0)) if len(args) > 11 else 0)
    return True


def vg_draw_measure_text_ex_handler(*args):
    """富测量：vg_draw_measure_text_ex(text, size[, bold[, wrap[, maxw]]])
    → [w, h]；wrap=true 返回换行后的实际宽高。"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return None
    w = _c.c_float(0)
    h = _c.c_float(0)
    dll.qg_draw_measure_text_ex(
        _qg_bytes(args[0]),
        _c.c_float(float(_qg_num(args[1], 14.0))),
        int(_qg_num(args[2], 0)) if len(args) > 2 else 0,
        int(_qg_num(args[3], 0)) if len(args) > 3 else 0,
        _c.c_float(float(_qg_num(args[4], 0))) if len(args) > 4 else _c.c_float(0),
        _c.byref(w), _c.byref(h))
    return [w.value, h.value]


# ---- v3 · 系统集成 ----

def vg_clipboard_set_handler(*args):
    """写剪贴板文本：vg_clipboard_set(text) → bool"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_clipboard_set(_qg_bytes(args[0])) == 1


def vg_clipboard_get_handler(*args):
    """读剪贴板文本：vg_clipboard_get() → str（无文本返回空串）"""
    dll = _qg_dll()
    if not dll:
        return ''
    import ctypes as _c
    buf = _c.create_string_buffer(8192)
    n = dll.qg_clipboard_get(buf, 8192)
    if n <= 0:
        return ''
    return buf.raw[:n].decode('utf-8', errors='replace')


def vg_window_accept_files_handler(*args):
    """拖放开关联动：vg_window_accept_files(win, on) → bool"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_window_accept_files(int(_qg_num(args[0], 0)),
                                      1 if args[1] else 0) == 1


def vg_take_drop_file_handler(*args):
    """取拖放文件路径：vg_take_drop_file(i) → str（kind=51 回调里立刻取）"""
    dll = _qg_dll()
    if not dll or not args:
        return ''
    import ctypes as _c
    buf = _c.create_string_buffer(4096)
    n = dll.qg_take_drop_file(int(_qg_num(args[0], 0)), buf, 4096)
    if n <= 0:
        return ''
    return buf.raw[:n].decode('utf-8', errors='replace')


def vg_taskbar_progress_handler(*args):
    """任务栏进度：vg_taskbar_progress(win, done, total) → bool"""
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    return dll.qg_taskbar_progress(int(_qg_num(args[0], 0)),
                                   int(_qg_num(args[1], 0)),
                                   int(_qg_num(args[2], 0))) == 1


def vg_taskbar_state_handler(*args):
    """任务栏状态：vg_taskbar_state(win, state)；0 无 1 不确定 2 正常 3 错误 4 暂停"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_taskbar_state(int(_qg_num(args[0], 0)),
                                int(_qg_num(args[1], 0))) == 1


# ---- v4 · 样式原语 ----

def _qg_color4(args, i):
    import ctypes as _c
    dll = _qg_dll()
    if dll is None or i >= len(args):
        return None
    col = args[i]
    if isinstance(col, (list, tuple)) and len(col) >= 3:
        r = float(_qg_num(col[0], 0))
        g = float(_qg_num(col[1], 0))
        b = float(_qg_num(col[2], 0))
        a = float(_qg_num(col[3], 1)) if len(col) > 3 else 1.0
        return (r, g, b, a)
    return None


def vg_style_bg_handler(*args):
    """背景色叠加：vg_style_bg(id, [r,g,b,a])；未设置走组件默认"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_style_bg(int(_qg_num(args[0], 0)),
                           _c.c_float(c[0]), _c.c_float(c[1]),
                           _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_style_bg_gradient_handler(*args):
    """渐变背景叠加：vg_style_bg_gradient(id, c1, c2[, vertical=true])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c1 = _qg_color4(args, 1)
    c2 = _qg_color4(args, 2)
    if c1 is None or c2 is None:
        return False
    vert = 1 if (len(args) < 4 or args[3]) else 0
    return dll.qg_style_bg_gradient(
        int(_qg_num(args[0], 0)),
        _c.c_float(c1[0]), _c.c_float(c1[1]), _c.c_float(c1[2]), _c.c_float(c1[3]),
        _c.c_float(c2[0]), _c.c_float(c2[1]), _c.c_float(c2[2]), _c.c_float(c2[3]),
        vert) == 1


def vg_style_border_handler(*args):
    """边框叠加：vg_style_border(id, [r,g,b,a], width)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_style_border(int(_qg_num(args[0], 0)),
                               _c.c_float(c[0]), _c.c_float(c[1]),
                               _c.c_float(c[2]), _c.c_float(c[3]),
                               _c.c_float(float(_qg_num(args[2], 1.0)))) == 1


def vg_style_radius_handler(*args):
    """圆角叠加：vg_style_radius(id, radius)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_style_radius(int(_qg_num(args[0], 0)),
                               _c.c_float(float(_qg_num(args[1], 8.0)))) == 1


def vg_style_padding_handler(*args):
    """内边距叠加：vg_style_padding(id, l, t, r, b)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    return dll.qg_style_padding(int(_qg_num(args[0], 0)),
                                _c.c_float(float(_qg_num(args[1], 0))),
                                _c.c_float(float(_qg_num(args[2], 0))),
                                _c.c_float(float(_qg_num(args[3], 0))),
                                _c.c_float(float(_qg_num(args[4], 0)))) == 1


def vg_style_text_handler(*args):
    """文字样式叠加：vg_style_text(id, [r,g,b,a], size[, bold])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    size = _c.c_float(float(_qg_num(args[2], 14.0)))
    bold = 1 if (len(args) > 3 and args[3]) else 0
    return dll.qg_style_text(int(_qg_num(args[0], 0)),
                             _c.c_float(c[0]), _c.c_float(c[1]),
                             _c.c_float(c[2]), _c.c_float(c[3]), size, bold) == 1


def vg_style_state_bg_handler(*args):
    """状态背景叠加：vg_style_state_bg(id, which, [r,g,b,a])；which 1悬停 2按下 3禁用 4焦点"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _qg_color4(args, 2)
    if c is None:
        return False
    return dll.qg_style_state_bg(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 1)),
                                 _c.c_float(c[0]), _c.c_float(c[1]),
                                 _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_style_state_text_handler(*args):
    """状态文字色叠加：vg_style_state_text(id, which, [r,g,b,a])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _qg_color4(args, 2)
    if c is None:
        return False
    return dll.qg_style_state_text(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 1)),
                                   _c.c_float(c[0]), _c.c_float(c[1]),
                                   _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_style_reset_handler(*args):
    """清空样式叠加：vg_style_reset(id) → 组件回到默认外观"""
    dll = _qg_dll()
    if not dll or not args:
        return False
    return dll.qg_style_reset(int(_qg_num(args[0], 0))) == 1


def vg_style_flags_handler(*args):
    """查状态位：vg_style_flags(id) → 0x1悬停 0x2按下 0x4禁用 0x8焦点"""
    dll = _qg_dll()
    if not dll or not args:
        return 0
    return dll.qg_style_flags(int(_qg_num(args[0], 0)))


def vg_on_paint_handler(*args):
    """绘制接管：vg_on_paint(id, fn)；每帧调 fn(w, h, state)，
    回调内用 vg_draw_* 原语自由作画（坐标 = 组件本地原点）。
    输入语义（点击/拖拽/滚轮）不变；容器类不支持（返回 false）。"""
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    clo = args[1]
    if not _is_closure(clo):
        return False
    cookie = _qg_cookie(clo)
    import ctypes as _c
    cb = _qg_paint_cb()
    return dll.qg_on_paint(int(_qg_num(args[0], 0)),
                           _c.cast(cb, _c.c_void_p),
                           _c.c_void_p(cookie)) == 1


def vg_toast_style_handler(*args):
    """Toast 样式：vg_toast_style(win, bg, text, border, radius)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 5:
        return False
    bg = _qg_color4(args, 1)
    tx = _qg_color4(args, 2)
    bd = _qg_color4(args, 3)
    if bg is None or tx is None or bd is None:
        return False
    flat = []
    for c in (bg, tx, bd):
        flat.extend((_c.c_float(c[0]), _c.c_float(c[1]), _c.c_float(c[2]), _c.c_float(c[3])))
    return dll.qg_toast_style(int(_qg_num(args[0], 0)), *flat,
                              _c.c_float(float(_qg_num(args[4], 8.0)))) == 1


def vg_scroll_style_handler(*args):
    """滚动条样式：vg_scroll_style(id, track, thumb, width)（作用于 ScrollPane）"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 4:
        return False
    tr = _qg_color4(args, 1)
    th = _qg_color4(args, 2)
    if tr is None or th is None:
        return False
    return dll.qg_scroll_style(int(_qg_num(args[0], 0)),
                               _c.c_float(tr[0]), _c.c_float(tr[1]), _c.c_float(tr[2]), _c.c_float(tr[3]),
                               _c.c_float(th[0]), _c.c_float(th[1]), _c.c_float(th[2]), _c.c_float(th[3]),
                               _c.c_float(float(_qg_num(args[3], 8.0)))) == 1


def vg_menu_bg_handler(*args):
    """菜单背景色（自绘菜单）：vg_menu_bg(mid, [r,g,b,a])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_menu_bg(int(_qg_num(args[0], 0)),
                          _c.c_float(c[0]), _c.c_float(c[1]),
                          _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_menu_hover_bg_handler(*args):
    """菜单悬停高亮色：vg_menu_hover_bg(mid, [r,g,b,a])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_menu_hover_bg(int(_qg_num(args[0], 0)),
                                _c.c_float(c[0]), _c.c_float(c[1]),
                                _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_menu_text_color_handler(*args):
    """菜单文字色：vg_menu_text_color(mid, [r,g,b,a])"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_menu_text_color(int(_qg_num(args[0], 0)),
                                  _c.c_float(c[0]), _c.c_float(c[1]),
                                  _c.c_float(c[2]), _c.c_float(c[3])) == 1


def vg_menu_border_handler(*args):
    """菜单描边：vg_menu_border(mid, [r,g,b,a], width)"""
    import ctypes as _c
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    c = _qg_color4(args, 1)
    if c is None:
        return False
    return dll.qg_menu_border(int(_qg_num(args[0], 0)),
                              _c.c_float(c[0]), _c.c_float(c[1]),
                              _c.c_float(c[2]), _c.c_float(c[3]),
                              _c.c_float(float(_qg_num(args[2], 1.0)))) == 1


def vg_set_position_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    dll.qg_set_pos(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)), int(_qg_num(args[2], 0)))
    return True


def vg_set_size_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 3:
        return False
    dll.qg_set_size(int(_qg_num(args[0], 0)), int(_qg_num(args[1], 0)), int(_qg_num(args[2], 0)))
    return True


def vg_set_visible_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_visible(int(_qg_num(args[0], 0)), 1 if args[1] else 0) == 1


def vg_set_enabled_handler(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    return dll.qg_set_enabled(int(_qg_num(args[0], 0)), 1 if args[1] else 0) == 1


def vg_destroy_handler(*args):
    dll = _qg_dll()
    return bool(dll) and bool(args) and dll.qg_destroy(int(_qg_num(args[0], 0))) == 1


def _vg_set_text(*args):
    dll = _qg_dll()
    return bool(dll) and len(args) >= 2 and \
        dll.qg_set_text(int(_qg_num(args[0], 0)), _qg_bytes(args[1])) == 1


def _vg_get_text(*args):
    dll = _qg_dll()
    if not dll or not args:
        return None
    import ctypes as _c
    buf = _c.create_string_buffer(4096)
    n = dll.qg_get_text(int(_qg_num(args[0], 0)), buf, 4096)
    if n <= 0:
        return None
    return buf.value.decode('utf-8', 'replace')


def _vg_set_value(*args):
    dll = _qg_dll()
    return bool(dll) and len(args) >= 2 and \
        dll.qg_set_value(int(_qg_num(args[0], 0)), _qg_num(args[1], 0.0)) == 1


def _vg_get_value(*args):
    dll = _qg_dll()
    if not dll or not args:
        return None
    return dll.qg_get_value(int(_qg_num(args[0], 0)))


def _vg_set_color(*args):
    dll = _qg_dll()
    if not dll or not args:
        return False
    c = _qg_color(args, 1)
    if c is None:
        return False
    import ctypes as _c
    return dll.qg_set_color(int(_qg_num(args[0], 0)), _c.c_float(c[0]), _c.c_float(c[1]),
                            _c.c_float(c[2]), _c.c_float(c[3])) == 1


def _vg_set_text_color(*args):
    dll = _qg_dll()
    if not dll or not args:
        return False
    c = _qg_color(args, 1)
    if c is None:
        return False
    import ctypes as _c
    return dll.qg_set_text_color(int(_qg_num(args[0], 0)), _c.c_float(c[0]), _c.c_float(c[1]),
                                 _c.c_float(c[2]), _c.c_float(c[3])) == 1


def _vg_add_item(*args):
    dll = _qg_dll()
    return bool(dll) and len(args) >= 2 and \
        dll.qg_add_item(int(_qg_num(args[0], 0)), _qg_bytes(args[1])) == 1


def _vg_set_font_size(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    import ctypes as _c
    return dll.qg_set_font_size(int(_qg_num(args[0], 0)), _c.c_float(_qg_num(args[1], 15.0))) == 1


def _vg_set_radius(*args):
    dll = _qg_dll()
    if not dll or len(args) < 2:
        return False
    import ctypes as _c
    return dll.qg_set_radius(int(_qg_num(args[0], 0)), _c.c_float(_qg_num(args[1], 8.0))) == 1

def input_handler(*args):
    """cinget 的 Python 参考实现（清单 interp 字段同名 'input_handler'）。

    解释器正主是 builtin_func._register_builtin_functions 的内嵌闭包
    （不可导入，语义含 IDE 模式提示符差异）；本表是【清单↔primitives.py
    覆盖面对账表】，登记保证「native 声明的每个内置在 python 原语层都有
    名字对得上的参考实现」——行为 oracle 由 --suite native 的 io 用例兜。
    """
    prompt = ' '.join(map(str, args)) if args else ''
    return input(prompt)


PRIMITIVE_IMPLS = {
    'json_encode': json_encode_handler,
    'json_decode': json_decode_handler,
    'json_pretty': json_pretty_handler,
    'json_valid': json_validate_handler,
    'file_open': file_open_prim_handler,
    'file_close': file_close_prim_handler,
    'file_read': file_read_prim_handler,
    'file_read_all': file_read_all_handler,
    'file_write': file_write_prim_handler,
    'file_write_at': file_write_at_handler,
    'file_seek': file_seek_handler,
    'file_tell': file_tell_handler,
    'file_size': file_size_prim_handler,
    'file_flush': file_flush_handler,
    'file_truncate': file_truncate_handler,
    'file_stat': file_stat_handler,
    'file_exists': file_exists_prim_handler,
    'file_remove': file_remove_prim_handler,
    'file_rename': file_rename_handler,
    'file_copy': file_copy_handler,
    'file_mtime': file_mtime_handler,
    'file_lock': file_lock_handler,
    'file_sync': file_sync_handler,
    'handle_valid': handle_valid_handler,
    'path_join': path_join_handler,
    'path_dirname': path_dirname_handler,
    'path_basename': path_basename_handler,
    'path_ext': path_ext_handler,
    'path_stem': path_stem_handler,
    'path_normalize': path_normalize_handler,
    'path_is_absolute': path_is_absolute_handler,
    'path_is_dir': path_is_dir_handler,
    'path_is_file': path_is_file_handler,
    'dir_list': dir_list_handler,
    'dir_mkdir': dir_mkdir_handler,
    'dir_mkdirs': dir_mkdirs_handler,
    'dir_remove': dir_remove_handler,
    'dir_size': dir_size_handler,
    'str_len': str_len_prim_handler,
    'str_empty': str_empty_handler,
    'str_slice': str_slice_handler,
    'str_byte_at': str_byte_at_handler,
    'str_find': str_find_handler,
    'str_rfind': str_rfind_handler,
    'str_contains': str_contains_handler,
    'str_starts_with': str_starts_with_handler,
    'str_ends_with': str_ends_with_handler,
    'str_count': str_count_handler,
    'str_split': str_split_prim_handler,
    'str_split_lines': str_split_lines_handler,
    'str_split_n': str_split_n_handler,
    'str_join': str_join_handler,
    'str_replace': str_replace_prim_handler,
    'str_replace_n': str_replace_n_handler,
    'str_upper': str_upper_handler,
    'str_lower': str_lower_handler,
    'str_trim': str_trim_prim_handler,
    'str_ltrim': str_ltrim_handler,
    'str_rtrim': str_rtrim_handler,
    'str_pad_left': str_pad_left_handler,
    'str_pad_right': str_pad_right_handler,
    'str_repeat': str_repeat_handler,
    'str_reverse': str_reverse_handler,
    'str_cmp': str_cmp_handler,
    'str_eq_ignore_case': str_eq_ignore_case_handler,
    'str_index_of_any': str_index_of_any_handler,
    'str_starts_with_any': str_starts_with_any_handler,
    'str_format': str_format_handler,
    'char_len': char_len_handler,
    'char_slice': char_slice_handler,
    'char_at': char_at_handler,
    'char_index_byte': char_index_byte_handler,
    'char_upper': char_upper_handler,
    'char_lower': char_lower_handler,
    'str_utf8_valid': str_utf8_valid_handler,
    'str_hash': str_hash_handler,
    'num_to_str': num_to_str_handler,
    'num_to_str_int': num_to_str_int_handler,
    'str_to_num': str_to_num_handler,
    'num_is_valid': num_is_valid_handler,
    'int_to_str': int_to_str_handler,
    'str_to_int': str_to_int_handler,
    'int_div': int_div_handler,
    'int_mod': int_mod_handler,
    'int_floor_div': int_floor_div_handler,
    # ---- P0-D 补：位运算（M5 尾 · 0）----
    'bit_and': bit_and_handler,
    'bit_or': bit_or_handler,
    'bit_xor': bit_xor_handler,
    'bit_not': bit_not_handler,
    'bit_shl': bit_shl_handler,
    'bit_shr': bit_shr_handler,
    'num_abs': num_abs_handler,
    'num_sign': num_sign_handler,
    'num_min': num_min_handler,
    'num_max': num_max_handler,
    'num_clamp': num_clamp_handler,
    'num_floor': num_floor_handler,
    'num_ceil': num_ceil_handler,
    'num_round': num_round_handler,
    'num_trunc': num_trunc_handler,
    'num_is_nan': num_is_nan_handler,
    'num_is_inf': num_is_inf_handler,
    'num_eq': num_eq_handler,
    'exp': exp_prim_handler,
    'log': log_prim_handler,
    'log2': log2_prim_handler,
    'log10': log10_prim_handler,
    'sin': sin_prim_handler,
    'cos': cos_prim_handler,
    'tan': tan_prim_handler,
    'asin': asin_prim_handler,
    'acos': acos_prim_handler,
    'atan': atan_prim_handler,
    'atan2': atan2_prim_handler,
    'hypot': hypot_prim_handler,
    'rand_seed': rand_seed_handler,
    'rand_int': rand_int_handler,
    'rand_range': rand_range_handler,
    'num_pi': num_pi_handler,
    'num_e': num_e_handler,
    'arr_new': arr_new_handler,
    'arr_new_n': arr_new_n_handler,
    'arr_from': arr_from_handler,
    'arr_len': arr_len_handler,
    'arr_get': arr_get_handler,
    'arr_set': arr_set_handler,
    'arr_push': arr_push_handler,
    'arr_pop': arr_pop_handler,
    'arr_insert': arr_insert_handler,
    'arr_remove_at': arr_remove_at_handler,
    'arr_remove': arr_remove_handler,
    'arr_clear': arr_clear_handler,
    'arr_index_of': arr_index_of_handler,
    'arr_contains': arr_contains_handler,
    'arr_count': arr_count_handler,
    'arr_slice': arr_slice_handler,
    'arr_concat': arr_concat_handler,
    'arr_reverse': arr_reverse_handler,
    'arr_sort': arr_sort_handler,
    'arr_sort_by': arr_sort_by_handler,
    'arr_unique': arr_unique_handler,
    'arr_flatten': arr_flatten_handler,
    'arr_map': arr_map_handler,
    'arr_filter': arr_filter_handler,
    'arr_reduce': arr_reduce_handler,
    'arr_sum': arr_sum_handler,
    'arr_min': arr_min_handler,
    'arr_max': arr_max_handler,
    'arr_join': arr_join_handler,
    'arr_copy': arr_copy_handler,
    'arr_eq': arr_eq_handler,
    'dict_new': dict_new_handler,
    'dict_from': dict_from_handler,
    'dict_len': dict_len_handler,
    'dict_get_or': dict_get_or_handler,
    'dict_has': dict_has_handler,
    'dict_del': dict_del_handler,
    'dict_values': dict_values_handler,
    'dict_items': dict_items_handler,
    'dict_clear': dict_clear_handler,
    'dict_copy': dict_copy_handler,
    'dict_merge': dict_merge_handler,
    'dict_update': dict_update_handler,
    'dict_invert': dict_invert_handler,
    'dict_get_path': dict_get_path_handler,
    'dict_set_path': dict_set_path_handler,
    # ---- P0.2（C 编码字节 / G 时间 / H 系统进程 / K 类型错误）----
    'bytes_len': bytes_len_prim_handler,
    'bytes_slice': bytes_slice_handler,
    'bytes_byte_at': bytes_byte_at_handler,
    'bytes_find': bytes_find_handler,
    'bytes_concat': bytes_concat_handler,
    'bytes_eq': bytes_eq_handler,
    'bytes_to_hex': bytes_to_hex_handler,
    'bytes_from_hex': bytes_from_hex_handler,
    'bytes_to_base64': bytes_to_base64_handler,
    'bytes_from_base64': bytes_from_base64_handler,
    'utf8_encode': utf8_encode_prim_handler,
    'utf8_decode': utf8_decode_handler,
    'utf8_char_count': utf8_char_count_handler,
    'url_encode': url_encode_handler,
    'url_decode': url_decode_handler,
    'time_now': time_now_handler,
    'time_monotonic': time_monotonic_handler,
    'time_now_ms': time_now_ms_handler,
    'time_monotonic_ms': time_monotonic_ms_handler,
    'time_date_parts': time_date_parts_handler,
    'time_utc_parts': time_utc_parts_handler,
    'time_make': time_make_handler,
    'time_strftime': time_strftime_handler,
    'time_parse': time_parse_handler,
    'time_sleep_ms': time_sleep_ms_handler,
    'time_zone_offset': time_zone_offset_handler,
    'env_get': env_get_handler,
    'env_set': env_set_handler,
    'env_unset': env_unset_handler,
    'env_list': env_list_handler,
    'env_expand': env_expand_handler,
    'proc_run': proc_run_handler,
    'proc_shell': proc_shell_handler,
    'proc_spawn': proc_spawn_handler,
    'proc_wait': proc_wait_handler,
    'proc_kill': proc_kill_handler,
    'proc_pid': proc_pid_handler,
    'proc_is_running': proc_is_running_handler,
    'proc_exit_code': proc_exit_code_handler,
    'cwd': cwd_handler,
    'cwd_set': cwd_set_handler,
    'hostname': hostname_handler,
    'username': username_handler,
    'args_get': args_get_handler,
    'stdout_write': stdout_write_handler,
    'stderr_write': stderr_write_handler,
    'stdin_read_line': stdin_read_line_handler,
    'type_of': type_of_handler,
    'type_is': type_is_handler,
    'is_null': is_null_handler,
    'is_array': is_array_handler,
    'is_dict': is_dict_handler,
    'is_string': is_string_handler,
    'is_bool': is_bool_handler,
    'is_handle': is_handle_handler,
    'to_num': to_num_handler,
    'to_bool': to_bool_handler,
    'to_array': to_array_handler,
    # ---- L 组 FFI（CP-4 · P0-L）----
    'ffi_load': ffi_load_handler,
    'ffi_sym': ffi_sym_handler,
    'ffi_call': ffi_call_handler,
    'ffi_call_v': ffi_call_v_handler,
    'ffi_free': ffi_free_handler,
    'ffi_last_error': ffi_last_error_handler,
    'ffi_str': ffi_str_handler,
    'ffi_ptr': ffi_ptr_handler,
    'ffi_abi_version': ffi_abi_version_handler,
    # ---- L2 原生插件（M7 · 0，docs/）----
    'plugin_load': plugin_load_handler,
    'plugin_call': plugin_call_handler,
    'plugin_free': plugin_free_handler,
    'plugin_names': plugin_names_handler,
    # ---- GUI 组（vg_*）：解释器与原生程序共用 wingui 实现 ----
    'vg_create_label': _vg_create_named('vg_create_label', 'label'),
    'vg_create_button': _vg_create_named('vg_create_button', 'button'),
    'vg_create_checkbox': _vg_create_named('vg_create_checkbox', 'checkbox'),
    'vg_create_toggle': _vg_create_named('vg_create_toggle', 'toggle'),
    'vg_create_textbox': _vg_create_named('vg_create_textbox', 'textbox'),
    'vg_create_slider': _vg_create_named('vg_create_slider', 'slider'),
    'vg_create_progressbar': _vg_create_named('vg_create_progressbar', 'progressbar'),
    'vg_create_card': _vg_create_named('vg_create_card', 'card'),
    'vg_create_combobox': _vg_create_named('vg_create_combobox', 'combobox'),
    'vg_create_listbox': _vg_create_named('vg_create_listbox', 'listbox'),
    'vg_create_tabcontrol': _vg_create_named('vg_create_tabcontrol', 'tabcontrol'),
    'vg_create_spinner': _vg_create_named('vg_create_spinner', 'spinner'),
    'vg_init': vg_init_handler,
    'vg_uninit': vg_uninit_handler,
    'vg_create_window': vg_create_window_handler,
    'vg_show_window': vg_show_window_handler,
    'vg_run': vg_run_handler,
    'vg_close_window': vg_close_window_handler,
    'vg_set_window_title': vg_set_window_title_handler,
    'vg_set_window_size': vg_set_window_size_handler,
    'vg_set_window_bg_gradient': vg_set_window_bg_gradient_handler,
    'vg_poll_event': vg_poll_event_handler,
    'vg_wait_event': vg_wait_event_handler,
    'vg_last_error': vg_last_error_handler,
    'vg_set_position': vg_set_position_handler,
    'vg_set_size': vg_set_size_handler,
    'vg_set_visible': vg_set_visible_handler,
    'vg_set_enabled': vg_set_enabled_handler,
    'vg_destroy': vg_destroy_handler,
    'vg_textbox_get_text': _named(_vg_get_text, 'vg_textbox_get_text_handler'),
    'vg_button_set_text': _named(_vg_set_text, 'vg_button_set_text_handler'),
    'vg_label_set_text': _named(_vg_set_text, 'vg_label_set_text_handler'),
    'vg_card_set_title': _named(_vg_set_text, 'vg_card_set_title_handler'),
    'vg_textbox_set_text': _named(_vg_set_text, 'vg_textbox_set_text_handler'),
    'vg_textbox_set_placeholder': _named(_vg_set_text, 'vg_textbox_set_placeholder_handler'),
    'vg_slider_set_value': _named(_vg_set_value, 'vg_slider_set_value_handler'),
    'vg_progressbar_set_value': _named(_vg_set_value, 'vg_progressbar_set_value_handler'),
    'vg_checkbox_set_checked': _named(_vg_set_value, 'vg_checkbox_set_checked_handler'),
    'vg_toggle_set_checked': _named(_vg_set_value, 'vg_toggle_set_checked_handler'),
    'vg_combobox_set_selected': _named(_vg_set_value, 'vg_combobox_set_selected_handler'),
    'vg_listbox_set_selected': _named(_vg_set_value, 'vg_listbox_set_selected_handler'),
    'vg_tabcontrol_set_selected': _named(_vg_set_value, 'vg_tabcontrol_set_selected_handler'),
    'vg_slider_get_value': _named(_vg_get_value, 'vg_slider_get_value_handler'),
    'vg_progressbar_get_value': _named(_vg_get_value, 'vg_progressbar_get_value_handler'),
    'vg_checkbox_get_checked': _named(_vg_get_value, 'vg_checkbox_get_checked_handler'),
    'vg_toggle_get_checked': _named(_vg_get_value, 'vg_toggle_get_checked_handler'),
    'vg_combobox_get_selected': _named(_vg_get_value, 'vg_combobox_get_selected_handler'),
    'vg_listbox_get_selected': _named(_vg_get_value, 'vg_listbox_get_selected_handler'),
    'vg_button_set_color': _named(_vg_set_color, 'vg_button_set_color_handler'),
    'vg_button_set_hover_color': _named(_vg_set_color, 'vg_button_set_hover_color_handler'),
    'vg_progressbar_set_color': _named(_vg_set_color, 'vg_progressbar_set_color_handler'),
    'vg_card_set_bg_color': _named(_vg_set_color, 'vg_card_set_bg_color_handler'),
    'vg_slider_set_fill_color': _named(_vg_set_color, 'vg_slider_set_fill_color_handler'),
    'vg_label_set_text_color': _named(_vg_set_text_color, 'vg_label_set_text_color_handler'),
    'vg_button_set_text_color': _named(_vg_set_text_color, 'vg_button_set_text_color_handler'),
    'vg_textbox_set_text_color': _named(_vg_set_text_color, 'vg_textbox_set_text_color_handler'),
    'vg_combobox_add_item': _named(_vg_add_item, 'vg_combobox_add_item_handler'),
    'vg_listbox_add_item': _named(_vg_add_item, 'vg_listbox_add_item_handler'),
    'vg_tabcontrol_add_tab': _named(_vg_add_item, 'vg_tabcontrol_add_tab_handler'),
    'vg_label_set_font_size': _named(_vg_set_font_size, 'vg_label_set_font_size_handler'),
    'vg_button_set_font_size': _named(_vg_set_font_size, 'vg_button_set_font_size_handler'),
    'vg_textbox_set_font_size': _named(_vg_set_font_size, 'vg_textbox_set_font_size_handler'),
    'vg_button_set_border_radius': _named(_vg_set_radius, 'vg_button_set_border_radius_handler'),
    'vg_card_set_border_radius': _named(_vg_set_radius, 'vg_card_set_border_radius_handler'),
    'vg_progressbar_set_border_radius': _named(_vg_set_radius, 'vg_progressbar_set_border_radius_handler'),
    'vg_button_set_position': _named(_vg_set_pos, 'vg_button_set_position_handler'),
    'vg_card_set_elevation': _named(_vg_noop_true, 'vg_card_set_elevation_handler'),
    'vg_combobox_clear': _named(_vg_clear, 'vg_combobox_clear_handler'),
    'vg_listbox_clear': _named(_vg_clear, 'vg_listbox_clear_handler'),
    'vg_spinner_start': _named(_vg_noop_true, 'vg_spinner_start_handler'),
    'vg_spinner_stop': _named(_vg_noop_true, 'vg_spinner_stop_handler'),
    # ---- v2：事件回调（推式绑定；与轮询并存） ----
    'vg_bind': vg_bind_handler,
    'vg_unbind': vg_unbind_handler,
    'vg_on_click': vg_on_click_handler,
    'vg_on_change': vg_on_change_handler,
    'vg_on_select': vg_on_select_handler,
    'vg_on_mouse': vg_on_mouse_handler,
    'vg_on_key': vg_on_key_handler,
    'vg_on_text': vg_on_text_handler,
    'vg_on_close': vg_on_close_handler,
    'vg_on_resize': vg_on_resize_handler,
    'vg_on_load': vg_on_load_handler,
    'vg_on_scroll': vg_on_scroll_handler,
    'vg_on_menu': vg_on_menu_handler,
    'vg_on_drop': vg_on_drop_handler,
    # ---- v2：帧原语 / 缓动 ----
    'vg_on_frame': vg_on_frame_handler,
    'vg_off_frame': vg_off_frame_handler,
    'vg_request_frame': vg_request_frame_handler,
    'vg_easing': vg_easing_handler,
    # ---- v2：窗口合成能力原语 ----
    'vg_window_transparent': vg_window_transparent_handler,
    'vg_window_backdrop': vg_window_backdrop_handler,
    'vg_set_window_round': vg_set_window_round_handler,
    'vg_set_window_borderless': vg_set_window_borderless_handler,
    'vg_set_window_topmost': vg_set_window_topmost_handler,
    'vg_prevent_close': vg_prevent_close_handler,
    'vg_window_drag': vg_window_drag_handler,
    'vg_window_minimize': vg_window_minimize_handler,
    'vg_window_maximize': vg_window_maximize_handler,
    'vg_window_restore': vg_window_restore_handler,
    'vg_set_window_bg_rgba': vg_set_window_bg_rgba_handler,
    'vg_set_theme': vg_set_theme_handler,
    'vg_set_font': vg_set_font_handler,
    # ---- v2：效果 / 动画糖 ----
    'vg_set_opacity': vg_set_opacity_handler,
    'vg_animate': vg_animate_handler,
    'vg_animate_stop': vg_animate_stop_handler,
    # ---- v2：Canvas / 图片组件 ----
    'vg_create_canvas': vg_create_canvas_handler,
    'vg_canvas_on_draw': vg_canvas_on_draw_handler,
    'vg_create_image': vg_create_image_handler,
    'vg_image_load': vg_image_load_handler,
    'vg_image_free': vg_image_free_handler,
    'vg_image_size': vg_image_size_handler,
    # ---- v2：绘制原语（vg_draw_*，Canvas 回调期间用） ----
    'vg_draw_fill_rect': vg_draw_fill_rect_handler,
    'vg_draw_stroke_rect': vg_draw_stroke_rect_handler,
    'vg_draw_fill_round_rect': vg_draw_fill_round_rect_handler,
    'vg_draw_stroke_round_rect': vg_draw_stroke_round_rect_handler,
    'vg_draw_fill_ellipse': vg_draw_fill_ellipse_handler,
    'vg_draw_stroke_ellipse': vg_draw_stroke_ellipse_handler,
    'vg_draw_line': vg_draw_line_handler,
    'vg_draw_text': vg_draw_text_handler,
    'vg_draw_measure_text': vg_draw_measure_text_handler,
    'vg_draw_gradient_rect': vg_draw_gradient_rect_handler,
    'vg_draw_push_clip': vg_draw_push_clip_handler,
    'vg_draw_pop_clip': vg_draw_pop_clip_handler,
    'vg_draw_push_translate': vg_draw_push_translate_handler,
    'vg_draw_push_rotate': vg_draw_push_rotate_handler,
    'vg_draw_push_scale': vg_draw_push_scale_handler,
    'vg_draw_pop_transform': vg_draw_pop_transform_handler,
    'vg_draw_image': vg_draw_image_handler,
    'vg_draw_push_blur': vg_draw_push_blur_handler,
    'vg_draw_pop_blur': vg_draw_pop_blur_handler,
    # ---- 绘制原语 ----
    'vg_path_begin': vg_path_begin_handler,
    'vg_path_move_to': vg_path_move_to_handler,
    'vg_path_line_to': vg_path_line_to_handler,
    'vg_path_quad_to': vg_path_quad_to_handler,
    'vg_path_curve_to': vg_path_curve_to_handler,
    'vg_path_arc': vg_path_arc_handler,
    'vg_path_svg_arc': vg_path_svg_arc_handler,
    'vg_path_rect': vg_path_rect_handler,
    'vg_path_round_rect': vg_path_round_rect_handler,
    'vg_path_ellipse': vg_path_ellipse_handler,
    'vg_path_close': vg_path_close_handler,
    'vg_path_fill': vg_path_fill_handler,
    'vg_path_fill_brush': vg_path_fill_brush_handler,
    'vg_path_stroke': vg_path_stroke_handler,
    'vg_path_stroke_brush': vg_path_stroke_brush_handler,
    'vg_path_bounds': vg_path_bounds_handler,
    'vg_path_hit': vg_path_hit_handler,
    'vg_path_stroke_hit': vg_path_stroke_hit_handler,
    'vg_path_line_cap': vg_path_line_cap_handler,
    'vg_path_line_join': vg_path_line_join_handler,
    'vg_path_miter_limit': vg_path_miter_limit_handler,
    'vg_path_dash': vg_path_dash_handler,
    'vg_brush_solid': vg_brush_solid_handler,
    'vg_brush_linear': vg_brush_linear_handler,
    'vg_brush_radial': vg_brush_radial_handler,
    'vg_brush_radial_focus': vg_brush_radial_focus_handler,
    'vg_brush_bitmap': vg_brush_bitmap_handler,
    'vg_brush_extend': vg_brush_extend_handler,
    'vg_brush_transform': vg_brush_transform_handler,
    'vg_brush_free': vg_brush_free_handler,
    'vg_draw_fill_rect_brush': vg_draw_fill_rect_brush_handler,
    'vg_draw_stroke_rect_brush': vg_draw_stroke_rect_brush_handler,
    'vg_draw_fill_round_rect_brush': vg_draw_fill_round_rect_brush_handler,
    'vg_draw_stroke_round_rect_brush': vg_draw_stroke_round_rect_brush_handler,
    'vg_draw_fill_ellipse_brush': vg_draw_fill_ellipse_brush_handler,
    'vg_draw_stroke_ellipse_brush': vg_draw_stroke_ellipse_brush_handler,
    'vg_rich_begin': vg_rich_begin_handler,
    'vg_rich_add': vg_rich_add_handler,
    'vg_rich_add_weight': vg_rich_add_weight_handler,
    'vg_rich_add_brush': vg_rich_add_brush_handler,
    'vg_rich_line_height': vg_rich_line_height_handler,
    'vg_rich_spacing': vg_rich_spacing_handler,
    'vg_rich_draw': vg_rich_draw_handler,
    'vg_rich_measure': vg_rich_measure_handler,
    'vg_draw_push_blend': vg_draw_push_blend_handler,
    'vg_draw_pop_blend': vg_draw_pop_blend_handler,
    'vg_draw_push_shadow': vg_draw_push_shadow_handler,
    'vg_draw_pop_shadow': vg_draw_pop_shadow_handler,
    'vg_draw_push_mask': vg_draw_push_mask_handler,
    'vg_draw_pop_mask': vg_draw_pop_mask_handler,
    'vg_draw_push_clip_path': vg_draw_push_clip_path_handler,
    'vg_surface': vg_surface_handler,
    'vg_surface_begin': vg_surface_begin_handler,
    'vg_surface_end': vg_surface_end_handler,
    'vg_surface_draw': vg_surface_draw_handler,
    'vg_surface_pixel': vg_surface_pixel_handler,
    'vg_surface_free': vg_surface_free_handler,
    'vg_draw_image_ex': vg_draw_image_ex_handler,
    'vg_draw_image_round': vg_draw_image_round_handler,
    'vg_backend': vg_backend_handler,
    # ---- v6：声明式视觉模板 ----
    'vg_path_store': vg_path_store_handler,
    'vg_path_free': vg_path_free_handler,
    'vg_geo_hit': vg_geo_hit_handler,
    'vg_textstyle': vg_textstyle_handler,
    'vg_textstyle_free': vg_textstyle_free_handler,
    'vg_tpl_create': vg_tpl_create_handler,
    'vg_tpl_part': vg_tpl_part_handler,
    'vg_tpl_part_text': vg_tpl_part_text_handler,
    'vg_tpl_part_dyn': vg_tpl_part_dyn_handler,
    'vg_tpl_state': vg_tpl_state_handler,
    'vg_tpl_transition': vg_tpl_transition_handler,
    'vg_tpl_free': vg_tpl_free_handler,
    'vg_apply_tpl': vg_apply_tpl_handler,
    'vg_clear_tpl': vg_clear_tpl_handler,
    'vg_tpl_default': vg_tpl_default_handler,
    # ---- v6 配套：表格 / 颜色选择器 / 日期选择器 ----
    'vg_create_table': _vg_create_named('vg_create_table', 'table'),
    'vg_table_add_column': vg_table_add_column_handler,
    'vg_table_add_row': vg_table_add_row_handler,
    'vg_table_set_cell': vg_table_set_cell_handler,
    'vg_table_get_selected': vg_table_get_selected_handler,
    'vg_table_set_selected': vg_table_set_selected_handler,
    'vg_create_colorpicker': _vg_create_named('vg_create_colorpicker', 'colorpicker'),
    'vg_colorpicker_set_rgb': vg_colorpicker_set_rgb_handler,
    'vg_colorpicker_get_rgb': vg_colorpicker_get_rgb_handler,
    'vg_create_datepicker': _vg_create_named('vg_create_datepicker', 'datepicker'),
    'vg_datepicker_set_date': vg_datepicker_set_date_handler,
    'vg_datepicker_get_date': vg_datepicker_get_date_handler,
    # ---- v3：文本富能力 ----
    'vg_draw_text_ex': vg_draw_text_ex_handler,
    'vg_draw_measure_text_ex': vg_draw_measure_text_ex_handler,
    # ---- v3：布局系统 ----
    'vg_layout_begin': vg_layout_begin_handler,
    'vg_layout_add': vg_layout_add_handler,
    'vg_layout_spacing': vg_layout_spacing_handler,
    'vg_layout_padding': vg_layout_padding_handler,
    'vg_layout_cols': vg_layout_cols_handler,
    'vg_layout_props': vg_layout_props_handler,
    'vg_layout_end': vg_layout_end_handler,
    'vg_get_pos': vg_get_pos_handler,
    'vg_get_size': vg_get_size_handler,
    'vg_window_client_size': vg_window_client_size_handler,
    # ---- v3：滚动容器与虚拟化列表 ----
    'vg_create_scrollpane': vg_create_scrollpane_handler,
    'vg_scroll_content': vg_scroll_content_handler,
    'vg_scroll_attach': vg_scroll_attach_handler,
    'vg_scroll_offset': vg_scroll_offset_handler,
    'vg_scroll_to': vg_scroll_to_handler,
    'vg_create_vlist': vg_create_vlist_handler,
    'vg_list_set_count': vg_list_set_count_handler,
    'vg_list_add_item': vg_list_add_item_handler,
    'vg_list_draw_count': vg_list_draw_count_handler,
    'vg_list_on_data': vg_list_on_data_handler,
    # ---- v3：模态对话框 / 菜单 / toast ----
    'vg_show_dialog': vg_show_dialog_handler,
    'vg_set_dialog_result': vg_set_dialog_result_handler,
    'vg_create_menu': vg_create_menu_handler,
    'vg_menu_add_item': vg_menu_add_item_handler,
    'vg_menu_separator': vg_menu_separator_handler,
    'vg_menu_popup': vg_menu_popup_handler,
    'vg_menu_destroy': vg_menu_destroy_handler,
    'vg_toast': vg_toast_handler,
    # ---- v3：系统集成 ----
    'vg_clipboard_set': vg_clipboard_set_handler,
    'vg_clipboard_get': vg_clipboard_get_handler,
    'vg_window_accept_files': vg_window_accept_files_handler,
    'vg_take_drop_file': vg_take_drop_file_handler,
    'vg_taskbar_progress': vg_taskbar_progress_handler,
    'vg_taskbar_state': vg_taskbar_state_handler,
    # ---- v4：样式原语 ----
    'vg_style_bg': vg_style_bg_handler,
    'vg_style_bg_gradient': vg_style_bg_gradient_handler,
    'vg_style_border': vg_style_border_handler,
    'vg_style_radius': vg_style_radius_handler,
    'vg_style_padding': vg_style_padding_handler,
    'vg_style_text': vg_style_text_handler,
    'vg_style_state_bg': vg_style_state_bg_handler,
    'vg_style_state_text': vg_style_state_text_handler,
    'vg_style_reset': vg_style_reset_handler,
    'vg_style_flags': vg_style_flags_handler,
    'vg_on_paint': vg_on_paint_handler,
    'vg_toast_style': vg_toast_style_handler,
    'vg_scroll_style': vg_scroll_style_handler,
    'vg_menu_bg': vg_menu_bg_handler,
    'vg_menu_hover_bg': vg_menu_hover_bg_handler,
    'vg_menu_text_color': vg_menu_text_color_handler,
    'vg_menu_border': vg_menu_border_handler,
    # ---- O 组 网络（P0.3 · 0 §4.9）----
    'socket_new': socket_new_handler,
    'socket_connect': socket_connect_handler,
    'socket_bind': socket_bind_handler,
    'socket_listen': socket_listen_handler,
    'socket_accept': socket_accept_handler,
    'socket_send': socket_send_handler,
    'socket_recv': socket_recv_handler,
    'socket_send_all': socket_send_all_handler,
    'socket_recv_line': socket_recv_line_handler,
    'socket_close': socket_close_handler,
    'socket_set_timeout': socket_set_timeout_handler,
    'socket_set_nodelay': socket_set_nodelay_handler,
    'socket_local_addr': socket_local_addr_handler,
    'socket_remote_addr': socket_remote_addr_handler,
    'socket_resolve': socket_resolve_handler,
    'socket_udp_new': socket_udp_new_handler,
    'socket_udp_send': socket_udp_send_handler,
    'socket_udp_recv': socket_udp_recv_handler,
    'socket_select': socket_select_handler,
    'cinget': input_handler,
}
