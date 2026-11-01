"""
PvZ2 PTX 解码/编码工具 - Android (Kivy) 界面
支持 ptx1 格式（ASTC 5x5）：解码 PTX→PNG、编码 PNG→PTX（.ptxmeta 完美回包）、批量处理
编解码由 APK 内置 astcenc 5.7 (arm64) 完成
"""
import os
import sys
import threading
import traceback

# ══════════════════════════════════════════════════════════════════════
# 崩溃日志：任何阶段闪退都会把堆栈写到 ptx_crash.log（无需存储权限的目录）
# 位置：手机存储/Android/data/org.kivy.ptxtool/files/ptx_crash.log
# ══════════════════════════════════════════════════════════════════════════
LOG_PATH = None
_logfile = None


class _Tee(object):
    """同时输出到原始 stdout/stderr 和日志文件"""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _init_crash_log():
    global LOG_PATH, _logfile
    dirs = []
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        for f in (act.getExternalFilesDir(None), act.getFilesDir(),
                  act.getCacheDir()):
            if f is not None:
                dirs.append(f.getAbsolutePath())
    except Exception:
        pass
    dirs.append(os.path.expanduser('~'))
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, 'ptx_crash.log')
            f = open(p, 'a', encoding='utf-8')
            _logfile, LOG_PATH = f, p
            sys.stdout = _Tee(sys.__stdout__, f)
            sys.stderr = _Tee(sys.__stderr__, f)
            return
        except Exception:
            continue


def stage(msg):
    print('[stage] %s' % msg, flush=True)


def _excepthook(etype, value, tb):
    print('=== UNCAUGHT EXCEPTION ===', flush=True)
    traceback.print_exception(etype, value, tb)
    if _logfile:
        _logfile.flush()


def _thread_excepthook(args):
    print('=== UNCAUGHT in thread %s ===' % args.thread.name, flush=True)
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    if _logfile:
        _logfile.flush()


_init_crash_log()
sys.excepthook = _excepthook
threading.excepthook = _thread_excepthook

stage('==== boot pid=%d python=%s ====' % (os.getpid(), sys.version.split()[0]))

# 设备/环境信息（帮助定位架构、Android 版本、native 库）
try:
    from jnius import autoclass
    Build = autoclass('android.os.Build')
    stage('device: %s %s | Android %s (SDK %d) | ABIs=%s' % (
        Build.BRAND, Build.MODEL, Build.VERSION.RELEASE,
        Build.VERSION.SDK_INT, ','.join(Build.SUPPORTED_ABIS)))
    _info = autoclass('org.kivy.android.PythonActivity').mActivity \
        .getApplicationInfo()
    stage('nativeLibraryDir: %s' % _info.nativeLibraryDir)
    stage('native libs: %s' % ', '.join(
        sorted(os.listdir(_info.nativeLibraryDir))))
except Exception as e:
    stage('device info unavailable (desktop?): %r' % e)

# ── 分阶段导入，崩溃时日志能指出具体阶段 ──
try:
    stage('import kivy')
    from kivy.app import App
    from kivy.core.text import LabelBase
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.popup import Popup
    from kivy.clock import mainthread

    stage('import kivy window backend')
    import kivy.core.window  # noqa: F401  强制尽早加载 SDL2 窗口后端

    stage('import PIL')
    from PIL import Image  # noqa: F401

    stage('import ptx_codec_android')
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ptx_codec_android as codec
    stage('astcenc = %s (exists=%s)' % (
        codec.ASTCENC,
        os.path.isfile(codec.ASTCENC) if codec.ASTCENC != 'astcenc'
        else False))
except Exception:
    traceback.print_exc()
    if _logfile:
        _logfile.flush()
    raise


# ── 中文字体：覆盖 Kivy 默认的 Roboto 别名（默认字体不含中文）──
def setup_font():
    candidates = [
        '/system/fonts/NotoSansCJK-Regular.ttc',
        '/system/fonts/NotoSerifCJK-Regular.ttc',
        '/system/fonts/DroidSansFallback.ttf',
        '/system/fonts/SourceHanSansSC-Regular.otf',
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\simhei.ttf',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                LabelBase.register(name='Roboto', fn_regular=p, fn_bold=p)
                return p
            except Exception:
                pass
    return None


# ── Android 权限/环境 ──
def _autoclass(name):
    from jnius import autoclass
    return autoclass(name)


def sdk_int():
    try:
        return _autoclass('android.os.Build$VERSION').SDK_INT
    except Exception:
        return 0


def is_android():
    try:
        from jnius import autoclass  # noqa
        _autoclass('org.kivy.android.PythonActivity').mActivity
        return True
    except Exception:
        return False


def has_storage():
    """是否可读写共享存储（Android 11+ 需“所有文件访问”权限）"""
    if not is_android():
        return True
    try:
        if sdk_int() >= 30:
            return _autoclass('android.os.Environment').isExternalStorageManager()
        if sdk_int() >= 23:
            act = _autoclass('org.kivy.android.PythonActivity').mActivity
            return act.checkSelfPermission(
                'android.permission.WRITE_EXTERNAL_STORAGE') == 0
        return True
    except Exception:
        return True


def request_storage():
    """跳转到“所有文件访问”授权页（Android 11+）或弹窗申请旧版存储权限"""
    act = _autoclass('org.kivy.android.PythonActivity').mActivity
    if sdk_int() >= 30:
        Intent = _autoclass('android.content.Intent')
        Uri = _autoclass('android.net.Uri')
        pkg = act.getPackageName()
        uri = Uri.parse('package:' + pkg)
        # 优先本应用的授权页，失败则打开总列表
        try:
            act.startActivity(Intent(
                'android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION', uri))
            return
        except Exception:
            pass
        act.startActivity(Intent(
            'android.settings.MANAGE_ALL_FILES_ACCESS_PERMISSION'))
    elif sdk_int() >= 23:
        act.requestPermissions([
            'android.permission.READ_EXTERNAL_STORAGE',
            'android.permission.WRITE_EXTERNAL_STORAGE',
        ], 100)


def app_files_dir():
    """无需任何权限即可读写的应用专属目录"""
    try:
        act = _autoclass('org.kivy.android.PythonActivity').mActivity
        f = act.getExternalFilesDir(None)
        if f is not None:
            p = f.getAbsolutePath()
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    return os.path.expanduser('~')


def start_dir():
    if has_storage():
        for p in ('/storage/emulated/0', '/sdcard'):
            if os.path.isdir(p):
                return p
    return app_files_dir()


class PTXApp(App):
    def build(self):
        self.title = 'PTX 编解码工具'
        self._running = False
        stage('App.build: setup font')
        font = setup_font()
        stage('font: %s' % font)

        root = BoxLayout(orientation='vertical', spacing=5, padding=10)

        title = Label(text='PvZ2 PTX 编解码工具', size_hint_y=0.06,
                      font_size='20sp', bold=True)
        root.add_widget(title)

        # 权限状态行
        perm_row = BoxLayout(size_hint_y=0.07, spacing=5)
        self.perm_label = Label(font_size='13sp')
        self.perm_btn = Button(text='授予文件权限', font_size='13sp',
                               size_hint_x=0.32)
        self.perm_btn.bind(on_press=lambda i: self._request_perm())
        perm_row.add_widget(self.perm_label)
        perm_row.add_widget(self.perm_btn)
        root.add_widget(perm_row)

        # 功能按钮
        btn_layout = BoxLayout(size_hint_y=0.36, spacing=5,
                               orientation='vertical')
        for text, cb in [
            ('解码 PTX → PNG', self.decode_one),
            ('编码 PNG → PTX', self.encode_one),
            ('回包验证', self.roundtrip),
            ('批量解码（选目录）', self.batch_decode),
            ('批量编码（选目录）', self.batch_encode),
            ('测试 astcenc 自检', self.test_astcenc),
        ]:
            b = Button(text=text, font_size='16sp')
            b.bind(on_press=cb)
            btn_layout.add_widget(b)
        root.add_widget(btn_layout)

        info = Label(
            text='解码产物：同名 .png + .ptxmeta（务必保留，用于字节级回包）\n'
                 '编码：有 .ptxmeta 完美回包；无则 astcenc 有损重编码\n'
                 '输出文件与输入文件在同一目录',
            size_hint_y=0.16, font_size='12sp',
            halign='left', valign='top')
        info.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
        root.add_widget(info)

        scroll = ScrollView(size_hint_y=0.39)
        self.log_label = Label(text='', font_size='12sp', size_hint_y=None,
                               valign='top', halign='left', padding=(4, 4))
        self.log_label.bind(
            width=lambda i, w: setattr(i, 'text_size', (w, None)),
            texture_size=lambda i, ts: setattr(i, 'height', ts[1]))
        scroll.add_widget(self.log_label)
        root.add_widget(scroll)

        self._refresh_perm()
        self.log('就绪。astcenc: %s' % codec.ASTCENC)
        if codec.ASTCENC == 'astcenc':
            self.log('警告: 未找到内置 astcenc，编解码将失败')
        if not is_android():
            self.log('(桌面调试模式)')
        # 启动时自检 astcenc，直接显示结果在日志区
        stage('App.build: astcenc self-test')
        try:
            ok, msg = codec.test_astcenc()
            if ok:
                self.log('[自检] ✓ %s' % msg)
            else:
                self.log('[自检] ✗ astcenc 不可用:')
                self.log('  %s' % msg)
                self.log('  解码/编码功能将无法工作，请看下面的建议')
                self.log('  - 若提示 PermissionError/W^X: 系统拒绝执行')
                self.log('    libastcenc.so，需用静态链接版本或换打包方式')
                self.log('  - 若提示 FileNotFoundError: 二进制未被打包进 APK')
        except Exception as e:
            self.log('[自检] 异常: %r' % e)
        stage('App.build: UI ready')
        return root

    def on_resume(self):
        self._refresh_perm()

    @mainthread
    def _refresh_perm(self):
        if not is_android():
            self.perm_label.text = '桌面模式'
            self.perm_btn.disabled = True
            return
        if has_storage():
            self.perm_label.text = '存储权限：已授予\n工作目录: ' + start_dir()
            self.perm_btn.disabled = True
        else:
            self.perm_label.text = '存储权限：未授予\n无法访问手机存储中的文件'
            self.perm_btn.disabled = False

    def _request_perm(self):
        try:
            request_storage()
        except Exception as e:
            self.log('授权失败: %s' % e)

    @mainthread
    def log(self, msg):
        self.log_label.text += str(msg) + '\n'
        if len(self.log_label.text) > 4000:
            self.log_label.text = self.log_label.text[-4000:]

    def run_async(self, func, *args):
        if self._running:
            self.log('有任务正在运行，请等待完成')
            return
        self._running = True

        def worker():
            try:
                func(*args)
                self.log('── 完成 ──')
            except Exception as e:
                self.log('错误: %s' % e)
                traceback.print_exc()
            finally:
                self._running = False

        threading.Thread(target=worker, daemon=True).start()

    # ── 选择器 ──
    def _open_picker(self, title, callback, select_dir=False, filters=None):
        content = BoxLayout(orientation='vertical')
        kwargs = {'path': start_dir(), 'dirselect': True} if select_dir \
            else {'path': start_dir()}
        if filters:
            kwargs['filters'] = filters
        try:
            fc = FileChooserListView(**kwargs)
        except Exception:
            kwargs['path'] = app_files_dir()
            fc = FileChooserListView(**kwargs)
        content.add_widget(fc)

        bar = BoxLayout(size_hint_y=0.12, spacing=5)
        btn_ok = Button(text='选择当前目录' if select_dir else '确定')
        btn_cancel = Button(text='取消')
        bar.add_widget(btn_ok)
        bar.add_widget(btn_cancel)
        content.add_widget(bar)
        popup = Popup(title=title, content=content, size_hint=(0.95, 0.92))

        def do_ok(_):
            if select_dir:
                # 优先用高亮选中的目录，否则用当前浏览目录
                path = fc.selection[0] if fc.selection and \
                    os.path.isdir(fc.selection[0]) else fc.path
                popup.dismiss()
                callback(path)
            elif fc.selection and os.path.isfile(fc.selection[0]):
                path = fc.selection[0]
                popup.dismiss()
                callback(path)

        btn_ok.bind(on_press=do_ok)
        btn_cancel.bind(on_press=popup.dismiss)
        popup.open()

    # ── 功能 ──
    def test_astcenc(self, _):
        self.log('── 测试 astcenc 自检 ──')
        def do():
            ok, msg = codec.test_astcenc()
            self.log('结果: %s | %s' % ('✓' if ok else '✗', msg))
            if not ok:
                self.log('  解码/编码功能将无法工作')
        self.run_async(do)

    def decode_one(self, _):
        self._open_picker('选择 PTX 文件', self._do_decode,
                          filters=['*.PTX', '*.ptx'])

    def _do_decode(self, path):
        out = os.path.splitext(path)[0] + '.png'

        def do():
            self.log('解码: %s' % path)
            codec.decode_ptx(path, out, log=self.log)
            self.log('  -> %s' % out)
        self.run_async(do)

    def encode_one(self, _):
        self._open_picker('选择 PNG 文件', self._do_encode,
                          filters=['*.png', '*.PNG'])

    def _do_encode(self, path):
        out = os.path.splitext(path)[0] + '.PTX'

        def do():
            self.log('编码: %s' % path)
            codec.encode_ptx(path, out, log=self.log)
            self.log('  -> %s' % out)
        self.run_async(do)

    def roundtrip(self, _):
        self._open_picker('选择 PTX 文件', self._do_roundtrip,
                          filters=['*.PTX', '*.ptx'])

    def _do_roundtrip(self, path):
        self.run_async(lambda: codec.roundtrip_verify(path, log=self.log))

    def batch_decode(self, _):
        self._open_picker('选择 PTX 所在目录', self._do_batch_decode,
                          select_dir=True)

    def _do_batch_decode(self, d):
        self.run_async(lambda: (
            self.log('批量解码: %s' % d),
            codec.batch_decode(d, d, log=self.log)))

    def batch_encode(self, _):
        self._open_picker('选择 PNG 所在目录', self._do_batch_encode,
                          select_dir=True)

    def _do_batch_encode(self, d):
        self.run_async(lambda: (
            self.log('批量编码: %s' % d),
            codec.batch_encode(d, d, log=self.log)))


class CrashScreenApp(App):
    """主界面崩溃后的兜底界面：把错误显示在屏幕上"""
    def __init__(self, tb_text, **kw):
        self._tb = tb_text
        super(CrashScreenApp, self).__init__(**kw)

    def build(self):
        sv = ScrollView()
        lbl = Label(
            text='PTX 工具启动失败，请把日志文件发给开发者：\n%s\n\n%s'
                 % (LOG_PATH or '(未知)', self._tb),
            font_size='13sp', size_hint_y=None,
            halign='left', valign='top', padding=(10, 10))
        lbl.bind(
            width=lambda i, w: setattr(i, 'text_size', (w, None)),
            texture_size=lambda i, ts: setattr(i, 'height', ts[1]))
        sv.add_widget(lbl)
        return sv


if __name__ == '__main__':
    stage('starting PTXApp.run()')
    try:
        PTXApp().run()
    except Exception:
        tb = traceback.format_exc()
        print('FATAL during App.run:\n' + tb, flush=True)
        if _logfile:
            _logfile.flush()
        try:
            stage('showing crash screen')
            CrashScreenApp(tb).run()
        except Exception:
            traceback.print_exc()
        raise
