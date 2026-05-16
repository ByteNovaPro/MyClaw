import argparse
import hmac
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from network_access import print_lan_access_info

MISSING_DEPENDENCIES = []

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - keeps startup message clear before install
    load_dotenv = None
    MISSING_DEPENDENCIES.append("python-dotenv")

try:
    from openai import OpenAI, OpenAIError
except ImportError:  # pragma: no cover - keeps startup message clear before install
    OpenAI = None  # type: ignore[assignment]

    class OpenAIError(Exception):
        pass

try:
    import uvicorn
    from fastapi import Body, FastAPI, Header
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - keeps startup message clear before install
    uvicorn = None  # type: ignore[assignment]
    Body = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]
    StaticFiles = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_MAX_COMMAND_OUTPUT_CHARS = 20000
DEFAULT_SESSION_TTL_SECONDS = 21600
DEFAULT_MAX_SESSIONS = 50
EXIT_WORDS = {"exit", "quit", "退出"}
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
TOKEN_HEADER = "X-Access-Token"
CONVERSATION_HEADER = "X-Conversation-Id"
COMMAND_WORKDIR = PROJECT_DIR

SYSTEM_PROMPT = """
你是一个命令行对话智能体。你必须根据用户输入判断是否需要执行 shell 命令。

输出规则：
1. 你每次只能输出一个 JSON 对象，不要输出 Markdown、解释或额外文本。
2. 如果不需要执行命令，输出：{"action":"reply","content":"你的回复"}
3. 如果需要执行命令，输出：{"action":"command","command":"具体 shell 命令","description":"执行该命令后会发生什么，用一句话说明"}
4. 如果收到命令执行结果，需要判断任务是否已经完成：
   - 已完成，输出：{"action":"reply","content":"完成"}
   - 还需要用户继续输入需求，输出：{"action":"reply","content":"请继续输入需求"}
5. 不要解释命令，不要输出原因。
""".strip()


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv()


def ensure_dependencies() -> None:
    if OpenAI is None and "openai" not in MISSING_DEPENDENCIES:
        MISSING_DEPENDENCIES.append("openai")
    if MISSING_DEPENDENCIES:
        packages = " ".join(sorted(set(MISSING_DEPENDENCIES)))
        raise RuntimeError(
            f"缺少依赖 {packages}，请先运行：python -m pip install -r requirements.txt"
        )


def ensure_server_dependencies() -> None:
    ensure_dependencies()
    missing = []
    if FastAPI is None:
        missing.append("fastapi")
    if uvicorn is None:
        missing.append("uvicorn")
    if missing:
        packages = " ".join(sorted(set(missing)))
        raise RuntimeError(
            f"缺少依赖 {packages}，请先运行：python -m pip install -r requirements.txt"
        )


def build_client() -> Any:
    ensure_dependencies()

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少环境变量 DASHSCOPE_API_KEY")

    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def build_access_token() -> str:
    access_token = os.getenv("ACCESS_TOKEN", "").strip()
    if not access_token:
        raise RuntimeError("缺少环境变量 ACCESS_TOKEN，请在 .env 中配置访问口令")
    if not re.fullmatch(r"\d{4}", access_token):
        raise RuntimeError("ACCESS_TOKEN 必须是 4 位数字")
    return access_token


def get_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def command_timeout_seconds() -> int:
    return get_int_env("COMMAND_TIMEOUT_SECONDS", DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, 900)


def max_command_output_chars() -> int:
    return get_int_env("MAX_COMMAND_OUTPUT_CHARS", DEFAULT_MAX_COMMAND_OUTPUT_CHARS, 1000, 100000)


def session_ttl_seconds() -> int:
    return get_int_env("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS, 300, 86400)


def max_sessions() -> int:
    return get_int_env("MAX_SESSIONS", DEFAULT_MAX_SESSIONS, 1, 500)


def chat(client: OpenAI, messages: List[Dict[str, str]]) -> str:
    model = os.getenv("MODEL", DEFAULT_MODEL)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""


def truncate_output(value: str, max_chars: int) -> Tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    omitted = len(value) - max_chars
    suffix = f"\n\n[输出已截断，省略 {omitted} 个字符]"
    return value[:max_chars] + suffix, True


def parse_decision(content: str) -> Dict[str, str]:
    try:
        data: Dict[str, Any] = json.loads(content)
    except JSONDecodeError:
        return {"action": "reply", "content": content or "请继续输入需求"}

    action = data.get("action")
    if action == "command" and isinstance(data.get("command"), str):
        command = data["command"].strip()
        if command:
            description = data.get("description")
            if not isinstance(description, str) or not description.strip():
                description = f"将在服务所在电脑的当前目录执行命令：{command}"
            return {"action": "command", "command": command, "description": description.strip()}
        return {"action": "reply", "content": "请继续输入需求"}

    if action == "reply" and isinstance(data.get("content"), str):
        content_value = data["content"].strip()
        return {"action": "reply", "content": content_value or "请继续输入需求"}

    return {"action": "reply", "content": "请继续输入需求"}


def analyze_command_risk(command: str) -> Dict[str, str]:
    normalized = re.sub(r"\s+", " ", command.strip().lower())
    high_patterns = [
        r"\brm\s+(-[^\s]*[rf][^\s]*|-[^\s]*[fr][^\s]*)\b",
        r"\bsudo\b",
        r"\bchmod\s+(-r\s+)?777\b",
        r"\bchown\s+(-r\s+)?",
        r"\bdd\s+.*\bof=",
        r"\bmkfs\b",
        r"\bdiskutil\b",
        r"\bshutdown\b|\breboot\b|\bhalt\b",
        r"\bcurl\b.*\|\s*(sh|bash)\b",
        r"\bwget\b.*\|\s*(sh|bash)\b",
        r":\(\)\s*\{\s*:\|:",
    ]
    medium_patterns = [
        r"\bkill(all)?\b|\bpkill\b",
        r"\bdocker\s+compose\s+down\b",
        r"\bdocker\s+rm\b|\bdocker\s+rmi\b",
        r"\bgit\s+push\b|\bgit\s+reset\b|\bgit\s+clean\b",
        r"\bmv\b|\bcp\b",
        r">\s*\S+|>>\s*\S+",
    ]

    if any(re.search(pattern, normalized) for pattern in high_patterns):
        return {
            "riskLevel": "high",
            "riskNote": "该命令可能修改系统、删除文件或执行高权限操作，请确认你完全信任它。",
        }
    if any(re.search(pattern, normalized) for pattern in medium_patterns):
        return {
            "riskLevel": "medium",
            "riskNote": "该命令可能修改文件、容器、Git 状态或运行中的进程，请确认影响范围。",
        }
    return {
        "riskLevel": "low",
        "riskNote": "该命令看起来风险较低，但仍会在服务所在环境中执行。",
    }


def run_command(command: str) -> Dict[str, Any]:
    timeout_seconds = command_timeout_seconds()
    output_limit = max_command_output_chars()
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=str(COMMAND_WORKDIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        stdout, stdout_truncated = truncate_output(stdout.strip(), output_limit)
        stderr, stderr_truncated = truncate_output(stderr.strip(), output_limit)
        return {
            "command": command,
            "cwd": str(COMMAND_WORKDIR),
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timeout": False,
            "timeoutSeconds": timeout_seconds,
            "truncated": stdout_truncated or stderr_truncated,
        }
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.kill()
            try:
                stdout_after_kill, stderr_after_kill = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout_after_kill, stderr_after_kill = process.communicate()
        else:
            stdout_after_kill = ""
            stderr_after_kill = ""
        stdout_value = exc.stdout if isinstance(exc.stdout, str) else stdout_after_kill
        stderr_value = exc.stderr if isinstance(exc.stderr, str) else stderr_after_kill
        stdout, stdout_truncated = truncate_output((stdout_value or "").strip(), output_limit)
        stderr, stderr_truncated = truncate_output((stderr_value or "").strip(), output_limit)
        return {
            "command": command,
            "cwd": str(COMMAND_WORKDIR),
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "timeout": True,
            "timeoutSeconds": timeout_seconds,
            "truncated": stdout_truncated or stderr_truncated,
        }


def command_result_message(result: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": "command_result",
            "command": result["command"],
            "cwd": result["cwd"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "timeout": result["timeout"],
            "timeoutSeconds": result["timeoutSeconds"],
            "truncated": result["truncated"],
        },
        ensure_ascii=False,
    )


def normalize_after_command_reply(content: str) -> str:
    return "完成" if content.strip() == "完成" else "请继续输入需求"


def prepare_user_input(client: OpenAI, messages: List[Dict[str, str]], user_input: str) -> Dict[str, str]:
    messages.append({"role": "user", "content": user_input})

    decision = parse_decision(chat(client, messages))
    if decision["action"] == "reply":
        messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        return {"type": "reply", "reply": decision["content"]}

    messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
    risk = analyze_command_risk(decision["command"])
    return {
        "type": "command_confirmation",
        "command": decision["command"],
        "description": decision["description"],
        "riskLevel": risk["riskLevel"],
        "riskNote": risk["riskNote"],
    }


def execute_command_decision(client: OpenAI, messages: List[Dict[str, str]], command: str) -> Dict[str, Any]:
    result = run_command(command)
    messages.append({"role": "user", "content": command_result_message(result)})

    try:
        final_decision = parse_decision(chat(client, messages))
        final_content = normalize_after_command_reply(final_decision.get("content", ""))
    except OpenAIError:
        final_content = "请继续输入需求"

    messages.append(
        {
            "role": "assistant",
            "content": json.dumps({"action": "reply", "content": final_content}, ensure_ascii=False),
        }
    )
    return {
        "type": "command_result",
        "reply": final_content,
        "commandResult": result,
    }


def cancel_command_decision(messages: List[Dict[str, str]], command: str) -> str:
    messages.append(
        {
            "role": "user",
            "content": json.dumps({"type": "command_cancelled", "command": command}, ensure_ascii=False),
        }
    )
    messages.append(
        {
            "role": "assistant",
            "content": json.dumps({"action": "reply", "content": "已取消执行命令"}, ensure_ascii=False),
        }
    )
    return "已取消执行命令"


def build_messages() -> List[Dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def reset_context(messages: List[Dict[str, str]], pending_commands: Dict[str, str]) -> None:
    messages.clear()
    messages.extend(build_messages())
    pending_commands.clear()


def token_error() -> Any:
    return JSONResponse(status_code=401, content={"type": "error", "reply": "访问口令错误"})


def conversation_error() -> Any:
    return JSONResponse(status_code=401, content={"type": "error", "reply": "会话已失效，请重新输入口令"})


def valid_token(expected_token: str, provided_token: str) -> bool:
    return hmac.compare_digest(provided_token.encode("utf-8"), expected_token.encode("utf-8"))


def build_conversation() -> Dict[str, Any]:
    now = time.time()
    return {
        "messages": build_messages(),
        "pending_commands": {},
        "lock": threading.Lock(),
        "created_at": now,
        "updated_at": now,
    }


def cleanup_sessions_locked(sessions: Dict[str, Dict[str, Any]]) -> None:
    now = time.time()
    ttl = session_ttl_seconds()
    expired = [
        conversation_id
        for conversation_id, conversation in sessions.items()
        if now - float(conversation.get("updated_at", 0)) > ttl
    ]
    for conversation_id in expired:
        sessions.pop(conversation_id, None)

    limit = max_sessions()
    if len(sessions) <= limit:
        return
    ordered = sorted(sessions.items(), key=lambda item: float(item[1].get("updated_at", 0)))
    for conversation_id, _conversation in ordered[: len(sessions) - limit]:
        sessions.pop(conversation_id, None)


def create_conversation(app: Any) -> str:
    with app.state.lock:
        cleanup_sessions_locked(app.state.sessions)
        conversation_id = uuid.uuid4().hex
        app.state.sessions[conversation_id] = build_conversation()
        return conversation_id


def get_conversation(app: Any, conversation_id: str) -> Optional[Dict[str, Any]]:
    cleanup_sessions_locked(app.state.sessions)
    conversation = app.state.sessions.get(conversation_id)
    if conversation is None:
        return None
    conversation["updated_at"] = time.time()
    return conversation


def build_app(client: Any, access_token: str) -> Any:
    app = FastAPI(title="MyClaw智能体")
    app.state.client = client
    app.state.access_token = access_token
    app.state.session_id = uuid.uuid4().hex
    app.state.sessions = {}
    app.state.lock = threading.Lock()

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"ok": "true"}

    @app.get("/session")
    def session() -> Dict[str, str]:
        return {"sessionId": app.state.session_id}

    @app.post("/auth")
    def auth_endpoint(payload: Dict[str, Any] = Body(default_factory=dict)) -> Any:
        token = str(payload.get("token", "")).strip()
        if not re.fullmatch(r"\d{4}", token):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "reply": "请输入 4 位数字口令"},
            )
        if not valid_token(app.state.access_token, token):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "reply": "访问口令错误"},
            )
        conversation_id = create_conversation(app)
        return {"ok": True, "sessionId": app.state.session_id, "conversationId": conversation_id}

    @app.post("/chat")
    def chat_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
        x_conversation_id: str = Header(default="", alias=CONVERSATION_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        user_input = str(payload.get("message", "")).strip()
        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "reply", "reply": "请继续输入需求"},
            )

        if user_input.lower() in EXIT_WORDS:
            return {"type": "reply", "reply": "完成"}

        try:
            with app.state.lock:
                conversation = get_conversation(app, x_conversation_id.strip())
                if conversation is None:
                    return conversation_error()
            with conversation["lock"]:
                response = prepare_user_input(app.state.client, conversation["messages"], user_input)
                if response["type"] == "command_confirmation":
                    command_id = uuid.uuid4().hex
                    conversation["pending_commands"][command_id] = response
                    response["commandId"] = command_id
        except OpenAIError:
            response = {"type": "reply", "reply": "请继续输入需求"}

        return response

    @app.post("/confirm")
    def confirm_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
        x_conversation_id: str = Header(default="", alias=CONVERSATION_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        command_id = str(payload.get("commandId", "")).strip()
        try:
            with app.state.lock:
                conversation = get_conversation(app, x_conversation_id.strip())
                if conversation is None:
                    return conversation_error()
            with conversation["lock"]:
                command_decision = conversation["pending_commands"].pop(command_id, None)
                if not command_decision:
                    return JSONResponse(
                        status_code=404,
                        content={"type": "reply", "reply": "请继续输入需求"},
                    )
                reply = execute_command_decision(app.state.client, conversation["messages"], command_decision["command"])
        except OpenAIError:
            reply = {"type": "reply", "reply": "请继续输入需求"}

        return reply

    @app.post("/cancel")
    def cancel_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
        x_conversation_id: str = Header(default="", alias=CONVERSATION_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        command_id = str(payload.get("commandId", "")).strip()
        with app.state.lock:
            conversation = get_conversation(app, x_conversation_id.strip())
            if conversation is None:
                return conversation_error()
        with conversation["lock"]:
            command_decision = conversation["pending_commands"].pop(command_id, None)
            if not command_decision:
                return JSONResponse(
                    status_code=404,
                    content={"type": "reply", "reply": "请继续输入需求"},
                )
            reply = cancel_command_decision(conversation["messages"], command_decision["command"])

        return {"type": "reply", "reply": reply}

    @app.post("/reset")
    def reset_endpoint(
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
        x_conversation_id: str = Header(default="", alias=CONVERSATION_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        with app.state.lock:
            conversation = get_conversation(app, x_conversation_id.strip())
            if conversation is None:
                return conversation_error()
        with conversation["lock"]:
            reset_context(conversation["messages"], conversation["pending_commands"])
        return {"type": "reply", "reply": "上下文已清空"}

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


def ensure_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno in {48, 98}:
                raise RuntimeError(f"端口 {port} 已被占用，服务启动失败") from exc
            raise


def run_server(client: Any, access_token: str, host: str, port: int) -> None:
    ensure_server_dependencies()
    app = build_app(client, access_token)
    ensure_port_available(host, port)
    public_port = os.getenv("PUBLIC_PORT", "").strip()
    display_port = int(public_port) if public_port.isdigit() else None
    print_lan_access_info(port, display_port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_cli(client: Any) -> int:
    messages = build_messages()
    print("你可以直接输入问题，或描述需要在终端完成的任务；输入 exit、quit 或 退出 结束。")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input.lower() in EXIT_WORDS:
            return 0

        try:
            response = prepare_user_input(client, messages, user_input)
            if response["type"] == "reply":
                print(response["reply"])
                continue

            print(response["description"])
            print(f"命令：{response['command']}")
            confirm = input("确认执行？输入 y 确认，其他任意输入取消：").strip().lower()
            if confirm == "y":
                result = execute_command_decision(client, messages, response["command"])
                command_result = result.get("commandResult", {})
                print(f"调用命令：{command_result.get('command', response['command'])}")
                print(f"退出码：{command_result.get('returncode')}")
                if command_result.get("stdout"):
                    print(command_result["stdout"])
                if command_result.get("stderr"):
                    print(command_result["stderr"])
                print(result.get("reply", "请继续输入需求"))
            else:
                print(cancel_command_decision(messages, response["command"]))
        except OpenAIError:
            print("请继续输入需求")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MyClaw智能体")
    parser.add_argument("--cli", action="store_true", help="使用命令行对话模式")
    parser.add_argument("--serve", action="store_true", help="兼容旧参数；默认已经会启动局域网 HTTP 服务")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP 服务监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP 服务端口")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_environment()

    if args.cli:
        try:
            client = build_client()
        except RuntimeError as exc:
            print(str(exc))
            return 1
        return run_cli(client)

    try:
        client = build_client()
        access_token = build_access_token()
    except RuntimeError as exc:
        print(str(exc))
        return 1

    try:
        run_server(client, access_token, args.host, args.port)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
