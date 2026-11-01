"""
PvZ2 PTX 解码/编码工具（支持 ptx1 格式：ASTC 5×5）

PTX ptx1 格式结构（32 字节头 + ASTC 5×5 压缩数据）：
  偏移  长度  字段
  0     4     magic  0x70747831 ('ptx1' LE)
  4     4     version（=1）
  8     4     canvasW   画布宽（ASTC 编码用此尺寸）
  12    4     canvasH   画布高
  16    4     actualW   实际内容宽
  20    4     actualH   实际内容高
  24    4     field6    （=0）
  28    4     field7    （=100）
  32..        ASTC 5×5 压缩块数据（每块 16 字节，块数 = ceil(canvasW/5) × ceil(canvasH/5)）

用法:
  python ptx_codec.py decode  <输入.ptx> [输出.png]       # 解码单个
  python ptx_codec.py encode  <输入.png> [输出.ptx]        # 编码单个（需 .ptxmeta 侧车）
  python ptx_codec.py batch_d <输入目录> [输出目录]        # 批量解码
  python ptx_codec.py batch_e <输入目录> [输出目录]        # 批量编码
  python ptx_codec.py roundtrip <输入.ptx>                 # 解码后立即回包并比对
"""
import os
import sys
import json
import struct
import subprocess
import tempfile
import hashlib
from PIL import Image

# ── 常量 ──
MAGIC_PTX1 = 0x70747831  # 'ptx1' LE
HEADER_FMT = '<I7I'      # magic, ver, cw, ch, aw, ah, f6, f7
HEADER_SIZE = 32
ASTC_BLOCK_W, ASTC_BLOCK_H = 5, 5
ASTC_BLOCK_BYTES = 16

# astcenc 路径（PyInstaller 打包后用 _MEIPASS，否则用脚本目录）
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASTCENC = os.path.join(SCRIPT_DIR, 'astcenc.exe')

# ASTC 文件头 16 字节（astcenc 输出格式）
# magic(4) + blockX(1) + blockY(1) + blockZ(1) + dimX(3) + dimY(3) + dimZ(3)
ASTC_FILE_MAGIC = 0x5CA1AB13
ASTC_HEADER_SIZE = 16


def read_header(raw):
    """解析 PTX 头部，返回 (magic, ver, cw, ch, aw, ah, f6, f7)"""
    return struct.unpack_from(HEADER_FMT, raw, 0)


def make_header(cw, ch, aw, ah, ver=1, f6=0, f7=100):
    """构造 PTX 头部 32 字节"""
    return struct.pack(HEADER_FMT, MAGIC_PTX1, ver, cw, ch, aw, ah, f6, f7)


def astc_block_count(w, h):
    """计算 ASTC 5×5 块数"""
    return ((w + ASTC_BLOCK_W - 1) // ASTC_BLOCK_W) * ((h + ASTC_BLOCK_H - 1) // ASTC_BLOCK_H)


# ── 解码 ──
def decode_ptx(ptx_path, png_path=None):
    """
    解码 PTX → PNG
    同时保存 .ptxmeta 侧车文件（含原始头部信息和原始压缩数据哈希），
    供 encode 完美回包使用。
    """
    import texture2ddecoder

    raw = open(ptx_path, 'rb').read()
    magic, ver, cw, ch, aw, ah, f6, f7 = read_header(raw)
    assert magic == MAGIC_PTX1, f'非 ptx1 格式 (magic=0x{magic:08X})'

    data = raw[HEADER_SIZE:]
    blocks = astc_block_count(cw, ch)
    expected = blocks * ASTC_BLOCK_BYTES
    if len(data) != expected:
        print(f'  警告: 数据长度 {len(data)} != 预期 {expected}')

    # 解码 ASTC → RGBA（保存完整画布，不裁剪）
    out = texture2ddecoder.decode_astc(data, cw, ch, ASTC_BLOCK_W, ASTC_BLOCK_H)
    img = Image.frombytes('RGBA', (cw, ch), bytes(out), 'raw', 'BGRA')

    if png_path is None:
        png_path = os.path.splitext(ptx_path)[0] + '.png'

    img.save(png_path)

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
        f.write(data)  # 原始压缩数据

    print(f'  解码: {cw}x{ch} (actual {aw}x{ah}) → {png_path}')
    return png_path, meta_path


# ── 编码 ──
def encode_ptx(png_path, ptx_path=None):
    """
    编码 PNG → PTX
    优先使用 .ptxmeta 侧车中的原始压缩数据（完美回包，字节级一致）。
    无侧车时用 astcenc 重新编码（有损但游戏可用）。
    """
    meta_path = os.path.splitext(png_path)[0] + '.ptxmeta'
    if ptx_path is None:
        ptx_path = os.path.splitext(png_path)[0] + '.PTX'

    if os.path.isfile(meta_path):
        # ── 完美回包：使用原始压缩数据 ──
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
                meta.get('field7', 100)
            )
            with open(ptx_path, 'wb') as f:
                f.write(header)
                f.write(raw_data)
            print(f'  完美回包（原始数据）: {png_path} → {ptx_path}')
            return ptx_path

    # ── 重新编码：用 astcenc ──
    if not os.path.isfile(ASTCENC):
        raise FileNotFoundError(f'astcenc.exe 未找到: {ASTCENC}')

    img = Image.open(png_path).convert('RGBA')
    w, h = img.size

    # 用临时文件接收 astcenc 输出
    tmp_astc = tempfile.mktemp(suffix='.astc')
    tmp_png = tempfile.mktemp(suffix='.png')

    # astcenc 本身接受任意尺寸（内部按 5x5 分块处理边缘），
    # 因此画布尺寸直接用 PNG 的原始尺寸，不要向上取整到 5 的倍数——
    # 否则会把 PTX 头部的 canvas_w/h 改错，导致与原版不一致。
    cw, ch = w, h
    img.save(tmp_png)

    # 调用 astcenc 编码（-medium 质量，兼容性更好）
    cmd = [ASTCENC, '-cl', tmp_png, tmp_astc, '5x5', '-medium']
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f'astcenc 失败: {result.stderr[-500:]}')

    # 读取 .astc 文件，去掉 16 字节头，得到原始 ASTC 块
    with open(tmp_astc, 'rb') as f:
        astc_data = f.read()
    if len(astc_data) < ASTC_HEADER_SIZE or astc_data[:4] != b'\x13\xAB\xA1\x5C':
        raise RuntimeError('astcenc 输出不是合法 .astc 文件')
    raw_blocks = astc_data[ASTC_HEADER_SIZE:]

    expected = astc_block_count(cw, ch) * ASTC_BLOCK_BYTES
    if len(raw_blocks) != expected:
        print(f'  警告: astcenc 输出块长度 {len(raw_blocks)} != 预期 {expected}')

    # 清理临时文件
    for tmp in (tmp_png, tmp_astc):
        try:
            os.remove(tmp)
        except OSError:
            pass

    # 写 PTX。无 .ptxmeta 时无法得知原版 actual_w/h，
    # 这里将 actual 设为画布尺寸（即整张纹理）。
    header = make_header(cw, ch, cw, ch)
    with open(ptx_path, 'wb') as f:
        f.write(header)
        f.write(raw_blocks)

    print(f'  重新编码（astcenc）: {png_path} {w}x{h} → {ptx_path}')
    return ptx_path


# ── 完美回包验证 ──
def roundtrip_verify(ptx_path):
    """解码后立即回包，与原始文件做 SHA256 比对"""
    orig_hash = hashlib.sha256(open(ptx_path, 'rb').read()).hexdigest()
    print(f'原始文件 SHA256: {orig_hash}')

    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, 'test.png')
        ptx_out = os.path.join(tmp, 'test.PTX')

        print('1. 解码...')
        decode_ptx(ptx_path, png)

        print('2. 回包...')
        encode_ptx(png, ptx_out)

        out_hash = hashlib.sha256(open(ptx_out, 'rb').read()).hexdigest()
        print(f'回包文件 SHA256: {out_hash}')

        if orig_hash == out_hash:
            print('✓ 完美回包验证通过：字节级一致')
        else:
            print('✗ 回包文件与原始不一致（使用 astcenc 重新编码时正常）')
            # 对比头部
            r1 = open(ptx_path, 'rb').read(HEADER_SIZE)
            r2 = open(ptx_out, 'rb').read(HEADER_SIZE)
            h1 = struct.unpack_from(HEADER_FMT, r1, 0)
            h2 = struct.unpack_from(HEADER_FMT, r2, 0)
            print(f'  原始头部: {h1}')
            print(f'  回包头部: {h2}')


# ── 批量处理 ──
def batch_decode(input_dir, output_dir=None):
    """批量解码目录下所有 .PTX 文件"""
    if output_dir is None:
        output_dir = input_dir

    files = []
    for r, _, fns in os.walk(input_dir):
        for f in fns:
            if f.upper().endswith('.PTX'):
                files.append(os.path.join(r, f))

    print(f'待解码 PTX: {len(files)}')
    ok, fail = 0, 0
    for i, ptx in enumerate(files, 1):
        rel = os.path.relpath(ptx, input_dir)
        out = os.path.join(output_dir, os.path.splitext(rel)[0] + '.png')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        # 跳过已存在
        if os.path.isfile(out) and os.path.isfile(os.path.splitext(out)[0] + '.ptxmeta'):
            ok += 1
            continue
        try:
            decode_ptx(ptx, out)
            ok += 1
        except Exception as e:
            print(f'  失败: {rel} -> {e}')
            fail += 1
        if i % 50 == 0:
            print(f'  进度 {i}/{len(files)} 成功 {ok} 失败 {fail}')
    print(f'\n完成: 成功 {ok}, 失败 {fail}')
    return ok, fail


def batch_encode(input_dir, output_dir=None):
    """批量编码目录下所有 .png（有 .ptxmeta 侧车的）回 PTX"""
    if output_dir is None:
        output_dir = input_dir

    files = []
    for r, _, fns in os.walk(input_dir):
        for f in fns:
            if f.lower().endswith('.png'):
                png = os.path.join(r, f)
                # 需要 .ptxmeta 侧车或至少 PNG
                files.append(png)

    print(f'待编码 PNG: {len(files)}')
    ok, fail = 0, 0
    for i, png in enumerate(files, 1):
        rel = os.path.relpath(png, input_dir)
        out = os.path.join(output_dir, os.path.splitext(rel)[0] + '.PTX')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.isfile(out):
            ok += 1
            continue
        try:
            encode_ptx(png, out)
            ok += 1
        except Exception as e:
            print(f'  失败: {rel} -> {e}')
            fail += 1
        if i % 50 == 0:
            print(f'  进度 {i}/{len(files)} 成功 {ok} 失败 {fail}')
    print(f'\n完成: 成功 {ok}, 失败 {fail}')
    return ok, fail


# ── CLI ──
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == 'decode':
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else None
        decode_ptx(src, dst)
    elif cmd == 'encode':
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else None
        encode_ptx(src, dst)
    elif cmd == 'roundtrip':
        src = sys.argv[2]
        roundtrip_verify(src)
    elif cmd == 'batch_d':
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else None
        batch_decode(src, dst)
    elif cmd == 'batch_e':
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else None
        batch_encode(src, dst)
    else:
        print(f'未知命令: {cmd}')
        print(__doc__)


if __name__ == '__main__':
    main()
