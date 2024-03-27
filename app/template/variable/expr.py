from typing import Dict, Any, Optional, Callable, TypeVar, TypedDict, Union, List, Generic, Final, NotRequired
from typeguard import check_type
import warnings
import ast
from pydantic import BaseModel, PrivateAttr, validator, root_validator
from .visitor import UnboundVariableFinder, ImportValidator

EnvDict = Optional[Dict[str, Any]]

T = TypeVar("T")

MAGIC_FN_NAME: Final[str] = UnboundVariableFinder.MAGIC_FN_NAME
RAW_MAGIC: Final[str] = "__raw__"


class LazyExprDict(TypedDict):
    raw: str
    imports: NotRequired[List[str]]


class LazyExpr(BaseModel, Generic[T]):
    """
    A lazy expression that can be evaluated later
    
    template parameters:
        T: the type of the expression after evaluation
    """
    raw: str
    imports: Optional[List[str]]
    _ast: ast.Module = PrivateAttr()
    _finder: UnboundVariableFinder = PrivateAttr()

    class Config:
        exclude = ["_ast", "_finder"]
        frozen = True

    def __init__(self, raw: str, imports: Optional[List[str]] = None, **data):
        super().__init__(raw=raw, imports=imports, **data)
        # handle additional imports here
        # might need some ugly hacks to handle it
        preload = "\n".join(imports or [])
        preload_ast = ast.parse(preload)

        import_validator = ImportValidator()
        import_validator.visit(preload_ast)

        raw_ast = ast.parse(raw)
        if not raw_ast.body:
            raise ValueError("Empty AST body")
        last = raw_ast.body[-1]
        if not isinstance(last, ast.Expr):
            raise ValueError("Last statement is not an expression")
        if len(raw_ast.body) > 1:
            warnings.warn(
                "Multiple expressions in the body. Only the last one will be evaluated"
            )

        func = ast.FunctionDef(name=MAGIC_FN_NAME,
                               args=ast.arguments(args=[],
                                                  vararg=None,
                                                  kwarg=None,
                                                  kwonlyargs=[],
                                                  kw_defaults=[],
                                                  posonlyargs=[],
                                                  defaults=[]),
                               body=[ast.Return(value=last.value)],
                               decorator_list=[])
        preload_ast.body.append(func)
        self._ast = preload_ast
        ast.fix_missing_locations(self._ast)
        self._finder = UnboundVariableFinder()
        self._finder.visit(preload_ast)

    @property
    def unbound(self):
        """
        Returns the set of unbound variables in the function
        """
        return self._finder.unbound

    @property
    def solely_dependency(self):
        """
        Returns whether the expression only depends on one and solely a variable
        """
        return self._finder.solely_dependency

    @property
    def target(self):
        """
        If the expression is a named expression (defined with walrus operator `:=`), returns the name of the variable
        """
        return self._finder.target

    def eval(self,
             env: Optional[Dict[str, Any]] = None,
             is_type_check: bool = True) -> T:
        """
        Evaluates the LazyExpr and returns the result

        params:
            env: a dictionary of variables to be injected into the function
        """
        compiled = compile(self._ast, "<string>", "exec")
        _env = {}
        # pylint: disable-next=exec-used
        exec(compiled, _env)
        if env:
            _env.update(env)
        val = _env[MAGIC_FN_NAME]()
        if is_type_check:
            check_type(val, T)
        return val

    # https://peps.python.org/pep-3102/
    def __call__(self, *args, env: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Evaluates the LazyExpr as a function

        params:
            env: a dictionary of variables to be injected into the function
            args: positional arguments to be passed to the function
            kwargs: keyword arguments to be passed to the function
        """
        fn = self.eval(env, is_type_check=False)
        if not isinstance(fn, Callable):
            raise TypeError("Not a callable. Actual type {} ({})".format(
                type(fn), fn))
        return fn(*args, **kwargs)
