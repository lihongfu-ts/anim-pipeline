# -*- coding: utf-8 -*-
"""⚑⚑⚑ **MCP server** —— ⚑ 把管线的每一步暴露成 MCP 工具，⚑ 任何支持 MCP 的 harness 都能挂：
DeepSeek Harness（dsh）/ Claude Code / Cline / Continue …。⚑ 脑子由 harness 提供，⚑ 这里只有「手」和「尺」。

```bash
pip install mcp
python mcp/server.py                       # stdio
# Claude Code:  claude mcp add anim-pipeline -- python D:/anim-pipeline/mcp/server.py
# dsh:          见 dsh-plugin/README.md（⚑ 原生插件）或 dsh 的 MCP 配置
```

⚑ 数据根 ＝ 环境变量 ANIMPIPE_ROOT（⚑ 没设就是当前目录）：⚑ 产物落 work/anim、out/anim。
⚑ 收费工具（gen_portrait / gen_video 走万相）**不自己拦**：⚑ harness 的审批机制负责——⚑ 但工具说明里写清了单价，
  ⚑ 让模型在花钱前知道数。⚑ 视觉能力（看联络表）由 harness 自己的多模态提供 ⇒ ⚑ contact_sheet 返回图路径给它看。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'tools'
WEB = HERE.parent / 'web'
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(TOOLS))
import pipeline  # noqa: E402  ⚑ 复用单价 / 提示词模板 / 解析器
import _creds  # noqa: E402

try:                                                   # ⚑ mcp 2.x：FastMCP 改名 MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: E402
except ImportError:                                    # ⚑ mcp 1.x
    from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP('anim-pipeline')
TOOL_FUNCS = {}          # ⚑ 原函数表 —— ⚑ 给 `server.py call <tool>` 用（⚑ dsh 插件走这条，⛔ 不经 MCP）


def tool(fn):
    TOOL_FUNCS[fn.__name__] = fn
    return mcp.tool()(fn)


def root() -> Path:
    return Path(os.environ.get('ANIMPIPE_ROOT') or os.getcwd()).resolve()


def sh(argv, env=None, timeout=1800):
    e = {**os.environ, 'ANIMPIPE_ROOT': str(root()), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1', **(env or {})}
    p = subprocess.run([sys.executable] + [str(a) for a in argv], cwd=str(root()), env=e,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


@tool
def doctor() -> dict:
    """环境 / 凭据 / 能做什么 / 单价。开始任何工作前先调它。"""
    import shutil
    have, can = _creds.probe()
    return {'root': str(root()), 'ffmpeg': bool(shutil.which('ffmpeg')),
            'creds': {k: bool(v) for k, v in have.items()}, 'can': can,
            'prices': {'portrait_yuan': pipeline.PRICE_PORTRAIT,
                       'video_yuan': {f'{p}|{r}|{d}s': v for (p, r, d), v in pipeline.PRICE_VIDEO.items()}},
            'rules': '定稿用 dashscope（480P/2s ¥0.40，钉首尾帧）；zhipu cogvideox-3 也支持首尾帧（标价 ¥1/次，买包约 ¥0.1）；ark 免费额度但会重画角色只能抽姿势；一次一发；每动作最多 2 发'}


@tool
def gen_portrait(desc: str, style: str = '') -> dict:
    """出立绘（收费 ¥0.15/发，走 relay 中转站）。desc 只写角色外观，不写雾气/光晕/特效。返回 portrait 路径。"""
    c = _creds.get('relay')
    if not c:
        return {'error': '没配 relay（中转站）'}
    item = {'id': 1, 'name': 'portrait', 'desc': '立绘', 'size': '1024x1024', 'transparent': False, 'quality': 'medium',
            'prompt': pipeline.PORTRAIT_TMPL.format(desc=desc.strip('。 '), style=style or pipeline.DEFAULT_STYLE)}
    (root() / 'prompts.json').write_text(json.dumps({'items': [item]}, ensure_ascii=False), encoding='utf-8')
    im = c.get('model')                                   # ⚑ 图像模型名从 relay 凭据取（各家中转站不同）
    code, out = sh([TOOLS / 'artgen' / 'gen.py', '1', '--force'] + (['--model', im] if im else []),
                   {'OPENAI_API_KEY': c['key'], 'OPENAI_BASE_URL': c['base']})
    ok = code == 0 and (root() / 'out' / '01_portrait.png').exists()
    return {'portrait': 'out/01_portrait.png', 'cost_yuan': pipeline.PRICE_PORTRAIT} if ok else {'error': out[-800:]}


@tool
def edit_portrait(src: str, instruction: str) -> dict:
    """在已有立绘上图生图改版（换武器 / 换装，姿势比例不变；收费 ¥0.15）。换了立绘后所有动作都要按新立绘重出。"""
    c = _creds.get('relay')
    if not c:
        return {'error': '没配 relay（中转站）'}
    (root() / 'prompt_edit.txt').write_text(instruction + '\n保持人物的姿势、体型比例、朝向、构图和背景颜色完全不变，只修改上面提到的部分。', encoding='utf-8')
    im = c.get('model')
    code, out = sh([TOOLS / 'artgen' / 'edit.py', src, 'out/01_portrait.png', '--promptfile=prompt_edit.txt'] + ([f'--model={im}'] if im else []),
                   {'OPENAI_API_KEY': c['key'], 'OPENAI_BASE_URL': c['base']})
    return {'portrait': 'out/01_portrait.png', 'cost_yuan': pipeline.PRICE_PORTRAIT} if code == 0 else {'error': out[-800:]}


@tool
def make_liubai(src: str, dst: str = 'liubai.png') -> dict:
    """立绘 → 留白图（角色占 55%，底色从图里量）。出片的唯一合法输入；满画幅立绘直接出片会四边出画。"""
    code, out = sh([TOOLS / 'make_liubai.py', src, dst])
    return {'liubai': dst, 'log': out[-400:]} if code == 0 else {'error': out[-600:]}


@tool
def gen_video(action: str, motion: str, liubai: str = 'liubai.png', provider: str = 'dashscope',
              res: str = '480P', dur: int = 2, model: str = '') -> dict:
    """图生视频。motion 只写运动段（要看到的姿态和方向、该动的部位点名+幅度、结尾回站姿）；构图约束和禁止句由模板补。
    --img 和 --last 都给留白图 ⇒ 首尾帧同图，动作间不需要过渡帧。
    单价：dashscope 480P/2s ¥0.40、480P/5s ¥1.05、720P/5s ¥2.10；zhipu / ark 免费。任务 id 落盘即扣费。"""
    model = model or pipeline.VIDEO_PROVIDERS.get(provider, {}).get('model', '')
    price = pipeline.video_price(provider, res, int(dur), model)
    pf = f'prompt_{action}.txt'
    (root() / pf).write_text(pipeline.build_prompt(action if action in pipeline.ACTIONS else 'attack', '', motion), encoding='utf-8')
    (root() / 'work' / 'anim').mkdir(parents=True, exist_ok=True)
    use_last = pipeline.VIDEO_PROVIDERS.get(provider, {}).get('last', True)     # ⚑ 智谱不给首尾帧（给了就静止）
    code, out = sh([TOOLS / 'gen_video.py', f'--img={liubai}'] + ([f'--last={liubai}'] if use_last else []) +
                   [f'--tag={action}', f'--promptfile={pf}', f'--provider={provider}', f'--model={model}', f'--res={res}', f'--dur={int(dur)}'],
                   timeout=1800)
    mp4 = f'work/anim/{action}.mp4'
    spent = 'id 已落盘' in out
    if code != 0 or not (root() / mp4).exists():
        return {'error': out[-800:], 'cost_yuan': price if spent else 0.0}
    return {'mp4': mp4, 'cost_yuan': price if spent else 0.0, 'prompt_file': pf}


@tool
def synth_demo(action: str, kind: str = 'attack') -> dict:
    """不花钱的合成素材（kind = walk | attack），用来验证后半段链路。"""
    (root() / 'work' / 'anim').mkdir(parents=True, exist_ok=True)
    mp4 = f'work/anim/{action}.mp4'
    code, out = sh([WEB / 'synth.py', kind, mp4])
    return {'mp4': mp4} if code == 0 else {'error': out[-400:]}


@tool
def contact_sheet(mp4: str, n: int = 24) -> dict:
    """带帧号的联络表（挑帧靠它，别凭感觉）。返回 png 路径 + 总帧数；用你的视觉能力看它，帧号是 1 起、和 vid2anim 的 at 同一套。"""
    tag = Path(mp4).stem
    png = f'work/anim/{tag}_联络表{n}.png'
    code, out = sh([TOOLS / '_contact.py', mp4, f'--n={n}', f'--cols={6 if n > 12 else 4}', f'--out={png}'])
    m = re.search(r'共 (\d+) 帧', out)
    return {'png': png, 'abs': str(root() / png), 'total': int(m.group(1)) if m else None} if code == 0 else {'error': out[-400:]}


@tool
def vid2anim(mp4: str, tag: str, frames: int = 4, pick: str = 'loop', cell: str = '192x256', at: str = '') -> dict:
    """视频 → 序列帧图集（抠灰底 / 对齐 / 归一 / 切格）。走路 frames=4 pick=loop；一次性动作 frames=6 pick=even 并给 at="12,22,30,34,38,52"。
    形变 ≈ 0 说明挑到的帧全一样（loop 周期误判）——改 frames 或改用 at。"""
    argv = [TOOLS / 'vid2anim.py', mp4, f'--tag={tag}', f'--frames={int(frames)}', f'--cols={int(frames)}', f'--pick={pick}', f'--cell={cell}']
    if at:
        argv.append(f'--at={at}')
    code, out = sh(argv)
    if code != 0:
        return {'error': out[-600:]}
    info = pipeline.parse_vid2anim(out)
    cw, ch = (int(v) for v in cell.lower().split('x'))
    return {'sheet': f'out/anim/{tag}.png', 'gif': f'work/anim/{tag}_看.gif', 'info': info,
            'foot_trim': pipeline.foot_trims(root() / 'out' / 'anim' / f'{tag}.png', (cw, ch), int(frames), int(frames))}


@tool
def anim_bench(sheet: str, cell: str = '192x256') -> dict:
    """五项验收指标：动量 / 形变% / 循环缝 / 剪影密度% / 亮度漂。循环缝 >1.5 循环会跳；亮度漂 >10 多半是崩了；剪影密度 <50 缩小后散架。
    配了基准素材（ANIMPIPE_REF）才有合格区间。"""
    code, out = sh([TOOLS / 'anim_bench.py', f'--sheet={sheet}', f'--cell={cell}'])
    r = pipeline.parse_bench(out)
    return {'metrics': r.get('ours'), 'has_ref': r.get('has_ref'), 'log': out[-900:]}


@tool
def move_check(mp4: str) -> dict:
    """逐帧量位移 / 抬升（换算成游戏像素）。走路应 ≈ 0；代码位移只能补动画里已有的位移。"""
    code, out = sh([TOOLS / '_move_check.py', mp4, '--every=10'])
    return pipeline.parse_move(out) or {'error': out[-400:]}


@tool
def blade_check(mp4: str) -> dict:
    """逐帧查「刀变细白线」（刃口转向镜头，指标看不出来的那类）。不过就用 at 避开那些帧号重新 vid2anim，不用重出片。"""
    code, out = sh([TOOLS / '_blade_check.py', mp4])
    return {'ok': code == 0, 'log': out[-800:]}


@tool
def anim_check(tag: str, cell_w: int, foot_trim: int, hit: int, facing: str = 'left') -> dict:
    """图集自检：命中格武器朝向 / 脚底一致 / 贴边 / footTrim 配对。攻击类动作专用（走路别用，会把手臂当刀）。
    「武器朝后」→ 换 hit；「贴边」→ 放大 cell 重切；foot_trim 传 vid2anim 返回的中位数。"""
    code, out = sh([TOOLS / '_anim_check.py', f'{tag}:{int(cell_w)}:{int(foot_trim)}:{int(hit)}', f'--facing={facing}'])
    r = pipeline.parse_anim_check(out)
    r['log'] = out[-800:]
    return r


@tool
def export_zip(tags: str, out_zip: str = 'anim_export.zip') -> dict:
    """把若干动作（逗号分隔 tag）的图集 + 逐帧 PNG + gif 打成 zip，附 manifest（格子 / 帧数 / footTrim / holds）。"""
    import zipfile
    from PIL import Image
    r = root()
    man = {'animations': {}}
    with zipfile.ZipFile(r / out_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for tag in [t.strip() for t in tags.split(',') if t.strip()]:
            sheet = r / 'out' / 'anim' / f'{tag}.png'
            if not sheet.exists():
                continue
            im = Image.open(sheet).convert('RGBA')
            z.write(sheet, f'{tag}/{tag}_sheet.png')
            gif = r / 'work' / 'anim' / f'{tag}_看.gif'
            if gif.exists():
                z.write(gif, f'{tag}/{tag}_preview.gif')
            n = max(1, im.width // 192) if im.height == 256 else 1
            man['animations'][tag] = {'sheet': f'{tag}/{tag}_sheet.png', 'size': [im.width, im.height]}
    (r / 'manifest.json').write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'zip': out_zip, 'animations': list(man['animations'])}


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'call':
        # ⚑ dsh 插件 / 任何壳：`python mcp/server.py call <tool>`，⚑ 参数 JSON 走 stdin（⛔ 别走 argv，Windows 引号会炸）
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
        fn = TOOL_FUNCS.get(sys.argv[2])
        if not fn:
            print(json.dumps({'error': f'没有工具 {sys.argv[2]}', 'tools': list(TOOL_FUNCS)}, ensure_ascii=False))
            sys.exit(2)
        raw = sys.stdin.read().strip()
        try:
            args = json.loads(raw) if raw else {}
            print(json.dumps(fn(**args), ensure_ascii=False, default=str))
        except TypeError as e:
            print(json.dumps({'error': f'参数不对：{e}'}, ensure_ascii=False))
            sys.exit(2)
        except Exception as e:
            print(json.dumps({'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False))
            sys.exit(1)
    else:
        mcp.run()
