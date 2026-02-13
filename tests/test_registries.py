"""Tests for app.core.registries (GOAL_SYNONYMS, MACRO_REGISTRY)."""
import pytest

from app.core.registries import GOAL_SYNONYMS, MACRO_REGISTRY


def test_goal_synonyms_has_expected_keys():
    assert "darker" in GOAL_SYNONYMS
    assert "brighter" in GOAL_SYNONYMS
    assert "punchier" in GOAL_SYNONYMS
    assert "wider" in GOAL_SYNONYMS
    assert "more_energy" in GOAL_SYNONYMS


def test_goal_synonyms_values_are_lists():
    for key, val in GOAL_SYNONYMS.items():
        assert isinstance(val, list)
        assert len(val) >= 1
        assert all(isinstance(s, str) for s in val)


def test_macro_registry_darker_maps_to_mix_darker():
    assert MACRO_REGISTRY["darker"] == ["mix.darker"]


def test_macro_registry_values_are_lists():
    for key, val in MACRO_REGISTRY.items():
        assert isinstance(val, list)
        assert all("." in s for s in val)
