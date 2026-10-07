"""服务层统一异常。"""

from __future__ import annotations


class ServiceError(Exception):
    """携带稳定错误码的业务异常，供 API 映射 HTTP 状态、供测试断言。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
