# -*- coding: utf-8 -*-
"""⚑⚑⚑ **任务运行器** —— ⚑ 一句话 → 立绘 → 留白图 → 出片 → 图集 → 验收，⚑ 每个动作独立记账。

⚑ 铁律：**⛔ 不重写管线**。⚑ 每一步 = 一次 subprocess 调 `tools/` 里那份已验证的脚本，
  ⚑ 每个任务一个独立目录（`ANIMPIPE_ROOT` 指过去）⇒ ⚑ 产物互不串、⚑ 管线代码一行不动。

⚑ 记账：⚑ 视频**按「任务 id 落盘」时刻记**，⛔ 不按成功记 —— ⚑ 片取不回钱照扣（管线文档 §3.7）。
"""
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'tools'
JOBS = Path(os.environ.get('ANIMPIPE_JOBS') or HERE / 'jobs')
sys.path.insert(0, str(TOOLS))
import _creds  # noqa: E402

# ─────────────────────────────── 单价（《AI角色动画管线.md》§8.1，2026-09 口径，⚠ 会漂）
PRICE_PORTRAIT = 0.15                       # ⚑ gpt-image 类中转，一发
PRICE_VIDEO = {                             # ⚑ (provider, 分辨率, 秒) → 元
    ('dashscope', '480P', 2): 0.40,
    ('dashscope', '480P', 5): 1.05,
    ('dashscope', '720P', 5): 2.10,
}
PER_SEC = {('dashscope', '480P'): 0.21, ('dashscope', '720P'): 0.42}   # ⚑ 表里没有的组合按秒估


def video_price(provider: str, res: str, dur: int) -> float:
    if provider != 'dashscope':
        return 0.0                          # ⚑ GLM 免费 / ark 免费额度 / minimax 另计（未接入计价）
    if (provider, res, dur) in PRICE_VIDEO:
        return PRICE_VIDEO[(provider, res, dur)]
    return round(PER_SEC.get((provider, res), 0.21) * dur, 2)


# ⚑ 各家默认模型（⚑ 与 gen_video.py 的默认一致；⚠ 模型名会漂，⚑ 网页「凭据 → 检查」能列出可用的）
VIDEO_PROVIDERS = {
    'dashscope': dict(label='万相（定稿，唯一能钉首尾帧）', model='wan3.0-video', paid=True),
    'zhipu':     dict(label='智谱 GLM（免费抽构图，做不出大动作）', model='cogvideox-flash', paid=False),
    'ark':       dict(label='火山 seedance（免费额度，会重画角色）', model='doubao-seedance-1-0-pro-250528', paid=False),
    'minimax':   dict(label='MiniMax（另计费）', model='MiniMax-H3', paid=False),
}
LLM_DEFAULT_MODEL = 'glm-5.3-flash'         # ⚑ 一句话 → 四段提示词的扩写模型（走智谱 chat/completions）

# ⚑ 连招预设（文档 §7）：⚑ 三段必须是**不同动作类型**（⚠ 同一动作换方向 IoU 会撞），⚑ 每段首尾都回站姿。
#   ⚑ 拳击天然满足：直线 / 水平弧＋转体 / 下沉后垂直上击 —— 三种剪影分得开。
PRESETS = {
    'boxing3': {
        'label': '拳击三连招（刺拳 → 平勾 → 上勾）',
        'actions': {
            'jab': dict(label='刺拳', dur=2, res='480P', frames=6, pick='even', cell='320x256', checks=['anim'],
                        motion=('一记快速的前手直拳：前手握拳从下巴旁笔直向身体正前方打出，手臂完全伸直，'
                                '拳头到达最前端时短暂停住；肩膀随出拳略微前送；后手保持在下巴旁防守；'
                                '然后前手沿原路收回下巴旁，回到开头的站姿。')),
            'hook': dict(label='平勾拳', dur=2, res='480P', frames=6, pick='even', cell='320x256', checks=['anim'],
                         motion=('一记后手平勾拳：后手握拳，手肘抬到与肩同高，拳头沿水平弧线从身体外侧横扫到身体正前方，'
                                 '躯干随之明显转动，拳头到达正前方时短暂停住；然后收回，回到开头的站姿。')),
            'uppercut': dict(label='上勾拳', dur=2, res='480P', frames=6, pick='even', cell='320x288', checks=['anim'],
                             motion=('一记上勾拳：身体先略微下沉蓄力，后手握拳从腰部沿垂直方向向正上方猛击，'
                                     '拳头到达下巴高度以上时短暂停住，身体随之挺起；然后收回，回到开头的站姿。')),
        },
        'combo': ['jab', 'hook', 'uppercut'],
    },
}
CUSTOM_DEFAULTS = dict(dur=2, res='480P', frames=6, pick='even', cell='320x256', checks=['anim'], motion='')
CANCEL_FROM = 0.23     # ⚑ 取消窗口开在收招帧（文档 §7.1 ④：0.16 会跳过收招帧 ⇒ 拳头瞬移）


# ─────────────────────────────── 动作定义（⚑ 默认值全部来自文档实测）
ACTIONS = {
    'walk': dict(label='移动', dur=2, res='480P', frames=4, pick='loop', cell='192x256',
                 checks=['move'],
                 motion=('原地踏步走路：双腿交替前后迈步，幅度清晰可见；双臂随步伐前后自然摆动；'
                         '身体随步伐有轻微的上下起伏；头发和衣摆随动作摆动。'
                         '全程不离开原位，不向任何方向移动。')),
    'attack': dict(label='攻击', dur=5, res='480P', frames=6, pick='even', cell='384x256',
                   checks=['blade', 'anim'],
                   motion=('一次完整的横向挥砍：先把武器向身后拉开蓄力，身体略微后坐；'
                           '然后全力向身体前方水平挥出，武器划出清晰的弧线并带拖影，'
                           '挥到最前端短暂停住；最后收回武器，回到开头的站姿。'
                           '只描述运动方向，命中时武器必须指向角色面朝的方向。')),
}

# ⚑ 提示词四段结构（文档 §2）：①构图约束 ②镜头 ≠ 人物，分两句 ③该动的单独放开 ④禁止句单独成段 ＋ 画风锁死
PROMPT_HEAD = (
    '必须保持人物全身完整地在画面内，从头顶到双脚全部可见，双脚下方留出空白，'
    '绝对不要推近、不要放大人物、不要裁掉腿和脚。人物在画面中的大小和构图自始至终与原图完全一致。\n'
    '镜头完全固定不动，不推进、不拉远、不平移、不旋转。\n'
    '人物始终位于画面中央，脚底位置保持不变。\n'
)
PROMPT_TAIL = (
    '\n严禁改变发型。严禁改变面部。严禁改变身材比例。严禁改变服装样式和颜色。\n'
    '严禁武器改变形状、数量、长度或颜色。严禁出现第二把武器。\n'
    '严禁画出地面、阴影、背景物体。严禁改变背景颜色。严禁发光变白、严禁整体亮度变化。\n'
    '严格保持原图的画风、线条和配色。'
)

# ⚑ 立绘提示词（文档 §1.2）：刀横在身前、别写雾气光晕、底色给中性灰
PORTRAIT_TMPL = (
    '{desc}。{style}\n'
    '全身立绘，从头顶到双脚完整可见，3/4 侧面朝向画面左侧，双脚并拢自然站立。'
    '如果持有武器，武器横在身前、刀面正对观众。\n'
    '纯色中性灰背景（#9A9A9A），没有地面、没有阴影、没有任何背景元素。'
    '没有雾气、没有光晕、没有粒子、没有特效。干净清晰的硬边轮廓，游戏角色立绘。'
)
DEFAULT_STYLE = 'Q 版 3.5 头身，厚涂风格，颜色饱和，轮廓清晰。'


def build_prompt(action: str, extra: str = '', motion: str = None) -> str:
    return PROMPT_HEAD + '只有这些在运动，其余部位保持原位：' + (motion or ACTIONS[action]['motion']) + (
        ('\n' + extra) if extra else '') + PROMPT_TAIL


# ─────────────────────────────── 可选：让 LLM 把「一句话」扩成立绘描述 ＋ 各动作的运动段
LLM_SYSTEM = """你是 2D 游戏角色动画的提示词工程师。用户给一句话描述角色，你要产出 JSON：
{"portrait": "<立绘描述>", "walk": "<走路运动段>", "attack": "<攻击运动段>"}

规则（全部来自实测，违反就出废片）：
1. portrait 只描述角色外观（体型、服装、发型、武器、配色），一两句。不要写雾气、光晕、粒子、特效——它们会被画成实心形状。
2. walk / attack 是「运动段」：只描述**要看到的姿态和运动方向**，不描述物理原因（写"发梢指向画面顶边"，不写"有风"）。
3. 把该动的部位一个个点名并给幅度（"幅度清晰可见"、"划出清晰的弧线"）。不要写"其余保持不动"——那由外层模板负责。
4. 不要写转速、不要写"每秒"。不要写"砸在地面上"这类会引入地面的词。
5. attack 必须是一次完整动作：蓄力 → 发力 → 停住 → 收回到开头站姿。命中时武器指向角色面朝方向。
6. walk 是原地踏步，不位移。
只输出 JSON，不要解释。"""


def llm_prompts(sentence: str, style: str, actions, model: str, log):
    """⚑ 走智谱 OpenAI 兼容接口。⚠ 任何失败都退回模板 —— ⛔ 提示词这步不能成为花钱前的阻塞点。"""
    import urllib.request
    import urllib.error
    # ⚑ 优先用独立配的 llm（⚑ 任意 OpenAI 兼容端点），⚑ 没配再退回出片用的 glm key
    c = _creds.get('llm') or _creds.get('glm')
    if not c:
        log('  ⚠ 没配 llm / glm，提示词用内置模板')
        return None
    base = (c['base'] or 'https://open.bigmodel.cn/api/paas/v4').rstrip('/')
    model = model or c.get('model') or LLM_DEFAULT_MODEL
    log(f'  ⚑ LLM：{c["_from"]} · {base} · {model}')
    body = {'model': model, 'temperature': 0.4,
            'messages': [{'role': 'system', 'content': LLM_SYSTEM},
                         {'role': 'user', 'content': f'角色：{sentence}\n风格：{style}\n需要的动作：{", ".join(actions)}'}]}
    req = urllib.request.Request(f'{base}/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + c['key']})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = json.loads(r.read().decode())['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        log(f'  ⚠ LLM HTTP {e.code}：{e.read().decode(errors="replace")[:300]} ⇒ 用内置模板')
        return None
    except Exception as e:
        log(f'  ⚠ LLM 调用失败：{e} ⇒ 用内置模板')
        return None
    m = re.search(r'\{.*\}', txt, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except Exception:
        d = None
    if not isinstance(d, dict):
        log(f'  ⚠ LLM 没给出合法 JSON ⇒ 用内置模板\n  {txt[:300]}')
        return None
    log(f'  ✅ 提示词由 {model} 扩写')
    return d


# ─────────────────────────────── 任务
_lock = threading.Lock()


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


class Job:
    def __init__(self, spec: dict):
        self.id = time.strftime('%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
        self.dir = JOBS / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        import shutil
        if spec.get('portrait_upload'):                     # ⚑ 上传的立绘拷进任务目录，⚑ 产物自包含
            shutil.copy(spec['portrait_upload'], self.dir / 'portrait.png')
            spec['portrait_upload'] = 'portrait.png'
        if spec.get('portrait_from'):                       # ⚑ 图生图的底图也拷一份（⚑ 可追溯是从哪张改的）
            shutil.copy(spec['portrait_from'], self.dir / 'portrait_base.png')
            spec['portrait_from'] = 'portrait_base.png'
        self.spec = spec
        # ⚑ 本任务的动作表 ＝ 内置 ＋ 预设 ＋ 自定义（⚑ 连招就是若干个 even 挑帧的单发动作）
        self.actions = dict(ACTIONS)
        for pk in spec.get('presets', []):
            self.actions.update(PRESETS.get(pk, {}).get('actions', {}))
        for k, v in (spec.get('custom_actions') or {}).items():
            self.actions[k] = {**CUSTOM_DEFAULTS, **{kk: vv for kk, vv in v.items() if vv not in (None, '')}}
        self.state = {
            'id': self.id, 'created': _now(), 'status': 'queued',
            'mode': spec['mode'], 'prompt': spec.get('prompt', ''), 'style': spec.get('style', ''),
            'actions': [a for a in spec['actions'] if a in self.actions], 'options': spec.get('options', {}),
            'action_defs': {a: {k: v for k, v in self.actions[a].items()} for a in spec['actions'] if a in self.actions},
            'combo': spec.get('combo') or [], 'steps': [], 'artifacts': {},
            'cost': {'est': 0.0, 'spent': 0.0, 'by': {}}, 'error': None,
        }
        self._estimate()
        self.save()

    # ── 状态
    def save(self):
        with _lock:
            (self.dir / 'state.json').write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                                 encoding='utf-8')

    def log(self, line: str):
        with open(self.dir / 'log.txt', 'a', encoding='utf-8') as f:
            f.write(line.rstrip('\n') + '\n')
            f.flush()

    def _opt(self, action, key):
        v = self.state['options'].get(action, {}).get(key)
        return v if v not in (None, '') else self.actions[action][key]

    def _prov(self, a):
        o = self.state['options'].get(a, {})
        prov = o.get('provider', 'dashscope')
        return prov, o.get('model') or VIDEO_PROVIDERS[prov]['model']

    def _estimate(self):
        by, total = {}, 0.0
        if self.spec['mode'] == 'generate':
            by['角色'] = 0.0 if self.spec.get('portrait_upload') else PRICE_PORTRAIT
            total += by['角色']
            for a in self.state['actions']:
                p = video_price(self._prov(a)[0], self._opt(a, 'res'), int(self._opt(a, 'dur')))
                by[self.actions[a]['label']] = p
                total += p
        else:
            by['角色'] = 0.0
            for a in self.state['actions']:
                by[self.actions[a]['label']] = 0.0
        self.state['cost'].update(est=round(total, 2), by=by,
                                  spent_by={k: 0.0 for k in by})

    def _spend(self, who: str, amount: float):
        c = self.state['cost']
        c['spent_by'][who] = round(c['spent_by'].get(who, 0.0) + amount, 2)
        c['spent'] = round(sum(c['spent_by'].values()), 2)
        self.save()

    # ── 一步
    def step(self, key, label, argv, *, env=None, cost=0.0, who=None, spend_on='success',
             parse=None, must=True):
        st = {'key': key, 'label': label, 'status': 'running', 'started': _now(), 'cost_est': cost}
        self.state['steps'].append(st)
        self.save()
        self.log(f'\n───── {label}\n  $ {" ".join(os.path.basename(a) if str(a).endswith(".py") else str(a) for a in argv)}')
        e = {**os.environ, 'ANIMPIPE_ROOT': str(self.dir), 'PYTHONUNBUFFERED': '1',
             'PYTHONIOENCODING': 'utf-8', **(env or {})}
        out_lines, spent = [], False
        p = subprocess.Popen([sys.executable] + [str(a) for a in argv], cwd=str(self.dir), env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding='utf-8', errors='replace')
        for line in p.stdout:
            out_lines.append(line)
            self.log('  ' + line)
            # ⚑⚑ 视频：**id 一落盘就记账** —— ⚑ 之后不管成功失败，钱已经花了
            if spend_on == 'submit' and not spent and 'id 已落盘' in line:
                spent = True
                self._spend(who, cost)
        code = p.wait()
        text = ''.join(out_lines)
        if code == 0 and spend_on == 'success' and cost:
            self._spend(who, cost)
        st.update(status='done' if code == 0 else 'failed', ended=_now(), code=code)
        if parse:
            try:
                st['result'] = parse(text)
            except Exception as ex:      # ⚑ 解析失败不该拖垮任务
                st['result'] = {'_parse_error': str(ex)}
        self.save()
        if code != 0 and must:
            raise RuntimeError(f'{label} 失败（退出码 {code}）')
        return text

    # ── 主流程
    def run(self):
        try:
            self.state['status'] = 'running'
            self.save()
            if self.state['mode'] == 'generate':
                o = self.state['options']
                if o.get('prompt_llm'):
                    self.log('\n───── 提示词扩写（LLM）')
                    d = llm_prompts(self.state['prompt'], self.state['style'] or DEFAULT_STYLE,
                                    self.state['actions'], o.get('prompt_llm_model') or LLM_DEFAULT_MODEL, self.log)
                    if d:
                        self.state['prompts'] = d
                        self.save()
                if self.spec.get('portrait_upload'):
                    self.state['artifacts']['portrait'] = self.spec['portrait_upload']
                else:
                    self._portrait()
                src = self._liubai()
            else:
                src = None
            for a in self.state['actions']:
                self._action(a, src)
            self._manifest()
            self.state['status'] = 'done'
        except Exception as ex:
            self.state['status'] = 'failed'
            self.state['error'] = str(ex)
            self.log(f'\n✗ {ex}')
        self.save()

    def _portrait(self):
        c = _creds.get('relay')
        if not c:
            raise RuntimeError('没配 relay（中转站）—— 立绘出不了。⚑ 到「凭据」页配，或跳过立绘直接上传一张')
        env = {'OPENAI_API_KEY': c['key'], 'OPENAI_BASE_URL': c['base']}
        if self.spec.get('portrait_from'):
            # ⚑ 图生图：⚑ 在底图上只改该改的（换武器/换装），⚑ 姿势比例不动 —— ⚑ 唯一源图原则的正确打开方式
            instr = (self.spec.get('portrait_edit') or '').strip()
            (self.dir / 'prompt_portrait_edit.txt').write_text(
                instr + '\n保持人物的姿势、体型比例、朝向、构图和背景颜色完全不变，只修改上面提到的部分。', encoding='utf-8')
            self.state['artifacts']['portrait_base'] = self.spec['portrait_from']
            self.state['artifacts']['portrait_prompt'] = instr
            argv = [TOOLS / 'artgen' / 'edit.py', self.spec['portrait_from'], 'out/01_portrait.png',
                    '--promptfile=prompt_portrait_edit.txt']
            if self.state['options'].get('image_model'):
                argv.append(f'--model={self.state["options"]["image_model"]}')
            self.step('portrait', '立绘：图生图改版', argv, cost=PRICE_PORTRAIT, who='角色', env=env)
            self.state['artifacts']['portrait'] = 'out/01_portrait.png'
            self.save()
            return
        desc = (self.state.get('prompts') or {}).get('portrait') or self.state['prompt']
        item = {'id': 1, 'name': 'portrait', 'desc': '立绘', 'size': '1024x1024',
                'transparent': False, 'quality': self.state['options'].get('image_quality', 'medium'),
                'prompt': PORTRAIT_TMPL.format(desc=desc.strip('。 '),
                                               style=self.state['style'] or DEFAULT_STYLE)}
        self.state['artifacts']['portrait_prompt'] = item['prompt']
        (self.dir / 'prompts.json').write_text(json.dumps([item], ensure_ascii=False, indent=2),
                                               encoding='utf-8')
        argv = [TOOLS / 'artgen' / 'gen.py', '1', '--force']
        if self.state['options'].get('image_model'):
            argv += ['--model', self.state['options']['image_model']]
        # ⚑ gen.py 只认环境变量 ⇒ ⚑ 把 _creds 里的 relay 注进去（⚑ 网页端配的 key 由此生效）
        self.step('portrait', '出立绘', argv, cost=PRICE_PORTRAIT, who='角色', env=env)
        png = self.dir / 'out' / '01_portrait.png'
        if not png.exists():
            raise RuntimeError('gen.py 没有产出 out/01_portrait.png')
        self.state['artifacts']['portrait'] = 'out/01_portrait.png'
        self.save()

    def _liubai(self):
        src = self.state['artifacts']['portrait']
        self.step('liubai', '立绘 → 留白图', [TOOLS / 'make_liubai.py', src, 'liubai.png'])
        self.state['artifacts']['liubai'] = 'liubai.png'
        self.save()
        return 'liubai.png'

    def _action(self, a, src):
        A, who = self.actions[a], self.actions[a]['label']
        art = self.state['artifacts'].setdefault(a, {})
        work = self.dir / 'work' / 'anim'
        work.mkdir(parents=True, exist_ok=True)
        mp4 = f'work/anim/{a}.mp4'
        if self.state['mode'] == 'generate':
            res, dur = self._opt(a, 'res'), int(self._opt(a, 'dur'))
            prov, model = self._prov(a)
            motion = (self.state.get('prompts') or {}).get(a) or A['motion']
            pf = self.dir / f'prompt_{a}.txt'
            pf.write_text(build_prompt(a, self.state['options'].get(a, {}).get('extra', ''), motion),
                          encoding='utf-8')
            art.update(prompt=f'prompt_{a}.txt', provider=prov, model=model, res=res, dur=dur)
            price = video_price(prov, res, dur)
            self.step(f'{a}.video', f'{who}：出片（{prov} {model} {res}/{dur}s，{"¥%.2f" % price if price else "免费"}）',
                      [TOOLS / 'gen_video.py', f'--img={src}', f'--last={src}', f'--tag={a}',
                       f'--promptfile=prompt_{a}.txt', f'--provider={prov}', f'--model={model}',
                       f'--res={res}', f'--dur={dur}'],
                      cost=price, who=who, spend_on='submit')
        else:
            # ⚑ demo 只有 walk / attack 两种合成素材；⚑ 自定义/连招动作一律用 attack 那段顶
            self.step(f'{a}.synth', f'{who}：合成测试视频（⛔ 不调 API）',
                      [HERE / 'synth.py', 'walk' if A.get('pick') == 'loop' else 'attack', mp4])
        art['video'] = mp4
        self.step(f'{a}.contact', f'{who}：联络表', [TOOLS / '_contact.py', mp4], must=False)
        art['contact'] = f'work/anim/{a}_联络表.png'
        cell = self._opt(a, 'cell')
        n_frames = int(self._opt(a, 'frames'))
        # ⚑⚑ 一律出**单行**图集（--cols=帧数）：⚑ `_anim_check` 按单行切格（⚠ 4×2 会把第一行脚底算到整图高上，
        #   ⛔ 报"差 256px"），⚑ 引擎接单行也最省事。⚑ 多行支持只留给「提取」页上传的图集。
        self.step(f'{a}.sheet', f'{who}：视频 → 图集',
                  [TOOLS / 'vid2anim.py', mp4, f'--tag={a}', f'--frames={n_frames}', f'--cols={n_frames}',
                   f'--pick={self._opt(a, "pick")}', f'--cell={cell}'],
                  parse=parse_vid2anim)
        r = self.state['steps'][-1].get('result') or {}
        art.update(sheet=f'out/anim/{a}.png', gif=f'work/anim/{a}_看.gif',
                   cell=[int(v) for v in cell.lower().split('x')], frames=n_frames,
                   cols=r.get('cols', n_frames), sheet_info=r)
        art['foot_trim'] = foot_trims(self.dir / art['sheet'], art['cell'], art['cols'], n_frames)
        self.step(f'{a}.bench', f'{who}：验收打分',
                  [TOOLS / 'anim_bench.py', f'--sheet=out/anim/{a}.png', f'--cell={cell}'],
                  parse=parse_bench, must=False)
        art['metrics'] = (self.state['steps'][-1].get('result') or {}).get('ours')
        if 'move' in A['checks']:
            self.step(f'{a}.move', f'{who}：位移量化', [TOOLS / '_move_check.py', mp4, '--every=10'],
                      parse=parse_move, must=False)
            art['move'] = self.state['steps'][-1].get('result')
        if 'blade' in A['checks']:
            self.step(f'{a}.blade', f'{who}：查「刀变细线」', [TOOLS / '_blade_check.py', mp4], must=False)
            art['blade_ok'] = self.state['steps'][-1]['status'] == 'done'
        if 'anim' in A['checks']:
            # ⚑ footTrim 传**实测中位数**（⛔ 传 0 必报"差 N px"）；⚑ 命中格默认取中间那格
            ft = sorted(v for v in (art['foot_trim'] or []) if v is not None)
            ft_med = ft[len(ft) // 2] if ft else 0
            hit = max(1, n_frames // 2)
            self.step(f'{a}.anim', f'{who}：图集自检',
                      [TOOLS / '_anim_check.py', f'{a}:{art["cell"][0]}:{ft_med}:{hit}', '--facing=left'],
                      parse=parse_anim_check, must=False)
            art['anim_check'] = self.state['steps'][-1].get('result')
        self.save()

    def _manifest(self):
        m = {'id': self.id, 'prompt': self.state['prompt'], 'mode': self.state['mode'],
             'cost': self.state['cost'], 'animations': {}}
        for a in self.state['actions']:
            art, A = self.state['artifacts'].get(a, {}), self.actions[a]
            n = int(art.get('frames') or 0)
            one_shot = A.get('pick') != 'loop'
            m['animations'][a] = {
                'label': A['label'], 'loop': not one_shot,
                'sheet': art.get('sheet'), 'cell': art.get('cell'),
                'frames': n, 'cols': art.get('cols'), 'foot_trim': art.get('foot_trim'),
                'holds_ms': [120] * n,                                # ⚑ 起手值，⚑ 顿帧在预览里调
                # ⚑ 单发动作的接线字段（文档 §6.1 / §7）：⚑ 命中帧、⚑ 取消窗口从哪帧开
                'hit_frame': (max(1, n // 2) if one_shot else None),
                'cancel_from_frame': (max(1, math.ceil(n * CANCEL_FROM)) if one_shot else None),
                'metrics': art.get('metrics'), 'move': art.get('move'),
            }
        combo = [k for k in self.state.get('combo') or [] if k in m['animations']]
        if len(combo) >= 2:
            m['combo'] = {'order': combo, 'cancel_from': CANCEL_FROM,
                          'note': '段间衔接靠首尾帧同图；取消窗口开在收招帧；输入缓冲在窗口内记下、播完立刻接'}
        (self.dir / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
        self.state['artifacts']['manifest'] = 'manifest.json'


# ─────────────────────────────── 解析工具输出（⚑ v1 先用正则；⚑ 下一步给工具加 --json）
def parse_vid2anim(t):
    r = {}
    m = re.search(r'步频周期 (\d+) 帧', t)
    if m:
        r['period'] = int(m.group(1))
    m = re.search(r'循环点 f(\d+)→f(\d+)', t)
    if m:
        r['loop'] = [int(m.group(1)), int(m.group(2))]
    m = re.search(r'✅ .*?\.png\s+(\d+)x(\d+).*?\((\d+)列 × (\d+)行\)', t)
    if m:
        r.update(w=int(m.group(1)), h=int(m.group(2)), cols=int(m.group(3)), rows=int(m.group(4)))
    m = re.search(r'量到底色 \[([\d. ]+)\]', t)
    if m:
        r['bg'] = [round(float(v)) for v in m.group(1).split()]
    r['no_loop'] = '没有合法循环段' in t
    return r


def parse_bench(t):
    r = {}
    for name, line in re.findall(r'^\s+(\S+\.png \d+帧|Idle  2帧|Move  4帧|Die   3帧)\s+(动量.*)$', t, re.M):
        m = re.search(r'动量\s+([\d.]+)\s+形变\s+([\d.]+)%\s+循环缝\s+([\d.]+)\s+剪影密度\s+([\d.]+)%\s+亮度漂\s+([\d.]+)', line)
        if not m:
            continue
        d = dict(zip(['动量', '形变%', '循环缝', '剪影密度%', '亮度漂'], map(float, m.groups())))
        r['ours' if name.endswith('.png') or '.png' in name else name.strip()] = d
    r['has_ref'] = 'Idle  2帧' in r
    return r


def parse_move(t):
    r = {}
    for k, pat in (('right', r'最右 Δx\s+([+-]?\d+)px = 身高的\s+([+-]?[\d.]+)%'),
                   ('left', r'最左 Δx\s+([+-]?\d+)px = 身高的\s+([+-]?[\d.]+)%'),
                   ('lift', r'最大抬升\s+([+-]?\d+)px = 身高的\s+([+-]?[\d.]+)%')):
        m = re.search(pat, t)
        if m:
            r[k] = {'px': int(m.group(1)), 'pct': float(m.group(2))}
    return r


def parse_anim_check(t):
    return {'pass': '项不通过' not in t,
            'issues': re.findall(r'⛔ \*\*(.+?)\*\*', t) + re.findall(r'③ footTrim.*⛔ (.+)', t)}


def cell_box(i, cell, cols):
    """⚑ 第 i 格（0 起）在图集里的裁切框 —— ⚑ 多行图集按 cols 折行"""
    cw, ch = cell
    c, r = i % cols, i // cols
    return (c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)


def foot_trims(sheet: Path, cell, cols=None, frames=None):
    """⚑ 每格脚底距格子底边的像素 —— ⚑ 中间 40% 列的最低不透明行（⚑ 沿用 vid2anim 的约定）"""
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(sheet).convert('RGBA')
        cw, ch = cell
        cols = cols or max(1, im.width // cw)
        frames = frames or cols * max(1, im.height // ch)
        out = []
        for i in range(frames):
            a = np.asarray(im.crop(cell_box(i, cell, cols)))[:, :, 3]
            band = a[:, int(cw * .3):int(cw * .7)] > 200
            rows = np.where(band.any(axis=1))[0]
            out.append(int(ch - 1 - rows.max()) if len(rows) else None)
        return out
    except Exception:
        return None


# ─────────────────────────────── 导出
def export_zip(job_dir: Path, dst: Path):
    """⚑ 图集 + 逐帧 PNG + gif + manifest —— ⚑ 接进任何引擎都够用"""
    from PIL import Image
    st = json.loads((job_dir / 'state.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('manifest.json', 'liubai.png'):
            if (job_dir / name).exists():
                z.write(job_dir / name, name)
        if st['artifacts'].get('portrait') and (job_dir / st['artifacts']['portrait']).exists():
            z.write(job_dir / st['artifacts']['portrait'], 'portrait.png')
        for a in st['actions']:
            art = st['artifacts'].get(a, {})
            sheet = job_dir / (art.get('sheet') or '')
            if not sheet.exists():
                continue
            z.write(sheet, f'{a}/{a}_sheet.png')
            if art.get('gif') and (job_dir / art['gif']).exists():
                z.write(job_dir / art['gif'], f'{a}/{a}_preview.gif')
            if art.get('contact') and (job_dir / art['contact']).exists():
                z.write(job_dir / art['contact'], f'{a}/{a}_contact.png')
            im = Image.open(sheet).convert('RGBA')
            cols = art.get('cols') or max(1, im.width // art['cell'][0])
            for i in range(art.get('frames') or cols):
                buf = job_dir / f'_tmp_{a}_{i:02d}.png'
                im.crop(cell_box(i, art['cell'], cols)).save(buf)
                z.write(buf, f'{a}/frames/{a}_{i:02d}.png')
                buf.unlink()
    return dst


def slice_sheet_zip(sheet: Path, cell, dst: Path):
    """⚑ 给「上传自己的图集 → 提取帧」用"""
    from PIL import Image
    im = Image.open(sheet).convert('RGBA')
    cw, ch = cell
    cols, rows = im.width // cw, max(1, im.height // ch)
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        k = 0
        for r in range(rows):
            for c in range(cols):
                buf = dst.with_suffix(f'.{k:02d}.png')
                im.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)).save(buf)
                z.write(buf, f'frames/{k:02d}.png')
                buf.unlink()
                k += 1
        z.writestr('manifest.json', json.dumps({'cell': [cw, ch], 'frames': k,
                                                'foot_trim': foot_trims(sheet, (cw, ch))},
                                               ensure_ascii=False, indent=2))
    return dst


# ─────────────────────────────── 任务表
def list_jobs():
    out = []
    if JOBS.exists():
        for d in sorted(JOBS.iterdir(), reverse=True):
            f = d / 'state.json'
            if f.exists():
                try:
                    out.append(json.loads(f.read_text(encoding='utf-8')))
                except Exception:
                    pass
    return out


def load_job(job_id: str):
    f = JOBS / job_id / 'state.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else None


def start_job(spec: dict) -> dict:
    job = Job(spec)
    threading.Thread(target=job.run, daemon=True).start()
    return job.state
