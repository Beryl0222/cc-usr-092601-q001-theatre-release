# 领域约定

维护跨语种戏剧文本、人物授权和场次采用版本的基础事件契约，以及建立在契约之上的演出版本服务。

聚合对象包括 `play_script`、`source_segment`、`consent_record`、`translation_candidate`、`cultural_note`、`release_version`、`performance_run`、`live_change`、`dispute_case`。所有发生时间都必须携带时区，事件版本号按聚合从 1 开始递增，基础校验不会改写调用方输入。

## 业务规则

- 剧目阶段沿 `draft -> rehearsal -> resident -> archived` 单向推进；只有驻演（resident）剧目可以排期。
- 亲历者只能确认涉及自己的事实（`witness` 必须在段落的 `involves` 中）。
- 人物原型授权携带撤回期限（`withdraw_by`），逾期不可撤回；未获授权或已撤回的口述段落对观众整段隐藏。
- 版本会签由译者、导演、发布人员三方完成：提交人不得终审自己的版本，同一人员不得兼任多个会签角色。三方签齐后自动登记 `VERSION_APPROVED`；会签中途重启服务可从日志恢复继续。
- 版本须经排练确认后方可被场次锁定；锁定要求版本提交时间不晚于场次截稿时间、当前时间早于开演时间。
- 场次锁定即冻结实际采用的版本编号与整场文本指纹；临场变更逐条登记并同步刷新指纹。
- 迟到修订（`RUN_REVISED`）只影响尚未开演的已锁定场次；已经演出的场次只能通过勘误（`ERRATA_ISSUED`）沿版本链关联更正。
- 场次回执按 `receipt_id` 幂等；回执编号与锁定编号相同而文本指纹不同时登记 `DISPUTE_RAISED`，场次进入争议。
- 截稿、开演、撤回期限全部经由注入时钟判定，测试与演练可使用 `ManualClock`。

## 事件载荷

- `STAGE_ADVANCED`：`to_stage`。
- `SEGMENT_ADDED`：`script_ref`, `order`, `text`, `kind`（`dialogue` / `narration` / `oral_account`），可带 `involves`。
- `FACT_CONFIRMED`：`segment_ref`, `witness`。
- `CONSENT_RECORDED`：`subject_ref`, `scope`（含 `script_ref` 与 `segments`，`*` 表示全剧），`withdraw_by`。
- `CONSENT_WITHDRAWN`：`reason`。
- `TRANSLATION_SUBMITTED`：`segment_ref`, `language`, `text`, `translator`, `fingerprint`。
- `NOTE_ADDED`：`segment_ref`, `language`, `text`。
- `VERSION_SUBMITTED`：`script_ref`, `number`, `items`, `submitted_by`。
- `VERSION_SIGNED`：`role`, `signer`。
- `VERSION_APPROVED`：`number`。
- `REHEARSAL_CONFIRMED`：`confirmer`。
- `RUN_SCHEDULED`：`script_ref`, `starts_at`, `submission_cutoff`。
- `LANGUAGE_REQUESTED`：`language`。
- `RUN_LOCKED`：`script_version`, `starts_at`, `version_ref`, `fingerprint`。
- `RUN_REVISED`：`version_ref`, `script_version`, `fingerprint`。
- `LIVE_CHANGE_RECORDED`：`run_ref`, `segment_ref`, `language`, `text`, `reason`, `fingerprint`。
- `RECEIPT_RECORDED`：`receipt_id`, `version_number`, `fingerprint`, `outcome`。
- `DISPUTE_RAISED`：`run_ref`, `receipt_id`, `expected_fingerprint`, `actual_fingerprint`。
- `ERRATA_ISSUED`：`supersedes`, `reason`, `run_ref`, `errata_id`。

相同事件标识的业务幂等由服务层保证：命令携带已处理的 `event_id` 时直接返回当前状态，不重复落事件。
