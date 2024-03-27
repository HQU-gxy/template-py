from typing import Dict, Iterable, Literal, List, Tuple, Optional
from pydantic import BaseModel, validator
from result import Result, Ok, Err
from typeguard import check_type
from app.template.dependency.extractor import parse as parse_expr, ParseResult
from app.template.variable.expr import EnvDict, LazyExpr
from app.template.variable.model import ImportsLike

SUPPORTED_TAGS = Literal["h1", "h2", "h3", "p", "em", "strong", "blockquote",
                         "b", "i", "u"]

SUPPORTED_PLOTS = Literal["line", "bar", "pie", "scatter", "histogram"]

NumberArray = List[int | float]

ColumnLike = Dict[str, NumberArray | str]


class HtmlParseResult(BaseModel):
    tag: SUPPORTED_TAGS
    text_with_hash: str
    exprs: Dict[str, LazyExpr]
    style: Dict[str, str] = {}

    class Config:
        frozen = True

    def load(self, env: EnvDict) -> str:

        def replace_exprs(text: str) -> str:
            # mut_text will be mutated by each replacement
            mut_text = text
            for name, expr in self.exprs.items():
                val = expr.eval(env)
                mut_text = mut_text.replace(f"${{{name}}}", str(val))
            return mut_text

        return replace_exprs(self.text_with_hash)


class ColumnParseResult(BaseModel):
    literal_cols: Dict[str, NumberArray]
    lazy_cols: Dict[str, LazyExpr | LazyExpr[NumberArray]]

    class Config:
        frozen = True

    def load(self, env: EnvDict) -> Dict[str, NumberArray]:

        def load_lazy(lazy: LazyExpr | LazyExpr[NumberArray]) -> NumberArray:
            val = list(lazy.eval(env))
            check_type(val, NumberArray)
            return val

        evaled = {k: load_lazy(v) for k, v in self.lazy_cols.items()}
        return {**self.literal_cols, **evaled}


ColumnLikeType = Tuple[Literal["plot"],
                       SUPPORTED_PLOTS] | Tuple[Literal["table"],
                                                Literal["table"]]

ColumnLikeParseResult = Tuple[ColumnLikeType, ColumnParseResult]


class HtmlContent(BaseModel):
    tag: SUPPORTED_TAGS
    content: str
    style: Dict[str, str] = {}

    class Config:
        frozen = True

    def extract(
            self, imports: Optional[ImportsLike]
    ) -> Result[HtmlParseResult, Exception]:
        r = parse_expr(self.content)

        def establish_expr(parse_result: ParseResult) -> HtmlParseResult:

            def ex(pair: Tuple[str, str]):
                name, expr = pair
                return name, LazyExpr(expr, imports)

            exprs = map(ex, ParseResult.table)
            return HtmlParseResult(tag=self.tag,
                                   text_with_hash=parse_result.text,
                                   exprs=dict(exprs),
                                   style=self.style)

        return r.map(establish_expr)


def common_extract_for_column(
        t: ColumnLikeType, items: Iterable[Tuple[str, str | NumberArray]],
        imports: Optional[ImportsLike]
) -> Result[ColumnLikeParseResult, Exception]:
    to_be_expr = {k: v for k, v in items if isinstance(v, str)}
    to_be_literal = {k: v for k, v in items if not isinstance(v, str)}

    def try_ex_parse(
            to_be: Dict[str, str]) -> Dict[str, Result[LazyExpr, Exception]]:

        def ex_parse(pair: Tuple[str, str]):
            name, expr = pair
            r = parse_expr(expr)
            if r.is_err():
                e = r.unwrap_err()
                return name, Err(e)
            table = r.unwrap().table
            if len(table) != 1:
                return name, Err(
                    ValueError(
                        f"Invalid interpolation for {name} `{expr}`. Expected exactly one expression, got {len(table)}"
                    ))
            raw = table[0][1]
            return name, Ok(LazyExpr(raw, imports))

        return dict(map(ex_parse, to_be.items()))

    exprs = try_ex_parse(to_be_expr)
    if any(e.is_err() for e in exprs.values()):
        return Err(
            ValueError("Failed to parse some expressions", {
                k: v.unwrap_err() for k, v in exprs.items()
            }))

    filtered = {k: v.unwrap() for k, v in exprs.items() if v.is_ok()}
    return Ok(
        (t, ColumnParseResult(literal_cols=to_be_literal, lazy_cols=filtered)))


class PlotContent(BaseModel):
    plot_type: SUPPORTED_PLOTS
    # if it's str, we interoperate it
    data: ColumnLike

    class Config:
        frozen = True

    def extract(
        self, imports: Optional[ImportsLike]
    ) -> Result[ColumnLikeParseResult, Exception]:
        t: ColumnLikeType = ("plot", self.plot_type)
        return common_extract_for_column(t, self.data.items(), imports)


class TableContent(BaseModel):
    table_type: Literal["table"]
    data: ColumnLike

    class Config:
        frozen = True

    def extract(self) -> Result[ColumnLikeParseResult, Exception]:
        t: ColumnLikeType = ("table", "table")
        return common_extract_for_column(t, self.data.items(), None)
