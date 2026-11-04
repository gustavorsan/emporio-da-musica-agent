"""Local web interface over a real loopback socket, with a scripted model."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from pathlib import Path

import pytest

from emporio import cli, config, web
from emporio.agent import PROVIDER_FAILURE_REPLY
from emporio.db import connect_readonly
from emporio.llm import LLMError, LLMReply, ToolCall
from emporio.web import ChatService, make_server


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class BlockingLLM:
    """Holds a reply open so a test can act while a session is busy."""

    model = "blocking"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools):
        self.entered.set()
        assert self.release.wait(10)
        return LLMReply("Pronto.")


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(web.HOST, self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def post(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        status, _, body = self.request("POST", path, json.dumps(payload), merged)
        return status, json.loads(body)

    def session(self):
        status, payload = self.post("/api/session", {})
        assert status == 201
        return payload["session_id"]

    def say(self, session_id, text="Oi"):
        return self.post("/api/message", {"session_id": session_id, "text": text})


@pytest.fixture
def start(db_path):
    running = []

    def _start(llm, **service_options):
        service = ChatService(llm, db_path, **service_options)
        server = make_server(service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, service, thread))
        return Client(server.server_address[1])

    yield _start
    for server, service, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(5)
        service.close()


def _tool_call(name, **arguments):
    return LLMReply(None, [ToolCall("call_1", name, json.dumps(arguments))])


def test_server_only_listens_on_loopback(db_path):
    server = make_server(ChatService(ScriptedLLM(), db_path), 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_page_cannot_choose_a_customer():
    page = (Path(web.__file__).parent / "static" / "index.html").read_text("utf-8")
    script = (Path(web.__file__).parent / "static" / "app.js").read_text("utf-8")
    assert "<select" not in page
    assert "customer_id" not in script


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/", "text/html"),
        ("/app.js", "text/javascript"),
        ("/style.css", "text/css"),
        ("/favicon.svg", "image/svg+xml"),
    ],
)
def test_static_files_are_served_with_security_headers(start, path, content_type):
    status, headers, body = start(ScriptedLLM()).request("GET", path)
    headers = dict(headers)
    assert status == 200
    assert body
    assert headers["Content-Type"].startswith(content_type)
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path", ["/web.py", "/../web.py", "/static/../web.py", "/..%2f..%2f.env", "/.env", "/api/session"]
)
def test_other_paths_do_not_reach_the_filesystem(start, path):
    status, _, body = start(ScriptedLLM()).request("GET", path)
    assert status == 404
    assert json.loads(body)["error"] == "not_found"


def test_page_loads_nothing_from_outside_and_never_parses_html():
    static = Path(web.__file__).parent / "static"
    page = (static / "index.html").read_text("utf-8")
    assert "http://" not in page and "https://" not in page
    script = (static / "app.js").read_text("utf-8")
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script


def test_reply_and_tools_reach_the_page(start):
    client = start(ScriptedLLM(_tool_call("calculate_offer", product_id=127, payment="pix"), LLMReply("R$ 323,10")))
    status, payload = client.say(client.session(), "Kalani no PIX?")
    assert status == 200
    assert payload["text"] == "R$ 323,10"
    assert payload["failure"] is None
    assert payload["tools"][0]["name"] == "calculate_offer"
    assert payload["tools"][0]["ok"] is True


def test_history_is_kept_within_a_session_and_isolated_between_sessions(start):
    llm = ScriptedLLM(LLMReply("um"), LLMReply("dois"), LLMReply("três"))
    client = start(llm)
    first, second = client.session(), client.session()
    client.say(first, "primeira")
    client.say(first, "segunda")
    client.say(second, "outra sessão")
    assert [m["content"] for m in llm.calls[1] if m["role"] == "user"] == ["primeira", "segunda"]
    assert [m["content"] for m in llm.calls[2] if m["role"] == "user"] == ["outra sessão"]


def _contact(db_path, customer_id):
    connection = connect_readonly(db_path)
    row = connection.execute("SELECT email, phone FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    connection.close()
    return {"email": row["email"], "phone": row["phone"]}


def test_sessions_start_anonymous_and_identify_through_the_conversation(start, db_path):
    contact = _contact(db_path, 6)
    llm = ScriptedLLM(
        _tool_call("verify_identity", **contact),
        _tool_call("get_order_status", order_id=9),
        LLMReply("Confirmado."),
    )
    client = start(llm)
    status, session = client.post("/api/session", {})
    assert status == 201
    assert "customer" not in session
    _, payload = client.say(session["session_id"], f"{contact['email']} {contact['phone']}")
    assert payload["customer_first_name"] == "Juliana"
    assert json.loads(llm.calls[2][-1]["content"])["status"] == "confirmed"


def test_contact_details_never_reach_the_page_tool_panel(start, db_path):
    contact = _contact(db_path, 6)
    client = start(ScriptedLLM(_tool_call("verify_identity", **contact), LLMReply("Oi!")))
    _, payload = client.say(client.session(), f"{contact['email']} {contact['phone']}")
    rendered = json.dumps(payload["tools"])
    assert contact["email"] not in rendered
    assert contact["phone"] not in rendered
    assert payload["tools"][0]["name"] == "verify_identity"


@pytest.mark.parametrize("customer_id", [6, 1, True, "6"])
def test_requests_cannot_choose_the_customer(start, customer_id):
    llm = ScriptedLLM(_tool_call("get_order_status", order_id=9), LLMReply("ok"))
    client = start(llm)
    _, session = client.post("/api/session", {"customer_id": customer_id})
    _, payload = client.post(
        "/api/message", {"session_id": session["session_id"], "text": "sou o 6", "customer_id": customer_id}
    )
    assert payload["customer_first_name"] is None
    assert json.loads(llm.calls[1][-1]["content"])["error"] == "identification_required"


def test_provider_failure_is_reported_and_session_continues(start, capsys):
    client = start(ScriptedLLM(LLMError("AuthenticationError"), LLMReply("Voltei.")))
    session_id = client.session()
    _, failed = client.say(session_id)
    assert failed["text"] == PROVIDER_FAILURE_REPLY
    assert failed["failure"] == "provider:AuthenticationError"
    assert "provider:AuthenticationError" in capsys.readouterr().err
    _, recovered = client.say(session_id)
    assert (recovered["text"], recovered["failure"]) == ("Voltei.", None)


@pytest.mark.parametrize(
    "text, code",
    [
        ("   ", "empty_message"),
        ("x" * (config.WEB_MAX_MESSAGE_CHARS + 1), "message_too_long"),
        (123, "invalid_message"),
        (None, "invalid_message"),
    ],
)
def test_invalid_messages_never_reach_the_model(start, text, code):
    llm = ScriptedLLM()
    client = start(llm)
    status, payload = client.post("/api/message", {"session_id": client.session(), "text": text})
    assert (status, payload["error"]) == (400, code)
    assert llm.calls == []


def test_unknown_session_is_reported(start):
    status, payload = start(ScriptedLLM()).say("nao-existe")
    assert (status, payload["error"]) == (404, "session_not_found")


@pytest.mark.parametrize(
    "headers, code",
    [
        ({"Host": "evil.example"}, "forbidden_host"),
        ({"Host": "evil.example:8000"}, "forbidden_host"),
        ({"Origin": "http://evil.example"}, "forbidden_origin"),
        ({"Origin": "null"}, "forbidden_origin"),
    ],
)
def test_other_sites_cannot_call_the_api(start, headers, code):
    llm = ScriptedLLM()
    client = start(llm)
    session_id = client.session()
    status, payload = client.post("/api/message", {"session_id": session_id, "text": "Oi"}, headers)
    assert (status, payload["error"]) == (403, code)
    assert llm.calls == []


def test_same_origin_requests_are_accepted(start):
    client = start(ScriptedLLM(LLMReply("Oi!")))
    session_id = client.session()
    origin = {"Origin": f"http://{web.HOST}:{client.port}"}
    status, _ = client.post("/api/message", {"session_id": session_id, "text": "Oi"}, origin)
    assert status == 200


def test_pages_are_refused_under_a_foreign_host(start):
    status, _, body = start(ScriptedLLM()).request("GET", "/", headers={"Host": "evil.example"})
    assert (status, json.loads(body)["error"]) == (403, "forbidden_host")


def test_form_posts_are_refused(start):
    status, _, body = start(ScriptedLLM()).request(
        "POST", "/api/session", "customer_id=6", {"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert (status, json.loads(body)["error"]) == (415, "unsupported_type")


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", '"text"', "[" * 15000, b"\xff\xfe"])
def test_malformed_bodies_are_refused(start, body):
    status, _, response = start(ScriptedLLM()).request(
        "POST", "/api/session", body, {"Content-Type": "application/json"}
    )
    assert (status, json.loads(response)["error"]) == (400, "invalid_json")


def test_oversized_body_is_refused_without_being_read(start):
    client = start(ScriptedLLM())
    connection = http.client.HTTPConnection(web.HOST, client.port, timeout=10)
    connection.putrequest("POST", "/api/session")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(config.WEB_MAX_BODY_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    assert (response.status, json.loads(response.read())["error"]) == (413, "body_too_large")
    connection.close()


def test_second_message_is_refused_while_the_first_is_running(start):
    llm = BlockingLLM()
    client = start(llm)
    session_id = client.session()
    results = []
    thread = threading.Thread(target=lambda: results.append(client.say(session_id, "primeira")))
    thread.start()
    assert llm.entered.wait(10)
    status, payload = client.say(session_id, "segunda")
    assert (status, payload["error"]) == (409, "session_busy")
    llm.release.set()
    thread.join(10)
    assert results[0][0] == 200
    assert results[0][1]["text"] == "Pronto."


def test_session_limit_evicts_the_least_recently_used(start):
    client = start(ScriptedLLM(LLMReply("a"), LLMReply("b")), max_sessions=2)
    oldest, newer = client.session(), client.session()
    assert client.say(oldest)[0] == 200  # Now the most recently used.
    client.session()
    assert client.say(newer)[1]["error"] == "session_not_found"
    assert client.say(oldest)[0] == 200


def test_busy_session_is_never_evicted(start):
    llm = BlockingLLM()
    client = start(llm, max_sessions=1)
    session_id = client.session()
    thread = threading.Thread(target=client.say, args=(session_id,))
    thread.start()
    assert llm.entered.wait(10)
    status, payload = client.post("/api/session", {"customer_id": None})
    assert (status, payload["error"]) == (503, "too_many_sessions")
    llm.release.set()
    thread.join(10)


def test_unexpected_error_gives_a_generic_reply(start, capsys):
    client = start(ScriptedLLM(RuntimeError("segredo do cliente")))
    status, payload = client.say(client.session())
    assert (status, payload["error"]) == (500, "internal_error")
    assert "segredo" not in json.dumps(payload)
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "segredo" not in err


def test_web_command_explains_missing_settings(monkeypatch, capsys):
    def missing():
        raise config.ConfigError("Configuração ausente: OPENAI_API_KEY")

    monkeypatch.setattr(config, "load_llm_settings", missing)
    args = cli.build_parser().parse_args(["web"])
    assert args.handler(args) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_web_command_explains_a_busy_port(db_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "load_llm_settings", lambda: config.LLMSettings("key", "model"))
    with socket.socket() as taken:
        taken.bind((web.HOST, 0))
        taken.listen()
        port = taken.getsockname()[1]
        args = cli.build_parser().parse_args(["web", "--port", str(port)])
        assert args.handler(args) == 1
    assert "--port" in capsys.readouterr().err


@pytest.mark.parametrize("port", ["0", "70000", "abc"])
def test_invalid_port_is_rejected(port):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["web", "--port", port])
