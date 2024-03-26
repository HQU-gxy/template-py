from enum import Enum, auto
from pydantic import BaseModel, PrivateAttr, validator, root_validator
from typing import Dict, Any, List, Optional, Callable, Set
from typing_extensions import Protocol, TypedDict, runtime_checkable, NotRequired
from result import Result, Ok, Err
from .expr import LazyExpr, EnvDict, LazyExprDict
from app.data_source.model import DataSource, unmarshal_data_source
from functools import lru_cache
from typeguard import check_type, typechecked
from jsonpath_ng import parse, jsonpath

FormatterFn = Callable[[Any], str] | LazyExpr[Callable[[Any], str]]
ValidatorFn = Callable[[Any], bool] | LazyExpr[Callable[[Any], bool]]
PreprocessorFn = Callable[[Any], Any] | LazyExpr[Callable[[Any], Any]]
NullableType = Optional[type]

LazyExprLike = str | LazyExprDict | LazyExpr
ImportsLike = List[str]


def common_parse_expr(expr: LazyExprLike,
                      imports: Optional[ImportsLike] = None) -> LazyExpr:
    if isinstance(expr, dict):
        imports_ = expr.get("imports", [])
        return LazyExpr(raw=expr["raw"],
                        imports=[*imports, *imports_] if imports else imports_)
    elif isinstance(expr, str):
        return LazyExpr(raw=expr, imports=imports)
    elif isinstance(expr, LazyExpr):
        if imports:
            imports_ = expr.imports if expr.imports else []
            return LazyExpr(raw=expr.raw, imports=[*imports, *imports_])
        return expr
    else:
        raise ValueError(f"Invalid type {type(expr)}")


def common_parse_type(t: str | type | None,
                      imports: Optional[ImportsLike] = None) -> NullableType:
    if t is None:
        return None
    if isinstance(t, type):
        return t
    expr = LazyExpr(raw=t, imports=imports)
    t = expr.eval()
    if not isinstance(t, type):
        raise ValueError(f"Expected type must be a type. Get {t} ({type(t)})")
    return t


def nullable_common_parse_expr(
        expr: Optional[LazyExprLike],
        imports: Optional[ImportsLike] = None) -> LazyExpr | None:
    if expr is None:
        return None
    return common_parse_expr(expr, imports)


def common_format_impl(val: Any,
                       formatter: Optional[FormatterFn],
                       env: EnvDict = None) -> str:
    if formatter is not None:
        if isinstance(formatter, LazyExpr):
            return formatter(val, env=env)
        elif isinstance(formatter, Callable):
            return formatter(val)
        else:
            raise ValueError(f"Invalid formatter type {type(formatter)}")
    return str(val)


def common_preprocess_impl(val: Any,
                           preprocessor: Optional[PreprocessorFn],
                           env: EnvDict = None) -> Any:
    if preprocessor is not None:
        if isinstance(preprocessor, LazyExpr):
            return preprocessor(val, env=env)
        elif isinstance(preprocessor, Callable):
            return preprocessor(val)
        else:
            raise ValueError(f"Invalid preprocessor type {type(preprocessor)}")


def common_verify_impl(val: Any,
                       verifier: Optional[ValidatorFn],
                       t: NullableType = None,
                       env: EnvDict = None) -> bool:

    def validate_with_verifier(val: Any) -> bool:
        if verifier:
            if isinstance(verifier, LazyExpr):
                return verifier(val, env=env)
            elif isinstance(verifier, Callable):
                return verifier(val)
        return True

    def validate_with_expected_type(val: Any) -> bool:
        if t:
            try:
                check_type(val, t)
            except TypeError:
                return False
        return True

    return validate_with_verifier(val) and validate_with_expected_type(val)


@runtime_checkable
class Variable(Protocol):

    @property
    def name(self) -> str:
        ...

    @property
    def unbound(self) -> set[str]:
        ...

    @classmethod
    def set_default_imports(cls, imports: ImportsLike):
        ...

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        ...

    def format(self, env: EnvDict = None) -> str:
        ...

    def verify(self, env: EnvDict = None) -> bool:
        return True


class LiteralVariable(BaseModel):
    """
    LiteralVariable represents a variable that has a expression that can be evaluated to a value
    """
    name: str
    # for some reasom, LazyExpr and LazyExpr[Any] are not compatible
    # they are not the same type
    expr: LazyExpr
    comment: Optional[str] = None
    formatter: Optional[FormatterFn] = None
    t: NullableType = None
    _imports: Optional[ImportsLike] = PrivateAttr(default=None)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def set_default_imports(cls, imports: ImportsLike):
        cls._imports = imports

    @property
    def default_imports(self):
        return self._imports

    @validator("expr", pre=True)
    def _parse_expr(cls, v: LazyExprLike) -> LazyExpr[Any]:  # pylint: disable=no-self-argument
        return common_parse_expr(v, cls._imports)

    @validator("formatter", pre=True)
    def _parse_formatter(  # pylint: disable=no-self-argument
            cls, v: Optional[LazyExprLike]) -> Optional[FormatterFn]:
        return nullable_common_parse_expr(v, cls._imports)

    @validator("t", pre=True)
    def _parse_expected_type(cls, v: str | type | None) -> NullableType:  # pylint: disable=no-self-argument
        return common_parse_type(v, cls._imports)

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        """
        Load the value from the expression
        """
        try:
            val = self.expr.eval(env)
            return Ok(val)
        except Exception as e:
            return Err(e)

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self.expr, LazyExpr):
            s |= self.expr.unbound
        return s

    def format(self, env: EnvDict = None) -> str:
        return common_format_impl(self.load(env).unwrap(), self.formatter, env)

    def verify(self, env: EnvDict = None) -> bool:
        val = self.load(env)
        return common_verify_impl(val.unwrap(), None, self.t, env)


class PathVariable(BaseModel):
    """
    PathVariable represents a variable that can be extracted from a dictionary-like structure
    """
    name: str
    source: Dict[str, Any]
    json_path: str
    comment: Optional[str] = None
    formatter: Optional[FormatterFn] = None
    verifier: Optional[ValidatorFn] = None
    preprocessor: Optional[PreprocessorFn] = None
    # the type that after preprocessing
    t: NullableType = None
    _imports: Optional[ImportsLike] = PrivateAttr(default=None)

    @classmethod
    def set_default_imports(cls, imports: ImportsLike):
        cls._imports = imports

    @property
    def default_imports(self):
        return self._imports

    @validator("formatter", pre=True)
    def _parse_formatter(  # pylint: disable=no-self-argument
            cls, v: Optional[LazyExprLike]) -> Optional[FormatterFn]:
        return nullable_common_parse_expr(v, cls._imports)

    @validator("verifier", pre=True)
    def _parse_verifier(  # pylint: disable=no-self-argument
            cls, v: Optional[LazyExprLike]) -> Optional[ValidatorFn]:
        return nullable_common_parse_expr(v, cls._imports)

    @validator("preprocessor", pre=True)
    def _parse_preprocessor(  # pylint: disable=no-self-argument
            cls, v: Optional[LazyExprLike]) -> Optional[PreprocessorFn]:
        return nullable_common_parse_expr(v, cls._imports)

    @validator("t", pre=True)
    def _parse_expected_type(cls, v: str | type | None) -> NullableType:  # pylint: disable=no-self-argument
        return common_parse_type(v)

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self.formatter, LazyExpr):
            s |= self.formatter.unbound
        if isinstance(self.verifier, LazyExpr):
            s |= self.verifier.unbound
        if isinstance(self.preprocessor, LazyExpr):
            s |= self.preprocessor.unbound
        return s

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        """
        Load the data from the data source and apply the json path
        TODO: cache the data source loadings
        """
        # since it's not an expression we don't need env
        # env exists for the sake of consistency/satisfaction of the protocol
        _env = env or {}
        data = self.source
        try:
            json_path_expr = parse(self.json_path)
            match = json_path_expr.find(data)
            if not match:
                return Err(ValueError("No match found"))
            return Ok(match[0].value)
        except Exception as e:
            return Err(e)

    def preprocess(self, item: Any, env: EnvDict = None) -> Any:
        if self.preprocessor is not None:
            return common_preprocess_impl(item, self.preprocessor, env)
        return item

    def format(self, env: EnvDict = None) -> str:
        return common_format_impl(self.load(env).unwrap(), self.formatter, env)

    def verify(self, env: EnvDict = None) -> bool:
        val = self.load(env)
        return common_verify_impl(self.preprocess(val.unwrap()), self.verifier,
                                  self.t, env)
