# Roadmap And Remaining Work

This roadmap replaces the older V8 iteration plan. It tracks the current
repository state after the BenchmarkAgent, CodeAgent, live framework docs, and
reproducibility updates were integrated.

## Completed Or Integrated

| Item | Status | Evidence |
| --- | --- | --- |
| F-02 Advanced CodeAgent | Integrated | `researchclaw/pipeline/code_agent.py`; Stage 10 CodeAgent path in `_code_generation.py` |
| F-01 Static framework docs | Integrated | `researchclaw/data/framework_docs/`; `load_framework_docs()` |
| F-01 live framework docs | Integrated, opt-in | `researchclaw/literature/framework_docs.py`; `experiment.framework_doc_fetch` |
| F-01 Context7 MCP fallback | Integrated, optional | `researchclaw/mcp/context7_client.py` |
| MCP literature search tool | Integrated | `search_literature` now calls `researchclaw.literature.search_papers()` and returns serialized paper metadata instead of a fixed stub response |
| MCP paper review tool | Integrated | `review_paper` now reads the target manuscript and returns offline structural checks for word count, sections, citations, missing sections, and issues |
| MCP trackable pipeline requests | Integrated | `run_pipeline` now creates a real `artifacts/rc-*` run directory with `checkpoint.json` and `progress.json` so MCP clients can follow the request via `get_pipeline_status` |
| MCP local client dispatch | Integrated | `MCPClient` now supports a local ResearchClaw server backend for tool discovery and tool calls; MCP transport docstrings reflect the implemented queue-backed SSE semantics |
| P4.1 Benchmark discovery | Integrated | `researchclaw/agents/benchmark_agent/`; Stage 9 benchmark plan |
| P4.1 non-ML benchmark support | Integrated | `researchclaw/data/benchmark_knowledge.yaml`; domain-aware survey/acquire paths |
| P4.2 reproducibility artifacts | Integrated | `researchclaw/experiment/environment.py`; Stage 12 writes environment metadata and requirements |
| Q22 train/validation overlap detection | Integrated | `check_data_split_overlap()` in `researchclaw/experiment/validator.py`; covered by validator tests |
| Q23 loss direction detection | Integrated | `check_loss_direction()` flags wrong-sign metric, error, and penalty terms in generated losses |
| Q25 capacity fairness detection | Integrated | `check_capacity_fairness()` detects obvious proposed-vs-baseline model capacity mismatches |
| General domain profiles | Integrated | Added `chemistry_general`, `biology_general`, `mathematics_general`, and `economics_general` YAML profiles |
| MCP SSE transport | Integrated | `SSETransport` now supports queued JSON-RPC receive and SSE data-frame send semantics |
| WebSocket synchronous HITL input | Integrated | `WebSocketHITLAdapter.collect_input()` now uses waiting/response file IPC for blocking pipeline callbacks |
| Recommendation systems domain | Integrated | Added `ml_recommendation` detector rules and domain profile |
| Conference template expansion | Integrated | Added CVPR, ACL, AAAI, KDD, Nature-style, and Science-style export templates |
| Multilingual paper generation | Integrated | Added `export.paper_language` and Stage 17 language instructions for manuscript prose |
| Multi-GPU training guidance | Integrated | Added `experiment.distributed` config and Stage 10 DeepSpeed/FSDP/torchrun guidance with single-GPU fallback |
| Distributed launcher execution | Integrated | Docker and SSH sandboxes now launch `torchrun`, `accelerate launch`, or DeepSpeed when `experiment.distributed.enabled=true` |
| Parallel hypothesis branch planning | Integrated | Stage 8 can write `hypothesis_branches.json`; runner prepares per-branch Stage 8 contexts and `branches/branch_manifest.json` |
| Pipeline parallel hypothesis fan-out | Integrated | Runner executes Stage 9-15 per prepared branch, selects the best branch by configured metric, and promotes its artifacts for paper writing |
| Structured progress observability | Integrated | Runner writes `progress.json` snapshots after each stage; dashboard collector reads stage status, counts, elapsed time, and cost |
| Fine-grained cost accounting | Integrated | `cost_log.jsonl` is aggregated into `cost_summary.json` by stage and model; progress snapshots include token and spend totals |
| Cost forecast calibration | Integrated | `cost_summary.json` now includes built-in provider/model price estimates plus forecast-vs-actual cost variance totals by run, stage, and model |
| Provider price table maintenance | Integrated | Token price presets now live in `researchclaw/data/provider_prices.json`; `load_price_table()` lets tests and future refreshes update pricing without touching aggregation logic |
| Topic trend validation | Prompt-level support | Topic prompt requires recent work and benchmark context |
| Multi-seed enforcement | Prompt-level support | Code generation guidance and quality checks |
| RL step guidance | Prompt-level support | RL topics receive minimum training-step guidance |

## Open High-Value Gaps

| Item | Priority | Notes |
| --- | --- | --- |
| None currently tracked | - | Continue using `audit_report.md` and future iteration plans for newly discovered issues. |

## Suggested Next Order

1. Continue sweeping `audit_report.md` follow-up batches, especially remaining broad exception logging and MCP server stub behavior.
