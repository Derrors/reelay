# 参与贡献

感谢愿意改进 Reelay！提交前请花几分钟读完本页。

## 开发环境

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
pre-commit install            # 提交前自动跑 ruff、空白检查、前端 typecheck/eslint
cd web && npm ci
```

运行与检查：

```bash
python -m reelay.main --dry-run               # 无需账号的端到端冒烟
python -m unittest discover -s tests          # 后端测试
ruff check .
cd web && npm run typecheck && npm run lint:eslint && npm test && npm run build
```

CI 会在每个 PR 上运行以上全部检查并构建 Docker 镜像。

## 提交规范

- 提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/) 风格：`feat:`、`fix:`、`docs:`、`refactor:`、`test:`、`chore:`、`ci:`，可加范围如 `fix(115):`。
- 一个 PR 只做一件事；行为变化请附测试，用户可见的变化请更新 `docs/` 与 `CHANGELOG.md`。
- 代码注释与文档使用中文，风格与周边代码保持一致。

## 必须遵守的约束

- **不要提交任何真实凭证或个人数据**：`.env`、`config.yaml`、`data/`、session 文件、cookie、真实目录 CID。日志和截图里的这些信息也要打码。
- **不要在代码、测试、文档、Issue 中出现具体的资源频道、Bot 或站点名称**，统一使用 `@example_channel` 这类占位符。
- 涉及 115 写操作（转存、改名、移动、删除）的改动必须保持"先持久化检查点、再调用远端、事后读回核验"的顺序，结果不明时只能核验不能重发，并补充故障注入测试（参考 `tests/test_transfer.py`）。
- 测试不得访问网络：使用 `tests/test_transfer.py` 中的内存网盘、`FakeAI` 等替身。

## 写插件

新能力优先做成插件而不是改内核，见 [插件开发指南](docs/plugins.md)。通用性强的插件欢迎以 PR 形式加入 `reelay/plugins/` 或 `examples/plugins/`。

## 报告问题

- Bug：请附版本、运行方式（Docker / 本地）、复现步骤、相关的任务轨迹或日志（**先去掉凭证、链接与频道名**）。
- 安全问题请不要公开提 Issue，按 [SECURITY.md](SECURITY.md) 私下报告。
