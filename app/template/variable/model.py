from enum import Enum, auto
from pydantic import BaseModel, Field, PrivateAttr, model_validator, validator, root_validator
from typing import Dict, Any, List, Optional, Callable, Sequence, Set, TypeVar, cast
from typing_extensions import Protocol, TypedDict, runtime_checkable, NotRequired
from result import Result, Ok, Err
from .expr import LazyExpr, EnvDict, LazyExprDict
from app.template.data_source.model import IDataSource, unmarshal_data_source
from functools import lru_cache
from typeguard import TypeCheckError, check_type, typechecked
from jsonpath_ng import parse, jsonpath
from jsonpath_ng.exceptions import JsonPathParserError

T = TypeVar("T")
U = TypeVar("U")

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


def _common_parse_expr(expr: LazyExprLike,
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
        raise ValueError(f"Invalid type {type(expr)} when parsing expression")


def _common_parse_type(t: str | type | None,
                       imports: Optional[ImportsLike] = None) -> TypeLike:
    if t is None:
        return None
    if isinstance(t, type):
        return t
    expr = LazyExpr(raw=t, imports=imports)
    t = expr.eval()
    if not isinstance(t, type):
        raise ValueError(
            f"Expected type must be a type. Get {t} ({type(t)}) when parsing type"
        )
    return t


def _common_parse_expr_nullable(
        expr: Optional[LazyExprLike],
        imports: Optional[ImportsLike] = None) -> LazyExpr | None:
    if expr is None:
        return None
    return _common_parse_expr(expr, imports)


def _common_preprocess_impl(val: T,
                            preprocessor: LazyExpr[Callable[[T], U]]
                            | Callable[[T], U] | None,
                            env: EnvDict = None) -> Result[U, Exception]:
    if preprocessor is not None:
        if isinstance(preprocessor, LazyExpr):
            try:
                v = cast(U, preprocessor(val, env=env))
                return Ok(v)
            except Exception as e:
                return Err(e)
        elif isinstance(preprocessor, Callable):
            try:
                return Ok(preprocessor(val))
            except Exception as e:
                return Err(e)
        else:
            return Err(
                ValueError(
                    f"Invalid preprocessor type {type(preprocessor)} when preprocessing value {val}"
                ))
    # T == U in this case since there is no preprocessor
    v = cast(U, val)
    return Ok(v)


def _common_verify_impl(val: T,
                        verifier: Optional[LazyValidator | ValidatorFn],
                        t: TypeLike = None,
                        env: EnvDict = None) -> Result[T, Exception]:
    """
    Verify the value with the verifier and the expected type
    
    Args:
    - val: the value to be verified
    - verifier: the verifier to be used
    - t: the expected type
    - env: the environment to be used for the verifier
    
    Returns:
    - Result[T, Exception]: the value itself if the verification is successful,
    otherwise an error
    """

    def validate_with_verifier(val: Any) -> bool:
        if verifier:
            if isinstance(verifier, LazyExpr):
                return verifier(val, env=env)
            elif isinstance(verifier, Callable):
                return verifier(val)
        return True

    def validate_with_expected_type(val: T) -> Result[T, Exception]:
        if t:
            try:
                check_type(val, t)
            except TypeCheckError as e:
                return Err(e)
        return Ok(val)

    if not validate_with_verifier(val):
        return Err(ValueError("failed to validate with verifier"))
    return validate_with_expected_type(val)


@runtime_checkable
class IVariable(Protocol):

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
        values["expr"] = _common_parse_expr(expr_, imports)
        formatter_ = values.get("formatter")
        values["formatter"] = _common_parse_expr_nullable(formatter_, imports)
        t_ = values.get("t")
        values["t"] = _common_parse_type(t_, imports)
        return values

    def _load_unchecked(self, env: EnvDict = None) -> Result[Any, Exception]:
        try:
            val = self.expr.eval(env)
            return Ok(val)
        except Exception as e:
            return Err(e)

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        val = self._load_unchecked(env).and_then(
            lambda x: _common_verify_impl(x, None, self.t, env))
        match val:
            case Ok(_):
                return Ok(val.unwrap())
            case Err(e):
                return Err(
                    ValueError(f"failed to verify value: {self.name}", e))

    @property
    def unbound(self) -> set[str]:
        s: Set[str] = set()
        if isinstance(self.expr, LazyExpr):
            s |= self.expr.unbound
        return s

    def eval_formatter(self, env: EnvDict = None) -> Optional[FormatterFn]:
        return _common_eval_formatter(self.formatter, env)


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
        formatter_ = values.get("formatter")
        values["formatter"] = _common_parse_expr_nullable(formatter_, imports)
        t_ = values.get("t")
        preprocessor_ = values.get("preprocessor")
        values["preprocessor"] = _common_parse_expr_nullable(
            preprocessor_, imports)
        verifier_ = values.get("verifier")
        values["verifier"] = _common_parse_expr_nullable(verifier_, imports)
        values["t"] = _common_parse_type(t_, imports)
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

    def _load_unchecked(self) -> Result[Any, Exception]:
        data = self.source
        try:
            json_path_expr = parse(self.json_path)
            match = json_path_expr.find(data)
            if not match:
                return Err(ValueError("No match found"))
            val = match[0].value
            return Ok(val)
        except JsonPathParserError as e:
            return Err(e)

    def load(self, env: EnvDict = None) -> Result[Any, Exception]:
        val = self._load_unchecked().and_then(
            lambda x: _common_preprocess_impl(x, self.preprocessor, env)
        ).and_then(
            lambda x: _common_verify_impl(x, self.verifier, self.t, env))
        match val:
            case Ok(v):
                return Ok(v)
            case Err(e):
                return Err(
                    ValueError(f"failed to verify value: {self.name}", e))

    def preprocess(self, item: Any, env: EnvDict = None) -> Any:
        if self.preprocessor is not None:
            return _common_preprocess_impl(item, self.preprocessor, env)
        return item

    def eval_formatter(self, env: EnvDict = None) -> Optional[FormatterFn]:
        return _common_eval_formatter(self.formatter, env)


async def unmarshal_variable(
        variable: Dict[str, Any],
        data_sources: Optional[Sequence[IDataSource]] = None,
        loaded_sources: Optional[Dict[str, Dict[str, Any]]] = None,
        imports: Optional[ImportsLike] = None) -> IVariable:
    """
    Unmarshal a list of variables from a dictionary

    @warning: if `loaded_sources` is provided, this function will use the data
    from the loaded sources prior to loading the data from the data sources and
    mutate the `loaded_sources` dictionary by storing the loaded data
    """
    if "expr" in variable:
        return LiteralVariable(**variable, imports=imports)
    elif "source" in variable:
        # source would be a string that refers to the data source
        if data_sources is None:
            raise ValueError(
                "Data sources must be provided to unmarshal PathVariable")

        async def try_load(source_name: str) -> Dict[str, Any]:
            if loaded_sources is not None and source_name in loaded_sources:
                return loaded_sources[source_name]
            try:
                source: IDataSource = next(
                    filter(lambda x: x.name == source_name, data_sources))
            except StopIteration:
                raise ValueError(f"Data source {source_name} not found")
            data = await source.load_async()
            if data.is_err():
                raise RuntimeError(f"Failed to load data source {source_name}",
                                   data.unwrap_err())
            if loaded_sources is not None:
                loaded_sources[source_name] = data.unwrap()
            return data.unwrap()

        source_key = variable["source"]
        check_type(source_key, str)
        source = await try_load(variable["source"])
        # exclude the source key
        variable.pop("source")
        return PathVariable(source=source, **variable, imports=imports)
    else:
        raise ValueError("Invalid variable type")
