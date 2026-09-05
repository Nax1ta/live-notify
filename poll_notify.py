"""GitHub Actions 定时轮询：检测直播间是否开播，开播则发送带标题和封面的邮件。

支持的直播间：
    B 站（BILIBILI_ROOM_NAME_{房间号}=名称）——主接口 bilibili-api，
    被风控时自动切换备用接口 room/v1/Room/get_info

用法：
    python poll_notify.py            # 检测并发送
    python poll_notify.py --dry-run  # 只打印结果，不发送

容错原则：
    - 单个房间检测失败只跳过本轮（状态保持不变），不中断其他房间；
    - 只有"上一次检测未开播 -> 本次开播"的转变才会发信；
    - 发信失败：不保存状态、任务标红，下一轮自动重试；
    - state.json 只记录 was_live/title，无时间戳，避免每轮产生提交。
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

STATE_FILE = Path(__file__).resolve().parent / "state.json"
BILI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _load_env_file(path: Path) -> None:
    """简易 .env 解析（纯标准库），已存在的环境变量优先。"""
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


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8"
    )


async def _bili_room_direct(rid: str) -> dict:
    """备用接口：主接口失败/被风控时兜底。返回与 room.get 相同形状的数据。"""
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20)
    ) as session:
        async with session.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info",
            params={"room_id": rid},
            headers={"User-Agent": BILI_UA, "Referer": "https://live.bilibili.com/"},
        ) as resp:
            data = await resp.json(content_type=None)
    if data.get("code") != 0:
        raise RuntimeError(f"get_info 返回 code={data.get('code')}")
    ri = data.get("data") or {}
    ri["cover"] = ri.get("cover") or ri.get("user_cover") or ""
    return {
        "name": os.getenv(f"BILIBILI_ROOM_NAME_{rid}") or rid,
        "title": ri.get("title") or "",
        "url": f"https://live.bilibili.com/{rid}",
        "data": {"room_info": ri},
        "popularity": ri.get("online") or 0,
    }


# 各环节硬超时（秒）：宁可本轮跳过，也不让任务卡死
TIMEOUT_BILI_MAIN = 35
TIMEOUT_BILI_FALLBACK = 25


async def main():
    dry_run = "--dry-run" in sys.argv
    _load_env_file(Path(__file__).resolve().parent / ".env")

    from bilibili_live_notification import config, emailtools, rate_limit, room
    from bilibili_live_notification.__main__ import (
        _live_email_html,
        _live_email_images,
    )

    rate_limit.BILIBILI_API.set(rate_limit.RateLimiter(50, 1))

    try:
        port = emailtools.check_smtp()
        print(f"SMTP 通道正常: {config.EMAIL_HOST}:{port}")
    except Exception as e:
        print(
            f"[警告] SMTP 探测失败: {e}\n"
            "        （本轮无开播则不受影响；一旦需要发信而失败，任务会标红并自动重试）",
            file=sys.stderr,
        )

    state: dict = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf8"))

    tz = ZoneInfo("Asia/Shanghai")
    ok_count = 0
    fail_count = 0

    # ---------- B 站直播间 ----------
    for rid in config.discover_bilibili_room_id():
        try:
            try:
                data = await asyncio.wait_for(
                    room.get(rid, max_age_secs=0), timeout=TIMEOUT_BILI_MAIN
                )
                ri = (data.get("data") or {}).get("room_info") or {}
                if not ri:
                    raise RuntimeError("主接口无房间数据（可能被风控）")
            except Exception:
                print(f"[B站] {rid} 主接口不可用，尝试备用接口")
                data = await asyncio.wait_for(
                    _bili_room_direct(rid), timeout=TIMEOUT_BILI_FALLBACK
                )
                ri = data["data"]["room_info"]
        except Exception as e:
            fail_count += 1
            print(
                f"[B站] {rid} 检测失败（本轮跳过，状态保持不变）: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            continue

        ok_count += 1
        is_live = ri.get("live_status") == 1
        title = ri.get("title") or ""
        prev = state.get(rid) or {}
        was_live = prev.get("was_live")

        if is_live and was_live is False:
            now = datetime.now(tz)
            images = await _live_email_images(data)
            subject = f'[开播]{data["name"]} - {now.strftime("%H:%M:%S %Y-%m-%d")}'
            html = _live_email_html(data, with_cover=bool(images))
            text = f'{data["name"]}《{data["title"]}》开播了\n{data["url"]} '
            to_addrs = config.get_room_email_to(rid)
            if dry_run:
                print(f"DRY-RUN 将发送: {subject} -> {to_addrs} (封面: {bool(images)})")
            else:
                try:
                    emailtools.send_html(to_addrs, subject, html, text=text, images=images)
                    print(f"已发送开播通知: {subject}")
                except Exception as e:
                    print(f"[发送失败] {subject} -> {to_addrs}: {e}", file=sys.stderr)
                    sys.exit(1)

        state[rid] = {"was_live": is_live, "title": title}
        _save_state(state)
        print(f"{rid} {data.get('name', '')} live={is_live} title={title!r}")

    print(f"检查完成: 成功 {ok_count} 个, 跳过 {fail_count} 个 | 状态已保存: {STATE_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
