# existence-trace 系统设计 v0.1（草案，待 Tiger 审）

> 工作名 `existence-trace`（存在痕迹），正式名待定。

## 1. 目标

把 Tiger 的「存在痕迹」定时工作流做成别人也能安装的开源项目：
每天写日记 → AI 按可配置的规则估计状态（PAD / G / XP）→ 写成日报 → 用户在笔记里反馈 → 规则和估计持续校准。

### 已确认的产品决定（2026-10-07）

| # | 决定 |
|---|---|
| 1 | v1 范围：核心闭环 = 每日日报 + PAD + G + 反馈校准。周报、成就页、周审计放 v2 |
| 2 | 平台无关：核心是 Python 引擎 + 配置，调度层适配 Codex、Claude Code、cron |
| 3 | 存储：本地 Markdown/JSON 优先，Notion 作为可选输出适配器 |
| 4 | 反馈渠道：笔记内反馈区（不依赖邮箱登录） |
| 5 | 模型：引擎通用、维度/权重/词表全部来自配置；Tiger 的 PAD/G/XP 去掉个人事件后作为默认预设 |
| 6 | 语言：代码与 README 英文；prompt、词表、模板中英双语 |
| 7 | 自我来信放 v2 可选模块；GitHub 空提交 streak 不进入开源产品 |

## 2. 原则

### 2.2 值得保留的设计（产品的灵魂）

这些是 Tiger 几个月迭代出来、别人最难自己想到的东西。v1 要把它们写成**代码和测试**，不能只留在 prompt 里：

1. **原文保护**：用户原话逐字保存（原版本），AI 只能在授权区域做最小整理（阅读版）。
2. **AI 估计不冒充自述**：每个数值都带 `provenance`（self_report / model_inference / gap_fill）、来源和置信度。
3. **缺失不是 0**：没写就留空或标「待评分」；补值必须低置信度，且不进入校准和准确率统计。
4. **事件只入账一次**：重大事件用唯一 `event_id`，在发生日记一次。
5. **冻结预测再训练**：先保存预测快照，反馈到了再评价；数值修正要过 10 日训练 + 10 日检验门槛。
6. **最新反馈优先**：同一适用范围内，按用户原始表达时间，新规则替代旧规则；原始反馈永远保留为证据。
7. **先决定、再报告、邀请反馈**：AI 不等精确数字，先做合理决定并写明理由，用户可以推翻。
8. **写后核验**：每次写入后读回检查，不以「已执行」冒充「已完成」。


## 3. 架构

```
           ┌──────────── 调度层（adapters）─────────────┐
           │ Codex automation / Claude Code / cron     │
           └──────────────┬────────────────────────────┘
                          │ 运行任务：daily / g / calibrate
┌──────────────┐   ┌──────▼──────────┐   ┌─────────────────┐
│ 用户笔记       │──▶│ Agent（LLM）     │──▶│ trace 引擎       │
│ journal/*.md  │   │ 读证据、做判断     │   │ 校验、计算、写入   │
│ 原文区/反馈区   │◀──│ 输出结构化 JSON    │◀──│ 确定性、可测试    │
└──────────────┘   └─────────────────┘   └────────┬────────┘
                                                  │
                         ┌────────────────────────┼──────────────┐
                         ▼                        ▼              ▼
                  data/*.jsonl            journal 日报 .md    Notion 适配器（可选）
           （预测、反馈、决定、规则）        + SVG 图
```

**分工原则**：需要判断的（读日记、给 PAD 档位和 G 细项打分、写第一人称叙事）交给 Agent；
可以算的（加权、补值、平滑、去重、版本替代、保护块校验）全部交给引擎。
Agent 把判断写成 JSON，引擎拒绝不合格的输入（比如缺 provenance、自报和推断混用）。

### 3.1 目录

```
etrace/                 Python 包，只用标准库
  config.py            读 trace.toml，时区等
  store.py             追加式 JSONL、原子写、文件锁
  presets.py           加载并校验预设
  pad.py               五档、27 格投影、时长加权、平滑
  chart.py             SVG 图
  g.py                 B/E/G 计算、补值、冻结预测、校准门槛
  xp.py                维度等级 → 分数、事件加分
  rules.py             规则版本与替代、decisions 日志
  feedback.py          解析笔记反馈区、去重
  journal.py           补记页模板、正式化、保护块校验
  prompts/             任务 prompt 模板（zh / en）
  adapters/            scheduler: codex / claude-code / cron；output: notion
  cli.py               `python3 -m etrace ...`
presets/default/       Tiger 方法的去个人化版本（pad.json, g.json, xp.json）
tests/                 unittest
```

### 3.2 用户数据目录（不在仓库里）

```
~/existence-trace-data/
  trace.toml           时区、预设、输出方式、Notion token 路径
  profile/             用户自己的偏好摘要、语言样本（可选）
  journal/2026-10-07.md
  data/ predictions.jsonl  feedback.jsonl  decisions.jsonl  rules.json  questions.jsonl  usage.jsonl
  charts/
```

### 3.3 日报文件结构（Markdown）

```
<!-- trace:ai:headline -->        今日主线（AI 区）
<!-- trace:ai:pad -->             PAD 图 + 时点表（AI 区）
<!-- trace:user:pad-self -->      我来填写 PAD（用户区，保护）
<!-- trace:ai:xp -->              XP
<!-- trace:ai:g -->               G 估值 + 符号说明
<!-- trace:user:g-self -->        我的 G 自评（用户区，保护）
<!-- trace:user:journal -->       这里更接近真实的我（用户区，保护）
<!-- trace:user:feedback -->      给 AI 的反馈（用户区，保护）
<!-- trace:ai:narrative -->       我今天经历了什么
<!-- trace:verbatim -->           原版本（逐字，引擎写入后锁定 hash）
```

引擎在每次写入前后比对所有 `user:*` 和 `verbatim` 块的 hash，任何改动都让写入失败。

## 4. 每日流程（v1）

时间都从 `trace.toml` 读，下面是默认值：

1. **09:00 calibrate**：读最近 14 天日报和反馈区，生成有来源、去重的真实疑问，写进当天补记页的「AI 想问你」区。没有疑问就写一句「目前没有新的重要疑问」。
2. **19:30 daily**：读补记页 → Agent 产出 PAD 时点、XP 维度等级、叙事 → 引擎校验并写正式日报 → 建次日补记页。
3. **20:30 g**：Agent 给 B1–B4、E1–E4 打分（带证据）→ 引擎补值、计算、冻结预测、出图。
4. **每次运行开头**：解析所有反馈区的新内容 → 写 `feedback.jsonl` → Agent 作决定 → `rules.py` 按范围和时间替代旧规则 → 在日报里写「我做了什么决定、为什么」，邀请用户反馈。

v1 不做 03:00 默认通过：没有反馈的决定保持当前状态，用户随时可以推翻。

## 5. 隐私与安全

- 仓库里不得出现：真实姓名、邮箱、Notion ID、绝对路径、日记内容。用一个自动测试扫描（邮箱、32 位十六进制 ID、`/Users/` 路径）。
- 示例数据只用虚构人物。
- 文档写明：G 和 PAD 不是临床量表，不做医学判断；情绪低落时系统只做陪伴性描述，不给目标数字。

## 6. 还没定、需要 Tiger 决定的

1. 正式项目名和开源协议（MIT / Apache-2.0 / GPL）。
2. Tiger 自己要不要迁到这个新系统上？要的话需要一个从现有 Notion 和容器导入的工具，放 v1 还是 v2？
3. 对话来源（ChatGPT、Claude、Google AI Mode）的读取：隐私负担很重，v1 是否先只读用户日记？
4. XP 默认预设里的六个维度是从 Tiger 9/20 的难题里长出来的，要不要原样作为默认值，还是给一个更通用的版本？
5. 默认要不要附带 125 词候选层（v0.2），还是只放 27 词表？
