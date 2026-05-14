# ResearchClaw 项目全面审计报告

**审计日期:** 2026-05-13  
**项目版本:** 0.3.1 (__init__.py) / 0.5.0 (server/app.py) -- 版本不一致  
**审计范围:** `researchclaw/` 全部源文件 (~150+ Python 文件), 配置文件, 测试文件

---

## 2026-05-14 Fix Update

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Version reporting is unified: `researchclaw/server/app.py` now uses `researchclaw.__version__` for FastAPI metadata and `/api/health`.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `_helpers.py` now has dedicated tests in `tests/test_pipeline_helpers.py`, covering code-block extraction, multi-file parsing safety, stdout metric parsing, experiment result aggregation, and paper title extraction. The new coverage also fixed a path-traversal fallback bug in `_extract_multi_file_blocks()`.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `print_doctor_report()` now explicitly disables emoji icons for cp936/cp932/GBK/Shift-JIS style terminals before writing output.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `SandboxResult` now includes `has_divergence`; completed and timeout runs set it from `detect_nan_divergence()` so callers can distinguish filtered non-finite metrics from clean output.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `_write_checkpoint()` now sets temporary checkpoint file mode to `0o644` before atomic replacement.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Paper verification now checks always-allowed numeric constants using an absolute tolerance instead of exact float set membership.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `researchclaw wizard` now serializes generated YAML through `yaml.safe_dump(..., sort_keys=False)` and writes UTF-8 output.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Stage 17 paper drafting no longer fabricates `[PLACEHOLDER]` manuscript sections when LLM retries are exhausted. `_write_paper_sections()` now logs the failed section with exception context and raises a section-specific `RuntimeError`, with regression coverage proving incomplete drafts fail instead of being treated as real papers.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Stage 23 citation verification no longer writes a placeholder `references_verified.bib` when verification and citation pruning remove every BibTeX entry. It now writes `citation_verify_failure.json`, clears the verified bibliography to avoid stale content, and returns `StageStatus.FAILED` so publication packaging cannot treat an empty bibliography as complete.</span>

## 一、安全问题 (Security)

### 严重 (CRITICAL)

#### 1. HF_TOKEN 泄露到实验容器
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Docker experiment containers no longer receive host HF_TOKEN/HUGGING_FACE_HUB_TOKEN by default. Added explicit experiment.docker.forward_hf_token opt-in config, default false, with regression tests.</span>

- **文件:** `researchclaw/experiment/docker_sandbox.py:518-520`
- **问题:** `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` 从宿主机环境变量读取并通过 `-e` 传递给 Docker 容器。LLM 生成的实验代码在容器内可访问此令牌，在网络可用时可外泄。
- **建议:** 不自动转发 HF_TOKEN。添加显式配置选项 `docker_sandbox.forward_hf_token: false` (默认关闭)。

#### 2. ClaudeCodeAgent 使用 `--dangerously-skip-permissions`
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Claude Code commands no longer include `--dangerously-skip-permissions`; default allowed tools are limited to `Edit Write Read`, and dangerous extra_args attempting to re-enable permission bypass or Bash are filtered.</span>

- **文件:** `researchclaw/experiment/code_agent.py:566-580`
- **问题:** `--dangerously-skip-permissions` 标志允许 Claude Code CLI 执行任意系统命令，无需用户批准。`--allowed-tools "Bash Edit Write Read"` 权限极宽。
- **建议:** 移除 `--dangerously-skip-permissions` 或从 allowed-tools 中移除 Bash。

#### 3. ACPClient 对所有操作使用 `--approve-all`
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] ACP prompt and warm-up commands no longer include `--approve-all`; prompt command construction is centralized through a no-blanket-approval helper and covered by regression tests.</span>

- **文件:** `researchclaw/llm/acp_client.py:249,469,489`
- **问题:** ACPClient 在所有提示中使用 `--approve-all`，绕过工具使用审批。
- **建议:** 不使用 `--approve-all`，改为配置白名单工具策略。

### 高 (HIGH)

#### 4. 本地进程回退执行 LLM 生成的代码
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Figure rendering no longer falls back to local subprocess execution by default. Local execution now requires explicit allow_local_execution opt-in; Docker remains the secure default.</span>

- **文件:** `researchclaw/agents/figure_agent/renderer.py:256-284`
- **问题:** Docker 不可用时，`_execute_local()` 以本地子进程运行 LLM 生成的 Python 脚本，无任何限制。
- **建议:** 添加明确的配置标志 `renderer.allow_local_execution: false`。

#### 5. SSH 远程沙箱的裸露 Python 执行
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] SSH remote execution now requires Docker by default and rejects non-Docker runs before SSH upload/execution; examples and config defaults were updated accordingly.</span>

- **文件:** `researchclaw/experiment/ssh_sandbox.py:296-314`
- **问题:** 当 `unshare` 不可用时网络隔离静默降级，远程代码具有完全文件系统访问权限。
- **建议:** 要求远程执行始终使用 Docker (`use_docker: true`)。

#### 6. API 密钥通过环境变量传递给子进程
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] OpenCode subprocesses now receive a minimal sanitized environment by default; API key forwarding requires explicit forward_api_key_env opt-in and only maps the configured key to OPENAI_API_KEY.</span>

- **文件:** `researchclaw/pipeline/opencode_bridge.py:462-468`
- **问题:** API 密钥通过 env 字典传递给 OpenCode 子进程，子进程继承这些敏感环境变量。
- **建议:** 使用临时凭证或作用域令牌，避免通过环境变量传递密钥。

### 中 (MEDIUM)

#### 7. YAML 配置中可直接存储 API 密钥
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] llm.api_key is now rejected during config validation and ignored by parsing/runtime clients; LLM credentials must come from llm.api_key_env, and user-facing examples were updated.</span>

- **文件:** `researchclaw/config.py:194-195`
- **问题:** `LlmConfig` 同时支持 `api_key` (纯文本) 和 `api_key_env` (环境变量)，用户可能将真实密钥放入配置文件。
- **建议:** 弃用 `api_key` 字段，仅保留 `api_key_env`。

#### 8. Web 服务器认证可选
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Web authentication is now enabled by default with a generated bearer token when none is configured; protected endpoints return 401 without the token and CLI startup prints the tokenized URL.</span>

- **文件:** `researchclaw/server/middleware/auth.py:31-32`, `app.py:58`
- **问题:** `auth_token` 未设置时完全跳过认证中间件，所有端点公开可访问。
- **建议:** 默认要求认证，生成随机令牌。

#### 9. WebSocket 聊天端点暴露异常详情
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Chat WebSocket errors now return a generic client message while full exception details remain server-side only via logger.exception.</span>

- **文件:** `researchclaw/server/routes/chat.py:57-62`
- **问题:** 完整异常字符串 `str(exc)` 泄露内部路径和配置信息给客户端。
- **建议:** 返回通用错误消息，完整异常仅记录在服务器端。

#### 10. 宽松的 CORS 策略
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] CORS defaults are now restricted to localhost origins, and FastAPI only allows GET/POST/OPTIONS plus Authorization/Content-Type headers instead of wildcard methods and headers.</span>

- **文件:** `researchclaw/server/app.py:49-55`
- **问题:** `allow_methods=["*"]` + `allow_headers=["*"]` + 默认 `cors_origins=("*",)` 过宽。
- **建议:** 生产环境限制 CORS 源和允许的方法/头。

#### 11. AgenticSandbox 配置注入风险
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] agent_install_cmd is now parsed into argv with shell metacharacter rejection and executed via docker exec without bash -c shell interpretation.</span>

- **文件:** `researchclaw/experiment/agentic_sandbox.py:96-100`
- **问题:** `agent_install_cmd` 通过 `bash -c` 执行，无验证。
- **建议:** 验证配置字符串中的 shell 元字符，或使用列表形式参数。

#### 12. Slurm 执行器提交未验证命令
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Slurm submissions now reject unsafe command shell syntax, validate job names/resources/partitions, and validate job IDs before squeue/sacct/scancel calls.</span>

- **文件:** `researchclaw/servers/slurm_executor.py:52-85`
- **问题:** 调用者提供的 command 直接写入 sbatch 脚本提交到集群。
- **建议:** 验证 command 参数，或限制为预定义脚本。

### 低 (LOW)

#### 13. API 密钥硬编码 URL 路径
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] CLI provider choices, endpoint URLs, setup menu labels, and default model lists now derive from shared LLM provider metadata instead of standalone hardcoded CLI maps; regression coverage locks the CLI maps to the centralized presets.</span>

- **文件:** `researchclaw/cli.py:658-667`
- **问题:** `_PROVIDER_URLS` 和 `_PROVIDER_CHOICES` 包含硬编码的 API 端点 URL，如果服务迁移可能需要全局修改。
- **建议:** 从配置文件读取，而非硬编码。

#### 14. 大量 `except Exception: pass` 模式
<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-13] Core pipeline runner silent broad catches were converted to warning/debug logs with exception context for EventLog, cost budget checks, ExperimentSpec, PitfallDetector, experiment memory, KB writes, HITL checkpoint/finalization, deliverables, Evolution, and MetaClaw hooks. Remaining occurrences in other modules should be reviewed in follow-up batches.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-13] DockerSandbox, AgenticSandbox, and LLM/ACP adapter silent catches were logged for environment metadata writes, structured results parsing, container cleanup, ACP session cleanup/reconnect/stdin/stream handling, and provider error-body extraction. Targeted tests verify representative logging paths.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-13] HITL cost guard, file wait, notification persistence, quality predictor, and summarizer silent catches now log tracker, cost-log, response-file, notification-log, artifact-read, PRM-score, dynamic-analysis, and preview failures while preserving retry/degradation behavior. Targeted HITL tests cover representative logging paths.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-13] Web search, literature retrieval, and HITL collaboration/context silent degradation paths now log async crawler fallback, malformed scholar years, Semantic Scholar/OpenAlex parse failures, branch state/artifact reads, guidance reads, and collaboration artifact refresh failures. Targeted web, literature, and HITL tests cover representative logging paths.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-13] Remaining pure `except ...: pass` handlers under `researchclaw/web`, `researchclaw/literature`, and `researchclaw/hitl` were removed or converted to debug/warning logs, including claim verification, stage editor snapshots, TUI status rendering, experiment monitoring, and idea/baseline workshop JSON parsing. AST scanning now reports no pure pass handlers in those directories.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Dashboard progress collection now logs malformed or unreadable `progress.json` snapshots with debug exception context instead of silently discarding them, while still returning a fallback run snapshot so the dashboard remains resilient.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Dashboard optional artifact collection now logs malformed `checkpoint.json`, `heartbeat.json`, nested `results.json`, and unreadable `pipeline.log` files with debug exception context. `researchclaw/dashboard/collector.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Dashboard training-curve metric extraction now logs non-numeric metric values with debug context instead of silently skipping them. `researchclaw/dashboard/metrics.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Wizard environment detection now logs optional Docker version, torch GPU, and psutil memory probe failures with debug context while keeping setup recommendations resilient. `researchclaw/wizard/validator.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Apple Silicon hardware detection now logs failed `sysctl` brand probes with debug exception context while falling back to the generic `Apple Silicon GPU` profile. `researchclaw/hardware.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Memory embedding backend detection now logs unavailable `sentence-transformers` imports before falling back to TF-IDF. `researchclaw/memory/embeddings.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Collaboration artifact publishing now logs malformed `stage-14/experiment_summary.json` parse failures with debug exception context instead of silently skipping experiment results. `researchclaw/collaboration/publisher.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Daily digest LLM-summary parsing now logs malformed relevance scores before falling back to the default relevance value. `researchclaw/trends/daily_digest.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Experiment code complexity validation now logs syntax parse failures with debug context before returning style warnings. `researchclaw/experiment/validator.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Evolution lesson extraction now logs malformed decision-rationale files and non-numeric runtime metrics with debug context while continuing to extract usable lessons. `researchclaw/evolution.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Domain prompt adapter registry now logs unavailable optional domain adapters through a shared lazy-import helper instead of silently ignoring import failures. `researchclaw/domains/prompt_adapter.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Multi-agent base JSON parsing now logs malformed direct, fenced, and embedded JSON candidates with debug context while preserving fallback behavior for non-JSON text. `researchclaw/agents/base.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Context7 MCP subprocess shutdown now logs both failed terminate/wait cleanup and failed kill fallback with debug exception context, so stuck documentation helper processes are no longer silently ignored.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Stage 23 citation verification now logs `paper.tex` read failures while collecting LaTeX `\cite{}` keys, instead of silently ignoring unreadable export artifacts and then pruning references without diagnostic context.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Code search cache statistics now logs malformed or unreadable cache JSON entries with debug exception context while preserving resilient aggregate counts. `researchclaw/agents/code_searcher/cache.py` no longer contains pure `except: pass` handlers.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Code search snippet content retrieval now logs GitHub file fetch failures with repository/path context while preserving graceful degradation. AST scanning now reports no pure `except: pass` handlers under `researchclaw/agents/code_searcher`.</span>

<span style="color: green; font-weight: 700;">[PARTIAL FIX 2026-05-14] Benchmark surveyor optional HuggingFace Hub import is now routed through a testable loader that logs unavailable dependency details at debug level. `researchclaw/agents/benchmark_agent/surveyor.py` no longer contains pure `except: pass` handlers.</span>

- **文件:** 约 50+ 处 (搜索 `# noqa: BLE001`)
- **问题:** 静默吞下异常可能隐藏安全关键错误。
- **建议:** 审查每个实例，至少记录日志，缩小异常类型。

#### 15. SSRF 检查缺失的 DuckDuckGo 降级路径
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] DuckDuckGo fallback requests now pass through the shared web-layer SSRF validator before Request/urlopen construction; unsafe URLs are logged and rejected without making a network call. Regression coverage verifies urlopen is not called when validation blocks the URL.</span>

- **文件:** `researchclaw/web/search.py:204`
- **问题:** `# noqa: S310` 标记，虽然 URL 硬编码无害，但代码模式在重构中易出错。
- **建议:** 添加 URL 验证或路由所有外部请求通过 SSRF 检查。

---

## 二、Bug 和功能缺陷 (Bugs & Functional Issues)

### 严重 (CRITICAL)

#### 16. 4 个关键模块被导入但文件不存在
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Implemented the missing `researchclaw.pipeline.event_log`, `researchclaw.cost_tracker`, `researchclaw.pipeline.experiment_spec`, and `researchclaw.pipeline.pitfall_detector` modules. The runner/HITL dynamic imports now resolve, with regression tests for event JSONL logging, budget checks, experiment spec parsing/validation, and pitfall detection.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Cost tracking price presets were moved into `researchclaw/data/provider_prices.json` with `load_price_table()` support, so future provider/model pricing refreshes are data-only and covered by regression tests.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] MCP `search_literature` now delegates to the real literature search module and serializes paper metadata, replacing the fixed empty stub response while keeping existing MCP response shape.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] MCP `review_paper` now reads the manuscript and returns deterministic structural review metrics instead of a fixed `Stub review` string; missing files now fail explicitly.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] MCP `run_pipeline` now creates a real trackable `artifacts/rc-*` request directory with checkpoint and progress snapshots instead of returning an untracked `mcp-stub-*` id.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `MCPClient` now dispatches local ResearchClaw tool discovery and tool calls through `ResearchClawMCPServer`; stale MCP transport/client stub wording was removed after regression coverage verified local calls and SSE queue semantics.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `CloudExecutor` no longer returns fake `stub_launched`/`stub_unknown` cloud states. It now fails explicitly when no provider backend is configured and supports `host="dry-run"` for deterministic launch/status planning.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Recording web/browser adapters now return explicit recording-only metadata instead of success-looking `stub fetch`/`Stub browser page` content, preventing standalone runs from mistaking captured calls for real external fetches.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] OpenCode Beast Mode entry-point normalization now fails fast when generated `main.py` has neither a `__main__` guard nor a known entry function, instead of silently passing a non-executable project to the sandbox.</span>

<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Stage 4 literature collection no longer fabricates `[Placeholder]` papers when all searches fail. It now writes empty candidates plus `search_meta.json` failure metadata and returns `StageStatus.FAILED` so retry/diagnosis paths can handle the outage honestly.</span>

- **文件:** `researchclaw/pipeline/runner.py:513, 560, 615, 645`
- **问题:** 以下模块在 try/except 中被动态导入，但对应的 .py 文件 **不存在**:
  - `researchclaw.pipeline.event_log` → 管道事件日志功能静默禁用
  - `researchclaw.cost_tracker` → API 成本预算强制执行静默禁用
  - `researchclaw.pipeline.experiment_spec` → 实验规格验证静默禁用
  - `researchclaw.pipeline.pitfall_detector` → ML 实验陷阱检测静默禁用
- **影响:** 用户设置了 `max_budget_usd` 但永远不会被强制执行。所有管道事件（启动、阶段完成、失败）未记录。实验规格验证完全跳过。这些都是设计中的核心功能但从未实现。
- **建议:** 实现这 4 个缺失模块，或移除相关代码并更新文档说明这些功能暂不可用。

#### 17. `_NO_TEMPERATURE_MODELS` 前缀匹配过于宽泛
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] Temperature support now uses exact normalized model-name matching. Future names such as `o3-pro` and `o3-turbo` are no longer blocked by the `o3` prefix.</span>

- **文件:** `researchclaw/llm/client.py:39-45, 120-121`
- **问题:** `_supports_temperature` 使用 `model.startswith("o3")` 匹配。前缀 `"o3"` 将错误匹配未来的 `"o3-pro"`, `"o3-turbo"`, `"o3-high"` 等模型，导致这些模型的 temperature 参数被静默去除。
- **建议:** 使用精确模型名匹配 (`model in _NO_TEMPERATURE_MODELS`) 或更精确的前缀检查。

### 高 (HIGH)

#### 18. AnthropicAdapter httpx.Client 连接池永不关闭
<span style="color: green; font-weight: 700;">[FIXED 2026-05-13] `LLMClient` now exposes `close()` plus context manager support and forwards cleanup to Anthropic/Gemini provider adapters, clearing adapter references even if close logging is needed. Regression tests cover explicit close and `with LLMClient(...)` cleanup.</span>

- **文件:** `researchclaw/llm/anthropic_adapter.py:34-51`, `llm/client.py:96-100`
- **问题:** `AnthropicAdapter` 创建 `httpx.Client` 连接池，但 `LLMClient` 无 `close()` 方法，连接池永不被清理。在长时间运行的服务进程中会累积 TCP 连接和文件描述符。
- **建议:** 为 `LLMClient` 添加 `close()` 方法，或使用 `with` 上下文管理器。

#### 19. 重复的 `_STOP_WORDS` 定义
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Stop-word constants were extracted to shared `researchclaw.utils.text` definitions. Pipeline keyword extraction now uses `BASE_STOP_WORDS`, novelty detection uses `NOVELTY_STOP_WORDS` for its preserved extra filters, and regression tests lock both modules to the shared constants.</span>

- **文件:** `researchclaw/pipeline/_helpers.py:127` vs `researchclaw/literature/novelty.py:35`
- **问题:** 两个文件定义了几乎相同的 ~90+ 英语停用词集合。如果一处更新另一处遗漏，会产生不一致的行为。
- **建议:** 提取为共享常量到 `utils/` 模块。

#### 20. `_helpers.py` (50+ 函数) 缺少专门测试
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Added dedicated `tests/test_pipeline_helpers.py` coverage for core `_helpers.py` behavior, including code-block extraction, multi-file parsing safety, stdout metric parsing, experiment result aggregation, and paper title extraction. The same coverage fixed a path-traversal fallback bug in `_extract_multi_file_blocks()`.</span>

- **文件:** `researchclaw/pipeline/_helpers.py`
- **问题:** 该文件包含 50+ 个被所有 stage_impls 使用的共享辅助函数，但没有专门测试（`test_rc_stages.py` 仅间接测试）。包括文本提取、指标解析、代码块提取、引用格式化等关键功能。
- **建议:** 添加 `test_pipeline_helpers.py`，至少覆盖核心函数。

#### 21. 版本号不一致
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] FastAPI app metadata and `/api/health` now report `researchclaw.__version__` instead of a hardcoded server version. Web platform tests verify both `app.version` and the health endpoint match the package version.</span>

- **文件:** `researchclaw/__init__.py:2` vs `researchclaw/server/app.py:41`
- **问题:** `__init__.py` 声明 `__version__ = "0.3.1"`，但 `server/app.py` 硬编码 `version="0.5.0"`。
- **建议:** 统一使用 `from researchclaw import __version__`。

#### 22. print() 替代 logging 使用
- **文件:** 25 个文件中 339 处使用 `print()`
- **关键文件:** `pipeline/executor.py:18`, `pipeline/runner.py:20`, `prompts.py:4`, `hitl/session.py:1`
- **问题:** 在库代码中使用 `print()` 而非 `logging` 模块，使输出不可控制、不可过滤。
- **建议:** CLI 入口可保留 print，库代码改用 logging。

#### 18. 抽象方法未实现
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `BaseAgent` and `AgentOrchestrator` now inherit from `ABC`, and their required `execute()` / `orchestrate()` methods are decorated with `@abstractmethod`. Regression coverage verifies incomplete base implementations cannot be instantiated while static JSON helpers remain usable.</span>

- **文件:** `researchclaw/agents/base.py:165,210`
- **问题:** 两个 `raise NotImplementedError` 在抽象基类中，但调用方未检查。
- **建议:** 确认所有子类正确覆盖了这些方法。

#### 19. chcp 编码处理缺失 (Windows)
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Same fix as #35: doctor output now pre-detects cp936/cp932/GBK/GB2312/Shift-JIS style stdout encodings and uses ASCII status markers before printing, with regression coverage for cp936 and generic ASCII fallback.</span>

- **文件:** `researchclaw/health.py:627`
- **问题:** `print_doctor_report` 尝试使用 emoji 图标，检测 stdout 编码并降级为 ASCII。但如果终端编码是 cp936/cp932，降级逻辑可能已足够。
- **建议:** 添加对 Windows cp936/cp932 编码的显式处理。

#### 20. Docker 路径在 Windows 上的兼容性
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Docker bind-mount paths now pass through `_docker_mount_path()`, which preserves Docker Desktop Windows paths but converts Windows drive paths to `/mnt/<drive>/...` when WSL2 environment markers are present. Regression tests cover both WSL2 and Docker Desktop path behavior.</span>

- **文件:** `researchclaw/experiment/docker_sandbox.py:452-455`
- **问题:** `os.getuid()` / `os.getgid()` 在 Windows 上不存在，代码使用 `sys.platform == "win32"` 检查返回空列表。但如果用户在 Windows 上使用 WSL2 Docker，路径映射可能有问题。
- **建议:** 添加 WSL2 路径转换逻辑。

#### 21. _PACKAGE_DELIVERABLES 中的硬编码阶段路径
- **文件:** `researchclaw/pipeline/runner.py:945-1235`
- **问题:** 打包逻辑硬编码 `stage-22`, `stage-23` 等阶段号。如果阶段顺序改变，打包会静默失败。
- **建议:** 使用 Stage 枚举引用阶段号。

#### 22. 实验沙箱指标解析对 NaN/Inf 只发警告
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `SandboxResult` now carries `has_divergence`, and sandbox execution sets it from `detect_nan_divergence()` so callers can distinguish clean output from filtered NaN/Inf or divergent metrics. Regression coverage verifies non-finite metric output marks divergence.</span>

- **文件:** `researchclaw/experiment/sandbox.py:141-142`
- **问题:** parse_metrics 遇到 NaN/Inf 时只是 `logger.warning` 并跳过，没有将异常指标的存在通知调用方。
- **建议:** 在 SandboxResult 中添加 `has_divergence: bool` 字段。

### 中 (MEDIUM)

#### 23. 临时文件原子写入
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `_write_checkpoint()` now sets the temporary checkpoint file mode to `0o644` before atomic replacement. Runner tests verify the chmod call and existing atomic-write behavior.</span>

- **文件:** `researchclaw/pipeline/runner.py:158-166`
- **问题:** `_write_checkpoint` 使用 `tempfile.mkstemp` + 重命名实现原子写入，但未设置文件权限模式。
- **建议:** 设置 `os.chmod` 为 `0o644` 防止权限泄露。

#### 24. 浮点数精确相等比较
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Paper verification now checks always-allowed numeric constants with an absolute tolerance instead of exact float set membership. Regression coverage verifies common constants with floating-point roundoff still pass.</span>

- **文件:** `researchclaw/pipeline/paper_verifier.py:218`
- **问题:** `if value in _ALWAYS_ALLOWED:` 使用精确浮点数比较匹配 `0.0003`, `3e-4` 等。浮点表示误差可能导致相等比较失败。
- **建议:** 使用容差比较 (`abs(a-b) < 1e-10`)。

#### 25. 166 个裸 `except Exception` 块
- **文件:** 约 50+ 处 (搜索 `# noqa: BLE001`)
- **问题:** 大量的 `except Exception: pass` 静默丢弃所有错误，包括磁盘满、权限错误、数据损坏等关键失败。
- **关键位置:** `runner.py:719` (KB 写入失败静默跳过), `runner.py:906-913` (进化课程提取失败), `runner.py:1755-1782` (MetaClaw 后处理失败)
- **建议:** 审查每个实例，至少记录日志，缩小异常类型。

#### 26. yaml.dump 不使用安全导出
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `researchclaw wizard` now serializes generated config through `yaml.safe_dump(..., sort_keys=False)` and writes UTF-8 output. CLI regression coverage fails if the wizard path calls `yaml.dump()`.</span>

- **文件:** `researchclaw/cli.py:639,642`
- **问题:** `yaml.dump(config, default_flow_style=False)` 未显式使用 SafeDumper。`yaml.dump` 默认使用 Dumper，支持 Python 对象标记 (`!!python/object`)。
- **建议:** 使用 `yaml.safe_dump`。

#### 25. 并发计数器变量
- **文件:** `researchclaw/experiment/docker_sandbox.py:36-37`, `agentic_sandbox.py:25-26`
- **问题:** `_CONTAINER_COUNTER` 是模块级全局变量，使用 `global` 关键字和 `threading.Lock()` 保护。虽然正确，但在多进程场景下计数器会碰撞。
- **建议:** 使用 `multiprocessing.Value` 或加 PID 前缀（已经做了 `os.getpid()`）。

#### 26. LLM 客户端缺乏连接池
- **文件:** `researchclaw/llm/client.py`
- **问题:** 每次 API 调用都新建 `urllib.request.urlopen` 连接。高频率调用会导致连接建立开销大。
- **建议:** 考虑使用 `urllib3` 或 `httpx` 的连接池。

#### 27. 配置加载时缺少递归深度限制
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Config validation now rejects dictionaries/lists deeper than `MAX_CONFIG_NESTING_DEPTH` using an iterative scanner before normal field parsing; `RCConfig.from_dict()` surfaces the validation error. Regression tests cover both `validate_config()` and `from_dict()`.</span>

- **文件:** `researchclaw/config.py:764-915`
- **问题:** `from_dict()` 方法支持任意嵌套的配置字典。恶意构造的深层嵌套 YAML 可能导致栈溢出。
- **建议:** 添加递归深度检查。

#### 28. Web Search 多查询内联延迟
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `WebSearchClient` now exposes `search_multi_async()`, which uses `asyncio.sleep()` for inter-query delay and runs the existing blocking search call through `asyncio.to_thread()`. The original synchronous `search_multi()` remains compatible for non-async callers, while async callers can avoid blocking the event loop. Regression coverage verifies the async path does not call `time.sleep()`.</span>

- **文件:** `researchclaw/web/search.py:128`
- **问题:** `search_multi` 使用 `time.sleep(inter_query_delay)` 同步等待，如果延迟太长会影响响应性。
- **建议:** 使用 `asyncio.sleep` 在异步上下文中。

#### 29. figure_agent 中 LLM 返回值类型不安全
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Added a shared FigureAgent schema normalization layer that drops non-object figure specs, coerces scalar text fields, sanitizes figure IDs, normalizes string `data_source` values, and clamps invalid priorities. Planner and CodeGen now both pass LLM-derived figure specs through this layer, replacing ad hoc BUG-36/37 guards. Regression coverage verifies malformed string/list/scalar responses are normalized before downstream processing.</span>

- **文件:** `researchclaw/agents/figure_agent/planner.py:311,415`, `codegen.py:447,518`
- **问题:** LLM 返回值可能返回字符串而非预期字典，代码有防御性检查但注释为 "BUG-36/37/38"。这些 bug 已知但仅标记未系统修复。
- **建议:** 添加 schema 验证层包装 LLM 响应。

#### 30. Pipeline 递归调用无限循环风险
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `execute_pipeline()` now carries an internal pivot recursion depth guard in addition to `decision_history.json` counting. If history reads fail or keep returning zero, rollback recursion stops at `MAX_DECISION_PIVOTS` and the pipeline proceeds through the remaining stages. Regression coverage simulates a broken pivot counter.</span>

- **文件:** `researchclaw/pipeline/runner.py:792-808`
- **问题:** `execute_pipeline` 在 PIVOT/REFINE 时可以递归调用自身。虽然有 `MAX_DECISION_PIVOTS=2` 限制，但如果 `_read_pivot_count` 错误返回 0，递归可能无限。
- **建议:** 添加递归深度保护 (`sys.setrecursionlimit` 检查)。

### 低 (LOW)

#### 31. 空的 except 块
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] KB write failures in `execute_pipeline()` now log `Knowledge-base stage write failed` with exception context instead of silently passing. Regression coverage forces `write_stage_to_kb()` to raise and verifies the pipeline continues while the warning includes the underlying error.</span>

- **文件:** `researchclaw/pipeline/runner.py:718-719`
- **问题:** `except Exception: pass` 在 KB 写入失败时静默跳过。
- **建议:** 至少记录 debug 日志。

#### 32. 不必要的 import 在函数内部
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] The remaining function-local `dataclasses` import in `researchclaw/server/routes/pipeline.py` was moved to module scope; `researchclaw/cli.py` already used a module-level import. A static AST regression test now guards `start_pipeline()` against reintroducing function-local `dataclasses` imports.</span>

- **文件:** `researchclaw/cli.py:177,197,356` 等多处
- **问题:** 在函数内部 `import dataclasses` 重复导入（如 `cmd_run` 中的第 177,197 行）。
- **建议:** 将重复的 import 移到函数外部或模块顶部。

#### 33. 硬编码的 `artifacts/` 路径
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] The default artifacts directory is now centralized as `DEFAULT_ARTIFACTS_DIR` in `researchclaw.config`. CLI run directory creation/resume lookup and server pipeline/project route listings use the shared constant instead of repeated `Path("artifacts")` or `f"artifacts/..."` literals. Static regression tests guard the CLI and pipeline route against reintroducing direct literals.</span>

- **文件:** `researchclaw/cli.py:214`, `researchclaw/server/routes/pipeline.py:24`
- **问题:** `artifacts/` 目录路径硬编码在多处，未从配置读取。
- **建议:** 使用配置的 output dir 或定义为常量。

#### 34. 缺少 `data/__init__.py` 目录的实际用途
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `researchclaw.data` is an active static-data API package, not an empty placeholder: it exposes framework detection/docs loading and seminal-paper lookup backed by YAML/Markdown assets. Added `tests/test_data_assets.py` to lock the public API and asset-backed behavior.</span>

- **文件:** `researchclaw/data/__init__.py`
- **问题:** data 目录存在但似乎没有实际使用的模块文件。
- **建议:** 移除空目录或添加实际功能。

---

## 三、编码/乱码问题 (Encoding Issues)

### 高 (HIGH)

#### 35. Windows CP936 终端 Emoji 显示问题
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `print_doctor_report()` now checks stdout encoding before printing icons and forces ASCII `[OK]/[FAIL]/[WARN]` for cp936, cp932, GBK/GB2312, and Shift-JIS style terminals, with a UnicodeEncodeError fallback for other limited encodings. Regression coverage verifies cp936 output contains only ASCII status markers.</span>

- **文件:** `researchclaw/health.py:626-632`
- **问题:** `print_doctor_report` 使用 emoji (✅❌⚠️)，在 Windows CP936 终端上会导致 UnicodeEncodeError。
- **当前缓解:** 代码检测编码并降级为 ASCII `[OK]/[FAIL]/[WARN]`，但仅在 `UnicodeEncodeError` 时触发。
- **建议:** 在启动时预检终端编码，全局关闭 emoji。

#### 36. urllib 的 `errors="replace"` 可能丢失数据
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] The urllib crawler fallback no longer decodes with `errors="replace"`. It now strictly tries the declared charset first, then common fallback encodings, and only falls back to UTF-8 `surrogateescape` if all candidates fail. Crawl metadata records the declared encoding, actual encoding, and whether fallback was used. Regression coverage verifies a bad ASCII charset declaration preserves UTF-8 content without replacement characters.</span>

- **文件:** `researchclaw/web/crawler.py:218`
- **问题:** `html = raw.decode(encoding, errors="replace")` — 如果 Content-Type 声明的编码错误，使用 `replace` 会静默丢弃无法解码的字节。
- **建议:** 先尝试 `errors="strict"` 失败后回退到 `chardet` 自动检测编码。

### 中 (MEDIUM)

#### 37. 编译器 LaTeX 文件中包含隐形 Unicode 字符
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Added upstream Unicode normalization in `markdown_to_latex()` final output sanitization: generated LaTeX is now normalized with NFKC before it is written/compiled, whitespace-like Unicode is converted to ASCII spaces, and invisible direction/zero-width/BOM characters are stripped. Existing compiler-level `.tex` and `.bib` sanitizers remain as a fallback. Regression coverage verifies problematic LLM output is normalized before the compiler stage.</span>

- **文件:** `researchclaw/templates/compiler.py:577-661`
- **问题:** LaTeX 编译器检测到多个 BUG-180, BUG-197 修复：隐形 Unicode 字符 (U+200E, U+200F, U+202F 等) 导致 LaTeX 编译失败。虽然已修复，但属于打补丁式修复而非根治。
- **建议:** 在 LLM 输出后立即进行 Unicode 规范化 (NFKC)。

#### 38. subprocess 编码错误处理
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Docker sandbox execution now captures subprocess output as bytes and decodes stdout/stderr with UTF-8 `surrogateescape`, preserving undecodable experiment output instead of replacing it with U+FFFD. Timeout output uses the same decoder. Regression tests cover normal completion and timeout paths with invalid UTF-8 bytes while still parsing metrics.</span>

- **文件:** `researchclaw/experiment/docker_sandbox.py:352-354`
- **问题:** `subprocess.run` 使用 `encoding="utf-8", errors="replace"` 但 stdout/stderr 可能包含混合编码（实验脚本可能打印非 UTF-8 内容）。
- **建议:** 使用 `surrogateescape` 错误处理器保留原始字节。

### 低 (LOW)

#### 39. 缺少 encoding 声明的文件写入
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] CLI HITL edit handling now reads editor output from bytes through `_read_editor_output()`: UTF-8 strict first, then the platform preferred encoding, CP1252, Latin-1, and finally UTF-8 `surrogateescape`. Edited content is still saved back to stage files as UTF-8. Regression coverage simulates a CP1252 editor write and verifies the edit is preserved.</span>

- **文件:** `researchclaw/hitl/adapters/cli_adapter.py:341`
- **问题:** `subprocess.run([editor, tmp_path])` 依赖于编辑器正确处理文件编码。如果编辑器写入非 UTF-8，后续读取会失败。
- **建议:** 在编辑器返回后检测并转换编码。

#### 40. README.md 中的文档编码声明
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `README.md` now starts with a non-rendered `charset: utf-8` metadata comment, and CI-testable coverage verifies the README strictly decodes as UTF-8 and declares the charset in the first five lines.</span>

- **文件:** `README.md` (45KB)
- **问题:** README.md 较大 (45KB)，但未声明 charset。
- **建议:** 保持 UTF-8 编码并在 CI 中验证。

---

## 四、代码质量问题 (Code Quality)

### 高 (HIGH)

#### 41. prompts.py 文件过大
- **文件:** `researchclaw/prompts.py`
- **问题:** 文件包含超过 2000 行硬编码的提示词字符串，难以维护和审查。
- **建议:** 拆分为独立的 `.yaml` 或 `.md` 提示词文件。

#### 42. converter.py 文件过大且充满 bug 注释
- **文件:** `researchclaw/templates/converter.py`
- **问题:** 1500+ 行，包含 20+ 个 BUG-xxx 注释，表明多次修复但未重构。
- **建议:** 分拆为多个专注的模块。

#### 43. runner.py 循环复杂度高
- **文件:** `researchclaw/pipeline/runner.py:490-942`
- **问题:** `execute_pipeline` 函数 450+ 行，包含深度嵌套的条件逻辑、递归调用和内联事件处理。
- **建议:** 拆分为多个关注点分离的辅助函数。

### 中 (MEDIUM)

#### 44. 多处重复使用 `dataclasses.replace`
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] `RCConfig` now exposes `with_research_overrides(**overrides)` for immutable research config updates. CLI run overrides and the pipeline start API now call this helper instead of duplicating nested `dataclasses.replace(config.research, ...)` patterns. Regression coverage verifies the helper returns an updated copy without mutating the original config.</span>

- **文件:** `researchclaw/cli.py:177-180`, `cli.py:197-198`, `server/routes/pipeline.py:74-76`
- **问题:** 相同的 `dataclasses.replace(config.research, topic=...)` 模式重复出现。
- **建议:** 添加 `config.with_topic(topic)` 方法。

#### 45. 硬编码的数字常量
- **文件:** `researchclaw/pipeline/stages.py:132` 等多处
- **问题:** `MAX_DECISION_PIVOTS = 2`, `_MAX_BACKOFF_SEC = 300`, novelty.py 中的 `0.7/0.3/0.45` 等魔术数字未使用命名常量。
- **建议:** 定义为命名常量并移到配置。

#### 46. 测试覆盖率不足的模块 (35+ 模块缺少测试)
- **文件:** 以下关键模块缺少对应测试:
  - `pipeline/_helpers.py` — 50+ 函数，零专门测试 ⚠️
  - `pipeline/_domain.py` — 领域检测逻辑，零测试
  - `dashboard/`, `voice/`, `wizard/` — 无测试
  - `llm/acp_client.py`, `llm/gemini_adapter.py` — 无测试
  - `experiment/visualize.py`, `experiment/git_manager.py` — 无测试
  - `writing_guide.py` — 无测试
  - `literature/verify.py`, `literature/framework_docs.py` — 无测试
  - `domains/adapters/` — 大部分适配器无测试 (仅 neuroscience/robotics 有)
  - `mcp/registry.py`, `mcp/transport.py` — 无测试
- **建议:** 为关键路径添加测试覆盖，特别是 `_helpers.py`。

#### 47. requirements.txt 中的依赖未锁定版本
- **文件:** `pyproject.toml`
- **问题:** 依赖使用 `>=` 约束 (`pyyaml>=6.0`)，未锁定特定版本。
- **建议:** 为生产部署添加 `requirements.lock` 或使用 `poetry.lock`。

---

## 五、功能缺失 (Missing Features)

### 中 (MEDIUM)

#### 48. 缺少分布式运行的健康检查
- **问题:** 虽然支持分布式训练 (DistributedTrainingConfig)，但没有对应的健康检查验证多节点通信。
- **建议:** 添加 `nccl-tests` 或类似检查到 `researchclaw doctor`。

#### 49. 缺少实验运行的进度通知
- **问题:** 实验在沙箱中运行期间无可观测性 (无 stdout 流式传输，无进度回调)。
- **建议:** 添加 WebSocket 推送实验指标进度。

#### 50. 缺少实验运行的优雅中断
- **文件:** `researchclaw/pipeline/runner.py:539-542`
- **问题:** 支持通过 `cancel_event` 取消管道，但实验沙箱中的运行子进程不会收到 SIGTERM。
- **建议:** 在取消时向子进程发送中断信号。

### 低 (LOW)

#### 51. 缺少 Redis/数据库支持的运行状态
- **问题:** 管道状态仅存储在本地 JSON 文件中，不支持分布式部署或跨机恢复。
- **建议:** 添加可插拔的状态后端 (Redis, SQLite)。

#### 52. 缺少 API 速率限制
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] Added a dependency-free in-memory `RateLimitMiddleware` for state-changing API control endpoints, currently protecting `POST /api/pipeline/start` with per-client sliding-window limits and `Retry-After` responses. `ServerConfig` exposes `rate_limit_requests` and `rate_limit_window_sec`, `create_app()` wires the middleware, and Web platform tests verify 429 behavior.</span>

- **文件:** `researchclaw/server/app.py`
- **问题:** FastAPI 应用未配置速率限制，单个客户端可以频繁调用 `/api/pipeline/start`。
- **建议:** 添加 `slowapi` 或类似中间件。

#### 53. 缺少 LLM 代理配置验证
<span style="color: green; font-weight: 700;">[FIXED 2026-05-14] LLM provider validation now uses the shared `researchclaw.llm.PROVIDER_DETAILS` registry, the same source used by CLI provider menus/defaults. Unknown `llm.provider` values are rejected during `validate_config()`, while every registered provider preset, including `acp`, is covered by regression tests.</span>

- **文件:** `researchclaw/cli.py:646-719`
- **问题:** `_PROVIDER_CHOICES` 硬编码了 9 个 LLM 提供商，但新增提供商需要修改代码。
- **建议:** 从配置文件读取提供商列表。

---

## 六、总结 (Summary)

| 类别 | 严重 | 高 | 中 | 低 | 合计 |
|------|------|-----|-----|-----|------|
| 安全 | 3 | 3 | 6 | 3 | **15** |
| Bug/功能 | 2 | 7 | 10 | 5 | **24** |
| 编码 | 1 | 1 | 2 | 2 | **6** |
| 代码质量 | 2 | 5 | 6 | 2 | **15** |
| 功能缺失 | 0 | 3 | 2 | 3 | **8** |
| **总计** | **8** | **19** | **26** | **15** | **68** |

### 关键修复优先级

**立即修复 (本周):**
1. 移除 HF_TOKEN 自动转发到实验容器 [#1]
2. 移除 `--dangerously-skip-permissions` [#2]
3. 移除 `--approve-all` [#3]
4. 实现或移除 4 个缺失模块的引用 [#16]
5. 修复 `_NO_TEMPERATURE_MODELS` 前缀匹配 bug [#17]

**短期修复 (本月):**
6. 统一版本号 [#21]
7. 添加本地执行保护 [#4]
8. SSH 远程要求 Docker [#5]
9. Web 服务器强制认证 [#8]
10. 隐藏 WebSocket 错误详情 [#9]
11. 弃用配置中的 api_key 字段 [#7]
12. 为 AnthropicAdapter 添加 close() [#18]
13. 为 `_helpers.py` 添加测试 [#46]

**长期改进:**
14. 重构 prompts.py 和 converter.py [#41, #42]
15. 添加 API 速率限制 [#52]
16. 审查并修复 166 个裸 except 块 [#25]
17. 实现缺失模块 (event_log, cost_tracker 等) [#16]
18. 统一重复的停用词定义 [#19]

### 项目整体评价

**共发现 68 个问题：8 严重、19 高、26 中、15 低。**

**优点:**
- 安全防护意识较强（SSRF 检查、路径遍历保护、shlex.quote 转义、Docker 沙箱隔离）
- 整体架构设计良好，23 阶段管道清晰分离
- 错误处理模式成熟（显式检查、优雅降级）
- 配置系统完善，支持多种提供商和实验模式

**关键风险:**
1. **凭据安全:** HF_TOKEN 传递到 LLM 生成的代码容器中，API 密钥可在配置文件中明文存储
2. **权限过高:** `--dangerously-skip-permissions` + `--approve-all` 允许 LLM 生成的代码执行任意系统命令
3. **功能缺失:** 4 个核心模块 (event_log, cost_tracker, experiment_spec, pitfall_detector) 文件不存在，静默禁用关键管道功能
4. **资源泄漏:** AnthropicAdapter httpx 连接池永不关闭
5. **测试空白:** `_helpers.py` (50+ 函数) 无专门测试
