from enum import Enum, auto
from altair import LayerChart
from pexpect import ExceptionPexpect
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Callable, Set
from typing_extensions import Protocol, TypedDict, runtime_checkable, NotRequired
from result import Result, Ok, Err
from .expr import LazyExpr, EnvDict
from app.data_source.model import DataSource
from typeguard import check_type, typechecked
from jsonpath_ng import parse, jsonpath

FormatterFn = Callable[[Any], str] | LazyExpr[Callable[[Any], str]] | None
ValidatorFn = Callable[[Any], bool] | LazyExpr[Callable[[Any], bool]] | None
PreprocessorFn = Callable[[Any], Any] | LazyExpr[Callable[[Any], Any]] | None
ExpectedType = Optional[type]


@runtime_checkable
class Variable(Protocol):

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        ...

    def value(self, env: EnvDict = None) -> Any:
        ...

    @property
    def name(self) -> str:
        ...

    @property
    def comment(self) -> Optional[str]:
        ...

    @property
    def unbound(self) -> set[str]:
        ...

    @property
    def expected_type(self) -> ExpectedType:
        ...

    def format(self, env: EnvDict = None) -> str:
        return str(self.value)

    def validate(self, env: EnvDict = None) -> bool:
        return True


class LiteralVariableDict(TypedDict):
    name: str
    comment: Optional[str]
    formatter: NotRequired[str]
    expected_type: NotRequired[str]
    value: str


class LiteralVariable(Variable):
    _name: str
    _comment: Optional[str] = None
    formatter: FormatterFn = None
    _value: LazyExpr
    _evaluated_value: ExpectedType = None

    def __init__(self,
                 name: str,
                 value: LazyExpr,
                 comment: Optional[str] = None,
                 formatter: FormatterFn = None,
                 expected_type: ExpectedType = None):
        self._name = name
        self._value = value
        self.formatter = formatter
        self._comment = comment
        self._expected_type = expected_type

    @staticmethod
    def from_dict(
        data: LiteralVariableDict,
        imports: Optional[List[str]] = None
    ) -> Result["LiteralVariable", Exception]:
        try:
            name = data["name"]
            comment = data["comment"]
            value = LazyExpr(data["value"], imports)
            formatter_ = data.get("formatter", None)
            formatter = LazyExpr(formatter_, imports) if formatter_ and len(
                formatter_.strip()) != 0 else None
            expected_type_ = data.get("expected_type", None)
            expected_type__ = LazyExpr(
                expected_type_, imports) if expected_type_ and len(
                    expected_type_.strip()) != 0 else None
            expected_type___: ExpectedType = None
            if expected_type__ is not None:
                if (t :=
                        expected_type__.eval()) is not None and not isinstance(
                            t, type):
                    expected_type___ = t
                    raise ValueError(
                        f"Expected type must be a type. Get {t} ({type(t)})")
            return Ok(
                LiteralVariable(name, value, comment, formatter,
                                expected_type___))
        except Exception as e:
            return Err(e)

    @property
    def expected_type(self) -> ExpectedType:
        return self._expected_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def comment(self) -> Optional[str]:
        return self._comment

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        """
        Load the value from the expression
        """
        try:
            val = self._value.eval(env)
            return Ok(val)
        except Exception as e:
            return Err(e)

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self._value, LazyExpr):
            s |= self._value.unbound
        return s

    def value(self, env: EnvDict = None) -> Any:
        """
        Check cache first, if not found, load the value from the expression
        """
        if self._evaluated_value:
            return self._evaluated_value
        res = self.load(env)
        if res.is_err():
            raise res.unwrap_err()
        self._evaluated_value = res.unwrap()
        return self._evaluated_value

    def format(self, env: EnvDict = None) -> str:
        val = self.value(env)
        if self.formatter:
            if isinstance(self.formatter, LazyExpr):
                return self.formatter(val, env=env)
            elif isinstance(self.formatter, Callable):
                return self.formatter(val)
        return str(val)

    def validate(self, env: EnvDict = None) -> bool:
        val = self.value(env)

        def validate_with_expected_type(val: Any) -> bool:
            if self.expected_type:
                check_type(val, self.expected_type)
            return True

        return validate_with_expected_type(val)


class JsonPathVariableDict(TypedDict):
    name: str
    comment: Optional[str]
    formatter: NotRequired[str]
    validator: NotRequired[str]
    preprocessor: NotRequired[str]
    expected_type: NotRequired[str]
    data_source: str
    json_path: str


class JsonPathVariable(Variable):
    _name: str
    _comment: Optional[str] = None
    formatter: FormatterFn = None
    validator: ValidatorFn = None
    preprocessor: PreprocessorFn = None
    _data_source: DataSource
    _json_path: str
    # value after preprocessed
    _value: Any = None
    _evaluated_value: ExpectedType = None

    def __init__(self,
                 name: str,
                 data_source: DataSource,
                 json_path: str,
                 comment: Optional[str] = None,
                 formatter: FormatterFn = None,
                 validator: ValidatorFn = None,
                 preprocessor: PreprocessorFn = None,
                 expected_type: ExpectedType = None):
        self._name = name
        self._data_source = data_source
        self._json_path = json_path
        self._comment = comment
        self.formatter = formatter
        self.validator = validator
        self.preprocessor = preprocessor
        self._expected_type = expected_type

    @staticmethod
    def from_dict(
        data: JsonPathVariableDict,
        data_sources: List[DataSource],
        imports: Optional[List[str]] = None
    ) -> Result["JsonPathVariable", Exception]:
        try:
            name = data["name"]
            comment = data["comment"]
            formatter_ = data.get("formatter", None)
            formatter = LazyExpr(formatter_, imports) if formatter_ and len(
                formatter_.strip()) != 0 else None
            validator_ = data.get("validator", None)
            validator = LazyExpr(validator_, imports) if validator_ and len(
                validator_.strip()) != 0 else None
            preprocessor_ = data.get("preprocessor", None)
            preprocessor = LazyExpr(preprocessor_,
                                    imports) if preprocessor_ and len(
                                        preprocessor_.strip()) != 0 else None
            data_source = next(
                (ds for ds in data_sources if ds.name == data["data_source"]),
                None)
            json_path = data["json_path"]
            expected_type_ = data.get("expected_type", None)
            expected_type__ = LazyExpr(
                expected_type_, imports) if expected_type_ and len(
                    expected_type_.strip()) != 0 else None
            expected_type___: ExpectedType = None
            if expected_type__ is not None:
                if (t :=
                        expected_type__.eval()) is not None and not isinstance(
                            t, type):
                    expected_type___ = t
                    raise ValueError(
                        f"Expected type must be a type. Get {t} ({type(t)})")
        except Exception as e:
            return Err(e)
        if not data_source:
            return Err(
                ValueError(f"Data source {data['data_source']} not found"))
        return Ok(
            JsonPathVariable(name, data_source, json_path, comment, formatter,
                             validator, preprocessor, expected_type___))

    @property
    def expected_type(self) -> ExpectedType:
        return self._expected_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def comment(self) -> Optional[str]:
        return self._comment

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        """
        Load the data from the data source and apply the json path
        TODO: cache the data...
        """
        res = self._data_source.load()
        if res.is_err():
            return res
        data = res.unwrap()
        try:
            json_path_expr = parse(self._json_path)
            match = json_path_expr.find(data)
            if not match:
                return Err(ValueError("No match found"))
            return Ok(match[0].value)
        except Exception as e:
            return Err(e)

    def value(self, env: EnvDict = None) -> Any:
        """
        Check cache first, if not found, load the data and apply the json path
        and then preprocess the value
        """
        if self._value:
            return self._value
        res = self.load()
        if res.is_err():
            raise res.unwrap_err()
        self._value = self.preprocess(res.unwrap(), env=env)
        return self._value

    def preprocess(self, item: Any, env: EnvDict = None) -> Any:
        """
        Run the preprocessor on the item
        """
        if self.preprocessor:
            if isinstance(self.preprocessor, LazyExpr):
                return self.preprocessor(item, env=env)  # pylint: disable=not-callable
            if isinstance(self.preprocessor, Callable):
                return self.preprocessor(item)  # pylint: disable=not-callable
        return item

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self.formatter, LazyExpr):
            s |= self.formatter.unbound
        if isinstance(self.validator, LazyExpr):
            s |= self.validator.unbound
        if isinstance(self.preprocessor, LazyExpr):
            s |= self.preprocessor.unbound
        return s

    def format(self, env: EnvDict = None) -> str:
        val = self.value(env)
        if self.formatter:
            if isinstance(self.formatter, LazyExpr):
                return self.formatter(val, env=env)  # pylint: disable=not-callable
            elif isinstance(self.formatter, Callable):
                return self.formatter(val)  # pylint: disable=not-callable
        return str(val)

    def validate(self, env: EnvDict = None) -> bool:
        val = self.value(env)

        def validate_with_validator(val: Any) -> bool:
            if self.validator:
                if isinstance(self.validator, LazyExpr):
                    return self.validator(val, env=env)  # pylint: disable=not-callable
                elif isinstance(self.validator, Callable):
                    return self.validator(val)  # pylint: disable=not-callable
            return True

        def validate_with_expected_type(val: Any) -> bool:
            if self.expected_type:
                check_type(val, self.expected_type)
            return True

        return validate_with_validator(val) and validate_with_expected_type(
            val)
