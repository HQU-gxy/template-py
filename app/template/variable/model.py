from enum import Enum, auto
from pydantic import BaseModel, Field, PrivateAttr, model_validator, validator, root_validator
from typing import Dict, Any, List, Optional, Callable, Set
from typing_extensions import Protocol, TypedDict, runtime_checkable, NotRequired
from result import Result, Ok, Err
from .expr import LazyExpr, EnvDict, LazyExprDict
from app.template.data_source.model import DataSource, unmarshal_data_source
from functools import lru_cache
from typeguard import check_type, typechecked
from jsonpath_ng import parse, jsonpath
from jsonpath_ng.exceptions import JsonPathParserError

FormatterFn = Callable[[Any], str]
LazyFormatter = LazyExpr[FormatterFn] | LazyExpr
LazyFormatterLike = str | LazyFormatter | None

ValidatorFn = Callable[[Any], bool]
LazyValidator = LazyExpr[ValidatorFn] | LazyExpr
LazyValidatorLike = str | LazyValidator | None

PreprocessorFn = Callable[[Any], Any]
LazyPreprocessor = LazyExpr[PreprocessorFn] | LazyExpr
LazyPreprocessorLike = str | LazyPreprocessor | None

TypeLike = Optional[type]

LazyExprLike = str | LazyExprDict | LazyExpr
ImportsLike = List[str]

__global_imports__: ImportsLike = []


def set_global_imports(imports: ImportsLike):
    global __global_imports__
    __global_imports__ = imports


def global_imports() -> ImportsLike:
    global __global_imports__
    return __global_imports__


def common_parse_expr(expr: LazyExprLike,
                      imports: Optional[ImportsLike] = None) -> LazyExpr:
    if isinstance(expr, dict):
        imports_ = expr.get("imports", [])
        return LazyExpr(raw=expr["raw"],
                        imports=[*imports, *imports_] if imports else imports_)
    elif isinstance(expr, str):
        return LazyExpr(raw=expr, imports=imports)
    elif isinstance(expr, LazyExpr):
        if imports is not None:
            imports_ = expr.imports if expr.imports else []
            return LazyExpr(raw=expr.raw, imports=[*imports, *imports_])
        return expr
    else:
        raise ValueError(f"Invalid type {type(expr)}")


def common_parse_type(t: str | type | None,
                      imports: Optional[ImportsLike] = None) -> TypeLike:
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


def _common_format_impl(val: Any,
                       formatter: Optional[LazyFormatter | FormatterFn],
                       env: EnvDict = None) -> str:
    if formatter is not None:
        if isinstance(formatter, LazyExpr):
            return formatter(val, env=env)
        elif isinstance(formatter, Callable):
            return formatter(val)
        else:
            raise ValueError(f"Invalid formatter type {type(formatter)}")
    return str(val)


def _common_preprocess_impl(val: Any,
                           preprocessor: Optional[LazyPreprocessor |
                                                  PreprocessorFn],
                           env: EnvDict = None) -> Any:
    if preprocessor is not None:
        if isinstance(preprocessor, LazyExpr):
            return preprocessor(val, env=env)
        elif isinstance(preprocessor, Callable):
            return preprocessor(val)
        else:
            raise ValueError(f"Invalid preprocessor type {type(preprocessor)}")


def _common_verify_impl(val: Any,
                       verifier: Optional[LazyValidator | ValidatorFn],
                       t: TypeLike = None,
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

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        ...

    def eval_formatter(self, env: EnvDict = None) -> Optional[FormatterFn]:
        """
        maybe throw an error if the formatter is not callable
        """
        ...

    def format(self, env: EnvDict = None) -> str:
        ...

    def verify(self, env: EnvDict = None) -> bool:
        return True


def _common_eval_formatter(formatter: Optional[LazyFormatter],
                           env: EnvDict = None) -> Optional[FormatterFn]:
    if formatter is not None:
        if isinstance(formatter, LazyExpr):
            f = formatter.eval(env)
            if isinstance(f, Callable):
                return f
            else:
                raise ValueError(f"Invalid formatter type {type(f)} ({f})")
        elif isinstance(formatter, Callable):
            return formatter
        else:
            raise ValueError(f"Invalid formatter type {type(formatter)}")
    return None


class LiteralVariable(BaseModel):
    """
    LiteralVariable represents a variable that has a expression that can be evaluated to a value
    """
    name: str
    # for some reason, LazyExpr and LazyExpr[Any] are not compatible
    # they are not the same type
    expr: LazyExpr
    comment: Optional[str] = None
    formatter: Optional[LazyFormatter] = None
    t: TypeLike = None

    class Config:
        frozen = True

    def __init__(self,
                 name: str,
                 expr: LazyExprLike,
                 comment: Optional[str] = None,
                 formatter: LazyFormatterLike = None,
                 t: TypeLike = None,
                 imports: Optional[ImportsLike] = None,
                 **data):
        super().__init__(name=name,
                         expr=expr,
                         comment=comment,
                         formatter=formatter,
                         t=t,
                         _inst_imports=imports,
                         **data)

    @model_validator(mode="before")  # type: ignore
    def _preprocess_expressions(cls, values):  # pylint: disable=no-self-argument
        inst_imports = values.get("_inst_imports") or []
        imports = [*inst_imports, *global_imports()]
        expr_ = values.get("expr")
        values["expr"] = common_parse_expr(expr_, imports)
        formatter_ = values.get("formatter")
        values["formatter"] = nullable_common_parse_expr(formatter_, imports)
        t_ = values.get("t")
        values["t"] = common_parse_type(t_, imports)
        return values

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

    def eval_formatter(self, env: EnvDict = None) -> Optional[FormatterFn]:
        return _common_eval_formatter(self.formatter, env)

    def format(self, env: EnvDict = None) -> str:
        return _common_format_impl(self.load(env).unwrap(), self.formatter, env)

    def verify(self, env: EnvDict = None) -> bool:
        val = self.load(env)
        return _common_verify_impl(val.unwrap(), None, self.t, env)


class PathVariable(BaseModel):
    """
    PathVariable represents a variable that can be extracted from a dictionary-like structure
    """
    name: str
    source: Dict[str, Any]
    json_path: str
    comment: Optional[str] = None
    formatter: Optional[LazyFormatter] = None
    verifier: Optional[LazyValidator] = None
    preprocessor: Optional[LazyPreprocessor] = None
    # the type that after preprocessing
    t: TypeLike = None

    class Config:
        frozen = True

    def __init__(self,
                 name: str,
                 source: Dict[str, Any],
                 json_path: str,
                 comment: Optional[str] = None,
                 formatter: LazyFormatterLike = None,
                 verifier: LazyValidatorLike = None,
                 preprocessor: LazyPreprocessorLike = None,
                 t: TypeLike = None,
                 imports: Optional[ImportsLike] = None,
                 **data):
        super().__init__(name=name,
                         source=source,
                         json_path=json_path,
                         comment=comment,
                         formatter=formatter,
                         verifier=verifier,
                         preprocessor=preprocessor,
                         t=t,
                         _inst_imports=imports,
                         **data)

    @model_validator(mode="before")  # type: ignore
    def _preprocess_expressions(cls, values):  # pylint: disable=no-self-argument
        inst_imports = values.get("_inst_imports") or []
        imports = [*inst_imports, *global_imports()]
        expr_ = values.get("expr")
        values["expr"] = common_parse_expr(expr_, imports)
        formatter_ = values.get("formatter")
        values["formatter"] = nullable_common_parse_expr(formatter_, imports)
        t_ = values.get("t")
        preprocessor_ = values.get("preprocessor")
        values["preprocessor"] = nullable_common_parse_expr(
            preprocessor_, imports)
        verifier_ = values.get("verifier")
        values["verifier"] = nullable_common_parse_expr(verifier_, imports)
        values["t"] = common_parse_type(t_, imports)
        return values

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
        except JsonPathParserError as e:
            return Err(e)

    def preprocess(self, item: Any, env: EnvDict = None) -> Any:
        if self.preprocessor is not None:
            return _common_preprocess_impl(item, self.preprocessor, env)
        return item

    def eval_formatter(self, env: EnvDict = None) -> Optional[FormatterFn]:
        return _common_eval_formatter(self.formatter, env)

    def format(self, env: EnvDict = None) -> str:
        return _common_format_impl(self.load(env).unwrap(), self.formatter, env)

    def verify(self, env: EnvDict = None) -> bool:
        val = self.load(env)
        return _common_verify_impl(self.preprocess(val.unwrap()), self.verifier,
                                  self.t, env)
