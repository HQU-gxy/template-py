from dataclasses import dataclass
from result import Result, Ok, Err
import xxhash
from typing import Tuple, List


@dataclass
class ParseStepResult:
    text: str
    offset: int
    hash: str
    expr: str

@dataclass
class ParseResult:
    text: str
    table: List[Tuple[str, str]]


def parse_step(text: str,
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
                while end < len(sub) and sub[end] != "}":
                    end += 1
                if end == len(sub):
                    return Err(ValueError("Unmatched braces"))
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
                return Ok(ParseStepResult(new_text, next_offset, digest, expr))
    return Err(EOFError("No more expressions"))


def parse(text) -> Result[ParseResult, Exception]:
    table: List[tuple[str, str]] = []
    mutate_text = text
    offset = 0
    while True:
        res_ = parse_step(mutate_text, offset)
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
    return Ok(ParseResult(mutate_text, table))
