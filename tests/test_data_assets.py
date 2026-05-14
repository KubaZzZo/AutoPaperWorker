from __future__ import annotations


def test_data_package_exposes_framework_docs_and_seminal_papers() -> None:
    from researchclaw.data import (
        detect_frameworks,
        load_framework_docs,
        load_seminal_papers,
    )

    frameworks = detect_frameworks(
        "LoRA fine-tuning with TRL DPO for language models"
    )

    assert "trl" in frameworks
    assert "peft" in frameworks
    docs = load_framework_docs(frameworks, max_chars=2000)
    assert "Framework API Documentation" in docs
    assert "TRL" in docs or "PEFT" in docs
    assert isinstance(load_seminal_papers("transformer attention"), list)
