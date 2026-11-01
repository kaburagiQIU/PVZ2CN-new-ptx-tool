"""
PvZ2 PTX 解码/编码工具 - 图形界面
支持 ptx1 格式（ASTC 5×5）：解码 PTX→PNG、编码 PNG→PTX（完美回包）
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 确保能 import ptx_codec
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptx_codec


class PTXToolGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('PvZ2 PTX 解码/编码工具')
        self.root.geometry('620x480')
        self.root.resizable(False, False)

        # ── 上方：功能按钮 ──
        frame_btn = ttk.LabelFrame(root, text='操作', padding=10)
        frame_btn.pack(fill='x', padx=10, pady=5)

        row1 = ttk.Frame(frame_btn)
        row1.pack(fill='x', pady=3)
        ttk.Button(row1, text='解码 PTX→PNG', command=self.decode_one).pack(side='left', padx=5)
        ttk.Button(row1, text='编码 PNG→PTX', command=self.encode_one).pack(side='left', padx=5)
        ttk.Button(row1, text='回包验证', command=self.roundtrip).pack(side='left', padx=5)

        row2 = ttk.Frame(frame_btn)
        row2.pack(fill='x', pady=3)
        ttk.Button(row2, text='批量解码（选目录）', command=self.batch_decode).pack(side='left', padx=5)
        ttk.Button(row2, text='批量编码（选目录）', command=self.batch_encode).pack(side='left', padx=5)

        # ── 中间：说明 ──
        frame_info = ttk.LabelFrame(root, text='说明', padding=10)
        frame_info.pack(fill='x', padx=10, pady=5)
        info_text = (
            '• 解码：PTX → PNG（同时保存 .ptxmeta 侧车文件用于完美回包）\n'
            '• 编码：PNG → PTX（有 .ptxmeta 时字节级完美回包，无则用 astcenc 重新编码）\n'
            '• 批量：选择目录，自动处理所有 PTX/PNG 文件\n'
            '• 回包验证：解码后立即回包，与原文件做 SHA256 比对'
        )
        ttk.Label(frame_info, text=info_text, justify='left').pack()

        # ── 下方：日志 ──
        frame_log = ttk.LabelFrame(root, text='日志', padding=5)
        frame_log.pack(fill='both', expand=True, padx=10, pady=5)

        self.log_text = tk.Text(frame_log, height=12, font=('Consolas', 9))
        scrollbar = ttk.Scrollbar(frame_log, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 状态栏
        self.status = tk.StringVar(value='就绪')
        ttk.Label(root, textvariable=self.status, relief='sunken', anchor='w').pack(
            fill='x', side='bottom')

        self._running = False

    def log(self, msg):
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.root.update_idletasks()

    def run_async(self, func, *args):
        """在后台线程运行，避免界面卡死"""
        if self._running:
            messagebox.showwarning('提示', '有任务正在运行，请等待完成')
            return
        self._running = True

        def worker():
            try:
                func(*args)
                self.log('── 完成 ──')
            except Exception as e:
                self.log(f'错误: {e}')
            finally:
                self._running = False
                self.status.set('就绪')

        self.status.set('处理中...')
        threading.Thread(target=worker, daemon=True).start()

    def decode_one(self):
        path = filedialog.askopenfilename(
            title='选择 PTX 文件',
            filetypes=[('PTX 文件', '*.PTX *.ptx'), ('所有文件', '*.*')])
        if not path:
            return
        out = os.path.splitext(path)[0] + '.png'

        def do():
            self.log(f'解码: {path}')
            ptx_codec.decode_ptx(path, out)
            self.log(f'  → {out}')

        self.run_async(do)

    def encode_one(self):
        path = filedialog.askopenfilename(
            title='选择 PNG 文件',
            filetypes=[('PNG 文件', '*.png'), ('所有文件', '*.*')])
        if not path:
            return
        out = os.path.splitext(path)[0] + '.PTX'

        def do():
            self.log(f'编码: {path}')
            ptx_codec.encode_ptx(path, out)
            self.log(f'  → {out}')

        self.run_async(do)

    def roundtrip(self):
        path = filedialog.askopenfilename(
            title='选择 PTX 文件',
            filetypes=[('PTX 文件', '*.PTX *.ptx'), ('所有文件', '*.*')])
        if not path:
            return
        self.log(f'回包验证: {path}')
        self.run_async(ptx_codec.roundtrip_verify, path)

    def batch_decode(self):
        d = filedialog.askdirectory(title='选择 PTX 所在目录')
        if not d:
            return
        out_d = filedialog.askdirectory(title='选择输出目录（取消=同源目录）') or None

        def do():
            self.log(f'批量解码: {d} → {out_d or d}')
            ptx_codec.batch_decode(d, out_d)

        self.run_async(do)

    def batch_encode(self):
        d = filedialog.askdirectory(title='选择 PNG 所在目录')
        if not d:
            return
        out_d = filedialog.askdirectory(title='选择输出目录（取消=同源目录）') or None

        def do():
            self.log(f'批量编码: {d} → {out_d or d}')
            ptx_codec.batch_encode(d, out_d)

        self.run_async(do)


if __name__ == '__main__':
    root = tk.Tk()
    app = PTXToolGUI(root)
    root.mainloop()
