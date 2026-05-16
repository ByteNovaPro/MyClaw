# MyClaw智能体

一个运行在 Wi-Fi 局域网中的网页问答智能体，后端使用 FastAPI + Uvicorn，模型使用阿里云百炼 OpenAI 兼容接口连接 `qwen-plus`。程序在本次运行内保留对话记忆；当模型判断用户需求需要执行命令时，会先说明执行后会发生什么，并等待用户确认。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果直接使用指定解释器运行，也要用同一个解释器安装依赖：

```bash
/Users/lby/.pyenv/versions/3.11.9/bin/python -m pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
DASHSCOPE_API_KEY=你的百炼 API Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL=qwen-plus
ACCESS_TOKEN=1234
```

`ACCESS_TOKEN` 是 4 位数字局域网访问口令。服务具备执行电脑命令的能力，未配置或不是 4 位数字时会拒绝启动。

## 运行

```bash
python main.py
```

直接运行时默认使用端口 `8080`。如果端口已被占用，服务会启动失败，不会自动切换端口。

```text
局域网服务已启动。
可访问地址：
http://192.168.x.x:8080
http://172.20.10.x:8080
```

同一 Wi-Fi 下的设备可以在浏览器打开这些地址。如确实需要临时指定其他端口：

```bash
python main.py --port 8000
```

连通性检查：

```text
http://电脑IP:端口/health
```

如果需要使用原来的命令行对话模式：

```bash
python main.py --cli
```

## Docker 部署

先准备 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 后构建并启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

停止服务：

```bash
docker compose down
```

Docker 内部服务和宿主机默认都使用 `8080` 端口。`docker-compose.yml` 里的 `PUBLIC_PORT=8080` 只用于让启动日志打印正确的外部访问端口：

```text
http://服务器IP:8080
```

如果在 Docker 日志里看到 `172.x.x.x`，那通常是容器内部地址。外部访问时请使用宿主机或云服务器公网 IP，例如：

```text
http://8.148.231.238:8080
```

注意：在 Docker 模式下，智能体确认后执行的 shell 命令发生在容器内部，不是直接在宿主机系统中执行。

浏览器访问统一使用 `:8080`。

## 行为

- 首次访问：页面要求输入 `ACCESS_TOKEN`。口令只保存在当前浏览器会话中；服务重启后需要重新输入。
- 普通对话：页面直接显示模型回复。
- 命令需求：页面先显示执行影响说明、具体命令、确认按钮和取消按钮。点击确认后才会执行，执行后显示：
- 清空上下文：点击页面右上角 `清空上下文`，会清空页面对话记录、模型对话记忆和待确认命令。

```text
调用命令：<具体命令>
完成
```

或：

```text
调用命令：<具体命令>
请继续输入需求
```

## 注意

命令会在服务所在电脑的当前目录执行。请只在可信局域网中启动服务。

## 局域网和手机热点访问

- 电脑和手机连接同一个 Wi-Fi：手机浏览器打开终端打印的 `http://电脑IP:端口`。
- 电脑连接手机热点：启动服务后，使用终端打印的热点网段 IP 访问。
- 其他手机连接同一个手机热点：理论上可以访问同一个地址，但取决于手机热点是否允许设备互访。

如果其他设备访问失败：

1. 确认访问地址不是 `127.0.0.1`。
2. 在其他设备浏览器打开 `http://电脑IP:端口/health`。
3. 检查 macOS 是否弹出“允许 Python 接收传入连接”，需要选择允许。
4. 检查路由器或手机热点是否开启 AP isolation/client isolation/设备隔离。

## 文件结构

```text
main.py
network_access.py
Dockerfile
docker-compose.yml
static/
  index.html
  chat.html
  styles.css
  app.js
```
