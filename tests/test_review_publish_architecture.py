"""Architecture checks for review/publish stage implementation modules."""

from __future__ import annotations


def test_review_publish_stage_functions_live_in_focused_modules() -> None:
    from researchclaw.pipeline.stage_impls import _publish, _review, _revision

    assert _review._execute_peer_review.__module__.endswith("._review")

    assert _revision._execute_paper_revision.__module__.endswith("._revision")
    assert _revision._execute_quality_gate.__module__.endswith("._revision")

    assert _publish._execute_knowledge_archive.__module__.endswith("._publish")
    assert _publish._execute_export_publish.__module__.endswith("._publish")
    assert _publish._execute_citation_verify.__module__.endswith("._publish")


def test_review_publish_facade_keeps_legacy_stage_imports() -> None:
    from researchclaw.pipeline.stage_impls import _review_publish

    assert _review_publish._execute_peer_review.__module__.endswith("._review")
    assert _review_publish._execute_paper_revision.__module__.endswith("._revision")
    assert _review_publish._execute_quality_gate.__module__.endswith("._revision")
    assert _review_publish._execute_knowledge_archive.__module__.endswith("._publish")
    assert _review_publish._execute_export_publish.__module__.endswith("._publish")
    assert _review_publish._execute_citation_verify.__module__.endswith("._publish")


def test_publish_module_logger_uses_its_module_name() -> None:
    from researchclaw.pipeline.stage_impls import _publish

    assert _publish.logger.name == "researchclaw.pipeline.stage_impls._publish"
