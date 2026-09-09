# -*- coding: utf-8 -*-
"""⚑ **联络表** —— ⚑ 把一条视频摊成一张 N 格的图，⚑ 用来**肉眼看整段动作**。

```bash
python tools/_contact.py work/anim/atk1_hengzhan_glm.mp4            # ⚑ 默认 12 格 / 4 列
python tools/_contact.py <mp4> --n=16 --cols=4 --out=<png>
```

⚠⚠ 这份**⛔ 不是** `vid2anim.py` 的替代品。⚑ 两者干的是完全不同的两件事：
```
_contact.py   ⚑ 原样等距采样 ＋ **烧帧号** ⇒ ⚑ 给人看的，⚑ 用来决定「挑哪几帧」
vid2anim.py   ⚑ 抠底 ＋ 归一 ＋ 对脚 ＋ 切格子  ⇒ ⚑ 给游戏用的，⚑ 需要先知道帧号
```
⚑⚑ 帧号必须烧进图里 —— ⚠ 否则看完联络表还得回头数格子换算成 `--at=` 的帧号，
  ⛔ 数错一格切出来就是另一个姿势（⚑ 而且切完才发现）。

⚑ ⛔ 不抠底、不归一化：⚑ 联络表要回答的是「刀有没有出画」「有没有细成一条线」，
  ⚠ 而这两件事一旦抠过底/缩放过就**看不准了**（⚑ 出画的部分已经被裁掉）。
"""
import os
import subprocess
import sys

from PIL import Image, ImageDraw

try:                                                # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # ⚑ 幂等 ⇒ ⚑ 被 import 也安全
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))   # ⚑ 代码位置（⛔ 别拿它找数据）
# ⚑ 数据根 ＝ **当前工作目录**（⛔ 不是脚本位置）—— ⚑ cd 到你的项目再跑，产物就落在那儿。
#   ⚑ 和 artgen（ai-asset-gen）同一条约定。⚑ 要挪位置就设 ANIMPIPE_WORK / ANIMPIPE_OUT。
ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
WORK = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')  # ⚑ 中间产物：视频/联络表/gif
OUT = os.environ.get('ANIMPIPE_OUT') or os.path.join(ROOT, 'out', 'anim')     # ⚑ 成品：序列帧图集
sys.path.insert(0, HERE)
from _env import need_ffmpeg, need_file  # noqa: E402


def opt(flag: str, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    mp4 = args[0] if os.path.isabs(args[0]) else os.path.join(ROOT, args[0])
    need_ffmpeg()
    need_file(mp4, '视频', 'gen_video.py 出的片默认落在 work/anim/')
    n, cols = opt('--n', 12, int), opt('--cols', 4, int)
    tag = os.path.splitext(os.path.basename(mp4))[0]
    out = opt('--out', os.path.join(WORK, f'{tag}_联络表.png'))

    tmp = os.path.join(WORK, '_tmp_contact_' + tag)
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mp4,
                    os.path.join(tmp, 'f%04d.png')], check=True)
    fs = sorted(f for f in os.listdir(tmp) if f.endswith('.png'))
    if not fs:
        print(f'✗ 一帧都没拆出来：{mp4}')
        sys.exit(1)

    # ⚑ 等距取 n 帧（⚑ 含首尾）—— ⚠ 首尾是**连招衔接**要看的两帧，⛔ 不能漏
    idx = [round(i * (len(fs) - 1) / max(n - 1, 1)) for i in range(min(n, len(fs)))]
    cells = []
    for i in idx:
        im = Image.open(os.path.join(tmp, fs[i])).convert('RGB')
        d = ImageDraw.Draw(im)
        # ⚑ 帧号是 **ffmpeg 的 1-based 序号**，⚑ 和 vid2anim 的 --at= 用同一套编号
        t = f'f{i + 1:03d}'
        d.rectangle([0, 0, 92, 34], fill=(0, 0, 0))
        d.text((8, 8), t, fill=(255, 230, 60))
        cells.append(im)

    w, h = cells[0].size
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new('RGB', (w * min(cols, len(cells)), h * rows), (24, 24, 24))
    for i, c in enumerate(cells):
        sheet.paste(c, (i % cols * w, i // cols * h))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out)

    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print(f'✅ {os.path.relpath(out, ROOT)}  {sheet.size[0]}x{sheet.size[1]}  '
          f'{os.path.getsize(out) // 1024} KB   共 {len(fs)} 帧，取 {len(cells)} 格')


if __name__ == '__main__':
    main()
