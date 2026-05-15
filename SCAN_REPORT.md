# AutoResearchClaw v0.3.1 — 完整项目扫描报告

## 项目概览

| 指标 | 数值 |
|------|------|
| 源码文件 | 316 个 Python 文件 |
| 源码行数 | 78,285 行 |
| 测试文件 | 110 个 |
| 测试行数 | 41,335 行 |
| 测试结果 | **3,065 通过**, 52 跳过, 0 失败 |
| 子包数量 | 30+ |
| 管道阶段 | 23 个 |

---

## 一、安全问题 (按严重程度排序)

### HIGH-1: WebSocket 端点绕过认证中间件

**文件**: `researchclaw/server/middleware/auth.py` + `researchclaw/server/app.py`

**问题**: `TokenAuthMiddleware` 继承自 `BaseHTTPMiddleware`，该基类**不拦截 WebSocket 升级请求**。所有 WebSocket 端点（实时管道状态、HITL 交互）完全无认证保护。

**修复建议**:
```python
# 在 WebSocket 路由中添加显式 token 校验
@app.websocket("/ws/pipeline/{run_id}")
async def ws_pipeline(websocket: WebSocket, run_id: str):
    token = websocket.query_params.get("token") or websocket.headers.get("Authorization")
    if not _verify_token(token):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    ...
```

### HIGH-2: SSH 命令注入

**文件**: `researchclaw/servers/ssh_executor.py:46`

**问题**: `command` 参数被直接拼接到 SSH shell 字符串中，未经任何清理。攻击者可通过注入 `; rm -rf /` 等 payload 执行任意命令。

**修复建议**:
```python
import shlex
sanitized = shlex.quote(command)
# 或使用 paramiko 的 exec_command() 代替 shell 拼接
```

### HIGH-3: 速率限制 IP 可伪造

**文件**: `researchclaw/server/middleware/rate_limit.py`

**问题**: 速率限制基于 `X-Forwarded-For` 头提取客户端 IP，该头可被任意客户端伪造，从而绕过速率限制。

**修复建议**: 仅在已知反向代理后端信任 `X-Forwarded-For`，否则使用 `request.client.host`。可考虑使用 uvicorn 的 `--proxy-headers` 配置配合受信任代理列表。

### HIGH-4: SSRF 防护存在 DNS 重绑定窗口

**文件**: `researchclaw/web/_ssrf.py`

**问题**: DNS 解析和实际 HTTP 请求之间存在时间窗口（TOCTOU），攻击者可利用 DNS 重绑定在检查通过后将域名指向内网 IP。

**修复建议**: 在 socket 层面固定 IP，使用 `urllib3` 的 `ResolverOverride` 或在连接时二次校验目标 IP。

### MEDIUM-5: SSH 全局禁用主机密钥检查

**文件**: `researchclaw/servers/ssh_executor.py`

**问题**: `StrictHostKeyChecking=no` 被全局设置，使得 SSH 连接容易遭受中间人攻击。

**修复建议**: 首次连接时保存主机密钥，后续连接进行校验 (`StrictHostKeyChecking=accept-new`)。

### MEDIUM-6: Docker 沙箱授予 NET_ADMIN 能力

**文件**: `researchclaw/experiment/docker_sandbox.py`

**问题**: `setup_only` 网络策略使用 `--cap-add=NET_ADMIN` 来实现"实验开始后断网"，但该能力赋予容器修改宿主机网络命名空间的权限。

**修复建议**: 改用 Docker 网络策略（创建无外部连接的 Docker network）或分阶段构建容器。

### MEDIUM-7: API Key 出现在文档字符串中

**文件**: `researchclaw/web/` 模块中 Tavily 客户端

**问题**: Tavily API key 以示例形式硬编码在 docstring 中，可能被提取利用。

**修复建议**: 移除 docstring 中的真实/示例 key，改为环境变量引用说明。

### MEDIUM-8: HuggingFace Token 输出到调试日志

**文件**: `researchclaw/experiment/docker_sandbox.py`

**问题**: `HF_TOKEN` 在 debug 级别日志中被完整记录，日志文件可能被非授权人员访问。

**修复建议**: 日志中对敏感 token 进行掩码处理 (`HF_TOKEN=sk-...xxxx`)。

### LOW-9: /api/config 信息泄露

**文件**: `researchclaw/server/app.py`

**问题**: 配置端点暴露了内部系统配置详情，可能泄露模型名称、API 端点等敏感信息。

**修复建议**: 过滤返回的配置字段，仅暴露前端需要的非敏感项。

### LOW-10: 大量裸 except Exception 捕获

**全项目**: 141 处 `except Exception` (仅 pipeline/ 目录)

**问题**: 吞掉所有异常使得调试困难，也可能掩盖严重错误（如内存不足、权限不足）。

**修复建议**: 逐步替换为具体异常类型，至少将 `except Exception as e` 中的异常记录完整堆栈。

---

## 二、架构与代码质量建议

### ARCH-1: config.py 巨石文件 (1,542 行)

**文件**: `researchclaw/config.py`

**问题**: 30+ 个 dataclass 定义 + 20+ 个解析函数 + 验证逻辑全部混在一个文件里。

**建议拆分为**:
```
researchclaw/config/
├── __init__.py        # 重新导出 RCConfig
├── schema.py          # 所有 @dataclass 定义
├── parsing.py          # _parse_* 函数
├── validation.py       # 校验逻辑
└── defaults.py         # 默认值常量
```

### ARCH-2: _review_publish.py 涵盖 6 个阶段 (2,661 行)

**文件**: `researchclaw/pipeline/stage_impls/_review_publish.py`

**问题**: 一个文件实现了阶段 18-23（自审、外审、修订、最终检查、发布准备、归档），职责过多。

**建议拆分为**:
```
_review.py       # 阶段 18-19 (自审 + 外审)
_revision.py     # 阶段 20-21 (修订 + 最终检查)
_publish.py      # 阶段 22-23 (发布 + 归档)
```

### ARCH-3: _execute_paper_draft 单函数 907 行

**文件**: `researchclaw/pipeline/stage_impls/_paper_writing.py`

**问题**: 这是整个项目中最大的单函数，包含论文各部分的生成逻辑、LaTeX 拼接、引用插入等。

**建议**: 提取为 `PaperDraftBuilder` 类，每个论文章节（摘要、引言、方法、实验、结论）各自一个方法。

### ARCH-4: 零自定义异常类

**全项目**

**问题**: 整个 78,000+ 行的代码库没有定义任何自定义异常类。所有错误处理都使用 `ValueError` / `RuntimeError` / `Exception`，调用方无法精确捕获特定错误。

**建议**:
```python
# researchclaw/exceptions.py
class ResearchClawError(Exception): ...
class PipelineError(ResearchClawError): ...
class StageFailedError(PipelineError): ...
class LLMError(ResearchClawError): ...
class LLMRateLimitError(LLMError): ...
class SandboxError(ResearchClawError): ...
class ConfigValidationError(ResearchClawError): ...
```

### ARCH-5: LLM 适配器缺少统一接口

**文件**: `researchclaw/llm/` 目录

**问题**: `client.py`、`anthropic_adapter.py`、`gemini_adapter.py`、`acp_client.py` 四个文件各自实现 LLM 调用，但没有共享的 `Protocol` 或 `ABC`。消息格式转换逻辑（~50 行）在 Anthropic 和 Gemini 适配器中重复。

**建议**:
```python
from typing import Protocol

class LLMClient(Protocol):
    def chat(self, messages: list[dict], **kwargs) -> dict: ...
    def chat_json(self, messages: list[dict], **kwargs) -> dict: ...
```

### ARCH-6: conftest.py 为空，105 个测试文件重复 fixture

**文件**: `tests/conftest.py` (仅 1 行注释)

**问题**: 所有测试 fixture 在各文件中独立定义和维护，导致大量重复代码和不一致的 mock 行为。

**建议**: 将通用 fixture（mock LLM client、临时配置、pipeline context 等）提取到 `conftest.py`，按功能组织：
```python
# tests/conftest.py
@pytest.fixture
def mock_llm_client(): ...

@pytest.fixture
def tmp_config(tmp_path): ...

@pytest.fixture
def pipeline_context(mock_llm_client, tmp_config): ...
```

### ARCH-7: pyproject.toml 中的幽灵包

**文件**: `pyproject.toml`

**问题**: Hatchling 构建目标中包含 `sibyl` 和 `arc` 两个包，但项目中没有明显对应的说明或用途。可能是实验性/遗留代码。

**建议**: 确认这两个包的用途。如果已废弃，从构建配置中移除以避免混淆。

---

## 三、测试质量建议

### TEST-1: 测试通过率良好，但覆盖率未知

- 3,065 个测试全部通过，52 个跳过
- **未配置 `pytest-cov`**，无法确认实际代码覆盖率
- 建议添加覆盖率报告：`pip install pytest-cov && pytest --cov=researchclaw --cov-report=html`

### TEST-2: 52 个跳过的测试需要审查

建议运行 `pytest -rs` 查看跳过原因，确认是否有永久性跳过的"死"测试。

### TEST-3: 缺少集成测试标记

建议使用 `@pytest.mark.integration` / `@pytest.mark.slow` 等标记区分单元测试和集成测试，在 CI 中可分层运行。

---

## 四、开发体验建议

### DX-1: 未配置 linting/formatting 工具

项目没有 `ruff`、`flake8`、`black`、`mypy` 的配置。对于一个 78,000 行的 Python 项目，这是必要的。

**建议在 `pyproject.toml` 中添加**:
```toml
[tool.ruff]
target-version = "py310"
line-length = 100
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
```

### DX-2: 缺少 CI/CD 配置

项目中没有看到 `.github/workflows/` 或其他 CI 配置文件。建议至少配置：
- PR 构建：lint + test
- 主分支：lint + test + coverage report
- 安全扫描：`bandit` 或 `semgrep`

### DX-3: 缺少 CONTRIBUTING.md / CHANGELOG.md

对于一个具有 23 个管道阶段的复杂项目，缺少贡献指南和变更日志。

---

## 五、优先级行动清单

| 优先级 | 项目 | 工作量 |
|--------|------|--------|
| **P0 - 立即修复** | SSH 命令注入 (HIGH-2) | 0.5 天 |
| **P0 - 立即修复** | WebSocket 认证绕过 (HIGH-1) | 1 天 |
| **P0 - 立即修复** | 速率限制 IP 伪造 (HIGH-3) | 0.5 天 |
| **P1 - 尽快修复** | SSRF DNS 重绑定 (HIGH-4) | 1 天 |
| **P1 - 尽快修复** | 日志中的敏感 token (MEDIUM-8) | 0.5 天 |
| **P1 - 尽快修复** | 添加 ruff/mypy 配置 (DX-1) | 0.5 天 |
| **P2 - 计划修复** | 自定义异常层次 (ARCH-4) | 2 天 |
| **P2 - 计划修复** | conftest.py fixture 提取 (ARCH-6) | 2 天 |
| **P2 - 计划修复** | config.py 拆分 (ARCH-1) | 2 天 |
| **P3 - 持续改善** | _review_publish.py 拆分 (ARCH-2) | 1 天 |
| **P3 - 持续改善** | LLM 统一接口 (ARCH-5) | 1 天 |
| **P3 - 持续改善** | 测试覆盖率集成 (TEST-1) | 0.5 天 |

---

## 总结

AutoResearchClaw 是一个功能非常完整且野心宏大的项目——23 阶段全自动研究管道、多 LLM 后端、沙箱执行、HITL 系统、自我进化机制。**3,065 个测试全部通过**说明核心功能稳定。

最紧迫的问题在**安全层面**：SSH 命令注入和 WebSocket 认证绕过是可被远程利用的漏洞，应立即修复。架构方面最大的改进点是引入自定义异常层次和拆分几个过大的模块——这些不会阻塞功能开发，但会显著提升长期可维护性。
