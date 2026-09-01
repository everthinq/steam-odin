"""Crash-safety + recovery guarantees of jsonio (Critical fix #1/#7)."""
import json
import os

import pytest

import jsonio


def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / 'x.json'
    jsonio.atomic_write_json(str(p), {'a': 1, 'b': [1, 2, 3]})
    assert json.loads(p.read_text()) == {'a': 1, 'b': [1, 2, 3]}


def test_atomic_write_leaves_no_temp_litter(tmp_path):
    p = tmp_path / 'x.json'
    jsonio.atomic_write_json(str(p), {'a': 1})
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith('.tmp-')]
    assert leftovers == []


def test_overwrite_replaces_content(tmp_path):
    p = tmp_path / 'x.json'
    jsonio.atomic_write_json(str(p), {'v': 1})
    jsonio.atomic_write_json(str(p), {'v': 2})
    assert json.loads(p.read_text()) == {'v': 2}


def test_failed_serialize_keeps_original_intact(tmp_path):
    """A write that can't be serialized must not touch the existing good file."""
    p = tmp_path / 'x.json'
    jsonio.atomic_write_json(str(p), {'good': True})
    with pytest.raises(TypeError):
        jsonio.atomic_write_json(str(p), {'bad': {1, 2, 3}})  # set is not JSON
    assert json.loads(p.read_text()) == {'good': True}
    assert [f for f in os.listdir(tmp_path) if f.startswith('.tmp-')] == []


def test_read_missing_returns_default(tmp_path):
    got = jsonio.read_json(str(tmp_path / 'nope.json'),
                           default=lambda: {'portfolios': {}})
    assert got == {'portfolios': {}}


def test_read_corrupt_uses_recover_hook(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{ not valid json ')
    got = jsonio.read_json(str(p), default={'x': 0},
                           recover=lambda: {'recovered': True})
    assert got == {'recovered': True}


def test_read_corrupt_without_recover_returns_default(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('}}}')
    got = jsonio.read_json(str(p), default={'fallback': 1})
    assert got == {'fallback': 1}
