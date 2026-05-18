# AutoPaperWorker (ResearchClaw) 项目全面审计报告

> 审计日期: 2026-05-17 | 分支: `workbench-implementation` | 版本: 0.3.1  
> 项目总行数: ~79,583 行 Python 代码

## 修复进度（2026-05-18）

- <span style="color: green">✅ FIXED: Stage 17 论文草稿构建中的 `_re_q` 未定义错误已修复，并恢复 `_paper_writing._detect_domain` 兼容入口，避免无指标实验绕过经验领域硬阻断。</span>
- <span style="color: green">✅ FIXED: Review / revision / quality gate 的 fallback 诊断日志已同步写入历史兼容 logger，日志断言和线上排障路径一致。</span>
- <span style="color: green">✅ FIXED: Web pipeline 后台执行器已从 `asyncio.get_event_loop()` 迁移到 `asyncio.get_running_loop()`，补充 Python 3.12 兼容回归测试。</span>
- <span style="color: green">✅ VERIFIED: 全量测试已通过 `3316 passed, 7 skipped`；新增 Web 路由回归用例单独通过。</span>

---

## 目录

1. [严重程度概览](#严重程度概览)
2. [CRITICAL — 必须立即修复](#critical--必须立即修复)
3. [HIGH — 合并前应修复](#high--合并前应修复)
4. [MEDIUM — 建议修复](#medium--建议修复)
5. [LOW — 可选优化](#low--可选优化)
6. [结构性问题](#结构性问题)
7. [安全审计](#安全审计)
8. [性能问题](#性能问题)
9. [测试与覆盖率](#测试与覆盖率)
10. [正面评价](#正面评价)
11. [修复优先级路线图](#修复优先级路线图)

---

## 严重程度概览

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| CRITICAL | 2 | 运行时崩溃风险 |
| HIGH | 5 | 兼容性/逻辑错误 |
| MEDIUM | 11 | 代码质量/可维护性 |
| LOW | 5 | 风格/小问题 |
| 结构性 | 3 | 架构层面 |

---

## CRITICAL — 必须立即修复

### C1. ZeroDivisionError — `_publish.py:256`

**文件**: `researchclaw/pipeline/stage_impls/_publish.py` 第 256 行  
**问题**: `_is_verified()` 闭包中的复合布尔表达式存在除零错误。

```python
# 当 v == 0.0 且 num 不近似零时，第一个子句 abs(num - v) / abs(v) 会触发 ZeroDivisionError
abs(num - v) / abs(v) <= 0.01 or v != 0.0 and abs(num / 100.0 - v) / abs(v) <= 0.01 ...
```

由于 `and` 优先级高于 `or`，`v != 0.0` 的守卫只保护了后面的子句，第一个 `abs(v)` 除法不受保护。当 `v == 0.0` 且 `abs(num) >= 1e-9` 时，上面的 `if v == 0.0` 守卫会 fall through 到这里。

**修复方案**:
```python
def _is_verified(num: float) -> bool:
    for v in verified_values:
        if v == 0.0:
            if abs(num) < 1e-9:
                return True
            continue  # 无法与 v==0 做比率比较
        rel_tol = 0.01
        if abs(num - v) / abs(v) <= rel_tol:
            return True
        if abs(num / 100.0 - v) / abs(v) <= rel_tol:
            return True
        if abs(num - v * 100.0) / abs(v * 100.0) <= rel_tol:
            return True
    return False
```

---

### C2. Python 3.12 兼容性崩溃 — `transport.py:32`

**文件**: `researchclaw/mcp/transport.py` 第 32 行  
**问题**: `StdioTransport.start()` 使用了 `asyncio.get_event_loop()`（Python 3.10+ 已弃用），并将 `loop` 显式传给 `StreamWriter`（Python 3.12 已移除该参数）。

在 Python 3.12 上运行将抛出 `TypeError`。

**修复方案**:
```python
async def start(self) -> None:
    loop = asyncio.get_running_loop()  # 替换 get_event_loop()
    self._reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(self._reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout
    )
    self._writer = asyncio.StreamWriter(w_transport, w_protocol, self._reader, loop)
```

---

## HIGH — 合并前应修复

### H1. 702 行巨型函数 — `_analysis.py:62`

**文件**: `researchclaw/pipeline/stage_impls/_analysis.py`  
**问题**: `_execute_result_analysis` 函数长达 702 行，内部嵌套定义了辅助函数 `_get_best_sandbox`，包含 mid-function imports，使用模块级命名约定（`_it`, `_k`, `_v`）作为局部变量名。

**建议**: 拆分为至少 5 个子函数:
- `_merge_refinement_log()`
- `_compute_bootstrap_ci()`
- `_detect_ablation_failures()`
- `_select_best_sandbox()`
- `_build_analysis_summary()`

---

### H2. 440 行嵌套闭包 — `_publish.py:169`

**文件**: `researchclaw/pipeline/stage_impls/_publish.py`  
**问题**: `_sanitize_fabricated_data` 函数 440 行，内含 3 层嵌套闭包，两次导入同一模块 (`import re as _re_san` 和 `import re as _re_san2`)。

---

### H3. sys.modules 猴子补丁模式 — `_publish.py:53`

**文件**: `researchclaw/pipeline/stage_impls/_publish.py` 第 53 行  
**问题**: `_get_collect_raw_experiment_metrics` 和 `_get_review_compiled_pdf` 通过 `sys.modules` 进行自引用猴子补丁调度，用于解决循环导入。该模式依赖导入顺序，模块 reload 时静默失败。

**建议**: 将共享函数提取到独立的工具模块，消除循环依赖。

---

### H4. 动态类型伪造 — `_synthesis.py:149`

**文件**: `researchclaw/pipeline/stage_impls/_synthesis.py` 第 149 行  
**问题**: 使用 `type('IC', (), {...})` 动态创建伪造的 dataclass 实例替代真正的 `IdeaCandidate`。完全无类型标注，`isinstance` 检查会失败，缺失属性时错误被宽泛的 `except` 静默吞掉。

**建议**: 导入并实例化真正的 `IdeaCandidate` 数据类。

---

### H5. Bootstrap CI 固定种子 — `_analysis.py:320`

**文件**: `researchclaw/pipeline/stage_impls/_analysis.py` 第 328 行  
**问题**: Bootstrap 置信区间计算使用固定种子 `Random(42)`，导致 CI 宽度不反映真实采样变异性。对于科学论文生成，这可能产生误导性的统计结果。

**建议**: 使用 `random.Random()` 无种子或基于数据的种子。

---

## MEDIUM — 建议修复

### M1. 函数内部导入标准库

| 文件 | 行号 | 导入 |
|------|------|------|
| `_analysis.py` | 320, 327, 374, 381 | `statistics`, `random`, `math` |
| `_publish.py` | 169, 1322 | `re` (两次不同别名) |
| `config/parsing.py` | 85 | `math` (每次 `_safe_float` 调用) |
| `cli.py` | 356 | `logging` |

**建议**: 将所有标准库导入移至模块顶层。

---

### M2. 线程安全问题 — `acp_client.py:71`

**文件**: `researchclaw/llm/acp_client.py`  
**问题**: `ACPClient._live_instances` 类级别可变列表在 `__init__` 中修改时无锁保护。多线程并发创建实例时，prune + append 序列不是原子操作。

**建议**: 添加 `threading.Lock`。

---

### M3. `__del__` 异常处理缺失 — `context7_client.py:316`

**文件**: `researchclaw/mcp/context7_client.py`  
**问题**: `__del__` 直接调用 `self._stop()` 没有 try/except 包裹。异常在 `__del__` 中会被静默忽略。

---

### M4. noqa F401 重复导入模式 — 多个文件

**文件**: `_publish.py`, `_review.py`, `_revision.py`  
**问题**: 三个文件各自导入相同的符号并标记 `noqa: F401`（声称 "available for downstream use"），属于隐式重导出反模式。

**建议**: 创建显式 `__all__` 或让调用方直接从定义模块导入。

---

### M5. O(n²) 迭代 — `_analysis.py:414`

**文件**: `researchclaw/pipeline/stage_impls/_analysis.py`  
**问题**: 消融失败检测使用双层 range-index 循环，应使用 `itertools.combinations`。还包含冗余的 `isinstance` 检查。

---

### M6. 异常静默吞噬 — `cli.py:47`

**文件**: `researchclaw/cli.py`  
**问题**: `_is_opencode_installed()` 捕获 bare `Exception` 并返回 `False`，无任何日志输出。权限错误或 PATH 配置问题会被误报为 "未安装"。

---

### M7. Logger 名称错误 — `_publish.py:44`

**文件**: `researchclaw/pipeline/stage_impls/_publish.py`  
**问题**: Logger 使用硬编码名称 `'researchclaw.pipeline.stage_impls._review_publish'`，而非 `__name__`（实际为 `._publish`）。日志输出中模块路径错误。

---

## LOW — 可选优化

### L1. yaml 可能为死导入 — `_literature.py:14`

如果 `_literature.py` 中没有直接使用 `yaml.`，则该导入为冗余。

### L2. print/logging 混用 — `cli.py`

CLI 模块同时使用 `print()` 和 `logging.getLogger()`，应统一使用 logging。

### L3. 命名不一致

`_analysis.py` 中大量使用 `_` 前缀命名局部变量（`_it`, `_k`, `_v`, `_sbx`），与 Python 惯例中表示模块私有的含义冲突。

### L4. 重复的 re 别名 — `_publish.py`

同一函数内 `import re as _re_san` 和 `import re as _re_san2` — 同一模块两个别名。

### L5. `_literature.py` yaml 导入

yaml 在顶层导入但可能仅被导入的 helper 使用，非本文件直接使用。

---

## 结构性问题

### S1. 超大文件（>800 行限制）

| 文件 | 行数 | 超标倍数 |
|------|------|----------|
| `pipeline/stage_impls/_publish.py` | 2,072 | 2.6x |
| `pipeline/stage_impls/_paper_writing.py` | 1,834 | 2.3x |
| `pipeline/code_agent.py` | 1,519 | 1.9x |
| `experiment/validator.py` | 1,475 | 1.8x |
| `cli.py` | 1,442 | 1.8x |
| `pipeline/stage_impls/_code_generation.py` | 1,387 | 1.7x |
| `pipeline/runner.py` | 1,373 | 1.7x |
| `pipeline/_helpers.py` | 1,244 | 1.6x |
| `pipeline/stage_impls/_execution.py` | 1,088 | 1.4x |
| `prompt_defaults/stage_prompts.py` | 1,054 | 1.3x |
| `agents/figure_agent/codegen.py` | 1,004 | 1.3x |
| `literature/verify.py` | 959 | 1.2x |
| `pipeline/stage_impls/_analysis.py` | 941 | 1.2x |
| `pipeline/experiment_repair.py` | 940 | 1.2x |
| `pipeline/executor.py` | 863 | 1.1x |
| `templates/compiler.py` | 857 | 1.1x |
| `pipeline/stage_impls/_literature.py` | 848 | 1.1x |
| `pipeline/opencode_bridge.py` | 839 | 1.0x |
| `experiment/docker_sandbox.py` | 821 | 1.0x |

**共 19 个文件超过 800 行限制**。最严重的 `_publish.py` (2,072 行) 包含至少 4 个独立逻辑关注点。

**建议拆分方案**:
- `_publish.py` → `_stage21_archive.py`, `_stage22_export.py`, `_stage23_citations.py`, `_fabrication_sanitizer.py`
- `_paper_writing.py` → 按论文章节拆分
- `cli.py` → `cli_commands.py`, `cli_utils.py`

---

### S2. 循环导入回避方式不当

`_publish.py` 使用 `sys.modules` 自引用调度来回避循环导入，应通过重构模块依赖图来解决。

---

### S3. mid-function import 蔓延

至少 6 个文件在函数体内导入标准库模块。这些都是零成本的标准库模块，没有延迟导入的理由。

---

## 安全审计

### 无硬编码密钥 ✅

全局搜索 `password|secret|api_key|token` 赋值模式未发现硬编码凭证。

### subprocess 调用审查

发现 **25+ 处** `subprocess.run/Popen/call` 调用，分布在:

| 文件 | 调用数 | 风险评估 |
|------|--------|----------|
| `llm/acp_client.py` | 6 | 中 — 需确认命令参数不含用户输入 |
| `experiment/agentic_sandbox.py` | 6 | 高 — 沙箱执行，需确认隔离 |
| `agents/figure_agent/renderer.py` | 4 | 中 — 图表渲染命令 |
| `cli.py` | 3 | 低 — 版本检查等内部命令 |
| `overleaf/sync.py` | 2 | 中 — Git 操作 |
| `mcp/context7_client.py` | 1 (Popen) | 中 — 长运行子进程 |

### env.py 安全工具 ✅

`researchclaw/utils/env.py` 的 `minimal_subprocess_env` 使用 frozenset 白名单过滤环境变量，主动排除类似密钥的变量名 — 设计良好。

### experiment/validator.py ✅

代码验证器维护了 `subprocess.call`, `subprocess.run`, `subprocess.Popen`, `subprocess.check_output` 的黑名单 — 用于检测实验代码中的危险调用。

---

## 性能问题

### P1. 每次调用重复导入 — `config/parsing.py:85`

`_safe_float` 函数每次调用都执行 `import math`。虽然 Python 缓存模块，但 `sys.modules` 查找在配置加载时可能被调用 20+ 次。

### P2. O(n²) 消融比较 — `_analysis.py:414`

双层循环应替换为 `itertools.combinations(cond_names, 2)`。

### P3. 固定种子统计采样 — `_analysis.py:328`

Bootstrap CI 使用固定种子，每次产生相同的 "随机" 样本，CI 宽度不反映真实数据分布。

---

## 测试与覆盖率

### 测试基础设施 ✅

- 使用 `pytest` + `pytest-asyncio` + `pytest-cov`
- 配置了覆盖率报告 (`--cov=researchclaw`)
- 定义了 `integration`, `slow`, `live_api` markers
- 100+ 测试文件

### 待确认

- 当前实际覆盖率百分比（需运行 `pytest` 确认是否达到 80% 目标）
- 新增的 `researchclaw/utils/env.py` 和 `tests/test_subprocess_env.py` 是未跟踪文件（`??`），尚未提交

---

## 正面评价

| 方面 | 评价 |
|------|------|
| 配置层不可变性 | 所有 config dataclass 使用 `frozen=True`，`RCConfig` 提供 `with_research_overrides()` 返回新实例 |
| env 过滤工具 | `minimal_subprocess_env` 使用 frozenset 白名单 + 密钥名过滤 |
| ACPClient 生命周期管理 | 正确的 atexit 清理、弱引用追踪、幂等 close() |
| voice 路由上传限制 | 同时检查 Content-Length 头和流式读取大小 |
| YAML NaN/Inf 防护 | `_safe_int/_safe_float` 正确拒绝 NaN/Inf |
| Context7 JSON-RPC | 正确处理请求 ID 不匹配（丢弃陈旧响应） |
| 测试套件 | 100+ 测试文件，覆盖全面 |

---

## 修复优先级路线图

### Phase 1 — 紧急（阻塞合并）
1. 修复 `_publish.py:256` ZeroDivisionError
2. 修复 `transport.py:32` Python 3.12 兼容性

### Phase 2 — 高优先级（合并前建议完成）
3. 拆解 `_execute_result_analysis` 702 行巨型函数
4. 移除 sys.modules 猴子补丁模式
5. 替换 `type()` 伪造 dataclass

### Phase 3 — 中优先级（近期迭代）
6. 将所有标准库 mid-function import 移至模块顶层
7. 添加 `ACPClient._live_instances` 线程锁
8. 修复 Logger 名称错误
9. 清理 noqa F401 重导出模式
10. 为 `_is_opencode_installed` 添加 debug 日志

### Phase 4 — 结构重构（规划后执行）
11. 拆分 19 个超大文件（从 `_publish.py` 2,072 行开始）
12. 统一 CLI 模块的 print/logging 使用
13. 规范化局部变量命名约定

---

> 报告由 Claude Opus 4.6 自动生成 | 审计范围: researchclaw/ + tests/
