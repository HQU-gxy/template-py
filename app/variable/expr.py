from typing import Dict, Any, Optional, Callable
import warnings
import ast
from .visitor import UnboundVariableFinder, ImportValidator

EnvDict = Optional[Dict[str, Any]]

class LazyExpr:
    MAGIC_FN_NAME = "__lazy_expr"
    _raw: str
    _ast: ast.Module
    _imports: list[str]
    _finder: UnboundVariableFinder

    def __init__(self, raw: str, imports: Optional[list[str]] = None):
        self._raw = raw
        self._imports = imports if imports else []
        preload = "\n".join(self._imports)
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

        func = ast.FunctionDef(name=self.MAGIC_FN_NAME,
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
        self._finder.visit(self._ast)
    

    @property
    def unbound(self):
        """
        Returns the set of unbound variables in the function
        """
        return self._finder.unbound

    @property
    def imports(self):
        """
        Returns the set of imported variables in the function
        """
        return self._finder.imports

    @property
    def target(self):
        """
        If the expression is a named expression (defined with walrus operator `:=`), returns the name of the variable
        """
        return self._finder.target
    
    @property
    def raw(self):
        """
        Returns the raw expression
        """
        return self._raw

    def eval(self, env: Optional[Dict[str, Any]] = None):
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
        return _env[self.MAGIC_FN_NAME]()

    # https://peps.python.org/pep-3102/
    def __call__(self, *args, env: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Evaluates the LazyExpr as a function

        params:
            env: a dictionary of variables to be injected into the function
            args: positional arguments to be passed to the function
            kwargs: keyword arguments to be passed to the function
        """
        fn = self.eval(env)
        if not isinstance(fn, Callable):
            raise TypeError("Not a callable. Actual type {} ({})".format(
                type(fn), fn))
        return fn(*args, **kwargs)
