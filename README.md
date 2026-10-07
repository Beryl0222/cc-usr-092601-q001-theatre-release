# 跨语种戏剧演出版本台

维护跨语种戏剧文本、人物授权和场次采用版本的基础事件契约，并在此之上提供演出版本服务：会签、场次锁定、临场变更、幂等回执、争议、勘误与面向观众的多语种内容接口。

## 目录

- `contracts/domain.schema.json`：对象、事件和载荷字段约定。
- `data/sample.json`：可直接校验的联调样例。
- `src/theatre_release/`：契约校验、服务核心、HTTP API、审计与命令行入口。
- `tests/`：信封、时间、版本、事件载荷与服务行为测试。
- `docs/domain.md`：领域对象、事件语义与业务规则。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```

## 样例校验

```bash
PYTHONPATH=src python3 -m theatre_release.cli contracts/domain.schema.json data/sample.json
```

样例有效时输出 `valid`；发现问题时逐行给出字段、代码和中文说明，并返回非零状态。

## 演示数据与 HTTP API

```bash
PYTHONPATH=src python3 -m theatre_release.demo /tmp/theatre-store
PYTHONPATH=src python3 -m theatre_release.api /tmp/theatre-store --port 8000
curl 'http://127.0.0.1:8000/tonight?lang=en'
curl 'http://127.0.0.1:8000/runs/run-tonight/content?lang=en'
```

接口按观众语言给出当晚有效内容；未获授权或授权已撤回的口述段落整段隐藏。

## 审计

```bash
PYTHONPATH=src python3 -m theatre_release.audit /tmp/theatre-store tr-1-en
```

从一句译文还原原文段落、版本批准链（提交人、三方会签、排练确认）、适用场次和历次勘误更正，输出 JSON。
