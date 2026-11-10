from cxquality.scoring import (
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NOT_SCORABLE,
    STATUS_SCORED,
    ScoringEngine,
    coverage_metrics,
    redact_transcript_for_model,
    transcript_hash,
)


def score_answer(score=3.0, confidence=0.9):
    return {"type": "score", "score": score, "confidence": confidence}


class FakeConfig:
    decision_model = "typesafe/jev-1.14"


class FakeClient:
    config = FakeConfig()

    def __init__(self, response, clarification=None):
        self.response = response
        self.clarification = clarification or {
            "question": "Which policy applies?",
            "knowledge_gap": "policy",
        }
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        return self.response

    def clarification_question(self, **kwargs):
        return self.clarification


def transcript():
    return """CHAT TRANSCRIPT
Customer Name: Customer
03 Nov 2026 10:00:00 | Customer : I need help with my order
03 Nov 2026 10:01:00 | Agent : I found it and the delivery is tomorrow
03 Nov 2026 10:02:00 | Customer : Thank you, that solves it
Exported from Agent Assist on 09 Nov 2026 11:53:13"""


def good_response():
    return {
        "model": "typesafe/jev-1.14-test",
        "answers": {
            "understanding": {"choice": "understood", "confidence": 0.95},
            "sentiment_start": {"choice": "neutral", "confidence": 0.9},
            "sentiment_end": {"choice": "positive", "confidence": 0.95},
            "customer_satisfaction": score_answer(4, 0.95),
            "resolution": score_answer(4, 0.9),
            "empathy": score_answer(3, 0.8),
            "clarity": score_answer(4, 0.9),
            "tone": {"choice": "supportive", "confidence": 0.9},
        },
    }


def test_scored_result_and_hash_cache():
    client = FakeClient(good_response())
    engine = ScoringEngine(client, cache={})
    first = engine.evaluate(transcript())
    second = engine.evaluate(transcript().replace("11:53:13", "11:55:07"))
    assert first.status == STATUS_SCORED
    assert first.quality_score > 90
    assert second.cache_key == first.cache_key
    assert client.calls == 1


def test_unknown_is_not_given_a_score():
    response = good_response()
    response["answers"]["understanding"] = {
        "choice": "needs_clarification",
        "confidence": 0.8,
    }
    client = FakeClient(response)
    result = ScoringEngine(client).evaluate(transcript())
    assert result.status == STATUS_NEEDS_CLARIFICATION
    assert result.quality_score is None
    assert result.clarification_question == "Which policy applies?"


def test_short_input_is_excluded_without_api_call():
    client = FakeClient(good_response())
    result = ScoringEngine(client).evaluate("hello")
    assert result.status == STATUS_NOT_SCORABLE
    assert client.calls == 0


def test_coverage_metrics_are_clear():
    key = transcript_hash("x")
    from cxquality.scoring import ConversationEvaluation

    values = [
        ConversationEvaluation(status=STATUS_SCORED, cache_key=key),
        ConversationEvaluation(status=STATUS_SCORED, cache_key=key),
        ConversationEvaluation(status=STATUS_NEEDS_CLARIFICATION, cache_key=key),
        ConversationEvaluation(status=STATUS_NOT_SCORABLE, cache_key=key),
    ]
    metrics = coverage_metrics(values)
    assert metrics["scoring_coverage"] == 2 / 3
    assert metrics["clarification_rate"] == 1 / 3
    assert metrics["exclusion_rate"] == 1 / 4


def test_model_text_redacts_direct_identifiers_but_keeps_sentiment():
    value = redact_transcript_for_model(
        "Customer Name: Sara Chat Started At: 03 Nov 2026 10:00:00\n"
        "03 Nov 2026 10:00:00 | Sara : I am very unhappy, call +971 50 123 4567\n"
        "03 Nov 2026 10:01:00 | Agent : Email me at agent@example.com about order 123456789"
    )
    assert "Sara" not in value
    assert "+971" not in value
    assert "example.com" not in value
    assert "123456789" not in value
    assert "very unhappy" in value

