import json

import pytest

from cxquality.ai import OpenRouterClient, OpenRouterError
from cxquality.budget import BudgetGuard


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def test_decision_tracks_reported_cost_without_exposing_key():
    http = FakeHttp([FakeResponse({"answers": {}, "usage": {"cost": 0.001}})])
    budget = BudgetGuard()
    client = OpenRouterClient(api_key="secret", budget=budget, http=http)
    result = client.decide("hello", {"x": {"type": "noul", "instructions": "Is this hello?"}})
    assert result["answers"] == {}
    assert budget.snapshot().spent_usd == pytest.approx(0.001)
    assert client.consume_thread_cost() == pytest.approx(0.001)
    assert client.consume_thread_cost() == 0
    assert http.requests[0][1]["headers"]["Authorization"] == "Bearer secret"


def test_writer_parses_json_object():
    content = json.dumps({"question": "Which return policy applies?"})
    http = FakeHttp(
        [FakeResponse({"choices": [{"message": {"content": content}}], "usage": {"cost": 0.002}})]
    )
    client = OpenRouterClient(api_key="secret", http=http)
    result = client.write_json(task="ask", context={"reason": "ambiguous"})
    assert result["question"].startswith("Which")


def test_error_omits_sensitive_response_body():
    http = FakeHttp([FakeResponse({"error": "customer transcript here"}, status_code=400)])
    client = OpenRouterClient(api_key="secret", http=http)
    with pytest.raises(OpenRouterError) as exc:
        client.decide("private transcript", {"x": {"type": "noul", "instructions": "x"}})
    assert "customer transcript" not in str(exc.value)

