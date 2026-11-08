"""Acceptance tests for Phases 1-5 (docs/06-roadmap.md). They are executable now and SKIP until the
`jade` package exists; once it does they run for real and the skip turns into pass/fail. Each test
names the roadmap row it verifies so the suite is the acceptance checklist."""
import pytest

jade = pytest.importorskip("jade", reason="Phase 1 not implemented: `jade` package absent (BACKLOG W-02)")
