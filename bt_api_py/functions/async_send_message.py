from __future__ import annotations

import base64
import hashlib
import hmac
import random
import time
import traceback
from collections.abc import Sequence
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

try:
    import aiosmtplib

    AIOSMTPLIB_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    aiosmtplib = None  # type: ignore[assignment]
    AIOSMTPLIB_AVAILABLE = False

from bt_api_py.functions.async_base import AsyncBase
from bt_api_py.logging_factory import get_logger

logger = get_logger("function")

# from bt_api_py.functions.calculate_time import get_string_tz_time
# from bt_api_py.functions.log_message import SpdLogManager
# spd_log = SpdLogManager("./log/async_data.log", "rest_async", 0, 0, False)
# logger = spd_log.create_logger()


class FeishuManagerAsync(AsyncBase):
    def __init__(self) -> None:
        super().__init__()
        self.host = "https://open.larksuite.com/open-apis/bot/v2/hook/"

    def gen_sign(self, timestamp: int, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        return sign

    def async_send(
        self,
        content: str,
        bot: str = "4b90880c-3015-4e98-aac5-1248c55e8730",
        secret: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"msg_type": "text", "content": {"text": content}}
        timestamp = int(time.time())
        if secret:
            sign = self.gen_sign(timestamp, secret)
            data["timestamp"] = timestamp
            data["sign"] = sign
        self.submit(self.async_http_request("post", self.host + bot, body=data))


class EmailManagerAsync(AsyncBase):
    def __init__(
        self,
        from_email_list: list[dict[str, Any]] | None = None,
        to_email_list: list[Sequence[str]] | None = None,
    ) -> None:
        super().__init__()
        self.from_email_list = from_email_list or []
        self.to_email_list = to_email_list or []

    async def __send_email(
        self,
        title: str,
        content: str,
        sender: dict[str, Any] | None = None,
        receiver: Sequence[str] | None = None,
        files: Sequence[str | Path] | None = None,
    ) -> None:
        if sender is None:
            sender = random.choice(self.from_email_list)
        if receiver is None:
            receiver = random.choice(self.to_email_list)
        sender_mail = sender.get("sender_mail", "")
        msg_root = MIMEMultipart("mixed")
        msg_root["From"] = sender_mail
        msg_root["To"] = ",".join(receiver)
        msg_root["subject"] = Header(title, "utf-8")
        text_sub = MIMEText(content, "html", "utf-8")
        msg_root.attach(text_sub)
        if files:
            for file_path in files:
                resolved_path = Path(file_path)
                with resolved_path.open("rb") as f:
                    part_attach1 = MIMEApplication(f.read())
                part_attach1.add_header(
                    "Content-Disposition", "attachment", filename=resolved_path.name
                )
                msg_root.attach(part_attach1)
        if not AIOSMTPLIB_AVAILABLE:
            raise ImportError(
                "aiosmtplib is required for email sending. Install with: pip install aiosmtplib"
            )
        try:
            async with aiosmtplib.SMTP(
                hostname=sender.get("smtp_server"),
                port=465,
                use_tls=True,
                timeout=5,
                validate_certs=False,
            ) as smtp:
                await smtp.login(sender_mail, sender.get("sender_pass"))
                await smtp.sendmail(sender_mail, receiver, msg_root.as_string())
        except Exception:
            logger.error(traceback.format_exc(), exc_info=True)

    def async_send(
        self,
        title: str,
        content: str,
        sender: dict[str, Any] | None = None,
        receiver: Sequence[str] | None = None,
        files: Sequence[str | Path] | None = None,
    ) -> None:
        self.submit(self.__send_email(title, content, sender, receiver, files))


def _main() -> None:
    a1 = time.perf_counter()
    print("test feishu function")
    feishu_manager = FeishuManagerAsync()
    lark_bot = "368e0d1e-523b-4020-9122-80efaf935b4e"
    feishu_manager.async_send("test async feishu function", bot=lark_bot)
    b1 = time.perf_counter()
    print(f"call feishu_function consume time is {b1 - a1}")
    time.sleep(5)


if __name__ == "__main__":
    _main()
