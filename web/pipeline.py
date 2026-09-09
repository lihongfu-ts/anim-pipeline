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
PRICE_PORTRAIT = 0.15                       # ⚑ gpt-image 类中转标价（文档 §8.1）；⚑ 各家不同 ⇒ 用 portrait_price()


def portrait_price() -> float:
    """⚑ 出图单价：⚑ relay 凭据里填了 price 就按它（⚑ 用户那家 gpt-image-2.5 是 ¥0.05/张），⚑ 否则标价。"""
    c = _creds.get('relay')
    if c and c.get('price') not in (None, ''):
        try:
            return float(c['price'])
        except (TypeError, ValueError):
            pass
    return PRICE_PORTRAIT
PRICE_VIDEO = {                             # ⚑ (provider, 分辨率, 秒) → 元
    ('dashscope', '480P', 2): 0.40,
    ('dashscope', '480P', 5): 1.05,
    ('dashscope', '720P', 5): 2.10,
}
PER_SEC = {('dashscope', '480P'): 0.21, ('dashscope', '720P'): 0.42}   # ⚑ 表里没有的组合按秒估


# ⚑ 智谱按次计价（2026-09 文档）：⚑ cogvideox-flash 曾免费、现已不在文档里（⚠ 能不能用得真发一次）；
#   ⚑ cogvideox-3 ¥1/次（支持首尾帧）；vidu2 ¥1.25/次（4s 720P，首尾帧）；vidu2-reference ¥2.5
ZHIPU_PRICE = {'cogvideox-flash': 0.0, 'cogvideox-3': 1.0, 'vidu2-image': 1.25, 'vidu2-start-end': 1.25,
               'vidu2-reference': 2.5, 'viduq1-text': 1.0, 'viduq1-image': 1.0}


def video_price(provider: str, res: str, dur: int, model: str = '') -> float:
    if provider == 'zhipu':
        model = model or VIDEO_PROVIDERS['zhipu']['model']
        c = _creds.get('glm')
        # ⚑ 凭据里填了 price（⚑ 买了包：如 ¥10/100 次 ⇒ 0.10）且模型对得上 ⇒ 按包价记
        if c and c.get('price') not in (None, '') and (not c.get('model') or c['model'] == model):
            try:
                return float(c['price'])
            except (TypeError, ValueError):
                pass
        return ZHIPU_PRICE.get(model, 1.0)
    if provider != 'dashscope':
        return 0.0                          # ⚑ ark 免费额度 / minimax 另计（未接入计价）
    if (provider, res, dur) in PRICE_VIDEO:
        return PRICE_VIDEO[(provider, res, dur)]
    return round(PER_SEC.get((provider, res), 0.21) * dur, 2)


# ⚑ 各家默认模型（⚑ 与 gen_video.py 的默认一致；⚠ 模型名会漂，⚑ 网页「凭据 → 检查」能列出可用的）
# ⚑ last：⚑ 要不要把留白图同时当尾帧（--last）。⚠ 实测 2026-09-09：⚑ CogVideoX-3 首尾帧给同一张图 ⇒ **161 帧一动不动**
#   （⚑ 它的首尾帧模式像是在两张图之间插值，⚑ 首尾相同就等于不动）；⚑ 万相则是在中间"演"，⚑ 首尾同图正是循环闭合的地基。
VIDEO_PROVIDERS = {
    'dashscope': dict(label='万相（定稿，钉首尾帧）', model='wan3.0-video', paid=True, last=True),
    'zhipu':     dict(label='智谱 CogVideoX-3（标价 ¥1/次，买包约 ¥0.1；⛔ 不给首尾帧，给了就静止）', model='cogvideox-3', paid=True, last=False),
    'ark':       dict(label='火山 seedance（免费额度，会重画角色）', model='doubao-seedance-1-0-pro-250528', paid=False, last=True),
    'minimax':   dict(label='MiniMax（另计费）', model='MiniMax-H3', paid=False, last=True),
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
# ⚑ 网页只露六个常用的：待机 / 移动 / 奔跑 / 受击 / 死亡 / 攻击（⚑ 用户 2026-09-09 定）；
#   ⚑ 攻击可设 1~3 段 ⇒ attack / attack2 / attack3 —— ⚑ 三段剪影分得开（横弧 / 竖劈 / 直刺，文档 §7 的规矩）
#   ⚑ last=False：⚑ 死亡不能钉尾帧（⚠ 首尾同图 ＝ 倒下又站起来）
ACTIONS = {
    'idle': dict(label='待机', dur=2, res='480P', frames=4, pick='loop', cell='192x256',
                 checks=['move'],
                 motion=('原地待机呼吸：胸口和肩膀随呼吸轻微起伏，幅度小但清晰可见；'
                         '头发末端和衣摆轻微飘动；双脚原地不动，双腿不动，重心不变；'
                         '手中的武器保持在原来的持握位置，不挥动、不转动、不举起。'
                         '全程不离开原位，不向任何方向移动，人物大小不变。')),
    'walk': dict(label='移动', dur=2, res='480P', frames=4, pick='loop', cell='192x256',
                 checks=['move'],
                 # ⚑ 实测 2026-09-09（job 0909-195554）：写"武器保持原位不转动"模型照样让持刀臂大幅摆、刀从 30° 抬到 70° 且不随步频回来 ⇒ 循环点必跳
                 #   ⚑ 改成**有界 + 跟步伐同周期**的小幅摆动（⛔ 别写"固定住"——假，而且模型也不听）
                 motion=('原地踏步走路：双腿交替前后迈步，抬腿幅度清晰可见；空着的那只手臂随步伐前后自然摆动；'
                         '身体随步伐有轻微的上下起伏；头发和衣摆随动作摆动。'
                         '持武器的手臂随步伐小幅前后摆动，幅度约为空手那侧的一半；武器与前臂的夹角保持不变、跟着手臂一起小幅摆动，'
                         '武器尖端始终指向前下方，不抬到水平以上，不挥动、不举起。'
                         '全程不离开原位，不向任何方向移动，人物大小不变。')),
    'run':  dict(label='奔跑', dur=2, res='480P', frames=6, pick='loop', cell='224x256',
                 checks=['move'],
                 # ⚑ 实测 2026-09-09（job 0909-175645）：只写"抬膝到腰际"⇒ 模型演成**直腿侧踢**；⚑ 必须点名小腿向后折叠
                 motion=('原地大步奔跑：身体明显前倾，双腿交替蹬地抬膝，抬起的那条腿膝盖朝前、小腿向后折叠、脚跟贴近臀部，'
                         '不要伸直、不要向前踢出；另一条腿在身体下方蹬直支撑；有短暂的双脚离地瞬间；'
                         '双臂随步伐大幅前后摆动，肘部弯曲；头发和衣摆向后大幅飘起。'
                         '持武器的手臂摆动幅度约为空手那侧的一半；武器与前臂的夹角保持不变、跟着手臂一起摆动，'
                         '武器尖端始终指向前下方或后下方，不抬到水平以上，不挥动、不举起。'
                         '全程不离开原位，不向任何方向移动，人物大小不变。')),
    'hurt': dict(label='受击', dur=2, res='480P', frames=4, pick='even', cell='224x256',
                 checks=[],
                 # ⚑ 角色朝画面左 ⇒ 受击从左来、身体向右缩；⛔ 别写"上半身后仰"（MOTION_SYSTEM 第 5 条：会被画成转向镜头）
                 motion=('受到来自面朝方向的一击：头和肩膀猛地向画面右侧缩一下，肩膀耸起，头低下，'
                         '上半身向右侧弹开一小段，重心后坐、膝盖微弯；双脚原地不动；'
                         '短暂停住；然后恢复到开头的站姿。手中的武器随手臂带动但不挥动。')),
    'die':  dict(label='死亡', dur=5, res='480P', frames=6, pick='even', cell='320x256',
                 checks=[], last=False,
                 motion=('倒下死亡：身体先向画面右侧踉跄半步，膝盖弯曲跪倒，上半身前倾；'
                         '然后整个身体向侧面倒下，最终平躺在画面底部、头朝画面右侧，四肢摊开，完全静止不再动；'
                         '手中的武器随手臂落下、放在身旁。结尾停留在倒下静止的姿势，不要站起来。')),
    'attack': dict(label='攻击', dur=5, res='480P', frames=6, pick='even', cell='384x256',
                   checks=['blade', 'anim'],
                   motion=('一次完整的横向挥砍：先把武器向身后拉开蓄力，身体略微后坐；'
                           '然后全力向身体前方水平挥出，武器划出清晰的弧线并带拖影，'
                           '挥到最前端短暂停住；最后收回武器，回到开头的站姿。'
                           '只描述运动方向，命中时武器必须指向角色面朝的方向。')),
    'attack2': dict(label='攻击2', dur=5, res='480P', frames=6, pick='even', cell='320x320',
                    checks=['blade', 'anim'],
                    motion=('一次完整的竖直下劈：先把武器高举到头顶上方蓄力，身体略微挺起；'
                            '然后全力向身体前下方竖直劈下，武器划出清晰的竖直弧线并带拖影，'
                            '劈到身前最低点短暂停住；最后收回武器，回到开头的站姿。'
                            '只描述运动方向，命中时武器必须指向角色面朝的方向。')),
    'attack3': dict(label='攻击3', dur=5, res='480P', frames=6, pick='even', cell='384x256',
                    checks=['blade', 'anim'],
                    motion=('一次完整的向前直刺：先把武器收到腰侧向后拉开蓄力，身体略微下沉；'
                            '然后手臂完全伸直、武器笔直向身体正前方刺出，带直线拖影，'
                            '刺到最前端短暂停住；最后收回武器，回到开头的站姿。'
                            '只描述运动方向，命中时武器必须指向角色面朝的方向。')),
}
ATTACK_CHAIN = ['attack', 'attack2', 'attack3']     # ⚑ 攻击 N 段 ⇒ 取前 N 个，⚑ combo 就是这个顺序
UI_ACTIONS = ['idle', 'walk', 'run', 'hurt', 'die', 'attack']   # ⚑ 网页 chip 顺序（⚑ attack 一个 chip 带段数）

# ⚑ 提示词四段结构（文档 §2）：①构图约束 ②镜头 ≠ 人物，分两句 ③该动的单独放开 ④禁止句单独成段 ＋ 画风锁死
PROMPT_HEAD = (
    '必须保持人物全身完整地在画面内，从头顶到双脚全部可见，双脚下方留出空白，'
    '绝对不要推近、不要放大人物、不要裁掉腿和脚。人物在画面中的大小和构图自始至终与原图完全一致。\n'
    '镜头完全固定不动，不推进、不拉远、不平移、不旋转。\n'
    '人物始终位于画面中央，脚底位置保持不变。\n'
)
PROMPT_TAIL = (
    '\n严禁改变发型。严禁改变帽子和头饰。严禁改变面部。严禁改变身材比例。严禁改变服装样式和颜色。\n'
    '严禁武器改变形状、数量、长度或颜色。严禁出现第二把武器。\n'
    '严禁画出地面、阴影、背景物体。严禁改变背景颜色。严禁发光变白、严禁整体亮度变化。\n'
    '严格保持原图的画风、线条和配色。'
)

# ⚑ 持武器姿势（⚑ 用户 2026-09-09 提出：横持适合攻击，⚠ 但要做奔跑就不自然）
#   ⚑ across：文档 §1.2 的原规矩——薄片武器斜拿、摆臂时刀面转向镜头会"消失"，横持最稳
#   ⚑ side：跑步/走路自然，⚑ 靠"宽面始终朝向观众"压细线风险；攻击从此姿势抬剑起手也顺
#   ⚑ back：跑步最自然、完全没有细线问题，⚠ 但攻击得从拔剑开始，剑形靠模型凭空画，一致性风险大
WEAPON_POSE = {
    'across': '如果持有武器，武器横在身前、宽面正对观众。',
    'side':   '如果持有武器，持武器的手自然垂在身体一侧，武器尖端斜指后下方，武器的宽面始终朝向观众；另一只手空着自然下垂。',
    'back':   '如果持有武器，武器收在背后或腰间的鞘中，双手空着自然下垂。',
}

# ⚑ 视角（docs/2D游戏类型与视角参考.md）—— ⚑ 用户 2026-09-09：先支持两个「1 朝向」的类型，⚑ 差别只在镜头：
#   ⚑ portrait：立绘模板里的镜头/朝向句；⚑ video：视频提示词头部追加的锁镜头句（空 = 不加）
#   ⚑ 表结构留给以后的 card（3/4 正面）/ top4（多朝向，要转身立绘 + 子动作展开）—— 见文档 §四
VIEWS = {
    'side':    dict(label='横版（侧视）', dirs=1,
                    portrait='3/4 侧面朝向画面左侧，水平视角',
                    video='',
                    note='角色纯侧面，引擎左右镜像；实测都在这个视角上'),
    'topside': dict(label='斜俯视地图 + 侧视角色（传奇类 H5 / 放置刷怪）', dirs=1,
                    portrait='3/4 侧面朝向画面左侧、身体略微转向观众，镜头轻微俯视（像俯视地图里的角色，能略微看到头顶和肩膀上沿），双脚站在同一水平面上',
                    video='镜头保持轻微俯视的角度，自始至终不变，不要变成水平视角。',
                    note='地图斜俯、角色仍只有左右两个朝向（镜像）；上下走用同一套侧面图。⚠ 俯视镜头锁不锁得住没实测过'),
}


def view_of(v: str) -> dict:
    return VIEWS.get(v or 'side', VIEWS['side'])


# ⚑ 立绘提示词（文档 §1.2）：别写雾气光晕、底色给中性灰；持武器姿势按 WEAPON_POSE 选；镜头/朝向按 VIEWS 选
PORTRAIT_TMPL = (
    '{desc}。{style}\n'
    '全身立绘，从头顶到双脚完整可见，{camera}，双脚并拢自然站立。'
    '{weapon}\n'
    '纯色中性灰背景（#9A9A9A），没有地面、没有阴影、没有任何背景元素。'
    '没有雾气、没有光晕、没有粒子、没有特效。干净清晰的硬边轮廓，游戏角色立绘。'
)
DEFAULT_STYLE = 'Q 版 3.5 头身，厚涂风格，颜色饱和，轮廓清晰。'


def build_prompt(action: str, extra: str = '', motion: str = None, view: str = 'side') -> str:
    cam = view_of(view)['video']
    return PROMPT_HEAD + (cam + '\n' if cam else '') + '只有这些在运动，其余部位保持原位：' + (motion or ACTIONS[action]['motion']) + (
        ('\n' + extra) if extra else '') + PROMPT_TAIL


# ─────────────────────────────── 可选：让 LLM 把「一句话」扩成立绘描述 ＋ 各动作的运动段
LLM_SYSTEM = """你是 2D 游戏角色动画的提示词工程师。用户给一句话描述角色和需要的动作列表，你要产出 JSON：
{"portrait": "<立绘描述>", "<动作key>": "<该动作的运动段>", ...}   （每个动作 key 一个字段，如 walk / run / attack / jab）

规则（全部来自实测，违反就出废片）：
1. portrait 只描述角色外观（体型、服装、发型、武器、配色），一两句。不要写雾气、光晕、粒子、特效——它们会被画成实心形状。
2. 每个动作字段都是「运动段」：只描述**要看到的姿态和运动方向**，不描述物理原因（写"发梢指向画面顶边"，不写"有风"）。
3. 把该动的部位一个个点名并给幅度（"幅度清晰可见"、"划出清晰的弧线"）。不要写"其余保持不动"——那由外层模板负责。
4. 不要写转速、不要写"每秒"。不要写"砸在地面上"这类会引入地面的词。
5. attack 必须是一次完整动作：蓄力 → 发力 → 停住 → 收回到开头站姿。命中时武器指向角色面朝方向。
6. walk / run 都是原地循环动作，不位移；**如果角色持有武器，必须写明持武器手臂的摆动是有界的**："持武器的手臂随步伐小幅摆动，幅度约为空手那侧的一半；武器与前臂夹角不变、跟着手臂一起小幅摆动，尖端始终指向前下方，不抬到水平以上，不挥动、不举起"（实测：写"长剑划出弧线"模型就只演挥剑、腿不动；不写武器，模型就把武器画丢或变形；写"固定不动"模型不听、而且假 —— 关键是**幅度有上限、和步伐同周期**，否则循环点必跳）。腿是主角：抬腿幅度、交替、身体起伏。
7. run 必须写"身体前倾"，并写清抬起那条腿的形态：**膝盖朝前、小腿向后折叠、脚跟贴近臀部，不伸直、不向前踢出**（实测：只写"膝盖抬到腰际"，模型就演成直腿侧踢）。头发衣摆向后飘，不是向上。
8. idle 是原地待机：只有呼吸起伏和发丝衣摆轻微飘动，双脚双腿不动、武器不动。hurt 是受击：头肩向画面右侧缩一下、重心后坐，双脚原地，然后恢复站姿；⛔ 别写"上半身后仰"（会被画成转向镜头）。die 是死亡：踉跄 → 跪倒 → 侧向倒下 → 静止，**结尾停在倒下的姿势不回站姿**；⛔ 不写"地面"，写"倒在画面底部"。attack2 / attack3 是攻击连招的后两段，要与 attack 的剪影明显不同（横弧 / 竖劈 / 直刺）。
9. 用户会附上每个动作的「基准运动段」（经过实测的模板）。你的任务是在**保留其全部约束句**（原地不位移、武器保持原位、结尾回站姿之类）的前提下，结合角色外观把它写得更具体：点名这个角色的武器、发型、衣摆、配饰在动作中怎么动。不要删约束，不要引入模板里没有的新动作。
只输出 JSON，不要解释。"""


VLM_SYSTEM = """你是 2D 游戏动作动画的挑帧师。你会看到一张联络表：从一段视频等距抽出的格子，每格烧着帧号（f001 这种，1 起）。
输出 JSON：{"frames":[帧号(整数)...], "collapse":false, "thin_weapon":[帧号...], "notes":"一句话"}
规则（全部来自实测）：
1. frames 选恰好 N 个、递增、只能从图里出现的帧号里选。要表达完整动作。
   攻击：蓄力 → 发力 → 命中（停住）→ 收招；重心放在命中那一段，别选成"举刀×3 + 命中×1"。命中帧的武器/拳头必须指向角色面朝的方向；只有蓄力帧可以朝后。
   受击：站姿 → 被击中后缩 → 恢复站姿。死亡：站姿 → 踉跄/跪倒 → 倒下 → 静止，**最后一帧必须是倒下静止的姿势**。
2. collapse=true 当且仅当：发型/帽子头饰/面部/服装/身材比例明显变了、四肢变形（腿变成一根黑棍、多了少了一条腿、靴子形状变了）、
   武器消失/变形/变短/多出第二把、整个人发白发光、人物出画或腿脚被裁掉。
3. thin_weapon：武器变成一条细线（刃口转向镜头）的帧号——这些帧不能进 frames。
4. 只输出 JSON，不要解释。"""


# ⚑ 循环动作的质检（⚑ 用户 2026-09-09：奔跑演成踢腿、腿变棍、剑消失，可五项指标全绿 —— ⚑ 因为循环动作从没人看过）
LOOP_VLM_SYSTEM = """你是 2D 游戏循环动作动画的质检员。你会看到一张联络表：从一段视频等距抽出的格子，每格烧着帧号（f001 这种，1 起）。
用户会告诉你这段应该是什么动作，以及运动段描述。
输出 JSON：{"is_action": true, "collapse": false, "issues": ["f017 右腿伸直向前踢，不是跑"], "notes": "一句话"}
规则（全部来自实测）：
1. is_action：画面里演的到底是不是这个动作。
   奔跑必须看到：身体前倾、双腿交替、抬起的腿小腿向后折叠；如果是直腿向前/向侧面踢、原地站着只摆手、只有头发衣摆在动，就是 false。
   走路必须看到：双腿交替迈步、手臂摆动。待机必须几乎不动：只有呼吸起伏和发丝衣摆轻微飘动；腿在动就是 false。
2. collapse=true 当且仅当：发型/帽子头饰/面部/服装/身材比例明显变了；四肢变形（腿变成一根黑棍、多了少了一条腿、靴子形状/长度变了）；
   武器消失、变形、变短、多出第二把；整个人发白发光；人物出画或腿脚被裁掉。
3. issues 逐条写"帧号 + 问题"，没有就给空数组。
4. 只输出 JSON，不要解释。"""

DESCRIBE_SYSTEM = """你是游戏美术。看这张角色立绘，用一两句中文只描述角色外观：体型/头身比、性别年龄感、发型发色、服装款式和配色、
手里拿的武器（种类、长短、持握方式、在哪只手）、显眼的配饰（飘带、穗子、披风）。不要写背景、不要写画风评价、不要写动作建议。只输出这段描述。"""


def _vision_chat(system: str, text: str, png_path, model: str, log, timeout: int = 150, temperature: float = 0.2):
    """⚑ 一次带图的 OpenAI 兼容 chat 调用（⚑ 走 llm 凭据的 vision_model）。⚑ 返回原文；⚑ 失败返回 None（⚑ 调用方决定退路）。"""
    import base64
    import urllib.request
    import urllib.error
    c = _creds.get('llm')
    if not c:
        log('  ⚠ 没配 llm，跳过看图')
        return None
    base = (c['base'] or '').rstrip('/')
    model = model or c.get('vision_model') or c.get('model') or LLM_DEFAULT_MODEL
    b64 = base64.b64encode(Path(png_path).read_bytes()).decode()
    body = {'model': model, 'temperature': temperature, 'messages': [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': text},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}}]}]}
    req = urllib.request.Request(f'{base}/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + c['key']})
    log(f'  → {model} @ {base}  图 {len(b64) // 1024} KB  超时 {timeout}s')   # ⚑ 卡住时至少知道在等谁
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = json.loads(r.read().decode())['choices'][0]['message']['content']
        log(f'  ← {time.time() - t0:.1f}s')
        return model, txt
    except urllib.error.HTTPError as e:
        log(f'  ⚠ VLM HTTP {e.code}：{e.read().decode(errors="replace")[:300]}')
    except Exception as e:
        log(f'  ⚠ VLM 调用失败：{e}')
    return None


def _json_obj(txt: str):
    m = re.search(r'\{.*\}', txt or '', re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except Exception:
        d = None
    return d if isinstance(d, dict) else None


def vlm_describe(png_path, model: str, log) -> str:
    """⚑ 看立绘写角色外观一句话 —— ⚑ 上传/沿用立绘、一句话留空时，⚑ 给提示词扩写补上"角色长什么样"。⚑ 失败返回 ''。"""
    r = _vision_chat(DESCRIBE_SYSTEM, '描述这个角色的外观。', png_path, model, log, timeout=90, temperature=0.3)
    if not r:
        return ''
    s = r[1].strip().strip('`"「」').strip()
    return s[:300] if len(s) >= 10 else ''


def vlm_judge_loop(png_path, label: str, motion: str, model: str, log, timeout: int = 150):
    """⚑ 循环动作（待机/走/跑）质检：⚑ 是不是这个动作 ＋ 有没有崩。⚑ 失败返回 None（⚑ 只能放行，日志留痕）。"""
    r = _vision_chat(LOOP_VLM_SYSTEM, f'应该是的动作：{label}\n运动段：{motion}', png_path, model, log, timeout)
    if not r:
        return None
    model, txt = r
    d = _json_obj(txt)
    if d is None:
        log(f'  ⚠ VLM 没给合法 JSON ⇒ 放行\n  {txt[:300]}')
        return None
    issues = [str(x)[:120] for x in (d.get('issues') or []) if str(x).strip()][:8]
    return {'model': model, 'kind': 'loop', 'is_action': bool(d.get('is_action', True)), 'collapse': bool(d.get('collapse')),
            'issues': issues, 'notes': str(d.get('notes', ''))[:200], 'frames': [], 'thin_weapon': [], 'raw': txt[:600]}


def vlm_pick(png_path, motion: str, n: int, total: int, model: str, log, timeout: int = 150):
    """⚑ 让带视觉的模型看联络表：⚑ 挑帧（→ --at=）＋ 判崩坏 ＋ 找"刀变细线"。
    ⚑ 文档 §6 ④ 说"技能动画必须手点"——这一步就是把"手点"交给 VLM；⚠ 任何失败都退回自动挑帧。"""
    r = _vision_chat(VLM_SYSTEM, f'动作：{motion}\nN = {n}。视频共 {total} 帧。', png_path, model, log, timeout)
    if not r:
        log('  ⚠ ⇒ 退回自动挑帧')
        return None
    model, txt = r
    d = _json_obj(txt)
    if d is None:
        log(f'  ⚠ VLM 没给合法 JSON ⇒ 退回自动挑帧\n  {txt[:300]}')
        return None

    def ints(v):
        out = []
        for x in (v or []):
            s = str(x).strip().lstrip('fF')
            if s.isdigit() and 1 <= int(s) <= max(total, 1):
                out.append(int(s))
        return sorted(set(out))
    return {'model': model, 'frames': ints(d.get('frames')), 'collapse': bool(d.get('collapse')),
            'thin_weapon': ints(d.get('thin_weapon')), 'notes': str(d.get('notes', ''))[:200], 'raw': txt[:600]}


def _chat(provider, model, system, user, log, temperature=0.4, timeout=60):
    """⚑ 一次 OpenAI 兼容 chat 调用。⚑ provider = 'glm' | 'llm'（⚑ 没指定：llm → glm）。⚑ 失败返回 None（⚑ 调用方决定退路）。"""
    import urllib.request
    import urllib.error
    c = _creds.get(provider) if provider else None
    if not c:
        c = _creds.get('llm') or _creds.get('glm')
        provider = 'llm' if _creds.get('llm') else 'glm'
    if not c:
        log('  ⚠ 没配 llm / glm')
        return None
    base = (c['base'] or 'https://open.bigmodel.cn/api/paas/v4').rstrip('/')
    model = model or c.get('model') or (LLM_DEFAULT_MODEL if provider == 'glm' else '')
    log(f'  ⚑ {provider}（{c["_from"]}）· {base} · {model}')
    body = {'model': model, 'temperature': temperature,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
    req = urllib.request.Request(f'{base}/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + c['key']})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        log(f'  ⚠ HTTP {e.code}：{e.read().decode(errors="replace")[:300]}')
    except Exception as e:
        log(f'  ⚠ 调用失败：{e}')
    return None


MOTION_SYSTEM = """你是 2D 游戏角色动作动画的提示词工程师。用户给一个动作名和角色描述，你只输出一段中文「运动段」（100~200 字，纯文本，不要标题、不要 JSON、不要解释）。
规则（全部来自实测，违反就出废片）：
1. 只描述**要看到的姿态和运动方向**，不描述物理原因（写"每一缕头发笔直朝向画面正上方"，不写"有风"）。
2. 该动的部位一个个点名并给幅度（"幅度清晰可见"、"划出清晰的弧线"）。不写"其余保持不动"——外层模板负责。
3. 不写转速、不写"每秒"；不写"砸在地面上"这类会引入地面的词，只写方向。
4. 一次性动作：蓄力 → 发力 → 停住 → 收回到开头站姿；命中时武器/拳头指向角色面朝方向。走路：原地踏步、双腿交替、双臂摆动、身体起伏，不位移。
   奔跑：原地、身体前倾，抬起那条腿**膝盖朝前、小腿向后折叠、脚跟贴近臀部，不伸直、不向前踢出**（实测：只写"抬膝到腰际"会演成直腿侧踢）；头发衣摆向后飘。
   走路和奔跑如果角色持有武器，必须写有界的摆动："持武器的手臂随步伐小幅摆动，幅度约为空手那侧的一半；武器与前臂夹角不变，尖端始终指向前下方，不抬到水平以上，不挥动、不举起"——不写，模型就把武器画丢或变形；写"固定不动"假而且模型不听。
5. 侧视图里别写"上半身后仰"（会被画成转向镜头），用"下蹲、双脚原地"这类中性描述。"""


def llm_motion(action: str, label: str, character: str, hint: str, model: str, log, provider='glm'):
    """⚑ 让 GLM（免费）写某个动作的运动段；⚑ DeepSeek 只负责审和判。⚑ 失败返回 None（⚑ 调用方退回内置模板）。"""
    txt = _chat(provider, model, MOTION_SYSTEM,
                f'动作：{label}（{action}）\n角色：{character or "未指定"}\n补充要求：{hint or "无"}', log, temperature=0.5)
    if not txt:
        return None
    txt = txt.strip().strip('`').strip()
    return txt if 20 <= len(txt) <= 600 else None


def llm_prompts(sentence: str, style: str, actions, model: str, log, provider=None):
    """⚑ 一句话 → 立绘描述 + 各动作运动段（JSON）。⚑ provider 默认 llm（DeepSeek；⚑ 用户裁决：GLM 只负责免费抽视频）。
    ⚠ 任何失败都退回模板 —— ⛔ 这步不能成为花钱前的阻塞点。
    ⛔ 一句话为空（上传立绘没写描述）时**不扩写**：⚑ 实测 2026-09-09（job 0909-175645）LLM 拿到空角色就凭空编了个
       "橙发运动少女"，运动段里没有剑 ⇒ 顶掉内置模板后剑丢形、腿演成踢腿，¥0.40 废片。"""
    if not (sentence or '').strip():
        log('  ⚠ 一句话为空（上传立绘没写角色描述）⇒ 不扩写，用内置模板（LLM 不知道角色长什么样，只会瞎编）')
        return None
    txt = _chat(provider or ('llm' if _creds.get('llm') else 'glm'), model, LLM_SYSTEM,
                f'角色：{sentence}\n风格：{style}\n需要的动作：{", ".join(actions)}', log)
    if not txt:
        log('  ⚠ 扩写失败 ⇒ 用内置模板')
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


def optimize_prompts(actions, sentence: str, style: str, portrait_png, *, labels=None, bases=None, hints=None,
                     extras=None, character: str = '', model: str = '', provider: str = None, view: str = 'side'):
    """⚑⚑ 出片**之前**把各动作的运动段写好给用户看（⚑ 用户 2026-09-09：先优化完提示词再去做视频）。⚑ 同步、不花视频钱。
    ⚑ 角色描述：一句话 → 没有就让 VLM 看立绘写一句（⚑ 上传/沿用立绘时唯一能知道"手里有剑"的办法）→ 还没有就只给模板。
    ⚑ 每个动作都带「基准运动段」（模板或用户改过的），LLM 只许在保留约束的前提下结合角色外观写具体。⚠ 任何失败都退回基准。"""
    labels, bases, hints, extras = labels or {}, bases or {}, hints or {}, extras or {}
    logs = []
    log = logs.append
    out = {'character': (character or sentence or '').strip(), 'character_source': 'given' if character else ('sentence' if (sentence or '').strip() else ''),
           'motions': {}, 'source': {}, 'prompts': {}, 'model': '', 'log': logs}
    base_of = {a: (bases.get(a) or '').strip() or ACTIONS.get(a, {}).get('motion', '') for a in actions}
    if not out['character'] and portrait_png and Path(portrait_png).exists() and _creds.get('llm'):
        log('───── 看立绘写角色描述')
        out['character'] = vlm_describe(portrait_png, '', log)
        out['character_source'] = 'vlm' if out['character'] else ''
        if out['character']:
            log(f'  👁 {out["character"]}')
    d = None
    if out['character'] and (_creds.get('llm') or _creds.get('glm')):
        log('───── 大模型写运动段')
        V = view_of(view)
        lines = [f'角色：{out["character"]}', f'风格：{style or DEFAULT_STYLE}', f'游戏视角：{V["label"]}（{V["note"]}）',
                 '需要的动作（key｜名字｜基准运动段｜用户补充要求）：']
        for a in actions:
            lines.append(f'- {a}｜{labels.get(a) or ACTIONS.get(a, {}).get("label", a)}｜{base_of[a] or "（无基准，按规则写）"}｜{hints.get(a) or "无"}')
        prov = provider or ('llm' if _creds.get('llm') else 'glm')
        txt = _chat(prov, model, LLM_SYSTEM, '\n'.join(lines), log, timeout=120)
        d = _json_obj(txt) if txt else None
        if d is None:
            log(f'  ⚠ 大模型没给合法 JSON ⇒ 用基准\n  {(txt or "")[:300]}')
        else:
            c = _creds.get(prov) or {}
            out['model'] = model or c.get('model') or LLM_DEFAULT_MODEL
    for a in actions:
        m = str((d or {}).get(a) or '').strip()
        ok = 20 <= len(m) <= 800
        out['motions'][a] = m if ok else base_of[a]
        out['source'][a] = 'llm' if ok else ('base' if bases.get(a) else 'template')
        out['prompts'][a] = build_prompt(a, extras.get(a, ''), out['motions'][a], view=view) if a in ACTIONS or out['motions'][a] else ''
    return out


# ⚑ 立绘描述扩写（⚑ 用户 2026-09-09：出图的提示词也要大模型优化、而且要先看到）。⚑ 规则全部来自文档 §1.2 的实测
PORTRAIT_SYSTEM = """你是 2D 游戏角色立绘的提示词工程师。用户给一句话（角色 + 想法）和画风，你只输出一段中文「外观描述」（40~120 字，纯文本，不要标题、不要解释）。
规则（全部来自实测，违反就出废图）：
1. 只写外观：体型/头身感、性别年龄感、发型发色、服装款式与配色（2~3 个主色）、武器（种类、长短、在哪只手、怎么握）、1~2 个显眼配饰（飘带/穗子/斗笠）。
2. ⛔ 不写雾气、光晕、粒子、特效、烟、火、水墨晕染、飞溅——会被画成实心形状贴在身上。画风词（如"国风水墨"）只在开头出现一次。
3. ⛔ 不写背景、地面、光线、氛围、镜头、动作、姿势、朝向、"全身"、"灰底"——这些由外层模板负责，重复写会打架。
4. 把一句话里的想法落到具体外观上："刀客"⇒ 写清刀的形制（宽背单刀 / 长柄大刀）和持握；"国风"⇒ 写清服装形制（劲装 / 长衫 / 束腰 / 绑腿）。
5. 用户给了「基准描述」时，保留其全部要素，只按补充要求改。"""


# ⚑ 图生图（改一版）的改图指令 —— ⚑ 用户 2026-09-09：给了参考图，提示词同样要大模型优化、先看到
PORTRAIT_EDIT_SYSTEM = """你是游戏立绘「图生图改版」的提示词工程师。用户给一句「改什么」，你只输出一段中文改图指令（30~100 字，纯文本，不要标题、不要解释）。
规则（全部来自实测）：
1. 先逐项写清**要改的部位 → 改成什么**，形制和颜色要具体（"刀改成宽背单刀，刀背厚、刀尖上翘，刃面朝观众" 而不是 "换把刀"）。
2. 再写一句"其余全部保持不变：同一角色、同一姿势、同一体型比例、同一朝向、同一构图大小、同一灰底、同一画风"。
3. ⛔ 不引入特效、光晕、背景、动作、表情变化。⛔ 不写与改动无关的外观描述（会让模型顺手改掉）。
4. 如果用户是要"换成另一个角色"，就写清新角色的外观（发型发色、服装形制与配色、武器与持握），并同样保留姿势 / 构图 / 灰底 / 画风不变。
5. 用户给了「基准指令」时，保留其全部要素，只按补充要求改。"""
PORTRAIT_EDIT_SUFFIX = '保持人物的姿势、体型比例、朝向、构图和背景颜色完全不变，只修改上面提到的部分。'


def optimize_portrait(sentence: str, style: str, *, hint: str = '', base: str = '', model: str = '', provider: str = None,
                      view: str = 'side', weapon_pose: str = 'side', edit: bool = False):
    """⚑ 一句话 → 立绘外观描述（⚑ edit=True：「改什么」→ 改图指令），⚑ 出图前给用户看。
    ⚑ 失败退回一句话本身（⛔ 不能成为花钱前的阻塞点）。"""
    logs = []
    log = logs.append
    sentence = (sentence or '').strip()
    out = {'desc': (base or sentence), 'source': 'base' if base else 'sentence', 'model': '', 'log': logs, 'edit': edit}
    if not sentence and not base:
        return out
    if _creds.get('llm') or _creds.get('glm'):
        prov = provider or ('llm' if _creds.get('llm') else 'glm')
        if edit:
            user = f'改什么：{sentence or "（无）"}\n基准指令：{base or "无"}\n补充要求：{hint or "无"}'
        else:
            user = f'一句话：{sentence or "（无）"}\n画风：{style or DEFAULT_STYLE}\n基准描述：{base or "无"}\n补充要求：{hint or "无"}'
        txt = _chat(prov, model, PORTRAIT_EDIT_SYSTEM if edit else PORTRAIT_SYSTEM, user, log, temperature=0.5, timeout=90)
        s = (txt or '').strip().strip('`"「」').strip()
        if 15 <= len(s) <= 400:
            c = _creds.get(prov) or {}
            out.update(desc=s, source='llm', model=model or c.get('model') or LLM_DEFAULT_MODEL)
        else:
            log(f'  ⚠ 大模型没给出可用{"指令" if edit else "描述"} ⇒ 用{"基准" if base else "原句"}\n  {(txt or "")[:200]}')
    if edit:
        out['prompt'] = out['desc'].strip() + '\n' + PORTRAIT_EDIT_SUFFIX
    else:
        out['prompt'] = PORTRAIT_TMPL.format(desc=out['desc'].strip('。 '), style=style or DEFAULT_STYLE,
                                             camera=view_of(view)['portrait'],
                                             weapon=WEAPON_POSE.get(weapon_pose, WEAPON_POSE['side']))
    return out


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
        # ⚑ 每动作可覆盖 label / motion（⚑ 攻击连招里 attack 叫「攻击1」；⚑ motion 是用户在网页看过/改过的运动段 ⇒ 最高优先）
        #   ⚑ 换成新 dict 再改，⛔ 别动模块级 ACTIONS
        for a in spec['actions']:
            o = (spec.get('options') or {}).get(a) or {}
            if a in self.actions and (o.get('label') or (o.get('motion') or '').strip()):
                self.actions[a] = {**self.actions[a],
                                   **({'label': str(o['label'])[:20]} if o.get('label') else {}),
                                   **({'motion': o['motion'].strip(), 'motion_from': 'user'} if (o.get('motion') or '').strip() else {})}
        self.state = {
            'id': self.id, 'created': _now(), 'status': 'queued',
            'mode': spec['mode'], 'prompt': spec.get('prompt', ''), 'style': spec.get('style', ''),
            'view': spec.get('view') if spec.get('view') in VIEWS else 'side',
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
            by['角色'] = 0.0 if (self.spec.get('portrait_upload') and not self.spec.get('portrait_from')) else portrait_price()
            total += by['角色']
            for a in self.state['actions']:
                p = video_price(*self._prov(a)[:1], self._opt(a, 'res'), int(self._opt(a, 'dur')), self._prov(a)[1])
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
                user_desc = (self.spec.get('portrait_desc') or '').strip()   # ⚑ 网页上看过/改过的立绘描述 ⇒ 最高优先
                if user_desc:
                    self.state['prompts'] = {'portrait': user_desc}
                    self.state['portrait_desc_from'] = 'user'
                # ⚑ 任务内扩写：⚑ 立绘描述已由用户审过且没有动作要写 ⇒ 不用调
                need_actions = [a for a in self.state['actions'] if self.actions[a].get('motion_from') != 'user']
                if o.get('prompt_llm') and (need_actions or not user_desc):
                    self.log('\n───── 提示词扩写（LLM）')
                    d = llm_prompts(self.state['prompt'], self.state['style'] or DEFAULT_STYLE,
                                    self.state['actions'], o.get('prompt_llm_model') or '', self.log,
                                    provider=o.get('prompt_llm_provider'))
                    if d:
                        for a in self.state['actions']:            # ⚑ 用户看过的运动段不许被任务内扩写顶掉
                            if self.actions[a].get('motion_from') == 'user':
                                d.pop(a, None)
                        if user_desc:                              # ⚑ 用户审过的立绘描述同理
                            d['portrait'] = user_desc
                        self.state['prompts'] = {**(self.state.get('prompts') or {}), **d}
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
            (self.dir / 'prompt_portrait_edit.txt').write_text(instr + '\n' + PORTRAIT_EDIT_SUFFIX, encoding='utf-8')
            self.state['artifacts']['portrait_base'] = self.spec['portrait_from']
            self.state['artifacts']['portrait_prompt'] = instr
            argv = [TOOLS / 'artgen' / 'edit.py', self.spec['portrait_from'], 'out/01_portrait.png',
                    '--promptfile=prompt_portrait_edit.txt']
            # ⚑ 图像模型：任务选项 > relay 凭据里的 model > 脚本默认（⚠ 各家中转站模型名不同，⛔ 别信脚本默认）
            im = self.state['options'].get('image_model') or c.get('model')
            if im:
                argv.append(f'--model={im}')
            self.step('portrait', '立绘：图生图改版', argv, cost=portrait_price(), who='角色', env=env)
            self.state['artifacts']['portrait'] = 'out/01_portrait.png'
            self.save()
            return
        desc = (self.state.get('prompts') or {}).get('portrait') or self.state['prompt']
        pose = WEAPON_POSE.get(self.state['options'].get('weapon_pose', 'side'), WEAPON_POSE['side'])   # ⚑ 默认垂身侧（跑步/走路自然）
        item = {'id': 1, 'name': 'portrait', 'desc': '立绘', 'size': '1024x1024',
                'transparent': False, 'quality': self.state['options'].get('image_quality', 'medium'),
                'prompt': PORTRAIT_TMPL.format(desc=desc.strip('。 '), camera=view_of(self.state['view'])['portrait'],
                                               style=self.state['style'] or DEFAULT_STYLE, weapon=pose)}
        self.state['artifacts']['portrait_prompt'] = item['prompt']
        # ⚠ gen.py 要的是 {"items": [...]}，⛔ 不是裸数组（⚑ 预检时崩过一次）
        (self.dir / 'prompts.json').write_text(json.dumps({'items': [item]}, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
        argv = [TOOLS / 'artgen' / 'gen.py', '1', '--force']
        im = self.state['options'].get('image_model') or c.get('model')
        if im:
            argv += ['--model', im]
        self.state['artifacts']['portrait_model'] = im or '(脚本默认)'
        # ⚑ gen.py 只认环境变量 ⇒ ⚑ 把 _creds 里的 relay 注进去（⚑ 网页端配的 key 由此生效）
        self.step('portrait', '出立绘', argv, cost=portrait_price(), who='角色', env=env)
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
            motion = self._motion(a)
            pf = self.dir / f'prompt_{a}.txt'
            pf.write_text(build_prompt(a, self.state['options'].get(a, {}).get('extra', ''), motion, view=self.state['view']),
                          encoding='utf-8')
            art.update(prompt=f'prompt_{a}.txt', provider=prov, model=model, res=res, dur=dur,
                       motion_from=A.get('motion_from') or ('llm' if (self.state.get('prompts') or {}).get(a) else 'template'))
            price = video_price(prov, res, dur, model)
            # ⚑ 尾帧钉不钉：⚑ provider 说了算（CogVideoX 钉了就静止）＋ 动作说了算（⚑ 死亡钉了就会站起来）
            use_last = VIDEO_PROVIDERS.get(prov, {}).get('last', True) and A.get('last', True)
            self.step(f'{a}.video', f'{who}：出片（{prov} {model} {res}/{dur}s，{"¥%.2f" % price if price else "免费"}{"" if use_last else "，无首尾帧"}）',
                      [TOOLS / 'gen_video.py', f'--img={src}'] + ([f'--last={src}'] if use_last else []) +
                      [f'--tag={a}', f'--promptfile=prompt_{a}.txt', f'--provider={prov}', f'--model={model}',
                       f'--res={res}', f'--dur={dur}'],
                      cost=price, who=who, spend_on='submit')
        else:
            # ⚑ demo 只有 walk / attack 两种合成素材；⚑ 自定义/连招动作一律用 attack 那段顶
            self.step(f'{a}.synth', f'{who}：合成测试视频（⛔ 不调 API）',
                      [HERE / 'synth.py', 'walk' if A.get('pick') == 'loop' else 'attack', mp4])
        art['video'] = mp4
        ctxt = self.step(f'{a}.contact', f'{who}：联络表', [TOOLS / '_contact.py', mp4], must=False)
        art['contact'] = f'work/anim/{a}_联络表.png'
        mt = re.search(r'共 (\d+) 帧', ctxt or '')
        total = int(mt.group(1)) if mt else 0
        cell = self._opt(a, 'cell')
        n_frames = int(self._opt(a, 'frames'))
        # ⚑⚑ 可选：VLM 看联络表 —— ⚑ 一次性动作：挑帧变 --at= ＋ 判崩坏；⚑ 循环动作：判「是不是这个动作」＋ 判崩坏
        #   （⚑ 2026-09-09 之前循环动作从没人看过 ⇒ 奔跑演成踢腿、腿变棍、剑消失照样全绿）。
        #   ⚑ 判废**不立刻 raise**：⚑ 钱已经花了，图集/GIF 是本地免费的 ⇒ 先出完让用户自己看，⚑ 再停后续动作（⛔ 别继续花钱）
        at_arg, verdict = None, None
        # ⚑ 试运行承诺「不调任何 API」⇒ ⛔ demo 模式不叫 VLM（2026-09-09 抓到：火柴人也被送去 DeepSeek 看了一眼）
        if self.state['options'].get('vlm_pick') and self.state['mode'] == 'generate':
            vc = f'work/anim/{a}_vlm联络表.png'
            self.step(f'{a}.vcontact', f'{who}：密联络表（给 VLM 看，24 格）',
                      [TOOLS / '_contact.py', mp4, '--n=24', '--cols=6', f'--out={vc}'], must=False)
            is_loop = A.get('pick') == 'loop'
            self.log(f'\n───── {who}：VLM {"质检（是不是这个动作 / 有没有崩）" if is_loop else "挑帧 / 判崩坏"}')
            if is_loop:
                res = vlm_judge_loop(self.dir / vc, who, self._motion(a), self.state['options'].get('vlm_model'), self.log)
            else:
                res = vlm_pick(self.dir / vc, self._motion(a), n_frames, total,
                               self.state['options'].get('vlm_model'), self.log)
            if res:
                art['vlm'] = res
                if is_loop:
                    self.log(f'  ⚑ {res["model"]}：is_action={res["is_action"]} collapse={res["collapse"]} —— {res["notes"]}'
                             + ''.join(f'\n    · {x}' for x in res['issues']))
                    if res['collapse']:
                        verdict = f'角色崩坏（{res["notes"]}）'
                    elif not res['is_action']:
                        verdict = f'演的不是「{who}」（{res["notes"]}）'
                else:
                    self.log(f'  ⚑ {res["model"]}：frames={res["frames"]} collapse={res["collapse"]} '
                             f'thin={res["thin_weapon"]} —— {res["notes"]}')
                    if res['collapse']:
                        verdict = f'角色崩坏（{res["notes"]}）'
                    if len(res['frames']) == n_frames:
                        at_arg = '--at=' + ','.join(str(v) for v in res['frames'])
                    else:
                        self.log(f'  ⚠ VLM 给了 {len(res["frames"])} 帧、要 {n_frames} 帧 ⇒ 退回 --pick={A.get("pick")}')
                art['verdict'] = verdict
            self.save()
        # ⚑⚑ 一律出**单行**图集（--cols=帧数）：⚑ `_anim_check` 按单行切格（⚠ 4×2 会把第一行脚底算到整图高上，
        #   ⛔ 报"差 256px"），⚑ 引擎接单行也最省事。⚑ 多行支持只留给「提取」页上传的图集。
        self.step(f'{a}.sheet', f'{who}：视频 → 图集',
                  [TOOLS / 'vid2anim.py', mp4, f'--tag={a}', f'--frames={n_frames}', f'--cols={n_frames}',
                   f'--pick={self._opt(a, "pick")}', f'--cell={cell}'] + ([at_arg] if at_arg else []),
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
        if verdict and self.state['mode'] == 'generate' and self.state['options'].get('stop_on_collapse', True):
            raise RuntimeError(f'{who}：VLM 判废 —— {verdict} ⇒ 停止后续动作，别继续花钱。'
                               f'图集/GIF 已出，自己看一眼 work/anim/{a}_vlm联络表.png；改提示词重出')

    def _motion(self, a) -> str:
        """⚑ 生效的运动段：⚑ 用户看过/改过的（options[a].motion，已合进 self.actions）> 任务内 LLM 扩写 > 模板。"""
        A = self.actions[a]
        if A.get('motion_from') == 'user':
            return A['motion']
        return (self.state.get('prompts') or {}).get(a) or A['motion']

    def _manifest(self):
        build_manifest(self.dir, self.state)


def build_manifest(job_dir: Path, state: dict) -> dict:
    """⚑ 从 state 生成 manifest.json（⚑ 任务结束时调；⚑ 重切帧数后也要重生成 ⇒ 抽成模块函数）。⚑ 动作定义取 state.action_defs（含 label/motion 覆盖）。"""
    V = view_of(state.get('view'))
    m = {'id': state['id'], 'prompt': state['prompt'], 'mode': state['mode'],
         'view': {'key': state.get('view', 'side'), 'label': V['label'], 'dirs': V['dirs'], 'facing': 'left', 'mirror_for_right': True},
         'cost': state['cost'], 'animations': {}}
    defs = state.get('action_defs') or {}
    for a in state['actions']:
        art, A = state['artifacts'].get(a, {}), defs.get(a) or ACTIONS.get(a) or {'label': a}
        n = int(art.get('frames') or 0)
        one_shot = (art.get('pick') or A.get('pick')) != 'loop'
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
    combo = [k for k in state.get('combo') or [] if k in m['animations']]
    if len(combo) >= 2:
        m['combo'] = {'order': combo, 'cancel_from': CANCEL_FROM,
                      'note': '段间衔接靠首尾帧同图；取消窗口开在收招帧；输入缓冲在窗口内记下、播完立刻接'}
    (job_dir / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    state['artifacts']['manifest'] = 'manifest.json'
    return m


def reslice(job_id: str, action: str, frames: int, pick: str = None) -> dict:
    """⚑⚑ 用已出的视频**重切**图集（⚑ 用户 2026-09-09：换帧数不该再花钱）。⚑ 覆盖 out/anim/{a}.png 和 GIF，
    ⚑ 重算 foot_trim / 五项指标，⚑ 追加一步记录，⚑ 重生成 manifest。⚑ 返回新 state。⚠ 一次性动作换了帧数就用不上 VLM 挑的帧 ⇒ 退回 even。"""
    d = JOBS / job_id
    st = load_job(job_id)
    if not st:
        raise ValueError('任务不存在')
    if st.get('status') in ('running', 'queued'):
        raise ValueError('任务还在跑，跑完再切')
    art = (st.get('artifacts') or {}).get(action)
    if not isinstance(art, dict) or not art.get('video') or not (d / art['video']).exists():
        raise ValueError(f'{action} 没有可用的视频')
    A = (st.get('action_defs') or {}).get(action) or ACTIONS.get(action) or {}
    who = A.get('label', action)
    frames = max(2, min(24, int(frames)))
    pick = pick or art.get('pick') or A.get('pick') or 'even'
    cell = 'x'.join(str(v) for v in (art.get('cell') or [192, 256]))
    argv = [str(TOOLS / 'vid2anim.py'), art['video'], f'--tag={action}', f'--frames={frames}', f'--cols={frames}',
            f'--pick={pick}', f'--cell={cell}']
    vf = (art.get('vlm') or {}).get('frames') or []
    if pick != 'loop' and len(vf) == frames:                      # ⚑ 帧数正好等于 VLM 挑的 ⇒ 还用它的
        argv.append('--at=' + ','.join(str(v) for v in vf))
    env = {**os.environ, 'ANIMPIPE_ROOT': str(d), 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'}

    def run(a):
        p = subprocess.run([sys.executable] + a, cwd=str(d), env=env, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        return p.returncode, (p.stdout or '') + (p.stderr or '')
    code, out = run(argv)
    with open(d / 'log.txt', 'a', encoding='utf-8') as f:
        f.write(f'\n───── {who}：重切 {frames} 帧（{pick}，不花钱）\n  $ vid2anim.py {" ".join(argv[1:])}\n'
                + ''.join('  ' + ln + '\n' for ln in out.splitlines()))
    if code != 0:
        raise RuntimeError(f'重切失败（vid2anim 退出码 {code}）：{out[-300:]}')
    r = parse_vid2anim(out)
    art.update(frames=frames, cols=r.get('cols', frames), sheet_info=r, pick=pick, resliced=int(time.time()))
    art['foot_trim'] = foot_trims(d / art['sheet'], art['cell'], art['cols'], frames)
    code2, out2 = run([str(TOOLS / 'anim_bench.py'), f'--sheet={art["sheet"]}', f'--cell={cell}'])
    if code2 == 0:
        art['metrics'] = (parse_bench(out2) or {}).get('ours') or art.get('metrics')
    st.setdefault('steps', []).append({'key': f'{action}.reslice', 'label': f'{who}：重切 {frames} 帧（{pick}，不花钱）',
                                       'status': 'done', 'started': _now(), 'ended': _now(), 'cost_est': 0.0, 'code': 0, 'result': r})
    build_manifest(d, st)
    with _lock:
        (d / 'state.json').write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    return st


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
def sweep_stale():
    """⚑ 服务重启 ⇒ 线程全没了 ⇒ ⚑ 把还写着 running/queued 的任务标成失败，⛔ 别让它永远转圈。"""
    n = 0
    for st in list_jobs():
        if st.get('status') in ('running', 'queued'):
            st['status'], st['error'] = 'failed', '服务重启，任务被中断（可重新提交）'
            for s in st.get('steps', []):
                if s.get('status') == 'running':
                    s['status'] = 'failed'
            (JOBS / st['id'] / 'state.json').write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
            n += 1
    return n


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
