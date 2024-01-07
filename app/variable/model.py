from enum import Enum, auto
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Callable, Set
from typing_extensions import Protocol, TypedDict, runtime_checkable
from result import Result, Ok, Err
from .expr import LazyExpr
from app.data_source.model import DataSource
from jsonpath_ng import parse, jsonpath

FormatterFn = Callable[[Any], str] | LazyExpr | None
ValidatorFn = Callable[[Any], bool] | LazyExpr | None
PreprocessorFn = Callable[[Any], Any] | LazyExpr | None


@runtime_checkable
class Variable(Protocol):
    name: str
    comment: Optional[str] = None

    def load(self) -> Result[Any, Exception]:
        ...

    @property
    def value(self) -> Any:
        ...

    def format(self) -> str:
        return str(self.value)

    def validate(self) -> bool:
        return True


class LiteralVariableDict(TypedDict):
    name: str
    comment: Optional[str]
    value: str


class LiteralVariable(Variable):
    name: str
    comment: Optional[str] = None
    _value: LazyExpr
    _evaluated_value: Optional[Any] = None

    def __init__(
        self,
        name: str,
        value: LazyExpr,
        comment: Optional[str] = None,
    ):
        self.name = name
        self._value = value
        self.comment = comment

    @staticmethod
    def from_dict(
        data: LiteralVariableDict,
        imports: Optional[List[str]] = None
    ) -> Result["LiteralVariable", Exception]:
        try:
            name = data["name"]
            comment = data["comment"]
            value = LazyExpr(data["value"], imports)
            return Ok(LiteralVariable(name, value, comment))
        except Exception as e:
            return Err(e)

    def load(self) -> Result[Any, Exception]:
        try:
            val = self._value.eval()
            return Ok(val)
        except Exception as e:
            return Err(e)

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self._value, LazyExpr):
            s |= self._value.unbound
        return s

    @property
    def value(self) -> Any:
        if self._evaluated_value:
            return Ok(self._evaluated_value)
        res = self.load()
        if res.is_err():
            raise res.unwrap_err()
        self._evaluated_value = res.unwrap()
        return Ok(self._evaluated_value)


class JsonVariableDict(TypedDict):
    name: str
    comment: Optional[str]
    formatter: str
    validator: str
    preprocessor: str
    data_source: str
    json_path: str


class JsonVariable(Variable):
    name: str
    comment: Optional[str] = None
    formatter: FormatterFn = None
    validator: ValidatorFn = None
    preprocessor: PreprocessorFn = None
    data_source: DataSource
    json_path: str
    # value after preprocessed
    _value: Any = None

    def __init__(self,
                 name: str,
                 data_source: DataSource,
                 json_path: str,
                 comment: Optional[str] = None,
                 formatter: FormatterFn = None,
                 validator: ValidatorFn = None,
                 preprocessor: PreprocessorFn = None):
        self.name = name
        self.data_source = data_source
        self.json_path = json_path
        self.comment = comment
        self.formatter = formatter
        self.validator = validator
        self.preprocessor = preprocessor

    @staticmethod
    def from_dict(
        data: JsonVariableDict,
        data_sources: List[DataSource],
        imports: Optional[List[str]] = None
    ) -> Result["JsonVariable", Exception]:
        name = data["name"]
        comment = data["comment"]
        formatter = LazyExpr(data["formatter"],
                             imports) if data["formatter"] and len(
                                 data["formatter"]) != 0 else None
        validator = LazyExpr(data["validator"],
                             imports) if data["validator"] and len(
                                 data["validator"]) != 0 else None
        preprocessor = LazyExpr(data["preprocessor"],
                                imports) if data["preprocessor"] and len(
                                    data["preprocessor"]) != 0 else None
        data_source = next(
            (ds for ds in data_sources if ds.name == data["data_source"]),
            None)
        json_path = data["json_path"]
        if not data_source:
            return Err(
                ValueError(f"Data source {data['data_source']} not found"))
        return Ok(
            JsonVariable(name, data_source, json_path, comment, formatter,
                         validator, preprocessor))

    def load(self) -> Result[Any, Exception]:
        res = self.data_source.load()
        if res.is_err():
            return res
        data = res.unwrap()
        try:
            json_path_expr = parse(self.json_path)
            match = json_path_expr.find(data)
            if not match:
                return Err(ValueError("No match found"))
            return Ok(match[0].value)
        except Exception as e:
            return Err(e)

    def preprocess(self, value: Any) -> Any:
        if self.preprocessor:
            if isinstance(self.preprocessor, LazyExpr):
                return self.preprocessor(value)  # pylint: disable=not-callable
            elif isinstance(self.preprocessor, Callable):
                return self.preprocessor(value)  # pylint: disable=not-callable
        return value

    @property
    def value(self) -> Any:
        if self._value:
            return Ok(self._value)
        res = self.load()
        if res.is_err():
            raise res.unwrap_err()
        self._value = self.preprocess(res.unwrap())
        return Ok(self._value)

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self._value, LazyExpr):
            s |= self._value.unbound
        if isinstance(self.formatter, LazyExpr):
            s |= self.formatter.unbound
        if isinstance(self.validator, LazyExpr):
            s |= self.validator.unbound
        if isinstance(self.preprocessor, LazyExpr):
            s |= self.preprocessor.unbound
        return s

    def format(self) -> str:
        if self.formatter:
            if isinstance(self.formatter, LazyExpr):
                return self.formatter(self.value)  # pylint: disable=not-callable
            elif isinstance(self.formatter, Callable):
                return self.formatter(self.value)  # pylint: disable=not-callable
        return str(self.value)

    def validate(self) -> bool:
        if self.validator:
            if isinstance(self.validator, LazyExpr):
                return self.validator(self.value)  # pylint: disable=not-callable
            elif isinstance(self.validator, Callable):
                return self.validator(self.value)  # pylint: disable=not-callable
        return True
