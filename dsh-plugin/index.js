// ⚑⚑⚑ DeepSeek Harness 插件 —— ⚑ 把 anim-pipeline 的每一步注册成 dsh 工具。
//
// ⚑ 这一层只是壳：⚑ 每个工具 spawn 一次 `python mcp/server.py call <工具名>`，⚑ 参数 JSON 走 stdin，
//   ⚑ 所有逻辑仍在 Python 里一份（⚑ 和 MCP server / 网页端 / agent.py 共用）。
// ⚑ key：⚑ 插件设置里填的以环境变量注给子进程 —— ⚑ `_creds.py` 的查找顺序第一位就是环境变量，
//   ⚑ Python 侧一行不改；⚑ 没填的退回 ~/.gamegen/creds.json。
// ⚑ 脑子和眼睛由 dsh 提供（V4 Pro 多模态）：⚑ contact_sheet 返回联络表的绝对路径，⚑ 让模型自己看。
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// ⚠⚠ 依赖解析：⚑ 插件装进 profile 是 `link:`（软链），⚑ Node 会从本文件的**真实路径**往上找 node_modules
//   ⇒ ⛔ 直接 `import '@deepseek-ai/dsh-tools'` 必炸（实测 ERR_MODULE_NOT_FOUND；profile 的 node_modules 里也没有它，
//   ⚑ dsh 是从自己的安装树解析的）。⇒ ⚑ 多路找：正常 import → 从 dsh 启动器自身位置（process.argv[1]）→
//   ⚑ 从 cwd / ~/.dsh 往上找；⚑ 都不行就退化：defineTool 恒等（它只是类型帮助器）、Config 不校验。
async function loadDep(name) {
  try { return await import(name) } catch {}
  const bases = [process.argv[1], join(process.cwd(), 'x'), join(homedir(), '.dsh', 'x')].filter(Boolean)
  for (const base of bases) {
    try { return await import(pathToFileURL(createRequire(base).resolve(name)).href) } catch {}
  }
  return null
}
const _tools = await loadDep('@deepseek-ai/dsh-tools')
const defineTool = _tools?.defineTool ?? ((t) => t)
const _schema = await loadDep('@deepseek-ai/schemastery')
const z = _schema?.default ?? _schema

export const name = 'anim-pipeline'
export const inject = ['tools']

export const Config = !z ? undefined : z.object({
  python: z.string().default('python').description('Python 3 可执行文件（需已 pip install numpy pillow mcp，且 ffmpeg 在 PATH）'),
  repoDir: z.string().description('anim-pipeline 仓库目录（含 tools/ 和 mcp/）。留空 = 本插件所在仓库'),
  workDir: z.string().description('产物目录（ANIMPIPE_ROOT）。留空 = 当前会话工作目录；产物落 work/anim 与 out/anim'),
  dashscopeApiKey: z.string().role('secret').description('万相（阿里云百炼）apiKey —— 出片定稿唯一必需的一家'),
  dashscopeBaseUrl: z.string().default('https://dashscope.aliyuncs.com/api/v1').description('sk-ws- 开头的业务空间 key 用公共域名即可（实测）；普通 key 要填专属域名 https://ws-xxxx.cn-beijing.maas.aliyuncs.com/api/v1'),
  dashscopeWorkspaceId: z.string().description('万相业务空间 id'),
  relayApiKey: z.string().role('secret').description('出立绘用的 OpenAI 兼容中转站 key（gpt-image 类）；已有立绘可不填'),
  relayBaseUrl: z.string().description('中转站 base url，形如 https://xxx/v1'),
  zhipuApiKey: z.string().role('secret').description('智谱（免费抽构图，可选）'),
  arkApiKey: z.string().role('secret').description('火山 seedance（免费额度抽姿势，可选）'),
})

const HERE = dirname(fileURLToPath(import.meta.url))

// ⚑ 工具规格 —— ⚑ 描述里写清单价和判据，⚑ 让模型在花钱前就知道数（⚑ 这是文档 §8.4 的纪律）
const TOOLS = [
  ['doctor', '环境 / 凭据 / 能做什么 / 单价。开始任何工作前先调它。', {}],
  ['gen_portrait', '出立绘（收费 ¥0.15/发，走 relay 中转站）。desc 只写角色外观，不写雾气/光晕/特效。返回 portrait 路径。',
    { desc: { type: 'string', required: true, description: '角色外观一句话' }, style: { type: 'string', description: '风格，默认 Q 版 3.5 头身厚涂' } }],
  ['edit_portrait', '在已有立绘上图生图改版（换武器/换装，姿势比例不变；¥0.15）。换了立绘后所有动作都要按新立绘重出。',
    { src: { type: 'string', required: true }, instruction: { type: 'string', required: true, description: '改什么；其余不变' } }],
  ['make_liubai', '立绘 → 留白图（角色占 55%，底色从图里量）。出片的唯一合法输入；满画幅立绘直接出片会四边出画。',
    { src: { type: 'string', required: true }, dst: { type: 'string', default: 'liubai.png' } }],
  ['gen_video', '图生视频。motion 只写运动段（要看到的姿态和方向、该动的部位点名+幅度、结尾回站姿）；构图约束和禁止句由模板补。--img 与 --last 都给留白图 ⇒ 首尾同图。单价：dashscope 480P/2s ¥0.40、480P/5s ¥1.05、720P/5s ¥2.10；zhipu/ark 免费但只能探方向。一次一发，每动作最多 2 发。',
    { action: { type: 'string', required: true, description: '动作 tag，如 walk / attack / jab' }, motion: { type: 'string', required: true },
      liubai: { type: 'string', default: 'liubai.png' }, provider: { type: 'string', default: 'dashscope', description: 'dashscope | zhipu | ark' },
      res: { type: 'string', default: '480P' }, dur: { type: 'number', default: 2 }, model: { type: 'string' } }],
  ['synth_demo', '不花钱的合成素材（kind = walk | attack），用来验证后半段链路。',
    { action: { type: 'string', required: true }, kind: { type: 'string', default: 'attack' } }],
  ['contact_sheet', '带帧号的联络表（挑帧靠它，别凭感觉）。返回 png 绝对路径 + 总帧数；用你的看图能力打开它，帧号 1 起、和 vid2anim 的 at 同一套。一次性动作要挑出蓄力→发力→命中停住→收招，重心放命中段；肉眼必查"刀有没有变成细白线"。',
    { mp4: { type: 'string', required: true }, n: { type: 'number', default: 24 } }],
  ['vid2anim', '视频 → 序列帧图集（抠灰底/对齐/归一/切格）。走路 frames=4 pick=loop；一次性动作 frames=6 pick=even 并给 at="12,22,30,34,38,52"。形变≈0 说明挑到的帧全一样——改 frames 或改用 at。',
    { mp4: { type: 'string', required: true }, tag: { type: 'string', required: true }, frames: { type: 'number', default: 4 },
      pick: { type: 'string', default: 'loop' }, cell: { type: 'string', default: '192x256' }, at: { type: 'string', description: '手点帧号，逗号分隔' } }],
  ['anim_bench', '五项验收：动量/形变%/循环缝/剪影密度%/亮度漂。循环缝>1.5 循环会跳；亮度漂>10 多半是崩了；剪影密度<50 缩小后散架。',
    { sheet: { type: 'string', required: true }, cell: { type: 'string', default: '192x256' } }],
  ['move_check', '逐帧量位移/抬升。走路应≈0；代码位移只能补动画里已有的位移。', { mp4: { type: 'string', required: true } }],
  ['blade_check', '逐帧查"刀变细白线"。不过就用 at 避开那些帧号重新 vid2anim，不用重出片。', { mp4: { type: 'string', required: true } }],
  ['anim_check', '图集自检：命中格武器朝向/脚底一致/贴边。攻击类专用（走路别用）。"武器朝后"换 hit；"贴边"放大 cell 重切。foot_trim 传 vid2anim 返回的中位数。',
    { tag: { type: 'string', required: true }, cell_w: { type: 'number', required: true }, foot_trim: { type: 'number', required: true },
      hit: { type: 'number', required: true }, facing: { type: 'string', default: 'left' } }],
  ['export_zip', '把若干动作（逗号分隔 tag）打成 zip：图集 + gif + manifest。', { tags: { type: 'string', required: true }, out_zip: { type: 'string', default: 'anim_export.zip' } }],
]

function envFrom(config) {
  const e = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
  const put = (k, v) => { if (v) e[k] = v }
  put('DASHSCOPE_API_KEY', config.dashscopeApiKey); put('DASHSCOPE_BASE_URL', config.dashscopeBaseUrl)
  put('DASHSCOPE_WORKSPACE_ID', config.dashscopeWorkspaceId)
  put('OPENAI_API_KEY', config.relayApiKey); put('OPENAI_BASE_URL', config.relayBaseUrl)
  put('ZHIPU_API_KEY', config.zhipuApiKey); put('ARK_API_KEY', config.arkApiKey)
  return e
}

function callPython(config, tool, args, exec) {
  const repo = config.repoDir || resolve(HERE, '..')
  const server = resolve(repo, 'mcp', 'server.py')
  const cwd = config.workDir || exec?.cwd || process.cwd()
  return new Promise((done) => {
    if (!existsSync(server)) return done({ error: `找不到 ${server}：把 repoDir 指到 anim-pipeline 仓库（含 tools/ 与 mcp/）` })
    const env = { ...envFrom(config), ANIMPIPE_ROOT: cwd }
    const p = spawn(config.python || 'python', [server, 'call', tool], { cwd, env, signal: exec?.signal, windowsHide: true })
    let out = '', err = ''
    p.stdout.on('data', (d) => { out += d })
    p.stderr.on('data', (d) => { err += d })
    p.on('error', (e) => done({ error: `启动 Python 失败：${e.message}（检查插件设置里的 python 路径）` }))
    p.on('close', (code) => {
      const line = out.trim().split('\n').pop() || ''
      try { return done(JSON.parse(line)) } catch { return done({ error: `工具 ${tool} 输出不是 JSON（退出码 ${code}）`, stdout: out.slice(-1200), stderr: err.slice(-800) }) }
    })
    p.stdin.end(JSON.stringify(args || {}))
  })
}

export function apply(ctx, config) {
  for (const [tname, description, parameters] of TOOLS) {
    ctx.tools.register(defineTool({
      name: `anim_${tname}`,
      description,
      parameters,
      output: {
        schema: { type: 'object', additionalProperties: true },   // ⚑ dsh 要求显式写，⛔ 不写启动直接炸
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 1) }],
      },
      async execute(args, exec) {
        exec?.logger?.info?.(`[anim-pipeline] ${tname} ${JSON.stringify(args).slice(0, 200)}`)
        return callPython(config, tname, args, exec)
      },
    }))
  }
  ctx.logger?.info?.(`[anim-pipeline] 已注册 ${TOOLS.length} 个工具（anim_*）`)
}
