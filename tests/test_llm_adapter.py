from src.apps.comic_gen.llm_adapter import LLMAdapter


def test_dashscope_provider_can_initialize_openai_compatible_client(monkeypatch):
    """DashScope's default path also requires the OpenAI-compatible SDK."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    adapter = LLMAdapter()
    client = adapter._get_client()

    assert adapter.provider == "dashscope"
    assert client.__class__.__name__ == "OpenAI"
    assert client.chat.completions is not None
