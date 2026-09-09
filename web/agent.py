# -*- coding: utf-8 -*-
"""⚑⚑⚑ **DeepSeek 当大脑的 agent** —— ⚑ 脑：文本模型规划 / 写提示词 / 读数 / 决定下一步；⚑ 眼：视觉模型看联络表；
⚑ 手：tools/ 里的脚本；⚑ 尺：anim_bench 五项区间。⚑ 一个 DeepSeek key 就能跑，⛔ 不依赖任何 IDE / MCP。

⚑ 协议：**纯 JSON**。⚑ 模型每步只回一个对象 `{"thought": "...", "tool": "<名>", "args": {...}}`，
  ⚑ 运行时执行工具、把结果作为下一条用户消息喂回去。⚑ 不用各家不一致的 function calling ⇒ ⚑ GLM / Qwen / 任意
  OpenAI 兼容端点换个 base 就能当脑子。

⚑ 四道闸（⚑ 全部来自管线文档 §8.4 / §3.2 / §5）：
  ① 收费工具先停下来问用户（⚑ 预算内且用户勾了"自动批准"才放行）  ② 每个动作最多出 2 发
  ③ 崩坏判定来自视觉模型 ⇒ 按「换 provider / 改禁止句 / 问人」路由，⛔ 不盲目重试  ④ 总步数上限
"""
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'tools'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TOOLS))
import pipeline  # noqa: E402
import _creds  # noqa: E402

MAX_STEPS = 28
MAX_SHOTS_PER_ACTION = 2

TOOLS_DOC = """可用工具（args 全是 JSON 对象；路径相对任务目录）：
- doctor {}                                     环境 / 凭据 / 能做什么 / 单价
- gen_portrait {"desc": "..."}                   出立绘（¥0.15，收费需确认）→ {"portrait": path}
- edit_portrait {"src": path, "instruction": "..."}  在已有立绘上图生图改版（¥0.15）→ {"portrait": path}
- make_liubai {"src": path}                      立绘 → 留白图 → {"liubai": "liubai.png"}
- gen_video {"action": tag, "motion": "运动段文字", "provider": "dashscope|zhipu|ark", "res": "480P", "dur": 2}
                                                 出片（dashscope 收费需确认；zhipu/ark 免费）→ {"mp4": path, "cost": 元}
                                                 运行时自动：--img 和 --last 都给留白图、把 motion 包进四段模板
- contact_sheet {"mp4": path, "n": 24}           带帧号联络表 → {"png": path, "total": 总帧数}
- pick_frames {"png": path, "motion": "...", "n": 6, "total": 60}
                                                 视觉模型看联络表 → {"frames": [...], "collapse": bool, "thin_weapon": [...], "notes"}
- vid2anim {"mp4": path, "tag": tag, "frames": 4, "pick": "loop|even", "cell": "192x256", "at": "27,60,76" 可选}
                                                 视频 → 图集 → {"sheet": path, "info": {...period, loop, no_loop...}}
- anim_bench {"sheet": path, "cell": "192x256"}  五项指标 → {"metrics": {...}}
- move_check {"mp4": path}                       位移 / 抬升（走路应 ≈ 0）
- blade_check {"mp4": path}                      查"刀变细线" → {"ok": bool}
- anim_check {"tag": tag, "hit": 命中格号}         图集自检（朝向 / 脚底一致 / 贴边）→ {"pass": bool, "issues": [...]}
- ask_user {"question": "..."}                   问用户（暂停等回答）
- finish {"summary": "...", "animations": {tag: {"sheet": path, "cell": "WxH", "frames": n, "loop": bool}}}
"""

SYSTEM = """你是「AI 2D 角色动作动画管线」的操作员。目标由用户给。你每一步只输出**一个 JSON 对象**：
{"thought": "一两句为什么", "tool": "工具名", "args": {...}}
不要输出别的文字。工具结果会作为下一条消息给你。

## 分工
你（DeepSeek）负责**写运动段提示词、读数、看图结果、路由、决定重出还是换路线**。GLM 只用来免费抽片探方向（provider=zhipu），
定稿用 dashscope（万相）。

## 标准流程
generate 模式：立绘（gen_portrait，或用户已给 portrait）→ make_liubai → 每个动作：gen_video → contact_sheet →
（一次性动作先 pick_frames 拿帧号）→ vid2anim → anim_bench →（walk: move_check；攻击类: blade_check + anim_check）→ finish。
demo 模式：没有立绘这一步，gen_video 会用合成素材、不花钱，其余一样——用来验证流程。

## 出片规则（来自实测）
- 定稿用 provider=dashscope（万相，480P/2s ¥0.40，能钉首尾帧）。zhipu：cogvideox-flash 免费但可能已下线、cogvideox-3 ¥1/次（比万相贵）、vidu2 ¥1.25；
  ark 免费额度但会重画角色（只能抽姿势不能定稿）。doctor 会给单价，按单价选。
- 一次只出一发；每个动作最多 2 发；收费工具会先请用户确认，超预算会被拒。
- walk：dur=2, frames=4, pick=loop, cell=192x256。一次性动作（攻击等）：dur=2~5, frames=6, pick=even, cell 用 320x256 起步，
  先 pick_frames 再 vid2anim 并把 frames 传成 at。

## motion 怎么写（只写运动段，模板会补构图约束和禁止句）
- 只描述**要看到的姿态和运动方向**，不描述物理原因（写"发梢指向画面顶边"，不写"有风"）。
- 该动的部位一个个点名并给幅度；不写转速、不写"每秒"；不写"砸在地面上"这类会引入地面的词。
- 一次性动作：蓄力 → 发力 → 停住 → 收回到开头站姿。走路：原地踏步、双腿交替、手臂摆动、身体起伏，不位移；**武器保持原位不挥不转**（写了"划出弧线"模型就只演挥剑）。
- vid2anim 输出 no_loop=true / 形变≈0 ⇒ 视频里没有周期动作 ⇒ 看联络表，改运动段重出，别用那张图集。
- 连招的每一段必须是**不同动作类型**（挥 / 刺 / 砸；直拳 / 勾拳 / 上勾），同一动作换方向会被看成同一招。

## 怎么读数、怎么路由
- pick_frames.collapse=true：这一发作废。provider 是 ark ⇒ 换 dashscope 重出（能力问题，改提示词没用）；是 dashscope ⇒ 把改变的部位写进禁止句重出一次；仍崩 ⇒ ask_user。
- anim_bench：循环缝 > 1.5 循环会跳一下；亮度漂 > 10 多半是崩了不是动得好；形变 ≈ 0 = 挑到的帧全一样（loop 周期误判）⇒ 改 frames 或改用 pick_frames + at；剪影密度 < 50 缩小后散架。
- move_check：走路 Δx 应 ≈ 0；带位移的动作代码位移只能补动画里已有的位移。
- blade_check 不过 = 有帧刀变细线 ⇒ pick_frames 时避开这些帧号重新 vid2anim（不用重出片）。
- anim_check 不过：看 issues——"武器朝后"换命中格；"贴边"放大 cell 重新 vid2anim；"脚底不一致"重新 vid2anim。
- 不该重试的：四边出画 ⇒ 是源图占满画幅的问题（留白图）；面部结构类要求（张嘴、表情）⇒ 改立绘不是改提示词。

## 完成标准
每个动作都有图集 + 验收数字。finish 的 summary 说清每个动作花了多少钱、验收哪项没过、建议下一步。
""" + TOOLS_DOC


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


class Run:
    def __init__(self, spec):
        self.id = 'ag-' + time.strftime('%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
        self.dir = pipeline.JOBS / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / 'work' / 'anim').mkdir(parents=True, exist_ok=True)
        c = _creds.get('llm') or {}
        self.brain = spec.get('brain') or c.get('model') or pipeline.LLM_DEFAULT_MODEL
        self.eyes = spec.get('eyes') or c.get('vision_model') or self.brain
        self.state = {
            'id': self.id, 'agent': True, 'created': _now(), 'status': 'queued',
            'mode': spec.get('mode', 'demo'), 'prompt': spec.get('goal', ''), 'goal': spec.get('goal', ''),
            'budget': float(spec.get('budget') or 3.0), 'auto_approve': bool(spec.get('auto_approve')),
            'brain': self.brain, 'eyes': self.eyes,
            'actions': [], 'action_defs': {}, 'artifacts': {}, 'steps': [],
            'cost': {'est': 0.0, 'spent': 0.0, 'by': {}, 'spent_by': {}},
            'messages': [], 'transcript': [], 'pending': None, 'shots': {}, 'error': None,
        }
        if spec.get('portrait_upload'):
            import shutil
            shutil.copy(spec['portrait_upload'], self.dir / 'portrait.png')
            self.state['artifacts']['portrait'] = 'portrait.png'
        self.tag_info = {}
        self.save()

    # ── 持久化 / 日志
    def save(self):
        (self.dir / 'state.json').write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')

    def log(self, line):
        with open(self.dir / 'log.txt', 'a', encoding='utf-8') as f:
            f.write(str(line).rstrip('\n') + '\n')

    def _spend(self, who, amount):
        c = self.state['cost']
        c['spent_by'][who] = round(c['spent_by'].get(who, 0.0) + amount, 2)
        c['by'].setdefault(who, 0.0)
        c['spent'] = round(sum(c['spent_by'].values()), 2)

    # ── LLM
    def chat(self):
        import urllib.request
        import urllib.error
        c = _creds.get('llm')
        if not c:
            raise RuntimeError('没配 llm 凭据（DeepSeek 等 OpenAI 兼容端点）—— 到「凭据」页配')
        base = (c['base'] or '').rstrip('/')
        body = {'model': self.brain, 'temperature': 0.2, 'messages': self.state['messages'],
                'response_format': {'type': 'json_object'}}
        for attempt in range(2):
            req = urllib.request.Request(f'{base}/chat/completions', data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + c['key']})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode())['choices'][0]['message']['content']
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors='replace')[:300]
                if attempt == 0 and 'response_format' in txt:       # ⚑ 端点不认 json_object ⇒ 去掉再试
                    body.pop('response_format', None)
                    continue
                raise RuntimeError(f'LLM HTTP {e.code}：{txt}')

    # ── 工具执行
    def sh(self, argv, label, env=None, spend=None):
        e = {**__import__('os').environ, 'ANIMPIPE_ROOT': str(self.dir), 'PYTHONUNBUFFERED': '1',
             'PYTHONIOENCODING': 'utf-8', **(env or {})}
        st = {'label': label, 'status': 'running', 'started': _now()}
        self.state['steps'].append(st)
        self.log(f'\n───── {label}\n  $ {" ".join(Path(str(a)).name if str(a).endswith(".py") else str(a) for a in argv)}')
        p = subprocess.Popen([sys.executable] + [str(a) for a in argv], cwd=str(self.dir), env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        out, spent = [], False
        for line in p.stdout:
            out.append(line)
            self.log('  ' + line)
            if spend and not spent and 'id 已落盘' in line:        # ⚑ 视频按落盘时刻记账
                spent = True
                self._spend(*spend)
        code = p.wait()
        st.update(status='done' if code == 0 else 'failed', ended=_now())
        return code, ''.join(out)

    def _paid_gate(self, tool, cost, who, args):
        """⚑ 收费闸：demo 免费；预算不够直接拒；没自动批准就挂起等用户。⚑ 返回 None＝放行，⚑ dict＝拦下的结果"""
        if self.state['mode'] != 'generate' or cost <= 0:
            return None
        if self.state['cost']['spent'] + cost > self.state['budget']:
            return {'error': f'超预算：已花 ¥{self.state["cost"]["spent"]:.2f} + 这步 ¥{cost:.2f} > 预算 ¥{self.state["budget"]:.2f}。改用免费 provider，或 ask_user 加预算'}
        if self.state['auto_approve'] or self.state.get('_approved') == (tool, json.dumps(args, sort_keys=True)):
            self.state['_approved'] = None
            return None
        self.state['pending'] = {'type': 'confirm', 'tool': tool, 'args': args, 'cost': cost, 'who': who,
                                 'msg': f'agent 想花 ¥{cost:.2f}：{tool} {json.dumps(args, ensure_ascii=False)[:300]}'}
        return 'pause'

    def run_tool(self, name, args):
        A = pipeline.ACTIONS
        d = self.dir
        if name == 'doctor':
            have, can = _creds.probe()
            return {'mode': self.state['mode'], 'ffmpeg': bool(__import__('shutil').which('ffmpeg')),
                    'creds': {k: bool(v) for k, v in have.items()}, 'can': can,
                    'prices': {'portrait': pipeline.PRICE_PORTRAIT, 'video': {f'{p}|{r}|{dd}': v for (p, r, dd), v in pipeline.PRICE_VIDEO.items()}},
                    'budget': self.state['budget'], 'spent': self.state['cost']['spent'],
                    'portrait': self.state['artifacts'].get('portrait')}
        if name in ('gen_portrait', 'edit_portrait'):
            if self.state['mode'] != 'generate':
                return {'error': 'demo 模式没有立绘这一步，直接 gen_video'}
            g = self._paid_gate(name, pipeline.PRICE_PORTRAIT, '角色', args)
            if g:
                return g
            c = _creds.get('relay')
            if not c:
                return {'error': '没配 relay（中转站）出不了立绘；让用户上传一张或先配 key'}
            env = {'OPENAI_API_KEY': c['key'], 'OPENAI_BASE_URL': c['base']}
            im = args.get('model') or c.get('model')          # ⚑ 图像模型名各家中转站不同，⚑ 从 relay 凭据取
            if name == 'gen_portrait':
                item = {'id': 1, 'name': 'portrait', 'desc': '立绘', 'size': '1024x1024', 'transparent': False, 'quality': 'medium',
                        'prompt': pipeline.PORTRAIT_TMPL.format(desc=str(args.get('desc', '')).strip('。 '), style=pipeline.DEFAULT_STYLE)}
                (d / 'prompts.json').write_text(json.dumps({'items': [item]}, ensure_ascii=False), encoding='utf-8')
                code, out = self.sh([TOOLS / 'artgen' / 'gen.py', '1', '--force'] + (['--model', im] if im else []), '出立绘', env)
            else:
                (d / 'prompt_edit.txt').write_text(str(args.get('instruction', '')) + '\n保持人物的姿势、体型比例、朝向、构图和背景颜色完全不变，只修改上面提到的部分。', encoding='utf-8')
                code, out = self.sh([TOOLS / 'artgen' / 'edit.py', args.get('src', 'portrait.png'), 'out/01_portrait.png', '--promptfile=prompt_edit.txt']
                                    + ([f'--model={im}'] if im else []), '图生图改版', env)
            if code != 0 or not (d / 'out' / '01_portrait.png').exists():
                return {'error': '出图失败', 'log': out[-800:]}
            self._spend('角色', pipeline.PRICE_PORTRAIT)
            self.state['artifacts']['portrait'] = 'out/01_portrait.png'
            return {'portrait': 'out/01_portrait.png', 'cost': pipeline.PRICE_PORTRAIT}
        if name == 'make_liubai':
            src = args.get('src') or self.state['artifacts'].get('portrait')
            if not src:
                return {'error': '没有立绘：先 gen_portrait 或让用户上传'}
            code, out = self.sh([TOOLS / 'make_liubai.py', src, 'liubai.png'], '立绘 → 留白图')
            if code != 0:
                return {'error': '留白图失败', 'log': out[-600:]}
            self.state['artifacts']['liubai'] = 'liubai.png'
            return {'liubai': 'liubai.png', 'log': out[-300:]}
        if name == 'gen_video':
            tag = str(args.get('action', 'clip')).strip() or 'clip'
            label = A.get(tag, {}).get('label', tag)
            self.state['shots'][tag] = self.state['shots'].get(tag, 0)
            if self.state['shots'][tag] >= MAX_SHOTS_PER_ACTION:
                return {'error': f'{tag} 已出过 {MAX_SHOTS_PER_ACTION} 发，到上限了。要么用现有片子，要么 ask_user'}
            if tag not in self.state['actions']:
                self.state['actions'].append(tag)
                self.state['action_defs'][tag] = {'label': label, 'pick': 'loop' if tag == 'walk' else 'even'}
            mp4 = f'work/anim/{tag}.mp4'
            if self.state['mode'] == 'demo':
                code, out = self.sh([HERE / 'synth.py', 'walk' if tag == 'walk' else 'attack', mp4], f'{label}：合成素材（demo）')
                self.state['shots'][tag] += 1
                self.state['cost']['by'].setdefault(label, 0.0)
                return {'mp4': mp4, 'cost': 0, 'note': 'demo 合成素材'} if code == 0 else {'error': out[-400:]}
            prov, res, dur = args.get('provider', 'dashscope'), args.get('res', '480P'), int(args.get('dur', 2))
            model = args.get('model') or pipeline.VIDEO_PROVIDERS.get(prov, {}).get('model', '')
            price = pipeline.video_price(prov, res, dur, model)
            g = self._paid_gate(name, price, label, args)
            if g:
                return g
            liubai = self.state['artifacts'].get('liubai')
            if not liubai:
                return {'error': '没有留白图：先 make_liubai'}
            motion = str(args.get('motion', '')).strip()
            if not motion:
                return {'error': 'motion 是空的'}
            pf = f'prompt_{tag}_{self.state["shots"][tag] + 1}.txt'
            (d / pf).write_text(pipeline.build_prompt(tag if tag in A else 'attack', '', motion), encoding='utf-8')
            self.state['cost']['by'][label] = round(self.state['cost']['by'].get(label, 0.0) + price, 2)
            use_last = pipeline.VIDEO_PROVIDERS.get(prov, {}).get('last', True)     # ⚑ 智谱不给首尾帧（给了就静止）
            code, out = self.sh([TOOLS / 'gen_video.py', f'--img={liubai}'] + ([f'--last={liubai}'] if use_last else []) +
                                [f'--tag={tag}', f'--promptfile={pf}', f'--provider={prov}', f'--model={model}', f'--res={res}', f'--dur={dur}'],
                                f'{label}：出片（{prov} {res}/{dur}s ¥{price:.2f}）', spend=(label, price))
            self.state['shots'][tag] += 1
            if code != 0 or not (d / mp4).exists():
                return {'error': '出片失败', 'log': out[-800:]}
            return {'mp4': mp4, 'cost': price, 'prompt_file': pf}
        if name == 'contact_sheet':
            mp4 = args.get('mp4', '')
            n = int(args.get('n', 24))
            tag = Path(mp4).stem
            png = f'work/anim/{tag}_联络表{n}.png'
            code, out = self.sh([TOOLS / '_contact.py', mp4, f'--n={n}', f'--cols={6 if n > 12 else 4}', f'--out={png}'], f'{tag}：联络表 {n} 格')
            m = re.search(r'共 (\d+) 帧', out)
            if code != 0:
                return {'error': out[-400:]}
            self.state['artifacts'].setdefault(tag, {})['contact'] = png
            return {'png': png, 'total': int(m.group(1)) if m else None}
        if name == 'pick_frames':
            self.state['steps'].append({'label': f'视觉模型看图（{self.eyes}）', 'status': 'running', 'started': _now()})
            self.save()
            res = pipeline.vlm_pick(d / args.get('png', ''), str(args.get('motion', '')), int(args.get('n', 6)),
                                    int(args.get('total', 0) or 0), self.eyes, self.log)
            self.state['steps'][-1].update(status='done' if res else 'failed', ended=_now())
            if not res:
                return {'error': '视觉模型没返回可用结果（看日志），可退回 pick=even'}
            res.pop('raw', None)
            return res
        if name == 'vid2anim':
            mp4, tag = args.get('mp4', ''), str(args.get('tag', 'clip'))
            n, pick, cell = int(args.get('frames', 4)), args.get('pick', 'even'), args.get('cell', '192x256')
            argv = [TOOLS / 'vid2anim.py', mp4, f'--tag={tag}', f'--frames={n}', f'--cols={n}', f'--pick={pick}', f'--cell={cell}']
            if args.get('at'):
                argv.append(f'--at={args["at"] if isinstance(args["at"], str) else ",".join(map(str, args["at"]))}')
            code, out = self.sh(argv, f'{tag}：视频 → 图集')
            if code != 0:
                return {'error': out[-600:]}
            info = pipeline.parse_vid2anim(out)
            art = self.state['artifacts'].setdefault(tag, {})
            art.update(video=mp4, sheet=f'out/anim/{tag}.png', gif=f'work/anim/{tag}_看.gif',
                       cell=[int(v) for v in cell.lower().split('x')], frames=n, cols=n, sheet_info=info)
            art['foot_trim'] = pipeline.foot_trims(d / art['sheet'], art['cell'], n, n)
            if tag not in self.state['actions']:
                self.state['actions'].append(tag)
                self.state['action_defs'][tag] = {'label': A.get(tag, {}).get('label', tag), 'pick': pick}
            return {'sheet': art['sheet'], 'info': info, 'foot_trim': art['foot_trim']}
        if name == 'anim_bench':
            sheet, cell = args.get('sheet', ''), args.get('cell', '192x256')
            code, out = self.sh([TOOLS / 'anim_bench.py', f'--sheet={sheet}', f'--cell={cell}'], f'{Path(sheet).stem}：验收打分')
            r = pipeline.parse_bench(out)
            tag = Path(sheet).stem
            if r.get('ours'):
                self.state['artifacts'].setdefault(tag, {})['metrics'] = r['ours']
            return {'metrics': r.get('ours'), 'has_ref': r.get('has_ref'), 'note': '' if r.get('has_ref') else '没配基准素材，只有自己的数、没有合格区间'}
        if name == 'move_check':
            mp4 = args.get('mp4', '')
            code, out = self.sh([TOOLS / '_move_check.py', mp4, '--every=10'], f'{Path(mp4).stem}：位移量化')
            r = pipeline.parse_move(out)
            self.state['artifacts'].setdefault(Path(mp4).stem, {})['move'] = r
            return r or {'error': out[-300:]}
        if name == 'blade_check':
            mp4 = args.get('mp4', '')
            code, out = self.sh([TOOLS / '_blade_check.py', mp4], f'{Path(mp4).stem}：查刀变细线')
            self.state['artifacts'].setdefault(Path(mp4).stem, {})['blade_ok'] = code == 0
            return {'ok': code == 0, 'log': out[-500:]}
        if name == 'anim_check':
            tag = str(args.get('tag', ''))
            art = self.state['artifacts'].get(tag, {})
            if not art.get('cell'):
                return {'error': f'{tag} 还没有图集'}
            ft = sorted(v for v in (art.get('foot_trim') or []) if v is not None)
            ftm = ft[len(ft) // 2] if ft else 0
            code, out = self.sh([TOOLS / '_anim_check.py', f'{tag}:{art["cell"][0]}:{ftm}:{int(args.get("hit", max(1, art.get("frames", 6) // 2)))}', '--facing=left'], f'{tag}：图集自检')
            r = pipeline.parse_anim_check(out)
            art['anim_check'] = r
            return r
        if name == 'ask_user':
            self.state['pending'] = {'type': 'question', 'msg': str(args.get('question', ''))}
            return 'pause'
        if name == 'finish':
            for tag, v in (args.get('animations') or {}).items():
                art = self.state['artifacts'].setdefault(tag, {})
                if v.get('sheet'):
                    art['sheet'] = v['sheet']
                if v.get('cell') and isinstance(v['cell'], str):
                    art['cell'] = [int(x) for x in v['cell'].lower().split('x')]
                if v.get('frames'):
                    art['frames'] = art['cols'] = int(v['frames'])
                if tag not in self.state['actions']:
                    self.state['actions'].append(tag)
                    self.state['action_defs'][tag] = {'label': A.get(tag, {}).get('label', tag), 'pick': 'loop' if v.get('loop') else 'even'}
            self.state['summary'] = str(args.get('summary', ''))
            return 'finish'
        return {'error': f'不认识的工具 {name}'}

    # ── 主循环
    def start(self):
        self.state['messages'] = [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': (f'目标：{self.state["goal"]}\n模式：{self.state["mode"]}\n预算：¥{self.state["budget"]:.2f}\n'
                                         f'已有立绘：{self.state["artifacts"].get("portrait") or "无"}\n先调 doctor 看看环境。')}]
        self.state['status'] = 'running'
        self.save()
        threading.Thread(target=self.loop, daemon=True).start()

    def resume(self, approve=None, text=''):
        p = self.state.get('pending')
        self.state['pending'] = None
        if p and p['type'] == 'confirm':
            if approve:
                self.state['_approved'] = (p['tool'], json.dumps(p['args'], sort_keys=True))
                self._feed(p['tool'], p['args'])                     # ⚑ 用户批了 ⇒ 立刻执行那一步再继续
            else:
                self._append_result(p['tool'], {'error': f'用户拒绝花这笔钱：{text or "无说明"}。换免费 provider 或问用户'})
        elif p and p['type'] == 'question':
            self._append_result('ask_user', {'answer': text})
        self.state['status'] = 'running'
        self.save()
        threading.Thread(target=self.loop, daemon=True).start()

    def _append_result(self, tool, result):
        s = json.dumps(result, ensure_ascii=False)
        self.state['messages'].append({'role': 'user', 'content': f'工具 {tool} 返回：{s[:2500]}'})
        self.state['transcript'].append({'t': _now(), 'result': s[:800], 'tool': tool})

    def _feed(self, tool, args):
        r = self.run_tool(tool, args)
        if r == 'pause':
            self.state['status'] = 'waiting'
            self.save()
            return 'pause'
        if r == 'finish':
            self.state['status'] = 'done'
            self.state['transcript'].append({'t': _now(), 'tool': 'finish', 'result': self.state.get('summary', '')})
            self._manifest()
            self.save()
            return 'finish'
        self._append_result(tool, r)
        self.save()
        return None

    def loop(self):
        try:
            while self.state['status'] == 'running':
                if len(self.state['transcript']) >= MAX_STEPS * 2:
                    raise RuntimeError(f'超过 {MAX_STEPS} 步还没 finish，停下来省钱')
                raw = self.chat()
                m = re.search(r'\{.*\}', raw, re.S)
                try:
                    act = json.loads(m.group(0)) if m else None
                except Exception:
                    act = None
                if not isinstance(act, dict) or 'tool' not in act:
                    self.state['messages'].append({'role': 'assistant', 'content': raw})
                    self._append_result('(格式)', {'error': '请只输出一个 JSON 对象：{"thought","tool","args"}'})
                    continue
                self.state['messages'].append({'role': 'assistant', 'content': json.dumps(act, ensure_ascii=False)})
                self.state['transcript'].append({'t': _now(), 'thought': act.get('thought', ''), 'tool': act['tool'],
                                                 'args': act.get('args', {})})
                self.log(f'\n🧠 {act.get("thought", "")}\n→ {act["tool"]} {json.dumps(act.get("args", {}), ensure_ascii=False)[:300]}')
                self.save()
                r = self._feed(act['tool'], act.get('args') or {})
                if r in ('pause', 'finish'):
                    return
        except Exception as ex:
            self.state['status'] = 'failed'
            self.state['error'] = str(ex)
            self.log(f'\n✗ {ex}')
            self.save()

    def _manifest(self):
        m = {'id': self.id, 'goal': self.state['goal'], 'brain': self.brain, 'eyes': self.eyes,
             'cost': self.state['cost'], 'summary': self.state.get('summary', ''), 'animations': {}}
        for tag in self.state['actions']:
            art = self.state['artifacts'].get(tag, {})
            n = int(art.get('frames') or 0)
            m['animations'][tag] = {'label': self.state['action_defs'].get(tag, {}).get('label', tag),
                                    'loop': self.state['action_defs'].get(tag, {}).get('pick') == 'loop',
                                    'sheet': art.get('sheet'), 'cell': art.get('cell'), 'frames': n, 'cols': art.get('cols'),
                                    'foot_trim': art.get('foot_trim'), 'holds_ms': [120] * n,
                                    'metrics': art.get('metrics'), 'move': art.get('move')}
        (self.dir / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
        self.state['artifacts']['manifest'] = 'manifest.json'


_RUNS = {}


def start(spec):
    r = Run(spec)
    _RUNS[r.id] = r
    r.start()
    return r.state


def answer(run_id, approve=None, text=''):
    r = _RUNS.get(run_id)
    if not r:
        # ⚑ 服务重启后内存里没了 ⇒ 从盘上恢复（messages 全在 state.json 里）
        st = pipeline.load_job(run_id)
        if not st or not st.get('agent'):
            return None
        r = Run.__new__(Run)
        r.id, r.dir, r.state, r.tag_info = run_id, pipeline.JOBS / run_id, st, {}
        r.brain, r.eyes = st.get('brain'), st.get('eyes')
        _RUNS[run_id] = r
    if r.state.get('status') != 'waiting':
        return r.state
    r.resume(approve, text)
    return r.state
