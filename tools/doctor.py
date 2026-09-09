# -*- coding: utf-8 -*-
"""⚑⚑⚑ **一条命令回答「我现在能做什么」** —— ⚑ 环境 ＋ 凭据 ＋ 路径，⚑ 缺什么、怎么补。

```bash
python tools/doctor.py
```

⚑ 为什么要有它：⚑ 这条链的失败大多是**静默**的（⚑ 缺 ffmpeg 报一段 traceback、
  ⚑ 没配万相要到出完片才知道循环闭不上、⚑ 基准素材不在只会少一行输出）。
⚑ 把这些**在动手之前**一次问完，⛔ 别让人跑到一半再回头装东西。
"""
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
WORK = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')
OUT = os.environ.get('ANIMPIPE_OUT') or os.path.join(ROOT, 'out', 'anim')
REF = os.environ.get('ANIMPIPE_REF') or os.path.join(
    ROOT, '_ref', 'Slash-The-Hordes', 'assets', 'Media', 'Images', 'Game', 'Player')

OK, NO, WARN = '✅', '⛔', '⚠ '


def section(t):
    print(f'\n── {t} ' + '─' * max(0, 50 - len(t)))


def ver(cmd):
    try:
        out = subprocess.run([cmd, '-version'], capture_output=True, text=True, timeout=10).stdout
        return out.splitlines()[0].split(' Copyright')[0] if out else cmd
    except Exception:
        return cmd


def main():
    print(f'⚑ anim-pipeline doctor    {platform.system()} · Python {platform.python_version()}')
    print(f'⚑ 工作目录 {ROOT}')

    blocking = []          # ⚑ 缺了就**什么都跑不了**
    limits = []            # ⚑ 缺了只是某一步做不了

    section('① 环境（⚑ 不装跑不了）')
    for mod, pkg in (('numpy', 'numpy'), ('PIL', 'Pillow')):
        try:
            m = __import__(mod)
            print(f'  {OK} {pkg:<10} {getattr(m, "__version__", "")}')
        except ImportError:
            print(f'  {NO} {pkg:<10} 未安装        → pip install {pkg}')
            blocking.append(pkg)
    for cmd in ('ffmpeg', 'ffprobe'):
        p = shutil.which(cmd)
        if p:
            print(f'  {OK} {cmd:<10} {ver(cmd)}')
        else:
            print(f'  {NO} {cmd:<10} 不在 PATH     → winget install Gyan.FFmpeg / brew install ffmpeg')
            blocking.append(cmd)

    section('② 凭据（⚑ 决定能出哪一步）')
    try:
        import _creds
        have, can = _creds.probe()
        for n in ('relay', 'wan', 'ark', 'glm', 'minimax'):
            c = have.get(n)
            print(f'  {OK if c else "— "} {n:<9}{("来自 " + c["_from"]) if c else "未配置"}')
        print()
        for cap, (need, opt, desc) in _creds.CAPS.items():
            ok = can[cap]
            miss = [n for n in need if not have.get(n)] or ([] if ok else opt)
            print(f'  {OK if ok else NO} {desc}' + ('' if ok else f'   ← 缺 {"/".join(miss)}'))
            if not ok and cap in ('video', 'portrait'):
                limits.append(cap)
    except Exception as e:
        print(f'  {WARN} 读不到 _creds.py：{e}')

    section('③ 路径（⚑ 数据根 = 当前工作目录）')
    print(f'  中间产物  {WORK}   {"(已有)" if os.path.isdir(WORK) else "(还没建，跑一次就有)"}')
    print(f'  成品图集  {OUT}   {"(已有)" if os.path.isdir(OUT) else "(还没建，跑一次就有)"}')
    ref_ok = os.path.isdir(REF)
    print(f'  {OK if ref_ok else WARN} 基准素材  {REF}')
    if not ref_ok:
        print('      ⚑ anim_bench 只能出你自己的数、⛔ 给不出合格区间。'
              '⚑ 准备方法见 README「三条最容易栽的」①')

    section('④ 下一步')
    if blocking:
        print(f'  ⛔ 先装：{", ".join(blocking)}。⚑ 装完**重开终端**再跑一次 doctor。')
        sys.exit(1)
    demo_done = os.path.exists(os.path.join(OUT, 'demo_walk.png'))
    if not demo_done:
        print('  ⚑ 环境是通的。⚑ 先跑零成本 demo 看看产物长什么样（⛔ 不用任何 key）：')
        print('      python tools/demo.py')
    if 'video' in limits:
        print('  ⚑ 要出自己的片，⚠ **只有万相能钉首尾帧**（⚑ 循环动画的地基）—— ⚑ 先配它：')
        print('      python tools/_creds.py --set=wan')
        print('      ⚠ baseUrl 必须是「独立业务空间」专属域名（⚑ ws-xxxx.cn-beijing.maas.aliyuncs.com），'
              '⛔ 公共域名恒 401')
    if 'portrait' in limits:
        print('  ⚑ 要从零出立绘要配中转站（relay）；⚑ 已有立绘的话这步可以跳过')
    if demo_done and not limits:
        print(f'  {OK} 全部就绪。⚑ 拿你的立绘走一遍：make_liubai → gen_video → _contact → vid2anim → anim_bench')
    print('\n⚑ 每一步为什么这么做、坑在哪：《AI角色动画管线.md》')


if __name__ == '__main__':
    main()
