"""领域错误。

HTTP 层据此映射状态码：404/409/422/423 等；CLI 层据此输出中文原因。
"""

from __future__ import annotations


class TheatreError(Exception):
    """所有领域错误的基类。"""


class NotFoundError(TheatreError):
    """引用的聚合或段落不存在。"""


class ConflictError(TheatreError):
    """同一幂等标识携带了不同内容（例如回执指纹不一致）。"""


class AuthorizationError(TheatreError):
    """操作者无权完成该动作（职责分离、亲历者范围、授权缺失）。"""


class DeadlineError(TheatreError):
    """越过了截稿、开演或撤回期限。"""


class StateError(TheatreError):
    """聚合当前状态不允许该动作（重复会签、未锁场、未批准等）。"""
