# -*- coding: utf-8 -*-
"""⚑⚑⚑ **图生视频** —— ⚑ 整条链的第一棒：**一张图 → 一段视频**。

⚑ 链条：**这一份出视频** → `vid2anim.py`（抽帧/抠图/对齐/切图集）→ 接进游戏。

```bash
# ⚑ 免费抽卡（⚑ 探构图/姿势），⚑ 默认 provider=zhipu / cogvideox-flash
python tools/gen_video.py --img=liubai.png --tag=idle1 --promptfile=p_idle.txt

# ⚑⚑ 定稿走万相（⚑ 唯一能钉首尾帧的）—— ⚑ --last 是循环动画的地基
python tools/gen_video.py --img=liubai.png --last=liubai.png --tag=walk \
       --promptfile=p_walk.txt --provider=dashscope --res=480P --dur=2

python tools/gen_video.py --poll=<任务id>    # ⚑ 只取结果（⚠ 上一次跑断了用这个）
```

## ⚠⚠⚠ 三条**一眼看不出来**的

```
① ⚑⚑ 喂进去的图⛔ 不能带 alpha —— ⚠ 视频模型不认透明通道 ⇒ ⚑ 透明区出来是**黑块**，
   ⛔ 而且一行报错都没有。⇒ ⚑ 一律喂**不透明的图**（⚑ 角色用中性灰底的留白图，见 make_liubai.py）。

② ⚑⚑ prompt 必须**把镜头钉死** —— ⚑ 视频模型默认爱推镜头、爱让人物走出画面，
   ⚠ 镜头一飘 ＝ ⛔ 这一发废了（⚑ 人物大小和位置全变 ⇒ 脚底对齐直接作废）。
   ⚠⚠ 「镜头固定」⛔≠「人物固定」—— ⚑ 必须**分成两句**写（⚑ 合成一句会把人一起摁死）。

③ ⚑ 产物是 **5~6 秒 / 30fps ＝ 150~180 帧**，⚑ 而一个动作只要 3~6 帧
   ⇒ ⚑ 帧是**挑**出来的，⛔ 别指望模型给你一段刚好能用的。⚑ 挑帧规则见 vid2anim.py。
```

⚑ 凭据：`python tools/_creds.py` 看缺哪家；⚑ 三家分工和单价见《AI角色动画管线.md》§3 / §8。
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:                                                # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # ⚑ 幂等 ⇒ ⚑ 被 import 也安全
except Exception:
    pass

# ⚑ 数据根 ＝ **当前工作目录**（⛔ 不是脚本位置）—— ⚑ cd 到你的项目再跑，产物就落在那儿。
#   ⚑ 和 `artgen`（ai-asset-gen）同一条约定。⚑ 要改位置就设 ANIMPIPE_WORK。
ROOT = os.environ.get('ANIMPIPE_ROOT') or os.getcwd()
OUT = os.environ.get('ANIMPIPE_WORK') or os.path.join(ROOT, 'work', 'anim')

# ⚑⚑ **示例提示词** —— ⛔ 别直接拿去用，⚑ 它描述的是某个特定角色（红雾/厚涂/暗调）。
#   ⚑ 留在这儿是当**结构范本**：①镜头钉死 ②人物钉死 ③只放开该动的 ④画风锁死。
#   ⚑ 四段的写法和背后的判据见《AI角色动画管线.md》§2 —— ⚠ 那一节是这条链最贵的部分。
PROMPT_EXAMPLE = (
    '镜头完全固定不动，不推进不拉远不平移。人物保持原位，头部和肩膀不离开画面中心，'
    '构图、取景、人物大小完全不变。'
    '只有这些在轻微运动：呼吸时肩膀极缓慢起伏、几缕发丝和衣领被微风轻拂、缓慢眨眼一次、'
    '背景的红色雾气缓缓流动。'
    '严格保持原图的写实厚涂画风、暗调配色、暖色轮廓光和面部细节，不改变人物长相和服装。'
    '整体运动幅度极小，像一张会呼吸的立绘。'
)


# ⚑ 火山 seedance 候选（⚑ 新→旧）—— ⚑ 2026-09-01 从 /models 抄的非 Shutdown 视频模型。
#   ⚠ 这张表**会过期** ⇒ ⛔ 别当常量信，⚑ 全军覆没时重新列一次 /models。
CAND_ARK = ['doubao-seedance-2-5-260628', 'doubao-seedance-2-0-260128',
            'doubao-seedance-2-0-fast-260128', 'doubao-seedance-2-0-mini-260615',
            'doubao-seedance-1-5-pro-251215', 'doubao-seedance-1-0-pro-fast-251015',
            'doubao-seedance-1-0-pro-250528', 'doubao-seedance-1-0-lite-i2v-250428']


def cfg(name: str) -> dict:
    """⚑ 凭据统一走 `tools/_creds.py`（⚑ 环境变量 → 用户目录 → 项目 config.json/.env）。

    ⚑ 返回**原来那种形状**（⚑ `baseUrl`/`apiKey`/`workspaceId`）⇒ ⛔ 调用处一行都不用改。
    ⚠ `_creds` 读不到就退回**直接读 config.json** —— ⚑ 这份脚本可能被拷到别处单独跑。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _creds import get as _get
        c = _get(name)
        if c:
            return {'baseUrl': c['base'], 'apiKey': c['key'], 'workspaceId': c.get('workspace', '')}
    except Exception:
        pass
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        return json.load(f)['providers'][name]


def post(url: str, body: dict, key: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # ⚠⚠ 默认的 HTTPError 只印「HTTP Error 404: Not Found」，⛔ **正文被吞掉** ——
        #   ⚑ 而三家 API 把「到底哪儿错了」全写在正文里（⚑ 火山那条 404 的正文是
        #     「model doubao-seedance-1-0-lite-i2v-250428 does not exist」⇒ 一眼定位）。
        #   ⚑ 2026-09-01 为此手写了一遍探针脚本，⛔ 不该再有第二次。
        print(f'  ✗ HTTP {e.code} {url}\n    {e.read().decode(errors="replace")[:600]}')
        raise


def get(url: str, key: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def opt(flag: str, dflt=None, cast=str):
    """⚑ 取 `--key=value` 形式的命令行参数（⚑ 和 vid2anim.py 同一套写法）"""
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return cast(a.split('=', 1)[1])
    return dflt


def read_prompt(path: str) -> str:
    """⚑ 从 `_给千问/提示词_*.txt` 里取**正文**。

    ⚠⚠ 为什么要有这个：⚑ 提示词有一千多字中文，⛔ 直接当命令行参数传，
      ⚑ Windows 的 bash 下引号和 GBK 编码都会出事（⚠ 而且是**静默**截断/乱码，
      ⛔ 出片之后才发现 prompt 根本不是你写的那条）。
    ⚑ 取法沿用走路那条已有的分隔符约定：⚑ `正文 ↓` 和 `正文 ↑` 之间的部分。
      ⚑ 两个标记都没有就整篇当正文（⚑ 允许存纯文本 prompt）。
    """
    with open(path, encoding='utf-8') as f:
        t = f.read()
    if '正文 ↓' in t and '正文 ↑' in t:
        t = t.split('正文 ↓', 1)[1].rsplit('正文 ↑', 1)[0]
        t = '\n'.join(ln for ln in t.splitlines() if not ln.startswith('─'))
    return t.strip()


def to_url(path: str) -> str:
    """⚑ `input.media[].url` 要的是 URL。⚑ 本地文件先试 **base64 data URL**。

    ⚠ 官方文档的例子全是 http(s) 链接，⛔ **没写支不支持 data URL** ⇒ ⚑ 只能实测。
      ⚑ 万一不支持，退路是把图传到公网可访问的地方（OSS / 任意图床）再传链接。
    """
    if path.startswith(('http://', 'https://', 'data:')):
        return path
    ext = os.path.splitext(path)[1].lower().lstrip('.') or 'png'
    with open(path, 'rb') as f:
        return f'data:image/{"jpeg" if ext in ("jpg", "jpeg") else ext};base64,' + base64.b64encode(f.read()).decode()


def log_task(prov: str, model: str, tag: str, task_id: str) -> None:
    """⚑⚑ 任务 id **立刻落盘** —— ⛔ 别只印在 stdout 上。

    ⚠⚠⚠ 2026-09-01 丢过一次（⚑ 万相 480P，约 1 元）：⚑ 命令是
      `python tools/gen_video.py ... 2>&1 | tail -6`，⚑ 中途把进程杀了 ——
      ⚠ Python 的 stdout 一旦重定向就是**块缓冲**，⇒ ⛔ 那几行还在缓冲区里没落地，
      ⚑ 进程一杀全没了。⚠ 而这个域名**没有列任务的接口**（`/tasks` 恒 404）
      ⇒ ⛔ id 一丢就再也取不回来，⚑ 钱照扣。
    ⇒ ✅ 提交成功立刻 append 到 `work/anim/_tasks.log`，⚑ 每行一条，⚑ 写完就 flush。
    ⚑ 取片：`python tools/gen_video.py --provider=<prov> --poll=<id> --tag=<tag>`
    """
    os.makedirs(OUT, exist_ok=True)
    line = '\t'.join([time.strftime('%Y-%m-%d %H:%M:%S'), prov, model, tag, task_id]) + '\n'
    with open(os.path.join(OUT, '_tasks.log'), 'a', encoding='utf-8') as f:
        f.write(line)
        f.flush()
    print(f'  ⚑ id 已落盘 work/anim/_tasks.log ⇒ 断了用 --poll={task_id} 取')


def submit_dashscope(img: str, prompt: str, model: str) -> str:
    """⚑ 阿里云百炼（DashScope）图生视频 —— ⚑ 网页版会打**移动水印**，⚑ API 版不打。

    ⚠⚠⚠ **必须用「独立业务空间」的专属域名**（2026-08-31 连着栽了两次）——
      ⛔ 打公共的 `dashscope.aliyuncs.com` 恒 401 `InvalidApiKey`，
      ⚠ 而且报的是「key 格式不对」，⚑ 极容易误判成 key 本身有问题。
      ⚑ 正确的域名在控制台导出的 apiKey CSV 里，字段名 `dashScope`：
        `https://ws-<workspaceId>.cn-beijing.maas.aliyuncs.com/api/v1`
    ⚑ 和智谱的差别：⚑ 异步靠**请求头** `X-DashScope-Async: enable`，
      ⚑ 任务 id 在 `output.task_id`（⛔ 不是顶层 `id`）。
    """
    c = cfg('dashscope')
    # ⚠⚠ 图生视频走 `input.media` 数组，⛔ 不是 `input.img_url`（⚑ 按 wan3.0 官方文档）。
    #   ⚑ `first_frame` = 严格作为视频第一帧；⚑ 可再配一张 `last_frame` 做**首尾帧**。
    #   ⚑⚑ 首尾帧对**循环动画**是杀手锏：⚑ 首尾都钉死同一张 ⇒ 模型必须生成闭环动作，
    #     ⇒ ⚑ 循环缝天然趋近 0，⛔ 不用再靠自相关猜步频周期。
    media = []
    if img:
        media.append({'type': 'first_frame', 'url': to_url(img)})
    last = opt('--last')
    if last:
        media.append({'type': 'last_frame', 'url': to_url(last)})
    body = {'model': model,
            'input': {'prompt': prompt},
            'parameters': {'resolution': opt('--res', '720P'), 'ratio': 'adaptive',
                           # ⚑ 最长 30s（⚠ 智谱只有 5s）⇒ 可挑的帧多 6 倍
                           'duration': opt('--dur', 5, int),
                           'audio': False}}   # ⚑ 默认出**有声**视频，⛔ 我们只要画面，关掉
    if media:
        body['input']['media'] = media
    req = urllib.request.Request(
        c['baseUrl'].rstrip('/') + '/services/aigc/video-generation/video-synthesis',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + c['apiKey'],
                 'X-DashScope-Async': 'enable'})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    out = d.get('output', {})
    print(f"  ⚑ 提交 {model} → {out.get('task_status')}  id={out.get('task_id')}")
    log_task('dashscope', model, opt('--tag', 'video'), out['task_id'])
    return out['task_id']


def poll_dashscope(task: str, tag: str, every: int = 10, cap: int = 60):
    c = cfg('dashscope')
    url = c['baseUrl'].rstrip('/') + '/tasks/' + task
    for i in range(cap):
        r = get(url, c['apiKey'])
        out = r.get('output', {})
        st = out.get('task_status')
        if st == 'SUCCEEDED':
            u = out.get('video_url') or out.get('results', [{}])[0].get('url')
            os.makedirs(OUT, exist_ok=True)
            dst = os.path.join(OUT, f'{tag}.mp4')
            urllib.request.urlretrieve(u, dst)
            print(f'  ✅ {tag}.mp4  {os.path.getsize(dst)//1024} KB  ← {u[:60]}…')
            return dst
        if st in ('FAILED', 'CANCELED', 'UNKNOWN'):
            print(f'  ✗ {tag} {st}：{json.dumps(r, ensure_ascii=False)[:300]}')
            return None
        print(f'  … {tag} {st}（第 {i + 1} 次，等了 {i * every}s）')
        time.sleep(every)
    print(f'  ⚠ {tag} 等超了 —— ⚑ 用 --provider=dashscope --poll={task} 接着取')
    return None


def submit_ark(img: str, prompt: str, model: str) -> str:
    """⚑ 火山方舟 seedance 图生视频 —— ⚑ 拿它是为了**一个参数**：`--camerafixed`。

    ⚠⚠⚠ **参数不走 JSON 字段，⛔ 而是拼在 prompt 文本尾巴上**（2026-09-01 写这份时的官方约定）——
      ⚑ 形如 `<中文提示词> --resolution 720p --duration 5 --camerafixed true`。
      ⛔ 别照智谱/万相那样往 `parameters` 里塞，⚠ 塞了不报错，⚑ 只是**静默不生效**。
    ⚑⚑ 为什么非要 camerafixed：⚑ 2026-09-01 GLM 抽普攻三发，⚠ **三发全部镜头推近**
      （⛔ 第 3 段推成大特写，只剩一张脸），⚑ 而提示词第一句就是「镜头完全固定」。
      ⇒ ⚑ 提示词摁不住的事，⚑ 用**厂商自己的开关**摁。
    ⚑ 和另外两家的差别：⚑ 请求体是 `content` **数组**（⛔ 不是 input/prompt），
      ⚑ 任务 id 在顶层 `id`，⚑ 轮询路径和提交路径**同一条**（⛔ 不是 /async-result 或 /tasks）。
    """
    c = cfg('doubaoVideo')
    # ⚑ watermark 默认 true ⇒ ⚠ 右下角水印会落在角色脚上（⚑ 同 zhipu 那条注释的坑）⇒ 一律关掉
    tail = (f" --resolution {opt('--res', '720p').lower()} --ratio adaptive"
            f" --duration {opt('--dur', 5, int)} --watermark false"
            f" --camerafixed {opt('--camerafixed', 'true')}")
    content = [{'type': 'text', 'text': prompt + tail}]
    # ⚑⚑ 首尾帧：⚑ 火山靠 content 里每个 image_url 自带的 `role` 区分，
    #   ⛔ 不是万相那种 `media[].type`（⚠ 三家三个写法，⛔ 别互相抄）。
    #   ⚑⚑ 为什么非要它：⚑ 2026-09-01 实测，⚠ 提示词里「回到和开头完全相同的站姿」
    #     ⚑ 写了三遍，⛔ seedance 一次都没听（⚑ 回斩/撩劈两发收招全停在别的姿势）。
    #   ⇒ ⚑ 连招衔接靠的就是**每段尾帧都是同一张** ⇒ ⛔ 这件事不能靠嘴说，得钉死。
    last = opt('--last')
    if img:
        e = {'type': 'image_url', 'image_url': {'url': to_url(img)}}
        if last:
            e['role'] = 'first_frame'
        content.append(e)
    if last:
        content.append({'type': 'image_url', 'image_url': {'url': to_url(last)},
                        'role': 'last_frame'})

    # ⚑⚑ `--model=auto`：⚑ 按新→旧挨个试，⚑ 撞到**账号开通了的**那个就用它。
    #   ⚠⚠ 火山这里有**两种**不同的 404，⛔ 长得几乎一样但含义完全不同：
    #     `InvalidEndpointOrModel.NotFound` ＝ ⚑ model id 本身不存在（⚠ id 会漂，⚑ 查 /models）
    #     `ModelNotOpen`                    ＝ ⚑ id 对的，⛔ **账号没开通** ⇒ ⚑ 换一个试
    #   ⚑ 两种都只在**响应正文**里，⛔ 状态码都是 404（⚑ 所以 post() 必须印正文）。
    #   ⚑⚑ 试探用的是**真提示词** ⇒ ⚑ 一旦提交成功那就是一发真片，⛔ 不会白烧额度。
    cand = CAND_ARK if model == 'auto' else [model]
    for i, m in enumerate(cand):
        try:
            r = post(c['baseUrl'].rstrip('/') + '/contents/generations/tasks',
                     {'model': m, 'content': content}, c['apiKey'], timeout=180)
        except urllib.error.HTTPError as e:
            if e.code == 404 and i + 1 < len(cand):
                print(f'  ⚑ {m} 不可用，换 {cand[i + 1]} 再试')
                continue
            raise
        print(f"  ⚑ 提交 {m} → {r.get('status')}  id={r.get('id')}   开关：{tail.strip()}")
        log_task('ark', m, opt('--tag', 'video'), r['id'])
        return r['id']
    raise SystemExit('✗ 候选 model 全都不可用 —— ⚑ 去方舟控制台开通，或重新查 /models')


def poll_ark(task: str, tag: str, every: int = 10, cap: int = 60):
    c = cfg('doubaoVideo')
    url = c['baseUrl'].rstrip('/') + '/contents/generations/tasks/' + task
    for i in range(cap):
        r = get(url, c['apiKey'])
        st = r.get('status')
        if st == 'succeeded':
            u = r.get('content', {}).get('video_url')
            os.makedirs(OUT, exist_ok=True)
            dst = os.path.join(OUT, f'{tag}.mp4')
            urllib.request.urlretrieve(u, dst)
            print(f'  ✅ {tag}.mp4  {os.path.getsize(dst)//1024} KB  ← {u[:60]}…')
            return dst
        if st in ('failed', 'canceled'):
            print(f'  ✗ {tag} {st}：{json.dumps(r, ensure_ascii=False)[:300]}')
            return None
        print(f'  … {tag} {st}（第 {i + 1} 次，等了 {i * every}s）')
        time.sleep(every)
    print(f'  ⚠ {tag} 等超了 —— ⚑ 用 --provider=ark --poll={task} 接着取')
    return None


# ⚑ MiniMax 的 resolution 是**枚举**，⛔ 不是随便写的字符串 —— ⚠ 两个模型的合法值还不一样，
#   ⚠⚠ 而且**两个都没有 720P** ⇒ ⛔ 而 720P 正是这个脚本别处的默认值（见 submit_dashscope）
#   ⇒ ⚑ 一律过 res_minimax() 映射，⛔ 别把 --res 原样透传。
RES_MINIMAX = {'MiniMax-H3': ('768P', '2K'), 'MiniMax-H3-Max': ('480P', '768P')}


def res_minimax(model: str, raw: str) -> str:
    """⚑ 把 `--res=720p` 这种写法映射成 MiniMax 认的枚举值。⚠ 映射不到就退回该模型的**低档**。"""
    ok = RES_MINIMAX.get(model, ('768P', '2K'))
    v = {'480p': '480P', '720p': '768P', '768p': '768P', '1080p': '2K', '2k': '2K'}.get(raw.lower(), raw.upper())
    if v not in ok:
        print(f'  ⚠ {model} 不支持 {raw} —— ⚑ 退回 {ok[0]}（⚑ 合法值：{"/".join(ok)}）')
        return ok[0]
    return v


def submit_minimax(img: str, prompt: str, model: str) -> str:
    """⚑ MiniMax H3 图生视频 —— ⚑ 拿它是因为 **seedance 在「角色自转」上已经失败过**
    （⚑ 见 fx_whirl_demo.py 头部：⚠ cogvideox-flash 和 Seedance 2.5 两发角色都不转）。

    ⚑⚑ 请求体和火山**长得几乎一样**（⚑ 都是 content 数组 ＋ type ＋ role）
      ⇒ ⚠⚠ 正因为像，⛔ **四处不同的地方特别容易照抄错**：
    ```
    ⚑ 端点      /v2/video_generation            ⛔ 火山是 /contents/generations/tasks
    ⚑ 任务 id   顶层 `task_id`                   ⛔ 火山是顶层 `id`
    ⚑ 轮询      /v2/query/video_generation/{id}  ⛔ 火山是**提交同一条路径**
    ⚑ 结果      `task.content.url`（⚠ 外面套了一层 task）  ⛔ 火山是顶层 content.video_url
    ```
    ⚠⚠⚠ **⛔ 没有 camerafixed，也没有 watermark 开关** —— ⚑ 官方文档明写镜头只能靠提示词描述。
      ⛔ 火山那套「把 `--resolution 720p --camerafixed true` 拼在 prompt 尾巴上」在这儿是**灾难**：
      ⚑ MiniMax 的开关走 JSON 字段，⚠ 拼进文本会被**当成提示词内容读进去** ⇒ ⛔ 一个字都别拼。
    ⚠⚠ 水印：⛔ 文档里**没有**关水印的参数 ⇒ ⚑ 第一发出来**必须先看右下角**
      （⚑ 智谱那条踩过：水印落在角色脚上，⚠ vid2anim 裁水印会把整双靴子切掉）。
    ⚑ 图生视频时 `ratio` 恒为 adaptive 由输入图决定 ⇒ ⛔ 别传（⚑ 只有文生视频才必填）。
    ⚑ 出的是**有声**视频（⚠ 智谱/万相都能 with_audio=False，⛔ 这家不能）⇒ ⚑ 抽帧不受影响，只是文件大一圈。
    """
    c = cfg('minimax')
    content = [{'type': 'text', 'text': prompt}]
    # ⚑ 首尾帧的 role 名和火山**完全一致**（first_frame / last_frame）⇒ ⚑ 这段可以照抄
    last = opt('--last')
    if img:
        content.append({'type': 'image_url', 'image_url': {'url': to_url(img)}, 'role': 'first_frame'})
    if last:
        content.append({'type': 'image_url', 'image_url': {'url': to_url(last)}, 'role': 'last_frame'})
    body = {'model': model, 'content': content,
            # ⚑ H3 4～15 秒 / H3-Max 5～15 秒，⚑ 只收整数
            #   ⚠ ⛔ 仍然别给 10 秒：⚑ 撩劈那条实测长时长会让模型**重复动作**（见 提示词_普攻3）
            'duration': opt('--dur', 5, int),
            'resolution': res_minimax(model, opt('--res', '768P'))}
    r = post(c['baseUrl'].rstrip('/') + '/v2/video_generation', body, c['apiKey'], timeout=180)
    print(f"  ⚑ 提交 {model} → task_id={r.get('task_id')}  {body['resolution']} / {body['duration']}s")
    log_task('minimax', model, opt('--tag', 'video'), r['task_id'])
    return r['task_id']


def poll_minimax(task: str, tag: str, every: int = 10, cap: int = 60):
    c = cfg('minimax')
    url = c['baseUrl'].rstrip('/') + '/v2/query/video_generation/' + task
    for i in range(cap):
        r = get(url, c['apiKey'])
        t = r.get('task', {})           # ⚠ ⛔ 别忘了这一层（见 submit_minimax docstring）
        st = t.get('status')
        if st == 'succeeded':
            u = t.get('content', {}).get('url')
            os.makedirs(OUT, exist_ok=True)
            dst = os.path.join(OUT, f'{tag}.mp4')
            urllib.request.urlretrieve(u, dst)
            print(f'  ✅ {tag}.mp4  {os.path.getsize(dst)//1024} KB  ← {u[:60]}…')
            return dst
        if st in ('failed', 'cancelled'):
            print(f'  ✗ {tag} {st}：{json.dumps(r, ensure_ascii=False)[:300]}')
            return None
        print(f'  … {tag} {st}（第 {i + 1} 次，等了 {i * every}s）')
        time.sleep(every)
    print(f'  ⚠ {tag} 等超了 —— ⚑ 用 --provider=minimax --poll={task} 接着取')
    return None


def submit(img: str, prompt: str, model: str) -> str:
    """⚑ 提交一个图生视频任务，⚑ 返回任务 id"""
    c = cfg('zhipu')
    with open(img, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    # ⚠⚠ `watermark_enabled: False` ⛔ 不是可选项 —— ⚑ 2026-08-31 在 3/4 侧立绘上量出来的：
    #    ⚑ 水印在右下角 (x>0.78, y>0.91)，⚠ 而这一版角色的**脚底伸到 y=0.956**
    #    ⇒ ⚠ `vid2anim` 的 `WM_CUT=0.895`「裁底部一条」**从脚踝横切过去，整双靴子没了**。
    #    ⚠⚠ 而且 vid2anim 的自检只查「水印区还有没有残留」，⛔ 不查脚有没有被切掉
    #      ⇒ ⚑ 这个错误会**静默通过**，出来一张没有脚的图集。
    #    ⇒ ⚑ 从源头不打水印，⛔ 别在下游修。
    body = {'model': model, 'prompt': prompt, 'image_url': b64, 'with_audio': False,
            'watermark_enabled': False}
    r = post(c['baseUrl'].rstrip('/') + '/videos/generations', body, c['apiKey'])
    print(f"  ⚑ 提交 {model} → {r.get('task_status')}  id={r.get('id')}")
    log_task('zhipu', model, opt('--tag', 'video'), r['id'])
    return r['id']


def poll(task: str, tag: str, every: int = 10, cap: int = 40) -> str | None:
    """⚑ 轮询直到出片，⚑ 落盘返回路径。⚠ `cap` 是**次数**上限（⛔ 别无限等）"""
    c = cfg('zhipu')
    url = c['baseUrl'].rstrip('/') + '/async-result/' + task
    for i in range(cap):
        r = get(url, c['apiKey'])
        st = r.get('task_status')
        if st == 'SUCCESS':
            u = r['video_result'][0]['url']
            os.makedirs(OUT, exist_ok=True)
            dst = os.path.join(OUT, f'{tag}.mp4')
            urllib.request.urlretrieve(u, dst)
            kb = os.path.getsize(dst) // 1024
            print(f'  ✅ {tag}.mp4  {kb} KB  ← {u[:60]}…')
            return dst
        if st == 'FAIL':
            print(f'  ✗ {tag} 失败：{json.dumps(r, ensure_ascii=False)[:200]}')
            return None
        print(f'  … {tag} {st}（第 {i + 1} 次，等了 {i * every}s）')
        time.sleep(every)
    print(f'  ⚠ {tag} 等超了（⚑ 任务还在跑，⚑ 用 --poll={task} 接着取）')
    return None


def main() -> None:
    args = sys.argv[1:]
    kv = {a.split('=')[0].lstrip('-'): a.split('=', 1)[1] for a in args if '=' in a}
    pos = [a for a in args if not a.startswith('--')]

    # ⚑ provider 决定端点/请求体/轮询格式三样东西，⛔ 别只换 model 名
    prov = kv.get('provider', 'zhipu')
    do_submit, do_poll = {'zhipu': (submit, poll),
                          'dashscope': (submit_dashscope, poll_dashscope),
                          'ark': (submit_ark, poll_ark),
                          'minimax': (submit_minimax, poll_minimax)}[prov]

    if 'poll' in kv:
        do_poll(kv['poll'], kv.get('tag', 'video'))
        return

    model = kv.get('model', {'dashscope': 'wan3.0-video',
                             # ⚠ model id 会漂 —— ⚑ 2026-09-01 查 /models：seedance 1.0-lite 已 Retiring，
                             #   ⛔ 直接用会 404 `InvalidEndpointOrModel.NotFound`。⚑ 报 404 就先列 /models。
                             #   ⚑ 2026-09-01 实测：⚠ 这账号 seedance 2.x 全部 `ModelNotOpen`（⛔ 没开通），
                             #     ⚑ **只有 1-0-pro-fast 能用** ⇒ 直接把它设成默认，⛔ 别每次白试 6 个。
                             #     ⚑ 换账号/开通了新模型就改这里，⚑ 或者传 --model=auto 让它自己找。
                             #   ⚠⚠ ⛔ pro-**fast** 不支持首尾帧（`task_type flf2v does not support`）
                             #     ⇒ ⚑ 默认给 pro-250528：⚑ 它 i2v / flf2v 都能跑，⛔ 省得两个默认值。
                             'ark': 'doubao-seedance-1-0-pro-250528',
                             # ⚑ 默认给满血 H3：⚑ 首发要回答的是「角色到底转不转」，⚑ 用最强的答。
                             #   ⚑ 抽卡想快就 --model=MiniMax-H3-Max（⚑ fal 联合出的高速版，⚑ 有 480P 档），
                             #   ⛔ 但 H3-Max 只有 T2V/I2V，⚠ 全能参考（r2va）还没上。
                             'minimax': 'MiniMax-H3'}.get(prov, 'cogvideox-flash'))
    if 'img' not in kv:
        print('✗ 缺 --img=<源图>\n'
              '  用法：python tools/gen_video.py --img=<图> --tag=<名> --promptfile=<txt> [--provider=dashscope]\n'
              '  ⚑ 源图该长什么样（留白图 · 唯一源图原则）见《AI角色动画管线.md》§1')
        sys.exit(1)
    img = kv['img'] if os.path.isabs(kv['img']) else os.path.join(ROOT, kv['img'])
    # ⚑ --promptfile 优先于 --prompt（⚑ 长中文 prompt 一律走文件，见 read_prompt）
    if 'promptfile' in kv:
        prompt = read_prompt(kv['promptfile'])
    elif 'prompt' in kv:
        prompt = kv['prompt']
    else:
        # ⚠⚠ ⛔ 别拿 PROMPT_EXAMPLE 兜底 —— ⚑ 那是**别人角色**的描述（红雾/厚涂），
        #   ⚠ 悄悄用它出片 ＝ 出一段废片，⛔ 而且**钱照扣**。⇒ ⚑ 宁可在花钱前停下。
        print('✗ 没给提示词：--promptfile=<txt>（长中文推荐）或 --prompt="…"\n'
              '  ⚑ 结构范本见本文件 PROMPT_EXAMPLE，⚑ 三铁律五推论见《AI角色动画管线.md》§2')
        sys.exit(1)
    tag = kv.get('tag', 'video')

    if not os.path.exists(img):
        print(f'✗ 图不在：{img}')
        sys.exit(1)
    print(f'⚑ 源图 {os.path.relpath(img, ROOT)}  ({os.path.getsize(img) // 1024} KB)  provider={prov}  model={model}')
    do_poll(do_submit(img, prompt, model), tag)


if __name__ == '__main__':
    main()
