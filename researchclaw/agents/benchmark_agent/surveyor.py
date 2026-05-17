"""Surveyor Agent — searches for domain-relevant benchmarks and baselines.

Data sources (in priority order):
1. Local ``benchmark_knowledge.yaml`` — always available, no network.
2. HuggingFace Hub API (``huggingface_hub``) — dataset discovery by task/keyword.
3. LLM fallback — asks the LLM to suggest benchmarks when APIs unavailable.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from researchclaw.agents.base import AgentStepResult, BaseAgent

logger = logging.getLogger(__name__)

_KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "benchmark_knowledge.yaml"

# ---------------------------------------------------------------------------
# HuggingFace Hub helpers (optional dependency)
# ---------------------------------------------------------------------------

def _load_hf_api() -> tuple[bool, Any | None]:
    """Load the optional HuggingFace Hub API class."""
    try:
        module = importlib.import_module("huggingface_hub")
        return True, module.HfApi
    except ImportError as exc:
        logger.debug(
            "HuggingFace Hub optional dependency unavailable: %s",
            exc,
            exc_info=True,
        )
        return False, None


_HF_AVAILABLE, HfApi = _load_hf_api()

# Mapping from our domain keywords to HuggingFace task_categories filters
_DOMAIN_TO_HF_TASK: dict[str, list[str]] = {
    "image_classification": ["image-classification"],
    "text_classification": ["text-classification", "sentiment-analysis"],
    "language_modeling": ["text-generation"],
    "question_answering": ["question-answering"],
    "generative_models": ["unconditional-image-generation"],
    "graph_neural_networks": ["graph-ml"],
    "reinforcement_learning": ["reinforcement-learning"],
    "tabular_learning": ["tabular-classification", "tabular-regression"],
    "llm_finetuning": ["text-generation"],
}


class SurveyorAgent(BaseAgent):
    """Searches local knowledge base and HuggingFace Hub for benchmarks."""

    name = "surveyor"

    def __init__(
        self,
        llm: Any,
        *,
        enable_hf_search: bool = True,
        max_hf_results: int = 10,
    ) -> None:
        super().__init__(llm)
        self._enable_hf = enable_hf_search and _HF_AVAILABLE
        self._max_hf = max_hf_results
        self._knowledge = self._load_knowledge()

    # -- Knowledge base ----------------------------------------------------

    @staticmethod
    def _load_knowledge() -> dict[str, Any]:
        """Load the local benchmark knowledge base."""
        try:
            data = yaml.safe_load(_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
            return data.get("domains", {}) if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            logger.warning("Failed to load benchmark_knowledge.yaml", exc_info=True)
            return {}

    def _match_domains(self, topic: str) -> list[str]:
        """Return domain IDs whose keywords appear in the topic."""
        topic_lower = topic.lower()
        matched: list[str] = []
        for domain_id, info in self._knowledge.items():
            keywords = info.get("keywords", [])
            for kw in keywords:
                if kw in topic_lower:
                    matched.append(domain_id)
                    break
        return matched

    def _get_local_candidates(self, domain_ids: list[str]) -> dict[str, Any]:
        """Retrieve benchmarks and baselines from local knowledge base."""
        benchmarks: list[dict[str, Any]] = []
        baselines: list[dict[str, Any]] = []
        seen_bench: set[str] = set()
        seen_base: set[str] = set()

        for did in domain_ids:
            info = self._knowledge.get(did, {})
            for b in info.get("standard_benchmarks", []):
                name = b.get("name", "")
                if name not in seen_bench:
                    seen_bench.add(name)
                    benchmarks.append({**b, "source_domain": did, "origin": "knowledge_base"})
            for bl in info.get("common_baselines", []):
                name = bl.get("name", "")
                if name not in seen_base:
                    seen_base.add(name)
                    baselines.append({**bl, "source_domain": did, "origin": "knowledge_base"})

        return {"benchmarks": benchmarks, "baselines": baselines}

    # -- HuggingFace Hub ---------------------------------------------------

    def _search_hf_datasets(self, topic: str, domain_ids: list[str]) -> list[dict[str, Any]]:
        """Search HuggingFace Hub for relevant datasets."""
        if not self._enable_hf:
            return []

        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        try:
            api = HfApi()

            # Strategy 1: Search by task category
            for did in domain_ids:
                for task_cat in _DOMAIN_TO_HF_TASK.get(did, []):
                    try:
                        datasets = api.list_datasets(
                            filter=[f"task_categories:{task_cat}"],
                            sort="downloads",
                            direction=-1,
                            limit=self._max_hf,
                        )
                        for ds in datasets:
                            if ds.id not in seen:
                                seen.add(ds.id)
                                results.append({
                                    "name": ds.id,
                                    "downloads": getattr(ds, "downloads", 0),
                                    "origin": "huggingface_hub",
                                    "api": f"datasets.load_dataset('{ds.id}', cache_dir='/workspace/data/hf')",
                                    "tier": 2,
                                })
                    except Exception:  # noqa: BLE001
                        logger.debug("HF task search failed for %s", task_cat)

            # Strategy 2: Keyword search on topic
            keywords = self._extract_search_keywords(topic)
            for kw in keywords[:3]:
                try:
                    datasets = api.list_datasets(
                        search=kw,
                        sort="downloads",
                        direction=-1,
                        limit=self._max_hf,
                    )
                    for ds in datasets:
                        if ds.id not in seen:
                            seen.add(ds.id)
                            results.append({
                                "name": ds.id,
                                "downloads": getattr(ds, "downloads", 0),
                                "origin": "huggingface_hub",
                                "api": f"datasets.load_dataset('{ds.id}', cache_dir='/workspace/data/hf')",
                                "tier": 2,
                            })
                except Exception:  # noqa: BLE001
                    logger.debug("HF keyword search failed for %s", kw)

        except Exception as exc:  # noqa: BLE001
            logger.warning("HuggingFace Hub search failed: %s", exc)

        return results

    @staticmethod
    def _extract_search_keywords(topic: str) -> list[str]:
        """Extract 1-3 word search keywords from a topic string."""
        # Remove common filler words to get meaningful search terms
        stop = {
            "a", "an", "the", "for", "in", "on", "of", "to", "with", "and",
            "or", "is", "are", "using", "via", "based", "towards", "novel",
            "new", "improved", "approach", "method", "methods", "study",
        }
        words = [w.lower().strip(".,;:!?()[]") for w in topic.split()]
        filtered = [w for w in words if w and w not in stop and len(w) > 2]
        # Return 2-3 keyword phrases
        keywords: list[str] = []
        if len(filtered) >= 2:
            keywords.append(" ".join(filtered[:2]))
        if len(filtered) >= 3:
            keywords.append(" ".join(filtered[:3]))
        if filtered:
            keywords.append(filtered[0])
        return keywords

    # -- Domain guidance ---------------------------------------------------

    _DOMAIN_GUIDANCE: dict[str, str] = {
        "ml_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- ML/CV: Use torchvision datasets (CIFAR, ImageNet, MNIST). "
            "Baselines: ResNet, ViT, EfficientNet.\n"
            "- ML/NLP: Use HuggingFace datasets. Baselines: BERT, GPT, T5.\n"
            "- ML/RL: Use Gymnasium envs. Baselines: PPO, SAC, DQN, TD3.\n"
            "- ML/Graph: Use Cora, CiteSeer, ogbn-arxiv. Baselines: GCN, GAT.\n"
        ),
        "physics_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Physics/PDE: Use SYNTHETIC data (Burgers eq, Darcy flow, "
            "Navier-Stokes, heat equation, wave equation). Baselines: FNO, "
            "DeepONet, PINN, spectral methods, finite difference.\n"
            "- Physics/Quantum: Use Heisenberg model, Hubbard model, Ising model. "
            "Baselines: exact diagonalization, DMRG, VQE, coupled cluster.\n"
            "- Physics/Simulation: Use n-body, Lennard-Jones, double pendulum. "
            "Baselines: Velocity Verlet, RK4, leapfrog, symplectic integrators.\n"
        ),
        "chemistry_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Chemistry/Molecular: Use QM9 subset, ESOL, Lipophilicity, Tox21. "
            "Baselines: RF+XGBoost, GCN, SchNet, MPNN, ChemBERTa.\n"
            "- Chemistry/QM: Use H2/H2O PES data. Baselines: HF, DFT/B3LYP, "
            "MP2, CCSD(T), coupled cluster.\n"
        ),
        "biology_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Biology/Genomics: Use synthetic gene expression matrices, "
            "regulatory networks. Baselines: BLAST, GATK, random forest.\n"
            "- Biology/Protein: Use PDB subsets, mutation stability data. "
            "Baselines: ESM-2, ProtBERT, Rosetta, AlphaFold.\n"
            "- Biology/SingleCell: Use PBMC simulated data, cell cycle synthetic. "
            "Baselines: Leiden, Louvain, scanpy, Seurat.\n"
        ),
        "economics_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Economics: Use SYNTHETIC panel/cross-section data. "
            "Baselines: OLS, OLS+controls, fixed effects, 2SLS/IV, "
            "difference-in-differences, RDD.\n"
        ),
        "mathematics_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Mathematics/Numerical: Use Poisson, advection, Helmholtz eqs. "
            "Baselines: Euler, RK4, Adams-Bashforth, Crank-Nicolson, FEM.\n"
            "- Mathematics/Optimization: Use Rosenbrock, Rastrigin, Ackley "
            "functions. Baselines: GD, L-BFGS, Nelder-Mead, Adam, CMA-ES.\n"
        ),
        "neuroscience_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Neuroscience/Computational: Use Izhikevich tuning curves, "
            "Hodgkin-Huxley step response. Baselines: LIF, HH, Izhikevich, "
            "rate-coded models.\n"
            "- Neuroscience/Imaging: Use resting-state correlation matrices, "
            "BOLD synthetic. Baselines: SVM, correlation, atlas parcellation.\n"
        ),
        "robotics_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Robotics/Control: Use pendulum swing-up, cartpole balance, "
            "reacher. Baselines: PPO, SAC, TD3, PID, LQR, MPC.\n"
        ),
        "security_": (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Security/Detection: Use NSL-KDD subset, CICIDS subset, "
            "synthetic attack traces. Baselines: RF, XGBoost, SVM, "
            "isolation forest, one-class SVM.\n"
        ),
    }

    @classmethod
    def _build_domain_guidance(cls, domain_id: str) -> str:
        """Build domain-specific guidance for the LLM fallback prompt."""
        for prefix, guidance in cls._DOMAIN_GUIDANCE.items():
            if domain_id.startswith(prefix):
                return guidance
        return (
            "DOMAIN-SPECIFIC GUIDANCE:\n"
            "- Use synthetic/generated data when real datasets are unavailable.\n"
            "- Baselines should be well-established methods from the literature.\n"
        )

    # -- LLM fallback ------------------------------------------------------

    def _llm_suggest_benchmarks(
        self, topic: str, hypothesis: str, domain_id: str = ""
    ) -> dict[str, Any]:
        """Ask LLM to suggest benchmarks and baselines when APIs unavailable."""
        _domain_guidance = self._build_domain_guidance(domain_id)
        system = (
            "You are an expert researcher. Given a research topic and hypothesis, "
            "suggest appropriate benchmarks, datasets, and baseline methods.\n\n"
            "Return a JSON object with:\n"
            "- benchmarks: array of {name, domain, metrics: [], api (Python one-liner), "
            "  tier (1=pre-cached/generated, 2=downloadable), size_mb}\n"
            "- baselines: array of {name, source (Python code), paper (citation), pip: []}\n"
            "- rationale: string explaining why these are the right choices\n\n"
            "CRITICAL RULES:\n"
            "- Benchmarks and baselines MUST be DOMAIN-APPROPRIATE for the topic.\n"
            "- Do NOT suggest image classification datasets (CIFAR, ImageNet, MNIST) "
            "for non-image topics like PDE solvers, physics, chemistry, etc.\n"
            "- Do NOT suggest optimizers (SGD, Adam, AdamW) as METHOD baselines — "
            "optimizers are training tools, NOT research methods to compare against.\n"
            "- Baselines must be COMPETING METHODS from the same research domain.\n"
            "- If the domain naturally requires SYNTHETIC data (PDE, optimization, "
            "theoretical analysis), explicitly set tier=1 and api='synthetic' and "
            "describe the data generation procedure in the 'note' field.\n\n"
            + _domain_guidance +
            "\n- Prefer well-known, widely-used benchmarks from top venues\n"
            "- Include at least 2 datasets and 2 baselines"
        )
        user = (
            f"Research Topic: {topic}\n"
            f"Hypothesis: {hypothesis}\n\n"
            "Suggest appropriate benchmarks, datasets, and baseline methods. "
            "Make sure they are relevant to the specific domain of this research."
        )
        result = self._chat_json(system, user, max_tokens=4096)
        return result

    # -- Main entry point --------------------------------------------------

    def execute(self, context: dict[str, Any]) -> AgentStepResult:
        """Survey available benchmarks and baselines for the given topic.

        Context keys:
            topic (str): Research topic/title
            hypothesis (str): Research hypothesis
            experiment_plan (str): Experiment plan from previous stages
            domain_id (str, optional): Detected domain profile ID
        """
        topic = context.get("topic", "")
        hypothesis = context.get("hypothesis", "")
        domain_id = context.get("domain_id", "")

        if not topic:
            return self._make_result(False, error="No topic provided")

        self.logger.info("Surveying benchmarks for topic: %s", topic[:80])

        # 1. Match domains from knowledge base
        domain_ids = self._match_domains(topic)
        if hypothesis:
            domain_ids = list(dict.fromkeys(
                domain_ids + self._match_domains(hypothesis)
            ))
        self.logger.info("Matched domains: %s", domain_ids)

        # 2. Get local candidates
        local = self._get_local_candidates(domain_ids)

        # 3. Search HuggingFace Hub — only for ML domains
        hf_datasets: list[dict[str, Any]] = []
        _is_ml = domain_id.startswith("ml_") if domain_id else bool(domain_ids)
        if _is_ml:
            hf_datasets = self._search_hf_datasets(topic, domain_ids)
        else:
            self.logger.info(
                "Non-ML domain '%s' — skipping HuggingFace search", domain_id
            )

        # 4. LLM fallback (used when no local matches, or always for non-ML domains)
        llm_suggestions: dict[str, Any] = {}
        _need_llm = not local["benchmarks"] and not hf_datasets
        if _need_llm or (not _is_ml and not local["benchmarks"]):
            self.logger.info("Falling back to LLM for benchmark suggestions")
            llm_suggestions = self._llm_suggest_benchmarks(topic, hypothesis, domain_id=domain_id)

        # 5. Combine results
        all_benchmarks = local["benchmarks"] + hf_datasets
        if llm_suggestions.get("benchmarks"):
            for b in llm_suggestions["benchmarks"]:
                b["origin"] = "llm_suggestion"
                all_benchmarks.append(b)

        all_baselines = local["baselines"]
        if llm_suggestions.get("baselines"):
            for bl in llm_suggestions["baselines"]:
                bl["origin"] = "llm_suggestion"
                all_baselines.append(bl)

        survey_result = {
            "matched_domains": domain_ids,
            "benchmarks": all_benchmarks,
            "baselines": all_baselines,
            "hf_datasets_found": len(hf_datasets),
            "llm_fallback_used": bool(llm_suggestions),
            "rationale": llm_suggestions.get("rationale", ""),
        }

        self.logger.info(
            "Survey complete: %d benchmarks, %d baselines, %d HF datasets",
            len(all_benchmarks), len(all_baselines), len(hf_datasets),
        )

        return self._make_result(True, data=survey_result)
