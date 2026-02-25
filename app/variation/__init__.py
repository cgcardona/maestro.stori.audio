"""
Muse / Variations subsystem for Stori Maestro.

This package implements the canonical Variation lifecycle:
propose → stream → review → commit/discard.

Variations are "git diffs for music" — structured, reviewable,
auditionable, and partially accept/rejectable change proposals.
"""
from __future__ import annotations
