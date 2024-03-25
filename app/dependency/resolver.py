from typing import List, Dict, Any, Sequence
import networkx as nx
from result import Result, Ok, Err
from app.variable.model import Variable


class DependencyResolver:
    _table: list[Variable]
    _graph: nx.DiGraph
    _env: Dict[str, Any]
    _dirty: bool = False

    def __init__(self) -> None:
        self._table = []
        self._graph = nx.DiGraph()
        self._dirty = True

    def add(self, variable: Variable) -> Result[None, Exception]:
        valid_names = map(lambda x: x.name, self._table)
        if variable.name in valid_names:
            return Err(ValueError(f"duplicated variable name: {variable.name}"))
        self._table.append(variable)
        self._graph.add_node(variable.name)
        for unbound in variable.unbound:
            self._graph.add_edge(variable.name, unbound)
        self._dirty = True
        return Ok(None)

    def add_many(self,
                 variables: Sequence[Variable]) -> Result[None, Exception]:
        for var in variables:
            res = self.add(var)
            if res.is_err():
                return res
        return Ok(None)

    @staticmethod
    def sort_by_topological(variables: List[Variable], topological: List[str]):
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
            self._table = DependencyResolver.sort_by_topological(
                self._table, list(topological))
        except nx.NetworkXUnfeasible as e:
            # circular dependency
            return Err(e)
        return Ok(None)

    @property
    def env(self) -> Result[Dict[str, Any], Exception]:
        if self._dirty:
            err = self.resolve()
            if err.is_err():
                return Err(err.unwrap_err())
            env: Dict[str, Any] = {}
            for var in self._table:
                try:
                    env[var.name] = var.value(env)
                except Exception as e:
                    obj = {"var": var, "exception": e}
                    return Err(
                        RuntimeError(
                            "failed to evaluate variable `{}`".format(var.name),
                            obj))
            self._env = env
            self._dirty = False
        return Ok(self._env)


def resolve_env(
        variables: Sequence[Variable]) -> Result[Dict[str, Any], Exception]:
    """
    Resolve the environment from the given variables
    """
    resolver = DependencyResolver()
    res = resolver.add_many(variables)
    if res.is_err():
        return Err(res.unwrap_err())
    return resolver.env
