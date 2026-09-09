# -*- coding: utf-8 -*-
"""⚑⚑ 把**满画幅立绘**做成**留白图**（技能出片的唯一合法输入）。

```bash
python tools/make_liubai.py _src/hero_q34_朝左_立绘.png _src/hero_q34_斜刀_留白.png
python tools/make_liubai.py <in> <out> --h=0.547 --cx=0.540 --foot=0.779 --size=1024
```

⚠⚠⚠ **为什么非做这一步不可**（HANDOFF §14.2 ①）——
  ⚑ 提示词里有一句「人物在画面中的大小自始至终与原图完全一致」，⚑ 这句删不得
    （⛔ 删了模型会推镜头）。⚠ 但拿**满画幅立绘**当输入时，⚑ 这句等于锁死「占满画面」
    ⇒ ⛔ 刀一展开必然出画，⚑ 留白句写多少遍都摁不住。
  ⚑ 实测（崩山击）：⚑ 四边全干净帧 **22/125 → 146/150**，⚑ 只换源图、提示词一个字没改。

⚑ 三个默认值是从 `_src/hero_q34_横刀_留白.png` 反量出来的（⚑ 那张是已验证的），
  ⚑ 换角色/换画幅时先量一遍已有的留白图，⛔ 别拍脑袋。

⚠⚠ **底色必须照抄这张立绘自己量到的**，⛔ 别用 #808080、⛔ 也别抄别的立绘的 ——
  ⚑ 22(朝左) 是 (143,142,144)，⚑ 25(横刀) 是 (153,152,152)，⚑ 两张就不一样。
  ⚑ 底色错了 ⇒ ⚠ vid2anim 抠灰底会连角色边缘一起吃掉／或留一圈灰边。
"""
import io
import os
import sys

import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')  # Windows 控制台默认 GBK

HERE = os.path.dirname(os.path.abspath(__file__))   # ⚑ 代码位置（⛔ 别拿它找数据）
# ⚑ 数据根 ＝ **当前工作目录**（⛔ 不是脚本位置）—— ⚑ cd 到你的项目再跑，产物就落在那儿。
#   ⚑ 和 artgen（ai-asset-gen）同一条约定。⚑ 要挪位置就设 ANIMPIPE_WORK / ANIMPIPE_OUT。
ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
WORK = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')  # ⚑ 中间产物：视频/联络表/gif
OUT = os.environ.get('ANIMPIPE_OUT') or os.path.join(ROOT, 'out', 'anim')     # ⚑ 成品：序列帧图集


def opt(flag: str, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def bg_color(im: Image.Image) -> tuple[int, int, int]:
    """⚑ 四角各取 20×20 的中位色。⛔ 别拿全图中位亮度反推 RGB（⚠ 那样丢色偏）。"""
    a = np.asarray(im)
    c = np.concatenate([a[:20, :20].reshape(-1, 3), a[:20, -20:].reshape(-1, 3),
                        a[-20:, :20].reshape(-1, 3), a[-20:, -20:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(c, axis=0))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    src = args[0] if os.path.isabs(args[0]) else os.path.join(ROOT, args[0])
    dst = args[1] if os.path.isabs(args[1]) else os.path.join(ROOT, args[1])
    size = opt('--size', 1024, int)
    tgt_h, cx_r, foot_r = opt('--h', 0.547, float), opt('--cx', 0.540, float), opt('--foot', 0.779, float)

    im = Image.open(src).convert('RGB')
    col = bg_color(im)
    a = np.asarray(im).astype(float)
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    m = np.abs(lum - np.median(lum)) > 16
    ys, xs = np.where(m)
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1

    s = (tgt_h * size) / bh
    nw, nh = max(1, round(bw * s)), max(1, round(bh * s))
    # ⚑ 连同周围一圈底色一起裁再缩：⚑ 底色是平的 ⇒ 拼上去看不出缝，
    #   ⛔ 别做硬阈值抠图 —— ⚠ 头发丝和刀刃的软边会被切秃。
    pad = int(min(bw, bh) * 0.06)
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad),
           min(im.size[0], xs.max() + 1 + pad), min(im.size[1], ys.max() + 1 + pad))
    crop = im.crop(box).resize((round((box[2] - box[0]) * s), round((box[3] - box[1]) * s)), Image.LANCZOS)

    out = Image.new('RGB', (size, size), col)
    # ⚑ 按**脚底**和**中心 x** 对位（⛔ 不是按 bbox 左上角）——
    #   ⚠ bbox 会被刀撑歪，⚑ 而脚底/中心才是 vid2anim 后面要用的两个基准。
    foot_y = round(foot_r * size)
    cx = round(cx_r * size)
    px = cx - round((xs.min() - box[0]) * s) - nw // 2
    py = foot_y - round((ys.max() - box[1]) * s)
    out.paste(crop, (px, py))
    out.save(dst)

    # ⚑ 自检：⚑ 出来的图必须和已验证的留白图**同一套比例**，⛔ 否则白出片
    b = np.asarray(out).astype(float)
    l2 = 0.299 * b[:, :, 0] + 0.587 * b[:, :, 1] + 0.114 * b[:, :, 2]
    m2 = np.abs(l2 - np.median(l2)) > 16
    y2, x2 = np.where(m2)
    edge = int(m2[0, :].sum() + m2[-1, :].sum() + m2[:, 0].sum() + m2[:, -1].sum())
    print(f'✅ {os.path.relpath(dst, ROOT)}  {size}x{size}  底色 {col}')
    print(f'   角色高 {y2.max() - y2.min()} ({(y2.max() - y2.min()) / size:.3f})  '
          f'中心x {((x2.min() + x2.max()) / 2) / size:.3f}  脚底y {y2.max() / size:.3f}')
    print(f'   贴边像素 {edge} ⇒ {"✅ 四边干净" if edge == 0 else "⚠⚠ 角色碰到画布边了，⛔ 调小 --h"}')


if __name__ == '__main__':
    main()
