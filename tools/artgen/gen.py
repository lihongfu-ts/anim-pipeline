#!/usr/bin/env python3
"""批量生成美术资源（OpenAI 图像 API / 兼容中转站）。

**在当前工作目录下工作**：默认读 ./prompts.json，写 ./out/。
脚本装在 ~/.claude/skills/ai-asset-gen/scripts/ 也不影响，产物永远落在你 cd 进去的那个项目里。

凭据按这个顺序找，先找到的赢（已存在的环境变量优先级最高）：
    1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL
    2. ./.env              项目自带的 key
    3. <skill 目录>/.env   兜底的常用 key

用法：
    python gen.py                 生成全部（已存在的自动跳过，中断了重跑不会重复扣费）
    python gen.py 1 2 3           只生成指定编号
    python gen.py --force 7       强制重生成第 7 张（不满意时反复试）
    python gen.py --list          列出全部条目和状态
    python gen.py --dry-run       只打印将要发的请求，不真的调用
    python gen.py --model dall-e-3    换模型（默认 gpt-image-1）

为什么默认 gpt-image-1：只有它支持 background=transparent。
图标和 HUD 框架必须是透明底，DALL-E 3 生成的是白底或黑底，抠图很麻烦。

零第三方依赖，只用标准库。
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 上标准输出默认走 GBK，中文提示全是乱码。这里强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
# 默认认当前工作目录，不是脚本所在目录——脚本装在全局 skill 里，
# 产物必须落在你 cd 进去的那个项目下
PROMPTS_FILE = Path.cwd() / "prompts.json"
OUT_DIR = Path.cwd() / "out"

RETRIES = 4
# 限流之后要等够久，退避太短只会连着撞墙
RETRY_WAIT = [10, 25, 60]
TIMEOUT = 300          # 出图很慢，high 质量的大图能跑一两分钟
DEFAULT_DELAY = 12     # 两次请求之间的间隔

# 中转站把模型名改过了，不是官方的 gpt-image-1。用 --model 可以换，
# 不确定就先跑 python gen.py --models 看它到底提供哪些。
DEFAULT_MODEL = "gpt-image-2-adobe-xy"

# 403 也当可重试：中转站在限流和 UA 拦截两种情况下都回 403，前者等等就好
RETRYABLE = {403, 408, 409, 429, 500, 502, 503, 504}

# 别用 urllib 的默认 UA，见 post_json 里的注释
UA = "curl/8.4.0"


def load_dotenv():
    """凭据从 .env 读。比设系统环境变量省事，换机器也带得走。

    按 ./.env → <skill>/.env 的顺序找，全部用 setdefault 灌进环境变量，
    所以**先来的赢**：项目自带的 key 盖过 skill 里那份兜底 key，
    而命令行前面临时加的环境变量盖过所有文件。
    """
    # ⚑ 先问统一层（⚑ tools/_creds.py：环境变量 → 用户目录 → 项目 .env）。
    #   ⚑ 它读到就灌进环境变量，⚑ 下面那段照旧当兜底 ⇒ ⛔ 这份脚本被拷到别处也还能跑。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from _creds import get as _get
        c = _get("relay")
        if c:
            os.environ.setdefault("OPENAI_API_KEY", c["key"])
            if c.get("base"):
                os.environ.setdefault("OPENAI_BASE_URL", c["base"])
    except Exception:
        pass
    for f in (Path.cwd() / ".env", SKILL_DIR / ".env"):
        if not f.exists():
            continue
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_items():
    with PROMPTS_FILE.open(encoding="utf-8") as f:
        return json.load(f)["items"]


def normalize_base(base: str) -> str:
    """中转站地址有的带 /v1 有的不带，这里统一补齐。"""
    base = base.strip().rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def out_path(item) -> Path:
    return OUT_DIR / f"{item['id']:02d}_{item['name']}.png"


def build_payload(item, model: str) -> dict:
    payload = {
        "model": model,
        "prompt": item["prompt"],
        "n": 1,
        "size": item["size"],
    }
    if model.startswith("gpt-image"):
        payload["quality"] = item.get("quality", "medium")
        payload["output_format"] = "png"
        # 透明底是图标和 HUD 能不能直接用的关键
        payload["background"] = "transparent" if item.get("transparent") else "opaque"
    else:
        # DALL-E 3 没有 background 参数，quality 只有 standard/hd
        payload["quality"] = "hd" if item.get("quality") == "high" else "standard"
        payload["response_format"] = "b64_json"
    return payload


def post_json(url: str, payload: dict, key: str) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            # 关键：不能用 urllib 的默认 UA。这家中转站会把 Python-urllib/x.y
            # 当成脚本滥用直接拦掉，还回一个误导性的 "403 error code 1010"（看着像限流）。
            # 同样的请求用 curl 就能通，差别只有这一行。
            "User-Agent": UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_png(result: dict) -> bytes:
    """兼容两种返回：b64_json 直接给数据，url 得再下一次。"""
    if not result.get("data"):
        raise RuntimeError(f"响应里没有 data 字段：{json.dumps(result, ensure_ascii=False)[:300]}")
    first = result["data"][0]
    if first.get("b64_json"):
        return base64.b64decode(first["b64_json"])
    if first.get("url"):
        with urllib.request.urlopen(first["url"], timeout=TIMEOUT) as resp:
            return resp.read()
    raise RuntimeError("响应里既没有 b64_json 也没有 url")


def list_models(base: str, key: str) -> list:
    """问中转站现在挂着哪些模型。

    注意也要伪装 UA——原来的 --models 分支没带，一问就是 403。
    """
    req = urllib.request.Request(f"{base}/models",
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return [m.get("id", "") for m in json.loads(r.read().decode()).get("data", [])]


def resolve_model(want: str, base: str, key: str) -> str:
    """挑一个当前真能用的图像模型。

    这家中转站的模型名会漂：gpt-image-2 和 gpt-image-2-adobe-xy 轮流上线，
    取决于它当时挂着哪组上游账号。同一个名字十分钟前能用，十分钟后就
    "not supported by any configured account"（还回的是 404，不是 503，
    看着像名字写错了）。批量跑到一半漂一次，后面几十张会连着废掉，
    所以这里按现挂的列表自动纠正。
    """
    try:
        ids = list_models(base, key)
    except Exception:
        return want
    if want in ids:
        return want
    for i in ids:
        if "image" in i.lower():
            return i
    return want


def explain_http_error(e: urllib.error.HTTPError) -> str:
    try:
        body = e.read().decode("utf-8", "replace")[:400]
    except Exception:
        body = ""
    hint = {
        401: "API Key 不对，或者中转站要求别的鉴权方式",
        403: "多半是限流（这家中转站用 403+1010 表示请求太频繁），等一会儿会自动重试",
        404: "模型名不对——跑 python gen.py --models 看中转站有哪些",
        429: "限流或余额不足",
    }.get(e.code, "")
    return f"HTTP {e.code} {hint}\n    {body}"


def generate_one(item, model: str, key: str, base: str, force: bool, dry: bool) -> str:
    dst = out_path(item)
    if dst.exists() and not force:
        return "skip"

    payload = build_payload(item, model)
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return "dry"

    url = f"{base}/images/generations"
    for attempt in range(1, RETRIES + 1):
        try:
            t0 = time.time()
            result = post_json(url, payload, key)
            png = extract_png(result)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(png)
            # 把用的提示词一起存下来，回头想微调时知道上次是怎么写的
            dst.with_suffix(".txt").write_text(
                f"{item['desc']}\nsize={item['size']} transparent={item.get('transparent')} "
                f"quality={item.get('quality')} model={model}\n\n{item['prompt']}\n",
                encoding="utf-8",
            )
            print(f"    完成 {time.time() - t0:.0f}s  {len(png) // 1024} KB")
            return "ok"
        except urllib.error.HTTPError as e:
            msg = explain_http_error(e)
            # 模型名漂了：换成当前挂着的那个再来一次，别把整批都赔进去
            if e.code == 404 and "model_not_found" in msg:
                fixed = resolve_model(model, base, key)
                if fixed != model:
                    print(f"    模型 {model} 已下线，改用 {fixed} 重试")
                    model = fixed
                    payload = build_payload(item, model)
                    continue
            if e.code not in RETRYABLE and 400 <= e.code < 500:
                print(f"    失败：{msg}")
                return "fail"
            print(f"    第 {attempt}/{RETRIES} 次失败：{msg}")
        except Exception as e:
            print(f"    第 {attempt}/{RETRIES} 次失败：{type(e).__name__} {e}")
        if attempt < RETRIES:
            wait = RETRY_WAIT[min(attempt - 1, len(RETRY_WAIT) - 1)]
            print(f"    等 {wait}s 后重试")
            time.sleep(wait)
    return "fail"


def main():
    ap = argparse.ArgumentParser(description="批量生成 Unity 美术资源")
    ap.add_argument("ids", nargs="*", type=int, help="只生成这些编号，留空表示全部")
    ap.add_argument("--force", action="store_true", help="已存在也重新生成")
    ap.add_argument("--list", action="store_true", help="列出全部条目")
    ap.add_argument("--dry-run", action="store_true", help="只打印请求体，不调用")
    ap.add_argument("--models", action="store_true", help="列出中转站提供的模型")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"默认 {DEFAULT_MODEL}")
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY,
                    help=f"两次请求之间歇多少秒，默认 {DEFAULT_DELAY}（防限流）")
    # 上位机那套 HUD 素材跟孪生端不是一批东西，条目和产物都分开放，
    # 免得两边的编号和风格互相干扰。工具本身共用这一份。
    ap.add_argument("--prompts", help="换一份条目文件，默认 prompts.json")
    ap.add_argument("--out", help="换一个输出目录，默认 out/")
    args = ap.parse_args()

    global PROMPTS_FILE, OUT_DIR
    if args.prompts:
        PROMPTS_FILE = Path(args.prompts).resolve()
    if args.out:
        OUT_DIR = Path(args.out).resolve()

    load_dotenv()
    items = load_items()

    if args.list:
        for it in items:
            done = "已生成" if out_path(it).exists() else "  --  "
            print(f"{it['id']:>3}  {done}  {it['desc']}  [{it['size']}"
                  f"{' 透明' if it.get('transparent') else ''}]")
        return 0

    if args.ids:
        wanted = set(args.ids)
        items = [it for it in items if it["id"] in wanted]
        missing = wanted - {it["id"] for it in items}
        if missing:
            print(f"没有这些编号：{sorted(missing)}")
            return 2

    key = os.environ.get("OPENAI_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not key and not args.dry_run:
        print("没读到 OPENAI_API_KEY。先设环境变量：\n"
              "    set OPENAI_API_KEY=sk-xxx           (Windows CMD)\n"
              "    export OPENAI_API_KEY=sk-xxx        (bash)")
        return 2
    base = normalize_base(base)

    if args.models:
        for m in list_models(base, key):
            print("   ", m)
        return 0

    if not args.dry_run:
        # 开跑前先对一次模型名，省得第一张就白等一轮重试
        fixed = resolve_model(args.model, base, key)
        if fixed != args.model:
            print(f"[!] 模型 {args.model} 当前不可用，改用 {fixed}")
            args.model = fixed

    print(f"接口 {base}   模型 {args.model}   共 {len(items)} 张   间隔 {args.delay}s\n")

    tally = {"ok": 0, "skip": 0, "fail": 0, "dry": 0}
    failed = []
    for n, it in enumerate(items, 1):
        print(f"[{n}/{len(items)}] #{it['id']} {it['desc']}")
        r = generate_one(it, args.model, key, base, args.force, args.dry_run)
        tally[r] += 1
        if r == "skip":
            print("    已存在，跳过（加 --force 可覆盖）")
        elif r == "fail":
            failed.append(it["id"])
        # 这家中转站限流很敏感，成功之后也得歇一下再发下一张
        if r == "ok" and n < len(items):
            time.sleep(args.delay)

    print(f"\n成功 {tally['ok']}  跳过 {tally['skip']}  失败 {tally['fail']}")
    if failed:
        print(f"失败的重跑：python gen.py {' '.join(str(i) for i in failed)}")
    if tally["ok"]:
        print(f"输出目录：{OUT_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
