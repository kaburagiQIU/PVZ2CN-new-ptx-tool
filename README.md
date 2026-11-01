# PvZ2 PTX 解码/编码工具

《植物大战僵尸2》中文版贴图资源（`.PTX`）的查看与修改工具，支持 **ptx1** 格式（ASTC 5×5 压缩）的双向转换，并可对编码结果做**回包验证**，确保与原始文件一致。

提供两个版本：

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows | `PTX编解码工具.exe` | 单文件免安装，图形界面 |
| Android | `apk/ptxtool-1.1.1-arm64-v8a-debug.apk` | Kivy 界面，arm64-v8a，内置 astcenc 5.7 |

## 功能

- **解码 PTX → PNG**：单个或整个目录批量
- **编码 PNG → PTX**：需要同名 `.ptxmeta` 侧车文件（记录画布尺寸、实际内容尺寸等参数），保证回包一致
- **回包验证**：编码后立即解码并与原图比对，确认无损
- 保留原始 32 字节头字段，支持画布尺寸大于实际内容尺寸的贴图

## Windows 使用

1. 下载 `PTX编解码工具.exe`，双击运行（无需安装 Python）
2. 选择「解码 PTX→PNG」或「编码 PNG→PTX」
3. 支持单个文件与批量目录

> `astcenc.exe` 是随程序调用的 ASTC 编码器，必须与程序放在同一目录（打包版已内置）。

## 命令行用法

需要 Python 3.8+ 与 `Pillow`：

```bash
python ptx_codec.py decode input.ptx output.png      # 解码单个
python ptx_codec.py encode input.png output.ptx      # 编码单个（需 .ptxmeta）
python ptx_codec.py batch_d 输入目录 输出目录          # 批量解码
python ptx_codec.py batch_e 输入目录 输出目录          # 批量编码
```

## PTX (ptx1) 格式

32 字节头 + ASTC 5×5 压缩块：

| 偏移 | 长度 | 字段 |
|---|---|---|
| 0 | 4 | magic `0x70747831`（`ptx1`） |
| 4 | 4 | version（=1） |
| 8 | 4 | canvasW 画布宽 |
| 12 | 4 | canvasH 画布高 |
| 16 | 4 | actualW 实际内容宽 |
| 20 | 4 | actualH 实际内容高 |
| 24 | 4 | 保留（=0） |
| 28 | 4 | 保留（=100） |
| 32… | — | ASTC 5×5 块数据（每块 16 字节，数量 = ⌈canvasW/5⌉ × ⌈canvasH/5⌉） |

## 源码结构

```
ptx_tool/
├─ ptx_codec.py          PTX 编解码核心（可作库或 CLI）
├─ ptx_gui.py            Windows 图形界面（Tkinter）
├─ PTX编解码工具.spec     PyInstaller 打包配置
├─ astcenc.exe           ASTC 编码器（Windows）
└─ apk/
   ├─ main.py            Android 界面（Kivy）
   ├─ ptx_codec_android.py
   ├─ buildozer.spec      Buildozer 配置
   └─ ptxtool-1.1.1-arm64-v8a-debug.apk
```

## 构建

Windows：

```bash
pip install pyinstaller pillow
pyinstaller "PTX编解码工具.spec"
```

Android：

```bash
cd apk
buildozer android debug
```

## 依赖

- Python：`Pillow`
- `astcenc` 5.7（Windows 版随仓库提供；Android 版已打入 APK）

## 免责声明

本工具仅用于对**自己合法持有**的游戏资源进行格式解析、查看与学习研究，不附带任何游戏资源，也不得用于传播游戏原始资源。使用产生的任何后果由使用者自行承担。

## 作者

kaburagiQIU
</content>
