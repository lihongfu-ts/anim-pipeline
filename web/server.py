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
import sys
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

app = FastAPI(title='anim-pipeline')
app.mount('/static', StaticFiles(directory=str(HERE / 'static')), name='static')
UPLOADS = pipeline.JOBS / '_uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
_stale = pipeline.sweep_stale()
if _stale:
    print(f'⚠ 上次没跑完的任务 {_stale} 个，已标为失败')

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
        'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')),
        'numpy': _has('numpy'), 'pillow': _has('PIL'),
        'creds': {n: ({'from': c['_from'], 'base': c['base'], 'key': _mask(c['key']), 'model': c.get('model', ''),
                       'vision_model': c.get('vision_model', '')}
                      if c else None) for n, c in cr.items()},
        'can': can,
        'caps': {k: v[2] for k, v in _creds.CAPS.items()},
        'actions': pipeline.ACTIONS,
        'presets': pipeline.PRESETS,
        'custom_defaults': pipeline.CUSTOM_DEFAULTS,
        'providers': pipeline.VIDEO_PROVIDERS,
        'prices': {'portrait': pipeline.PRICE_PORTRAIT,
                   'video': {f'{p}|{r}|{d}': v for (p, r, d), v in pipeline.PRICE_VIDEO.items()},
                   'per_sec': {f'{p}|{r}': v for (p, r), v in pipeline.PER_SEC.items()},
                   'zhipu': pipeline.ZHIPU_PRICE},
        'llm_default': pipeline.LLM_DEFAULT_MODEL,
        'creds_file': str(_creds.USER_CFG),
    }


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
        if not body.get('prompt', '').strip() and not body.get('portrait_upload') and not body.get('portrait_job'):
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
    spec = {'mode': mode, 'prompt': body.get('prompt', ''), 'style': body.get('style', ''),
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


@app.get('/api/prompt/{action}')
def prompt_preview(action: str):
    if action == 'portrait':
        return {'text': pipeline.PORTRAIT_TMPL.format(desc='<你的一句话>', style=pipeline.DEFAULT_STYLE)}
    if action not in pipeline.ACTIONS:
        raise HTTPException(404)
    return {'text': pipeline.build_prompt(action)}


if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('ANIMPIPE_PORT', 8765))
    print(f'⚑ anim-pipeline web  →  http://127.0.0.1:{port}   （任务目录 {pipeline.JOBS}）')
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
