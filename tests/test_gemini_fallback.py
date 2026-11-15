from app.llm import gemini


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        outcome = self.behavior.get(model, "ok")
        if outcome == "quota":
            raise RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")
        if outcome == "timeout":
            raise RuntimeError("504 DEADLINE_EXCEEDED")
        return _Resp(f"from {model}")


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _setup(monkeypatch, behavior):
    models = _FakeModels(behavior)
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(models))
    monkeypatch.setattr(gemini.settings, "gemini_model", "m1")
    monkeypatch.setattr(gemini, "FALLBACK_MODEL", "m2")
    monkeypatch.setattr(gemini, "SPILLOVER_MODELS", ["m3"])
    monkeypatch.setattr(gemini, "_exhausted", set())
    return models


def test_quota_error_spills_over_and_is_remembered(monkeypatch):
    models = _setup(monkeypatch, {"m1": "quota", "m2": "timeout"})
    assert gemini.generate_text("hi") == "from m3"
    assert models.calls == ["m1", "m2", "m3"]
    models.calls.clear()
    gemini.generate_text("again")
    assert models.calls == ["m2", "m3"]  # m1 skipped after its 429; m2 only timed out


def test_no_spillover_without_quota_error(monkeypatch):
    models = _setup(monkeypatch, {"m1": "timeout", "m2": "timeout"})
    try:
        gemini.generate_text("hi")
    except gemini.GeminiError:
        pass
    assert models.calls == ["m1", "m2"]


def test_all_exhausted_still_tries(monkeypatch):
    models = _setup(monkeypatch, {"m1": "quota", "m2": "quota", "m3": "quota"})
    try:
        gemini.generate_text("hi")
    except gemini.GeminiError:
        pass
    models.calls.clear()
    try:
        gemini.generate_text("hi")
    except gemini.GeminiError:
        pass
    assert models.calls == ["m1", "m2"]  # everything exhausted: retry the main pair (quota may reset)
