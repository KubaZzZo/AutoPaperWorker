"""Data split, objective direction, and capacity-fairness checks."""

from __future__ import annotations

import ast

from researchclaw.experiment.validator_core import _resolve_call_name


def check_data_split_overlap(code: str, fname: str = "main.py") -> list[str]:
    """Detect likely train/validation data leakage in generated code.

    This is a conservative static check for Q22. It flags simple, high-risk
    patterns where train and validation/test loaders or subsets reuse the same
    dataset object or the same Subset indices.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings

    def _name_role(name: str) -> str:
        lowered = name.lower()
        if any(tok in lowered for tok in ("train", "trn")):
            return "train"
        if any(tok in lowered for tok in ("val", "valid", "validation", "test")):
            return "eval"
        return ""

    def _expr_key(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return ast.dump(node)

    loader_sources: dict[str, tuple[str, int]] = {}
    subset_sources: dict[str, tuple[str, str, int]] = {}
    direct_assignments: dict[str, tuple[str, int]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [
            target.id
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        if not targets:
            continue

        value = node.value
        if isinstance(value, ast.Name):
            for target_name in targets:
                direct_assignments[target_name] = (value.id, node.lineno)
            continue

        if not isinstance(value, ast.Call):
            continue

        call_name = _resolve_call_name(value.func)
        if call_name.endswith("DataLoader") and value.args:
            dataset_key = _expr_key(value.args[0])
            for target_name in targets:
                loader_sources[target_name] = (dataset_key, node.lineno)
        elif call_name.endswith("Subset") and len(value.args) >= 2:
            dataset_key = _expr_key(value.args[0])
            indices_key = _expr_key(value.args[1])
            for target_name in targets:
                subset_sources[target_name] = (dataset_key, indices_key, node.lineno)

    def _warn_pair(kind: str, name_a: str, line_a: int, name_b: str, line_b: int, detail: str) -> None:
        warnings.append(
            f"[{fname}:{line_b}] Potential train/validation overlap: "
            f"{kind} '{name_a}' (line {line_a}) and '{name_b}' share {detail}. "
            f"Use disjoint train/val/test splits or separate Subset indices."
        )

    seen_pairs: set[tuple[str, str, str]] = set()

    # Direct aliases such as train_set = dataset; val_set = dataset.
    direct_items = list(direct_assignments.items())
    for i, (name_a, (src_a, line_a)) in enumerate(direct_items):
        role_a = _name_role(name_a)
        if not role_a:
            continue
        for name_b, (src_b, line_b) in direct_items[i + 1:]:
            role_b = _name_role(name_b)
            if not role_b or role_a == role_b or src_a != src_b:
                continue
            pair = tuple(sorted((name_a, name_b))) + ("direct",)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                _warn_pair("dataset alias", name_a, line_a, name_b, line_b, f"dataset object '{src_a}'")

    # DataLoader(train_dataset) and DataLoader(val_dataset) using same source.
    loader_items = list(loader_sources.items())
    for i, (name_a, (src_a, line_a)) in enumerate(loader_items):
        role_a = _name_role(name_a)
        if not role_a:
            continue
        for name_b, (src_b, line_b) in loader_items[i + 1:]:
            role_b = _name_role(name_b)
            if not role_b or role_a == role_b or src_a != src_b:
                continue
            pair = tuple(sorted((name_a, name_b))) + ("loader",)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                _warn_pair("DataLoader", name_a, line_a, name_b, line_b, f"dataset object '{src_a}'")

    # Subset(dataset, indices) reused for train and validation/test.
    subset_items = list(subset_sources.items())
    for i, (name_a, (dataset_a, indices_a, line_a)) in enumerate(subset_items):
        role_a = _name_role(name_a)
        if not role_a:
            continue
        for name_b, (dataset_b, indices_b, line_b) in subset_items[i + 1:]:
            role_b = _name_role(name_b)
            if (
                not role_b
                or role_a == role_b
                or dataset_a != dataset_b
                or indices_a != indices_b
            ):
                continue
            pair = tuple(sorted((name_a, name_b))) + ("subset",)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                warnings.append(
                    f"[{fname}:{line_b}] Potential train/validation overlap: "
                    f"Subset '{name_a}' (line {line_a}) and '{name_b}' use shared "
                    f"Subset indices '{indices_a}' on dataset '{dataset_a}'. "
                    f"Create disjoint train/val/test index lists."
                )

    return warnings


def check_loss_direction(code: str, fname: str = "main.py") -> list[str]:
    """Detect likely wrong-sign terms in generated training losses.

    Q23 focuses on high-confidence static mistakes: adding a metric that should
    be maximized into a minimized loss, or subtracting an error/penalty term.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings

    maximize_tokens = {
        "accuracy", "acc", "f1", "precision", "recall", "auc", "auroc",
        "bleu", "rouge", "meteor", "map", "ndcg", "r2", "score",
    }
    minimize_tokens = {
        "error", "mse", "mae", "rmse", "loss", "penalty", "regularization",
        "reg", "distance", "divergence", "kl", "nll", "cross_entropy",
        "ce",
    }

    def _target_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Tuple | ast.List):
            names: list[str] = []
            for elt in target.elts:
                names.extend(_target_names(elt))
            return names
        return []

    def _contains_loss_name(names: list[str]) -> bool:
        return any("loss" in name.lower() or "objective" in name.lower() for name in names)

    def _term_names(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                names.add(child.id)
            elif isinstance(child, ast.Attribute):
                names.add(child.attr)
        return names

    def _has_token(name: str, tokens: set[str]) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in tokens)

    def _warn(node: ast.AST, term: str, detail: str) -> None:
        warnings.append(
            f"[{fname}:{getattr(node, 'lineno', '?')}] Potential loss direction error: "
            f"'{term}' {detail}. Losses are minimized, so reward/quality metrics "
            f"should usually be subtracted and error/penalty terms added."
        )

    def _scan(node: ast.AST, sign: int, owner: ast.AST) -> None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            _scan(node.left, sign, owner)
            _scan(node.right, sign if isinstance(node.op, ast.Add) else -sign, owner)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            _scan(node.operand, -sign, owner)
            return

        names = _term_names(node)
        if sign > 0:
            bad = sorted(name for name in names if _has_token(name, maximize_tokens))
            if bad:
                _warn(owner, bad[0], "is a higher-is-better metric added to a minimized loss")
        elif sign < 0:
            bad = sorted(name for name in names if _has_token(name, minimize_tokens))
            if bad:
                _warn(owner, bad[0], "is an error/penalty term subtracted from a minimized loss")

    for node in ast.walk(tree):
        value: ast.AST | None = None
        owner: ast.AST = node
        if isinstance(node, ast.Assign):
            target_names = [
                name
                for target in node.targets
                for name in _target_names(target)
            ]
            if _contains_loss_name(target_names):
                value = node.value
        elif isinstance(node, ast.AugAssign):
            target_names = _target_names(node.target)
            if _contains_loss_name(target_names):
                value = ast.BinOp(left=node.target, op=node.op, right=node.value)
        if value is not None:
            _scan(value, 1, owner)

    return warnings


def check_capacity_fairness(code: str, fname: str = "main.py") -> list[str]:
    """Detect obviously unfair model-capacity comparisons.

    Q25 is intentionally conservative: it estimates relative capacity only from
    literal architecture hyperparameters in generated code and warns when a
    proposed method is much larger than a baseline using the same constructor.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return warnings

    proposed_tokens = ("proposed", "ours", "our", "new", "method", "candidate")
    baseline_tokens = ("baseline", "base", "control", "reference", "standard")
    size_keywords = {
        "hidden", "hidden_dim", "dim", "d_model", "embed_dim", "embedding_dim",
        "width", "channels", "num_channels", "layers", "num_layers", "depth",
        "heads", "num_heads", "blocks", "num_blocks", "features",
    }

    def _role(name: str) -> str:
        lowered = name.lower()
        if any(tok in lowered for tok in baseline_tokens):
            return "baseline"
        if any(tok in lowered for tok in proposed_tokens):
            return "proposed"
        return ""

    def _literal_number(node: ast.AST) -> float | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = _literal_number(node.operand)
            return -value if value is not None else None
        return None

    def _capacity(call: ast.Call) -> float | None:
        factors: list[float] = []
        for kw in call.keywords:
            if kw.arg is None:
                continue
            key = kw.arg.lower()
            if not any(tok in key for tok in size_keywords):
                continue
            value = _literal_number(kw.value)
            if value is not None and value > 0:
                factors.append(value)
        if not factors:
            return None
        score = 1.0
        for value in factors:
            score *= value
        return score

    def _call_name(call: ast.Call) -> str:
        return _resolve_call_name(call.func) or "<call>"

    models: list[tuple[str, str, str, float, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    role = _role(target.id)
                    score = _capacity(node.value)
                    if role and score is not None:
                        models.append((target.id, role, _call_name(node.value), score, node.lineno))
                elif isinstance(target, ast.Name) and isinstance(node.value, ast.Dict):
                    for key_node, value_node in zip(node.value.keys, node.value.values, strict=False):
                        if not isinstance(value_node, ast.Call):
                            continue
                        key = ""
                        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                            key = key_node.value
                        role = _role(key)
                        score = _capacity(value_node)
                        if role and score is not None:
                            label = key or target.id
                            models.append((label, role, _call_name(value_node), score, value_node.lineno))

    seen: set[tuple[str, str]] = set()
    proposed = [item for item in models if item[1] == "proposed"]
    baselines = [item for item in models if item[1] == "baseline"]
    for prop_name, _, prop_ctor, prop_score, prop_line in proposed:
        for base_name, _, base_ctor, base_score, base_line in baselines:
            if prop_ctor != base_ctor or base_score <= 0:
                continue
            ratio = prop_score / base_score
            if ratio < 4.0:
                continue
            pair = (prop_name, base_name)
            if pair in seen:
                continue
            seen.add(pair)
            warnings.append(
                f"[{fname}:{prop_line}] Potential capacity fairness issue: "
                f"'{prop_name}' appears about {ratio:.1f}x larger than "
                f"'{base_name}' (line {base_line}) using {prop_ctor}. "
                f"Match parameter counts or report a controlled capacity ablation."
            )

    return warnings
