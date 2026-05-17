"""Tree-search and review mixin for CodeAgent."""

from __future__ import annotations

from researchclaw.pipeline.code_agent_models import SolutionNode, _SimpleResult


class CodeAgentSearchReviewMixin:
    def _phase3_tree_search(
        self,
        topic: str,
        exp_plan: str,
        metric: str,
        pkg_hint: str,
        arch_spec: str,
        max_tokens: int,
    ) -> tuple[SolutionNode, int]:
        """Explore multiple candidate solutions via tree search."""
        self._log_event("Phase 3: Solution tree search")
        all_nodes: list[SolutionNode] = []

        # Generate initial candidates
        n_cand = max(self._cfg.tree_search_candidates, 1)
        for k in range(n_cand):
            self._log_event(f"  Generating candidate {k + 1}/{n_cand}")
            files = self._generate_code(
                topic, exp_plan, metric, pkg_hint, arch_spec, max_tokens,
            )
            node = SolutionNode(
                node_id=f"gen-{k}",
                files=files,
                depth=0,
                generation_method="initial",
            )
            all_nodes.append(node)

        # Iterative evaluate-fix-branch loop
        for depth in range(self._cfg.tree_search_max_depth):
            # Evaluate unevaluated nodes
            for node in all_nodes:
                if not node.evaluated:
                    self._evaluate_node(node, metric)

            # Sort by score
            all_nodes.sort(key=lambda n: n.score, reverse=True)

            self._log_event(
                f"  Depth {depth}: {len(all_nodes)} nodes, "
                f"best={all_nodes[0].node_id} score={all_nodes[0].score:.2f}"
            )

            # If best runs OK, we're done
            if all_nodes[0].runs_ok:
                break

            # Generate fix variants for top-2 crashing candidates
            new_nodes: list[SolutionNode] = []
            for node in all_nodes[:2]:
                if not node.runs_ok:
                    fixed_files = self._fix_runtime_error(
                        node.files,
                        _SimpleResult(
                            returncode=node.returncode,
                            stdout=node.stdout,
                            stderr=node.stderr,
                        ),
                    )
                    new_node = SolutionNode(
                        node_id=f"{node.node_id}-fix{depth}",
                        files=fixed_files,
                        parent_id=node.node_id,
                        depth=depth + 1,
                        generation_method="fix",
                    )
                    new_nodes.append(new_node)

            all_nodes.extend(new_nodes)

        # Final evaluation of any remaining unevaluated nodes
        for node in all_nodes:
            if node.returncode == -1:
                self._evaluate_node(node, metric)

        all_nodes.sort(key=lambda n: n.score, reverse=True)
        best = all_nodes[0]
        self._log_event(
            f"  Tree search complete: best={best.node_id} "
            f"score={best.score:.2f}, explored {len(all_nodes)} nodes"
        )

        return best, len(all_nodes)

    def _evaluate_node(self, node: SolutionNode, metric_key: str) -> None:
        """Run a node's code in sandbox and update its score."""
        if not node.files:
            node.score = 0.0
            return

        result = self._run_in_sandbox(
            node.files,
            timeout_sec=self._cfg.tree_search_eval_timeout_sec,
        )
        node.evaluated = True
        node.returncode = result.returncode
        node.stdout = result.stdout
        node.stderr = result.stderr
        node.runs_ok = result.returncode == 0
        node.metrics = dict(result.metrics) if result.metrics else {}
        node.score = self._score_node(node, metric_key)

    @staticmethod
    def _score_node(node: SolutionNode, metric_key: str) -> float:
        """Score a solution node based on execution results."""
        score = 0.0
        if node.runs_ok:
            score += 1.0
        if node.stdout and len(node.stdout) > 100:
            score += 0.3  # produces meaningful output
        if node.metrics:
            score += 0.5
            if metric_key in node.metrics:
                score += 0.5
        if node.stderr and "Error" in node.stderr:
            score -= 0.2
        return max(score, 0.0)

    def _phase4_review(
        self,
        files: dict[str, str],
        topic: str,
        exp_plan: str,
        metric: str,
    ) -> tuple[dict[str, str], int]:
        """Reviewer agent examines code; coder fixes critical issues."""
        self._log_event("Phase 4: Review dialog")

        rounds = 0
        for r in range(self._cfg.review_max_rounds):
            rounds += 1
            files_ctx = self._format_files(files)

            sp = self._pm.sub_prompt(
                "code_reviewer",
                topic=topic,
                exp_plan=exp_plan,
                metric=metric,
                files_context=files_ctx,
            )
            resp = self._chat(sp.system, sp.user, max_tokens=4096)

            review = self._parse_json(resp.content)
            if not isinstance(review, dict) or not review:
                self._log_event(
                    f"  Review round {r + 1}: could not parse JSON, skipping"
                )
                break

            verdict = review.get("verdict", "APPROVE")
            score = review.get("score", 10)
            critical = review.get("critical_issues", [])

            self._log_event(
                f"  Review round {r + 1}: verdict={verdict}, score={score}, "
                f"critical_issues={len(critical)}"
            )

            if verdict == "APPROVE" or not critical:
                break

            # Fix critical issues using the code_generation system prompt
            fix_prompt = (
                "A code reviewer found these critical issues in your experiment code.\n"
                "Fix ALL of them while preserving the experiment design.\n\n"
                "## Critical Issues\n"
                + "\n".join(f"- {issue}" for issue in critical)
                + f"\n\n## Current Code\n{files_ctx}\n\n"
                "Output ALL files in ```filename:xxx.py``` format, "
                "including unchanged files."
            )
            sys_prompt = self._pm.system("code_generation")
            fix_resp = self._chat(sys_prompt, fix_prompt, max_tokens=16384)

            fixed = self._extract_files(fix_resp.content)
            if fixed:
                files = dict(files)
                files.update(fixed)

        return files, rounds
