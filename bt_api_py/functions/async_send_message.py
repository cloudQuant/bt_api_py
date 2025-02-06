import base64
import hashlib
import hmac
import random
import time
import traceback
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import aiosmtplib
from bt_api_py.functions.async_base import AsyncBase
# from bt_api_py.functions.calculate_time import get_string_tz_time
# from bt_api_py.functions.log_message import SpdLogManager
# spd_log = SpdLogManager("./log/async_data.log", "rest_async", 0, 0, False)
# logger = spd_log.create_logger()


class FeishuManagerAsync(AsyncBase):

    def __init__(self):
        super().__init__()
        self.host = "https://open.larksuite.com/open-apis/bot/v2/hook/"

    # noinspection PyMethodMayBeStatic
    def gen_sign(self, timestamp, secret):
        # 拼接timestamp和secret

        string_to_sign = "{}\n{}".format(timestamp, secret)
        hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()

        # 对结果进行base64处理
        sign = base64.b64encode(hmac_code).decode("utf-8")

        return sign

    def async_send(self, content,
                   bot="4b90880c-3015-4e98-aac5-1248c55e8730",
                   secret=None):
        data = {
            "msg_type": "text",
            "content": {"text": content}
        }
        timestamp = int(time.time())
        if secret:
            sign = self.gen_sign(timestamp, secret)
            data["timestamp"] = timestamp
            data["sign"] = sign
        self.submit(self.async_http_request("post", self.host + bot, body=data))


class EmailManagerAsync(AsyncBase):
    def __init__(self, from_email_list=None, to_email_list=None):
        super().__init__()
        if from_email_list is None:
            self.from_email_list = []
        else:
            self.from_email_list = from_email_list
        if to_email_list is None:
            self.to_email_list = []
        else:
            self.to_email_list = to_email_list

    async def __send_email(self, title, content, sender=None, receiver=None, files=None):
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
        # 发送附件
        if files and isinstance(files, list):
            for i in files:
                part_attach1 = MIMEApplication(open(i, "rb").read())  # 打开附件
                part_attach1.add_header("Content-Disposition", "attachment", filename=i)  # 为附件命名
                msg_root.attach(part_attach1)
        try:
            async with aiosmtplib.SMTP(
                    hostname=sender.get("smtp_server"),
                    port=465,
                    use_tls=True, timeout=5,
                    validate_certs=False
            ) as smtp:
                await smtp.login(sender_mail, sender.get("sender_pass"))
                await smtp.sendmail(sender_mail, receiver, msg_root.as_string())
        except Exception as e:
            print(traceback.format_exc(), e)

    def async_send(self, title, content, sender=None, receiver=None, files=None):
        self.submit(self.__send_email(title, content, sender, receiver, files))


if __name__ == "__main__":
    a1 = time.perf_counter()
    print("test feishu function")
    feishu_manager = FeishuManagerAsync()
    lark_bot = "368e0d1e-523b-4020-9122-80efaf935b4e"
    feishu_manager.async_send("test async feishu function", bot=lark_bot)
    b1 = time.perf_counter()
    print(f"call feishu_function consume time is {b1 - a1}")
    time.sleep(5)

