# -*- coding: utf-8 -*-
"""⚑⚑ **刀有没有变成一条细线** —— ⚑ 逐帧量，⛔ 不再只靠肉眼。

```bash
python tools/_blade_check.py work/anim/atk3_liaopi_wan1.mp4
python tools/_blade_check.py work/anim/*.mp4 --thr=0.45
```

⚠⚠⚠ HANDOFF §13.8 / §14.2④ 一直写着这条「⛔ 指标看不出来，只能肉眼」——
  ⚑ 那是因为之前量的是**整帧**的前景面积：⚠ 角色身体占了大头，⛔ 刀变细那点变化被淹没了。
  ⇒ ✅ 换个量法就看得出来了：⚑ 只数**离身体中线较远**的前景像素（≈ 刀身），
    ⚑ 刃口一转向镜头，⚑ 这块面积会掉到中位数的一半以下。

⚑ 实测（2026-09-01，万相，各 150 帧）：
```
① 横斩          过细帧 0 个
② 回斩          f052-f055（⚑ 回扫经过身体正前方那一下）
③ 撩劈          f050-f052（⚑ 撩到顶点的过渡段）
崩山击(22,480P) f049（⚑ 举刀转身那一瞬）
⚑ 全部都是**过渡帧**，⚑ 挑帧避开即可 ⇒ ⛔ 不是废片。
```
⚠⚠ **报出来⛔ 不等于废片** —— ⚑ 快速挥砍时刃口短暂转向镜头是正常的，
  ⚑ 这工具的用途是「⚑ 告诉你哪几帧别挑」，⛔ 不是「这发不能用」。

⛔ 这份**不判好坏**，⚑ 只告诉你「哪几帧别挑」。⚠ 出画/贴边是另一件事，⚑ 那个 vid2anim 自检里有。
"""
import os
import subprocess
import sys

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


def opt(flag: str, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def runs(idx: list[int]) -> str:
    """⚑ 把 [48,49,50,77] 印成 `f048-f050  f077` —— ⚑ 连续段比一串数字好读"""
    if not idx:
        return 'none'
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:] + [1 << 30]:
        if i != p + 1:
            out.append(f'f{s:03d}' if s == p else f'f{s:03d}-f{p:03d}')
            s = i
        p = i
    return '  '.join(out)


def check(mp4: str, thr: float, half: int) -> None:
    tmp = os.path.join(WORK, '_blade_' + os.path.basename(mp4).split('.')[0])
    os.makedirs(tmp, exist_ok=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mp4,
                    os.path.join(tmp, 'f%04d.png')], check=True)

    # ⚑ 第一趟：⚑ 只抠前景，⚑ 顺便收集每帧的身高/中心 —— ⚑ half 要等全片看完才能定
    masks, cxs, hs = [], [], []
    for fn in sorted(os.listdir(tmp)):
        a = np.asarray(Image.open(os.path.join(tmp, fn)).convert('RGB')).astype(float)
        lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
        # ⚑ 底色是干净的中性灰 ⇒ 中位数就是底色，⚑ 偏离它的即前景
        m = np.abs(lum - np.median(lum)) > 16
        ys, xs = np.where(m)
        masks.append(m)
        cxs.append((xs.min() + xs.max()) // 2 if len(xs) else m.shape[1] // 2)
        hs.append(ys.max() - ys.min() + 1 if len(ys) else 0)

    # ⚠⚠⚠ **half ⛔ 不能写死，⚑ 也⛔ 不能逐帧算** —— 2026-09-01 两个版本都栽了：
    #   ⛔ 写死 90px（照 960×960、角色站直 525px 定的）
    #     ⇒ ⚠ 换 480P（632×632）后 ±90 把**整个角色连刀**都圈成"身体"，
    #       ⚑ 刀面积恒为 0 ⇒ 报「整帧无前景」，⚠ 差点把一发好片当废片扔。
    #   ⛔ 逐帧按本帧身高算 ⇒ ⚠ 蓄力/落地时角色**蹲下身高变小** ⇒ half 跟着变小
    #     ⇒ ⚑ 更多身体被算成刀 ⇒ ⚠ 面积虚高，⛔ 给相邻帧的基准注入噪声。
    #   ⇒ ✅ 用**全片身高中位数**算一次，全片同一个 half。⚑ 系数 0.17 ＝ 90/525（实测反推）。
    hnz = [h for h in hs if h > 0]
    hf = half if half > 0 else max(8, int(float(np.median(hnz)) * 0.17)) if hnz else 8

    areas = np.array([0 if h == 0 else
                      int(m[:, :max(cx - hf, 0)].sum() + m[:, cx + hf:].sum())
                      for m, cx, h in zip(masks, cxs, hs)], dtype=float)

    # ⚠⚠⚠ 基准必须是**邻近帧**，⛔ 不能用全片中位数（2026-09-01 第一版就是这么误报的）——
    #   ⚑ 全片中位数由「刀伸展」的帧主导，⚠ 而**横刀站姿**时刀横在身前、贴近中线，
    #     ⚑ 面积本来就低 ⇒ ⛔ 首尾几十帧静止姿势会被全部报成「刀不见了」。
    #     ⚑ 实测 ② 回斩被误报 f001-f023 ＋ f121-f150，⚑ 那是**正常的起手和收招**。
    #   ⇒ ✅ 改成和 ±win 帧的**局部中位**比：
    #     ⚑ 突然掉下去 ＝ 刃口转向镜头（真故障）；⚑ 一直低 ＝ 静止姿势（正常）。
    win = 8
    bad = []
    for i, v in enumerate(areas):
        lo, hi = max(0, i - win), min(len(areas), i + win + 1)
        loc = float(np.median(areas[lo:hi]))
        if loc > 0 and v < loc * thr:
            bad.append(i + 1)
    gone = [i + 1 for i, v in enumerate(areas) if v == 0]

    tag = os.path.basename(mp4)
    mark = '✅' if not bad else '⚠⚠'
    print(f'{mark} {tag:<28} {len(areas)} 帧 · 刀面积中位 {np.median(areas[areas > 0]):.0f}px')
    print(f'     过细/消失（比邻近 ±{win} 帧低 {1 - thr:.0%} 以上）: {runs(bad)}')
    if gone:
        print(f'     ⛔ **整帧无前景**: {runs(gone)}')

    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    thr = opt('--thr', 0.45, float)
    # ⚑ 身体半宽。⚑ 默认 0 ＝ **按全片角色身高中位数自动推**（⚑ 理由见 check() 里那段）。
    #   ⚑ 只有在角色比例特殊时才手动给。
    half = opt('--half', 0, int)
    need_ffmpeg()
    for p in args:
        q = p if os.path.isabs(p) else os.path.join(ROOT, p)
        need_file(q, '视频')
        check(q, thr, half)


if __name__ == '__main__':
    main()
