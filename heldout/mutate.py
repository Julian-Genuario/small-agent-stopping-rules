"""Source-level single-edit mutation operators.

Every edit is a byte-span replacement in the original file, so a seeded bug is a
one-token diff. The same operator table is used in both directions: to seed a bug
and, in the search baseline, to propose repairs. By construction the repair space
always contains the exact inverse of the seeded edit.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict

ALTERNATIVES = {
    "<": ["<=", ">"], "<=": ["<", ">="], ">": [">=", "<"], ">=": [">", "<="],
    "==": ["!="], "!=": ["=="], "is": ["is not"], "is not": ["is"],
    "in": ["not in"], "not in": ["in"], "+": ["-"], "-": ["+"],
    "and": ["or"], "or": ["and"],
}
CMP_TEXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
            ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}
_CORE = re.compile(r"^[\s()\\]*(.*?)[\s()\\]*$", re.S)
_OPRE = re.compile(r"is\s+not|not\s+in|is|in|<=|>=|<|>|==|!=|\+|-|and|or")


@dataclass
class Edit:
    start: int          # byte offset in the file
    end: int
    old: str
    new: str
    op: str             # operator family
    lineno: int
    col: int | None = None   # byte column inside the line (independent of line endings)

    def locate(self, src: bytes):
        """Byte span of the edit in `src`. Offsets were recorded on the file as checked
        out; if the line endings differ (CRLF vs LF), fall back to line + column."""
        # line + column first: a short token such as "in" can also sit, by chance, at
        # the same byte offset of a file with different line endings
        if self.col is not None:
            s = _line_starts(src)[self.lineno - 1] + self.col
            n = len(self.old.encode())
            if src[s: s + n] == self.old.encode():
                return s, s + n
        if src[self.start: self.end] == self.old.encode():
            return self.start, self.end
        raise ValueError(f"edit does not match the file at line {self.lineno}: {self.old!r}")

    def apply(self, src: bytes) -> bytes:
        s, e = self.locate(src)
        return src[:s] + self.new.encode() + src[e:]

    def to_dict(self):
        return asdict(self)


def _line_starts(src: bytes):
    starts = [0]
    for i, b in enumerate(src):
        if b == 10:
            starts.append(i + 1)
    return starts


class _Locator:
    def __init__(self, src: bytes):
        self.src = src
        self.starts = _line_starts(src)

    def off(self, lineno, col):
        return self.starts[lineno - 1] + col

    def between(self, a: ast.AST, b: ast.AST):
        """Byte span strictly between the end of node a and the start of node b."""
        return self.off(a.end_lineno, a.end_col_offset), self.off(b.lineno, b.col_offset)


def _op_in_gap(loc: _Locator, s: int, e: int, expected: str):
    seg = loc.src[s:e].decode("utf-8", "replace")
    if "#" in seg:
        return None
    m = _CORE.match(seg)
    core = m.group(1)
    if not _OPRE.fullmatch(core):
        return None
    if re.sub(r"\s+", " ", core) != expected:
        return None
    i = seg.index(core)
    bs = s + len(seg[:i].encode())
    return bs, bs + len(core.encode())


def functions(tree: ast.AST, max_lines: int):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n = node.end_lineno - node.lineno + 1
            if n <= max_lines:
                out.append(node)
    return out


def _docstring_lines(fn):
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        return set(range(body[0].lineno, body[0].end_lineno + 1))
    return set()


def edits_in_function(src: bytes, fn: ast.AST, include_not_insert: bool = False, lines: set[int] | None = None):
    """All single-token edits inside a function body (decorators and signature excluded)."""
    loc = _Locator(src)
    body_first = fn.body[0].lineno
    skip = _docstring_lines(fn)
    edits: list[Edit] = []

    def ok_line(n):
        return n >= body_first and n not in skip and (lines is None or n in lines)

    for node in ast.walk(fn):
        if node is not fn and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            pass  # nested defs are still walked; their lines belong to fn's span anyway
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                left = node.left if i == 0 else node.comparators[i - 1]
                right = node.comparators[i]
                txt = CMP_TEXT[type(op)]
                span = _op_in_gap(loc, *loc.between(left, right), txt)
                if span and ok_line(left.end_lineno):
                    for alt in ALTERNATIVES[txt]:
                        edits.append(Edit(span[0], span[1], txt, alt, "cmp", left.end_lineno))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            txt = "+" if isinstance(node.op, ast.Add) else "-"
            span = _op_in_gap(loc, *loc.between(node.left, node.right), txt)
            if span and ok_line(node.left.end_lineno):
                edits.append(Edit(span[0], span[1], txt, ALTERNATIVES[txt][0], "arith", node.left.end_lineno))
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            txt = "and" if isinstance(node.op, ast.And) else "or"
            span = _op_in_gap(loc, *loc.between(node.values[0], node.values[1]), txt)
            if span and ok_line(node.values[0].end_lineno):
                edits.append(Edit(span[0], span[1], txt, ALTERNATIVES[txt][0], "bool", node.values[0].end_lineno))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            s = loc.off(node.lineno, node.col_offset)
            e = loc.off(node.operand.lineno, node.operand.col_offset)
            seg = loc.src[s:e].decode()
            if re.fullmatch(r"not\s+", seg) and ok_line(node.lineno):
                edits.append(Edit(s, e, seg, "", "not_del", node.lineno))
        elif isinstance(node, ast.Constant) and type(node.value) is bool:
            s, e = loc.off(node.lineno, node.col_offset), loc.off(node.end_lineno, node.end_col_offset)
            if ok_line(node.lineno) and loc.src[s:e].decode() in ("True", "False"):
                edits.append(Edit(s, e, str(node.value), str(not node.value), "bool_const", node.lineno))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            s, e = loc.off(node.lineno, node.col_offset), loc.off(node.end_lineno, node.end_col_offset)
            raw = loc.src[s:e].decode()
            if ok_line(node.lineno) and re.fullmatch(r"\d+", raw) and node.value < 1000:
                edits.append(Edit(s, e, raw, str(node.value + 1), "const", node.lineno))
                if node.value > 0:
                    edits.append(Edit(s, e, raw, str(node.value - 1), "const", node.lineno))
        if include_not_insert and isinstance(node, (ast.If, ast.While, ast.IfExp)):
            t = node.test
            if not (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)) and ok_line(t.lineno) \
                    and t.lineno == t.end_lineno:
                s, e = loc.off(t.lineno, t.col_offset), loc.off(t.end_lineno, t.end_col_offset)
                inner = loc.src[s:e].decode()
                new = f"not ({inner})" if isinstance(t, (ast.BoolOp, ast.Compare, ast.IfExp, ast.NamedExpr)) else f"not {inner}"
                edits.append(Edit(s, e, inner, new, "not_ins", t.lineno))
    # de-duplicate and keep only edits that parse and actually change the AST
    seen, good = set(), []
    base = ast.dump(ast.parse(src))
    for ed in edits:
        key = (ed.start, ed.end, ed.new)
        if key in seen:
            continue
        seen.add(key)
        try:
            if ast.dump(ast.parse(ed.apply(src))) == base:
                continue
        except SyntaxError:
            continue
        good.append(ed)
    good.sort(key=lambda d: (d.start, d.new))
    return good


def same_ast(a: bytes | str, b: bytes | str) -> bool:
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return False
