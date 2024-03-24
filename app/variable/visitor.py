import ast
from typing import Optional, Final


class UnboundVariableFinder(ast.NodeVisitor):
    MAGIC_FN_NAME: Final[str] = "__lazy_expr"
    _imports: set[str]
    _assigned: set[str]
    _unbound: set[str]
    _target: Optional[str] = None

    def __init__(self):
        self._imports = set()
        self._assigned = set()
        self._unbound = set()

    def visit_Assign(self, node):
        raise TypeError("Assignment is not allowed")

    def visit_ClassDef(self, node):
        raise TypeError("Class definition is not allowed")

    def visit_FunctionDef(self, node):
        if node.name != self.MAGIC_FN_NAME:
            raise TypeError("Function definition is not allowed")
        else:
            self.generic_visit(node)

    def visit_NamedExpr(self, node):
        if isinstance(node.target, ast.Name):
            self._target = node.target.id
        self.generic_visit(node)

    def visit_Lambda(self, node):
        for arg in node.args.args:
            self._assigned.add(arg.arg)
        for kwarg in node.args.kwonlyargs:
            self._assigned.add(kwarg.arg)
        if node.args.vararg:
            self._assigned.add(node.args.vararg.arg)
        if node.args.kwarg:
            self._assigned.add(node.args.kwarg.arg)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id not in self._assigned:
            self._unbound.add(node.id)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        for gen in node.generators:
            for target in gen.target.elts if isinstance(
                    gen.target, ast.Tuple) else [gen.target]:
                if isinstance(target, ast.Name):
                    self._assigned.add(target.id)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self._imports.add(alias.asname if alias.asname else alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self._imports.add(alias.asname if alias.asname else alias.name)
        self.generic_visit(node)

    @property
    def imports(self):
        return self._imports

    @property
    def builtin(self):
        return set(dir(__builtins__))

    @property
    def target(self):
        return self._target

    @property
    def unbound(self):
        return self._unbound - self._imports - self.builtin


class ImportValidator(ast.NodeVisitor):

    def visit_Import(self, node):
        pass

    def visit_ImportFrom(self, node):
        pass

    def generic_visit(self, node):
        if not isinstance(node, (ast.Module, ast.alias)):
            dump = ast.dump(node, indent=2)
            raise ValueError(f"Invalid import statement {dump}")
        super().generic_visit(node)
