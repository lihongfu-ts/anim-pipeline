# -*- coding: utf-8 -*-
"""⚑⚑⚑ **anim-pipeline 网页端** —— ⚑ 一句话生成角色 + 移动 + 攻击，⚑ 预览、提取、记账、配 key、查模型。

```bash
pip install -r web/requirements.txt
python web/server.py            # → http://127.0.0.1:8765
```

⚑ 只绑本机（127.0.0.1）—— ⚑ 凭据会经过它写到 ~/.gamegen/creds.json，⛔ 别暴露到公网。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

for _s in (sys.stdout, sys.stderr):                 # Windows 控制台 / 重定向默认 GBK
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'tools'))
import pipeline  # noqa: E402
import agent  # noqa: E402
import _creds  # noqa: E402


def _ffmpeg_path():
    """⚑ 让本进程（及它起的子进程）找得到 ffmpeg：⚑ tools/bin（一键安装放这）＋ winget 的 Gyan.FFmpeg 目录 ⇒ 塞进 PATH 前面。
    ⚑ 装完不用重启服务、不用改系统 PATH。⚑ 返回 which 结果。"""
    import glob
    cands = [HERE.parent / 'tools' / 'bin']
    la = os.environ.get('LOCALAPPDATA')
    if la:
        cands += [Path(p) for p in glob.glob(os.path.join(la, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', 'ffmpeg-*', 'bin'))]
    for d in cands:
        if (d / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')).exists() and str(d) not in os.environ.get('PATH', ''):
            os.environ['PATH'] = str(d) + os.pathsep + os.environ.get('PATH', '')
    return shutil.which('ffmpeg')


_ffmpeg_path()

app = FastAPI(title='anim-pipeline')
app.mount('/static', StaticFiles(directory=str(HERE / 'static')), name='static')
UPLOADS = pipeline.JOBS / '_uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
# ⚠ sweep_stale（把 running 的任务标 failed）⛔ 不能放在 import 时跑：⚑ 测试/别的进程一 import 就会把**正在跑的服务**的任务误标
#   （2026-09-09 真发生过，靠任务进程下一次 save 覆盖回来）⇒ ⚑ 挪到 __main__、拿到端口之后

# ⚑ 重启（⚑ 用户 2026-09-09：网页 HTML 从磁盘读、后端是旧进程 ⇒ 新前端配旧后端直接炸；⚑ 要能在网页上点一下重启）
#   ⚑ stale：⚑ 这几份代码的 mtime 比进程启动时新 ⇒ 网页上亮「重启服务」按钮
_STARTED = time.time()
_CODE_FILES = [HERE / 'server.py', HERE / 'pipeline.py', HERE / 'agent.py', HERE.parent / 'tools' / '_creds.py']
_CODE_MTIME = max(f.stat().st_mtime for f in _CODE_FILES if f.exists())
SERVER_LOG = pipeline.JOBS / '_server.log'


def _stale() -> bool:
    return any(f.exists() and f.stat().st_mtime > _CODE_MTIME + 0.5 for f in _CODE_FILES)


PROVIDERS = ['relay', 'wan', 'ark', 'glm', 'minimax', 'llm']
DEFAULT_BASE = {
    'relay': '', 'wan': 'https://dashscope.aliyuncs.com/api/v1', 'ark': 'https://ark.cn-beijing.volces.com/api/v3',
    'glm': 'https://open.bigmodel.cn/api/paas/v4', 'minimax': 'https://api.minimaxi.com',
    'llm': 'https://open.bigmodel.cn/api/paas/v4',
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) anim-pipeline/1.0'


@app.get('/', response_class=HTMLResponse)
def index():
    return (HERE / 'static' / 'index.html').read_text(encoding='utf-8')


# ─────────────────────────────── 体检 / 凭据
def _mask(k):
    return (k[:4] + '…' + k[-4:]) if k and len(k) > 10 else ('已配置' if k else '')


@app.get('/api/doctor')
def doctor():
    have, can = _creds.probe()
    cr = {n: _creds.get(n) for n in PROVIDERS}
    return {
        'python': sys.version.split()[0],
        'ffmpeg': bool(_ffmpeg_path()) and bool(shutil.which('ffprobe')), 'ffprobe': bool(shutil.which('ffprobe')),
        'ffmpeg_path': shutil.which('ffmpeg'),
        'numpy': _has('numpy'), 'pillow': _has('PIL'),
        'creds': {n: ({'from': c['_from'], 'base': c['base'], 'key': _mask(c['key']), 'model': c.get('model', ''),
                       'vision_model': c.get('vision_model', '')}
                      if c else None) for n, c in cr.items()},
        'can': can,
        'caps': {k: v[2] for k, v in _creds.CAPS.items()},
        'actions': pipeline.ACTIONS,
        'ui_actions': pipeline.UI_ACTIONS,
        'views': pipeline.VIEWS,
        'portrait_tmpl': pipeline.PORTRAIT_TMPL, 'weapon_pose': pipeline.WEAPON_POSE, 'default_style': pipeline.DEFAULT_STYLE,   # ⚑ 网页拼立绘完整提示词
        'portrait_edit_suffix': pipeline.PORTRAIT_EDIT_SUFFIX,
        'attack_chain': pipeline.ATTACK_CHAIN,
        'prompt_head': pipeline.PROMPT_HEAD, 'prompt_tail': pipeline.PROMPT_TAIL,   # ⚑ 网页拼「完整提示词」预览用
        'presets': pipeline.PRESETS,
        'custom_defaults': pipeline.CUSTOM_DEFAULTS,
        'providers': pipeline.VIDEO_PROVIDERS,
        'prices': {'portrait': pipeline.portrait_price(),
                   'video': {f'{p}|{r}|{d}': v for (p, r, d), v in pipeline.PRICE_VIDEO.items()},
                   'per_sec': {f'{p}|{r}': v for (p, r), v in pipeline.PER_SEC.items()},
                   'zhipu': pipeline.ZHIPU_PRICE},
        'llm_default': pipeline.LLM_DEFAULT_MODEL,
        'creds_file': str(_creds.USER_CFG),
        'started': _STARTED, 'stale': _stale(), 'server_log': str(SERVER_LOG),
    }


@app.post('/api/restart')
def restart():
    """⚑ 网页上点「重启服务」：⚑ 先拉起一个等端口的新进程，⚑ 自己再退出。⛔ 有任务在跑就拒绝（⚑ 杀了等于白花钱）。"""
    busy = [j['id'] for j in pipeline.list_jobs() if j.get('status') in ('running', 'queued', 'waiting')]
    if busy:
        raise HTTPException(409, f'有任务在跑（{", ".join(busy)}），跑完再重启')

    def go():
        time.sleep(0.3)                                   # ⚑ 让这次响应先发出去
        log = open(SERVER_LOG, 'a', encoding='utf-8')
        log.write(f'\n───── {time.strftime("%Y-%m-%d %H:%M:%S")} 网页重启：{sys.executable} {" ".join(sys.argv)}  cwd={os.getcwd()}\n')
        log.flush()
        kw = dict(cwd=os.getcwd(), env={**os.environ, 'ANIMPIPE_WAIT_PORT': '1'},
                  stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, close_fds=True)
        if os.name == 'nt':                               # ⚑ 脱离当前控制台，⚑ 旧进程退了它还活着
            kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kw['start_new_session'] = True
        subprocess.Popen([sys.executable] + sys.argv, **kw)
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()
    return {'ok': True, 'msg': '重启中', 'log': str(SERVER_LOG)}


def _has(m):
    try:
        __import__(m)
        return True
    except ImportError:
        return False


@app.post('/api/creds')
def set_cred(body: dict):
    name = body.get('provider')
    if name not in PROVIDERS:
        raise HTTPException(400, f'provider 只能是 {PROVIDERS}')
    key = (body.get('apiKey') or '').strip()
    if not key:
        raise HTTPException(400, 'apiKey 是空的')
    d = _creds._read_json(_creds.USER_CFG)
    ent = {'baseUrl': (body.get('baseUrl') or DEFAULT_BASE[name]).strip().rstrip('/'), 'apiKey': key}
    if body.get('workspaceId'):
        ent['workspaceId'] = body['workspaceId'].strip()
    if body.get('model'):
        ent['model'] = body['model'].strip()
    if body.get('vision_model'):
        ent['vision_model'] = body['vision_model'].strip()
    if body.get('price') not in (None, ''):                 # ⚑ 买了包按包价记账（如智谱 ¥10/100 次 ⇒ 0.10）
        try:
            ent['price'] = float(body['price'])
        except (TypeError, ValueError):
            raise HTTPException(400, 'price 要是数字（每次多少元）')
    d.setdefault('providers', {})[name] = ent
    _creds.USER_CFG.parent.mkdir(parents=True, exist_ok=True) if hasattr(_creds.USER_CFG, 'parent') else \
        os.makedirs(os.path.dirname(_creds.USER_CFG), exist_ok=True)
    with open(_creds.USER_CFG, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return {'ok': True, 'file': str(_creds.USER_CFG)}


@app.delete('/api/creds/{name}')
def del_cred(name: str):
    d = _creds._read_json(_creds.USER_CFG)
    d.get('providers', {}).pop(name, None)
    with open(_creds.USER_CFG, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return {'ok': True}


def _http(method, url, key, body=None, timeout=20):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': 'Bearer ' + key, 'User-Agent': UA,
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode(errors='replace')
            try:
                return r.status, json.loads(txt)
            except Exception:
                return r.status, txt[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace')[:400]      # ⚑ 正文才有真正的原因
    except Exception as e:
        return 0, str(e)


@app.post('/api/creds/{name}/probe')
def probe_cred(name: str, body: dict = None):
    """⚑ 认证通不通 ＋ 能列模型的列出来。⚑ 结论按各家实际接口形态给，⛔ 不假装都有 /models。"""
    c = _creds.get(name)
    if not c:
        return {'ok': False, 'msg': '未配置'}
    base, key = c['base'], c['key']
    notes, models = [], []
    if name == 'llm':
        # ⚑ 任意 OpenAI 兼容端点：⚑ 先试 /models 列表，⚑ 再用 1 token 探配置的那个模型能不能聊
        model = (body or {}).get('model') or c.get('model') or pipeline.LLM_DEFAULT_MODEL
        code, resp = _http('GET', f'{base}/models', key)
        if code == 200 and isinstance(resp, dict):
            models = [m.get('id') for m in resp.get('data', []) if isinstance(m, dict)]
        code, resp = _http('POST', f'{base}/chat/completions', key,
                           {'model': model, 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1})
        if code != 200:
            return {'ok': False, 'msg': f'HTTP {code}（模型 {model}）', 'detail': resp, 'models': models[:60], 'notes': notes}
        # ⚑ 再探**看不看得懂图**：⚑ 发一张 1×1 PNG，200 ＝ 这个模型能进 VLM 挑帧那一步
        tiny = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
        vmodel = (body or {}).get('vision_model') or c.get('vision_model') or model
        vcode, vresp = _http('POST', f'{base}/chat/completions', key,
                             {'model': vmodel, 'max_tokens': 5, 'messages': [{'role': 'user', 'content': [
                                 {'type': 'text', 'text': '这张图是什么颜色？'},
                                 {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + tiny}}]}]})
        return {'ok': True, 'msg': f'认证通过，模型 {model} 可用' + (f'（端点共 {len(models)} 个模型）' if models else ''),
                'vision': vcode == 200, 'vision_model': vmodel, 'vision_detail': None if vcode == 200 else vresp,
                'models': models[:60], 'notes': notes}
    if name == 'wan':
        # ⚑ 实测（2026-09-09）：`sk-ws-` 开头的业务空间 key 在公共域名 dashscope.aliyuncs.com 上直接 200；
        #   ⚑ 文档 §3.6「公共域名恒 401」是普通 key 的情况 ⇒ ⚑ 只在 key 不是 sk-ws- 时提醒专属域名
        if 'ws-' not in base and not key.startswith('sk-ws-'):
            notes.append('⚠ 普通 key 需要「独立业务空间」专属域名（https://ws-xxxx.cn-beijing.maas.aliyuncs.com/api/v1）；sk-ws- 开头的业务空间 key 公共域名可用')
        code, resp = _http('GET', f'{base}/tasks/probe-0000', key)
        if code in (401, 403):
            return {'ok': False, 'msg': f'HTTP {code} —— key 或域名不对', 'detail': resp, 'notes': notes}
        if code == 0:
            return {'ok': False, 'msg': f'连不上：{resp}', 'notes': notes}
        return {'ok': True, 'msg': f'认证通过（HTTP {code}，万相没有模型列表接口，只能探到这里）',
                'notes': notes, 'models': ['wan3.0-video']}
    if name in ('relay', 'ark', 'glm'):
        code, resp = _http('GET', f'{base}/models', key)
        if code == 200 and isinstance(resp, dict):
            ids = [m.get('id') for m in resp.get('data', []) if isinstance(m, dict)]
            if name == 'ark':
                ids = [i for i in ids if i and ('seedance' in i or 'video' in i)]
            if name == 'relay':
                ids = [i for i in ids if i and any(t in i.lower() for t in ('image', 'dall', 'flux', 'banana', 'seedream', 'gpt-image'))] or ids
            models = ids
            msg = f'认证通过，列到 {len(ids)} 个模型'
        elif name == 'glm':
            # ⚑ 智谱不一定有 /models ⇒ ⚑ 用 1 token 的 chat 探一下指定模型
            model = (body or {}).get('model') or pipeline.LLM_DEFAULT_MODEL
            code, resp = _http('POST', f'{base}/chat/completions', key,
                               {'model': model, 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1})
            if code == 200:
                return {'ok': True, 'msg': f'认证通过，模型 {model} 可用', 'models': [model, 'cogvideox-flash', 'cogvideox-3'], 'notes': notes}
            return {'ok': False, 'msg': f'HTTP {code}（模型 {model}）', 'detail': resp, 'notes': notes}
        else:
            return {'ok': False, 'msg': f'HTTP {code}', 'detail': resp, 'notes': notes}
        return {'ok': True, 'msg': msg, 'models': models, 'notes': notes}
    if name == 'minimax':
        code, resp = _http('GET', f'{base}/v1/query/video_generation?task_id=probe', key)
        if code in (401, 403):
            return {'ok': False, 'msg': f'HTTP {code} —— key 不对', 'detail': resp}
        return {'ok': code != 0, 'msg': f'HTTP {code}' + ('（认证通过）' if code not in (0, 401, 403) else ''),
                'detail': resp if code == 0 else None, 'models': ['MiniMax-H3', 'MiniMax-H3-Max']}
    return {'ok': False, 'msg': '未实现'}


# ─────────────────────────────── 环境：一键装 ffmpeg（⚑ 跑 tools/install_ffmpeg.py，⚑ 日志按行给网页）
_INSTALL = {'running': False, 'ok': None, 'log': []}


@app.post('/api/install/ffmpeg')
def install_ffmpeg():
    if _INSTALL['running']:
        return {'ok': True, 'msg': '正在装'}

    def go():
        _INSTALL.update(running=True, ok=None, log=[])
        try:
            p = subprocess.Popen([sys.executable, str(HERE.parent / 'tools' / 'install_ffmpeg.py')],
                                 cwd=str(HERE.parent), env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'},
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            for line in p.stdout:
                _INSTALL['log'].append(line.rstrip())
                del _INSTALL['log'][:-200]
            code = p.wait()
        except Exception as e:
            _INSTALL['log'].append(f'✗ {e}')
            code = 1
        _ffmpeg_path()                                    # ⚑ 装到 tools/bin 或 winget 目录 ⇒ 立刻塞进 PATH，不用重启
        _INSTALL.update(running=False, ok=(code == 0 and bool(shutil.which('ffmpeg'))))
    threading.Thread(target=go, daemon=True).start()
    return {'ok': True}


@app.get('/api/install/ffmpeg')
def install_ffmpeg_status():
    return {**_INSTALL, 'ffmpeg': shutil.which('ffmpeg')}


# ─────────────────────────────── 任务
@app.post('/api/jobs')
def create_job(body: dict):
    mode = body.get('mode', 'demo')
    known = dict(pipeline.ACTIONS)
    for pk in body.get('presets', []):
        known.update(pipeline.PRESETS.get(pk, {}).get('actions', {}))
    known.update(body.get('custom_actions') or {})
    actions = [a for a in body.get('actions', []) if a in known]
    # ⚑ 两段式（用户要求）：⚑ generate 模式允许**只出立绘**（actions 为空）⇒ 玩家看了满意再用它出动作
    portrait_only = mode == 'generate' and not actions and not body.get('portrait_upload') and not body.get('portrait_job')
    if not actions and not portrait_only:
        raise HTTPException(400, '至少选一个动作')
    for a in actions:
        if a not in pipeline.ACTIONS and not (known[a].get('motion') or '').strip() and mode == 'generate':
            raise HTTPException(400, f'自定义动作 {a} 没写运动描述')
    if mode == 'generate':
        if not body.get('prompt', '').strip() and not body.get('portrait_upload') and not body.get('portrait_job') and not body.get('portrait_from'):
            raise HTTPException(400, '真实生成需要一句话描述，或上传一张立绘')
        if not body.get('confirm'):
            raise HTTPException(400, '真实生成会花钱，需要勾选确认')
        have, can = _creds.probe()
        if not body.get('portrait_upload') and not body.get('portrait_job') and not have.get('relay'):
            raise HTTPException(400, '没配 relay（中转站）出不了立绘 —— 到「凭据」配，或上传一张立绘')
        for a in actions:
            prov = (body.get('options', {}).get(a, {}) or {}).get('provider', 'dashscope')
            alias = {'dashscope': 'wan', 'zhipu': 'glm'}.get(prov, prov)
            if not have.get(alias):
                raise HTTPException(400, f'{known[a].get("label", a)} 选的 {prov} 没配 key')
    portrait_upload = body.get('portrait_upload')
    if body.get('portrait_job'):                          # ⚑ 「满意，用这张立绘出动作」：⚑ 直接引用某个任务的立绘
        portrait_upload = str(_safe(body['portrait_job'], body.get('portrait_path') or 'out/01_portrait.png'))
    spec = {'mode': mode, 'prompt': body.get('prompt', ''), 'style': body.get('style', ''), 'view': body.get('view') or 'side',
            'portrait_desc': (body.get('portrait_desc') or '').strip(),     # ⚑ 网页审过的立绘描述（可空）
            'actions': actions, 'options': body.get('options', {}),
            'presets': body.get('presets', []), 'custom_actions': body.get('custom_actions') or {},
            'combo': body.get('combo') or [], 'portrait_upload': portrait_upload}
    pf = body.get('portrait_from')                       # ⚑ 图生图：{job, path} 或 {upload}
    if pf and mode == 'generate':
        if pf.get('job'):
            p = _safe(pf['job'], pf.get('path') or 'out/01_portrait.png')
        elif pf.get('upload'):
            p = Path(pf['upload'])
            if not p.exists():
                raise HTTPException(400, '底图不在')
        else:
            raise HTTPException(400, 'portrait_from 要给 job 或 upload')
        if not (body.get('portrait_edit') or '').strip():
            raise HTTPException(400, '图生图要写改什么（portrait_edit）')
        spec.update(portrait_from=str(p), portrait_edit=body['portrait_edit'], portrait_upload=None)
    return pipeline.start_job(spec)


@app.get('/api/jobs')
def jobs():
    return pipeline.list_jobs()


@app.get('/api/jobs/{job_id}')
def job(job_id: str):
    st = pipeline.load_job(job_id)
    if not st:
        raise HTTPException(404)
    return st


@app.get('/api/jobs/{job_id}/log')
def job_log(job_id: str, offset: int = 0):
    f = pipeline.JOBS / job_id / 'log.txt'
    if not f.exists():
        return {'text': '', 'offset': 0}
    data = f.read_bytes()
    return {'text': data[offset:].decode('utf-8', errors='replace'), 'offset': len(data)}


def _safe(job_id: str, rel: str) -> Path:
    base = (pipeline.JOBS / job_id).resolve()
    p = (base / rel).resolve()
    if base not in p.parents and p != base:
        raise HTTPException(400, 'bad path')
    if not p.exists():
        raise HTTPException(404, rel)
    return p


@app.get('/api/jobs/{job_id}/file/{rel:path}')
def job_file(job_id: str, rel: str):
    return FileResponse(str(_safe(job_id, rel)))


@app.get('/api/jobs/{job_id}/export')
def job_export(job_id: str):
    d = pipeline.JOBS / job_id
    if not (d / 'state.json').exists():
        raise HTTPException(404)
    z = pipeline.export_zip(d, d / f'{job_id}.zip')
    return FileResponse(str(z), filename=f'anim_{job_id}.zip')


@app.post('/api/jobs/{job_id}/reslice')
def job_reslice(job_id: str, body: dict):
    """⚑ 用已出的视频重切图集：换帧数（⚑ 不花钱）。body: {action, frames, pick?}"""
    try:
        return pipeline.reslice(job_id, str(body.get('action') or ''), int(body.get('frames') or 0), body.get('pick') or None)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.delete('/api/jobs/{job_id}')
def job_delete(job_id: str):
    d = (pipeline.JOBS / job_id).resolve()
    if pipeline.JOBS.resolve() not in d.parents:
        raise HTTPException(400)
    shutil.rmtree(d, ignore_errors=True)
    return {'ok': True}


# ─────────────────────────────── agent（DeepSeek 当大脑）
@app.post('/api/agent')
def agent_start(body: dict):
    if not (body.get('goal') or '').strip():
        raise HTTPException(400, '目标是空的')
    if not _creds.get('llm'):
        raise HTTPException(400, '没配 llm 凭据（DeepSeek 等）—— 到「凭据」页配')
    if body.get('mode') == 'generate' and not body.get('confirm'):
        raise HTTPException(400, '真实生成会花钱，需要勾选确认')
    return agent.start({'goal': body['goal'], 'mode': body.get('mode', 'demo'), 'budget': body.get('budget', 3.0),
                        'auto_approve': body.get('auto_approve', False), 'brain': body.get('brain'), 'eyes': body.get('eyes'),
                        'portrait_upload': body.get('portrait_upload')})


@app.post('/api/agent/{run_id}/answer')
def agent_answer(run_id: str, body: dict):
    st = agent.answer(run_id, body.get('approve'), body.get('text', ''))
    if st is None:
        raise HTTPException(404)
    return st


# ─────────────────────────────── 上传：立绘 / 图集提取
@app.post('/api/upload/portrait')
async def upload_portrait(file: UploadFile = File(...)):
    dst = UPLOADS / f'{uuid.uuid4().hex}.png'
    dst.write_bytes(await file.read())
    return {'path': str(dst)}


@app.post('/api/extract')
async def extract(file: UploadFile = File(...), cell: str = Form('192x256')):
    try:
        cw, ch = (int(v) for v in cell.lower().split('x'))
    except ValueError:
        raise HTTPException(400, 'cell 要写成 宽x高')
    src = UPLOADS / f'{uuid.uuid4().hex}.png'
    src.write_bytes(await file.read())
    z = pipeline.slice_sheet_zip(src, (cw, ch), src.with_suffix('.zip'))
    return FileResponse(str(z), filename='frames.zip')


@app.post('/api/prompt/optimize')
def prompt_optimize(body: dict):
    """⚑ 出片前：大模型按立绘/一句话把各动作的运动段写好，⚑ 返回给网页看、改、确认。⚑ 不花视频钱（只一两次 LLM 调用）。
    body: actions / prompt / style / labels / bases / hints / extras / character / portrait_job(+portrait_path) / portrait_upload"""
    actions = [a for a in body.get('actions', []) if a in pipeline.ACTIONS]
    if not actions:
        raise HTTPException(400, '没有动作')
    png = None
    if body.get('portrait_job'):
        try:
            png = _safe(body['portrait_job'], body.get('portrait_path') or 'out/01_portrait.png')
        except HTTPException:
            png = None
    elif body.get('portrait_upload'):
        p = Path(body['portrait_upload'])
        png = p if p.exists() else None
    return pipeline.optimize_prompts(actions, body.get('prompt', ''), body.get('style', ''), png,
                                     labels=body.get('labels') or {}, bases=body.get('bases') or {},
                                     hints=body.get('hints') or {}, extras=body.get('extras') or {},
                                     character=body.get('character', ''), model=body.get('model', ''),
                                     provider=body.get('provider'), view=body.get('view') or 'side')


@app.post('/api/prompt/portrait')
def prompt_portrait(body: dict):
    """⚑ 出图前：大模型把一句话写成立绘外观描述，⚑ 返回描述 + 完整提示词给网页看、改、确认。⚑ 不花图钱。"""
    if not ((body.get('prompt') or '').strip() or (body.get('base') or '').strip()):
        raise HTTPException(400, '先写一句话')
    return pipeline.optimize_portrait(body.get('prompt', ''), body.get('style', ''), hint=body.get('hint', ''),
                                      base=body.get('base', ''), model=body.get('model', ''), provider=body.get('provider'),
                                      view=body.get('view') or 'side', weapon_pose=body.get('weapon_pose') or 'side',
                                      edit=bool(body.get('edit')))


@app.get('/api/prompt/{action}')
def prompt_preview(action: str):
    if action == 'portrait':
        return {'text': pipeline.PORTRAIT_TMPL.format(desc='<你的一句话>', style=pipeline.DEFAULT_STYLE,   # ⚑ 原先漏了 weapon ⇒ KeyError 500
                                                      camera=pipeline.view_of('side')['portrait'], weapon=pipeline.WEAPON_POSE['side'])}
    if action not in pipeline.ACTIONS:
        raise HTTPException(404)
    return {'text': pipeline.build_prompt(action)}


if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('ANIMPIPE_PORT', 8765))
    if os.environ.get('ANIMPIPE_WAIT_PORT'):            # ⚑ 网页重启拉起的：⚑ 等旧进程把端口放出来（最多 ~20s）
        for _ in range(100):
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(('127.0.0.1', port)) != 0:
                    break
            time.sleep(0.2)
    n_stale = pipeline.sweep_stale()                    # ⚑ 到这里才是唯一活着的服务 ⇒ 磁盘上还 running 的一定是上次没跑完的
    if n_stale:                                         # ⚠ 别叫 _stale —— 会把上面那个函数覆盖成 int（2026-09-09 踩过）
        print(f'⚠ 上次没跑完的任务 {n_stale} 个，已标为失败', flush=True)
    print(f'⚑ anim-pipeline web  →  http://127.0.0.1:{port}   （任务目录 {pipeline.JOBS}）', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
