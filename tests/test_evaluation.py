from types import SimpleNamespace

from cxquality.evaluation import WEIGHTS, EvidenceScoringEngine
from cxquality.quality_review import mistake_flags, recompute_overall

TEXT = ('03 Nov 2026 10:00:00 | Customer : ابي ايزو\n'
        '03 Nov 2026 10:01:00 | Agent : تقصد واي ايزوليت؟ أي ماركة وحجم؟')


class Client:
    config = SimpleNamespace(decision_model="jev-test", writer_model="writer-test")

    def __init__(self, weak=False, invalid_evidence=False):
        self.calls = 0
        self.weak = weak
        self.invalid_evidence = invalid_evidence

    def decide(self, state, questions):
        self.calls += 1
        answers = {"understanding": {"choice": "understood", "confidence": .95}}
        for name in WEIGHTS:
            answers[name] = {"score": 4, "choice": "professional", "confidence": .9}
            answers[name + "_observable"] = {"choice": "yes", "confidence": .9}
            answers[name + "_evidence"] = {
                "choice": "invented" if self.invalid_evidence else "m2", "confidence": .9}
        answers["customer_satisfaction_observable"] = {"choice": "no", "confidence": .99}
        if self.weak:
            answers["empathy_observable"] = {"choice": "no", "confidence": .99}
        return {"answers": answers}

    def write_json(self, task, context, **kwargs):
        if "Independently" in task:
            self.invalid_evidence = False
            return self.decide(context["state"], context["questions"])
        if "Select up to" in task:
            return {"ids": [next(iter(context["references"])), "invalid"]}
        return {"intent": "product inquiry", "search_query": "whey isolate"}


def test_short_arabic_unknown_satisfaction_does_not_reduce_quality():
    result = EvidenceScoringEngine(Client(), mode="jev").evaluate(TEXT)
    assert result.status == "scored"
    assert result.quality_score == 100
    assert result.satisfaction_score is None
    assert result.raw_answers["component_evidence"]["resolution"].endswith("أي ماركة وحجم؟")


def test_unobservable_empathy_partial_coverage():
    result = EvidenceScoringEngine(Client(weak=True), mode="jev").evaluate(TEXT)
    assert result.quality_score == 100
    assert "empathy" not in result.component_scores
    assert result.raw_answers["scoring_coverage"] == .65
    assert recompute_overall(result.component_scores) == 100
    assert not mistake_flags(result.component_scores)


def test_media_excluded_before_any_model_call():
    client = Client()
    result = EvidenceScoringEngine(client).evaluate(TEXT + '\n[DOC]')
    assert result.status == "not_scorable"
    assert client.calls == 0


def test_arabic_retrieves_english_category():
    engine = EvidenceScoringEngine(Client(), known_policies=["Whey isolate protein sizes"])
    assert engine._knowledge("ابي ايزو") == ["Whey isolate protein sizes"]


def test_invalid_evidence_cannot_create_employee_penalty():
    result = EvidenceScoringEngine(Client(invalid_evidence=True), mode="jev").evaluate(TEXT)
    assert result.status == "needs_clarification"
    assert result.quality_score is None


def test_customer_message_cannot_be_agent_quality_evidence():
    class CustomerEvidence(Client):
        def decide(self, state, questions):
            result = super().decide(state, questions)
            result["answers"]["resolution_evidence"]["choice"] = "m1"
            return result

    result = EvidenceScoringEngine(CustomerEvidence(), mode="jev").evaluate(TEXT)
    assert result.status == "needs_clarification"
    assert result.quality_score is None


def test_hybrid_reviewer_resolves_unsupported_first_pass():
    result = EvidenceScoringEngine(Client(invalid_evidence=True)).evaluate(TEXT)
    assert result.status == "scored"
    assert result.raw_answers["escalated"]


def test_policy_change_invalidates_cache():
    client = Client()
    engine = EvidenceScoringEngine(client, mode="jev")
    first = engine.evaluate(TEXT)
    engine.known_policies = ["New brand guidance"]
    second = engine.evaluate(TEXT)
    assert client.calls == 2
    assert first.cache_key != second.cache_key


def test_semantic_selection_validates_reference_ids():
    result = EvidenceScoringEngine(Client(), known_policies=["Specific branch rule"]).evaluate(TEXT)
    assert result.raw_answers["reference_material"] == ["Specific branch rule"]


def test_missing_catalog_does_not_create_accuracy_penalty():
    result = EvidenceScoringEngine(Client(), mode="jev").evaluate(TEXT)
    assert "accuracy" not in result.component_scores
    assert result.raw_answers["scoring_coverage"] == .75


def test_malformed_provider_answers_remain_unscored():
    class Malformed(Client):
        def decide(self, state, questions):
            return {"answers": ["invalid"]}
    result = EvidenceScoringEngine(Malformed(), mode="jev").evaluate(TEXT)
    assert result.status == "needs_clarification"


def test_current_scores_do_not_flag_customer_sentiment():
    assert not mistake_flags({"_rubric_version": 2, "customer_satisfaction": 0,
                              "resolution": 4, "clarity": 4})


def test_nonfinite_model_score_is_not_scored():
    class Nonfinite(Client):
        def decide(self, state, questions):
            result = super().decide(state, questions)
            result["answers"]["resolution"]["score"] = float("nan")
            return result
    assert EvidenceScoringEngine(Nonfinite(), mode="jev").evaluate(TEXT).quality_score is None
