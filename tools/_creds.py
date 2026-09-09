# -*- coding: utf-8 -*-
"""⚑⚑⚑ **凭据统一层** —— ⚑ 四家 API 的 key 从**一个地方**读，⚑ 并回答"现在能做什么"。

```bash
python tools/_creds.py                 # ⚑ 能力报告（⚑ 缺哪家、⚑ 缺了会怎样）
python tools/_creds.py --set=wan       # ⚑ 交互式填一家的 key（⚑ 存用户目录，⛔ 不进仓库）
```
```python
from _creds import get, probe, CAN
c = get('relay')   # {'base':…, 'key':…}
if CAN['portrait']: ...
```

## ⚠⚠⚠ 为什么要这一层

⚑ 现在凭据散在**两处**：⚑ 中转站在 `.env`、⚑ 其余四家在 `config.json` 的 `providers.*`
  ⇒ ⚑ 每个脚本各写一份读取（⚑ `gen_image.py` 有两个函数、⚑ `gen_video.py` 一个、⚑ `artgen/gen.py` 一个）
  ⇒ ⚠ 加一家、⚑ 换存放位置，⛔ 得改四个文件。

⚑⚑ **更要紧的是「能不能做」这件事得有人回答** —— ⚑ 四家⛔ 不是「配哪个用哪个」，
  ⚑ 每一步有它**必须**用的那家（⚑ 见下面 CAPS）。⚠ 只配了 GLM 就出片 ⇒ ⚑ 循环闭不上，
  ⚑ 而且是**出完片才发现**（⚑ 钱已经花了）⇒ ⇒ ⚑ 要在**发之前**就说清楚。

## ⚑⚑ 四家的分工（⛔ 这不是偏好，是硬约束）

```
relay   中转站 gpt-image   ⚑⚑ 立绘唯一解 —— ⚑ 只有它能**喂参考图定画风** ＋ 出透明底
                          ⚠ 纯文字描述画风**必滑成写实**（§25.2）⇒ ⛔ 别指望别家顶上
wan     阿里万相           ⚑⚑ 出片唯一解 —— ⚑ 只有它能**钉首尾帧**（`--last`）
                          ⚠ 循环动画首尾对不上 ＝ 废片；⚑ 而且它**不改原图**（⚑ 标记色能守住）
ark     火山 seedance     ⚑ 抽结构用。⚠⚠ 它会**重新生成角色** ⇒ ⛔ 别拿它定稿
glm     智谱             ⚑ 免费抽卡。⚠ 只筛得出**构图**，⛔ 筛不出稳定性（⚑ 按"抖不抖"淘汰会误杀）
```

## ⚠ 查找顺序（⚑ 先找到的赢）

```
① 环境变量                     ⚑ CI / 临时覆盖
② ~/.gamegen/creds.json        ⚑⚑ 工具化之后的正式位置（⛔ 不在仓库里，⚑ 发给别人不会泄漏）
③ <项目>/.env ＋ config.json    ⚑ 现在的位置 ⇒ ⚑ **向后兼容**，⛔ 老脚本不用改
```
⚠⚠ ⛔ key **绝不能打进 exe / 提交进仓库** —— ⚑ 所以 `--set` 一律写 ②。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚑ 项目级凭据（③ .env / config.json）一律在**当前工作目录**找 —— ⛔ 不是脚本位置。
#   ⚑ 这样一台机器上多个项目各用各的 key，⚑ 而工具本身可以装在任何地方。
ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
USER_CFG = os.path.join(os.path.expanduser('~'), '.gamegen', 'creds.json')

# ⚑ 别名 → (config.json 里的 providers 键, 环境变量前缀)
#   ⚑ relay 特殊：⚑ 它在 .env 里，⛔ 不在 config.json
ALIASES = {
    'relay': (None, 'OPENAI'),
    'wan': ('dashscope', 'DASHSCOPE'),
    'dashscope': ('dashscope', 'DASHSCOPE'),
    'ark': ('doubaoVideo', 'ARK'),
    'doubaoVideo': ('doubaoVideo', 'ARK'),
    'glm': ('zhipu', 'ZHIPU'),
    'zhipu': ('zhipu', 'ZHIPU'),
    'minimax': ('minimax', 'MINIMAX'),
    'audio': ('doubaoAudio', 'DOUBAO_AUDIO'),
    # ⚑ 提示词扩写用的大模型（⚑ OpenAI 兼容：智谱 / DeepSeek / 任意中转），⚑ 和出片用的 glm 分开配
    #   ⚑ 多一个 model 字段（⚑ 环境变量 LLM_MODEL）
    'llm': ('llm', 'LLM'),
}

# ⚑ 能做什么 ← 需要哪几家。⚑ 缺了的后果写在第三项（⚠ 要在**花钱之前**告诉人）
CAPS = {
    'portrait': (['relay'], [], '出立绘（⚑ 喂参考图定画风 ＋ 透明底）'),
    'video': (['wan'], [], '出片定稿（⚑ 能钉首尾帧 ⇒ 循环闭得上）'),
    'draft_free': ([], ['glm'], '免费抽构图（⚑ 省万相额度）'),
    'draft_struct': ([], ['ark'], '抽结构（⚠ 会重画角色，⛔ 只能抽不能定稿）'),
    'cheap_img': ([], ['minimax'], '便宜图（¥0.025/张，⚠ 参考图只认人像）'),
}


def _read_dotenv(path):
    if not os.path.exists(path):
        return {}
    out = {}
    with io.open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with io.open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _sources():
    """⚑ 三处来源各读一遍。⛔ 别缓存到模块级 —— ⚑ `--set` 之后要能立刻看到新值。"""
    return (
        os.environ,
        _read_json(USER_CFG).get('providers', {}),
        {**_read_json(os.path.join(ROOT, 'config.json')).get('providers', {}),
         '_dotenv': _read_dotenv(os.path.join(ROOT, '.env'))},
    )


def get(name):
    """→ ⚑ `{'base':…, 'key':…, …}`；⛔ 没配返回 None（⚠ 调用方自己决定是致命还是降级）。"""
    if name not in ALIASES:
        raise KeyError(f'不认识的 provider "{name}"，可选：{list(ALIASES)}')
    cfg_key, env_prefix = ALIASES[name]
    env, user, proj = _sources()
    # ⚑⚑ 别名互通：⚑ `--set=wan` / 网页端存的是 `wan`，⚑ 而 gen_video.py 按 `dashscope` 来取 ——
    #   ⛔ 只查一个名字会漏（⚑ 实测 get('dashscope') 找不到 wan 那条 ⇒ 退回读 config.json ⇒ FileNotFoundError）。
    #   ⇒ ⚑ 同一 cfg_key 下的所有别名都查一遍。
    names = [name] + [n for n, (ck, _) in ALIASES.items() if ck and ck == cfg_key and n != name]
    if cfg_key and cfg_key not in names:
        names.append(cfg_key)

    # ① 环境变量
    k = env.get(f'{env_prefix}_API_KEY')
    if k:
        return {'base': (env.get(f'{env_prefix}_BASE_URL') or '').rstrip('/'), 'key': k,
                'workspace': env.get(f'{env_prefix}_WORKSPACE_ID', ''),
                'model': env.get(f'{env_prefix}_MODEL', ''),
                'vision_model': env.get(f'{env_prefix}_VISION_MODEL', ''), '_from': 'env'}
    # ② 用户目录
    d = next((user[n] for n in names if user.get(n)), None)
    if d and d.get('apiKey'):
        return {'base': (d.get('baseUrl') or '').rstrip('/'), 'key': d['apiKey'],
                'workspace': d.get('workspaceId', ''), 'model': d.get('model', ''), 'vision_model': d.get('vision_model', ''),
                'price': d.get('price'), '_from': 'user'}   # ⚑ price：⚑ 用户买了包的话按包价记账（⚑ 如智谱 ¥10/100 次）
    # ③ 项目（⚑ 向后兼容）
    if name == 'relay':
        e = proj.get('_dotenv', {})
        if e.get('OPENAI_API_KEY'):
            return {'base': (e.get('OPENAI_BASE_URL') or '').rstrip('/'),
                    'key': e['OPENAI_API_KEY'], 'workspace': '', '_from': 'project/.env'}
        return None
    d = next((proj[n] for n in names if isinstance(proj.get(n), dict)), None)
    if d and d.get('apiKey'):
        return {'base': (d.get('baseUrl') or '').rstrip('/'), 'key': d['apiKey'],
                'workspace': d.get('workspaceId', ''), 'model': d.get('model', ''), 'vision_model': d.get('vision_model', ''),
                '_from': 'project/config.json'}
    return None


def probe():
    """→ ⚑ `(有哪些家, 能做什么)`。⚑ 工具启动时调一次，⚑ 把结果直接摆给用户看。"""
    have = {n: get(n) for n in ('relay', 'wan', 'ark', 'glm', 'minimax', 'llm')}
    can = {}
    for cap, (need, opt, _desc) in CAPS.items():
        can[cap] = all(have.get(n) for n in need) and (not opt or any(have.get(n) for n in opt))
    return have, can


def report():
    have, can = probe()
    print('⚑ 凭据')
    for n in ('relay', 'wan', 'ark', 'glm', 'minimax', 'llm'):
        c = have.get(n)
        print(f'  {"✅" if c else "—"} {n:<9}{("来自 " + c["_from"]) if c else "未配置"}')
    print('\n⚑ 能做什么')
    for cap, (need, opt, desc) in CAPS.items():
        ok = can[cap]
        print(f'  {"✅" if ok else "⛔"} {desc}')
        if not ok:
            miss = [n for n in need if not have.get(n)] or opt
            print(f'      ⚠ 缺 {"/".join(miss)}')
    if not can['portrait']:
        print('\n⛔ 立绘出不了 —— ⚑ 中转站是唯一解（⚠ 纯文字写画风必滑成写实，⛔ 别家顶不上）')
    if not can['video']:
        print('\n⛔ 出片不可用 —— ⚑ 只有万相能钉首尾帧；⚠ 用别家出的循环动画首尾对不上 ＝ 废片')
    elif not can['draft_free']:
        print('\n⚠ 没配 GLM ⇒ ⚑ 抽姿势也得烧万相额度（⚑ GLM 那步是免费的）')


def _set(name):
    """⚑ 交互式填一家 —— ⚑ 一律写 `~/.gamegen/creds.json`（⛔ 不写仓库）。"""
    if name not in ALIASES:
        print(f'✗ 不认识 "{name}"，可选：{list(ALIASES)}')
        sys.exit(1)
    base = input(f'{name} baseUrl（⚑ 直接回车＝保持默认/留空）：').strip()
    key = input(f'{name} apiKey：').strip()
    if not key:
        print('✗ key 是空的，没写')
        return
    d = _read_json(USER_CFG)
    d.setdefault('providers', {})[name] = {'baseUrl': base, 'apiKey': key}
    if name in ('wan', 'dashscope'):
        ws = input('workspaceId（⚑ 万相要，没有就回车）：').strip()
        if ws:
            d['providers'][name]['workspaceId'] = ws
    if name == 'llm':
        m = input('model（⚑ 例如 glm-5.3-flash / deepseek-chat）：').strip()
        if m:
            d['providers'][name]['model'] = m
    os.makedirs(os.path.dirname(USER_CFG), exist_ok=True)
    with io.open(USER_CFG, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f'✅ 写入 {USER_CFG}')
    print('⚑ ⛔ 这个文件别提交、别打进 exe')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    arg = next((a for a in sys.argv[1:] if a.startswith('--set=')), None)
    if arg:
        _set(arg.split('=', 1)[1])
    else:
        report()
