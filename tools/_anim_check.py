# -*- coding: utf-8 -*-
"""⚑⚑⚑ **动作图集自检** —— ⚑ 出一个动作跑一遍，⛔ 别再靠肉眼。

```bash
# 一段 = <图集名>:<格宽>:<footTrim>:<命中格号(1起)>
python tools/_anim_check.py hero_atk1:348:14:2 hero_atk2:392:16:2 hero_atk3:294:7:4
python tools/_anim_check.py hero_bengshan:300:16:4 --facing=left --iou=0.70
```

## ⚠⚠⚠ 为什么要有这份（⚑ 2026-09-01 普攻返工两轮的账）

```
⛔ 「偶尔出现刀向后的一帧」 —— ⚑ 命中帧的刀指向了角色**背后**
   ⚠⚠ 我判错过一次：⚑ 拿「离躯干中线最远的前景点」在**原始视频**上量，
     ⛔ 那会把一层很淡的运动模糊也算成刀 ⇒ ⚑ 方向判反。
   ⇒ ✅ **只在切出来的图集上量**（⚑ 抠底已经去掉那层模糊）—— ⚑ 这份就是这么做的。
⛔ 「我怎么就看到挥刀和砸下去」 —— ⚑ ①横斩 和 ②回斩 剪影 IoU 0.748 ＝ **同一个动作**
   ⇒ ✅ 逐对量命中帧 IoU，⚑ 超阈值就报。
⛔ 「偶尔人物变高又回来」 —— ⚑ 图集脚底和招式表里的 footTrim 对不上
   ⇒ ✅ 直接比。
```

⚑ 判据（⛔ 都不是"好不好看"，⚑ 是"会不会穿帮"）：
```
① 命中帧的武器**必须指向角色面朝的方向**；⚑ 蓄力帧允许朝后（⚑ 举刀/后拉，⛔ 不出戏）
② 任意两段的**命中帧剪影 IoU** 要低于阈值（⚑ 默认 0.70）—— ⚑ 否则玩家看成同一招
③ 图集实测 footpad ＝ 招式表里的 footTrim（⚑ 差 >2px 就报）
④ 每格**脚底 y 必须一致**（⚑ 差 >3px 会看成"人在抖"）
⑤ 四边**不许贴边**（⚑ 内容被格子切了）
```
⛔ 这份**不查**「刀有没有变成细线」和「实际位移多少」—— ⚑ 那是 `_blade_check.py` 和 `_move_check.py`
  的活（⚑ 它们量的是**视频**，这份量的是**图集**）。
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
ANIM = OUT

# ⚑ 找脚底/躯干时只看中间这几列 —— ⚑ 和 vid2anim 的 TORSO 保持一致，⛔ 别各用各的
TORSO = (0.30, 0.70)


def opt(flag, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def cells(path, cw):
    im = Image.open(path).convert('RGBA')
    n = im.size[0] // cw
    return [np.asarray(im.crop((i * cw, 0, (i + 1) * cw, im.size[1]))) for i in range(n)], im.size


def measure(a, cw):
    """⚑ 一格的四个数：脚底 y · 躯干中线 x · 武器尖偏移（负＝画面左）· 贴边像素"""
    m = a[:, :, 3] > 200
    ys, xs = np.where(m)
    if len(ys) == 0:
        return None
    c0, c1 = int(cw * TORSO[0]), int(cw * TORSO[1])
    yy, _ = np.where(m[:, c0:c1])
    foot, top = int(yy.max()), int(yy.min())
    # ⚑ 躯干中线用**下 55% 的 x 中位数** —— ⚑ 和 vid2anim 的 --alignx=torso 同一套：
    #   ⛔ 别用 bbox 中心（⚠ 会被伸出去的武器拽歪），⛔ 也别用均值（⚠ 会被飘起的长发拽歪）。
    lo = top + int((foot - top) * 0.45)
    _, bx = np.where(m[lo:foot + 1])
    cx = int(np.median(bx)) if len(bx) else cw // 2
    dl, dr = cx - int(xs.min()), int(xs.max()) - cx
    tip = -dl if dl > dr else dr          # ⚑ 负＝画面左，正＝画面右
    edge = int(m[0, :].sum() + m[-1, :].sum() + m[:, 0].sum() + m[:, -1].sum())
    return dict(foot=foot, cx=cx, tip=tip, edge=edge, h=foot - top, mask=m, bbox=(xs.min(), ys.min(), xs.max(), ys.max()))


def silhouette(a):
    """⚑ 裁到内容盒的 alpha 掩码 —— ⚑ 比 IoU 时先归一到同尺寸，⛔ 别带 padding 一起比"""
    m = a[:, :, 3] > 200
    ys, xs = np.where(m)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def iou(A, B):
    h, w = min(A.shape[0], B.shape[0]), min(A.shape[1], B.shape[1])
    a = np.array(Image.fromarray(A.astype(np.uint8) * 255).resize((w, h))) > 127
    b = np.array(Image.fromarray(B.astype(np.uint8) * 255).resize((w, h))) > 127
    return float((a & b).sum()) / max(int((a | b).sum()), 1)


def main() -> None:
    specs = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not specs:
        print(__doc__)
        sys.exit(1)
    facing = opt('--facing', 'left')          # ⚑ 角色面朝哪边 ⇒ 命中帧的武器该指哪边
    thr = opt('--iou', 0.70, float)
    want = -1 if facing == 'left' else 1      # ⚑ 面朝左 ⇒ tip 应为负

    bad = 0
    segs = []
    for sp in specs:
        p = sp.split(':')
        name, cw = p[0], int(p[1])
        trim = int(p[2]) if len(p) > 2 else None
        hit = int(p[3]) if len(p) > 3 else None      # ⚑ 1 起
        path = os.path.join(ANIM, name if name.endswith('.png') else name + '.png')
        if not os.path.exists(path):
            print(f'✗ 图集不在：{os.path.relpath(path, ROOT)}')
            bad += 1
            continue
        cs, size = cells(path, cw)
        ms = [measure(a, cw) for a in cs]
        H = size[1]
        print(f'\n=== {name}  {size[0]}x{H}  {len(cs)} 格 (格宽 {cw}) ===')

        line = []
        for i, mm in enumerate(ms):
            mark = ''
            if hit and i + 1 == hit:
                # ① ⚑ 命中帧朝向
                ok = (mm['tip'] < 0) if want < 0 else (mm['tip'] > 0)
                mark = '  ← 命中 ' + ('✅' if ok else '⛔ **武器朝后**')
                if not ok:
                    bad += 1
            line.append(f"  格{i+1}  刀尖 {mm['tip']:+5d} ({'前' if mm['tip'] < 0 else '後'})"
                        f"  脚底 {mm['foot']:3d}  中线 {mm['cx']:3d}  贴边 {mm['edge']}{mark}")
        print('\n'.join(line))

        # ③ footTrim
        pads = [H - 1 - m['foot'] for m in ms]
        if trim is not None:
            d = max(abs(p - trim) for p in pads)
            print(f"  ③ footTrim: 招式表 {trim}  实测 {min(pads)}~{max(pads)}  "
                  f"{'✅' if d <= 2 else f'⛔ 差 {d}px ⇒ 帧会整体错位'}")
            if d > 2:
                bad += 1
        # ④ 脚底一致
        fd = max(m['foot'] for m in ms) - min(m['foot'] for m in ms)
        print(f"  ④ 脚底一致性: 差 {fd}px  {'✅' if fd <= 3 else '⛔ 人会抖'}")
        if fd > 3:
            bad += 1
        # ⑤ 贴边
        e = sum(m['edge'] for m in ms)
        print(f"  ⑤ 贴边: {e}px  {'✅' if e == 0 else '⛔ 内容被格子切了，放大 --cell 重切'}")
        if e:
            bad += 1

        if hit:
            segs.append((name, silhouette(cs[hit - 1])))

    # ② ⚑⚑ 段与段的命中帧剪影 —— ⚑ 这一条是「三段普攻变成两个动作」那次唯一能提前发现的判据
    if len(segs) >= 2:
        print('\n=== ② 命中帧剪影 IoU（⚑ 越低越是不同的动作）===')
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                v = iou(segs[i][1], segs[j][1])
                ok = v < thr
                print(f"  {segs[i][0]} vs {segs[j][0]}: {v:.3f}  "
                      f"{'✅' if ok else f'⛔ ≥{thr} ⇒ 玩家会看成同一招'}")
                if not ok:
                    bad += 1

    print(f'\n{"✅ 全部通过" if bad == 0 else f"⚠⚠ {bad} 项不通过"}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
