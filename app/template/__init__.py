from pydantic import BaseModel, validator, root_validator
from typing import List, Dict, Any, TypedDict
from result import Ok, Err, Result

from typeguard import check_type, typechecked

from app.template.content import IContent, HtmlContent, PlotContent, TableContent, unmarshal_content
from app.template.data_source.model import IDataSource, unmarshal_data_source
from app.template.variable.model import ImportsLike, IVariable, unmarshal_variable
from app.template.dependency.resolver import EvaluatedVariable, resolve_and_evaluate


class Template(TypedDict):
    """
    as reminder, won't actually be used
    """
    data_sources: List[IDataSource]
    variables: List[IVariable]
    content: List[IContent]
    imports: ImportsLike


class TemplateReturn(TypedDict):
    variables: List[Dict[str, Any]]
    content: List[Dict[str, Any]]


async def unmarshal_template(data: Dict[str, Any]) -> TemplateReturn:
    imports: ImportsLike = data.get("imports", [])
    check_type(imports, ImportsLike)
    data_sources_ = data.get("data_sources", [])
    data_sources = [unmarshal_data_source(ds) for ds in data_sources_]
    variables_ = data.get("variables", [])
    check_type(variables_, List[Dict[str, Any]])
    if len(variables_) == 0:
        raise ValueError("no variables provided")
    loaded = {}
    variables = [
        await unmarshal_variable(v, data_sources, loaded, imports)
        for v in variables_
    ]
    contents_ = data.get("content", [])
    contents = [unmarshal_content(c) for c in contents_]

    evaluated_vars_ = resolve_and_evaluate(variables)
    evaluated_vars: List[EvaluatedVariable] | None = None
    match evaluated_vars_:
        case Ok(v):
            evaluated_vars = v
        case Err(e):
            raise e
    evaluated_contents = [
        c.eval_result(evaluated_vars, imports) for c in contents
    ]
    return {
        "variables": [{
            var.name: var.value
        } for var in evaluated_vars],
        "content": evaluated_contents,
    }
