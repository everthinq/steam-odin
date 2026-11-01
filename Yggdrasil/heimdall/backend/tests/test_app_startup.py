"""app.py starts the background loops once, and only when run as ``python app.py``.

Importing ``app`` builds every service against the real data, so this reads the
source instead: every ``.start…()`` call of a background loop must sit inside
``start_background()``, and only the ``__main__`` block may call that.
"""
import ast
import os

SOURCE = os.path.join(os.path.dirname(__file__), '..', 'app.py')


def _tree():
    with open(SOURCE, encoding='utf-8') as handle:
        return ast.parse(handle.read())


def _calls(node):
    """Names of the calls made when *node* runs (a lambda's body runs later, so it is skipped)."""
    if isinstance(node, ast.Lambda):
        return
    if isinstance(node, ast.Call):
        function = node.func
        yield function.attr if isinstance(function, ast.Attribute) else getattr(function, 'id', '')
    for child in ast.iter_child_nodes(node):
        yield from _calls(child)


def test_loops_start_only_inside_start_background():
    tree = _tree()
    module_level = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.If))]
    started = [name for node in module_level for name in _calls(node) if name.startswith('start')]
    assert started == [], f'loops started on import: {started}'
    starter = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'start_background')
    assert 'start' in list(_calls(starter)) and 'start_keeper' in list(_calls(starter))


def test_only_the_main_block_starts_them_with_no_reloader():
    tree = _tree()
    main = [node for node in tree.body if isinstance(node, ast.If) and 'main' in ast.unparse(node.test)]
    assert len(main) == 1
    body = ast.unparse(main[0])
    assert 'start_background()' in body and 'use_reloader=False' in body and 'debug=False' in body
    assert 'signal.SIGTERM' in body
    others = [node for node in tree.body if node is not main[0]]
    assert all('start_background()' not in ast.unparse(node) for node in others
               if not (isinstance(node, ast.FunctionDef) and node.name == 'start_background'))
