# dsh-anim-pipeline —— DeepSeek Harness 插件

让 DeepSeek Harness（`dsh`，V4 Pro 当脑子）自己跑完「一句话 → 立绘 → 留白图 → 出片 → 序列帧图集 → 验收」。
插件只是壳：13 个 `anim_*` 工具，每个都 spawn 一次仓库里的 Python 脚本；判断力来自
[skills/anim-pipeline/SKILL.md](../skills/anim-pipeline/SKILL.md)（提示词铁律、provider 分工、读数路由、花钱纪律）。

## 用户需要准备什么

1. Python 3 + `pip install numpy pillow mcp`，`ffmpeg` 在 PATH
2. 一份本仓库（工具在 `tools/`，插件在 `dsh-plugin/`）：`git clone https://github.com/lihongfu-ts/anim-pipeline`
3. 万相（阿里云百炼）的 key —— **出片定稿唯一必需的一家**。已有立绘就不需要中转站 key

## 安装（三种，任选）

```sh
# ① 本地路径（相对路径锚定到当前目录）
dsh plugin --profile web add ./anim-pipeline/dsh-plugin

# ② 从 git 装（pnpm 支持子目录）
dsh plugin --profile web add "github:lihongfu-ts/anim-pipeline#path:dsh-plugin"

# ③ 发布到 npm 后
dsh plugin --profile web add dsh-anim-pipeline
```

验证：`dsh --profile web --dump-config` 里出现 `# == dsh-anim-pipeline` 层。重启 `dsh web` 生效。

技能：`npx skills add https://github.com/lihongfu-ts/anim-pipeline`（仓库里 `skills/anim-pipeline/SKILL.md`），
或把这个目录拷到 dsh 的 skills 目录。**没有这份 skill，工具还在，但模型不知道什么时候该用哪把尺子。**

## key 配在哪（三种，按优先级）

`tools/_creds.py` 的查找顺序：**环境变量 → `~/.gamegen/creds.json` → 当前目录 `.env` / `config.json`**。插件对应三种配法：

| 方式 | 怎么做 | 适合 |
|---|---|---|
| **插件设置**（推荐） | dsh Web UI → 插件 anim-pipeline → 填 `dashscopeApiKey` / `dashscopeBaseUrl` / `dashscopeWorkspaceId`（出立绘再填 `relayApiKey` / `relayBaseUrl`） | 只用 dsh 的人 |
| 命令行写用户目录 | `python tools/_creds.py --set=wan`（交互式，写 `~/.gamegen/creds.json`，不进仓库） | 同时用网页端 / MCP / CLI 的人 |
| 环境变量 | `DASHSCOPE_API_KEY` `DASHSCOPE_BASE_URL` `DASHSCOPE_WORKSPACE_ID` `OPENAI_API_KEY` `OPENAI_BASE_URL` `ZHIPU_API_KEY` `ARK_API_KEY` | CI / 临时覆盖 |

插件设置里填的 key 以上面那组环境变量注给 Python 子进程；留空的项退回用户目录那份。
`python tools/_creds.py` 能报告"现在配了哪家、能做哪一步、缺了会怎样"。

> ⚠ 万相的 `baseUrl` 必须是「独立业务空间」专属域名 `https://ws-xxxx.cn-beijing.maas.aliyuncs.com/api/v1`——
> 公共域名恒 401 且提示"key 格式不对"，极容易误判成 key 有问题。域名在控制台导出的 apiKey CSV 里，字段 `dashScope`。

> 插件设置的值会写进 profile 的 `cordis.patch.yml`（明文）。介意的话用第二种。

**两套 key 别混**：上面这些是管线**出片 / 出图**的 key（万相、中转站）；dsh 自己跑模型（V4 Pro）的 DeepSeek key 走 dsh 的设置页或
`DEEPSEEK_API_KEY` 环境变量，实测 `DEEPSEEK_API_KEY=sk-… dsh --profile headless "…"` 直接可用。

## 已验证（2026-09-09，dsh 0.1.2-rc.1，Windows）

```sh
dsh plugin --profile headless add ./anim-pipeline/dsh-plugin       # 装
dsh --profile headless --dump-config | grep anim-pipeline            # 出现 "# == dsh-anim-pipeline" 层
DEEPSEEK_API_KEY=sk-… dsh --profile headless "调用 anim_doctor，一句话告诉我 ffmpeg 是否可用、配了哪几家、万相 480P/2s 单价"
# → ffmpeg 可用；仅配了 llm 一家凭据；万相 480P/2s 单价 ¥0.40      ← 三个数全部来自工具
```

踩过的两个坑已在 `index.js` 里处理：`link:` 安装时 `@deepseek-ai/dsh-tools` 从插件真实路径解析不到（改成多路解析，从 dsh 启动器位置找）；
`output.schema` 为 object 时必须显式写 `additionalProperties`。

## 其它配置

| 项 | 默认 | 说明 |
|---|---|---|
| `python` | `python` | Python 可执行文件；Windows 上多半是 `python`，macOS 可能要 `python3` |
| `repoDir` | 插件所在仓库 | 从 npm 装时**必须**指到 anim-pipeline 仓库目录（含 `tools/` 与 `mcp/`） |
| `workDir` | 会话工作目录 | 产物落 `work/anim`（视频、联络表、gif）和 `out/anim`（图集） |

patch 是按 id 整体替换、不深合并：自己的 `cordis.patch.yml` 里改配置要**整条**重写（含 `name: dsh-anim-pipeline`）。

## 模型怎么用它

`anim_doctor` → `anim_make_liubai` → 每个动作 `anim_gen_video` → `anim_contact_sheet`（模型自己看图挑帧）→ `anim_vid2anim` →
`anim_anim_bench` → 检查器 → `anim_export_zip`。收费工具的描述里写了单价；请在 dsh 的权限策略里把 `anim_gen_portrait` /
`anim_edit_portrait` / `anim_gen_video` 设为需审批。

不用 dsh？同一份 Python 也是 **MCP server**：`python mcp/server.py`（Claude Code / Cline / Continue 直接挂），
也有独立网页端 `python web/server.py`（自带 DeepSeek 大脑的 agent，见仓库 README）。
