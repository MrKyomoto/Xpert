import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from code.agent import core
from code.agent.core import Agent, LLMCallError


def completion(
    content=None,
    *,
    finish_reason="stop",
    choices=True,
    refusal=None,
    completion_tokens=12,
    reasoning_tokens=None,
    tool_calls=None,
):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        refusal=refusal,
        reasoning_content=None,
    )
    choice_list = (
        [SimpleNamespace(message=message, finish_reason=finish_reason)]
        if choices
        else []
    )
    return SimpleNamespace(
        id="chatcmpl-test",
        choices=choice_list,
        usage=SimpleNamespace(
            completion_tokens=completion_tokens,
            total_tokens=completion_tokens + 20,
            completion_tokens_details=SimpleNamespace(
                reasoning_tokens=reasoning_tokens
            ),
        ),
    )


def stream_event(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ]
    )


class SequenceCompletions:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not self.results:
            raise AssertionError("unexpected extra API call")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, results):
        self.chat = SimpleNamespace(completions=SequenceCompletions(results))


class GatewayError(RuntimeError):
    def __init__(self, message, status_code, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = "req-test"
        self.response = SimpleNamespace(headers=headers or {})


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(core.config, "API_RETRIES", 2)
    monkeypatch.setattr(core.config, "API_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(core.config, "API_RETRY_MAX_DELAY", 30)
    monkeypatch.setattr(core.config, "API_RETRY_JITTER", 0)
    monkeypatch.setattr(core.config, "MAX_TOKENS", 8000)
    monkeypatch.setattr(core.config, "MAX_RETRY_TOKENS", 16000)


def make_agent(results, model="gpt-4o"):
    client = FakeClient(results)
    agent = Agent(
        name="重试测试",
        role_prompt="只返回结果",
        use_tools=False,
        model=model,
        client=client,
    )
    return agent, client.chat.completions


def sdk_completion_body(content):
    return {
        "id": "chatcmpl-sdk-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def make_sdk_agent(handler):
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        max_retries=0,
        timeout=1,
        http_client=http_client,
    )
    agent = Agent(
        name="SDK重试测试",
        role_prompt="只返回结果",
        use_tools=False,
        model="gpt-4o",
        client=client,
    )
    return agent, http_client


def test_nonstream_empty_content_is_retried_until_valid(fast_retries, capsys):
    agent, completions = make_agent(
        [completion(""), completion('{"ok": true}')]
    )

    assert agent.chat("评审") == '{"ok": true}'
    assert len(completions.calls) == 2
    assert [item["role"] for item in agent.context.messages] == [
        "system",
        "user",
        "assistant",
    ]
    error_log = capsys.readouterr().err
    assert "empty assistant response" in error_log
    assert "第 1/3 次请求失败" in error_log


def test_missing_choices_is_retried(fast_retries):
    agent, completions = make_agent(
        [completion(choices=False), completion("usable")]
    )

    assert agent.chat("评审") == "usable"
    assert len(completions.calls) == 2


def test_length_finished_empty_response_doubles_token_budget(
    fast_retries, capsys
):
    agent, completions = make_agent(
        [
            completion("", finish_reason="length", completion_tokens=8000),
            completion("complete"),
        ],
        model="gpt-5-test",
    )

    assert agent.chat("评审") == "complete"
    assert completions.calls[0]["max_completion_tokens"] == 8000
    assert completions.calls[1]["max_completion_tokens"] == 16000
    assert "从 8000 提高到 16000" in capsys.readouterr().err


def test_length_finished_partial_json_is_discarded_and_retried(fast_retries):
    agent, completions = make_agent(
        [
            completion('{"scores":', finish_reason="length"),
            completion('{"scores": {}}'),
        ]
    )

    assert agent.chat("评审") == '{"scores": {}}'
    assert completions.calls[1]["max_tokens"] == 16000


def test_transient_gateway_and_timeout_errors_are_retried(fast_retries):
    agent, completions = make_agent(
        [
            GatewayError("server busy", 503),
            TimeoutError("socket timeout"),
            completion("recovered"),
        ]
    )

    assert agent.chat("评审") == "recovered"
    assert len(completions.calls) == 3


def test_real_sdk_429_then_success_is_recovered(fast_retries):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429,
                request=request,
                headers={"content-type": "application/json"},
                json={"error": {"message": "rate limited", "type": "rate_limit"}},
            )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json=sdk_completion_body("sdk recovered"),
        )

    agent, http_client = make_sdk_agent(handler)
    try:
        assert agent.chat("评审") == "sdk recovered"
        assert len(calls) == 2
    finally:
        http_client.close()


def test_real_sdk_http_200_empty_content_is_retried(fast_retries):
    calls = []

    def handler(request):
        calls.append(request)
        content = None if len(calls) == 1 else "usable"
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=json.dumps(sdk_completion_body(content)).encode(),
        )

    agent, http_client = make_sdk_agent(handler)
    try:
        assert agent.chat("评审") == "usable"
        assert len(calls) == 2
    finally:
        http_client.close()


def test_real_sdk_read_timeout_then_success_is_recovered(fast_retries):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json=sdk_completion_body("after timeout"),
        )

    agent, http_client = make_sdk_agent(handler)
    try:
        assert agent.chat("评审") == "after timeout"
        assert len(calls) == 2
    finally:
        http_client.close()


def test_real_sdk_authentication_error_is_not_retried(fast_retries):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            401,
            request=request,
            headers={"content-type": "application/json"},
            json={"error": {"message": "invalid key", "type": "auth"}},
        )

    agent, http_client = make_sdk_agent(handler)
    try:
        with pytest.raises(LLMCallError, match="1 次尝试"):
            agent.chat("评审")
        assert len(calls) == 1
    finally:
        http_client.close()


def test_retry_after_header_controls_wait(fast_retries, monkeypatch):
    sleeps = []
    monkeypatch.setattr(core.time, "sleep", sleeps.append)
    agent, completions = make_agent(
        [
            GatewayError("rate limited", 429, {"retry-after": "2.5"}),
            completion("recovered"),
        ]
    )

    assert agent.chat("评审") == "recovered"
    assert len(completions.calls) == 2
    assert sleeps == [2.5]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_permanent_http_errors_fail_without_repeating(fast_retries, status_code):
    agent, completions = make_agent(
        [GatewayError("bad request", status_code), completion("must not run")]
    )

    with pytest.raises(LLMCallError, match="1 次尝试"):
        agent.chat("评审")
    assert len(completions.calls) == 1


def test_empty_responses_exhaust_configured_attempts(fast_retries):
    agent, completions = make_agent(
        [completion(""), completion(None), completion("   ")]
    )

    with pytest.raises(LLMCallError, match="3 次尝试") as exc_info:
        agent.chat("评审")
    assert "finish_reason=stop" in str(exc_info.value)
    assert len(completions.calls) == 3


def test_refusal_is_not_retried(fast_retries):
    agent, completions = make_agent(
        [completion(None, refusal="cannot comply"), completion("must not run")]
    )

    with pytest.raises(LLMCallError, match="1 次尝试"):
        agent.chat("评审")
    assert len(completions.calls) == 1


def test_content_filter_is_not_retried(fast_retries):
    agent, completions = make_agent(
        [
            completion(None, finish_reason="content_filter"),
            completion("must not run"),
        ]
    )

    with pytest.raises(LLMCallError, match="content filtering"):
        agent.chat("评审")
    assert len(completions.calls) == 1


def test_server_retry_override_headers_take_precedence(
    fast_retries, monkeypatch
):
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    retry_agent, retry_completions = make_agent(
        [
            GatewayError("retry this 400", 400, {"x-should-retry": "true"}),
            completion("ok"),
        ]
    )
    assert retry_agent.chat("评审") == "ok"
    assert len(retry_completions.calls) == 2

    stop_agent, stop_completions = make_agent(
        [
            GatewayError("do not retry 503", 503, {"x-should-retry": "false"}),
            completion("must not run"),
        ]
    )
    with pytest.raises(LLMCallError, match="1 次尝试"):
        stop_agent.chat("评审")
    assert len(stop_completions.calls) == 1


def test_retry_after_is_capped(fast_retries, monkeypatch):
    sleeps = []
    monkeypatch.setattr(core.time, "sleep", sleeps.append)
    agent, _ = make_agent(
        [
            GatewayError("rate limited", 429, {"retry-after": "90"}),
            completion("ok"),
        ]
    )

    assert agent.chat("评审") == "ok"
    assert sleeps == [30]


def test_empty_response_error_includes_reasoning_usage(fast_retries):
    agent, _ = make_agent(
        [
            completion(
                None,
                finish_reason="stop",
                completion_tokens=8000,
                reasoning_tokens=7999,
            )
        ]
    )
    core.config.API_RETRIES = 0

    with pytest.raises(LLMCallError) as exc_info:
        agent.chat("评审")
    assert "completion_tokens=8000" in str(exc_info.value)
    assert "reasoning_tokens=7999" in str(exc_info.value)


def test_reasoning_budget_exhaustion_is_detected_even_if_gateway_reports_stop(
    fast_retries,
):
    agent, completions = make_agent(
        [
            completion(
                None,
                finish_reason="stop",
                completion_tokens=8000,
                reasoning_tokens=7999,
            ),
            completion("visible result"),
        ],
        model="gpt-5-compatible-alias",
    )

    assert agent.chat("评审") == "visible result"
    assert completions.calls[1]["max_completion_tokens"] == 16000


def test_tool_call_is_not_repeated_when_followup_response_is_empty(
    fast_retries, monkeypatch
):
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="fake_tool", arguments='{"x": 1}'),
    )
    agent, completions = make_agent(
        [
            completion(None, finish_reason="tool_calls", tool_calls=[call]),
            completion(""),
            completion("final"),
        ]
    )
    executed = []
    monkeypatch.setattr(
        core.registry,
        "execute",
        lambda name, args: executed.append((name, args)) or "done",
    )

    assert agent.chat("use tool") == "final"
    assert executed == [("fake_tool", {"x": 1})]
    assert len(completions.calls) == 3
    assert [item["role"] for item in agent.context.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_empty_stream_is_retried_without_partial_history(fast_retries):
    agent, completions = make_agent(
        [
            iter([stream_event(finish_reason="stop")]),
            iter([stream_event("完整"), stream_event("回答", "stop")]),
        ]
    )

    assert "".join(agent.chat_stream("审阅")) == "完整回答"
    assert len(completions.calls) == 2
    assistant_messages = [
        item for item in agent.context.messages if item["role"] == "assistant"
    ]
    assert assistant_messages == [{"role": "assistant", "content": "完整回答"}]


def test_stream_length_retry_increases_regular_model_budget(fast_retries):
    agent, completions = make_agent(
        [
            iter([stream_event(finish_reason="length")]),
            iter([stream_event("ok", "stop")]),
        ]
    )

    assert "".join(agent.chat_stream("审阅")) == "ok"
    assert completions.calls[0]["max_tokens"] == 8000
    assert completions.calls[1]["max_tokens"] == 16000


def test_truncated_stream_chunks_are_not_emitted(fast_retries):
    agent, completions = make_agent(
        [
            iter([stream_event("partial", "length")]),
            iter([stream_event("complete", "stop")]),
        ]
    )

    assert "".join(agent.chat_stream("审阅")) == "complete"
    assert len(completions.calls) == 2


def test_midstream_disconnect_discards_partial_chunks(fast_retries):
    def broken_stream():
        yield stream_event("partial")
        raise ConnectionError("connection dropped")

    agent, completions = make_agent(
        [broken_stream(), iter([stream_event("complete", "stop")])]
    )

    assert "".join(agent.chat_stream("审阅")) == "complete"
    assert len(completions.calls) == 2
    assert agent.context.messages[-1]["content"] == "complete"


def test_empty_streams_exhaust_attempts(fast_retries):
    agent, completions = make_agent(
        [
            iter([stream_event(finish_reason="stop")]),
            iter([stream_event(finish_reason="stop")]),
            iter([stream_event(finish_reason="stop")]),
        ]
    )

    with pytest.raises(LLMCallError, match="3 次尝试"):
        list(agent.chat_stream("审阅"))
    assert len(completions.calls) == 3
