# -*- coding: utf-8 -*-
"""⚑⚑ **图生图：在一张已有立绘上改一版**（⚑ 换武器 / 换装 / 换配色），⚑ 姿势和比例不变。

```bash
python tools/artgen/edit.py base.png out.png --prompt="把弯刀去掉，改成缠着绷带的双拳，摆出拳击防守姿势；其余不变"
python tools/artgen/edit.py base.png out.png --promptfile=p.txt [--model=gpt-image-1] [--size=1024x1024] [--quality=medium]
```

⚑ 走 OpenAI 兼容的 `POST /images/edits`（multipart）。⚑ 凭据和 gen.py 同一套：
  环境变量 OPENAI_API_KEY / OPENAI_BASE_URL → 当前目录 .env → _creds（relay）。

⚠⚠ 为什么要有这份而不是重新文生图：
  ⚑ 「唯一源图原则」（管线文档 §1.1）—— ⚑ 同一角色的所有动作必须出自**同一张站姿**，
  ⚠ 换武器/换装若重新文生图，⚑ 站姿、头身比、朝向都会漂 ⇒ ⛔ 又是一个新角色。
  ⚑ 在原图上编辑，⚑ 只动该动的，⚑ 其余像素级保留。
⚠ 换了立绘之后，⚑ 走路/待机也得按**新立绘**重跑 —— ⛔ 别拿旧立绘的走路配新立绘的攻击（武器会瞬移）。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) anim-pipeline/1.0'


def opt(flag, dflt=None):
    for a in sys.argv[1:]:
        if a.startswith(flag + '='):
            return a.split('=', 1)[1]
    return dflt


def creds():
    key, base = os.environ.get('OPENAI_API_KEY'), os.environ.get('OPENAI_BASE_URL')
    if not key:
        env = os.path.join(os.getcwd(), '.env')
        if os.path.exists(env):
            for line in open(env, encoding='utf-8'):
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    if k == 'OPENAI_API_KEY':
                        key = v.strip()
                    if k == 'OPENAI_BASE_URL':
                        base = v.strip()
    if not key:
        try:
            sys.path.insert(0, os.path.dirname(HERE))
            from _creds import get
            c = get('relay')
            if c:
                key, base = c['key'], c['base']
        except Exception:
            pass
    if not key or not base:
        print('✗ 没有 relay 凭据（OPENAI_API_KEY / OPENAI_BASE_URL）—— python tools/_creds.py --set=relay')
        sys.exit(1)
    return key, base.rstrip('/')


def multipart(fields, files):
    b = '----animpipe' + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()]
    for k, (fn, data, ct) in files.items():
        out += [f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode(), data, b'\r\n']
    out += [f'--{b}--\r\n'.encode()]
    return b''.join(out), f'multipart/form-data; boundary={b}'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    src, dst = args[0], args[1]
    if not os.path.exists(src):
        print(f'✗ 源图不在：{src}')
        sys.exit(1)
    prompt = open(opt('--promptfile'), encoding='utf-8').read().strip() if opt('--promptfile') else opt('--prompt', '')
    if not prompt:
        print('✗ 没给 --prompt= 或 --promptfile=')
        sys.exit(1)
    key, base = creds()
    model = opt('--model', 'gpt-image-1')
    fields = {'model': model, 'prompt': prompt, 'n': '1', 'size': opt('--size', '1024x1024')}
    if model.startswith('gpt-image'):
        fields['quality'] = opt('--quality', 'medium')
    body, ct = multipart(fields, {'image': (os.path.basename(src), open(src, 'rb').read(), 'image/png')})
    req = urllib.request.Request(f'{base}/images/edits', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': ct, 'User-Agent': UA})
    print(f'⚑ 图生图  {os.path.basename(src)}  model={model}  prompt={prompt[:60]}…')
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f'  ✗ HTTP {e.code}\n    {e.read().decode(errors="replace")[:600]}')    # ⚑ 正文才有真正的原因
        sys.exit(1)
    item = (resp.get('data') or [{}])[0]
    if item.get('b64_json'):
        png = base64.b64decode(item['b64_json'])
    elif item.get('url'):
        with urllib.request.urlopen(urllib.request.Request(item['url'], headers={'User-Agent': UA}), timeout=120) as r:
            png = r.read()
    else:
        print(f'  ✗ 返回里没有图：{json.dumps(resp, ensure_ascii=False)[:400]}')
        sys.exit(1)
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or '.', exist_ok=True)
    open(dst, 'wb').write(png)
    print(f'  ✅ {dst}  ({len(png) // 1024} KB)')


if __name__ == '__main__':
    main()
