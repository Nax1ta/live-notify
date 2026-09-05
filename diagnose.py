"""网络与邮件诊断脚本：在 GitHub Actions 上运行，确认端口与外部服务是否可达。

用法：python diagnose.py
    需要同目录存在 .env（由 Actions 的 secret 写入）。

检查项目：
    1. TCP 连通性：smtp.163.com 的 465/994/587/25，B 站 API 443，封面图源 443
    2. SMTP 登录（只登录不发送）：SSL 端口（465/994）与普通端口（587/25）分别尝试
    3. B 站房间 API 可达性：查询两个监控房间的实时状态
    4. 封面图可达性：下载第一个房间的封面并核对字节数
"""

import json
import os
import socket
import smtplib
import sys
import urllib.error
import urllib.request
from pathlib import Path

SMTP_TARGETS = [
    ("smtp.163.com", 465, "ssl"),
    ("smtp.163.com", 994, "ssl"),
    ("smtp.163.com", 587, "starttls"),
    ("smtp.163.com", 25, "starttls"),
]
OTHER_TARGETS = [
    ("api.live.bilibili.com (B站API)", "api.live.bilibili.com", 443),
    ("i0.hdslb.com (封面图源)", "i0.hdslb.com", 443),
]
ROOM_IDS = ["22912576", "27290966"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
}


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if os.getenv(key) is None:
            os.environ[key] = value.strip().strip('"').strip("'")


def tcp_probe(label: str, host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=8):
            print(f"[TCP] OK   {label} {host}:{port}")
    except Exception as e:
        print(f"[TCP] FAIL {label} {host}:{port} {type(e).__name__}: {e}")


def smtp_probe(host: str, port: int, mode: str, user: str, password: str) -> None:
    session = None
    try:
        if mode == "ssl":
            session = smtplib.SMTP_SSL(host=host, port=port, timeout=15)
        else:
            session = smtplib.SMTP(host=host, port=port, timeout=15)
            try:
                session.starttls()
            except smtplib.SMTPNotSupportedError:
                pass
        session.ehlo(host)
        session.login(user=user, password=password)
        print(f"[SMTP] OK   {host}:{port} ({mode}) 登录成功（未发送邮件）")
    except Exception as e:
        print(f"[SMTP] FAIL {host}:{port} ({mode}) {type(e).__name__}: {e}")
    finally:
        if session is not None:
            try:
                session.quit()
            except Exception:
                pass


def http_get(url: str) -> tuple:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as resp:
        return resp.status, resp.read(2048)


def main() -> None:
    load_env_file(Path(__file__).resolve().parent / ".env")

    print("=" * 60)
    print("1) TCP 连通性")
    print("=" * 60)
    for label, host, port in OTHER_TARGETS:
        tcp_probe(label, host, port)
    smtp_host = os.getenv("EMAIL_HOST") or "smtp.163.com"
    for host, port, mode in SMTP_TARGETS:
        tcp_probe("邮件服务器", host, port)

    user = os.getenv("EMAIL_USER") or ""
    password = os.getenv("EMAIL_PASSWORD") or ""
    print()
    print("=" * 60)
    print("2) SMTP 登录（只登录，不发送任何邮件）")
    print("=" * 60)
    if not user or not password:
        print("[SMTP] 跳过：未配置 EMAIL_USER / EMAIL_PASSWORD")
    else:
        for host, port, mode in SMTP_TARGETS:
            smtp_probe(host, port, mode, user, password)

    print()
    print("=" * 60)
    print("3) B 站房间 API（在 GitHub 服务器上真实查询）")
    print("=" * 60)
    try:
        for rid in ROOM_IDS:
            url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={rid}"
            status, body = http_get(url)
            try:
                data = json.loads(body)
                code = data.get("code")
                ri = (data.get("data") or {}).get("room_info") or {}
                live = ri.get("live_status")
                print(
                    f"[B站] OK   房间 {rid} HTTP {status} code={code} "
                    f"live_status={live} title={ri.get('title')!r}"
                )
            except Exception as e:
                print(f"[B站] WARN 房间 {rid} HTTP {status} 响应解析失败: {e}")
    except urllib.error.HTTPError as e:
        print(f"[B站] FAIL HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"[B站] FAIL {type(e).__name__}: {e}")

    print()
    print("=" * 60)
    print("4) 封面图源")
    print("=" * 60)
    try:
        url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={ROOM_IDS[0]}"
        _, body = http_get(url)
        data = json.loads(body)
        cover = ((data.get("data") or {}).get("room_info") or {}).get("cover") or ""
        if cover:
            status, head = http_get(cover)
            print(f"[封面] OK   {cover} HTTP {status} 读取 {len(head)} 字节")
        else:
            print("[封面] WARN 房间无封面字段")
    except Exception as e:
        print(f"[封面] FAIL {type(e).__name__}: {e}")

    print()
    print("诊断完成。关键看点：SMTP 栏 465 为 OK 即可放心；"
          "若 465 全部 FAIL 而 587/25 OK，请告诉我按结果调整。")


if __name__ == "__main__":
    main()
