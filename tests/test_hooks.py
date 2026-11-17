"""Regression tests for DeepSeek Credit Monitor settings hooks.

Run inside the Agent Zero framework runtime, e.g.:
    /opt/venv-a0/bin/python -m pytest tests/test_hooks.py
The framework root (default /a0) must exist so hooks.py can import its
framework-side `state` module. Stored secrets are stubbed with fake values;
no real API key is read or printed.
"""

import importlib.util
import os
import sys

FRAMEWORK_ROOT = os.environ.get("A0_ROOT", "/a0")
if FRAMEWORK_ROOT and os.path.isdir(FRAMEWORK_ROOT) and FRAMEWORK_ROOT not in sys.path:
    sys.path.insert(0, FRAMEWORK_ROOT)

HOOKS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks.py"))

_spec = importlib.util.spec_from_file_location("dcm_hooks_under_test", HOOKS_PATH)
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)

STORED_KEY = "sk-test-stored"
NEW_KEY = "sk-test-new"


def _save(settings, stored):
    hooks._load_stored_settings = lambda **kwargs: dict(stored)
    return hooks.save_plugin_config(settings)


def test_mask_preserves_stored_key():
    result = _save({"deepseek_api_key": hooks.MASK}, {"deepseek_api_key": STORED_KEY})
    assert result["deepseek_api_key"] == STORED_KEY


def test_empty_field_clears_key():
    result = _save({"deepseek_api_key": ""}, {"deepseek_api_key": STORED_KEY})
    assert result["deepseek_api_key"] == ""


def test_absent_field_clears_key():
    result = _save({}, {"deepseek_api_key": STORED_KEY})
    assert result["deepseek_api_key"] == ""


def test_new_value_replaces_key():
    result = _save({"deepseek_api_key": NEW_KEY}, {"deepseek_api_key": STORED_KEY})
    assert result["deepseek_api_key"] == NEW_KEY


def test_mask_without_stored_key_stays_empty():
    result = _save({"deepseek_api_key": hooks.MASK}, {})
    assert result["deepseek_api_key"] == ""


def test_normalize_maps_mask_to_empty():
    assert hooks._normalize({"deepseek_api_key": hooks.MASK})["deepseek_api_key"] == ""


def test_get_config_masks_key_for_ui_only():
    hooks._load_stored_settings = lambda **kwargs: {}
    ui = hooks.get_plugin_config(
        default={"deepseek_api_key": NEW_KEY}, hook_context={"caller": "ui"}
    )
    assert ui["deepseek_api_key"] == hooks.MASK
    api = hooks.get_plugin_config(
        default={"deepseek_api_key": NEW_KEY}, hook_context={"caller": "api"}
    )
    assert api["deepseek_api_key"] == NEW_KEY
