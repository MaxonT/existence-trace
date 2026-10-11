# tiger-method preset

`tiger-method` uses the same PAD 27-word table and the same G model (g-1.4.0,
weights B .28 .18 .36 .18 / E .22 .14 .36 .28) as `default`. The difference is
XP (version 2.2): its six dimensions are named after real, recurring
difficulties instead of generic virtues. It also ships two extra starter rules
(a PAD reference for focus time, and "decide when no number is given").

No personal events, ledger entries or journal content ship with this preset.
`xp.json` has `booked_events: []` and the validator rejects anything else.

## How the dimensions were grown

Each dimension started as a problem that kept showing up in the journal, not as
a goal picked in advance:

| Dimension (cap) | The difficulty it grew from |
|---|---|
| Keeping today's schedule steady (18) | Days kept dissolving; holding *today* together mattered more than any long plan. |
| Truly absorbing and learning (16) | Lots of reading and watching, little that stuck. Only absorption counts, not exposure. |
| Stopping over-optimizing, holding boundaries (16) | Endless tuning of systems and plans. Stopping on purpose, and resting, earn points. |
| Acting under uncertainty (14) | Waiting for certainty before moving. Acting while unsure is the skill. |
| Personal discovery, knowing and doing as one (14) | Insights that never changed behaviour. Points need the insight *and* the act. |
| Real connection and self-directed exploration (12) | Contact that was polite but empty, curiosity that only followed obligations. |

The caps add up to 90 and reflect what hurt most, not what is most admirable.
Ordinary achievements add 0-10; a major event is uncapped but booked exactly
once, with a unique `event_id`, on the day it happened.

## Growing your own

1. Journal with `default` for two or three weeks.
2. Read back your notes and the questions the system asked. List the
   difficulties that came back at least three times.
3. Name each one as the behaviour you want to see, in your own words
   ("acting under uncertainty", not "courage").
4. Give the most painful ones the higher caps; keep six dimensions with caps
   18, 16, 16, 14, 14, 12 so totals stay comparable.
5. Copy `etrace/preset_data/default` to a new folder, edit the labels (zh and en) in
   `xp.json`, and load it with `etrace.presets.load(name, presets_dir)`; the
   validator tells you which field is wrong.
6. Re-check after a month. Let a dimension go when the difficulty is gone.

PAD and G are not clinical scales and make no medical judgement.

---

# tiger-method 预设（中文）

与 `default` 共用 PAD 27 词表和 G 模型（g-1.4.0）。不同在 XP 2.2：六个维度以反复出现的真实难题命名，
而不是通用美德；另外多两条起步规则（专注时段的 PAD 参考、没有数字时 AI 先代理决定）。
不含任何个人事件或日记内容。

**维度怎样长出来**：日程与稳定今天（日子总是散掉）、真正吸收与学习（看得多、留下的少）、
停下优化与边界（无止境地调系统）、在不确定中行动（总等确定了才动）、个人发现与知行整合（想明白却没改变行为）、
真实联结与自主探索（客气但空洞的交流、只跟着义务走的好奇）。封顶分数反映哪里最痛，而不是哪里最体面。

**长出你自己的**：先用 `default` 写两三周 → 找出至少出现三次的难题 → 用你自己的话写成想看到的行为 →
最痛的给最高上限（保持 18、16、16、14、14、12）→ 复制 `etrace/preset_data/default` 改 `xp.json` 的中英文名称 →
一个月后复查，难题消失就让维度退场。

PAD 和 G 不是临床量表，不做医学判断。
