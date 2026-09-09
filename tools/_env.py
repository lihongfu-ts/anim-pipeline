# -*- coding: utf-8 -*-
"""⚑⚑ **跑之前先确认环境齐** —— ⚑ 缺东西要在**第一秒**说清楚。

⚠⚠ 为什么单独有这一份：⚑ 这条链靠 `ffmpeg` 抽帧，⚠ 而它**不在 pip 里** ⇒ ⚑ 装 Python 依赖
  的人照样会缺它。⛔ 没有这层守卫时，缺 ffmpeg 报的是：

```
FileNotFoundError: [WinError 2] 系统找不到指定的文件。      ← ⚠ 还是一段 traceback
```

⚑ 新用户⛔ 看不出这句说的是 **ffmpeg** 还是**他自己那个 mp4** —— ⚑ 两种猜法都会浪费半小时。
⚑ 同理「文件不在」也得说清**是相对哪个目录找的**（⚑ 这套的路径根是**当前工作目录**）。
"""
import os
import shutil
import sys

# ⚠⚠ ⛔ 这里**不能**写 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, …)` ——
#   ⚑ 那是各脚本单独跑时的写法，⚠ 而这一份是**被 import 的**：⛔ 谁再包一次，
#   ⚑ 前一个 wrapper 被回收时会**关掉同一个 buffer** ⇒ `ValueError: I/O operation on closed file`。
#   ⇒ ✅ 用 `reconfigure`（⚑ 幂等，⚑ 包多少次都没事）。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # Windows 控制台默认 GBK
except Exception:
    pass


def die(*lines):
    print('\n'.join(lines))
    sys.exit(1)


def _local_ffmpeg_dirs():
    """⚑ 一键安装（tools/install_ffmpeg.py）会把 ffmpeg 放在 tools/bin，⚑ winget 装的在 %LOCALAPPDATA% 下 —— ⚑ 两处都不一定在 PATH 里。"""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    out = [os.path.join(here, 'bin')]
    la = os.environ.get('LOCALAPPDATA')
    if la:
        out += glob.glob(os.path.join(la, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', 'ffmpeg-*', 'bin'))
    return out


def need_ffmpeg():
    """⚑ ffmpeg ＋ ffprobe 都要 —— ⚠ 有些精简包只带 ffmpeg，⛔ 而这里两个都用。
    ⚑ PATH 里没有就看 tools/bin 和 winget 目录，有就塞进 PATH（⚑ 子进程 subprocess 靠 PATH 找）。"""
    exe = '.exe' if os.name == 'nt' else ''
    for d in _local_ffmpeg_dirs():
        if os.path.exists(os.path.join(d, 'ffmpeg' + exe)) and d not in os.environ.get('PATH', ''):
            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
    miss = [c for c in ('ffmpeg', 'ffprobe') if not shutil.which(c)]
    if not miss:
        return
    die(f'✗ 找不到 {" 和 ".join(miss)} —— ⚑ 这条链靠它抽帧，⛔ 不装跑不了',
        '',
        '  一键     : python tools/install_ffmpeg.py     （或双击 安装.bat；网页「设置 → 环境」也有按钮）',
        '  Windows : winget install Gyan.FFmpeg      （或 scoop install ffmpeg）',
        '  macOS   : brew install ffmpeg',
        '  Linux   : sudo apt install ffmpeg')


def need_file(path, what='文件', hint=''):
    """⚑ 找不到就一句话说清楚，⛔ 别让 ffprobe 的 CalledProcessError 糊一屏。"""
    if os.path.exists(path):
        return path
    lines = [f'✗ {what}不在：{path}', '',
             f'  ⚑ 相对路径是按**当前工作目录**解析的（⚑ 现在在 {os.getcwd()}）']
    if hint:
        lines.append(f'  ⚑ {hint}')
    die(*lines)


def need_pkgs():
    """⚑ numpy / Pillow —— ⚑ 给一句能直接抄的 pip 命令，⛔ 别只抛 ImportError。"""
    miss = []
    for mod, pkg in (('numpy', 'numpy'), ('PIL', 'Pillow')):
        try:
            __import__(mod)
        except ImportError:
            miss.append(pkg)
    if miss:
        die(f'✗ 缺 Python 包：{" ".join(miss)}',
            '', f'  pip install {" ".join(miss)}      （或 pip install -r requirements.txt）')
