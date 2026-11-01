"""
PvZ2 PTX 解码/编码核心逻辑 - Android 版
ptx1 格式：32 字节头 + ASTC 5x5 原始压缩块（每块 16 字节）
编解码全部由 APK 内置的 astcenc 5.7 (arm64, 位于 nativeLibraryDir/libastcenc.so)
完成，不依赖 texture2ddecoder。

.meta(.ptxmeta) 与桌面版 ptx_codec.py 格式完全一致，可跨端使用：
  JSON 头部 + b'\\n__RAW__\\n' + 原始 ASTC 压缩数据
"""
import os
import sys
import json
import struct
import subprocess
import tempfile
import hashlib
import shutil
from PIL import Image

# ── 常量 ──
MAGIC_PTX1 = 0x70747831          # 'ptx1' LE
HEADER_FMT = '<I7I'              # magic, ver, cw, ch, aw, ah, f6, f7
HEADER_SIZE = 32
ASTC_BLOCK_W, ASTC_BLOCK_H = 5, 5
ASTC_BLOCK_BYTES = 16
ASTC_FILE_MAGIC = 0x5CA1AB13     # .astc 文件头魔数（大端）
ASTC_HEADER_SIZE = 16
ASTC_TIMEOUT = 600


# ── astcenc 可执行文件定位 ──
def _find_astcenc():
    # 1. APK 解包后的 nativeLibraryDir（Android 上唯一保证可执行的位置）
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        nld = activity.getApplicationInfo().nativeLibraryDir
        cand = os.path.join(nld, 'libastcenc.so')
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    # 2. 脚本目录（桌面调试 / 其他打包方式）
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('libastcenc.so', 'astcenc', 'astcenc.exe'):
        cand = os.path.join(here, name)
        if os.path.isfile(cand):
            try:
                os.chmod(cand, 0o755)
            except OSError:
                pass
            return cand
    # 3. PATH
    found = shutil.which('astcenc')
    return found if found else 'astcenc'


ASTCENC = _find_astcenc()


def test_astcenc():
    """启动时自检 astcenc 是否能实际执行（Android 10+ W^X 限制等）

    返回 (ok: bool, message: str)
    """
    if ASTCENC == 'astcenc':
        return False, 'astcenc 未找到（PATH fallback）'
    if not os.path.isfile(ASTCENC):
        return False, 'astcenc 路径不存在: %s' % ASTCENC
    import stat
    try:
        st = os.stat(ASTCENC)
        if not (st.st_mode & 0o111):
            try:
                os.chmod(ASTCENC, 0o755)
            except OSError as e:
                return False, '无可执行权限且 chmod 失败: %r' % e
    except Exception as e:
        return False, 'stat 失败: %r' % e
    # 用 -help 测试：astcenc 无参数会打印用法并退出码非 0
    try:
        proc = subprocess.Popen(
            [ASTCENC, '-help'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out, _ = proc.communicate(timeout=10)
        text = out.decode('utf-8', 'replace')
        # astcenc -help 退出码非 0 但会打印用法，出现 "astcenc" 字样即可
        if 'astcenc' in text.lower() or 'usage' in text.lower() \
                or proc.returncode in (0, 1, 2):
            return True, 'astcenc 自检通过 (rc=%d, len=%d)' % (
                proc.returncode, len(text))
        return False, 'astcenc 异常输出 rc=%d: %s' % (
            proc.returncode, text[-400:])
    except FileNotFoundError as e:
        return False, 'execve 失败 (FileNotFoundError): %r' % e
    except PermissionError as e:
        return False, 'execve 失败 (PermissionError/W^X): %r' % e
    except OSError as e:
        return False, 'execve 失败 (OSError errno=%d): %r' % (e.errno, e)
    except subprocess.TimeoutExpired:
        return False, 'astcenc -help 超时 10s（可能卡在 stdin）'
    except Exception as e:
        return False, '未知错误: %r' % e


def _work_dir():
    """astcenc 临时工作目录（应用缓存目录）"""
    base = None
    try:
        from jnius import autoclass
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        base = activity.getCacheDir().getAbsolutePath()
    except Exception:
        pass
    if not base:
        base = tempfile.gettempdir()
    d = tempfile.mkdtemp(prefix='ptxwork_', dir=base)
    return d


def _run_astcenc(args, log=print):
    """执行 astcenc，失败时抛出带输出的异常

    Android 关键点：
    - 必须显式给 stdin=DEVNULL，否则子进程会继承 SDL2 的空 stdin 管道，
      astcenc 若尝试读取 stdin 会一直阻塞，表现就是界面"卡住"。
    - 用 Popen + communicate 而不是 run，方便在异常时 kill 子进程。
    """
    cmd = [ASTCENC] + args
    log('  $ astcenc ' + ' '.join(args[:4]) + ' ...')
    log('  cmd=%s' % cmd)
    if not os.path.isfile(ASTCENC):
        raise FileNotFoundError('astcenc 二进制不存在: %r' % ASTCENC)
    import stat
    try:
        st = os.stat(ASTCENC)
        log('  astcenc mode=0%o size=%d' % (stat.S_IMODE(st.st_mode),
                                          st.st_size))
        if not (st.st_mode & 0o111):
            log('  警告: astcenc 无可执行权限，尝试 chmod +x')
            os.chmod(ASTCENC, 0o755)
    except Exception as e:
        log('  stat 失败: %r' % e)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = proc.communicate(timeout=ASTC_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise RuntimeError('astcenc 超时 (%ds):\n%s' %
                           (ASTC_TIMEOUT, out.decode('utf-8', 'replace')[-800:]))
    text = out.decode('utf-8', 'replace')
    if proc.returncode != 0:
        raise RuntimeError('astcenc 返回 %d:\n%s' %
                           (proc.returncode, text[-1200:]))
    return text


def read_header(raw):
    return struct.unpack_from(HEADER_FMT, raw, 0)


def make_header(cw, ch, aw, ah, ver=1, f6=0, f7=100):
    return struct.pack(HEADER_FMT, MAGIC_PTX1, ver, cw, ch, aw, ah, f6, f7)


def astc_block_count(w, h):
    return ((w + ASTC_BLOCK_W - 1) // ASTC_BLOCK_W) * \
           ((h + ASTC_BLOCK_H - 1) // ASTC_BLOCK_H)


def _build_astc_header(w, h):
    """构造 16 字节 .astc 文件头

    ASTC 规范：magic 为固定字节序列；尺寸字段是 3 字节【小端】
    （astcenc unpack_bytes: a + b<<8 + c<<16，即 dim[0]=LSB）。
    早期版本误写成大端，导致 astcenc 把尺寸解析成天文数字，
    按错误长度读取时短读，报 "File read failed"。
    """
    return bytes([
        0x13, 0xAB, 0xA1, 0x5C,          # magic
        ASTC_BLOCK_W, ASTC_BLOCK_H, 1,   # 块尺寸 5x5x1
        w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF,   # dim_x 小端
        h & 0xFF, (h >> 8) & 0xFF, (h >> 16) & 0xFF,   # dim_y 小端
        1, 0, 0,                         # dim_z = 1（小端，单张 2D）
    ])


# ── 解码 ──
def decode_ptx(ptx_path, png_path=None, log=print):
    """PTX → PNG（保存完整画布 + .ptxmeta 侧车）"""
    raw = open(ptx_path, 'rb').read()
    magic, ver, cw, ch, aw, ah, f6, f7 = read_header(raw)
    if magic != MAGIC_PTX1:
        raise ValueError('非 ptx1 格式 (magic=0x%08X)' % magic)

    data = raw[HEADER_SIZE:]
    expected = astc_block_count(cw, ch) * ASTC_BLOCK_BYTES
    if len(data) != expected:
        log('  警告: 数据长度 %d != 预期 %d' % (len(data), expected))
        data = data[:expected]

    if png_path is None:
        png_path = os.path.splitext(ptx_path)[0] + '.png'

    work = _work_dir()
    try:
        a_in = os.path.join(work, 'in.astc')
        a_out = os.path.join(work, 'out.png')
        with open(a_in, 'wb') as f:
            f.write(_build_astc_header(cw, ch))
            f.write(data)

        # astcenc 5.x: astcenc -dl <in.astc> <out.png>
        out = _run_astcenc(['-dl', a_in, a_out], log)

        # astcenc 直接产出 PNG（尺寸 = 画布 cw x ch）
        img = Image.open(a_out)
        if img.size != (cw, ch):
            log('  警告: astcenc 输出 %s != 画布 %dx%d' %
                (str(img.size), cw, ch))
        img.save(png_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 保存侧车 .ptxmeta（头部信息 + 原始压缩数据，用于完美回包）
    meta_path = os.path.splitext(png_path)[0] + '.ptxmeta'
    meta = {
        'magic': magic,
        'version': ver,
        'canvas_w': cw,
        'canvas_h': ch,
        'actual_w': aw,
        'actual_h': ah,
        'field6': f6,
        'field7': f7,
        'data_hash': hashlib.sha256(data).hexdigest(),
        'data_len': len(data),
    }
    with open(meta_path, 'wb') as f:
        f.write(json.dumps(meta, indent=2).encode('utf-8'))
        f.write(b'\n__RAW__\n')
        f.write(data)

    log('  解码: %dx%d (actual %dx%d) -> %s' % (cw, ch, aw, ah, png_path))
    return png_path, meta_path


# ── 编码 ──
def encode_ptx(png_path, ptx_path=None, log=print):
    """PNG → PTX（有 .ptxmeta 完美回包；无则 astcenc 重新编码）"""
    meta_path = os.path.splitext(png_path)[0] + '.ptxmeta'
    if ptx_path is None:
        ptx_path = os.path.splitext(png_path)[0] + '.PTX'

    # ── 完美回包：使用侧车中的原始压缩数据 ──
    if os.path.isfile(meta_path):
        with open(meta_path, 'rb') as f:
            content = f.read()
        sep = content.find(b'\n__RAW__\n')
        if sep > 0:
            meta = json.loads(content[:sep].decode('utf-8'))
            raw_data = content[sep + len(b'\n__RAW__\n'):]
            header = make_header(
                meta['canvas_w'], meta['canvas_h'],
                meta['actual_w'], meta['actual_h'],
                meta.get('version', 1),
                meta.get('field6', 0),
                meta.get('field7', 100))
            with open(ptx_path, 'wb') as f:
                f.write(header)
                f.write(raw_data)
            log('  完美回包（原始数据）: %s -> %s' % (png_path, ptx_path))
            return ptx_path

    # ── astcenc 重新编码 ──
    img = Image.open(png_path).convert('RGBA')
    w, h = img.size

    # astcenc 本身接受任意尺寸（内部按 5x5 分块处理边缘），
    # 画布尺寸直接用 PNG 原始尺寸，不要向上取整到 5 的倍数。
    cw, ch = w, h

    work = _work_dir()
    try:
        p_in = os.path.join(work, 'in.png')
        p_out = os.path.join(work, 'out.astc')
        img.save(p_in)

        # astcenc 5.x: astcenc -cl <in.png> <out.astc> 5x5 -fastest
        _run_astcenc(['-cl', p_in, p_out, '5x5', '-fastest'], log)

        with open(p_out, 'rb') as f:
            astc_data = f.read()
        if len(astc_data) < ASTC_HEADER_SIZE or \
                astc_data[:4] != b'\x13\xAB\xA1\x5C':
            raise RuntimeError('astcenc 输出不是合法 .astc 文件')
        raw_blocks = astc_data[ASTC_HEADER_SIZE:]
    finally:
        shutil.rmtree(work, ignore_errors=True)

    expected = astc_block_count(cw, ch) * ASTC_BLOCK_BYTES
    if len(raw_blocks) != expected:
        log('  警告: 压缩块长度 %d != 预期 %d' %
            (len(raw_blocks), expected))

    header = make_header(cw, ch, w, h)
    with open(ptx_path, 'wb') as f:
        f.write(header)
        f.write(raw_blocks)

    log('  重新编码（astcenc）: %s %dx%d -> %s' %
        (png_path, w, h, ptx_path))
    return ptx_path


# ── 回包验证 ──
def roundtrip_verify(ptx_path, log=print):
    orig_hash = hashlib.sha256(open(ptx_path, 'rb').read()).hexdigest()
    log('原始文件 SHA256: %s' % orig_hash)

    work = _work_dir()
    try:
        png = os.path.join(work, 'test.png')
        ptx_out = os.path.join(work, 'test.PTX')
        log('1. 解码...')
        decode_ptx(ptx_path, png, log)
        log('2. 回包...')
        encode_ptx(png, ptx_out, log=log)
        out_hash = hashlib.sha256(open(ptx_out, 'rb').read()).hexdigest()
        log('回包文件 SHA256: %s' % out_hash)
        if orig_hash == out_hash:
            log('✓ 完美回包验证通过：字节级一致')
            return True
        else:
            log('✗ 回包与原始不一致（astcenc 重编码时属正常）')
            return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── 批量 ──
def batch_decode(input_dir, output_dir=None, log=print):
    if output_dir is None:
        output_dir = input_dir
    files = []
    for r, _, fns in os.walk(input_dir):
        for f in fns:
            if f.upper().endswith('.PTX'):
                files.append(os.path.join(r, f))

    log('待解码 PTX: %d' % len(files))
    ok, fail = 0, 0
    for i, ptx in enumerate(files, 1):
        rel = os.path.relpath(ptx, input_dir)
        out = os.path.join(output_dir, os.path.splitext(rel)[0] + '.png')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.isfile(out) and os.path.isfile(
                os.path.splitext(out)[0] + '.ptxmeta'):
            ok += 1
            continue
        try:
            decode_ptx(ptx, out, log)
            ok += 1
        except Exception as e:
            log('  失败: %s -> %s' % (rel, e))
            fail += 1
        if i % 20 == 0:
            log('  进度 %d/%d 成功 %d 失败 %d' % (i, len(files), ok, fail))
    log('完成: 成功 %d, 失败 %d' % (ok, fail))
    return ok, fail


def batch_encode(input_dir, output_dir=None, log=print):
    if output_dir is None:
        output_dir = input_dir
    files = []
    for r, _, fns in os.walk(input_dir):
        for f in fns:
            if f.lower().endswith('.png'):
                files.append(os.path.join(r, f))

    log('待编码 PNG: %d' % len(files))
    ok, fail = 0, 0
    for i, png in enumerate(files, 1):
        rel = os.path.relpath(png, input_dir)
        out = os.path.join(output_dir, os.path.splitext(rel)[0] + '.PTX')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.isfile(out):
            ok += 1
            continue
        try:
            encode_ptx(png, out, log)
            ok += 1
        except Exception as e:
            log('  失败: %s -> %s' % (rel, e))
            fail += 1
        if i % 20 == 0:
            log('  进度 %d/%d 成功 %d 失败 %d' % (i, len(files), ok, fail))
    log('完成: 成功 %d, 失败 %d' % (ok, fail))
    return ok, fail
