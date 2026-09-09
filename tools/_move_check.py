# -*- coding: utf-8 -*-
"""⚑⚑ 逐帧量「人往哪儿走了 / 脚离没离地」—— ⚑ 后跳、崩山击这类**带位移的动作**专用。

```bash
python tools/_move_check.py work/anim/back1_houtiao_ark.mp4
python tools/_move_check.py <mp4> --every=4          # ⚑ 每 4 帧打一行
```

⚑⚑ 它回答的是 §15.5 那条判据要的那个数：
   **「代码位移只能补动画里已经有的位移，⛔ 不能凭空造」** ⇒ ⚑ 那"已经有的位移"到底是多少像素？
   ⚠ 肉眼看联络表**量不出来**：⚑ 每格都是整幅画面、⛔ 人在格子里的绝对位置差几十像素看不出来，
   ⚠ 而且模型还会**缓慢缩放**（vid2anim 文件头③：5.1 秒长高 12%）⇒ ⛔ 像素数直接比也不对。
   ⇒ ✅ 一律输出**占身高的百分比**，⚑ 再换算成 target=202 的游戏像素。

⚑ 三个量怎么取（⛔ 全部沿用 vid2anim 的既有约定，⛔ 别另起一套）：
```
脚底 y   ⚑ 中间 40% 列的最低不透明行     ⚠ ⛔ 不是内容盒底部（刀尖会比脚低）
躯干 x   ⚑ 下 55% 的 x **中位数**        ⚠ ⛔ 不是内容盒中心（刀甩出去会把中心拽走）
                                        ⚠ ⛔ 也不是头部重心（长发一飘就漂，见 foot_anchor）
身高     ⚑ 中间 40% 列的 top→脚底        ⚑ 当尺子用：⚑ 所有位移都除以它
```
⚠⚠ **腾空判据⛔ 不能只看脚底 y** —— ⚑ 人往画面上方飘一点也会让脚底 y 变小。
  ⇒ ⚑ 同时印**身高**：⚑ 真腾空时身体蜷缩、身高**变短**（⚑ 正是 --norm=off 存在的原因）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

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
sys.path.insert(0, os.path.join(HERE, 'artgen'))
from cutout_grey import cutout  # noqa: E402

TORSO = (0.30, 0.70)     # ⚑ 同 vid2anim：找脚底只看中间这几列
TARGET = 202             # ⚑ 同 vid2anim：格内目标身高 ⇒ ⚑ 百分比换成游戏像素的换算尺


def opt(flag, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def body_cols(a):
    """⚑⚑ 人在哪几列 —— ⚑ **逐帧滑窗找**，⛔ 不能像 vid2anim 那样写死「中间 40% 列」。

    ⚠⚠⚠ 2026-09-01 在 back3b（⚑ 尾帧钉到 cx=0.80）上栽的：
      ⚑ vid2anim 的 TORSO=(0.30,0.70) 成立的前提是**人一直在画面中央**（⚑ 它切走路/待机/原地技能）。
      ⚠ 而这份工具专量**带位移**的动作 ⇒ ⚑ 人退到画面 0.80 时**整个身体在那 40% 列之外**，
        ⛔ 量到的 top/foot 变成刀尖或衣摆残片 ⇒ ⚠ 打出来「身高 49px」，⚑ 而画面里的人好好的。
      ⚠⚠ 后果是**静默的**：⚑ 一行报错都没有，⛔ 而你正要拿这个数去定 applyLunge。
    ⇒ ✅ 滑窗找**最密的那 40% 列**：⚑ 头＋躯干＋腿的像素密度远高于刀身和衣摆尖 ⇒ ⚑ 窗口自己粘住人。
    """
    W = a.shape[1]
    w = max(1, int(W * (TORSO[1] - TORSO[0])))
    s = np.concatenate([[0], np.cumsum((a > 200).sum(axis=0).astype(np.int64))])
    i = int((s[w:] - s[:-w]).argmax())
    return i, i + w


def measure(path):
    """⚑ 一帧 → (脚底 y, 躯干中位 x, 身高)。⚑ 量不到人就返回 None（⛔ 别 crash，⚑ 空帧照样打行）"""
    # ⚑ cutout 收的是**路径**⛔ 不是 Image（⚑ 它内部还要 outside_mask 重开一次图）
    a = np.asarray(cutout(path, quiet=True))[:, :, 3]
    H, W = a.shape
    x0, x1 = body_cols(a)
    # ⚠ a>200 ⛔ 不是 a>16：⚑ 万相/seedance 的背景噪点软阈值后 alpha≈43，⚑ 实体才是 255
    #   （⚑ 血泪见 vid2anim.rows_with_body 的注释）
    ys = np.where((a[:, x0:x1] > 200).any(axis=1))[0]
    if not len(ys):
        return None
    top, foot = int(ys.min()), int(ys.max())
    lo = top + int((foot - top) * 0.45)                      # ⚑ 下 55% ＝ 躯干＋腿，⛔ 不含头发
    bx = np.where(a[lo:foot + 1] > 200)[1]
    return foot, (int(np.median(bx)) if len(bx) else W // 2), foot - top


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    mp4 = args[0] if os.path.isabs(args[0]) else os.path.join(ROOT, args[0])
    need_ffmpeg()
    need_file(mp4, '视频', 'gen_video.py 出的片默认落在 work/anim/')
    every = opt('--every', 4, int)
    tmp = tempfile.mkdtemp(prefix='movechk_')
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, os.path.join(tmp, 'f%04d.png')], check=True)
        fs = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
        # ⚑⚑ 只量采样帧 —— ⚠ 原来每帧都抠图，⚑ 161 帧的真实视频跑了 371s（⚑ cutout 每帧 ~2s），
        #   ⛔ 而 --every 只管打印不管计算。⚑ 首帧必量（⚑ 基准），⚑ 其余按 every 取。
        m = [measure(f) if i % every == 0 else None for i, f in enumerate(fs)]
        base = next(v for v in m if v)                       # ⚑ 首帧当基准（⚑ 首尾帧钉死时它就是站姿）
        bf, bx, bh = base
        print(f'⚑ {os.path.basename(mp4)}  共 {len(fs)} 帧   基准(f001)：脚底 y={bf} 躯干 x={bx} 身高={bh}px')
        print(f'⚑ Δx 正数＝向画面**右**（后跳方向）  抬升 正数＝脚离地  ⚑ 括号内＝换算到 target={TARGET} 的游戏像素')
        print(f"{'帧':>5} {'Δx':>7} {'抬升':>7} {'身高':>7} {'Δx(游戏px)':>11} {'抬升(游戏px)':>12}")
        rows = []
        for i, v in enumerate(m, 1):
            if not v or (i - 1) % every:
                continue
            foot, x, h = v
            k = TARGET / bh                                  # ⚑ 视频像素 → 游戏像素
            rows.append((i, x - bx, bf - foot, h))
            print(f'{i:>5} {x - bx:>7} {bf - foot:>7} {h:>7} {(x - bx) * k:>11.1f} {(bf - foot) * k:>12.1f}')
        k = TARGET / bh
        dxs = [r[1] for r in rows]
        lifts = [r[2] for r in rows]
        hmin = min(r[3] for r in rows)
        print(f'\n⚑⚑ 结论')
        print(f'  最右 Δx  {max(dxs):+5d}px = 身高的 {max(dxs)/bh*100:5.1f}%  ⇒ 游戏 {max(dxs)*k:+6.1f}px'
              f'   (f{rows[dxs.index(max(dxs))][0]:03d})')
        print(f'  最左 Δx  {min(dxs):+5d}px = 身高的 {min(dxs)/bh*100:5.1f}%  ⇒ 游戏 {min(dxs)*k:+6.1f}px'
              f'   (f{rows[dxs.index(min(dxs))][0]:03d})')
        print(f'  最大抬升 {max(lifts):+5d}px = 身高的 {max(lifts)/bh*100:5.1f}%  ⇒ 游戏 {max(lifts)*k:+6.1f}px'
              f'   (f{rows[lifts.index(max(lifts))][0]:03d})')
        print(f'  身高范围 {hmin}~{max(r[3] for r in rows)}px（⚑ 真腾空时身体蜷缩 ⇒ 身高应明显变短）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
