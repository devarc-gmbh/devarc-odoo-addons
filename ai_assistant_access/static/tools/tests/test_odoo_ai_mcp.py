import asyncio
import json
import sys

import httpx
import odoo_ai_mcp as server
import pytest

FAKE_URL = "https://odoo.example.com"
FAKE_DB = "example_db"
FAKE_KEY = "sk_secret_do_not_leak_12345"


class FakePost:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.response


def ok_response(payload):
    return httpx.Response(200, json=payload)


def error_response(status_code, code=None, message=None):
    body = {"error": {}}
    if code is not None:
        body["error"]["code"] = code
    if message is not None:
        body["error"]["message"] = message
    return httpx.Response(status_code, json=body)


@pytest.fixture
def set_env(monkeypatch):
    monkeypatch.setenv("ODOO_URL", FAKE_URL)
    monkeypatch.setenv("ODOO_DB", FAKE_DB)
    monkeypatch.setenv("ODOO_AI_KEY", FAKE_KEY)


@pytest.fixture
def fake_post(monkeypatch, set_env):
    def install(response=None, exc=None):
        fake = FakePost(response=response, exc=exc)
        monkeypatch.setattr(server.httpx, "post", fake)
        return fake

    return install


def test_odoo_hello_sends_only_the_database_in_the_body(fake_post):
    fake = fake_post(response=ok_response({"status": "ok"}))
    server.odoo_hello()
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/hello"
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert fake.calls[0]["json"] == {"db": FAKE_DB}


def test_odoo_models_sends_correct_op_and_auth(fake_post):
    fake = fake_post(response=ok_response({"models": []}))
    server.odoo_models()
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/models"
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert fake.calls[0]["json"]["db"] == FAKE_DB


def test_odoo_fields_sends_correct_op_and_payload(fake_post):
    fake = fake_post(response=ok_response({"fields": {}}))
    server.odoo_fields("res.partner")
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/fields_get"
    assert fake.calls[0]["json"]["model"] == "res.partner"


def test_odoo_search_uses_pii_free_default_fields(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_search("res.partner")
    payload = fake.calls[0]["json"]
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/search_read"
    assert payload["fields"] == ["name", "company_name", "city", "country_id", "user_id"]
    for pii_field in ("email", "phone", "mobile", "vat"):
        assert pii_field not in payload["fields"]


def test_odoo_search_explicit_pii_field_is_passed_through(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_search("res.partner", fields=["name", "email"])
    assert fake.calls[0]["json"]["fields"] == ["name", "email"]


def test_odoo_search_fields_clamped_to_max(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    many_fields = [f"field_{i}" for i in range(80)]
    server.odoo_search("crm.lead", fields=many_fields)
    assert len(fake.calls[0]["json"]["fields"]) == server.MAX_FIELDS


def test_odoo_search_limit_clamped_to_max(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_search("crm.lead", limit=5000)
    assert fake.calls[0]["json"]["limit"] == server.MAX_LIMIT


def test_odoo_search_default_limit_when_not_given(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_search("crm.lead")
    assert fake.calls[0]["json"]["limit"] == server.DEFAULT_LIMIT


def test_odoo_search_unknown_model_falls_back_to_id_display_name(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_search("project.task")
    assert fake.calls[0]["json"]["fields"] == ["name", "project_id", "user_ids", "stage_id"]

    fake2 = fake_post(response=ok_response({"records": []}))
    server.odoo_search("some.unlisted.model")
    assert fake2.calls[0]["json"]["fields"] == ["id", "display_name"]


def test_odoo_read_sends_correct_payload(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    server.odoo_read("sale.order", [1, 2, 3])
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/read"
    assert fake.calls[0]["json"]["ids"] == [1, 2, 3]
    assert fake.calls[0]["json"]["model"] == "sale.order"


def test_odoo_read_empty_ids_raises_locally_without_network_call(fake_post):
    fake = fake_post(response=ok_response({"records": []}))
    with pytest.raises(server.OdooAIError):
        server.odoo_read("sale.order", [])
    assert fake.calls == []


def test_odoo_count_sends_correct_payload(fake_post):
    fake = fake_post(response=ok_response({"count": 3}))
    server.odoo_count("crm.lead", domain=[["user_id", "=", 7]])
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/search_count"
    assert fake.calls[0]["json"]["domain"] == [["user_id", "=", 7]]


def test_odoo_group_sends_correct_payload(fake_post):
    fake = fake_post(response=ok_response({"groups": []}))
    server.odoo_group("sale.order", groupby=["state"], fields=["amount_total"])
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/read_group"
    assert fake.calls[0]["json"]["groupby"] == ["state"]
    assert fake.calls[0]["json"]["fields"] == ["amount_total"]


def test_odoo_group_missing_groupby_raises_locally(fake_post):
    fake = fake_post(response=ok_response({"groups": []}))
    with pytest.raises(server.OdooAIError):
        server.odoo_group("sale.order", groupby=[])
    assert fake.calls == []


def test_odoo_link_calls_read_then_builds_url(fake_post):
    fake = fake_post(response=ok_response({"records": [{"id": 42}]}))
    result = server.odoo_link("res.partner", 42)
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/read"
    assert fake.calls[0]["json"]["ids"] == [42]
    assert result["url"] == f"{FAKE_URL}/odoo/res.partner/42"


def test_odoo_link_denied_access_does_not_return_url(fake_post):
    fake_post(response=error_response(403, code="model_not_allowed"))
    with pytest.raises(server.OdooAIError):
        server.odoo_link("ir.config_parameter", 1)


ALL_TOOL_CALLS = [
    ("odoo_hello", lambda: server.odoo_hello(), "hello"),
    ("odoo_models", lambda: server.odoo_models(), "models"),
    ("odoo_fields", lambda: server.odoo_fields("res.partner"), "fields_get"),
    ("odoo_search", lambda: server.odoo_search("res.partner"), "search_read"),
    ("odoo_read", lambda: server.odoo_read("res.partner", [1]), "read"),
    ("odoo_count", lambda: server.odoo_count("res.partner"), "search_count"),
    ("odoo_group", lambda: server.odoo_group("sale.order", groupby=["state"]), "read_group"),
    ("odoo_link", lambda: server.odoo_link("res.partner", 1), "read"),
]


@pytest.mark.parametrize(
    "name,call,expected_op", ALL_TOOL_CALLS, ids=[c[0] for c in ALL_TOOL_CALLS]
)
def test_every_tool_sends_op_and_bearer_auth(fake_post, name, call, expected_op):
    fake = fake_post(response=ok_response({"records": [{"id": 1}]}))
    call()
    request = fake.calls[0]
    assert request["url"] == f"{FAKE_URL}/ai_assistant/v1/{expected_op}"
    assert request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


@pytest.mark.parametrize(
    "name,call,expected_op", ALL_TOOL_CALLS, ids=[c[0] for c in ALL_TOOL_CALLS]
)
def test_key_never_appears_in_request_url(fake_post, name, call, expected_op):
    fake = fake_post(response=ok_response({"records": [{"id": 1}]}))
    call()
    for request in fake.calls:
        assert FAKE_KEY not in request["url"]


def test_key_never_leaks_in_output_on_success(fake_post):
    fake_post(response=ok_response({"records": [{"id": 1, "name": "Test"}]}))
    result = server.odoo_search("res.partner")
    assert FAKE_KEY not in json.dumps(result)


def test_key_never_leaks_on_endpoint_error_message(fake_post):
    fake_post(response=error_response(403, code="model_not_allowed"))
    with pytest.raises(server.OdooAIError) as exc_info:
        server.odoo_search("ir.config_parameter")
    assert FAKE_KEY not in str(exc_info.value)


def test_key_never_leaks_when_httpx_raises_exception_containing_key(fake_post):
    leaking_exc = httpx.ConnectError(f"connection failed, tried Authorization: Bearer {FAKE_KEY}")
    fake_post(exc=leaking_exc)
    with pytest.raises(server.OdooAIError) as exc_info:
        server.odoo_search("res.partner")
    assert FAKE_KEY not in str(exc_info.value)


def test_missing_env_raises_readable_error_without_network_call(monkeypatch):
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ODOO_DB", raising=False)
    monkeypatch.delenv("ODOO_AI_KEY", raising=False)
    calls = []
    monkeypatch.setattr(server.httpx, "post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(server.OdooAIError, match="ODOO_URL"):
        server.odoo_models()
    assert calls == []


@pytest.mark.parametrize(
    "code,expected_snippet",
    [
        ("write_attempt", "read-only"),
        ("model_not_allowed", "not allowed for the AI assistant"),
        ("unknown_op", "not supported"),
        ("not_internal", "internal access"),
        ("expired", "expired"),
        ("revoked", "revoked"),
        ("disabled", "disabled"),
        ("company_not_allowed", "outside your allowed companies"),
        ("missing_fields", "'fields'"),
    ],
)
def test_known_error_codes_translated_to_readable_text(fake_post, code, expected_snippet):
    fake_post(response=error_response(403, code=code))
    with pytest.raises(server.OdooAIError) as exc_info:
        server.odoo_search("res.partner")
    assert expected_snippet in str(exc_info.value)


def test_rate_limit_status_translated_regardless_of_body(fake_post):
    fake_post(response=httpx.Response(429, content=b""))
    with pytest.raises(server.OdooAIError, match="Rate limit exceeded"):
        server.odoo_search("res.partner")


def test_unknown_error_code_falls_back_to_server_message(fake_post):
    fake_post(response=error_response(400, code="some_future_code", message="some future failure"))
    with pytest.raises(server.OdooAIError, match="some future failure"):
        server.odoo_search("res.partner")


def test_non_json_error_body_falls_back_to_generic_message(fake_post):
    fake_post(response=httpx.Response(500, content=b"<html>server error</html>"))
    with pytest.raises(server.OdooAIError, match="status 500"):
        server.odoo_search("res.partner")


def test_success_returns_dict_payload(fake_post):
    fake_post(response=ok_response({"records": [{"id": 1, "name": "ACME"}]}))
    result = server.odoo_search("res.partner")
    assert result == {"records": [{"id": 1, "name": "ACME"}]}


PAIR_RESPONSE = {
    "api_key": FAKE_KEY,
    "login": "eva@example.com",
    "access_mode": "read_write",
    "expires_at": "2027-01-23 10:00:00",
    "odoo_url": FAKE_URL,
    "db": FAKE_DB,
    "instructions": "## Odoo AI assistant (read and edit)",
}

CREDENTIAL_KEYS = {"format", "odoo_url", "db", "login", "api_key", "access_mode", "expires_at"}


@pytest.fixture
def no_env(monkeypatch):
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_AI_KEY", "ODOO_AI_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pair_post(monkeypatch, no_env):
    def install(response=None, exc=None):
        fake = FakePost(response=response, exc=exc)
        monkeypatch.setattr(server.httpx, "post", fake)
        return fake

    return install


def write_credentials_file(path, **overrides):
    data = {
        "format": 1,
        "odoo_url": FAKE_URL,
        "db": FAKE_DB,
        "login": "eva@example.com",
        "api_key": FAKE_KEY,
        "access_mode": "read",
        "expires_at": "2027-01-23 10:00:00",
        **overrides,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_pair(tmp_path, code="pair-code-123"):
    path = tmp_path / "creds" / "odoo.json"
    exit_code = server.main(
        ["pair", "--url", f"{FAKE_URL}/", "--db", FAKE_DB, "--code", code, "--path", str(path)]
    )
    return exit_code, path


def test_pair_writes_credentials_file_and_never_prints_the_key(pair_post, tmp_path, capsys):
    fake = pair_post(response=ok_response(PAIR_RESPONSE))
    exit_code, path = run_pair(tmp_path)
    out, err = capsys.readouterr()
    assert exit_code == 0
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/pair"
    assert fake.calls[0]["json"] == {"db": FAKE_DB, "code": "pair-code-123"}
    assert not (fake.calls[0]["headers"] or {}).get("Authorization")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == CREDENTIAL_KEYS
    assert stored["format"] == 1
    assert stored["api_key"] == FAKE_KEY
    assert stored["access_mode"] == "read_write"
    assert FAKE_KEY not in out
    assert FAKE_KEY not in err
    assert str(path) in out
    assert "ODOO_AI_CREDENTIALS" in out
    assert PAIR_RESPONSE["instructions"] in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_pair_credentials_file_is_private(pair_post, tmp_path):
    pair_post(response=ok_response(PAIR_RESPONSE))
    _exit_code, path = run_pair(tmp_path)
    assert path.stat().st_mode & 0o077 == 0


def test_pair_mcp_block_points_to_credentials_file(pair_post, tmp_path, capsys):
    pair_post(response=ok_response(PAIR_RESPONSE))
    _exit_code, path = run_pair(tmp_path)
    block = server.mcp_config_block(path)
    assert block["mcpServers"]["odoo"]["env"] == {"ODOO_AI_CREDENTIALS": str(path)}
    assert json.dumps(block, indent=2) in capsys.readouterr().out


def test_pair_with_invalid_code_fails_without_file(pair_post, tmp_path, capsys):
    pair_post(response=error_response(401, code="invalid_code", message=f"leak {FAKE_KEY}"))
    exit_code, path = run_pair(tmp_path)
    out, err = capsys.readouterr()
    assert exit_code == 1
    assert "invalid or expired code" in err
    assert FAKE_KEY not in out + err
    assert not path.exists()


def test_pair_server_error_does_not_echo_response_body(pair_post, tmp_path, capsys):
    pair_post(response=httpx.Response(500, content=f"<html>{FAKE_KEY}</html>".encode()))
    exit_code, path = run_pair(tmp_path)
    out, err = capsys.readouterr()
    assert exit_code == 1
    assert "status 500" in err
    assert FAKE_KEY not in out + err
    assert not path.exists()


def test_pair_connection_error_fails_cleanly(pair_post, tmp_path, capsys):
    pair_post(exc=httpx.ConnectError("boom"))
    exit_code, path = run_pair(tmp_path)
    assert exit_code == 1
    assert "Could not connect" in capsys.readouterr().err
    assert not path.exists()


@pytest.mark.parametrize(
    "platform,env_name,expected_parts",
    [
        ("win32", "APPDATA", ("appdata", "odoo-ai")),
        ("darwin", None, ("home", "Library", "Application Support", "odoo-ai")),
        ("linux", "XDG_CONFIG_HOME", ("xdg", "odoo-ai")),
        ("linux", None, ("home", ".config", "odoo-ai")),
    ],
)
def test_default_credentials_path_per_platform(
    monkeypatch, tmp_path, platform, env_name, expected_parts
):
    monkeypatch.setattr(server.sys, "platform", platform)
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    if env_name:
        monkeypatch.setenv(env_name, str(tmp_path / expected_parts[0]))
    path = server.default_credentials_path("https://odoo.example.com:8069/", "my db")
    assert path == tmp_path.joinpath(*expected_parts, "odoo.example.com_my_db.json")


def test_pair_uses_default_path_when_none_given(pair_post, monkeypatch, tmp_path):
    pair_post(response=ok_response(PAIR_RESPONSE))
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert server.main(["pair", "--url", FAKE_URL, "--db", FAKE_DB, "--code", "c"]) == 0
    assert (tmp_path / "odoo-ai" / f"odoo.example.com_{FAKE_DB}.json").exists()


def test_credentials_file_is_used_for_calls(no_env, monkeypatch, tmp_path):
    path = write_credentials_file(tmp_path / "c.json")
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(path))
    fake = FakePost(response=ok_response({"models": []}))
    monkeypatch.setattr(server.httpx, "post", fake)
    server.odoo_models()
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/models"
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert fake.calls[0]["json"] == {"db": FAKE_DB}


def test_env_key_overrides_credentials_file(no_env, monkeypatch, tmp_path):
    path = write_credentials_file(tmp_path / "c.json")
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(path))
    monkeypatch.setenv("ODOO_AI_KEY", "env_key_wins")
    fake = FakePost(response=ok_response({"models": []}))
    monkeypatch.setattr(server.httpx, "post", fake)
    server.odoo_models()
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer env_key_wins"


@pytest.mark.parametrize(
    "content",
    [
        json.dumps({"format": 1, "api_key": FAKE_KEY})[:-1],
        json.dumps({"format": 2, "odoo_url": FAKE_URL, "db": FAKE_DB, "api_key": FAKE_KEY}),
        json.dumps([FAKE_KEY]),
    ],
)
def test_broken_credentials_file_raises_without_values(no_env, monkeypatch, tmp_path, content):
    path = tmp_path / "c.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(path))
    with pytest.raises(server.OdooAIError) as err:
        server.odoo_models()
    assert FAKE_KEY not in str(err.value)
    assert FAKE_URL not in str(err.value)


def test_missing_credentials_file_raises_readable_error(no_env, monkeypatch, tmp_path):
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(tmp_path / "missing.json"))
    with pytest.raises(server.OdooAIError, match="could not be read"):
        server.odoo_models()


def tool_names(access_mode):
    app = server.build_server(access_mode)
    return {tool.name for tool in asyncio.run(app.list_tools())}


def test_write_tools_registered_only_for_read_write_mode():
    read_tools = tool_names("read")
    assert "odoo_create" not in read_tools
    assert "odoo_write" not in read_tools
    assert {"odoo_hello", "odoo_search", "odoo_status"} <= read_tools
    read_write_tools = tool_names("read_write")
    assert {"odoo_create", "odoo_write"} <= read_write_tools
    assert "odoo_unlink" not in read_write_tools


def test_server_mode_follows_credentials_file(no_env, monkeypatch, tmp_path):
    path = write_credentials_file(tmp_path / "c.json", access_mode="read_write")
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(path))
    assert server.configured_access_mode() == "read_write"
    path.write_text("broken", encoding="utf-8")
    assert server.configured_access_mode() == "read"


def test_odoo_status_never_returns_the_key(no_env, monkeypatch, tmp_path):
    path = write_credentials_file(tmp_path / "c.json", access_mode="read_write")
    monkeypatch.setenv("ODOO_AI_CREDENTIALS", str(path))
    status = server.odoo_status()
    assert status["login"] == "eva@example.com"
    assert status["access_mode"] == "read_write"
    assert status["expires_at"] == "2027-01-23 10:00:00"
    assert status["credentials_path"] == str(path)
    assert FAKE_KEY not in json.dumps(status)


def test_odoo_create_and_write_send_values(fake_post):
    fake = fake_post(response=ok_response({"id": 7, "model": "crm.lead"}))
    server.odoo_create("crm.lead", {"name": "AI lead"})
    assert fake.calls[0]["url"] == f"{FAKE_URL}/ai_assistant/v1/create"
    assert fake.calls[0]["json"] == {
        "db": FAKE_DB,
        "model": "crm.lead",
        "values": {"name": "AI lead"},
    }
    server.odoo_write("crm.lead", [7], {"name": "AI lead 2"})
    assert fake.calls[1]["url"] == f"{FAKE_URL}/ai_assistant/v1/write"
    assert fake.calls[1]["json"]["ids"] == [7]
    assert fake.calls[1]["json"]["values"] == {"name": "AI lead 2"}


def test_odoo_write_rejects_empty_input_locally(fake_post):
    fake = fake_post(response=ok_response({}))
    with pytest.raises(server.OdooAIError):
        server.odoo_write("crm.lead", [], {"name": "x"})
    with pytest.raises(server.OdooAIError):
        server.odoo_create("crm.lead", {})
    assert fake.calls == []


@pytest.mark.parametrize(
    "code,expected_snippet",
    [("write_disabled", "Editing is disabled"), ("model_not_writable", "cannot be edited")],
)
def test_write_error_codes_translated(fake_post, code, expected_snippet):
    fake_post(response=error_response(403, code=code))
    with pytest.raises(server.OdooAIError, match=expected_snippet):
        server.odoo_create("crm.lead", {"name": "x"})


def run_pair_url(tmp_path, url, *extra):
    path = tmp_path / "creds" / "odoo.json"
    argv = ["pair", "--url", url, "--db", FAKE_DB, "--code=-pair-code", "--path", str(path)]
    return server.main([*argv, *extra]), path


def test_pair_refuses_plain_http_before_sending_the_code(pair_post, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ODOO_AI_ALLOW_HTTP", raising=False)
    fake = pair_post(response=ok_response(PAIR_RESPONSE))
    for url in ("http://odoo.example.com", "ftp://odoo.example.com", "odoo.example.com"):
        exit_code, path = run_pair_url(tmp_path, url)
        assert exit_code == 1
        assert "https://" in capsys.readouterr().err
        assert not path.exists()
    assert not fake.calls


def test_pair_allows_http_only_with_explicit_opt_in(pair_post, monkeypatch, tmp_path):
    monkeypatch.delenv("ODOO_AI_ALLOW_HTTP", raising=False)
    fake = pair_post(response=ok_response(PAIR_RESPONSE))
    exit_code, _path = run_pair_url(tmp_path, "http://localhost:8069", "--allow-http")
    assert exit_code == 0
    assert fake.calls[0]["json"] == {"db": FAKE_DB, "code": "-pair-code"}
    monkeypatch.setenv("ODOO_AI_ALLOW_HTTP", "1")
    exit_code, _path = run_pair_url(tmp_path, "http://localhost:8069")
    assert exit_code == 0
    assert len(fake.calls) == 2


def test_tools_refuse_plain_http_without_sending_the_key(fake_post, monkeypatch):
    monkeypatch.delenv("ODOO_AI_ALLOW_HTTP", raising=False)
    monkeypatch.setenv("ODOO_URL", "http://odoo.example.com")
    fake = fake_post(response=ok_response({"status": "ok"}))
    with pytest.raises(server.OdooAIError, match="https://"):
        server.odoo_hello()
    assert not fake.calls
    monkeypatch.setenv("ODOO_AI_ALLOW_HTTP", "1")
    server.odoo_hello()
    assert fake.calls[0]["url"] == "http://odoo.example.com/ai_assistant/v1/hello"
