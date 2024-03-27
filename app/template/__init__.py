from pydantic import BaseModel, validator, root_validator
from typing import List, Dict, Any

from app.template.content import IContent, HtmlContent, PlotContent, TableContent, unmarshal_content
from app.template.data_source.model import IDataSource, unmarshal_data_source
from app.template.variable.model import ImportsLike, IVariable, unmarshal_variable


class Template(BaseModel):
    """
    as reminder, won't actually be used
    """
    data_sources: List[IDataSource]
    variables: List[IVariable]
    content: List[IContent]
    imports: ImportsLike


def unmarshal_template(data: Dict[str, Any]):
    pass
