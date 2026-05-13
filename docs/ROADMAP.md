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
| Topic trend validation | Prompt-level support | Topic prompt requires recent work and benchmark context |
| Multi-seed enforcement | Prompt-level support | Code generation guidance and quality checks |
| RL step guidance | Prompt-level support | RL topics receive minimum training-step guidance |

## Open High-Value Gaps

| Item | Priority | Notes |
| --- | --- | --- |
| Distributed launcher execution | Medium | Config and code-generation guidance exist; execution backends do not yet launch `torchrun`/DeepSpeed directly. |

## Suggested Next Order

1. Add execution-backend launch support for `torchrun`, `accelerate`, and DeepSpeed when `experiment.distributed.enabled=true`.
