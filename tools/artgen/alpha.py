#!/usr/bin/env python3
"""把黑底生成图转成带 alpha 通道的 PNG。

为什么需要这一步：AI 生成不出真正的透明背景。提示词里直接写"透明背景"，
模型只会**画出一张棋盘格图案**——看着像透明，实际是 RGB 图，没有 alpha 通道，
拿进 Unity 就是一块带格子的方板。

所以提示词一律要求纯黑背景，再由这里把黑色转成 alpha：

    alpha = max(r, g, b)

对发光线条、HUD 边框这类素材，这个转换比抠棋盘格干净得多，
半透明玻璃面板也能自然地保留成半透明。

用法：
    python alpha.py                处理 out/ 里所有标记为 transparent 的图
    python alpha.py 4 7            只处理指定编号
    python alpha.py --mode flood   实心物体用这个（只抠掉与画面边缘连通的黑）
    python alpha.py --preview      额外导出贴在浅灰背景上的对照图，检查边缘干不干净

输出在 out_alpha/，原图不动。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
# 跟 gen.py 一样认当前工作目录，不是脚本所在目录
SRC_DIR = Path.cwd() / "out"
DST_DIR = Path.cwd() / "out_alpha"

# 低于这个亮度就当纯背景抹掉，否则整张图会蒙一层几乎看不见的灰底噪
FLOOR = 0.02


def alpha_by_luma(rgb: np.ndarray) -> np.ndarray:
    """按亮度取 alpha。适合发光线条、边框这类"亮的部分才是内容"的图。"""
    a = rgb.max(axis=2)
    return np.where(a < FLOOR, 0.0, a)


def alpha_by_flood(path: Path) -> np.ndarray:
    """只抠掉与画面边缘连通的黑色，物体内部的暗部保住。

    实心物体（比如深色金属方块）用亮度法会把本体也弄成半透明，
    这时候就得靠连通性判断哪些黑是背景、哪些黑是物体自己的暗部。
    """
    im = Image.open(path).convert("RGB")
    w, h = im.size
    KEY = (255, 0, 255)          # 一个原图里不可能出现的颜色，用来标记背景
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(im, seed, KEY, thresh=28)
    arr = np.asarray(im)
    bg = (arr[..., 0] == 255) & (arr[..., 1] == 0) & (arr[..., 2] == 255)
    return np.where(bg, 0.0, 1.0)


def convert(path: Path, mode: str) -> Image.Image:
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    a = alpha_by_flood(path) if mode == "flood" else alpha_by_luma(rgb)

    # 生成图相当于已经"预乘"在黑底上了。除回去才是直通 alpha，
    # 否则半透明边缘贴到浅色背景上会发暗，看着像一圈脏边。
    out_rgb = np.clip(rgb / np.maximum(a[..., None], 1e-4), 0.0, 1.0)
    out_rgb = np.where(a[..., None] > 0, out_rgb, 0.0)

    rgba = np.dstack([out_rgb, a])
    return Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8), "RGBA")


def make_preview(img: Image.Image) -> Image.Image:
    """贴到浅灰背景上，一眼就能看出有没有残留的黑边或没抠净的底。"""
    bg = Image.new("RGBA", img.size, (200, 205, 212, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description="黑底转 alpha")
    ap.add_argument("ids", nargs="*", type=int, help="只处理这些编号，留空表示全部")
    ap.add_argument("--mode", choices=("luma", "flood"), default="luma",
                    help="luma=按亮度（发光线条/边框），flood=只抠边缘连通的黑（实心物体）")
    ap.add_argument("--preview", action="store_true", help="额外导出浅灰底对照图")
    # 跟 gen.py 的 --prompts/--out 对称：上位机那套素材条目和产物都在别处
    ap.add_argument("--prompts", help="换一份条目文件，默认 prompts.json")
    ap.add_argument("--src", help="换一个输入目录，默认 out/")
    ap.add_argument("--dst", help="换一个输出目录，默认 out_alpha/")
    args = ap.parse_args()

    global SRC_DIR, DST_DIR
    if args.src:
        SRC_DIR = Path(args.src).resolve()
    if args.dst:
        DST_DIR = Path(args.dst).resolve()

    prompts_file = Path(args.prompts).resolve() if args.prompts else Path.cwd() / "prompts.json"
    if prompts_file.exists():
        items = json.loads(prompts_file.read_text(encoding="utf-8"))["items"]
        items = [it for it in items if it.get("transparent")]
        if args.ids:
            items = [it for it in items if it["id"] in set(args.ids)]
        srcs = [SRC_DIR / f"{it['id']:02d}_{it['name']}.png" for it in items]
    else:
        # 没有条目表就把 src 目录里的 PNG 全处理一遍。散图、别人给的图、
        # 单张试手都走这条路，不必为了抠一张图先编一份 prompts.json。
        print(f"[i] 没找到 {prompts_file.name}，改为处理 {SRC_DIR} 下全部 PNG")
        srcs = sorted(p for p in SRC_DIR.glob("*.png") if not p.name.startswith("_"))

    DST_DIR.mkdir(parents=True, exist_ok=True)
    done = missing = 0
    for src in srcs:
        if not src.exists():
            missing += 1
            continue
        img = convert(src, args.mode)
        dst = DST_DIR / src.name
        img.save(dst)
        if args.preview:
            make_preview(img).save(DST_DIR / f"{src.stem}_preview.png")
        # 报一下抠掉了多少，全 0 或全 1 都说明这张图不对劲
        a = np.asarray(img)[..., 3]
        print(f"  {src.name}  透明像素 {100.0 * (a == 0).mean():.1f}%  "
              f"不透明 {100.0 * (a == 255).mean():.1f}%")
        done += 1

    print(f"\n处理 {done} 张，输出 {DST_DIR}")
    if missing:
        print(f"还有 {missing} 张没生成，先跑 gen.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
