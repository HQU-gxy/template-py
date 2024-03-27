from pydantic import BaseModel
from result import Result, Ok, Err
import xxhash
from typing import Tuple, List


class ParseStepResult(BaseModel):
    text: str
    offset: int
    hash: str
    expr: str


class ParseResult(BaseModel):
    text: str
    table: List[Tuple[str, str]]


def _parse_step(text: str,
                start_offset: int = 0) -> Result[ParseStepResult, Exception]:
    """
    Parse a text and return the next unparsed text and the parsed expression
    """
    if start_offset >= len(text):
        return Err(IndexError("Start offset out of range"))
    sub = text[start_offset:]
    for i, char in enumerate(sub):
        if char == "$":
            if i + 1 < len(sub) and sub[i + 1] == "{":
                start = i + 2
                end = start
                nested_cnt = 0
                while end < len(sub):
                    if sub[end] == "{":
                        nested_cnt += 1
                    if sub[end] == "}":
                        if nested_cnt == 0:
                            break
                        else:
                            nested_cnt -= 1
                            end += 1
                    else:
                        end += 1
                if end == len(sub):
                    return Err(
                        ValueError(
                            "Unmatched braces from {}".format(start_offset +
                                                              start)))
                expr = sub[start:end]
                s_offset = start_offset + start - 2
                hsh = xxhash.xxh32()
                # also hash the offset to avoid same hash for same expression
                hsh.update(s_offset.to_bytes(4, byteorder="big"))
                hsh.update(expr.encode("utf-8"))
                digest = hsh.hexdigest()[:8]
                e_offset = start_offset + end + 1
                before_expr = text[:s_offset]
                after_expr = text[e_offset:]
                new_text = f"{before_expr}{digest}{after_expr}"
                next_offset = s_offset + len(digest)
                r = ParseStepResult(text=new_text,
                                    offset=next_offset,
                                    hash=digest,
                                    expr=expr)
                return Ok(r)
    return Err(EOFError("No more expressions"))


def parse(text: str) -> Result[ParseResult, Exception]:
    table: List[tuple[str, str]] = []
    mutate_text = text
    offset = 0
    while True:
        res_ = _parse_step(mutate_text, offset)
        match res_:
            case Err(e):
                if isinstance(e, EOFError):
                    break
                else:
                    return Err(e)
            case Ok(res):
                table.append((res.hash, res.expr))
                mutate_text = res.text
                offset = res.offset
    r = ParseResult(text=mutate_text, table=table)
    return Ok(r)
