from enum import Enum, auto
from io import TextIOWrapper
from pydantic import BaseModel, validator
from typing import Dict, Any, Optional, Union, Final, Callable
from typing_extensions import Protocol, runtime_checkable
from typeguard import typechecked
from result import Result, Ok, Err
from jsonschema import validate, ValidationError
from anyio import open_file, AsyncFile
import httpx
# ujson also works
import orjson as json

SOURCE_TYPE_KEY = "source_type"


class SourceType(Enum):
    File = "file"
    API = "api"
    Dict = "dict"


JsonSchemaDict = Dict[str, Any]

# the loading process should be offloaded to the caller
#  - str is could be parsed as file path or url
#  - dict is a json schema itself
#  - None means no schema (no validation)
JsonSchemaLoader = Union[JsonSchemaDict, str, None]


def try_load_json_schema(
        schema: JsonSchemaLoader
) -> Result[Optional[JsonSchemaDict], Exception]:
    """
    Tries to load the json schema from a file path or a url if the schema is a string

    Otherwise, it returns the schema itself if it's a dict
    """

    # TODO: search path if it's a file path
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

    if isinstance(schema, str):
        return parse_str_as_file_path_or_url(schema)
    elif isinstance(schema, dict):
        return Ok(schema)
    elif schema is None:
        return Ok(None)
    else:
        return Err(
            TypeError(f"schema must be a dict or a str; get {type(schema)}"))


def _verify_with_schema(data: Dict[str, Any],
                        schema: JsonSchemaLoader) -> Result[None, Exception]:
    """
    Verifies the data with the schema
    """
    m_schema = try_load_json_schema(schema)
    match m_schema:
        case Ok(s):
            if schema is None:
                return Ok(None)
            try:
                assert s is not None
                validate(data, s)
                return Ok(None)
            except ValidationError as e:
                return Err(e)
        case Err(e):
            return Err(e)


def _common_load_impl(
        s: str, is_verify: bool,
        verify_fn: Callable[[Dict[str, Any], JsonSchemaLoader],
                            Result[None, Exception]],
        schema: JsonSchemaLoader) -> Result[Dict[str, Any], Exception]:
    try:
        res = json.loads(s)  # pylint: disable=maybe-no-member
        if not isinstance(res, dict):
            return Err(
                TypeError("result must be a dict; get {} ({})".format(
                    res, type(res))))
        if is_verify:
            verify_res = verify_fn(res, schema)
            if verify_res.is_err():
                return Err(verify_res.unwrap_err())
        return Ok(res)
    except Exception as e:
        return Err(e)


# https://mypy.readthedocs.io/en/latest/protocols.html#using-isinstance-with-protocols
@runtime_checkable
class IDataSource(Protocol):
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


class DictSource(BaseModel):
    SOURCE_TYPE: Final[SourceType] = SourceType.Dict
    name: str
    data: Dict[str, Any]
    comment: Optional[str] = None
    json_schema: Optional[JsonSchemaDict] = None

    class Config:
        exclude = ["SOURCE_TYPE"]

    def __init__(self,
                 name: str,
                 data: Dict[str, Any],
                 comment: Optional[str] = None,
                 schema: JsonSchemaLoader = None,
                 **data_):
        super().__init__(name=name,
                         data=data,
                         comment=comment,
                         schema=schema,
                         **data_)

    @staticmethod
    def source_type() -> SourceType:
        return DictSource.SOURCE_TYPE

    def load(self, is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        load json file from `path`
        """
        if is_verify:
            verify_res = _verify_with_schema(self.data, self.json_schema)
            if verify_res.is_err():
                return Err(verify_res.unwrap_err())
        return Ok(self.data)

    async def load_async(self,
                         is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
            load json file from `path` asynchronously
            """
        return self.load(is_verify)

    def verify(self, content: Dict[str, Any]) -> Result[None, Exception]:
        """
        Verifies the data with the schema
        """
        return _verify_with_schema(content, self.json_schema)


class APISource(BaseModel):
    SOURCE_TYPE: Final[SourceType] = SourceType.API
    name: str
    url: str
    comment: Optional[str] = None
    json_schema: Optional[JsonSchemaDict] = None

    class Config:
        exclude = ["SOURCE_TYPE"]

    def __init__(self,
                 name: str,
                 url: str,
                 comment: Optional[str] = None,
                 schema: JsonSchemaLoader = None,
                 **data):
        super().__init__(name=name,
                         url=url,
                         comment=comment,
                         schema=schema,
                         **data)

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
            return _common_load_impl(res_.text, is_verify, _verify_with_schema,
                                     self.json_schema)
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
                return _common_load_impl(res_.text, is_verify,
                                         _verify_with_schema, self.json_schema)
        except Exception as e:
            return Err(e)

    def verify(self, content: Dict[str, Any]) -> Result[None, Exception]:
        """
        Verifies the data with the schema
        """
        return _verify_with_schema(content, self.json_schema)


class FileSource(BaseModel):
    SOURCE_TYPE: Final[SourceType] = SourceType.File
    name: str
    path: str
    comment: Optional[str] = None
    json_schema: Optional[JsonSchemaDict] = None

    class Config:
        exclude = ["SOURCE_TYPE"]

    def __init__(self,
                 name: str,
                 url: str,
                 comment: Optional[str] = None,
                 schema: JsonSchemaLoader = None,
                 **data):
        super().__init__(name=name,
                         url=url,
                         comment=comment,
                         schema=schema,
                         **data)

    @staticmethod
    def source_type() -> SourceType:
        return FileSource.SOURCE_TYPE

    def load(self, is_verify=True) -> Result[Dict[str, Any], Exception]:
        """
        load json file from `path`
        """
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                s = f.read()
                return _common_load_impl(s, is_verify, _verify_with_schema,
                                         self.json_schema)
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
                return _common_load_impl(s, is_verify, _verify_with_schema,
                                         self.json_schema)
            except Exception as e:
                return Err(e)

    def verify(self, content: Dict[str, Any]) -> Result[None, Exception]:
        """
        Verifies the data with the schema
        """
        return _verify_with_schema(content, self.json_schema)


def unmarshal_data_source(data: Dict[str, Any]) -> IDataSource:
    source_type = data.get(SOURCE_TYPE_KEY)
    if source_type is None:
        raise ValueError("source type is required")
    if source_type == SourceType.File.value:
        return FileSource(**data)
    elif source_type == SourceType.API.value:
        return APISource(**data)
    elif source_type == SourceType.Dict.value:
        return DictSource(**data)
    else:
        raise ValueError(f"unknown source type: {source_type}")
