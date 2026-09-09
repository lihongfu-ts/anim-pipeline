# -*- coding: utf-8 -*-
"""
中性灰底抠像 —— ⚑ 人物立绘专用（`out_guidao/` 这类）。

    python tools/cutout_grey.py <src.png> <dst.png> [--preview]

⚠⚠ **为什么不能用技能自带的 `alpha.py`**（2026-08-17 连踩两次）：

```
黑底 ＋ flood(thresh=28)   头发是黑的、和背景同色 → 整片头发被当背景抠掉，变白毛
黑底 ＋ flood(thresh=8)    头发保住了，但背景四角亮度 8.6 卡在阈值上泛洪进不去
                          → 残留一圈方块，棋子看着像「硬贴上去的」
灰底 ＋ flood(thresh=32)   四角干净了，⚠ 但 flood 出的是**二值** alpha，
                          发丝那种半透明过渡处理不了 → 发丝糊成白色
```

⚑ 解法：出图时背景钉死**纯中性灰 #808080**，然后
```
① floodfill 只用来圈出「哪些像素在人物外面」（外部 mask），⛔ 不拿它当 alpha
② 外部区域的 alpha ＝ 到灰底的**色度距离**做 smoothstep —— 发丝这种混合像素
   自然落在 0~255 中间，过渡是连续的
③ 人物内部一律 alpha=255 —— ⚠ 否则袍子上那些接近中灰的高光会被抠出洞
```
⚠ 灰底比黑底还有个额外好处：**出图时人物不会被背景吃掉轮廓**，
  AI 画暗色衣服时轮廓交代得更清楚。
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BG = (128, 128, 128)
# floodfill 圈外部用的容差。⚑ 给大点没关系 —— 它只定"内外"，不定 alpha
FLOOD_TH = 30
# 色度距离的软阈值：< LO 算全背景，> HI 算全前景，中间线性
LO, HI = 16, 52

"""
⚠⚠⚠ **底色⛔ 不许写死 128 —— 要从图里量**（2026-08-21 实见，查了一轮）。

prompt 里钉死了 `#808080`，但模型给的是"**差不多的**灰"：
```
06_q_jiumu    122 灰   距 128 是 10.3  → soft=0    ✓ 干净抠掉
09_q_danfang  115 灰   距 128 是 22.6  → soft=0.18 ⚠ **整片背景 alpha≈47**
```
⚑ 22.6 正好落在 `LO..HI`（16~52）中间 —— ⚠ 于是"背景"被当成"发丝那种混合像素"，
**整张图罩着一层半透明的灰纱**，⛔ 一行报错都没有：
截图上看只是"这张怎么有点脏"，⚑ 只有 `post_quiz_art.py` 那条半透明闸门（41.9%）逮得到。

⚠⚠ 而那条闸门当时给的建议是**错的**（「多半是衣服和灰底撞色，重出那一张」）——
⚑ 那位丹师穿的是深绛紫，一点都不撞。⚑ **重出一百次也不会好**，因为错的是这儿的常数。
⚑ 判据一句话：**凡是"AI 会照着画"的数，都不许当常数信** —— 量出来的才算。
"""


def measure_bg(a, out, fallback=BG):
    """
    量真正的底色 —— ⚑ 只在**画布最外那一圈**里量，⚠ 而且要和 `out` 取交集。

    ⚠⚠ ⛔ **别拿整个 `out` 去求中位数**（2026-08-21 我第一版就是这么写的，当场翻车）——
    `outside_mask` 的泛洪**会漏进人物里**（`FLOOD_TH=30` 是故意给大的，它只管定内外、
    不管定 alpha）。漏了之后 `out` 里一多半是袍子，中位数量出来是
    **`[53,27,24]` 那种深绛紫** —— ⚑ 于是"底色"变成了衣服色，整张图当场废掉。
    ⚠ 而这个错**在老代码里是隐形的**：`soft` 对着写死的 128 算，袍子离 128 很远、
      照样判成前景，⛔ 看不出泛洪漏过。

    ⚑ 最外一圈**一定是背景**（立绘都是居中的、四周留着底），⚑ 中位数再抗一次边缘混合。
    """
    h, w = a.shape[:2]
    b = max(8, int(min(h, w) * 0.02))
    ring = np.zeros((h, w), bool)
    ring[:b, :] = ring[-b:, :] = True
    ring[:, :b] = ring[:, -b:] = True
    # ⚑ 和 out 取交集：⚠ 人物贴到画布边（腰部立绘常常贴底）时把那一段剔掉
    sel = ring & out
    if sel.sum() < 500:
        sel = ring
    if sel.sum() < 500:
        return np.array(fallback, np.float32)
    return np.median(a[sel], axis=0).astype(np.float32)


def outside_mask(path, th=FLOOD_TH):
    """① 从四边泛洪，圈出人物外面那片"""
    im = Image.open(path).convert('RGB')
    W, H = im.size
    KEY = (255, 0, 255)
    seeds = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1),
             (W // 2, 0), (W // 2, H - 1), (0, H // 2), (W - 1, H // 2)]
    for s in seeds:
        try:
            ImageDraw.floodfill(im, s, KEY, thresh=th)
        except Exception:
            pass
    a = np.array(im)
    return (a[..., 0] == 255) & (a[..., 1] == 0) & (a[..., 2] == 255)


def cutout(path, bg=None, lo=LO, hi=HI, quiet=False):
    """
    ⚑ `bg=None` ＝ **从图里量**（见上面那段）。⚠ 传死值只在"确知底色"时用。
    """
    src = Image.open(path).convert('RGB')
    a = np.array(src).astype(np.float32)
    out = outside_mask(path)
    if bg is None:
        bg = measure_bg(a, out)
        off = float(np.sqrt(((bg - np.array(BG, np.float32)) ** 2).sum()))
        # ⚑ 自检：偏了多少要说出来 —— ⚠ 偏差落在 LO..HI 之间正是那个"半透明灰纱"的病灶
        if not quiet and off > 6:
            print('  ⚑ 量到底色 %s（离 #808080 %.1f）—— ⚑ 按量到的算，⛔ 不按 128'
                  % (np.round(bg, 1), off))
    bg = np.array(bg, np.float32)
    # ② 到**真实底色**的色度距离
    d = np.sqrt(((a - bg) ** 2).sum(axis=2))
    soft = np.clip((d - lo) / (hi - lo), 0, 1)
    # ③ 内部强制不透明，外部走 soft
    alpha = np.where(out, soft, 1.0)
    # ⚠ 极轻的羽化：只抹掉泛洪边界的锯齿，⛔ 别糊，糊了发丝就没了
    alpha = np.array(Image.fromarray((alpha * 255).astype(np.uint8))
                     .filter(ImageFilter.GaussianBlur(0.6)))

    # ④ ⚠⚠ **去底色（color unmixing）** —— 少这一步就是「战斗棋子上头发变白」
    #    半透明像素的实际颜色 C ＝ α·前景 ＋ (1-α)·灰底，⚑ 直接存 C 等于让每根发丝
    #    都掺着 128 的灰；叠上后面的 gamma 提亮，灰被推成白 → 一头白发。
    #    ⚑ 反解：F ＝ (C − (1−α)·B) ∕ α
    #    ⚠ α 太小时分母炸，钳到 0.25 —— 那些像素本来就几乎全透明，偏一点看不出来
    a3 = (alpha[..., None] / 255.0)
    F = (a - (1 - a3) * np.array(bg, np.float32)) / np.maximum(a3, 0.25)
    F = np.clip(F, 0, 255)
    return Image.fromarray(np.dstack([F.astype(np.uint8), alpha]), 'RGBA')


def _opt(flag, dflt):
    """⚑ `--lo=30` 这种取一个数（⚠ 不给就用默认）"""
    for x in sys.argv[1:]:
        if x.startswith(flag + '='):
            return float(x.split('=', 1)[1])
    return dflt


if __name__ == '__main__':
    args = [x for x in sys.argv[1:] if not x.startswith('--')]
    src, dst = args[0], args[1]
    """
    ⚑ `--lo=` / `--hi=` 那道软阈值能从命令行调（2026-08-23 加的，⚑ 纯加法，⚠ 不给就是默认）。
    ⚑ 用得着它的场合：底色**带暗角**时四角落进 `LO..HI` 那段斜坡 → 残一层半透明的灰
      （⚠ 浅底上看不出来，⛔ 而这工程的面板全是近黑的）。⚑ 抬 `lo` ⛔ 不会吃掉头发：
      ⚑ 暗发离灰底的色度距离在 100 上下，而 `lo` 撑死三四十。
    """
    im = cutout(src, lo=_opt('--lo', LO), hi=_opt('--hi', HI))
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    im.save(dst)
    arr = np.array(im)
    """
    ⚠⚠⚠ **四角要逐个报，⛔ 别报平均**（2026-08-23 实见，⚑ 白查了一轮）。

    ⚑ 大师兄那三张是**半身像**：⚠ 衣服本来就**被画面下沿裁着**（那是构图，⛔ 不是没抠干净）——
      ⚑ 于是"左下/右下"天生是 255，⚑ 平均一下报出 `四角alpha 63.8`，
      ⚠ 看着像**没抠干净**，⛔ 而图完全是对的。
    ⚑ 判据：**上面两角**必须 ≈0（⚑ 那儿一定是背景）；下面两角贴着人是正常的。
    """
    cor = [arr[0:8, 0:8, 3].mean(), arr[0:8, -8:, 3].mean(),
           arr[-8:, 0:8, 3].mean(), arr[-8:, -8:, 3].mean()]
    soft_px = ((arr[..., 3] > 12) & (arr[..., 3] < 243)).mean() * 100
    print('%s → %s  四角alpha 上%.0f/%.0f 下%.0f/%.0f  半透明像素 %.1f%%（发丝落在这里）'
          % (src, dst, cor[0], cor[1], cor[2], cor[3], soft_px))
    if max(cor[0], cor[1]) > 8:
        print('  ⚠ **上面两角没抠干净** —— ⚑ 近黑面板上那就是一圈灰方块，⚑ 试 --lo=32 --hi=64')
    if '--preview' in sys.argv:
        for name, col in [('light', (208, 212, 218)), ('dark', (18, 16, 20))]:
            b = Image.new('RGB', im.size, col)
            b.paste(im, (0, 0), im)
            b.save(dst.replace('.png', f'_on_{name}.png'))
