# existence-trace 系统设计 v0.2（草案，待 Tiger 审）

> 工作名 `existence-trace`（存在痕迹），正式名待定。Python 包名 `etrace`。
> v0.2 变化：偏好容器升为核心模块；每周落实审计进 v1；邮件外联进 v1；预设两套都开箱可用。

## 1. 一句话

**一个会自己找你要反馈、每天迭代自己的个人状态系统。**

你每天写日记。系统用 PAD（情绪）、G（安全感与希望）、XP（成长）去理解你的一天，写成日报。
更重要的是：它会发现自己哪里没把握，主动来问你；你回答后它自己做决定、改规则、真的用上，
并且每周审计自己有没有真的用上。规则越用越贴近你。

### 已确认的产品决定（2026-10-07）

| # | 决定 |
|---|---|
| 1 | v1 范围：偏好容器（核心）+ 每日日报 + PAD + G + XP + 每周落实审计。周报、成就页放 v2 |
| 2 | 平台无关：核心是 Python 引擎 + 配置，调度层适配 Codex、Claude Code、cron |
| 3 | 存储：本地 Markdown/JSON 优先，Notion 作为可选输出适配器 |
| 4 | 外联渠道：默认写进笔记；v1 同时提供邮件插件（SMTP 发、IMAP 收），配置后主动发信、读回复 |
| 5 | 预设两套，都开箱可用：`default`（通用）和 `tiger-method`（Tiger 的 PAD / G / XP 2.2，去掉个人事件） |
| 6 | 语言：代码与 README 英文；prompt、词表、模板中英双语 |
| 7 | 125 词候选层放 v2；v1 用 27 词表 |
| 8 | 被替代的旧规则退出生效集合，但在 decisions 里保留「谁替代了谁、为什么」 |
| 9 | 自我来信放 v2 可选模块；GitHub 空提交 streak 不进入开源产品 |
| 10 | v1 只读用户自己写的日记；AI 对话记录（ChatGPT、Claude、Google AI Mode）放 v2 做可选插件 |
| 11 | 开源协议 MIT；正式名不急，暂用 existence-trace |

## 2. 原则

### 2.2 原则（产品的灵魂，全部写成代码和测试）

1. **原文保护**：用户原话逐字保存（原版本），AI 只能在授权区域做最小整理（阅读版）。
2. **AI 估计不冒充自述**：每个数值都带 `provenance`（self_report / model_inference / gap_fill）、来源和置信度。
3. **缺失不是 0**：没写就留空或标「待评分」；补值必须低置信度，且不进入校准和准确率统计。
4. **事件只入账一次**：重大事件用唯一 `event_id`，在发生日记一次。
5. **冻结预测再训练**：先保存预测快照，反馈到了再评价；数值修正要过 10 日训练 + 10 日检验门槛。
6. **最新反馈优先**：同一适用范围内，按用户原始表达时间，新规则替代旧规则；原始反馈永远保留为证据。
7. **先决定、再报告、邀请反馈**：AI 不等精确数字，先做合理决定并写明理由，用户可以推翻。
8. **写后核验，读过不算用过**：每次写入后读回检查；只有真实产物里用上了才记「已应用」。
9. **手填是参考，不是命令**：用户自评原值永远保留并重点参考，AI 结合证据独立决定，并说明采纳或偏离的理由。


## 3. 核心：偏好容器（Preference Container）

整个系统围绕它转。PAD、G、XP 只是被它校准的对象。

### 3.1 闭环

```
 ① 发现疑问 ──▶ ② 主动外联 ──▶ ③ 收到反馈 ──▶ ④ 作决定
     ▲            笔记 / 邮件      逐字入库        改规则（按范围+时间替代）
     │                                                │
 ⑦ 每周落实审计 ◀── ⑥ 使用回执 ◀── ⑤ 真实应用到日报 / G / XP / PAD
   读过不算用过     changed / confirmed / no_match / not_applicable / blocked
```

### 3.2 七个部件

| 部件 | 文件 | 要点 |
|---|---|---|
| ① 疑问 | `questions.jsonl` | 每题必须有：来源、日期、用户原话或可核验行为、两种可能评估、具体不确定点、会改变的维度、`dedupe_key`。没有真实疑问不凑题；旧题最多温和跟进一次 |
| ② 外联 | `notifications.jsonl` | 默认写进当天笔记「AI 想问你」区；配了邮件就同时发信。每天一次状态：没疑问也说「目前没有新的重要疑问」；扫描失败就说无法判断，绝不冒称没有疑问。发送核验成功才记 `sent` |
| ③ 反馈 | `feedback.jsonl` | 来自笔记反馈区或邮件回复，逐字保存原文、收到时间、指向的发生日、范围。按「来源 ID + 问题 ID」去重；引用的旧邮件和签名不算新反馈 |
| ④ 决定与规则 | `decisions.jsonl`、`rules.json` | AI 根据反馈作具体决定，标明是用户数字还是 AI 代理决定；同范围按原始表达时间替代旧规则；旧规则退出生效集合，替代记录保留 |
| ⑤ 应用 | 日报 / G / XP / PAD 产物 | 各任务运行前读取生效规则 |
| ⑥ 回执 | `usage.jsonl` | 写回产物并读回核验后才记：规则版本、feedback_id / decision_id、原判断、新判断、目标、状态 |
| ⑦ 审计 | `audits/YYYY-Www.md` | 每周检查最近 7 天及历史未闭环链条：提问 → 回复 → 决定 → 规则 → 回执；抽查一个已应用项和一个不适用项。结构完整不等于通过 |

### 3.3 决定报告

每次有实质决定，就在笔记里（以及邮件里，如果配置了）写一段简短报告：
先一两句话说结果，再逐项写「我决定了什么、为什么、参考了你的哪句话」，最后邀请：「哪些你赞成、哪些不赞成？」
用户随时可以推翻；v1 不做 03:00 默认通过，没反馈的决定保持现状。

### 3.4 版本追踪（git）

`etrace checkpoint` 在每次任务前后把用户数据目录提交到**本地私有** git 仓库，没有变化就不提交。
默认不推送；用户可以自己配置私有远端。

## 4. 整体架构

```
           ┌──────────── 调度层（adapters/scheduler）────────────┐
           │ Codex automation / Claude Code / cron              │
           └──────────────┬─────────────────────────────────────┘
                          │ calibrate / feedback / daily / g / audit
┌──────────────┐   ┌──────▼──────────┐   ┌──────────────────────┐
│ 用户笔记 / 邮件 │──▶│ Agent（LLM）     │──▶│ etrace 引擎            │
│ 原文区/反馈区   │   │ 读证据、做判断     │   │ 校验、计算、去重、替代   │
│               │◀──│ 输出结构化 JSON    │◀──│ 确定性、可测试          │
└──────────────┘   └─────────────────┘   └──────────┬───────────┘
                                                   ▼
                      偏好容器 data/*.jsonl + rules.json ── 本地 git
                      journal 日报 .md + SVG ── Notion 适配器（可选）
```

**分工**：需要判断的（读日记、提真实疑问、给 PAD 档位和 G 细项打分、作决定、写叙事）交给 Agent；
可以算的（加权、补值、平滑、去重、规则替代、保护块校验、回执校验、审计链条）交给引擎。
引擎拒绝不合格的 JSON（缺 provenance、缺来源、问题缺 dedupe_key 等）。

### 4.1 目录

```
etrace/                Python 包，只用标准库
  config.py  store.py  journal.py
  container/           rules.py  feedback.py  questions.py  decisions.py  usage.py  audit.py  validate.py
  models/              pad.py  g.py  xp.py
  chart.py
  outreach/            note.py  email.py（smtplib / imaplib）
  checkpoint.py        本地 git
  prompts/{zh,en}/     _principles.md  calibrate.md  feedback.md  daily.md  g.md  audit.md
  adapters/            scheduler: codex / claude_code / cron；output: notion
  cli.py
presets/
  default/             通用：pad.json  g.json  xp.json  rules.json  question_templates.json
  tiger-method/        Tiger 的 PAD / G 1.3.0 / XP 2.2（无个人事件），同样五个文件
tests/
examples/              虚构人物的 3 天日记与期望输出
```

### 4.2 两套预设

两套都必须完整：`etrace init --preset NAME` 之后什么都不改，第一天就能跑完整个闭环。

| | `default` | `tiger-method` |
|---|---|---|
| PAD | 五档 + 27 词表 | 同左 |
| G | B1–B4 / E1–E4，权重 .28 .18 .36 .18 / .22 .14 .36 .28 | 同左（g-1.3.0） |
| XP 六维（合计 90） | 节律与稳定 18、学习与吸收 16、边界与休息 16、行动与尝试 14、反思与发现 14、联结与探索 12 | 日程与稳定今天 18、真正吸收与学习 16、停下优化与边界 16、在不确定中行动 14、个人发现与知行整合 14、真实联结与自主探索 12 |
| XP 其他 | 普通成果 0–10；事件加分不封顶、唯一 ID | 同左 |
| 起步规则 | 原则 1–9 对应的基础规则 | 同左，加上 Tiger 已确认的通用偏好（如专注状态 PAD 参考） |
| 疑问模板 | 局部情绪与整日基调冲突、手填与证据不一致等 | 同左 |

`tiger-method` 的 README 还要讲清「这些维度是怎样从自己的难题里长出来的」，教用户长出自己的维度。

### 4.3 用户数据目录（不在仓库里）

```
~/existence-trace-data/
  trace.toml           时区、预设、外联方式、邮件和 Notion 凭据文件路径
  profile/             用户自己的偏好摘要、语言样本（可选）
  journal/2026-10-07.md
  data/  questions.jsonl  notifications.jsonl  feedback.jsonl  decisions.jsonl  rules.json  usage.jsonl
         predictions.jsonl  xp.jsonl
  audits/  charts/
  .git/                本地版本追踪
```

### 4.4 日报文件结构（Markdown）

```
<!-- trace:ai:headline -->        今日主线
<!-- trace:ai:questions -->       AI 想问你（疑问 + 决定报告）
<!-- trace:ai:pad -->             PAD 图 + 时点表
<!-- trace:user:pad-self -->      我来填写 PAD（保护）
<!-- trace:ai:xp -->              XP
<!-- trace:ai:g -->               G 估值 + 符号说明
<!-- trace:user:g-self -->        我的 G 自评（保护）
<!-- trace:user:journal -->       这里更接近真实的我（保护）
<!-- trace:user:feedback -->      给 AI 的反馈（保护）
<!-- trace:ai:narrative -->       我今天经历了什么
<!-- trace:verbatim -->           原版本（逐字，写入后锁定 hash）
```

引擎在每次写入前后比对所有 `user:*` 和 `verbatim` 块的 hash，任何改动都让写入失败。

## 5. 每日与每周流程（默认时间，可在 trace.toml 改）

| 时间 | 任务 | 做什么 |
|---|---|---|
| 09:00 | `calibrate` | 处理新反馈 → 扫描最近 14 天 → 生成真实疑问 → 外联（笔记 + 邮件）→ 发当日状态 |
| 12:30、20:30 | `feedback` | 收邮件回复和笔记反馈 → 入库 → 决定 → 改规则 → 决定报告；没新事就安静 |
| 19:30 | `daily` | 读生效规则 → 正式化当天日报（PAD、XP、叙事）→ 写回执 → 建次日补记页 |
| 20:30 | `g` | 读生效规则 → G 细项打分 → 补值、计算、冻结预测、出图 → 写回执 |
| 周日 22:15 | `audit` | 每周落实审计，出报告；有漏用或需要用户判断才外联 |
| 每个任务前后 | `checkpoint` | 本地 git 提交 |

## 6. 隐私与安全

- 仓库里不得出现：真实姓名、邮箱、Notion ID、绝对路径、日记内容。由自动测试扫描。
- 邮件只带回答问题所需的最少上下文，不复制日记全文；凭据从文件读取，不写进配置或日志。
- 示例数据只用虚构人物。
- 文档写明：G 和 PAD 不是临床量表，不做医学判断。

## 7. 还没定、需要 Tiger 决定的

1. Tiger 自己要不要迁到这个新系统上？要的话需要从现有 Notion 和容器导入的工具（建议 v1 先不迁）。
2. 正式项目名。
