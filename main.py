import argparse
import json
import os
import re
import socket
import subprocess
import threading
import uuid
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional

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
COMMAND_TIMEOUT_SECONDS = 120
EXIT_WORDS = {"exit", "quit", "退出"}
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}
TOKEN_HEADER = "X-Access-Token"

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
    if OpenAI is None:
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


def chat(client: OpenAI, messages: List[Dict[str, str]]) -> str:
    model = os.getenv("MODEL", DEFAULT_MODEL)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""


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


def run_command(command: str) -> Dict[str, Optional[str]]:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "command": command,
            "returncode": str(completed.returncode),
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "timeout": None,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "timeout": str(COMMAND_TIMEOUT_SECONDS),
        }


def command_result_message(result: Dict[str, Optional[str]]) -> str:
    return json.dumps(
        {
            "type": "command_result",
            "command": result["command"],
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "timeout": result["timeout"],
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
    return {
        "type": "command_confirmation",
        "command": decision["command"],
        "description": decision["description"],
    }


def execute_command_decision(client: OpenAI, messages: List[Dict[str, str]], command: str) -> str:
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
    return f"调用命令：{command}\n{final_content}"


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


def valid_token(expected_token: str, provided_token: str) -> bool:
    return provided_token == expected_token


def build_app(client: Any, access_token: str) -> Any:
    app = FastAPI(title="MyClaw智能体")
    app.state.client = client
    app.state.access_token = access_token
    app.state.session_id = uuid.uuid4().hex
    app.state.messages = build_messages()
    app.state.pending_commands = {}
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
        return {"ok": True, "sessionId": app.state.session_id}

    @app.post("/chat")
    def chat_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
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
                response = prepare_user_input(app.state.client, app.state.messages, user_input)
                if response["type"] == "command_confirmation":
                    command_id = uuid.uuid4().hex
                    app.state.pending_commands[command_id] = response["command"]
                    response["commandId"] = command_id
        except OpenAIError:
            response = {"type": "reply", "reply": "请继续输入需求"}

        return response

    @app.post("/confirm")
    def confirm_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        command_id = str(payload.get("commandId", "")).strip()
        try:
            with app.state.lock:
                command = app.state.pending_commands.pop(command_id, "")
                if not command:
                    return JSONResponse(
                        status_code=404,
                        content={"type": "reply", "reply": "请继续输入需求"},
                    )
                reply = execute_command_decision(app.state.client, app.state.messages, command)
        except OpenAIError:
            reply = "请继续输入需求"

        return {"type": "reply", "reply": reply}

    @app.post("/cancel")
    def cancel_endpoint(
        payload: Dict[str, Any] = Body(default_factory=dict),
        x_access_token: str = Header(default="", alias=TOKEN_HEADER),
    ) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        command_id = str(payload.get("commandId", "")).strip()
        with app.state.lock:
            command = app.state.pending_commands.pop(command_id, "")
            if not command:
                return JSONResponse(
                    status_code=404,
                    content={"type": "reply", "reply": "请继续输入需求"},
                )
            reply = cancel_command_decision(app.state.messages, command)

        return {"type": "reply", "reply": reply}

    @app.post("/reset")
    def reset_endpoint(x_access_token: str = Header(default="", alias=TOKEN_HEADER)) -> Any:
        if not valid_token(app.state.access_token, x_access_token):
            return token_error()

        with app.state.lock:
            reset_context(app.state.messages, app.state.pending_commands)
        return {"type": "reply", "reply": "上下文已清空"}

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


def ensure_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno == 48:
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
                print(execute_command_decision(client, messages, response["command"]))
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
