from enum import Enum, auto
from pydantic import BaseModel
from typing import Dict, Any, Optional, Union, Final
from typing_extensions import Protocol, runtime_checkable
from typeguard import typechecked
from result import Result, Ok, Err
from jsonschema import validate, ValidationError
from anyio import open_file
import httpx
# ujson also works
import orjson as json


class SourceType(Enum):
    JSON = auto()
    API = auto()


# str is could be parsed as file path or url
# dict is a json schema itself
# None means no schema (no validation)
JsonSchemaLoader = Union[Dict[str, Any], str, None]


def verify(data: Dict[str, Any],
           schema: JsonSchemaLoader) -> Result[None, Exception]:
    """
    Verifies the data with the schema
    """
    if schema is None:
        return Ok(None)

    def parse_str_as_file_path_or_url(
            s: str) -> Result[Dict[str, Any], Exception]:
        # if it's start with http(s), it's a url
        if s.startswith("http://") or s.startswith("https://"):
            try:
                res_ = httpx.get(s, timeout=5)
                res = json.loads(res_.text)  # pylint: disable=maybe-no-member
                if not isinstance(res, dict):
                    return Err(
                        TypeError("result must be a dict; get {} ({})".format(
                            res, type(res))))
                return Ok(res)
            except Exception as e:
                return Err(e)
        else:
            try:
                with open(s, "r", encoding="utf-8") as f:
                    s = f.read()
                    res = json.loads(s)  # pylint: disable=maybe-no-member
                    if not isinstance(res, dict):
                        return Err(
                            TypeError(
                                "result must be a dict; get {} ({})".format(
                                    res, type(res))))
                    return Ok(res)
            except Exception as e:
                return Err(e)

    def handle_str(s: str) -> Result[None, Exception]:
        schema_res = parse_str_as_file_path_or_url(s)
        match schema_res:
            case Ok(schema):
                try:
                    validate(data, schema)
                    return Ok(None)
                except ValidationError as e:
                    return Err(e)
            case Err(e):
                return Err(e)

    if isinstance(schema, str):
        return handle_str(schema)
    elif isinstance(schema, dict):
        try:
            validate(data, schema)
            return Ok(None)
        except ValidationError as e:
            return Err(e)
    elif schema is None:
        return Ok(None)
    else:
        return Err(
            TypeError(f"schema must be a dict or a str; get {type(schema)}"))


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
        """
        Returns the type of the data source
        """
        ...

    # TODO: add an optional `options` parameter and async support
    def load(self, is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        Loads the data source and returns the data as a dictionary
        """
        ...

    async def load_async(self,
                         is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        Loads the data source asynchronously and returns the data as a dictionary
        """
        ...


class APISource(BaseModel):
    SOURCE_TYPE: Final[SourceType] = SourceType.API
    name: str
    url: str
    comment: Optional[str] = None
    schema: JsonSchemaLoader = None

    @staticmethod
    def source_type() -> SourceType:
        return APISource.SOURCE_TYPE

    def load(self, is_verify=True) -> Result[Dict[str, Any], Exception]:
        """load json file from `url`

        Returns:
            Result[Dict[str, Any], Exception]: a dict or an exception
        """
        try:
            res_ = httpx.get(self.url, timeout=5)
            res = json.loads(res_.text)  # pylint: disable=maybe-no-member
            if not isinstance(res, dict):
                return Err(
                    TypeError("result must be a dict; get {} ({})".format(
                        res, type(res))))
            if is_verify:
                verify_res = verify(res, self.schema)
                if verify_res.is_err():
                    return Err(verify_res.unwrap_err())
            return Ok(res)
        except Exception as e:
            return Err(e)

    async def load_async(self,
                         is_verify=True) -> Result[Dict[str, Any], Exception]:
        """load json file from `url` asynchronously

        Returns:
            Result[Dict[str, Any], Exception]: a dict or an exception
        """
        try:
            async with httpx.AsyncClient() as client:
                res_ = await client.get(self.url, timeout=5)
                res = json.loads(res_.text)  # pylint: disable=maybe-no-member
                if not isinstance(res, dict):
                    return Err(
                        TypeError("result must be a dict; get {} ({})".format(
                            res, type(res))))
                if is_verify:
                    verify_res = verify(res, self.schema)
                    if verify_res.is_err():
                        return Err(verify_res.unwrap_err())
                return Ok(res)
        except Exception as e:
            return Err(e)

    def verify(self, content: Dict[str, Any]) -> Result[None, Exception]:
        """
        Verifies the data with the schema
        """
        return verify(content, self.schema)


class JsonSource(BaseModel):
    SOURCE_TYPE: Final[SourceType] = SourceType.JSON
    name: str
    path: str
    comment: Optional[str] = None
    schema: JsonSchemaLoader = None

    @staticmethod
    def source_type() -> SourceType:
        return JsonSource.SOURCE_TYPE

    def load(self, is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        load json file from `path`
        """
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                s = f.read()
                res = json.loads(s)  # pylint: disable=maybe-no-member
                if not isinstance(res, dict):
                    return Err(
                        TypeError("result must be a dict; get {} ({})".format(
                            res, type(res))))
                if is_verify:
                    verify_res = verify(res, self.schema)
                    if verify_res.is_err():
                        return Err(verify_res.unwrap_err())
                return Ok(res)
            except Exception as e:
                return Err(e)

    async def load_async(self,
                         is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        load json file from `path` asynchronously
        """
        async with await open_file(self.path, "r", encoding="utf-8") as f:
            try:
                s = await f.read()
                res = json.loads(s)  # pylint: disable=maybe-no-member
                if not isinstance(res, dict):
                    return Err(
                        TypeError("result must be a dict; get {} ({})".format(
                            res, type(res))))
                if is_verify:
                    verify_res = verify(res, self.schema)
                    if verify_res.is_err():
                        return Err(verify_res.unwrap_err())
                return Ok(res)
            except Exception as e:
                return Err(e)

    def verify(self, content: Dict[str, Any]) -> Result[None, Exception]:
        """
        Verifies the data with the schema
        """
        return verify(content, self.schema)
