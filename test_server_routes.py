"""
Every name a route reads has to exist by the time it reads it.

WHY THIS EXISTS. /api/equity shipped calling re.fullmatch with no `import re` and 500ed on the first
click. test_dashboard.py then grew a check for that one route's imports. On 2026-10-14 the same class
of bug shipped again one route along: /api/paper/capital read `query`, which do_GET defines and
do_POST does not, so every request to it died with a NameError and no response at all -- and the
import check passed, because the missing name was not an import.

A route is only exercised when somebody uses it, and these handlers are long if-chains that the
suite never calls end to end. So this reads them instead: for each handler, every name it LOADS must
be a parameter, something assigned somewhere in that handler, a module global, or a builtin. It is
not type checking and it proves nothing about behaviour -- it catches exactly the mistake that keeps
happening, which is a name that is simply not there.

    python test_server_routes.py
"""

import ast
import builtins
import os
import unittest

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "server.py")
# The handlers worth reading: one long if-chain each, every branch reachable only by a real request.
HANDLERS = ("do_GET", "do_POST", "_build_state")


def _module() -> ast.Module:
    with open(SERVER, encoding="utf-8") as f:
        return ast.parse(f.read())


def _globals(tree: ast.Module) -> set:
    """Everything module level defines: imports, assignments, functions, classes."""
    out = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in ([node.target] if not isinstance(node, ast.Assign) else node.targets):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    out.add(sub.id)
    return out


def _bound(fn) -> set:
    """Every name the function itself introduces, wherever in it that happens.

    Deliberately flow-insensitive: a name assigned on line 900 counts as bound on line 100. That
    makes this blind to use-before-assignment, and still catches a name that is never assigned at
    all -- which is the bug that keeps shipping."""
    out = set()
    args = fn.args
    for a in (args.posonlyargs + args.args + args.kwonlyargs):
        out.add(a.arg)
    for a in (args.vararg, args.kwarg):
        if a:
            out.add(a.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if node is not fn:
                for a in getattr(node, "args", ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                                              vararg=None, kwarg=None, defaults=[],
                                                              kw_defaults=[])).args:
                    out.add(a.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.comprehension):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    out.add(n.id)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            out.add(n.id)
        elif isinstance(node, ast.Lambda):
            for a in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
                out.add(a.arg)
    return out


def _handlers(tree: ast.Module) -> dict:
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in HANDLERS}


class TestEveryNameARouteReadsExists(unittest.TestCase):
    def setUp(self):
        self.tree = _module()
        self.globals = _globals(self.tree)
        self.handlers = _handlers(self.tree)

    def test_the_handlers_are_all_still_there(self):
        """If one gets renamed this file quietly checks nothing, which is worse than failing."""
        self.assertEqual(sorted(self.handlers), sorted(HANDLERS))

    def test_no_handler_reads_a_name_that_does_not_exist(self):
        """THE BUG THIS CATCHES: /api/paper/capital read `query`, which only do_GET has. Every
        request to it died with a NameError before it could answer anything."""
        missing = []
        for name, fn in sorted(self.handlers.items()):
            known = self.globals | _bound(fn)
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                        and node.id not in known:
                    missing.append(f"{name}: line {node.lineno} reads `{node.id}`")
        self.assertEqual(missing, [], "names read but never defined: " + "; ".join(missing))

    def test_the_check_actually_fires(self):
        """A test that can only pass is not a test. Take one name do_POST really does define away
        from it, and the same check that the real handlers pass must fail."""
        fn = self.handlers["do_POST"]
        known = (self.globals | _bound(fn)) - {"parsed"}
        reads = [n.id for n in ast.walk(fn)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known]
        self.assertIn("parsed", reads, "do_POST no longer reads `parsed`, so this proves nothing")


class TestThePaperCapitalRouteIsCheap(unittest.TestCase):
    """It is a button on a row, not a report. Rebuilding the whole 2MB state inside the handler took
    long enough that the first two real requests to it never came back."""

    def _route(self) -> str:
        with open(SERVER, encoding="utf-8") as f:
            code = f.read()
        start = code.index('if parsed.path == "/api/paper/capital":')
        return code[start:code.index('if parsed.path == "/api/credentials":', start)]

    def test_it_goes_through_the_cache_rather_than_rebuilding(self):
        body = self._route()
        self.assertIn("state_cache.get(", body)

    def test_it_uses_the_same_cache_key_the_page_does(self):
        """A key of its own would mean its own rebuild on every click, cache or no cache."""
        with open(SERVER, encoding="utf-8") as f:
            code = f.read()
        self.assertIn('cache_key = (mode, json.dumps(advice, sort_keys=True) if advice else "")', code)
        self.assertIn('("paper", "")', self._route())


if __name__ == "__main__":
    unittest.main()
