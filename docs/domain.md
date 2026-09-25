# 领域约定

维护跨语种戏剧文本、人物授权和场次采用版本的基础事件契约。

聚合对象包括`play_script`、`performance_run`、`consent_record`、`release_version`。事件类型包括`SCRIPT_REGISTERED`、`CONSENT_RECORDED`、`VERSION_APPROVED`、`RUN_LOCKED`、`ERRATA_ISSUED`。所有发生时间都必须携带时区，版本号从 1 开始递增，基础校验不会改写调用方输入。

## 事件载荷

- `CONSENT_RECORDED`：载荷还需包含 `subject_ref`, `scope`。
- `RUN_LOCKED`：载荷还需包含 `script_version`, `starts_at`。
- `ERRATA_ISSUED`：载荷还需包含 `supersedes`, `reason`。

相同事件标识的业务幂等、冲突隔离和状态推进由上层服务负责；本仓库只定义可稳定交换的基础事实。
