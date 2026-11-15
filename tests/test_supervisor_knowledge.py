from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from cxquality.company_evaluation import CompanySupervisorEngine
from cxquality.db import Database
from cxquality.supervisor_knowledge import (
    approve_imported_supervisor_rules,
    import_whatsapp_supervisor_zip,
    is_group_penalty,
    supervisor_rule_text,
)


def make_export(path: Path) -> None:
    text = (
        "01/11/2026, 9:00 am - Supervisor: IMG-20261101-WA0001.jpg (file attached)\n"
        "Agent One\n"
        "خصم 30 نقطة يجب إرسال رابط العروض الحالية\n"
        "01/11/2026, 9:05 am - Agent One: IMG-20261101-WA0002.jpg (file attached)\n"
        "01/11/2026, 9:10 am - Supervisor: <Media omitted>\n"
        "Agent Two\n"
        "العميل بالفعل أرسل رقم الطلب في الرسالة السابقة"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("WhatsApp Chat.txt", text)
        archive.writestr("IMG-20261101-WA0001.jpg", b"\xff\xd8\xff\xd9")
        archive.writestr("IMG-20261101-WA0002.jpg", b"\xff\xd8\xff\xd9")


def test_whatsapp_import_links_images_and_missing_rule_candidates(tmp_path):
    archive = tmp_path / "chat.zip"
    make_export(archive)
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    result = import_whatsapp_supervisor_zip(db, archive, supervisor_sender="Supervisor")
    assert result["extracted_image_count"] == 2
    cases = db.list_supervisor_cases(limit=20)
    assert [case["auto_category"] for case in cases] == [
        "supervisor_case",
        "rule_candidate_missing_image",
        "report_or_unrelated",
    ]
    assert Path(cases[0]["image_path"]).is_file()
    assert cases[1]["image_path"] is None
    assert import_whatsapp_supervisor_zip(db, archive)["duplicate"] is True


def test_approved_precedent_and_promoted_rule_are_retrievable(tmp_path):
    archive = tmp_path / "chat.zip"
    make_export(archive)
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    import_whatsapp_supervisor_zip(db, archive, supervisor_sender="Supervisor")
    case = db.list_supervisor_cases(auto_category="supervisor_case")[0]
    knowledge_id = db.review_supervisor_case(
        case["id"],
        review_status="approved",
        final_category="supervisor_precedent",
        rule_text="Send the current offers link when the customer asks about offers.",
        promote_rule=True,
    )
    assert knowledge_id
    assert db.approved_supervisor_precedents()[0]["id"] == case["id"]
    assert db.list_knowledge(include_inactive=False)[0]["category"] == "company-supervisor-rule"


def test_bulk_approval_excludes_group_penalties_and_deduplicates_rules(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    db.initialize()
    db.create_supervisor_import(
        {
            "id": "import-1",
            "source_zip": "chat.zip",
            "archive_sha256": "a" * 64,
            "chat_filename": "chat.txt",
            "supervisor_sender": "Supervisor",
        },
        [
            {
                "message_index": 1,
                "sender": "Supervisor",
                "caption_original": "عبدالله خصم 3 نقاط يجب الرد على سؤال العميل",
                "auto_category": "supervisor_case",
            },
            {
                "message_index": 2,
                "sender": "Supervisor",
                "caption_original": "محمد خصم 6 نقاط يجب الرد على سؤال العميل",
                "auto_category": "supervisor_case",
            },
            {
                "message_index": 3,
                "sender": "Supervisor",
                "caption_original": "https://example.test خصم 3 نقاط علي الجميع",
                "auto_category": "supervisor_case",
            },
            {
                "message_index": 4,
                "sender": "Supervisor",
                "caption_original": "https://example.test",
                "auto_category": "link_only",
            },
        ],
    )

    preview = approve_imported_supervisor_rules(db, dry_run=True)
    assert preview == {
        "examined": 4,
        "approved_cases": 2,
        "group_penalty_excluded": 1,
        "non_rule_excluded": 1,
        "distinct_rules": 1,
        "deduplicated_cases": 1,
    }
    result = approve_imported_supervisor_rules(db)
    assert result["created_rules"] == 1
    cases = db.list_supervisor_cases(limit=10)
    approved = [case for case in cases if case["review_status"] == "approved"]
    excluded = [case for case in cases if case["review_status"] == "excluded"]
    assert len(approved) == 2
    assert len(excluded) == 2
    assert approved[0]["knowledge_item_id"] == approved[1]["knowledge_item_id"]
    assert supervisor_rule_text(approved[0]["caption_original"]) == "يجب الرد على سؤال العميل"
    assert is_group_penalty("خصم 12 نقطة على الجميع") is True

    rerun = approve_imported_supervisor_rules(db)
    assert rerun.get("created_rules", 0) == 0
    assert len(db.list_knowledge(include_inactive=False)) == 1


class CompanyClient:
    config = SimpleNamespace(decision_model="jev-test")

    def decide(self, state, questions):
        assert "customer_reaction_signal" in questions
        assert "resolution" not in questions
        reference_id = next(iter(state["company_references"]))
        return {
            "model": "jev-test",
            "answers": {
                "understanding": {"choice": "understood", "confidence": 0.95},
                "company_verdict": {"choice": "non_compliant", "confidence": 0.9},
                "applicable_reference": {"choice": reference_id, "confidence": 0.9},
                "agent_evidence": {"choice": "m2", "confidence": 0.9},
                "customer_context": {"choice": "m1", "confidence": 0.9},
                "customer_reaction_signal": {"choice": "not_observable", "confidence": 0.9},
                "style_signal": {"choice": "acceptable", "confidence": 0.9},
            },
        }


def test_company_evaluator_uses_rules_not_generic_dimensions():
    text = (
        "03 Nov 2026 10:00:00 | Customer : ما هي العروض الحالية؟\n"
        "03 Nov 2026 10:01:00 | Agent : شوف الموقع"
    )
    result = CompanySupervisorEngine(
        CompanyClient(), known_policies=["[RULE K1] Send the current offers link."]
    ).evaluate(text)
    assert result.status == "scored"
    assert result.quality_score == 0
    assert result.component_scores == {"company_compliance": 0.0, "_rubric_version": 3}
    assert result.raw_answers["customer_reaction_signal"] == "not_observable"


def test_company_evaluator_refuses_to_judge_without_company_knowledge():
    text = (
        "03 Nov 2026 10:00:00 | Customer : ما هي العروض الحالية؟\n"
        "03 Nov 2026 10:01:00 | Agent : شوف الموقع"
    )
    result = CompanySupervisorEngine(CompanyClient()).evaluate(text)
    assert result.status == "needs_clarification"
    assert result.quality_score is None
