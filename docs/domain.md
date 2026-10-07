# 领域约定

维护跨语种戏剧文本、人物授权、三会签终审与场次采用版本的事件契约与上层服务。

## 聚合与事件

| 聚合 | 事件 |
| --- | --- |
| `play_script` | `SCRIPT_REGISTERED` |
| `consent_record` | `CONSENT_RECORDED`、`CONSENT_WITHDRAWN` |
| `release_version` | `VERSION_PROPOSED`、`VERSION_SIGNED`、`REHEARSAL_CONFIRMED`、`VERSION_APPROVED`、`ERRATA_ISSUED` |
| `performance_run` | `RUN_PLANNED`、`RUN_LOCKED`、`RUN_ADJUSTED`、`RUN_RECEIPTED` |
| `dispute` | `DISPUTE_OPENED` |

所有发生时间必须携带时区；同一聚合 `version` 从 1 严格递增；基础契约校验不改写调用方输入。

## 业务规则

### 人物原型授权

- 授权只能由亲历者本人登记与撤回，授权范围显式列出 `script_id` 与 `segment_ids`。
- 可声明 `withdrawal_deadline`；越过该期限的撤回被拒绝。
- 译文版本终审时，每条 `testimony` 段落都必须存在有效授权；撤回授权后新版本无法终审，
  对外内容接口也会隐藏未授权口述（条目中列入 `hidden`，不返回译文与原文）。

### 亲历者排练确认

- 亲历者只能确认涉及本人的口述段落，且该段落必须已被译文版本收录。
- 确认可分批累积；进程重启后重放事件继续未完成会签，已确认段落不丢。

### 职责分离的三会签与终审

- 终审前需要 `translator`（译者）、`director`（导演）、`publisher`（发布）三个角色会签。
- 同一自然人不能占据两个角色；终审只能由完成发布会签的人执行。
- **提交人（提案者）不得是终审人**，因此译者、导演、发布任何一方都无法独自完成
  "提交 + 终审"。

### 场次、截稿、开演

- `RUN_PLANNED` 声明开演时间、截稿时间（不得晚于开演）与观众语言需求。
- 截稿前 `RUN_LOCKED` 锁定每个语种实际采用的译文版本；锁场后到开演前只允许
  `RUN_ADJUSTED` 临场变更（换用已终审版本、临场遮盖段落），开演时刻通道关闭。
- 开演后对外内容冻结为开演那一刻的勘误链头，之后的迟到修订不改写当晚。

### 勘误与已演出现场

- `ERRATA_ISSUED` 用一个已终审新版本勘误旧版本，要求同一剧目、同一目标语种。
- 勘误只影响**尚未开演**的场次：未开演场次自动跟随勘误链最新头；已经演出的场次
  保留当时锁定的文本，审计时通过勘误链（`supersedes` / `superseded_by`）关联还原。
- 已被勘误的版本不能再被新场次锁场或临场换用。

### 回执幂等与争议

- 开演后提交 `RUN_RECEIPTED`，携带回执编号与当晚实际内容指纹。
- 编号相同且指纹相同：幂等返回，不新增事件。
- 编号相同但指纹不同（无论与既有回执还是与锁场快照比对）：落 `DISPUTE_OPENED`，
  返回争议编号；争议需人工裁定，此后该编号不再自动接受任何回执。
- 触发争议的请求用同一 `event_id` 重传时仍返回同一个争议，不产生重复事件。

### 时钟与重启

- 服务只依赖 `Clock.now()`：生产用系统时钟（东八区显式时区），测试与截稿推演用
  `SettableClock` 拨钟；HTTP 实例仅在显式开启时提供 `/admin/clock`。
- 存储为只追加 JSONL；`event_id` 全局幂等，相同编号不同内容直接报冲突。
  重启后重放全部事件重建状态，未完成的会签可继续签署。

## HTTP 摘要

- `GET /runs/{id}/tonight?language=xx`：按观众语言返回当晚有效内容，隐藏未授权口述。
- 写接口均为 POST，JSON 体携带 `actor_id` 与幂等 `event_id`。
- `GET /audit?text=...` 或 `GET /audit?version_id=...&segment_id=...`：从一句译文
  还原原文段落与原文指纹、三会签批准链、亲历者确认、适用场次与历次勘误链。
- 错误码：403 无权、404 不存在、409 冲突/争议、422 状态或期限不允许。
