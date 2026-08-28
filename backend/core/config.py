import logging

from dotenv import load_dotenv
from google import genai

# 加载环境变量
load_dotenv()


logger = logging.getLogger(__name__)


class GoogleClients:
    def __init__(self):
        self.gemini_client = None
        self._init_clients()

    def _init_clients(self):
        try:
            self.gemini_client = genai.Client()
            logger.info("Gemini SDK 客户端初始化成功")
        except Exception:
            logger.exception("Gemini SDK 客户端初始化失败，请检查 GEMINI_API_KEY")


# 单例模式，供全局调用
clients = GoogleClients()
