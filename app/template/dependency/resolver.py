from dataclasses import dataclass
from typing import List, Dict, Any, Sequence, Optional
import networkx as nx
from result import Result, Ok, Err
from app.template.variable.model import FormatterFn, IVariable


@dataclass
class EvaluatedVariable:
    name: str
    value: Any
    formatter: Optional[FormatterFn]


def to_env_dict(variables: Sequence[EvaluatedVariable]) -> Dict[str, Any]:
    return {var.name: var.value for var in variables}


class DependencyResolver:
    _table: list[IVariable]
    _graph: nx.DiGraph

    def __init__(self) -> None:
        self._table = []
        self._graph = nx.DiGraph()
        self._dirty = True

    def add(self, variable: IVariable) -> Result[None, Exception]:
        valid_names = map(lambda x: x.name, self._table)
        if variable.name in valid_names:
            return Err(
                ValueError(f"duplicated variable name: {variable.name}"))
        self._table.append(variable)
        self._graph.add_node(variable.name)
        for unbound in variable.unbound:
            self._graph.add_edge(variable.name, unbound)
        return Ok(None)

    def add_many(self,
                 variables: Sequence[IVariable]) -> Result[None, Exception]:
        for var in variables:
            res = self.add(var)
            if res.is_err():
                return res
        return Ok(None)

    @staticmethod
    def _sort_by_topological(variables: List[IVariable],
                             topological: List[str]):
        index_map = {name: i for i, name in enumerate(reversed(topological))}
        # Sort the list based on the mapping
        sorted_lst = sorted(variables, key=lambda var: index_map[var.name])
        return sorted_lst

    def resolve(self) -> Result[None, Exception]:
        valid_names = map(lambda x: x.name, self._table)
        invalid_names = list(
            filter(lambda x: x not in valid_names, self._graph.nodes))
        if len(invalid_names) > 0:
            return Err(ValueError(f"unbound variables: {invalid_names}"))
        try:
            topological = nx.topological_sort(self._graph)
            self._table = DependencyResolver._sort_by_topological(
                self._table, list(topological))
        except nx.NetworkXUnfeasible as e:
            # circular dependency
            return Err(e)
        return Ok(None)

    def eval(self) -> List[EvaluatedVariable]:
        err = self.resolve()
        if err.is_err():
            raise RuntimeError("failed to resolve dependencies",
                               err.unwrap_err())

        env: Dict[str, Any] = {}

        def eval_var(var: IVariable) -> EvaluatedVariable:
            """
            @warning: cause side effect to `env`
            """
            value = var.load(env=env)
            if value.is_err():
                raise RuntimeError(f"failed to evaluate variable `{var.name}`",
                                   value.unwrap_err())
            val = value.unwrap()
            env[var.name] = val
            formatter = var.eval_formatter(env=env)
            return EvaluatedVariable(name=var.name,
                                     value=val,
                                     formatter=formatter)

        return [eval_var(var) for var in self._table]


def resolve_and_evaluate(
    variables: Sequence[IVariable]
) -> Result[List[EvaluatedVariable], Exception]:
    """
    Resolve the environment from the given variables
    """
    resolver = DependencyResolver()
    res = resolver.add_many(variables)
    if res.is_err():
        return Err(res.unwrap_err())
    try:
        lst = resolver.eval()
        return Ok(lst)
    except Exception as e:
        return Err(e)
