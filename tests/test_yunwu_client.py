"""Yunwu client unit tests — exercise the response parser and request shape
with httpx's MockTransport (no real network)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.yunwu_client import (
    YunwuClient,
    YunwuError,
    _extract_response_text,
)


def test_extract_response_text_chat_completions_preferred():
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "compat"}}]
    }
    assert _extract_response_text(payload) == "compat"


def test_extract_response_text_output_text_shortcut():
    payload = {"output_text": "hello"}
    assert _extract_response_text(payload) == "hello"


def test_extract_response_text_nested_output():
    payload = {
        "output": [
            {"content": [{"type": "output_text", "text": "first "}]},
            {"content": [{"type": "output_text", "text": "second"}]},
        ]
    }
    assert _extract_response_text(payload) == "first second"


def test_extract_response_text_chat_content_parts():
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "alpha "},
                        {"type": "text", "text": "beta"},
                    ],
                }
            }
        ]
    }
    assert _extract_response_text(payload) == "alpha beta"


def test_extract_response_text_raises_on_unknown_shape():
    with pytest.raises(YunwuError):
        _extract_response_text({"foo": "bar"})


@pytest.mark.asyncio
async def test_respond_posts_to_chat_completions(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}]
            },
        )

    transport = httpx.MockTransport(handler)

    # Patch httpx.AsyncClient to use our transport.
    real_async = httpx.AsyncClient

    def fake_async(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async)

    client = YunwuClient()
    out = await client.respond(instructions="sys", user_content="hello")
    assert out == "ok"
    assert captured["url"].endswith("/v1/chat/completions")
    body = captured["body"]
    assert body["model"]  # default chat model
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "sys"
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "hello"
    assert captured["auth"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_respond_passes_response_format_and_temperature(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    transport = httpx.MockTransport(handler)
    real_async = httpx.AsyncClient

    def fake_async(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async)

    client = YunwuClient()
    await client.respond(
        instructions="x",
        user_content=[{"type": "text", "text": "y"}],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.4
    assert captured["body"]["messages"][1]["content"] == [
        {"type": "text", "text": "y"}
    ]


@pytest.mark.asyncio
async def test_post_rejects_html_response(monkeypatch):
    """Yunwu sometimes returns its marketing HTML for unknown paths; we must error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<!DOCTYPE html><html>marketing</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    transport = httpx.MockTransport(handler)
    real_async = httpx.AsyncClient

    def fake_async(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async)

    client = YunwuClient()
    with pytest.raises(YunwuError):
        await client.respond(instructions="x", user_content="y")


@pytest.mark.asyncio
async def test_image_decodes_b64_payload(monkeypatch):
    raw = b"PNGDATA"
    payload = {"data": [{"b64_json": base64.b64encode(raw).decode()}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/images/generations")
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    real_async = httpx.AsyncClient

    def fake_async(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async)

    client = YunwuClient()
    images = await client.image(prompt="cat", size="1024x1024")
    assert images == [raw]


@pytest.mark.asyncio
async def test_post_raises_on_4xx(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    transport = httpx.MockTransport(handler)
    real_async = httpx.AsyncClient

    def fake_async(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async)

    client = YunwuClient()
    with pytest.raises(YunwuError):
        await client.respond(instructions="x", user_content="y")
