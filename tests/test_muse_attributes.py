"""Tests for maestro/services/muse_attributes.py.

Covers:
- parse_museattributes_file: valid rules, comments, blank lines, malformed lines,
  unknown strategies.
- resolve_strategy: exact track+dimension match, fnmatch wildcard patterns,
  fallback to MergeStrategy.AUTO when no rule matches.
- load_attributes: returns empty list when file not found; reads and parses
  when the file exists.
"""
from __future__ import annotations

import pathlib

import pytest

from maestro.services.muse_attributes import (
    MergeStrategy,
    MuseAttribute,
    load_attributes,
    parse_museattributes_file,
    resolve_strategy,
)


# ---------------------------------------------------------------------------
# parse_museattributes_file
# ---------------------------------------------------------------------------


class TestParseMuseattributesFile:
    def test_parses_basic_rule(self) -> None:
        content = "drums/*  *  ours\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 1
        assert rules[0].track_pattern == "drums/*"
        assert rules[0].dimension == "*"
        assert rules[0].strategy == MergeStrategy.OURS

    def test_parses_multiple_rules(self) -> None:
        content = (
            "drums/*  *        ours\n"
            "bass/*   harmonic  theirs\n"
            "*        *         auto\n"
        )
        rules = parse_museattributes_file(content)
        assert len(rules) == 3
        assert rules[0].strategy == MergeStrategy.OURS
        assert rules[1].strategy == MergeStrategy.THEIRS
        assert rules[2].strategy == MergeStrategy.AUTO

    def test_ignores_blank_lines(self) -> None:
        content = "\n\ndrum/*  *  ours\n\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 1

    def test_ignores_comment_lines(self) -> None:
        content = "# This is a comment\ndrum/*  *  ours\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 1

    def test_skips_malformed_lines(self) -> None:
        content = "bad-line-only-two-tokens  ours\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 0

    def test_skips_unknown_strategy(self) -> None:
        content = "drums/*  *  unknown_strategy\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 0

    def test_all_valid_strategies(self) -> None:
        content = (
            "a  *  ours\n"
            "b  *  theirs\n"
            "c  *  union\n"
            "d  *  auto\n"
            "e  *  manual\n"
        )
        rules = parse_museattributes_file(content)
        strategies = [r.strategy for r in rules]
        assert MergeStrategy.OURS in strategies
        assert MergeStrategy.THEIRS in strategies
        assert MergeStrategy.UNION in strategies
        assert MergeStrategy.AUTO in strategies
        assert MergeStrategy.MANUAL in strategies

    def test_strategy_case_insensitive(self) -> None:
        content = "drums/*  *  OURS\n"
        rules = parse_museattributes_file(content)
        assert len(rules) == 1
        assert rules[0].strategy == MergeStrategy.OURS

    def test_empty_content_returns_empty_list(self) -> None:
        assert parse_museattributes_file("") == []

    def test_only_comments_returns_empty_list(self) -> None:
        content = "# only comments\n# nothing else\n"
        assert parse_museattributes_file(content) == []


# ---------------------------------------------------------------------------
# resolve_strategy
# ---------------------------------------------------------------------------


class TestResolveStrategy:
    def _make_rule(
        self,
        track_pattern: str,
        dimension: str,
        strategy: MergeStrategy,
    ) -> MuseAttribute:
        return MuseAttribute(
            track_pattern=track_pattern,
            dimension=dimension,
            strategy=strategy,
        )

    def test_exact_match_returns_configured_strategy(self) -> None:
        attrs = [self._make_rule("drums/kick", "rhythmic", MergeStrategy.OURS)]
        result = resolve_strategy(attrs, "drums/kick", "rhythmic")
        assert result == MergeStrategy.OURS

    def test_fnmatch_wildcard_track_matches(self) -> None:
        attrs = [self._make_rule("drums/*", "*", MergeStrategy.OURS)]
        assert resolve_strategy(attrs, "drums/kick", "harmonic") == MergeStrategy.OURS
        assert resolve_strategy(attrs, "drums/snare", "melodic") == MergeStrategy.OURS

    def test_star_track_matches_any(self) -> None:
        attrs = [self._make_rule("*", "*", MergeStrategy.AUTO)]
        assert resolve_strategy(attrs, "bass/electric", "harmonic") == MergeStrategy.AUTO

    def test_first_match_wins(self) -> None:
        attrs = [
            self._make_rule("drums/*", "*", MergeStrategy.OURS),
            self._make_rule("*", "*", MergeStrategy.AUTO),
        ]
        # drums/hi-hat should match the first rule
        assert resolve_strategy(attrs, "drums/hi-hat", "rhythmic") == MergeStrategy.OURS
        # keys/piano should fall through to the second rule
        assert resolve_strategy(attrs, "keys/piano", "harmonic") == MergeStrategy.AUTO

    def test_no_match_returns_auto(self) -> None:
        attrs = [self._make_rule("drums/*", "rhythmic", MergeStrategy.OURS)]
        # Different track, no match
        result = resolve_strategy(attrs, "bass/electric", "harmonic")
        assert result == MergeStrategy.AUTO

    def test_empty_attributes_returns_auto(self) -> None:
        result = resolve_strategy([], "drums/kick", "rhythmic")
        assert result == MergeStrategy.AUTO

    def test_dimension_wildcard_matches_all_dimensions(self) -> None:
        attrs = [self._make_rule("bass/*", "*", MergeStrategy.THEIRS)]
        for dim in ("harmonic", "rhythmic", "melodic", "structural", "dynamic"):
            assert resolve_strategy(attrs, "bass/electric", dim) == MergeStrategy.THEIRS

    def test_specific_dimension_does_not_match_other(self) -> None:
        attrs = [self._make_rule("bass/*", "harmonic", MergeStrategy.THEIRS)]
        assert resolve_strategy(attrs, "bass/electric", "harmonic") == MergeStrategy.THEIRS
        assert resolve_strategy(attrs, "bass/electric", "rhythmic") == MergeStrategy.AUTO

    def test_theirs_strategy_resolved(self) -> None:
        attrs = [self._make_rule("keys/*", "harmonic", MergeStrategy.THEIRS)]
        assert resolve_strategy(attrs, "keys/piano", "harmonic") == MergeStrategy.THEIRS

    def test_union_strategy_resolved(self) -> None:
        attrs = [self._make_rule("*", "structural", MergeStrategy.UNION)]
        assert resolve_strategy(attrs, "any_track", "structural") == MergeStrategy.UNION

    def test_manual_strategy_resolved(self) -> None:
        attrs = [self._make_rule("*", "*", MergeStrategy.MANUAL)]
        assert resolve_strategy(attrs, "vocals/lead", "melodic") == MergeStrategy.MANUAL


# ---------------------------------------------------------------------------
# load_attributes
# ---------------------------------------------------------------------------


class TestLoadAttributes:
    def test_returns_empty_list_when_file_not_found(self, tmp_path: pathlib.Path) -> None:
        result = load_attributes(tmp_path)
        assert result == []

    def test_reads_and_parses_museattributes_file(self, tmp_path: pathlib.Path) -> None:
        attr_file = tmp_path / ".museattributes"
        attr_file.write_text("drums/*  *  ours\nbass/*  harmonic  theirs\n")
        result = load_attributes(tmp_path)
        assert len(result) == 2
        assert result[0].track_pattern == "drums/*"
        assert result[0].strategy == MergeStrategy.OURS
        assert result[1].strategy == MergeStrategy.THEIRS

    def test_returns_empty_list_for_empty_file(self, tmp_path: pathlib.Path) -> None:
        attr_file = tmp_path / ".museattributes"
        attr_file.write_text("")
        result = load_attributes(tmp_path)
        assert result == []

    def test_returns_empty_list_for_comments_only_file(self, tmp_path: pathlib.Path) -> None:
        attr_file = tmp_path / ".museattributes"
        attr_file.write_text("# only comments here\n")
        result = load_attributes(tmp_path)
        assert result == []
