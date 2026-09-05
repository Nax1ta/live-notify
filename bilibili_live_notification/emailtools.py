"""Send email."""
import email
import logging
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from . import config

LOGGER = logging.getLogger(__name__)

# 依次尝试的端口：465/994 为隐式 SSL，587/25 先连普通端口再尝试 STARTTLS。
# 云厂商（含 GitHub Actions 运行环境）通常封锁 25/587 而出站 465/994 可用。
SMTP_FALLBACK_PORTS = (465, 994, 587, 25)


def _open_session(host: str, port: int):
    """连接 host:port，按端口选择正确的 TLS 模式。"""
    if port in (465, 994):
        session = smtplib.SMTP_SSL(host=host, port=port, timeout=10)
    else:
        session = smtplib.SMTP(host=host, port=port, timeout=10)
        try:
            session.starttls()
        except smtplib.SMTPNotSupportedError:
            pass
    session.ehlo(host)
    return session


def _port_candidates() -> list:
    primary = int(config.EMAIL_PORT or "465")
    ports = [primary]
    for p in SMTP_FALLBACK_PORTS:
        if p not in ports:
            ports.append(p)
    return ports


def _deliver(msg, to_addrs: list) -> None:
    errors = []
    for port in _port_candidates():
        session = None
        try:
            session = _open_session(config.EMAIL_HOST, port)
            session.login(user=config.EMAIL_USER, password=config.EMAIL_PASSWORD)
            session.sendmail(
                from_addr=config.EMAIL_USER,
                to_addrs=to_addrs,
                msg=msg.as_string(),
            )
            LOGGER.info("sent via %s:%s", config.EMAIL_HOST, port)
            return
        except Exception as e:
            errors.append(
                "%s:%s -> %s: %s" % (config.EMAIL_HOST, port, type(e).__name__, e)
            )
        finally:
            if session is not None:
                try:
                    session.quit()
                except Exception:
                    pass
    raise RuntimeError(
        "邮件发送失败：所有 SMTP 端口均不可用（可能被网络封锁或服务器异常）。\n%s"
        % "\n".join(errors)
    )


def check_smtp() -> int:
    """只登录验证（不发送任何邮件），返回可用的端口；全部失败则抛出异常。"""
    errors = []
    for port in _port_candidates():
        session = None
        try:
            session = _open_session(config.EMAIL_HOST, port)
            session.login(user=config.EMAIL_USER, password=config.EMAIL_PASSWORD)
            LOGGER.info("smtp check ok: %s:%s", config.EMAIL_HOST, port)
            return port
        except Exception as e:
            errors.append(
                "%s:%s -> %s: %s" % (config.EMAIL_HOST, port, type(e).__name__, e)
            )
        finally:
            if session is not None:
                try:
                    session.quit()
                except Exception:
                    pass
    raise RuntimeError(
        "SMTP 探测失败：所有端口均不可用。\n%s" % "\n".join(errors)
    )


def _build_base(subject: str, to_addrs: list):
    msg = MIMEMultipart("related")
    msg["From"] = email.utils.formataddr(
        ("哔哩哔哩开播提醒", config.EMAIL_FROM),
    )
    msg["To"] = msg["From"]
    msg["Bcc"] = ",".join(to_addrs)
    msg["Subject"] = subject
    msg.add_header("Sender", config.EMAIL_FROM)
    return msg


def send(to_addrs: list, subject: str, payload: str):
    """Send a plain-text mail.

    Args:
        to_addrs (list): To address.
        subject (str): Mail subject.
        payload (str): Mail payload.
    """
    if not to_addrs:
        return

    msg = email.message.Message()
    msg["From"] = email.utils.formataddr(
        ("哔哩哔哩开播提醒", config.EMAIL_FROM),
    )
    msg["To"] = msg["From"]
    msg["Bcc"] = ",".join(to_addrs)
    msg["Subject"] = subject
    msg.add_header("Sender", config.EMAIL_FROM)
    msg.set_payload(payload, "utf-8")

    _deliver(msg, to_addrs)


def send_html(
    to_addrs: list,
    subject: str,
    html: str,
    *,
    text: str = "",
    images: Optional[List[dict]] = None,
):
    """Send a HTML mail with optional inline images.

    Args:
        to_addrs (list): To address.
        subject (str): Mail subject.
        html (str): HTML body.
        text (str): plain text alternative body.
        images (list[dict], optional): dict with keys:
            cid (str): content id, referenced by `<img src="cid:{cid}">`
            data (bytes): image content
            subtype (str): image subtype, e.g. "jpeg"
    """
    if not to_addrs:
        return

    msg = _build_base(subject, to_addrs)
    alt = MIMEMultipart("alternative")
    if text:
        alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    for image in images or []:
        image_msg = MIMEImage(image["data"], image.get("subtype", "jpeg"))
        image_msg.add_header("Content-ID", "<%s>" % image["cid"])
        image_msg.add_header("Content-Disposition", "inline")
        msg.attach(image_msg)

    _deliver(msg, to_addrs)
