# -*- coding: utf-8 -*-
"""⚑ demo 模式的素材合成器 —— ⚑ `python synth.py walk|attack <dst.mp4>`，⛔ 不调任何 API。

⚑ 走路直接复用 tools/demo.py 的火柴人（⚑ 含身体起伏 ＋ 缓慢漂移，⚑ 两条都是让 loop 模式量准周期的必要条件）。
⚑ 攻击画一次完整挥砍：⚑ 蓄力（武器拉到身后）→ 挥出（划弧到身前）→ 停住 → 收回站姿。
"""
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
sys.path.insert(0, TOOLS)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BG = (152, 152, 152)


def attack_frames(tmp, n=60):
    from PIL import Image, ImageDraw
    os.makedirs(tmp, exist_ok=True)
    for k in range(n):
        t = k / n
        # ⚑ 四段：蓄力 0~.25 · 挥出 .25~.5 · 停住 .5~.65 · 收回 .65~1
        if t < .25:
            ang, lunge = -110 * (t / .25), 0                      # 拉到身后（右上）
        elif t < .5:
            u = (t - .25) / .25
            ang, lunge = -110 + 190 * (u * u), -40 * u            # 加速挥到身前（左下），身体前冲
        elif t < .65:
            ang, lunge = 80, -40
        else:
            u = (t - .65) / .35
            ang, lunge = 80 - 80 * u, -40 * (1 - u)
        dr = int(t * 10)
        im = Image.new('RGB', (640, 640), BG)
        d = ImageDraw.Draw(im)
        cx = 320 + int(lunge)
        d.ellipse([cx - 45, 155, cx + 45, 245], fill=(40 + dr, 40 + dr, 60 + dr))
        d.rectangle([cx - 30, 245, cx + 30, 420], fill=(60 + dr, 50 + dr, 80 + dr))
        for s in (-1, 1):                                          # 弓步：前腿在左
            dx = -30 if s < 0 else 22
            d.line([(cx, 420), (cx + dx, 520)], fill=(50 + dr, 45 + dr, 70 + dr), width=18)
            d.rectangle([cx + dx - 16, 512, cx + dx + 18, 530], fill=(30 + dr, 28 + dr, 40 + dr))
        # ⚑ 武器：从手（cx-20, 300）出发，⚑ 角度 -110°(身后上方) → 80°(身前下方)，⚑ 朝左为正方向
        a = math.radians(ang)
        hx, hy = cx - 20, 300
        bx, by = hx - int(120 * math.cos(a)), hy - int(120 * math.sin(a))
        d.line([(hx, hy), (bx, by)], fill=(200 + min(dr, 40), 200 + min(dr, 40), 215), width=12)
        d.line([(hx, hy), (hx - int(30 * math.cos(a)), hy - int(30 * math.sin(a)))],
               fill=(90, 60, 40), width=16)                        # 刀柄
        im.save(os.path.join(tmp, 'f%03d.png' % k))


def main():
    kind, dst = sys.argv[1], sys.argv[2]
    if not shutil.which('ffmpeg'):
        print('✗ 找不到 ffmpeg'); sys.exit(1)
    tmp = tempfile.mkdtemp(prefix='synth_')
    if kind == 'walk':
        import demo
        demo.make_frames(tmp)
    else:
        attack_frames(tmp)
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or '.', exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', os.path.join(tmp, 'f%03d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', dst], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'  ✅ {dst}（合成 · {kind}）')


if __name__ == '__main__':
    main()
