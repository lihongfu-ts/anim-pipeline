# -*- coding: utf-8 -*-
"""⚑⚑⚑ **零成本跑通后半段管线** —— ⛔ 不用 API key、⛔ 不用注册、⛔ 不花一分钱。

```bash
python tools/demo.py              # ⚑ 产物落当前目录的 work/ 和 out/
```

⚑ 它自己合成一段「走路」视频（⚑ 中性灰底 ＋ 一个火柴人），⚑ 然后走**真实的那条链**：

```
合成视频 → 抽帧 → 抠灰底 → 按脚底/头部重心对齐 → 按核心面积归一 → 序列帧图集 → 验收打分
         └─────────────── ⚑ 这一整段和你拿真视频跑的是**同一份代码** ───────────────┘
```

⚠ 它**⛔ 不能**替你回答「AI 出的片好不好」—— ⚑ 前半段（立绘 → 提示词 → 出片）才是这条链
  最贵、最需要判断力的部分，⚑ 而那一段必须花钱。⚑ 这份只证明**你的环境是通的**、
  ⚑ 并让你先看清楚**产物长什么样**。
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _env import need_ffmpeg, need_pkgs  # noqa: E402

try:
    # ⚑ 幂等，⛔ 别改成重新包 buffer。⚑ line_buffering：⚠ 不加的话本进程的 print 会被块缓冲，
    #   ⛔ 子进程的输出先冲出来 ⇒ ⚑ 步骤标题和它的结果**对不上号**（⚑ 管道/重定向时必现）。
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass

ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
WORK = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')
OUT = os.environ.get('ANIMPIPE_OUT') or os.path.join(ROOT, 'out', 'anim')

BG = (152, 152, 152)   # ⚑ 中性灰底 —— ⚠ ⛔ 别用 #808080（⚑ 抠图是**量**出来的，不是猜的）
PERIOD = 20            # ⚑ 一个步态周期
# ⚠⚠ **⛔ 别只给一个周期** —— ⚑ `--pick=loop` 要「先量周期、再按周期钉死段长」去搜循环点，
#   ⛔ 素材只有一个周期时它没有搜索余量 ⇒ ⚑ 会挑中腿并拢的那几帧（⚠ 4 帧全一样、指标全 0）。
#   ⚑ 60 帧 ＝ 3 个周期 ＝ 2 秒 @30fps —— ⚑ 正好和万相 `--dur=2` 出的产物一个量级。
N = PERIOD * 3


def make_frames(tmp):
    """⚑ 画一个会走路的火柴人 —— ⚑ 要素：⚑ 灰底、⚑ 居中、⚑ 腿真的在动、⚑ 身体有起伏。

    ⚠⚠ **身体起伏（bob）⛔ 不是为了好看** —— ⚑ `--pick=loop` 要求「段内低活跃帧 ≤15%」，
      ⚑ 而只摆腿的话，⚠ 两腿并拢那几帧**整幅画面几乎不变** ⇒ ⛔ 低活跃帧超标
      ⇒ ⚠ 一个合法起点都没有 ⇒ ⛔ 退化成「4 张同帧、指标全 0」。
      ⚑ 真实视频不会这样（⚑ 头发/衣服/光影一直在动）—— ⚑ 这里补上走路本就有的上下起伏。
    """
    from PIL import Image, ImageDraw
    os.makedirs(tmp, exist_ok=True)
    for k in range(N):
        im = Image.new('RGB', (640, 640), BG)
        d = ImageDraw.Draw(im)
        ph = math.sin(k / PERIOD * 2 * math.pi)            # ⚑ 按**周期**算，⛔ 不是按总帧数
        bob = int(6 * math.sin(k / PERIOD * 4 * math.pi))  # ⚑ 一个步态周期上下起伏**两次**
        # ⚠⚠⚠ **缓慢亮度漂移 ⛔ 不是装饰** —— ⚑ 真实视频一定带漂移（⚑ vid2anim 文件头③：
        #   ⚑ 实测 5.1 秒角色长高 12%）。⚠ 而纯合成素材是**完美周期**：⛔ 2L 和 L 一样相似
        #   ⇒ ⚑ loop 的「全周期优先」判定会把 20 帧误翻成 40 帧
        #   ⇒ ⚠ 取 4 帧时**间隔正好是半周期** ⇒ ⛔ 4 张全是「两腿并拢」，形变 0.00%。
        #   ⚑ 补一点漂移，⚑ 让隔得越远越不相似 ⇒ ⚑ 周期就量准了。
        dr = int(k / N * 10)
        d.ellipse([275, 155 + bob, 365, 245 + bob], fill=(40 + dr, 40 + dr, 60 + dr))     # 头
        d.rectangle([290, 245 + bob, 350, 420 + bob], fill=(60 + dr, 50 + dr, 80 + dr))   # 躯干
        for s in (-1, 1):                                                    # 两条腿反相摆
            dx = int(26 * ph * s)
            # ⚑ 胯随身体起伏、⚑ 脚踩死在地面 —— ⚠ 脚底⛔ 不能跟着抖（⚑ 整条链靠脚底对齐）
            d.line([(320, 420 + bob), (320 + dx, 520)], fill=(50 + dr, 45 + dr, 70 + dr), width=18)
            d.rectangle([320 + dx - 16, 512, 320 + dx + 18, 530], fill=(30 + dr, 28 + dr, 40 + dr))
        d.line([(300, 300 + bob), (300 - int(30 * ph), 380 + bob)],
               fill=(70 + dr, 60 + dr, 90 + dr), width=14)                   # 手臂
        im.save(os.path.join(tmp, 'f%03d.png' % k))


def run(title, argv):
    print(f'\n───── {title} ' + '─' * max(0, 46 - len(title)))
    print('  $ python ' + ' '.join(os.path.basename(a) if a.endswith('.py') else a for a in argv))
    r = subprocess.run([sys.executable] + argv, cwd=ROOT)
    if r.returncode != 0:
        print(f'✗ 这一步失败了（退出码 {r.returncode}）')
        sys.exit(1)


def main():
    need_pkgs()
    need_ffmpeg()
    print('⚑ 零成本 demo —— ⛔ 不调任何 API，⚑ 只跑「视频 → 序列帧图集」这后半段')
    print(f'⚑ 工作目录 {ROOT}')

    os.makedirs(WORK, exist_ok=True)
    tmp = os.path.join(WORK, '_demo_src')
    mp4 = os.path.join(WORK, 'demo_walk.mp4')

    print('\n───── ① 合成一段走路视频（⚑ 代替「图生视频」那一步）' + '─' * 6)
    make_frames(tmp)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30',
                    '-i', os.path.join(tmp, 'f%03d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', mp4], check=True)
    print(f'  ✅ {os.path.relpath(mp4, ROOT)}  ({N} 帧 / 30fps)')

    run('② 联络表：带帧号看整段（⚑ 挑帧靠它，⛔ 别凭感觉）',
        [os.path.join(HERE, '_contact.py'), mp4])

    run('③ 核心：视频 → 序列帧图集',
        [os.path.join(HERE, 'vid2anim.py'), mp4, '--tag=demo_walk',
         '--frames=4', '--pick=loop'])

    # ⚑ ⛔ 这里**不跑** `_anim_check.py` —— ⚑ 它判「命中帧的武器朝向」，⚑ 是**攻击动画**专用，
    #   ⚠ 拿来判走路会把摆动的手臂当成刀 ⇒ ⛔ 报「武器朝后」。⚑ 走路该量的是**位移**。
    run('④ 位移量化（⚑ 原地走 ⇒ Δx 应 ≈ 0；⚑ 真跃出去才给代码位移）',
        [os.path.join(HERE, '_move_check.py'), mp4, '--every=15'])

    run('⑤ 验收打分（⚠ 没配基准素材时只出自己的数，⚑ 见 README）',
        [os.path.join(HERE, 'anim_bench.py'),
         f'--sheet={os.path.relpath(os.path.join(OUT, "demo_walk.png"), ROOT)}',
         '--cell=192x256'])

    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)

    print('\n' + '═' * 56)
    print('✅ 跑通了。⚑ 去看这两个：')
    print(f'   {os.path.relpath(os.path.join(OUT, "demo_walk.png"), ROOT)}      ⚑ 序列帧图集（⚑ 这就是接进游戏的东西）')
    print(f'   {os.path.relpath(os.path.join(WORK, "demo_walk_看.gif"), ROOT)}   ⚑ 循环预览')
    print('\n⚑ 下一步：⚑ 换成你自己的立绘走一遍 —— ⚠ 那一步要配 key，⚑ 先跑 python tools/doctor.py')


if __name__ == '__main__':
    main()
