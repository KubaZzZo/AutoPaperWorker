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
| Topic trend validation | Prompt-level support | Topic prompt requires recent work and benchmark context |
| Multi-seed enforcement | Prompt-level support | Code generation guidance and quality checks |
| RL step guidance | Prompt-level support | RL topics receive minimum training-step guidance |

## Open High-Value Gaps

| Item | Priority | Notes |
| --- | --- | --- |
| Q23 loss direction detection | High | Add checks that loss terms penalize the intended failure mode and respect metric direction. |
| Q25 capacity fairness detection | Medium | Detect unfair parameter-count/model-capacity differences across compared methods. |
| MCP SSE transport | Medium | `SSETransport.receive()` is still a stub. |
| WebSocket synchronous HITL input | Medium | `WebSocketHITLAdapter.collect_input()` is intentionally not implemented. |
| General domain profiles | Medium | Detector emits `chemistry_general`, `biology_general`, `mathematics_general`, `economics_general`; matching YAML profiles are still missing. |
| Recommendation systems domain | Medium | Add `ml_recommendation` detector rules, profile, and skill coverage. |
| Multi-GPU training | Medium | DeepSpeed/FSDP support is not wired into the main experiment path. |
| Conference template expansion | Low | Add CVPR, ACL, AAAI, KDD, Nature/Science-style templates. |
| Multilingual paper generation | Low | Docs are localized; paper generation remains English-first. |

## Suggested Next Order

1. Add Q22/Q23/Q25 programmatic experiment quality checks.
2. Add the four missing `*_general` domain profiles.
3. Implement MCP SSE transport for remote tool use.
4. Implement or document the WebSocket synchronous HITL path.
5. Add `ml_recommendation` domain support.
6. Expand conference templates.
