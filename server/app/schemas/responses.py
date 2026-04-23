from __future__ import annotations

from typing import Generic, TypeVar

from app.schemas.common import APIModel

T = TypeVar("T")


class DataResponse(APIModel, Generic[T]):
    data: T
