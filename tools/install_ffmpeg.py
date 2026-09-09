# -*- coding: utf-8 -*-
"""⚑⚑ 一键装 ffmpeg / ffprobe（⚑ 用户 2026-09-09：装这个东西也要一键）。⚑ 网页「一键安装」和 安装.bat 都调它。

```bash
python tools/install_ffmpeg.py            # 装（已装则直接 ✅ 退出）
python tools/install_ffmpeg.py --check    # 只查不装
python tools/install_ffmpeg.py --dest=tools/bin
```

顺序（Windows）：
  0. 已经找得到（PATH / tools/bin / winget 的 Gyan.FFmpeg 目录）⇒ 直接用
  1. winget install Gyan.FFmpeg（有 winget 就用，装完在 %LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_*\\ffmpeg-*\\bin）
  2. gyan.dev essentials zip（含 ffmpeg.exe + ffprobe.exe，约 100 MB，国内直连实测可达）⇒ 解压到 tools/bin
  3. BtbN GitHub zip（国内慢，最后兜底）
macOS / Linux：不下二进制，提示 brew / apt。

⚑ 输出每行以 ⚑ / ✅ / ✗ / ⚠ 开头，⚑ 下载进度每 5% 一行 —— ⚑ 网页端按行滚动显示。⚑ 退出码 0 = 装好且 `ffmpeg -version` 能跑。
"""
import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# ⚑ 输出编码：⚑ 被网页/管道调用（PYTHONIOENCODING 或非 tty）⇒ UTF-8；⚑ 在 cmd 控制台里直接跑 ⇒ 保持控制台码页（中文 Windows 是 GBK），
#   ⚑ 并把 GBK 里没有的符号换成 ASCII —— ⛔ 否则 安装.bat 里满屏乱码（2026-09-09 别人机器上实测）
_TTY = sys.stdout.isatty() and not os.environ.get('PYTHONIOENCODING')
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace', **({} if _TTY else {'encoding': 'utf-8'}))
    except Exception:
        pass
_ASCII = _TTY and (sys.stdout.encoding or '').lower().replace('-', '') != 'utf8'
_SYM = {'⚑': '*', '✅': '[OK]', '✗': '[X]', '⚠': '[!]', '⇒': '=>', '←': '<-', '→': '->'}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXE = '.exe' if os.name == 'nt' else ''
SOURCES = [   # ⚑ (名字, url)；⚑ zip 里找 */bin/ffmpeg.exe 和 ffprobe.exe
    ('gyan.dev essentials', 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'),
    ('BtbN GitHub', 'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip'),
]


def say(s):
    if _ASCII:
        for k, v in _SYM.items():
            s = s.replace(k, v)
    print(s, flush=True)


def candidates(dest: Path):
    """⚑ 可能放着 ffmpeg 的目录：tools/bin、winget 的 Gyan.FFmpeg。"""
    out = [dest]
    la = os.environ.get('LOCALAPPDATA')
    if la:
        out += [Path(p) for p in glob.glob(os.path.join(la, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', 'ffmpeg-*', 'bin'))]
    return out


def find(dest: Path):
    """⚑ 返回同时有 ffmpeg 和 ffprobe 的目录（⚑ 先 PATH，再候选目录），没有返回 None。"""
    w = shutil.which('ffmpeg')
    if w and shutil.which('ffprobe'):
        return Path(w).parent
    for d in candidates(dest):
        if (d / f'ffmpeg{EXE}').exists() and (d / f'ffprobe{EXE}').exists():
            return d
    return None


def verify(d: Path) -> bool:
    try:
        r = subprocess.run([str(d / f'ffmpeg{EXE}'), '-version'], capture_output=True, text=True, timeout=20)
        line = (r.stdout or '').splitlines()[0] if r.stdout else ''
        say(f'✅ {line or "ffmpeg 可用"}  ← {d}')
        return r.returncode == 0
    except Exception as e:
        say(f'✗ 跑不起来：{e}')
        return False


def try_winget(dest: Path):
    if os.name != 'nt' or not shutil.which('winget'):
        return None
    say('⚑ 有 winget，用它装 Gyan.FFmpeg（约 1~3 分钟，看网速）…')
    try:
        p = subprocess.run(['winget', 'install', '-e', '--id', 'Gyan.FFmpeg', '--accept-source-agreements',
                            '--accept-package-agreements', '--disable-interactivity'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900)
        tail = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()[-3:]
        for ln in tail:
            say('  ' + ln)
    except Exception as e:
        say(f'⚠ winget 失败：{e}')
        return None
    return find(dest)


def download(url: str, dst: Path, label: str) -> bool:
    say(f'⚑ 下载 {label}：{url}')
    t0, last = time.time(), -1
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'anim-pipeline-installer/1.0'})
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            got = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    pct = got * 100 // total
                    if pct // 5 > last // 5:          # ⚑ 每 5% 一行，别刷屏
                        last = pct
                        say(f'  {pct:3d}%  {got / 1048576:6.1f} / {total / 1048576:.1f} MB  {time.time() - t0:.0f}s')
        say(f'  ✅ 下载完成 {got / 1048576:.1f} MB')
        return True
    except Exception as e:
        say(f'  ⚠ 失败：{e}')
        return False


def extract(zp: Path, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    want = {f'ffmpeg{EXE}', f'ffprobe{EXE}'}
    got = set()
    with zipfile.ZipFile(zp) as z:
        for m in z.namelist():
            base = m.rsplit('/', 1)[-1]
            if base in want and '/bin/' in m:
                with z.open(m) as src, open(dest / base, 'wb') as out:
                    shutil.copyfileobj(src, out)
                got.add(base)
    say(f'  ⚑ 解压到 {dest}：{", ".join(sorted(got)) or "（zip 里没找到 bin/ffmpeg）"}')
    return got == want


def main():
    args = sys.argv[1:]
    check = '--check' in args
    dest = ROOT / 'tools' / 'bin'
    for a in args:
        if a.startswith('--dest='):
            dest = Path(a.split('=', 1)[1]).resolve()

    d = find(dest)
    if d:
        return 0 if verify(d) else 1
    if check:
        say('✗ 没找到 ffmpeg / ffprobe（PATH、tools/bin、winget 目录都没有）')
        return 1
    if os.name != 'nt':
        say('✗ 没找到 ffmpeg。macOS：brew install ffmpeg   Debian/Ubuntu：sudo apt install ffmpeg   然后重新运行')
        return 1

    d = try_winget(dest)
    if d:
        return 0 if verify(d) else 1

    tmp = dest.parent / '_ffmpeg_download.zip'
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES:
        if download(url, tmp, name) and extract(tmp, dest):
            tmp.unlink(missing_ok=True)
            d = find(dest)
            if d:
                return 0 if verify(d) else 1
        tmp.unlink(missing_ok=True)
    say('✗ 三个来源都没成。手动办法：到 https://www.gyan.dev/ffmpeg/builds/ 下 essentials zip，'
        f'把里面 bin/ 下的 ffmpeg.exe 和 ffprobe.exe 拷到 {dest}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
