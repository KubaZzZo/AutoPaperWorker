# AutoResearchClaw v0.3.1 — 项目扫描报告

**扫描日期**: 2026-05-17
**审查范围**: ~200 个源文件（流水线、HITL、MCP、内存、知识图谱、技能、GUI、模板）
**审查维度**: 安全性 / 缺陷与功能缺失 / 编码与乱码 / 代码质量

---

## 严重 (CRITICAL) — 共 6 项

### C1. 认证令牌泄露到 stdout 和 URL 查询参数

- **文件**: `researchclaw/cli.py:601-602`, `630-631`
- **类别**: 安全性
- **描述**: `cmd_serve` 和 `cmd_dashboard` 将认证令牌打印到终端 stdout（`print(f'Web auth token: {token}')`），并将其嵌入 URL（`http://{host}:{port}/?token={token}`）。`server/middleware/auth.py:30-33` 同时接受来自 URL 查询参数的令牌，这使得令牌暴露在浏览器历史记录、服务器访问日志、Referer 头以及终端回滚缓冲区中。
- **建议**: 移除打印语句和 URL 嵌入。令牌应仅通过 `Authorization: Bearer` 头传递。如果需要传达给用户，将其写入文件并告知路径，或输出到 stderr 并附带安全警告。

### C2. MCP 服务器任意文件读取

- **文件**: `researchclaw/mcp/server.py:178`
- **类别**: 安全性
- **描述**: `_handle_review_paper` 方法接受外部提供的 `paper_path` 参数，仅检查 `path.exists()` 和 `is_file()`，但没有目录遍历保护。连接 MCP 服务器的攻击者可以传递 `paper_path='/etc/passwd'` 或进程用户可访问的任何其他文件系统路径。
- **建议**: 将 `paper_path` 替换为经过 `_validated_run_dir()` 验证的 `run_id + 相对文件名` 模式。如果必须使用自由格式路径，使用 `is_relative_to()` 将其限制在允许的基目录内。

### C3. SSETransport 无界内存泄漏

- **文件**: `researchclaw/mcp/transport.py:87`, `100`
- **类别**: 缺陷
- **描述**: `sent_events: list[str]` 列表存储了曾经发送的每个 SSE 帧，永不清理。对于长时间运行的 MCP 服务器，内存会无限增长。如果服务器每小时处理数千个事件，几天内可能消耗数 GB 内存。
- **建议**: 将 `sent_events` 替换为有界环形缓冲区，或者如果外部不消费则完全移除。

### C4. 损失函数指标的置信度计算反转

- **文件**: `researchclaw/memory/experiment_memory.py:70`, `107`
- **类别**: 缺陷
- **描述**: `confidence = min(1.0, 0.4 + metric * 0.5)` 假定指标越高越好。对于精度值这是正确的，但对于损失函数（交叉熵、MSE），越低的损失越好 — 损失为 2.0 的较差运行获得最高置信度。这会污染所有下游记忆检索，使系统偏向于较差的配置。
- **建议**: 添加 `metric_direction` 参数（`maximize` / `minimize`），当方向为 `minimize` 时反转计算。

### C5. Context7 子进程在超时后泄漏

- **文件**: `researchclaw/mcp/context7_client.py:164-200`
- **类别**: 缺陷
- **描述**: 当 `_call_tool` 等待响应超时时，子进程继续运行。下次调用发现进程仍然存活并复用其标准输入/输出 — 但之前的陈旧响应可能仍然缓冲在标准输出上，导致请求/响应不匹配并返回陈旧数据。
- **建议**: 超时后终止并重启子进程。

### C6. WebSocket 文件编辑 TOCTOU 路径遍历

- **文件**: `researchclaw/hitl/adapters/ws_adapter.py:313-316`, `323`
- **类别**: 缺陷
- **描述**: 路径遍历检查解析 `fpath` 并验证它保持在 `stage_dir` 内，但第 323 行的写入使用未解析的 `fpath`。在检查和写入之间，符号链接可能在理论上被交换。
- **建议**: 存储 `resolved_fpath` 并用于写入操作。

---

## 高 (HIGH) — 共 22 项

### 安全类 (5 项)

#### H1. YAML 配置文件中明文存储 API 密钥

- **文件**: `researchclaw/config/parsing.py:221`, `263`, `429`, `509`, `514`
- **描述**: `tavily_api_key`、`s2_api_key`、`gemini_api_key`、`fallback_api_key`、MetaClaw PRM `api_key` 直接从 YAML 配置文件中作为明文字符串提取。配置 YAML 可能具有全局可读权限、被备份或意外提交到版本控制。
- **建议**: 统一使用 `api_key_env` 模式（如主 `LlmConfig` 所做）。配置文件中仅存储环境变量名称。

#### H2. 默认监听 0.0.0.0 暴露管理接口

- **文件**: `researchclaw/config/schema.py:487`, `config/parsing.py:656`, `mcp/transport.py:82`
- **描述**: Web 服务器和 MCP SSE 传输默认监听 `0.0.0.0`，将管理接口暴露给局域网。代码中标注了 `# nosec B104` 来抑制 Bandit 警告。
- **建议**: 将默认值改为 `127.0.0.1`。移除 `nosec B104` 注释。

#### H3. 音频上传无文件大小限制

- **文件**: `researchclaw/server/routes/voice.py:35`
- **描述**: `audio_bytes = await file.read()` 将整个上传文件读入内存，没有任何大小验证。攻击者可以上传任意大的文件导致 OOM。
- **建议**: 预先检查文件大小，超过合理限制（如 25 MB）时返回 HTTP 413。

#### H4. 完整环境变量传递给子进程

- **文件**: `researchclaw/mcp/context7_client.py:103`, `researchclaw/llm/acp_client.py:422-428`, `researchclaw/experiment/environment.py:35`
- **描述**: 子进程以 `env={**os.environ}` 启动，将所有 API 密钥传递给 npm 或 CLI 工具进程。如果该 npm 包被入侵，攻击者将获取所有环境变量中的凭据。
- **建议**: 使用白名单方式构建环境变量字典，仅传递子进程需要的变量。

#### H5. 查询参数接受认证令牌

- **文件**: `researchclaw/server/middleware/auth.py:30-33`, `36-41`
- **描述**: 认证中间件接受 URL 查询参数中的令牌（`?token=`），并在存在时优先于 Authorization 头。WebSocket 认证也优先使用查询参数。令牌暴露在浏览器历史记录、服务器日志和 Referer 头中。
- **建议**: 移除查询参数令牌支持，仅通过 Authorization 头接受令牌。

### 缺陷类 (5 项)

#### H6. 阶段 14 推广的版本目录排序错误

- **文件**: `researchclaw/pipeline/runner.py:837`
- **描述**: `sorted(run_dir.glob("stage-14*"))` 对 `Path` 对象使用词法排序。对于 `stage-14_v1`、`v10`、`v2`、`v3`，顺序是 `v1 < v10 < v2 < v3`。当超过 9 次实验迭代时，可能选错"最佳"迭代。`artifact_io.py:108` 中已有正确的 `_stage_sort_key` 函数但此处未使用。

#### H7. RESULT_ANALYSIS 输入契约指向过时目录

- **文件**: `researchclaw/pipeline/contracts.py:133-139`
- **描述**: `ITERATIVE_REFINE`（阶段 13）输出 `experiment_final/`，但 `RESULT_ANALYSIS`（阶段 14）要求输入 `runs/`。如果执行了细化，阶段 14 将分析原始实验数据而非细化后的数据。

#### H8. 子进程 stdout 无速率限制读取

- **文件**: `researchclaw/llm/client.py:356`
- **描述**: 错误响应主体通过 `e.read().decode()[:500]` 读取，没有长度限制。如果服务器返回异常大的错误页面，可能导致内存压力。

#### H9. MCP 工具调用参数在 INFO 级别记录

- **文件**: `researchclaw/mcp/server.py:64`
- **描述**: `logger.info('MCP tool call: %s(%s)', name, json.dumps(arguments, default=str)[:200])` 将任意 MCP 客户端参数记录到日志。如果未来工具接受敏感参数（API 密钥、令牌），它们将明文出现在日志中。
- **建议**: 在序列化之前对已知的敏感键名进行选择性脱敏。

#### H10. 速率限制仅覆盖单个端点

- **文件**: `researchclaw/server/middleware/rate_limit.py:17`
- **描述**: `LIMITED_PATHS` 仅包含 `/api/pipeline/start`。所有其他端点（语音上传、聊天、项目管理、WebSocket）没有速率限制，可能被滥用。

### 编码与乱码 (17 项)

#### H11-H18. `open()` 无 `encoding='utf-8'`（8 处）

| 文件 | 行号 |
|------|------|
| `scripts/test_codegen_v2.py` | 620 |
| `tests/e2e_real_llm.py` | 32 |
| `researchclaw/dashboard/collector.py` | 129, 148, 171 |
| `researchclaw/server/dialog/session.py` | 100 |
| `researchclaw/server/routes/pipeline.py` | 38 |
| `researchclaw/server/routes/projects.py` | 30 |

- **影响**: 这些文件打开 JSON 文件，JSON 经常包含非 ASCII 的作者姓名和期刊名称。在中国 Windows 系统上，默认编码为 `cp936`，会无声地破坏 UTF-8 JSON。
- **修复**: 在以上所有位置使用 `open(path, encoding='utf-8')` 或 `path.open(encoding='utf-8')`。

#### H19-H30. `.decode()` 无显式编码（13 处）

涉及的关键文件：
| 文件 | 行号 | 风险说明 |
|------|------|----------|
| `researchclaw/llm/client.py` | 356 | LLM 错误响应可能包含特殊字符 |
| `researchclaw/servers/ssh_executor.py` | 37, 64, 65, 84 | 远程 SSH 输出几乎肯定是 UTF-8 |
| `researchclaw/servers/slurm_executor.py` | 119, 122, 142 | 集群输出可能包含用户信息中的非 ASCII 字符 |
| `researchclaw/servers/monitor.py` | 67-68 | 远程监控输出 |
| `researchclaw/mcp/transport.py` | 57, 114 | SSE 消息负载 |
| `researchclaw/workbench/remote.py` | 273 | 远程数据响应 |

- **修复**: 使用 `.decode("utf-8")` 而不是 `.decode()`。

#### H31-H34. 源代码中的乱码文本（4 处）

- **文件**: `tests/test_rc_executor.py:4037`, `4236`, `4325`
  - 当前乱码: `# R7 Tests 鈥?Experiment-Paper` 等
  - 应为: `# R7 Tests — Experiment-Paper` 等（em-dash `—` U+2014）
- **文件**: `tests/test_rc_executor.py:4423`
  - 当前乱码: `85.3 卤 1.2 accuracy`
  - 应为: `85.3 ± 1.2 accuracy`（plus-minus `±` U+00B1）
- **原因**: 这些 UTF-8 字节序列被以 Windows-1252 或 Latin-1 错误解码。

---

## 中等 (MEDIUM) — 共 13 项

### 缺陷类

#### M1. HITL 会话持久化失败静默忽略

- **文件**: `researchclaw/hitl/session.py:379-380`, `391-392`, `411-412`
- **描述**: 三个 `except OSError` 处理程序记录级别为 `DEBUG`。如果磁盘满、权限变更或文件系统只读 — 失败在生产环境中不可见。
- **建议**: 至少将日志级别提升到 `logger.warning`。

#### M2. 未知 HITL 操作静默映射到 INJECT

- **文件**: `researchclaw/hitl/adapters/scripted_adapter.py:138-139`
- **描述**: `_ACTION_MAP.get(action_str, HumanAction.INJECT)` 会将 `"aproove"` 拼写错误或任何未知操作字符串静默回退到 `INJECT`。
- **建议**: 在 `from_file`/`from_dict` 加载时验证操作。

#### M3. MemoryStore.add 每次溢出时排序全部条目

- **文件**: `researchclaw/memory/store.py:132-134`
- **描述**: 每次 `add()` 超过容量时触发完全的 O(n log n) 排序。对于 `max_entries_per_category=500`，这是低效的。
- **建议**: 使用 `SortedList` 或仅定期排序。

#### M4. wait_for_human 阻塞线程长达 24 小时

- **文件**: `researchclaw/hitl/session.py:170-261`
- **描述**: `wait_for_human()` 同步阻塞直到人类响应（默认最长 24 小时），没有进度信号。在 Web 服务器上下文中，耗尽所有线程池线程将阻塞新的流水线启动。

#### M5. WebSocket 认证在空令牌时静默禁用

- **文件**: `researchclaw/server/middleware/auth.py:54-57`
- **描述**: 当 `app.state.auth_token` 未设置时，WebSocket 认证返回 `True` 而不要求任何令牌。配置错误的服务器会静默禁用 WebSocket 认证。
- **建议**: 移除空令牌绕过。测试应显式注入测试令牌。

#### M6. KnowledgeGraphQuery 破坏封装

- **文件**: `researchclaw/knowledge/graph/query.py:160`
- **描述**: 查询引擎直接访问 `KnowledgeGraphBuilder._entities` 私有属性。如果内部存储变更，查询引擎会静默失败。
- **建议**: 添加公共方法 `get_all_entities()`。

#### M7. 表情符号编码守卫遗漏代码页

- **文件**: `researchclaw/health.py:628-638`
- **描述**: 代码检测 `sys.stdout.encoding` 以决定使用表情符号还是 ASCII 图标，但遗漏了 `cp950`（繁体中文）、`cp949`（韩文）、`cp874`（泰文）等代码页。
- **建议**: 扩展回退列表或默认使用 ASCII。

#### M8. 不一致的 subprocess decoding error handlers

- **文件**: `researchclaw/experiment/docker_sandbox.py:71`, `sandbox.py:87`, `ssh_sandbox.py:426-428`, `colab_sandbox.py:107`, `code_agent.py:114`
- **描述**: Docker 沙箱使用 `errors="surrogateescape"`（保留原始字节），而基类沙箱和 SSH/Colab 后端使用 `errors="replace"`（静默替换为 `?`）。行为因后端而异。
- **建议**: 统一使用 `errors="replace"` 或由配置控制。

#### M9. 缺乏 PYTHONIOENCODING 设置

- **文件**: `researchclaw/__main__.py`
- **描述**: 入口点未为子进程设置 `PYTHONIOENCODING=utf-8` 或 `PYTHONUTF8=1`。在控制台编码为 `cp936` 的中文 Windows 上，任何子进程对包含中文或特殊字符的 `print()` 输出都将出现乱码。
- **建议**: 在入口点添加 `os.environ.setdefault("PYTHONIOENCODING", "utf-8")`。

---

## 低 (LOW) — 共 7 项

#### L1. MemoryStore.update_confidence 手动拷贝字段

- **文件**: `researchclaw/memory/store.py:170-180`
- **描述**: `update_confidence` 和 `mark_accessed` 都手动重建 `MemoryEntry`，逐字段拷贝。如果添加新字段，这些方法会静默丢弃它。
- **建议**: 使用 `dataclasses.replace(entry, ...)`。

#### L2. IdeationMemory 失败置信度下限无效

- **文件**: `researchclaw/memory/ideation_memory.py:64-66`
- **描述**: `if outcome == "failure": confidence = max(0.5, confidence)` — 对于 `quality_score >= 3`，置信度已经 >= 0.5，因此"失败增强"无效。
- **建议**: 使用 `confidence = max(0.5, confidence) + 0.1`。

#### L3. WebSocketHITLAdapter._connected_clients 为死代码

- **文件**: `researchclaw/hitl/adapters/ws_adapter.py:77`
- **描述**: 列表已初始化但从未被填充或读取。

#### L4. CLIAdapter 行计数差一

- **文件**: `researchclaw/hitl/adapters/cli_adapter.py:192-195`
- **描述**: 使用 `chr(10)` 替代 `"\n"`，尾部换行符导致行计数差一。

#### L5. 知识库阶段 7 语义分类不准确

- **文件**: `researchclaw/knowledge/base.py:127`
- **描述**: 阶段 7（"综合"）被归类为 `"findings"` 而非更准确的如 `"synthesis"`。

#### L6. 学术 ID 文件名部分清理

- **文件**: `researchclaw/literature/arxiv_client.py:250`
- **描述**: PDF 文件名仅将 `/` 替换为 `_`。如果从不受信任来源传入，应添加 regex 验证。

#### L7. 缺少编码声明头部

- **文件**: 含有中文字面量的 5 个文件（`workbench/cs_project.py`, `workbench/cnki_import.py`, `gui/app.py`, `voice/commands.py`, `server/dialog/intents.py`）
- **描述**: 缺少 `# -*- coding: utf-8 -*-` 头部。Python 3 中默认 UTF-8，但在 `PYTHONIOENCODING` 覆盖的环境中是良好实践。

---

## 正面发现

- **不可变模式**: 代码库一致使用 `@dataclass(frozen=True)` 和不可变替换，符合项目的编码标准
- **关注点分离**: HITL 子系统通过基于文件的 IPC 干净地将会话管理（session.py）与输入收集适配器（CLI、WebSocket、MCP、Scripted）分离
- **降级链**: 嵌入模块具有良好的回退链（API → sentence-transformers → TF-IDF），确保后端不可用时不会崩溃
- **纵深防御**: MCP 服务器、WebSocket 适配器和 CLI 适配器各自独立验证文件路径，提供多层保护
- **阶段契约系统**: `StageContract` 数据类为每个流水线阶段明确定义输入/输出边界
- **编码最佳实践**: 30+ 处代码使用显式 `encoding='utf-8'` 进行文件 I/O，表明编码意识已经存在

---

## 汇总统计

| 严重程度 | 数量 | 分类 |
|----------|------|----------|
| CRITICAL | 6 | 2 安全 + 4 缺陷 |
| HIGH | 22 | 5 安全 + 5 缺陷 + 17 编码 |
| MEDIUM | 13 | 9 缺陷 + 4 编码 |
| LOW | 7 | 7 质量 |
| **总计** | **48** | |

### 修复优先级

1. **立即**: C1（令牌泄露）、C2（任意文件读取）
2. **本周**: C3（内存泄漏）、C4（置信度反转）、H11-H18（编码缺失的 open() 调用）、H19-H30（编码缺失的 decode() 调用）
3. **尽快**: H1（YAML API 密钥）、H2（0.0.0.0 监听）、H31-H34（乱码文本）
4. **后续**: 所有中等和低严重程度项目
