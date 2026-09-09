# -*- coding: utf-8 -*-
"""⚑⚑⚑ **图生视频 → 角色序列帧图集** —— ⚑ `gen_video.py` 的下一棒。

```bash
python tools/vid2anim.py work/anim/hero_idle3.mp4 --tag=hero_idle --frames=2 --win=0,3
python tools/vid2anim.py ... --frames=4 --pick=loop      # ⚑ 循环动画（走路）用这个
python tools/vid2anim.py ... --frames=6 --pick=even      # ⚑ 一次性动画（攻击/死亡）用这个
```
产物：`out/anim/<tag>.png`（cols×rows 图集）＋ `work/anim/<tag>_看.gif`

⚠⚠ 这份**⛔ 不是** `vid2sheet.py` 的拷贝。那份是给**圆头像**用的，四条约束里三条在角色身上不成立：

```
⛔ 圆裁去水印    ⚑ 角色不能圆裁（手脚会切掉）→ ⚑ 改成裁底部一条
⛔ 乒乓播        ⚑ 走路乒乓 ＝ 倒着走 → ⚑ 循环动画改成「自动找循环点」
⛔ 只取开头 0.7s ⚑ 改成可指定窗口 ＋ 按内容挑帧
✅ 整段一个 gain ⚑ 这条照抄（⛔ 逐帧各归各的 ＝ 亮度自己一跳一跳）
```

## ⚠⚠⚠ 2026-08-31 在 cogvideox-flash 上实测出来的四条（⛔ 别按感觉改）

```
① ⚑⚑ **脚底 ⛔ 不是内容盒底部** —— ⚑ 实测刀尖伸到 y=0.884，比脚(0.879)还低。
   ⚠ 按内容盒对齐 ＝ 角色跟着刀的摆动上下浮，⛔ 而且一行报错都没有。
   ⇒ ⚑ 脚底 ＝ **中间 40% 那几列**的最低不透明行。

② ⚑⚑ **水印裁底部一条就够，⛔ 别中心裁** —— ⚑ 实测水印在 (0.85, 0.96)，
   ⚑ 角色最低点 0.884 ⇒ ⚑ 裁掉 y>0.895 水印全没，⚑ 角色一个像素不丢。
   ⚠ 位置**会随厂商/模型变**（⚑ gen_video.py 记的是 0.92,0.96）⇒ ⚑ 裁完必须自检。

③ ⚑⚑ **视频会缓慢缩放** —— ⚑ 实测 5.1 秒角色长高 12%（⚑ 脚不动、头往上跑），
   ⚠ prompt 里写死「不推进」「大小完全不变」**摁不住**。
   ⇒ ⚑ 抽完帧**按高度归一化**，⛔ 别指望模型。

④ ⚑ **底色要从图里量** —— ⚑ 出图时 #9A9A9A(154)，⚠ 过一遍视频压缩变成 [137,134,133]，
   ⚑ 而且**不再是严格中性**（R/G/B 差 4）。⚑ `cutout_grey.cutout(bg=None)` 自己会量。
```
"""
import io
import os
import shutil
import subprocess
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
sys.path.insert(0, os.path.join(HERE, 'artgen'))
from cutout_grey import cutout  # noqa: E402

DST = OUT
LOOK = WORK

# ⚠⚠⚠ **2026-08-31 起默认 off** —— ⚑ 智谱后台「去水印管理」开关打开后**根本不打水印**了
#   （实测 p2/p3/p4 三发，右下角水印区不透明像素 **0 个**）⇒ ⚑ 不该再裁。
#   ⚠⚠ 而且**继续裁会切掉脚**：⚑ 3/4 侧立绘那批角色**脚底伸到 y=0.934**，
#     ⛔ 裁 y>0.895 是从**脚踝**横切过去，整双靴子没了。
#   ⚠⚠⚠ 更坏的是**它不会报错** —— ⚑ 原来的自检只查「水印区还有没有残留」，
#     ⛔ 不查脚还在不在 ⇒ ⚑ 出来一张没有脚的图集，⚠ 一行报错都没有。
#   ⇒ ⚑ 只有确认源视频**真的带水印**时才 `--wmcut=0.895` 手动开。
WM_CUT = 0.0            # 0 = 不裁。⚑ 用 --wmcut=0.895 显式打开，见文件头 ②
CELL = (192, 256)       # 每格工作分辨率 —— ⚑ 是目标 96x128 的 2 倍，进游戏再缩
FOOT_Y = 0.92           # 脚底放在格子的这个高度（下面留一点给影子压过来）
TORSO = (0.30, 0.70)    # 找脚底时只看中间这几列，见 ①


def opt(flag, dflt=None, cast=str):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def grab(mp4, tmp):
    """⚑ 整段拆成 PNG（⛔ 不做等间隔抽样 —— ⚑ 挑帧比抽帧好，见文件头）"""
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, os.path.join(tmp, 'f%04d.png')], check=True)
    return sorted(os.path.join(tmp, f) for f in os.listdir(tmp))


def thumbs(fs):
    return np.stack([np.asarray(Image.open(f).convert('L').resize((128, 192)), dtype=np.int16) for f in fs])


def pick(fs, n, mode, win, fps):
    """挑哪几帧。⚑ 三种模式各配一类动画，⛔ 别混用（见文件头②的表）"""
    # ⚑⚑⚑ `--at=27,60,76,95,133` —— **手点帧号**（1 起，⚑ 和 ffmpeg 拆出来的 f%04d 对齐）。
    #   ⚠⚠ **攻击动画⛔ 不能用 `--pick=even`**（2026-08-31 崩山击）——
    #     ⚑ 循环动画看的是整体印象，⚑ 均分采样够用；
    #     ⚠ 但攻击的**关键帧就是全部**（举到最高、砸到最低），⛔ 均分命中不了，
    #       ⚑ 而且模型**不知道**哪几帧是关键帧，⇒ ⛔ 指望它把关键姿势摆在均分点上是碰运气。
    #   ⚑ 另一个必须手点的理由：⚠ 源视频**局部帧是废的**（崩山击实测 f44~53、f86 顶边切掉刀尖，
    #     ⛔ 那是视频本身丢了内容，⚑ 放大格子补不回来）⇒ ⚑ 得能**绕开**这些帧。
    at = opt('--at')
    if at:
        sel = [int(v) - 1 for v in at.split(',')]
        bad = [v + 1 for v in sel if not (0 <= v < len(fs))]
        if bad:
            print(f'  ⚠⚠ ⛔ 帧号越界：{bad}（⚑ 这段视频只有 {len(fs)} 帧）')
            sys.exit(1)
        print('  ⚑ 手点帧 ' + ' '.join(f'f{v+1:03d}' for v in sel))
        return [fs[i] for i in sel]

    A = thumbs(fs)
    lo = int(win[0] * fps)
    hi = min(int(win[1] * fps), len(fs)) if win[1] else len(fs)
    idx = list(range(lo, hi))
    T = A[:, 95:150, 35:95]                       # ⚑ 只看躯干，⛔ 别把飘动的头发和刀算进来

    if mode == 'even':                            # 一次性动画（攻击/死亡）：窗口内均分
        sel = [idx[round(k * (len(idx) - 1) / max(n - 1, 1))] for k in range(n)]

    elif mode == 'still':
        # ⚑⚑ **人不动，只有头发和刀动**（2026-08-31 玩家：「人还在动」）——
        #    ⚠⚠ `extreme` 模式是找**躯干差最大**的一对，⛔ 那是在专门挑「人动得最多」的两帧，
        #      ⚑ 跟这个需求正好相反。
        #    ⚑ 判据：核心身体框（躯干＋腿）的差**越小越好**，框外（头发·刀·衣摆）**越大越好**。
        #    ⚠⚠ 核心框**必须包含脸**（2026-08-31 玩家：「他的头动了」）——
        #      ⚑ 我原来的框从 30% 高度起 ⇒ ⚠ 头整个在框外，被当成「该动的外围」，
        #      ⇒ ⛔ 头的移动不但没被罚，反而被奖励了。
        #      ⚑ 但**头发该动、脸不该动** ⇒ 脸那一段的框要**窄**（避开两侧头发）。
        #    ⚠⚠⚠ **身体三区要分开算、⛔ 取最差的那个，⛔ 别塞进一个大框求平均**
        #      （2026-08-31 玩家：「待机人的身体还是在动」）——
        #      ⚑ 原来脸＋躯干＋腿共用一个 core 求 mean ⇒ ⚠ **腿脚面积小，被躯干稀释**。
        #      ⚑ 实测挑出来的那一对：报「身体差 1.53」看着很稳，⚠ 但切成 192×256 成品后量分区：
        #        ⛔ **腿脚剪影变化 5.86%**（判据 B 上限 1.5%，超 3.9 倍）、躯干 2.19%。
        #      ⇒ ⚑ 改成三区各算各的、⚑ 取 **max** —— ⚑ 任何一区动了就算它动。
        H_, W_ = A.shape[1], A.shape[2]
        face = (slice(int(H_ * .15), int(H_ * .42)), slice(int(W_ * .42), int(W_ * .58)))   # 脸：窄，避开两侧头发
        torso = (slice(int(H_ * .42), int(H_ * .72)), slice(int(W_ * .36), int(W_ * .64)))
        legs = (slice(int(H_ * .72), int(H_ * .95)), slice(int(W_ * .36), int(W_ * .64)))
        core = np.zeros(A.shape[1:], bool)
        for r in (face, torso, legs):
            core[r] = True

        def body_of(d):
            return max(float(d[face].mean()), float(d[torso].mean()), float(d[legs].mean()))

        best = (-1e9, idx[0], idx[0])
        for i in idx:
            for j in idx:
                if not (15 <= j - i <= 120):
                    continue
                d = np.abs(A[i] - A[j])
                sc = float(d[~core].mean()) - body_of(d) * 4.0    # ⚑ 身体一动就重罚
                if sc > best[0]:
                    best = (sc, i, j)
        d = np.abs(A[best[1]] - A[best[2]])
        print(f'  ⚑ 静立对 f{best[1]+1:03d}↔f{best[2]+1:03d}   身体差 脸{float(d[face].mean()):.2f}/'
              f'躯干{float(d[torso].mean()):.2f}/腿{float(d[legs].mean()):.2f}（越小越好）  '
              f'外围差 {float(d[~core].mean()):.2f}（越大越好）')
        sel = [best[1], best[2]] if n == 2 else list(np.linspace(best[1], best[2], n).round().astype(int))

    elif mode == 'extreme':                       # idle：躯干差最大的一对 ＝ 呼吸两极点
        best = (-1, idx[0], idx[0])
        for i in idx:
            for j in idx:
                if not (15 <= j - i <= 80):
                    continue
                v = float(np.abs(T[i] - T[j]).mean())
                if v > best[0]:
                    best = (v, i, j)
        print(f'  ⚑ 躯干极点 f{best[1]+1:03d}↔f{best[2]+1:03d}  差 {best[0]:.1f}')
        sel = [best[1], best[2]] if n == 2 else list(np.linspace(best[1], best[2], n).round().astype(int))

    else:                                         # loop：找首尾最接近的一段（走路循环）
        # ⚠⚠⚠ **⛔ 别只优化「首尾差最小」**（2026-08-31 走路第一次切图踩的）——
        #   ⚑ **站着不动的那一段首尾差必然最小**：实测 w5 开头 0.39s 静止段差 **4.49**，
        #     ⚑ 而真正在迈腿的段落是 **12.00** ⇒ ⚠ 这个目标函数是在**专门挑最不动的一段**，
        #     ⛔ 和「挑走路循环」的本意正好相反。
        #   ⚑ 症状和 `--pick=still` 当年误用 `extreme` 是**同一类错误：目标函数反了**。
        #     ⚠ 而且切出来的图集**指标不会全崩**（动量 4.46 / 形变 1.24%），
        #     ⛔ 只有跟基准 Move（17.79%）比才看得出是废的。
        #   ⇒ ⚑ 先用**逐帧运动量**筛掉静止段，⚑ 只在**连续活跃**的区间里找循环点。
        mot = np.array([0.0] + [float(np.abs(A[i] - A[i - 1]).mean()) for i in range(1, len(A))])
        thr = float(np.median(mot[lo:hi])) * 0.6      # ⚑ 静止段远低于窗口内运动量中位数
        aset = {i for i in idx if mot[i] > thr}
        if len(aset) < n + 2:                         # ⚠ 整段都很静 ⇒ 退回全窗口，⛔ 别硬筛成空
            aset = set(idx)
        act = sorted(aset)
        # ⚑ 前缀和：段 [i,j] 里有几帧是静止的 ⇒ O(1) 判「⛔ 别跨过静止段把两头接起来」
        dead = np.cumsum([0] + [(0 if k in aset else 1) for k in range(len(A))])
        print(f'  ⚑ 活跃帧 {len(act)}/{len(idx)}（运动量阈值 {thr:.2f}）')

        # ⚠⚠⚠ **先量周期，⛔ 再采样**（HANDOFF ⑥）—— ⚑ 只筛静止段还不够：
        #   ⚑ 走路左右腿**对称**，⇒ ⚠ **半个周期**首尾也很接近（姿势近似镜像），
        #     ⛔ 自由搜段长会优先选中半周期（实测选了 0.52s，真周期是 1.10s）。
        #   ⚠⚠ 半周期**不能用**：⚑ 循环播放时同一条腿一直在前 ⇒ ⛔ 看着是瘸的。
        #     ⚑ 而且 6 帧铺在半周期上，⚠ 形变只有 12.25%（基准 Move 17.79%）⇒ 不达标。
        #   ⇒ ⚑ 用**自相关**量周期（整段平均，⛔ 比单帧对比较稳健），⚑ 再把段长钉死在周期上。
        def corr_at(L):
            vals = [float(np.abs(A[i] - A[i + L]).mean()) for i in act if (i + L) in aset]
            return float(np.mean(vals)) if len(vals) >= 5 else None

        corr = {}
        for L in range(int(fps * .35), int(fps * 1.8)):
            v = corr_at(L)
            if v is not None:
                corr[L] = v
        if corr:
            period = min(corr, key=corr.get)
            # ⚑ **全周期优先**：⚑ 若 2L 也够相似，⛔ 说明刚才量到的是半周期。
            # ⚠⚠ 2L **必须单独算**，⛔ 别去 corr 表里查 —— ⚑ 表的上限只到 1.8s，
            #   ⇒ ⚠ 任何 >0.9s 的 period 其 2L 都不在表里，⛔ 这个检查会被**静默跳过**。
            #   ⚑ 实测 run_v2_3 量到 28 帧(0.90s) ⇒ 2L=56 超出表范围 ⇒ ⚠ 半周期蒙混过关，
            #     ⛔ 循环缝 1.44（全周期的那发是 0.95）。
            # ⚠⚠ **⛔ 别要求段内「一帧不落地全活跃」** —— ⚑ 运动量是逐帧算的，
            #   ⚑ 步态的两个极点附近本来就会短暂低于阈值 ⇒ ⚠ 活跃帧虽多但是**散布**的。
            #   ⚑ 实测万相那发 119/150 活跃，⛔ 却找不出一段 37 帧全活跃的
            #     ⇒ ⚠ 退化成 f022→f022、首尾差 1e9，⛔ 切出 6 张同帧。
            #   ⇒ ⚑ 允许段内有 ≤15% 的低活跃帧，⚑ 只挡「整段横跨静止区」那种。
            def dead_ok(i, P):
                return dead[i + P + 1] - dead[i] <= P * 0.15

            def starts(P):
                """⚑ 段长 P 时所有合法起点（⚑ 段内低活跃帧不超过 15%）"""
                return [i for i in act if (i + P) in aset and dead_ok(i, P)]

            sim = corr[period]
            c2 = corr_at(2 * period)
            # ⚠⚠ 翻倍**必须先确认还有合法起点** —— ⚑ 视频只有 5 秒，
            #   ⛔ 周期一翻倍可能整段都放不下 ⇒ ⚑ 实测 run_v2_1 翻到 108 帧(3.48s) 后
            #     ⚠ 一个起点都没有，⛔ 循环点退化成 f006→f006、首尾差 1e9，⚠ 切出来是 6 张同帧。
            if c2 is not None and c2 <= sim * 1.35 and starts(2 * period):
                period, sim = 2 * period, c2
            print(f'  ⚑ 步频周期 {period} 帧 ({period/fps:.2f}s)  自相似 {sim:.2f}')
        else:
            period = int(fps * 1.0)
            print(f'  ⚠ 量不到周期，退回 {period/fps:.2f}s')

        # ⚑ 段长钉死 = 周期，⛔ 只搜起点
        best = (1e9, act[0], act[0])
        for i in act:
            j = i + period
            if j not in aset or dead[j + 1] - dead[i] > period * 0.15:
                continue
            v = float(np.abs(A[i] - A[j]).mean())
            if v < best[0]:
                best = (v, i, j)
        if best[1] == best[2]:                     # ⚠ 一个合法起点都没有 ⇒ ⛔ 别静默产出同帧
            print(f'  ⚠⚠ ⛔ 周期 {period} 帧在窗口内放不下，没有合法循环段')
        print(f'  ⚑ 循环点 f{best[1]+1:03d}→f{best[2]+1:03d}  '
              f'({(best[2]-best[1])/fps:.2f}s)  首尾差 {best[0]:.2f}')
        sel = list(np.linspace(best[1], best[2], n + 1).round().astype(int)[:n])   # ⛔ 不含末帧（它≈首帧）

    return [fs[i] for i in sel]


def _longest_run(ok):
    """⚑ 布尔数组里**最长的一段连续 True** 的 (起, 止)。⚑ 没有就返回 None。"""
    best = None
    s = None
    for i, v in enumerate(ok):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if best is None or i - s > best[1] - best[0] + 1:
                best = (s, i - 1)
            s = None
    if s is not None and (best is None or len(ok) - s > best[1] - best[0] + 1):
        best = (s, len(ok) - 1)
    return best


def despeckle(im, minpx=5):
    """⚑⚑ 抠完之后，⛔ 干掉**画面边缘那条噪点带**（⚑ 只保留角色所在的那一段行/列）。

    ⚠⚠⚠ 2026-09-01（尸傀攻击那条）——⚑ 这是 §11.3 之后的**第三个**同族故障。
      ⚑ 三个症状完全一样（⚑ 内容盒撑满画面 ⇒ 归一化系数全错 ⇒ 某几格被缩小），
      ⛔ 但根因一层比一层深：
      ① 成片弱噪点（alpha≈43，每行 7~27 个）  ⇒ ✅ alpha>200 一刀切      （§11.3）
      ② 零星强像素（每行 2 个）                ⇒ ✅ MIN_ROW_PX 行像素下限（rows_with_body）
      ③ **画面边缘的一条噪点带**               ⇒ ✅ 本函数

    ⚑ ③ 长这样（⚑ mob_atk_v2 的 f049，⚑ 画面 774 高、⚑ 角色脚底在 y=692）：
      y=0        18 个强像素     ⚑ 顶边一行
      y=751~773  6→74 的渐变     ⚑ 底边一条带（⚑ 在脚下方 60px，⛔ 不是影子）

      ⚠⚠ **原始帧肉眼完全看不出来** —— ⚑ 低对比度，⚑ 但色度距离够大 ⇒ 过得了 alpha>200。
      ⚠⚠ 而且它**行像素数有几十个** ⇒ ⛔ MIN_ROW_PX 拦不住；
        ⚑ 试过形态学开运算（腐蚀到 er=11）和连通域重建，⛔ **两个都只能压到 621，压不干净**。
      ⇒ ⚑⚑ 分开它和角色的是**连续性**：⚑ 角色是 540 行连续，⚑ 噪点带只有 12~23 行。

    ⚠⚠ 为什么必须连**输出**一起洗（⛔ 光改测量不够）：
      ⚑ 那条带在脚下方 60px，⚑ 而 footpad 换算回源图约 99px ⇒ ⚑ **它会落进格子里**，
      ⇒ ⚠ 不洗的话图集底部会多一道灰渣（⚑ 工具报的「贴边 16px」就是它）。
    """
    a = np.asarray(im)
    strong = a[:, :, 3] > 200
    ry = _longest_run(strong.sum(1) >= minpx)
    rx = _longest_run(strong.sum(0) >= minpx)
    if ry is None or rx is None:                 # ⚠ 整帧是空的 ⇒ ⛔ 原样退回，别把帧废掉
        return im
    out = a.copy()
    out[:ry[0], :, 3] = 0
    out[ry[1] + 1:, :, 3] = 0
    out[:, :rx[0], 3] = 0
    out[:, rx[1] + 1:, 3] = 0
    return Image.fromarray(out, 'RGBA')


# ⚑⚑ rows_with_body 的**绝对行像素下限**（⚑ 见该函数 docstring 里 2026-09-01 那条）。
#   ⚑ 5 ＝ 挡得住孤立强散点，⚑ 又碰不到角色最细的部位（发丝/手指也 >2px 宽）。
MIN_ROW_PX = 5


def rows_with_body(a, frac=0.04):
    """⚑ 不透明像素**占列带宽 ≥ `frac`** 的行号。⛔ 别用 `.any()`，⛔ 也别用固定像素数。

    ⚠⚠⚠ 2026-08-31 在万相（wan3.0）的输出上栽的：
      ⚑ 它的背景比智谱噪 3 倍（噪点 ±13/通道 vs ±4）⇒ ⚑ 色度距离 d≈22
        ⚠ 正好落在 `cutout_grey` 的软阈值 `LO..HI`(16~52) 里 ⇒ ⚑ 背景残留 **alpha≈43**。
      ⚑ 残留是**满画面的椒盐噪点**（每行边缘 7~27 个孤立像素），⛔ 不是成片的灰纱，
        ⇒ ⚠ 调 `lo` 压不掉（实测 lo 从 16 提到 36，内容盒照样 0.000~0.999）。
      ⚠⚠ 后果是**静默**的：⚑ 内容盒撑满全画面 ⇒ ⚑ 量到的"身高"恒等于画面高
        （⚑ 打印出来是 `身高: 1175 1175 1175…` 六帧一模一样，⛔ 那是画面高 1176 不是人）
        ⇒ ⚠ 归一化系数全错 ⇒ ⛔ 切出来的格内身高在 157~202 之间乱跳。
      ⇒ ⚑ 分开它俩的**不是行像素数，是 alpha 值本身**：
        ⚑ 噪点的 alpha ≈ 43（软阈值 `(22-16)/(52-16)` 算出来的），⚑ 角色实体是 **255**。
    ⚑ 实测（wan prime 六帧，量 TORSO 列带的纵向跨度）：
      ```
      a>16 / >64 / >128 : 六帧全读成 1175  ⚠ 即画面高，⛔ 全是噪点
      a>200             : 944 939 882 897 917 923   ✅ 干净且稳定
      ```
    ⚠ 按「行像素数占比」筛过一版（4%），⛔ 不行 —— ⚑ 噪点密度逐帧变，
      ⚠ 有的帧照样过线（读出 1175/947/926/912… 一半真一半假）。

    ⚠⚠⚠ 2026-09-01 补一条 —— ⚑⚑ `a>200` **挡不住孤立强散点**（尸傀走路那发栽的）：
      ⚑ 症状和上面**很像但根因不同**，⛔ 别当成同一个问题：
        ⚑ 上面是**成片**的弱噪点（alpha≈43，每行 7~27 个）⇒ 靠 alpha 门槛解决；
        ⚠ 这次是视频后段冒出的**零星几个 alpha>200 的强像素**（顶行只有 **2 个**），
          ⚑ 而那一帧的身体强像素总数 ~91000，⚑ 和正常帧 88000~93000 **一模一样**
          ⇒ ⛔ 看总量根本发现不了，⚑ 但 `.any()` 一个像素就成立 ⇒ 内容盒 0~773（画面高 774）。
      ⚑ 实测（mob_walk 60 帧，⚑ 量到的身高）：
        ```
        行阈值      f011  f026  f042 │ f055  f058
        ≥1 (.any)    535   546   545 │  774   774   ⚠ 后段全废
        ≥3           534   546   545 │  616   619   ⚠ 还是不对
        ≥5           534   546   545 │  533   535   ✅ 落回正常区间
        ```
      ⇒ ✅ `alpha>200` **之上再加一个很低的绝对行像素下限**（⚑ 不是占比，⛔ 不是替代 alpha 门槛）。
        ⚑ 取 5 安全：⚑ 角色最细的地方（发丝、手指）也远不止 2 像素宽，
        ⚑ 干净帧受影响 ≤1px（⚑ 上表 f011 535→534，⚑ 其余不动）。
    """
    return np.where((a > 200).sum(axis=1) >= MIN_ROW_PX)[0]


def body_band(alpha):
    """⚑ 「人在哪几列」—— ⚑ 量脚底/身高**只看这几列**（⛔ 别看整幅：⚠ 刀尖比脚还低，见文件头①）。

    ⚑ 默认 fixed ＝ 写死的中间 40% 列（TORSO）。⚑⚑ `--bodycols=auto` ＝ 滑窗找**最密的** 40% 列。
    ⚠⚠⚠ 2026-09-01 后跳踩的：⚑ fixed 成立的前提是「人一直在画面中央」——
      ⚑ 走路/待机/原地技能都满足 ⇒ ⚑ 以前一直没事。
      ⚠ 而后跳用**右移尾帧**钉位移（见 `_给千问/提示词_后跳.txt`）⇒ ⚑ 后半段人站到画面 0.74，
        ⛔ 整个身体在那 40% 列之外 ⇒ ⚑ 量到的「脚底」其实是甩在那儿的刀尖或衣摆
        ⇒ ⚠ 那几格按**错误的脚底**贴进格子，⛔ **一行报错都没有**（⚑ 同文件头①那类静默故障）。
    ⚑ auto 为什么找得准：⚑ 头＋躯干＋腿的不透明像素密度远高于刀身和衣摆尖 ⇒ ⚑ 窗口自己粘住人。
    ⛔ 默认**不能**换成 auto：⚠ 已出货的走路/待机/普攻图集全是按 fixed 切的，
      ⚑ 换默认值 ＝ 悄悄改掉全部旧素材的对齐基准。
    """
    W = alpha.shape[1]
    x0, x1 = int(W * TORSO[0]), int(W * TORSO[1])
    if opt('--bodycols', 'fixed') != 'auto':
        return x0, x1
    w = max(1, x1 - x0)
    s = np.concatenate([[0], np.cumsum((alpha > 200).sum(axis=0).astype(np.int64))])
    i = int((s[w:] - s[:-w]).argmax())
    return i, i + w


def foot_anchor(alpha, xmode='head'):
    """⚑ y ＝ 人所在那几列的最低不透明行（脚底，见 ①）；⚑⚑ x ＝ **头部重心**。

    ⚠⚠ **x ⛔ 别用脚底那一带的水平中心**（2026-08-31 玩家指出「还是有移动」后量出来的）：
      ⚑ 破烂衣摆的锯齿一抖，脚底锚点就跟着抖 ⇒ ⚠ 实测两帧**头部重心差 6.2px**，
      ⚑ 而脚底 y、头顶 y、内容盒全都是对齐的 —— ⚑ 肉眼看到的「在动」就是**头在左右晃**。
      ⚑ Q 版角色头占一半，⇒ **头稳 ＞ 脚稳**（⚑ 何况脚本来就被衣摆盖住）。
    """
    H, W = alpha.shape
    x0, x1 = body_band(alpha)
    ys = rows_with_body(alpha[:, x0:x1])
    if not len(ys):
        return H - 1, W // 2
    fy = int(ys.max())
    top = int(ys.min())
    if xmode == 'bbox':
        # ⚑⚑ **自转动画用这个** —— ⚠ 头部重心对齐在旋转时会跳：
        #    ⚑ 转到侧面/背面头形完全变了（马尾甩到另一边），⇒ ⚑ 重心跟着乱跑。
        #    ⚑ 整体内容盒中心稳得多。
        xs = np.where(alpha > 16)[1]
        return fy, (int((xs.min() + xs.max()) / 2) if len(xs) else W // 2)
    if xmode == 'torso':
        # ⚑⚑ **长发角色的待机用这个**（2026-09-01 加，⚑ 玩家说「人有晃动的感觉」之后量出来的）。
        #
        # ⚠⚠ `head` 模式在 22 这个角色上会漂：⚑ 它取上 35% 求 x 的**均值**，
        #   ⛔ 而且切的是**整幅宽度** ⇒ ⚑ 长发一飘出去，⚠ 均值被整条头发拽走。
        #   ⚑ 实测 6 格里有 2 格整体右移 4~5px（⚑ 正是 vid2anim 报「身高 701」那两格 ——
        #     ⚑ 头发飘进中间 40% 列，把测量框撑高了），⇒ ⚑ 循环播就是肉眼的「晃」。
        # ⚠ 但也⛔ 不能退回「脚底那一带」——⚑ 旧注释记着：破烂衣摆的锯齿一抖锚点就抖。
        # ⇒ ✅ 两个毛病一起躲：⚑ 取**下 55%**（躯干＋腿，⛔ 不含头发）的 x **中位数**
        #    ⚑ 中位数⛔ 不是均值 ⇒ ⚠ 甩出去的衣摆尖角和刀身都是少数像素，⚑ 拽不动它。
        lo = top + int((fy - top) * 0.45)
        band = alpha[lo:fy + 1]
        bx = np.where(band > 16)[1]
        return fy, (int(np.median(bx)) if len(bx) else W // 2)
    head = alpha[top:top + max(1, int((fy - top) * 0.35))]     # 上 35% ＝ 头
    hx = np.where(head > 16)[1]
    return fy, (int(hx.mean()) if len(hx) else W // 2)


def process(paths, tmp):
    """裁水印 → 抠灰底 → 量脚底和身高 → 按首帧归一化缩放 → 按脚底贴进格子"""
    xmode = opt('--alignx', 'head')       # head=头部重心（idle用） / bbox=内容盒中心（自转用）
    do_norm = opt('--norm', 'on') != 'off'  # ⚑ 自转动画要 --norm=off：⚠ 侧面身体本来就窄，
                                            #    ⛔ 归一化会把侧面那几帧错误地放大
    _wm = str(opt('--wmcut', WM_CUT))
    wmcut = 0.0 if _wm.lower() == 'off' else float(_wm)

    cut = []
    for k, p in enumerate(paths):
        im = Image.open(p).convert('RGB')
        W, H = im.size
        if wmcut > 0:
            # ⚠⚠⚠ **裁之前先量脚底** —— ⚑ 裁到角色身上是**静默故障**：
            #   ⛔ 出片自检只查「水印区还有没有残留」，⛔ 不查脚还在不在
            #   ⇒ ⚑ 会安安静静产出一张**没有脚**的图集。⚑ 宁可在这里退出。
            a = np.asarray(im, dtype=float)
            bg = a[0:int(H * .06), 0:int(W * .08)].reshape(-1, 3).mean(0)
            d = np.abs(a - bg).max(axis=2)
            xs0, xs1 = int(W * TORSO[0]), int(W * TORSO[1])
            rows = np.where((d[:, xs0:xs1] > 16).any(axis=1))[0]
            foot = (float(rows.max()) / H) if len(rows) else 0.0
            if foot > wmcut:
                print(f'  ⚠⚠⚠ ⛔ 别用 —— --wmcut={wmcut:.3f} 会切掉角色：'
                      f'⚑ 脚底在 y={foot:.3f}，⚑ 裁切线在 y={wmcut:.3f}，⚠ 切在脚踝上方')
                sys.exit(1)
            im = im.crop((0, 0, W, int(H * wmcut)))              # ② 裁掉水印那条
        q = os.path.join(tmp, f'_c{k}.png')
        im.save(q)
        cut.append(despeckle(cutout(q, quiet=(k > 0))))          # ④ bg=None ⇒ 自己量底色

    metrics = []
    for im in cut:
        a = np.asarray(im)[:, :, 3]
        fy, fx = foot_anchor(a, xmode)
        # ⚠ 这里量身高的列**必须和 foot_anchor 用的同一段**（⚑ 所以走 body_band，⛔ 别再写死 TORSO）
        #   ⚑ 2026-09-01 后跳：⚑ 只给 foot_anchor 加了 auto，⛔ 忘了这行 ⇒ ⚠ 打印的身高是错的
        #     （⚑ 实测 f087 报 487，⚑ 而人站直才 345）—— ⚑ --norm=on 时它直接决定缩放系数。
        x0, x1 = body_band(a)
        ys = rows_with_body(a[:, x0:x1])
        top, h = int(ys.min()), fy - int(ys.min())
        # ⚠⚠⚠ **尺度基准用「核心区的不透明面积」（开方成线性）** —— ⛔ 别用身高，⛔ 也别用肩宽。
        #
        #    ⚑ 判据一句话：**基准里不能含会飘的东西**，否则归一化会照着错的比例去缩，
        #      ⚠ 而那一缩一放就是肉眼看到的「镜头在抖」。
        #
        #    ⚑ 2026-08-31 按这条走过的两次弯路（⛔ 别再走回去）：
        #    ```
        #    ⛔ 身高  ⚠ 玩家：「镜头一直在抖」→ 实测两帧身高 +4.4%、肩宽只差 −1.1%
        #             ⇒ ⚑ 角色本体没变大，长的是纵向（挺胸/抬头）＝ **动画本身**
        #             ⇒ ⚠ 等比缩放把**横向无端缩窄 4.4%**
        #    ⛔ 肩宽  ⚠ 换成肩宽后 idle7 测出 +7.1% —— ⚑ 长发垂到肩膀两侧把它撑大了
        #    ✅ 核心面积  ⚑ 腰腹那块是实心的，里面没有会飘的东西 ⇒ ⚑ 对头发和刀的摆动免疫
        #             ⚑ 实测 idle7: 355.73 → 356.56（系数 1.002）
        #    ```
        cy0, cy1 = top + int(h * 0.42), top + int(h * 0.80)
        cx0, cx1 = fx - int(h * 0.13), fx + int(h * 0.13)
        area = int((a[max(cy0, 0):cy1, max(cx0, 0):cx1] > 16).sum())
        metrics.append({'top': top, 'foot': fy, 'fx': fx, 'h': h, 'sw': max(float(np.sqrt(area)), 1.0)})

    # ⚠⚠⚠ `--scale=<源像素身高>` —— ⚑⚑ **全局缩放基准，⛔ 别用"碰巧点到的首帧"**
    #   （2026-08-31 崩山击踩的）：⚑ `--norm=off` 原本拿 `metrics[0]['h']` 当基准，
    #   ⚠ 而技能动画的首帧往往是**最深蹲**那一帧（实测 f33 身高 439，
    #     ⛔ 而伸展帧 f81 是 722 ＝ **1.645 倍**）⇒ ⚑ 整套帧被放大 64%，⚠ 溢出格子。
    #   ⚑ 判据一句话：**基准必须是"站直"的姿势** —— ⚑ 通常就是视频第 1 帧（＝立绘姿势）。
    #   ⚑ 量法：`f1` 那帧「中间 40% 列」的 foot−top（⚑ 和这里的 h 同一把尺）。
    h0, w0 = metrics[0]['h'], metrics[0]['sw']
    _sc = opt('--scale', 0, float)
    if _sc > 0:
        h0 = _sc
        print(f'  ⚑ --scale={_sc:.0f}：⚑ 全局基准取"站直"身高，⛔ 不用首帧（首帧 {metrics[0]["h"]}）')
    if not do_norm:
        print('  ⚑ --norm=off：⛔ 不做逐帧缩放归一化（自转动画侧面本来就窄）')
    print('  ⚑ 核心尺度: ' + '  '.join(f'{m["sw"]}({m["sw"]/w0:.3f})' for m in metrics)
          + '     身高: ' + '  '.join(f'{m["h"]}({m["h"]/h0:.3f})' for m in metrics)
          + '   ← ③ 按**核心面积**归一化')

    cw, ch = CELL
    # ⚠⚠⚠ **格子变大时，角色⛔ 不能跟着变大**（2026-08-31 崩山击）——
    #   ⚑ 原来这两个量都是**按格子高度比例**算的：⚑ 格子一从 256 放到 320，
    #     ⚠ 目标身高就从 202 变成 253 ⇒ ⛔ 技能帧比走路帧大一圈，接进游戏直接穿帮。
    #   ⇒ ⚑ 改成**绝对像素**，⚑ 默认值仍按老公式算 ⇒ ⛔ 不影响已出货的走路/待机：
    #     ⚑ 256 高的格子：目标身高 202、脚底距底边 20.5（⚑ 正好等于游戏里的 footTrim）
    #   ⚑ 技能动画放大格子时，**显式带上这两个**，⚑ 角色大小和脚底就和走路完全一致。
    # ⚠⚠⚠ **验收 `--target` 对不对时，量身高⛔ 不能用 `alpha > 16`**（2026-09-01 旋风斩踩的）——
    #   ⚑ 带**运动模糊**的帧（⚑ 刀快速划过的拖影）会在角色四周铺一层很淡的半透明像素，
    #     ⚑ 实测比头顶还高出 **60px** ⇒ ⚠ 按 alpha>16 量出来是「人 ＋ 雾」。
    #   ⚠⚠ 症状极具欺骗性：⚑ 走路 214 / 旋风 216 —— ⛔ 看着完全对上了，
    #     ⚑ 而**实心**像素（alpha>128）是 200 / 150 ⇒ ⚑ 人其实小了 25%。
    #   ⇒ ✅ 一律用 `alpha > 128` 量，⚑ 且和参照的那套图集**用同一个阈值**。
    #   ⚑ 用对阈值之后，`--target` 和实心人物高就是 **1:1** 的（⚑ 给 200 就是 200）。
    target = opt('--target', ch * FOOT_Y * 0.86, float)          # 格子里的目标身高（像素）
    footpad = opt('--footpad', ch * (1 - FOOT_Y), float)         # 脚底到格子底边的距离（像素）
    foot_line = ch - footpad
    out = []
    for im, m in zip(cut, metrics):
        # ⚠⚠ **一步到位**：s = 目标高 / 本帧身高。⛔ 别拆成"先归一化再缩进格子"两步 ——
        #    2026-08-31 踩过：第二步的系数里又除了一次 s，⚑ s 被约掉，⚠ 归一化等于没做
        #    （⚑ 症状：两格身高仍然差 6%，⛔ 而且脚底是对齐的，看不出来）。
        s = (target / m['h']) if do_norm else (target / h0)
        im2 = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        cell = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
        cell.alpha_composite(im2, (round(cw / 2 - m['fx'] * s),
                                   round(foot_line - m['foot'] * s)))     # ① 脚底对齐
        out.append(cell)
    return out


def freeze_body(cells, mode="body"):
    """⚑⚑⚑ **身体不靠模型冻结，⛔ 靠合成冻结** —— 2026-08-31 待机踩了一整轮才想到的。

    ⚠⚠ 「身体静止型」idle 的病根：**模型压根摁不住腿**。
      ⚑ 实测同一条 prompt 打三发，⚠ 成品腿脚剪影变化 **2.75% / 10.39% / 16.69%**（差 6 倍），
        ⛔ 判据 B 的上限是 1.5% ⇒ ⚑ 这是**方差**，⛔ 不是 prompt 能修的。
      ⚑ 而且已经排除了对齐的嫌疑：⚑ 两帧头部重心只差 1.50px、头顶 y 差 0，
        ⚠ 但**脚底水平中心差 18.5px** ⇒ ⛔ 脚是真的在挪。

    ⇒ ⚑ 既然两帧已经对齐好了，⚑ 那就把后续帧的**核心框整块换成首帧的**：
      ⚑ 框内（脸·躯干·腿脚）＝ 首帧 ⇒ **身体剪影变化数学上恒等于 0**；
      ⚑ 框外（头发·衣摆·刀）＝ 本帧 ⇒ 该飘的照飘。
      ⚑ 实测：脸 0.51% / 躯干 **0.00%** / 腿脚 **0.00%** / 外围 12.41%（要 >3%）⇒ 全部达标。
      ⚑ 框选**窄的**（0.36~0.64 宽）：⚠ 宽框会把肩侧的头发也冻住，外围掉到 7.55%。
    ⚠ ⛔ 别拿它去处理走路 —— 那是**身体必须动**的动画，冻了就废了。
    """
    if len(cells) < 2:
        return cells
    cw, ch = CELL
    core = np.zeros((ch, cw), bool)
    if mode == 'legs':
        # ⚑⚑ **只冻腿脚，⚑ 放开躯干** —— ⚑ 待机要「呼吸感」时用这个。
        #   ⚑ `body` 会把整个核心框换成首帧 ⇒ ⚠ **呼吸也一起冻掉了**，人像贴纸。
        #   ⚑ 而「脚在原地挪」才是真丑（看着像原地踏步）⇒ ⚑ 只钉死腿脚就够。
        #   ⚑ 参考基准 Slash-The-Hordes 的 Idle 本来就是**身体在动**型（形变 13.20%）。
        core[int(ch * .70):int(ch * .99), int(cw * .34):int(cw * .66)] = True
    else:
        core[int(ch * .40):int(ch * .99), int(cw * .36):int(cw * .64)] = True
    base = np.asarray(cells[0]).copy()
    out = [cells[0]]
    for c in cells[1:]:
        arr = np.asarray(c).copy()
        arr[core] = base[core]
        out.append(Image.fromarray(arr))
    print(f'  ⚑ --freeze={mode}：{"腿脚" if mode == "legs" else "核心框"}已换成首帧 '
          f'⇒ {"腿脚" if mode == "legs" else "身体"}剪影变化 = 0')
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    # ⚑⚑ `--cell=WxH` —— ⚠ **技能动画必须放大格子**（2026-08-31 崩山击踩的）：
    #   ⚑ 192×256 是照**直立**的待机/走路定的，⚑ 那时刀横在身前、人不离地。
    #   ⚠ 崩山击**举刀过头 ＋ 腾空伸展** ⇒ ⛔ 同一个格子装不下：
    #     ⚑ 实测溢出「右 59px / 上 13px / 左 7px」，⚠ 而且**是平齐切断的刀刃**，
    #     ⛔ 出片自检只查水印区，⚠ 不查有没有贴边 ⇒ 又是**静默**故障。
    #   ⚑ ⛔ 别为了塞进去而缩小角色 —— ⚑ 那会让技能帧和走路帧**不同比例**，接进游戏就穿帮。
    global CELL
    _c = opt('--cell')
    if _c:
        CELL = tuple(int(v) for v in _c.lower().split('x'))
    mp4 = args[0] if os.path.isabs(args[0]) else os.path.join(ROOT, args[0])
    tag = opt('--tag', os.path.splitext(os.path.basename(mp4))[0])
    n = opt('--frames', 2, int)
    mode = opt('--pick', 'extreme')
    cols = opt('--cols', 4, int)
    w = opt('--win', '0,0')
    win = tuple(float(x) for x in w.split(','))

    fps = float(subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=r_frame_rate', '-of', 'csv=p=0', mp4],
        capture_output=True, text=True, check=True).stdout.strip().split('/')[0])

    tmp = os.path.join(LOOK, '_tmp_' + tag)
    print(f'⚑ {os.path.basename(mp4)} → {tag}   {n} 帧 · pick={mode} · 窗口 {win} · {fps:.0f}fps')
    fs = grab(mp4, tmp)
    print(f'  ⚑ 拆出 {len(fs)} 帧')
    cells = process(pick(fs, n, mode, win, fps), tmp)
    _fz = opt('--freeze', 'off')
    if _fz in ('body', 'legs'):
        cells = freeze_body(cells, _fz)

    # 自检 ②：水印区真的没了。
    # ⚠⚠ **只在真的裁过水印时才查**（2026-08-31 崩山击）—— ⚑ 这个自检是验「裁干净没有」，
    #   ⛔ 没开 --wmcut 就等于在验一件没做过的事。
    #   ⚠ 而且它是照 192×256 的走路格子标定的：⚑ 走路时右下角必然是空的，
    #     ⛔ 但技能格子（448×432）那里是甩在后面的靴子/衣摆/刀 ⇒ ⚠ 必然误报，
    #     ⚑ 实测崩山击报「21 个残留 ⛔ 别用」，⚠ 而万相 API 根本不打水印（§11.2 ⑥）。
    #   ⚑⚑ 误报比不报更坏：⛔ 狼来了几次之后，真出问题时就没人看这行了。
    if float(str(opt('--wmcut', WM_CUT)).replace('off', '0')) > 0:
        a0 = np.asarray(cells[0])[:, :, 3]
        left = int((a0[int(CELL[1] * .93):, int(CELL[0] * .70):] > 16).sum())
        print(f'  ⚑ 自检 水印区不透明像素 {left} 个 ⇒ {"✅" if left == 0 else "⚠⚠ 还有残留，⛔ 别用"}')

    # ⚑⚑ 自检 ③：**有没有贴边**（⚑ 崩山击新增）—— ⚠ 原来一个都没有，
    #   ⛔ 而「刀被格子平齐切断」正是技能动画最容易出、且**看指标完全看不出来**的故障。
    for i, c in enumerate(cells):
        m = np.asarray(c)[:, :, 3] > 16
        e = int(m[0, :].sum() + m[-1, :].sum() + m[:, 0].sum() + m[:, -1].sum())
        if e:
            print(f'  ⚠⚠ 第 {i+1} 格**贴边 {e} px** ⇒ ⛔ 内容被格子切了，⚑ 放大 --cell 再切')

    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new('RGBA', (CELL[0] * min(cols, len(cells)), CELL[1] * rows), (0, 0, 0, 0))
    for i, c in enumerate(cells):
        sheet.paste(c, (i % cols * CELL[0], i // cols * CELL[1]))
    os.makedirs(DST, exist_ok=True)
    p = os.path.join(DST, f'{tag}.png')
    sheet.save(p)
    print(f'  ✅ {os.path.relpath(p, ROOT)}  {sheet.size[0]}x{sheet.size[1]}  '
          f'{os.path.getsize(p)//1024} KB  ({min(cols,len(cells))}列 × {rows}行)')

    # ⚑ 出 GIF —— ⚠ 「在动没在动」静态图看不出来
    os.makedirs(LOOK, exist_ok=True)
    g = []
    for c in cells:
        b = Image.new('RGB', CELL, (30, 28, 36))
        b.paste(c, (0, 0), c)
        g.append(b.resize((CELL[0] * 2, CELL[1] * 2), Image.NEAREST))
    gp = os.path.join(LOOK, f'{tag}_看.gif')
    g[0].save(gp, save_all=True, append_images=g[1:], duration=420, loop=0, optimize=False)
    print(f'  ⚑ {os.path.relpath(gp, ROOT)}（{len(g)} 帧循环）')
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
