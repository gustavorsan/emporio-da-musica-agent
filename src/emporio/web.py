"""Local web interface: a small JSON API and one static page, served only on the loopback address."""

from __future__ import annotations

import json
import secrets
import sqlite3
import sys
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from emporio import config
from emporio.agent import Agent, AgentReply
from emporio.db import connect_readonly
from emporio.llm import LLMClient
from emporio.orders import Customer

HOST = "127.0.0.1"

_STATIC_DIR = Path(__file__).parent / "static"
# An explicit map instead of a directory lookup, so a request path never reaches the filesystem.
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ApiError(Exception):
    """A request the API refuses; the message is safe to show on the page."""

    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class _Session:
    agent: Agent
    conn: sqlite3.Connection
    lock: threading.Lock


class ChatService:
    """Holds one agent per conversation. Each agent has its own read-only connection."""

    def __init__(
        self,
        llm: LLMClient,
        db_path: Path,
        log_path: Path | None = None,
        max_sessions: int = config.WEB_MAX_SESSIONS,
    ) -> None:
        self._llm = llm
        self._db_path = db_path
        self._log_path = log_path
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._registry_lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._llm.model

    def create_session(self) -> str:
        """Start anonymously: a customer is only set once the conversation confirms one."""
        # Requests run on different threads; the per-session lock keeps access to one at a time.
        conn = connect_readonly(self._db_path, check_same_thread=False)
        try:
            agent = Agent(self._llm, conn, log_path=self._log_path)
            session = _Session(agent, conn, threading.Lock())
            session_id = secrets.token_urlsafe(16)
            with self._registry_lock:
                self._make_room()
                self._sessions[session_id] = session
        except Exception:
            conn.close()
            raise
        return session_id

    def _make_room(self) -> None:
        """Drop the least recently used idle conversations; the caller holds the registry lock."""
        while len(self._sessions) >= self._max_sessions:
            for session_id, session in self._sessions.items():
                if session.lock.acquire(blocking=False):
                    session.conn.close()
                    del self._sessions[session_id]
                    session.lock.release()
                    break
            else:
                raise ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "too_many_sessions",
                    "Há muitas conversas em andamento. Tente de novo em instantes.",
                )

    def send(self, session_id: str, text: str) -> tuple[AgentReply, Customer | None]:
        # Taking the session lock under the registry lock stops eviction from closing its connection.
        with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApiError(
                    HTTPStatus.NOT_FOUND,
                    "session_not_found",
                    "Esta conversa não existe mais. Clique em “Nova conversa” para começar outra.",
                )
            if not session.lock.acquire(blocking=False):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "session_busy",
                    "Ainda estou respondendo à mensagem anterior desta conversa.",
                )
            self._sessions.move_to_end(session_id)
        try:
            return session.agent.respond(text), session.agent.customer
        finally:
            session.lock.release()

    def close(self) -> None:
        with self._registry_lock:
            for session in self._sessions.values():
                if session.lock.acquire(blocking=False):
                    session.conn.close()
                    session.lock.release()
            self._sessions.clear()


def _invalid(code: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, code, message)


def _parse_message(payload: dict) -> tuple[str, str]:
    session_id, text = payload.get("session_id"), payload.get("text")
    if not isinstance(session_id, str) or not isinstance(text, str):
        raise _invalid("invalid_message", "Mensagem inválida.")
    text = text.strip()
    if not text:
        raise _invalid("empty_message", "Escreva uma mensagem antes de enviar.")
    if len(text) > config.WEB_MAX_MESSAGE_CHARS:
        raise _invalid(
            "message_too_long",
            f"A mensagem passa do limite de {config.WEB_MAX_MESSAGE_CHARS} caracteres.",
        )
    return session_id, text


class _Server(ThreadingHTTPServer):
    # On Windows, address reuse would let a second server share the port and split the requests.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: tuple[str, int], service: ChatService) -> None:
        super().__init__(address, _Handler)
        self.service = service


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    # Applies to socket reads and writes only, not to the wait for the model.
    timeout = 30
    _body_read = False

    def log_message(self, format: str, *args: object) -> None:
        # The default handler writes every request to stderr, burying the operator warnings.
        pass

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # The tab was closed before the reply arrived; nobody is left to tell.
            pass

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error(self, error: ApiError) -> None:
        self._send_json(error.status, {"error": error.code, "message": error.message})

    def _check_host(self) -> None:
        """Refuse other host names, which is how DNS rebinding reaches a loopback server."""
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"{HOST}:{port}", f"localhost:{port}"}:
            raise ApiError(HTTPStatus.FORBIDDEN, "forbidden_host", "Endereço não permitido.")

    def _check_origin(self) -> None:
        """Refuse other sites, so a page open in the browser cannot spend API credits."""
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            raise ApiError(HTTPStatus.FORBIDDEN, "forbidden_origin", "Origem não permitida.")
        # Plain cross-site forms cannot send this type without a preflight, which is never approved.
        if self.headers.get_content_type() != "application/json":
            raise ApiError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_type", "Envie os dados em JSON."
            )

    def _discard_unread_body(self) -> None:
        """Read what a refused request sent: closing with unread data resets the socket on Windows,
        which can destroy the response before the client reads it."""
        if self._body_read:
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return
        # An oversized body is left alone: reading it is what the size limit exists to avoid.
        if 0 < length <= config.WEB_MAX_BODY_BYTES:
            self.rfile.read(length)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            raise _invalid("invalid_length", "Requisição inválida.")
        if length > config.WEB_MAX_BODY_BYTES:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "A mensagem é grande demais."
            )
        self._body_read = True
        try:
            payload = json.loads(self.rfile.read(length))
        # RecursionError covers bodies made of thousands of nested brackets.
        except (ValueError, RecursionError):
            raise _invalid("invalid_json", "Requisição inválida.") from None
        if not isinstance(payload, dict):
            raise _invalid("invalid_json", "Requisição inválida.")
        return payload

    def do_GET(self) -> None:
        try:
            self._check_host()
            static = _STATIC_FILES.get(urlsplit(self.path).path)
            if static is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "Página não encontrada.")
            filename, content_type = static
            self._send(HTTPStatus.OK, (_STATIC_DIR / filename).read_bytes(), content_type)
        except ApiError as error:
            self._send_error(error)

    def do_POST(self) -> None:
        try:
            self._check_host()
            self._check_origin()
            route = urlsplit(self.path).path
            if route == "/api/session":
                self._read_json()
                self._create_session()
            elif route == "/api/message":
                self._send_message(self._read_json())
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "Rota não encontrada.")
        except ApiError as error:
            self._discard_unread_body()
            self._send_error(error)
        except TimeoutError:
            # Handled by the base class, which closes the connection of a stalled client.
            raise
        except Exception as exc:
            # Only the exception type is reported, since messages may quote customer text.
            print(f"Erro interno: {type(exc).__name__}", file=sys.stderr)
            self._send_error(
                ApiError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "Algo deu errado no servidor local. Tente de novo.",
                )
            )

    def _create_session(self) -> None:
        service = self.server.service
        self._send_json(HTTPStatus.CREATED, {"session_id": service.create_session(), "model": service.model})

    def _send_message(self, payload: dict) -> None:
        session_id, text = _parse_message(payload)
        reply, customer = self.server.service.send(session_id, text)
        if reply.failure:
            print(f"Aviso: resposta de contingência, não do modelo ({reply.failure}).", file=sys.stderr)
        self._send_json(
            HTTPStatus.OK,
            {
                "text": reply.text,
                "failure": reply.failure,
                "tools": [asdict(trace) for trace in reply.tool_traces],
                "customer_first_name": customer.first_name if customer else None,
            },
        )


def make_server(service: ChatService, port: int) -> _Server:
    """Bind the server to the loopback address; port 0 picks a free one."""
    return _Server((HOST, port), service)
