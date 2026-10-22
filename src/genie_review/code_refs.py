"""Static extraction of data-object references from SQL and PySpark source.

Everything here is deterministic. Each reference records the line it came from so findings can
point at the right place in the PR.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

READ_CALLS = {"table", "forName"}
WRITE_CALLS = {"saveAsTable", "insertInto", "toTable"}
DF_METHODS = {
    "join", "select", "orderBy", "sort", "limit", "write", "filter", "where", "groupBy", "agg",
    "withColumn", "withColumnRenamed", "drop", "alias", "distinct", "union", "unionByName", "cache",
}


@dataclass
class CodeRefs:
    reads: dict[str, int] = field(default_factory=dict)       # fqn -> line
    writes: dict[str, int] = field(default_factory=dict)      # fqn -> line
    columns: dict[str, dict[str, int]] = field(default_factory=dict)  # fqn -> {column: line}
    joins: list[dict] = field(default_factory=list)           # {left: (fqn, col), right: (fqn, col), line}
    outputs: dict[str, list[str]] = field(default_factory=dict)  # written fqn -> output column names (SQL CTAS/INSERT SELECT)
    unresolved: list[dict] = field(default_factory=list)

    def add_col(self, fqn: str, col: str, line: int) -> None:
        self.columns.setdefault(fqn, {}).setdefault(col, line)


def _line_of(text: str, *needles: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if all(n.lower() in line.lower() for n in needles):
            return i
    return 1


# ------------------------------------------------------------------ SQL

IDENT_RE = re.compile(r"IDENTIFIER\(([^()]*)\)", re.I)
PARAM_RE = re.compile(r"(\$\{(\w+)\}|\{\{\s*(\w+)\s*\}\}|:(\w+)\b)")


def _substitute_sql_params(text: str, params: dict[str, str]) -> str:
    def ident(m: re.Match) -> str:
        # IDENTIFIER(:catalog || '.' || :schema || '.orders') -> main.sales.orders
        out = []
        for part in m.group(1).split("||"):
            part = part.strip()
            if part.startswith(":"):
                out.append(params.get(part[1:], "__" + part[1:]))
            elif len(part) >= 2 and part[0] == part[-1] == "'":
                out.append(part[1:-1])
            else:
                return m.group(0)
        return "".join(out)
    text = IDENT_RE.sub(ident, text)

    def repl(m: re.Match) -> str:
        key = m.group(2) or m.group(3) or m.group(4)
        return params.get(key, m.group(0))
    return PARAM_RE.sub(repl, text)


def _fqn(t: exp.Table) -> str:
    return ".".join(p.name for p in t.parts)


def qualify(name: str, params: dict[str, str]) -> str | None:
    """Fully qualify a table name with a pipeline's default catalog/schema. None if not resolvable."""
    parts = name.split(".")
    if len(parts) == 3:
        return name
    cat, sch = params.get("__default_catalog"), params.get("__default_schema")
    if len(parts) == 2 and cat:
        return f"{cat}.{name}"
    if len(parts) == 1 and cat and sch:
        return f"{cat}.{sch}.{name}"
    return None


def sql_refs(text: str, params: dict[str, str]) -> CodeRefs:
    refs = CodeRefs()
    resolved = _substitute_sql_params(text, params)
    try:
        statements = sqlglot.parse(resolved, read="databricks")
    except sqlglot.errors.ParseError as e:
        refs.unresolved.append({"line": 1, "partial": f"SQL parse error: {str(e)[:120]}"})
        return refs
    for stmt in statements:
        if stmt is None:
            continue
        target = None
        if isinstance(stmt, (exp.Create, exp.Insert, exp.Merge)):
            t = stmt.this if isinstance(stmt.this, exp.Table) else stmt.this.find(exp.Table)
            if t is not None:
                target = qualify(_fqn(t), params)
                if target:
                    refs.writes[target] = _line_of(text, t.name)
                    q = stmt.expression if isinstance(stmt, exp.Create) else stmt.args.get("expression")
                    if isinstance(q, exp.Query) and "*" not in q.named_selects:
                        refs.outputs[target] = [c for c in q.named_selects if c]
        alias: dict[str, str] = {}
        for t in stmt.find_all(exp.Table):
            fqn = qualify(_fqn(t), params)
            if fqn is None or fqn == target:
                continue
            refs.reads.setdefault(fqn, _line_of(text, t.name))
            alias[t.alias_or_name] = fqn
        for c in stmt.find_all(exp.Column):
            if c.table and c.table in alias:
                refs.add_col(alias[c.table], c.name, _line_of(text, f"{c.table}.{c.name}"))
        for j in stmt.find_all(exp.Join):
            on = j.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(exp.EQ):
                l, r = eq.left, eq.right
                if isinstance(l, exp.Column) and isinstance(r, exp.Column) and l.table in alias and r.table in alias:
                    refs.joins.append({
                        "left": [alias[l.table], l.name], "right": [alias[r.table], r.name],
                        "line": _line_of(text, f"{l.table}.{l.name}", f"{r.table}.{r.name}"),
                    })
    return refs


# ------------------------------------------------------------------ Python / PySpark


class _Resolver:
    """Resolves table-name expressions (str literals, f-strings) using task parameters."""

    def __init__(self, tree: ast.AST, params: dict[str, str]):
        self.params = params
        self.aliases: dict[str, str] = {}  # local var -> param key  (cat = args.catalog)
        self.consts: dict[str, str] = {}   # local var -> literal
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                name, v = n.targets[0].id, n.value
                if isinstance(v, ast.Attribute) and v.attr in params:
                    self.aliases[name] = v.attr
                elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                    self.consts[name] = v.value

    def value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.aliases:
                return self.params[self.aliases[node.id]]
            if node.id in self.params:
                return self.params[node.id]
            return self.consts.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr in self.params:
            return self.params[node.attr]
        return None

    def render(self, node: ast.AST) -> tuple[str, bool]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, True
        if isinstance(node, ast.JoinedStr):
            out = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    out.append(str(part.value))
                else:
                    v = self.value(part.value)
                    if v is None:
                        return ast.unparse(node), False
                    out.append(v)
            return "".join(out), True
        if isinstance(node, ast.Name):
            v = self.value(node)
            return (v, True) if v else (node.id, False)
        return ast.unparse(node), False


def python_refs(text: str, params: dict[str, str]) -> CodeRefs:
    refs = CodeRefs()
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        refs.unresolved.append({"line": e.lineno or 1, "partial": "Python syntax error"})
        return refs
    res = _Resolver(tree, params)
    var_table: dict[str, str] = {}

    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args and n.func.attr in READ_CALLS | WRITE_CALLS:
            name, ok = res.render(n.args[0])
            if not ok:
                refs.unresolved.append({"line": n.lineno, "partial": name})
                continue
            q = qualify(name, params)
            if q is None:
                continue
            name = q
            (refs.writes if n.func.attr in WRITE_CALLS else refs.reads).setdefault(name, n.lineno)
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in READ_CALLS and v.args:
                name, ok = res.render(v.args[0])
                q = qualify(name, params) if ok else None
                if q:
                    var_table[n.targets[0].id] = q

    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in var_table:
            if n.attr not in DF_METHODS:
                refs.add_col(var_table[n.value.id], n.attr, n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join":
            for arg in n.args[1:2] + [k.value for k in n.keywords if k.arg == "on"]:
                for cmp in [x for x in ast.walk(arg) if isinstance(x, ast.Compare)]:
                    l, r = cmp.left, cmp.comparators[0] if cmp.comparators else None
                    if (isinstance(cmp.ops[0], ast.Eq) and isinstance(l, ast.Attribute) and isinstance(r, ast.Attribute)
                            and isinstance(l.value, ast.Name) and isinstance(r.value, ast.Name)
                            and l.value.id in var_table and r.value.id in var_table):
                        refs.joins.append({"left": [var_table[l.value.id], l.attr],
                                           "right": [var_table[r.value.id], r.attr], "line": cmp.lineno})
    return refs


def select_output_columns(text: str) -> list[str]:
    """Output column names of DataFrame.select(...) calls in a module (names and aliases)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "select":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append(a.value)
                elif (isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "alias"
                      and a.args and isinstance(a.args[0], ast.Constant)):
                    out.append(a.args[0].value)
    return out


def file_refs(path: Path, params: dict[str, str]) -> CodeRefs:
    if not path.exists():
        return CodeRefs()
    text = path.read_text(errors="replace")
    if path.suffix == ".sql":
        return sql_refs(text, params)
    if path.suffix == ".py":
        return python_refs(text, params)
    return CodeRefs()


# ------------------------------------------------------------------ import graph


def _imports(path: Path, roots: list[Path]) -> list[Path]:
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, FileNotFoundError):
        return []
    # (search roots, dotted module) pairs; relative imports search from the file's own package
    wanted: list[tuple[list[Path], str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level:
                base = path.parent
                for _ in range(n.level - 1):
                    base = base.parent
                wanted.append(([base], n.module or ""))
                wanted += [([base], a.name) for a in n.names if not n.module]
            elif n.module:
                wanted.append((roots, n.module))
        elif isinstance(n, ast.Import):
            wanted += [(roots, a.name) for a in n.names]
    found = []
    for search, mod in wanted:
        if not mod:
            continue
        rel = mod.replace(".", "/")
        for root in search:
            for cand in (root / f"{rel}.py", root / rel / "__init__.py"):
                if cand.exists():
                    found.append(cand)
    return found


def executed_files(repo: Path, entry: str) -> set[str]:
    """Repo-relative files an entry point executes: itself plus transitive local imports."""
    entry_path = repo / entry
    roots = [entry_path.parent, repo / "src", repo]
    seen: set[Path] = set()
    stack = [entry_path]
    while stack:
        f = stack.pop()
        if f in seen or not f.exists():
            continue
        seen.add(f)
        if f.suffix == ".py":
            stack += _imports(f, roots)
    return {str(p.relative_to(repo)) for p in seen if p.is_relative_to(repo)}
