# existence-trace（存在痕迹）

[English](README.md)

**一个会主动找你要反馈、每天迭代、越用越像你的个人状态系统。**

你每天写日记。AI 代理用三个视角去理解你的一天：PAD（情绪）、G（安全感与希望）、XP（成长），
写成日报。只用 Python 3 标准库，MIT 协议。

## 会自我迭代的偏好闭环

日报不是核心，**偏好容器**（`etrace/container`）才是。系统发现自己哪里没把握，就来问你；
你回答后，它把答案变成规则，在下一份真实日报里用上，并且每周审计自己有没有真的用上。

```
 发现疑问 ──> 问你 ──> 你的反馈 ──> 做决定 ──> 改规则
    ^                                          │
    │                                          v
 每周审计 <── 使用回执 <── 读回核验 <── 用进真实笔记
```

1. **疑问**：代理扫描最近的笔记，把真实的疑问连同证据和两个候选解释存下来（`question add`），
   写进当天笔记；配置了邮件的话也会发信。
2. **反馈**：你在笔记受保护的反馈区写字，或者直接回邮件。以 `[Q-id]` 开头就是回答某个疑问
   （`feedback ingest`）。
3. **决定**：代理不等精确数字，先做合理决定并写明理由，再改规则（`decide`）。同一适用范围内
   最新反馈优先；被替代的规则退出生效集合，但历史保留。下一份笔记会出现决定报告，你随时可以推翻。
4. **回执**：决定真的改变了日报时，代理读回文件核验，再记一条使用回执（`usage add`）。
   读过规则不算用过。
5. **审计**：每周 `audit weekly` 沿着每条链（疑问 → 反馈 → 决定 → 规则 → 回执）检查，
   有决定从没落实就判失败。

`validate` 随时检查整条链，有任何问题就以退出码 1 结束。

## 九条原则

引擎用代码和测试强制执行；prompt 要求代理永远不要绕过。

1. **原文保护**：你的原话逐字保存。AI 只写自己的区块；原版本只能在末尾追加带日期的补写。
2. **AI 估计不冒充自述**：每个数值都带来源类型（self_report / model_inference / gap_fill）、
   出处和置信度。
3. **缺失不是 0**：没写就留空或标「待评分」。补值必须低置信度，且不进入校准。
4. **事件只入账一次**：重大事件用唯一 event id，在发生日记一次。
5. **冻结预测再训练**：先保存预测快照，反馈到了再评价；数值修正要过 10 日训练 + 10 日检验。
6. **最新反馈优先**：同一适用范围内，按你原始表达的时间，新规则替代旧规则；旧反馈永远保留为证据。
7. **先决定、再报告、邀请反馈**：先做有理由的合理决定，不干等；你可以推翻。
8. **写后核验，读过不算用过**：只有真实产物里核验过的改动才算「已应用」。
9. **手填是参考，不是命令**：你的自评原值永远保留并重点参考，AI 结合证据独立判断，
   并说明采纳或偏离的理由。

## 快速开始

需要 Python 3.11 以上（用到 `tomllib`）。无需安装，在本仓库的克隆目录里运行。

```sh
python3 -m etrace init --preset default --language zh --dir ~/existence-trace-data
export ETRACE_CONFIG=~/existence-trace-data/trace.toml

python3 -m etrace validate           # {"ok": true, ...}
python3 -m etrace prompt daily --raw # 代理跑日报时用的 prompt
python3 -m etrace schedule --target claude-code --out ./my-schedule  # 只写文件，不安装
```

`init` 会生成 `trace.toml`、带今天笔记的 `journal/`、装好预设起步规则的 `data/`，以及
`audits/`、`charts/`。在今天的笔记里写字，然后让代理按时运行任务：

| 默认时间 | 任务 | 做什么 |
|---|---|---|
| 09:00 | `calibrate` | 扫描最近的笔记、提出真实疑问、发当日状态 |
| 12:30、20:30 | `feedback` | 收反馈、做决定、改规则、发决定报告 |
| 19:30 | `daily` | 当天的 PAD、XP、主线和叙事，并写回执 |
| 20:30 | `g` | G 估值、冻结预测、校准 |
| 周日 22:15 | `audit` | 每周审计每条反馈链 |

每个任务都会把数据目录提交到它自己的本地 git 仓库（`checkpoint`）。时间、语言、外联方式
（`note` 或 `note+email`）和可选的 Notion 镜像都在 `trace.toml` 里设置，全部键见
[`examples/trace.toml`](examples/trace.toml)。

**预设**：`default` 是通用版。`tiger-method` 是一个人调出来的维度，不含任何个人事件；
它的 [README](presets/tiger-method/README.md) 讲怎样长出你自己的维度。

**示例**：[`examples/persona`](examples/persona) 是一位虚构面包师的三天日记和代理产出；
`tests/test_example.py` 用它跑完整个闭环。

## 目录

```
etrace/container/  偏好容器：questions、feedback、decisions、rules、usage、validate、audit
etrace/models/     pad.py  g.py  xp.py
etrace/outreach/   note.py  email.py（smtplib / imaplib）
etrace/adapters/   调度（codex / claude-code / cron）、notion（可选输出）
etrace/prompts/    zh/ 与 en/ 任务 prompt，共用 _principles.md
presets/           default、tiger-method
```

测试：`python3 -m unittest discover -s tests -v`。只要仓库里出现邮箱、Notion 风格的 ID
或用户主目录路径，`tests/test_privacy.py` 就会失败。

## 不是临床工具

PAD、G、XP 是个人反思辅助，**不是临床量表**，不做任何医学或心理学判断，也不能替代专业帮助。
如果你正在经历困难，请联系合格的专业人士或当地危机热线。

## 隐私

你的数据只在你自己的数据目录里，不在本仓库。邮件只带回答问题所需的最少上下文，不复制日记全文。
凭据从 `trace.toml` 里指定的文件读取，不写进配置、日志或 prompt。
