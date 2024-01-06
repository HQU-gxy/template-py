from enum import Enum, auto
from pydantic import BaseModel
from typing import Dict, Any, Optional
from typing_extensions import Protocol, runtime_checkable
from result import Result, Ok, Err
import requests
# or use ujson
import orjson as json


class SourceType(Enum):
    JSON = auto()
    API = auto()


# https://mypy.readthedocs.io/en/latest/protocols.html#using-isinstance-with-protocols
@runtime_checkable
class DataSource(Protocol):
    """
    DataSource is a protocol that defines the interface of a data source
    """
    name: str
    comment: Optional[str] = None

    @staticmethod
    def source_type() -> SourceType:
        ...

    # TODO: add an optional `options` parameter and async support
    def load(self) -> Result[Dict[str, Any], Exception]:
        ...


class APISource(BaseModel):
    name: str
    url: str
    comment: Optional[str] = None

    @staticmethod
    def source_type() -> SourceType:
        return SourceType.API

    def load(self) -> Result[Dict[str, Any], Exception]:
        """load json file from `url`

        Returns:
            Result[Dict[str, Any], Exception]: a dict or an exception
        """
        try:
            res_ = requests.get(self.url, timeout=5)
            res = json.loads(res_.text)  # pylint: disable=maybe-no-member
            if not isinstance(res, dict):
                return Err(Exception("json result file must be a dict"))
            return Ok(res)
        except Exception as e:
            return Err(e)


class JsonSource(BaseModel):
    name: str
    path: str
    comment: Optional[str] = None

    @staticmethod
    def source_type() -> SourceType:
        return SourceType.JSON

    def load(self) -> Result[Dict[str, Any], Exception]:
        """
        load json file from `path`
        """
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                s = f.read()
                res = json.loads(s)  # pylint: disable=maybe-no-member
                if not isinstance(res, dict):
                    return Err(Exception("json result file must be a dict"))
                return Ok(res)
            except Exception as e:
                return Err(e)
