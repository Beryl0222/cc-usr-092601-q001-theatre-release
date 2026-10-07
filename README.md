# 跨语种戏剧演出版本台

为戏剧村落常态化接待海外观众提供跨语种演出版本服务：剧目段落、人物原型授权、
译文候选与三会签、排练确认、场次计划/锁场/临场变更、演出回执、勘误链与争议，
全部保存为可追溯的只追加事件。

## 核心约束

- **亲历者范围**：授权与排练确认只能由本人对涉及本人的口述段落作出；未授权口述
  在对外接口中隐藏。
- **职责分离**：译者、导演、发布三方会签齐备且为三个不同的人；终审由发布会签人
  完成，且提交人不得终审——任何一方都不能独自完成提交与终审。
- **截稿与开演**：可控时钟判定截稿锁场、开演前临场变更窗口、授权撤回期限。
- **迟到修订**：勘误只影响尚未开演的场次；已演出的版本冻结，通过勘误链关联保留。
- **回执幂等**：编号+指纹一致幂等；编号相同指纹不同立争议、等待人工裁定。
- **可重启**：JSONL 事件日志重放后继续未完成会签；`event_id` 全局幂等。

## 目录

- `contracts/domain.schema.json`：聚合、事件和载荷字段约定。
- `data/sample.json`：可直接校验的联调样例。
- `src/theatre_release/`
  - `contracts.py` 事件信封契约校验
  - `clock.py` 系统/可控时钟
  - `fingerprint.py` 规范化与内容指纹
  - `store.py` 只追加 JSONL 存储（event_id 幂等、版本冲突检测）
  - `state.py` 事件重放与读模型
  - `service.py` 领域服务（授权、会签、截稿、勘误、回执、审计）
  - `httpapi.py` 标准库 HTTP API
  - `cli.py` `validate` / `serve` / `audit` 子命令
- `tests/`：契约、服务规则（含重启）、HTTP 端到端、CLI 测试。
- `docs/domain.md`：领域对象、事件语义与业务规则。

## 快速开始

```bash
# 校验事件样例
PYTHONPATH=src python3 -m theatre_release.cli validate contracts/domain.schema.json data/sample.json

# 启动服务（生产用系统时钟）
PYTHONPATH=src python3 -m theatre_release.cli serve --store data/events.jsonl --clock system

# 审计：从一句译文还原原文、批准链、适用场次和历次更正
PYTHONPATH=src python3 -m theatre_release.cli audit \
  --store data/events.jsonl --clock system \
  --text "I moved down from Xihaigu in 1997."
```

## HTTP 接口摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/scripts` | 注册剧目与原文段落 |
| POST | `/consents`、`/consents/{id}/withdraw` | 登记/撤回人物授权（本人） |
| POST | `/versions` | 提交译文候选（含原文/译文指纹） |
| POST | `/versions/{id}/sign` | 译者/导演/发布会签 |
| POST | `/versions/{id}/confirm-rehearsal` | 亲历者确认本人事实 |
| POST | `/versions/{id}/approve` | 终审（发布人、非提交人） |
| POST | `/errata` | 对旧版本发出已终审勘误 |
| POST | `/runs`、`/runs/{id}/lock`、`/runs/{id}/adjust` | 排场、截稿锁场、临场变更 |
| GET | `/runs/{id}/tonight?language=en` | 当晚有效内容（隐藏未授权口述） |
| POST | `/runs/{id}/receipts` | 开演后回执（幂等/争议） |
| GET | `/audit?text=...` | 译文全链路还原 |
| GET | `/disputes` | 争议列表 |

写接口的 JSON 体均需携带操作者 `actor_id` 与幂等 `event_id`。

## 测试

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src tests
```
