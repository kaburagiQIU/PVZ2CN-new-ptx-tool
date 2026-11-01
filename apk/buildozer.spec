[app]
# 应用基本信息
title = PTX编解码工具
package.name = ptxtool
package.domain = org.kivy

# 源码目录（main.py 为入口）
source.dir = .
source.include_exts = py,png,ptx,ptxmeta

# 版本
version = 1.1.1

# 构建要求：不依赖 texture2ddecoder，ASTC 由内置 astcenc 二进制完成
# 钉死 Python 3.11.9：p4a start.c 的 PyConfig 路径在 3.12+ 上有 zipimport 兼容问题
# （encodings 模块找不到 / Py_SetPath 崩溃），3.11 最稳定
requirements = hostpython3==3.11.9,python3==3.11.9,kivy,Pillow

# Android 配置（MANAGE_EXTERNAL_STORAGE 用于 Android 11+ 读写手机存储）
android.permissions = MANAGE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 31
# minapi=26(Android 8.0)：CPython 3.12 的 grpmodule.c 无条件调用 API 26 才有的
# getgrent/setgrent，在 21 下 -Werror 编译失败；26 可直接编译通过
android.minapi = 26
android.ndk = 25b
android.arch = arm64-v8a
android.build_tools = 33.0.0
android.accept_sdk_license = 1

# 预编译 astcenc（重命名为 lib*.so 以进入 nativeLibraryDir，可执行）
android.add_libs_arm64_v8a = native_libs/arm64-v8a/libastcenc.so

# 图形界面
orientation = portrait
fullscreen = 0

# 调试期保留日志
[buildozer]
log_level = 2
warn_on_static_libs = False
