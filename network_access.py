import ipaddress
import os
import re
import socket
import subprocess
from typing import List, Optional


def is_lan_ipv4(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback


def sort_lan_ips(ips: List[str]) -> List[str]:
    def priority(value: str) -> tuple:
        if value.startswith("192.168."):
            return (0, value)
        if value.startswith("172."):
            second = int(value.split(".")[1])
            if 16 <= second <= 31:
                return (1, value)
        if value.startswith("10."):
            return (2, value)
        return (3, value)

    return sorted(dict.fromkeys(ips), key=priority)


def get_lan_ips() -> List[str]:
    ips: List[str] = []

    try:
        completed = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ips.extend(
            match.group(1)
            for match in re.finditer(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", completed.stdout)
            if is_lan_ipv4(match.group(1))
        )
    except (OSError, subprocess.TimeoutExpired):
        pass

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            routed_ip = sock.getsockname()[0]
            if is_lan_ipv4(routed_ip):
                ips.append(routed_ip)
        except OSError:
            pass

    return sort_lan_ips(ips)


def is_running_in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def print_lan_access_info(port: int, public_port: Optional[int] = None) -> None:
    display_port = public_port or port
    lan_ips = get_lan_ips()
    print("局域网服务已启动。", flush=True)
    if is_running_in_docker():
        print("当前运行在 Docker 容器中。日志里的 172.x.x.x 通常是容器内部地址，外部设备不能直接访问。", flush=True)
        print(f"请使用宿主机、局域网 IP 或云服务器 IP 访问：http://宿主机IP:{display_port}", flush=True)
        print(f"本机浏览器访问：http://127.0.0.1:{display_port}", flush=True)
        print(f"连通性检查地址：http://127.0.0.1:{display_port}/health", flush=True)
    elif lan_ips:
        print("可访问地址：", flush=True)
        for lan_ip in lan_ips:
            print(f"http://{lan_ip}:{display_port}", flush=True)
        print(f"连通性检查地址：http://127.0.0.1:{display_port}/health", flush=True)
    else:
        print(f"未检测到局域网 IPv4，可先在本机访问：http://127.0.0.1:{display_port}", flush=True)
        print(f"连通性检查地址：http://127.0.0.1:{display_port}/health", flush=True)
    print("同一 Wi-Fi 或同一手机热点下的设备可打开可访问地址。按 Ctrl+C 停止服务。", flush=True)
    print("如果其他设备无法访问，请检查 macOS 防火墙、热点/路由器设备隔离。", flush=True)
