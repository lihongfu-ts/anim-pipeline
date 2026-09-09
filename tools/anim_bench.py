# -*- coding: utf-8 -*-
"""⚑⚑⚑ **序列帧的评判标准** —— ⛔ 不是我拍脑袋定的阈值，⚑ 是从**已出货的游戏**上量出来的。

```bash
python tools/anim_bench.py                                        # ⚑ 只量基准（⚑ 先确认基准素材找得到）
python tools/anim_bench.py --sheet=out/anim/idle.png --cell=192x256   # ⚑ 基准 ＋ 自己的，并排出表
python tools/anim_bench.py --sheet=… --ref=<基准素材目录>              # ⚑ 或用 ANIMPIPE_REF
```
⚠ 基准素材⛔ 不在本仓库里（⚑ 是别人游戏的资源）—— ⚑ 怎么准备见下面 REF 那段注释。

⚠⚠⚠ **为什么需要这份**（2026-08-31，玩家原话：「你做出来的序列帧你没有评判标准」）：

⚑ 在这之前我报的全是**描述性数字**（帧差 18.8 / 躯干差 102.5 / 身高 202）——
⚠ 它们说明「变了多少」，⛔ 说明不了「够不够」。⇒ ⚑ 「合格/不合格」全是我的主观印象。

⚑ 判据的来源只能是**实物**：`_ref/Slash-The-Hordes`（538★，已发布，Cocos Creator）
  的主角 9 张 32×32 —— ⚑ 那是一个真的能玩的游戏认可的下限。

── ⚑ 量四个指标，全部在**统一显示高度**下算（⛔ 不在原始分辨率下比）────
```
① 动量      相邻帧在不透明区域的平均色差       ⚑ 太小 ＝ 看着没动
② 形变      相邻帧剪影(alpha)变化的像素占比    ⚑⚑ 这条最要紧 —— ⚑ 「姿势变了」的度量
                                              ⚠ 只有①没有② ＝ 一张图在原地闪，⛔ 不是动画
③ 循环缝    首尾帧差 ÷ 相邻帧平均差            ⚑ >1.5 ＝ 循环时会跳一下
④ 剪影密度  不透明像素占内容盒的比例           ⚑ 太低 ＝ 缩小后散架
```
"""
import io
import os
import sys

import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))   # ⚑ 代码位置（⛔ 别拿它找数据）
# ⚑ 数据根 ＝ **当前工作目录**（⛔ 不是脚本位置）—— ⚑ cd 到你的项目再跑，产物就落在那儿。
#   ⚑ 和 artgen（ai-asset-gen）同一条约定。⚑ 要挪位置就设 ANIMPIPE_WORK / ANIMPIPE_OUT。
ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
WORK = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')  # ⚑ 中间产物：视频/联络表/gif
OUT = os.environ.get('ANIMPIPE_OUT') or os.path.join(ROOT, 'out', 'anim')     # ⚑ 成品：序列帧图集
# ⚑⚑ 判据的**基准素材** —— ⛔ 不跟着这个仓库分发（⚑ 是别人游戏的资源）。⚑ 自己拉一份：
#     git clone https://github.com/AlexeyGorbunov/Slash-The-Hordes   （⚑ 或任何一个已出货的同类游戏）
#     set ANIMPIPE_REF=<那份>/assets/Media/Images/Game/Player        （⚑ 或 --ref=）
# ⚠⚠⚠ **换画风/画幅就得自己重量一份基准** —— ⚑ 这套区间是从 32x32 的 Q 版剪影上量出来的，
#     ⛔ 照抄阈值会把好片判废、把废片判过。⚑ 判据为什么必须来自实物见《AI角色动画管线.md》§5.1。
REF = os.environ.get('ANIMPIPE_REF') or os.path.join(
    ROOT, '_ref', 'Slash-The-Hordes', 'assets', 'Media', 'Images', 'Game', 'Player')
NORM_H = 100          # ⚑ 统一缩到角色高 100px 再比 —— ⛔ 32x32 和 192x256 直接比没意义


def load(paths):
    ims = []
    for p in paths:
        im = Image.open(p).convert('RGBA')
        ims.append(im)
    return ims


def norm(ims):
    """按**第一帧**的内容盒统一裁切＋缩放 —— ⚠ 逐帧各裁各的 ＝ 把位移也归一化掉了"""
    a = np.asarray(ims[0])[:, :, 3]
    ys, xs = np.where(a > 16)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    h = box[3] - box[1]
    s = NORM_H / h
    out = []
    for im in ims:
        c = im.crop(box)
        out.append(c.resize((max(1, round(c.width * s)), NORM_H), Image.LANCZOS))
    return out


def metrics(ims):
    A = [np.asarray(i, dtype=np.int16) for i in ims]
    n = len(A)
    if n < 2:
        return None
    move, shape = [], []
    for k in range(n - 1):
        p, q = A[k], A[k + 1]
        both = (p[:, :, 3] > 16) | (q[:, :, 3] > 16)
        if both.sum() == 0:
            continue
        move.append(float(np.abs(p[:, :, :3] - q[:, :, :3]).sum(axis=2)[both].mean()) / 3)
        sil = (p[:, :, 3] > 128) ^ (q[:, :, 3] > 128)          # ② 剪影异或 ＝ 姿势真的变了
        shape.append(100.0 * sil.sum() / max(both.sum(), 1))
    both = (A[0][:, :, 3] > 16) | (A[-1][:, :, 3] > 16)
    seam = float(np.abs(A[0][:, :, :3] - A[-1][:, :, :3]).sum(axis=2)[both].mean()) / 3
    dens = 100.0 * (A[0][:, :, 3] > 128).sum() / (A[0].shape[0] * A[0].shape[1])
    m = float(np.mean(move))
    # ⚠⚠⚠ **崩坏检测** —— 2026-08-31 玩家指出前，这份只有下限没有上限，
    #   ⚠ 结果 idle4 那版「角色整个发白」的废片跑出动量 99.9 / 形变 35.9，**全部"达标"**。
    #   ⚑ 判据必须能把「动得多」和「崩了」分开 ⇒ 量**帧间平均亮度的落差**：
    #   ⚑ 正常的呼吸/走路，亮度基本不动；⚠ 一崩（发光·变色·换人）亮度立刻跳。
    lum = [float((0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2])[a[:, :, 3] > 128].mean())
           for a in A]
    return {'动量': m, '形变%': float(np.mean(shape)), '循环缝': seam / max(m, 1e-6),
            '剪影密度%': dens, '亮度漂': max(lum) - min(lum)}


def row(name, m, note=''):
    if m is None:
        print(f'  {name:<22} —— 只有 1 帧，量不了')
        return
    print(f'  {name:<22} 动量 {m["动量"]:6.2f}   形变 {m["形变%"]:5.2f}%   '
          f'循环缝 {m["循环缝"]:5.2f}   剪影密度 {m["剪影密度%"]:5.1f}%   '
          f'亮度漂 {m["亮度漂"]:5.1f}  {note}')


def main():
    args = sys.argv[1:]

    def opt(k, d=None):
        return next((a.split('=', 1)[1] for a in args if a.startswith(k + '=')), d)

    ref_dir = opt('--ref', REF)
    print('═══ 基准：Slash-The-Hordes（538★ · 已发布 · Cocos Creator · 32x32）═══')
    ref = {}
    for tag, names in [('Idle  2帧', ['PlWarriorIdle00', 'PlWarriorIdle01']),
                       ('Move  4帧', [f'PlWarriorMove0{i}' for i in range(4)]),
                       ('Die   3帧', [f'PlWarriorDie0{i}' for i in range(3)])]:
        ps = [os.path.join(ref_dir, f'{n}.png') for n in names]
        if not all(os.path.exists(p) for p in ps):
            print(f'  {tag} ✗ 找不到基准素材 —— ⚑ 设 ANIMPIPE_REF=<目录> 或 --ref=<目录>')
            print(f'      （现在找的是 {ref_dir}）')
            break
        m = metrics(norm(load(ps)))
        ref[tag] = m
        row(tag, m)

    # ⚑ 要量的图集走参数给 —— ⛔ 别写死文件名和格子尺寸（⚑ 每个动作的格子都不一样大）
    sheet = opt('--sheet')
    if not sheet:
        print('\n⚑ 只量了基准。⚑ 量自己的：--sheet=out/anim/<tag>.png [--cell=192x256]')
        return
    print('\n═══ 我们的 ═══')
    ours = sheet if os.path.isabs(sheet) else os.path.join(ROOT, sheet)
    try:
        cw, ch = (int(v) for v in opt('--cell', '192x256').lower().split('x'))
    except ValueError:
        print('✗ --cell 要写成 <宽>x<高>，例如 --cell=348x256')
        sys.exit(1)
    if os.path.exists(ours):
        sh = Image.open(ours).convert('RGBA')
        cells = [sh.crop((i * cw, 0, (i + 1) * cw, ch)) for i in range(sh.width // cw)]
        m = metrics(norm(cells))
        row(f'{os.path.basename(ours)} {len(cells)}帧', m)

        if 'Idle  2帧' in ref and m:
            bi, bm, bd = ref['Idle  2帧'], ref.get('Move  4帧'), ref.get('Die   3帧')
            # ⚠⚠ **区间，⛔ 不是下限** —— ⚑ 上限一律从基准里取，⛔ 不是我拍的：
            #    ⚑ idle 的形变超过 Move 基准 ＝ 它在做 Move 该做的事，⚠ 对待机就是异常。
            rng = {
                '动量': (bi['动量'] * 0.75, (bd or bm)['动量'] * 1.1),
                '形变%': (bi['形变%'] * 0.7, (bm or bi)['形变%'] * 1.2),
                '循环缝': (0, bi['循环缝'] * 1.5),
                '剪影密度%': (bi['剪影密度%'] * 0.85, 100),
                '亮度漂': (0, max(bi['亮度漂'], bm['亮度漂'] if bm else 0) * 1.5 + 2),
            }
            print('\n═══ 判据 A：「身体也在动」型 idle（基准 = Slash-The-Hordes）═══')
            for k, (lo, hi) in rng.items():
                v = m[k]
                ok = lo <= v <= hi
                why = '' if ok else ('  ← 太小' if v < lo else '  ← 太大，多半是崩了⛔不是动得好')
                print(f'  {k:<10} 我们 {v:6.2f}   合格区间 [{lo:.2f}, {hi:.2f}]   '
                      f'{"✅" if ok else "❌"}{why}')

            # ⚠⚠⚠ **判据 A ⛔ 不适用于「身体静止型」**（2026-08-31 玩家定的方向：
            #    「人固定，头发飘，刀冒烟」）—— ⚑ A 的动量下限是从一个**身体在动**的 idle
            #    上量出来的，⚠ 拿去判「身体不动」等于用走路的标准判站着。
            #    ⇒ ⚑ 静立型要**分区看**：⚑ 身体框内越静越好、⚑ 框外（发/刀）必须真的在动。
            print('\n═══ 判据 B：「身体静止 · 发和刀在动」型 idle ═══')
            # ⚠⚠ 这里**⛔ 不能套 `norm()`** —— ⚑ norm 按内容盒裁切，⚠ 而内容盒含往左伸出的刀
            #    ⇒ ⚑ 裁完身体偏右，⚠ 中间那个「核心框」框到的就不是躯干了（实测 0.79% 被误报成 3.08%）。
            #    ⚑ 原格子已经被 vid2anim 按头部对齐居中过了，⚑ 直接用。
            A2 = [np.asarray(x, dtype=np.int16) for x in cells]
            H2, W2 = A2[0].shape[:2]
            core = np.zeros((H2, W2), bool)
            core[int(H2 * .30):int(H2 * .95), int(W2 * .36):int(W2 * .64)] = True
            dif = np.abs(A2[0][:, :, :3] - A2[-1][:, :, :3]).sum(axis=2) / 3
            sil = (A2[0][:, :, 3] > 128) ^ (A2[-1][:, :, 3] > 128)
            checks = [
                ('身体剪影变化%', 100 * sil[core].sum() / max(core.sum(), 1), 0, 1.5, '越小越好'),
                ('外围剪影变化%', 100 * sil[~core].sum() / max((~core).sum(), 1), 3.0, 100, '必须真的在动'),
                ('身体色差', float(dif[core].mean()), 0, 12, '越小越好'),
                ('外围色差', float(dif[~core].mean()), 8, 100, '必须真的在动'),
                ('亮度漂', m['亮度漂'], 0, 4.17, '崩坏检测'),
            ]
            for name, v, lo, hi, note in checks:
                ok = lo <= v <= hi
                print(f'  {name:<14} {v:6.2f}   要求 [{lo:.1f}, {hi:.1f}]   {"✅" if ok else "❌"}   {note}')
    else:
        print(f'✗ 图集不在：{ours}')


if __name__ == '__main__':
    main()
