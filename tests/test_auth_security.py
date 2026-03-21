"""Tests for auth redirect validation (Phase 1 security hardening)."""

import pytest

from app.auth import _is_safe_redirect


class TestIsSafeRedirect:
    """Validate the next-URL checker used on login."""

    def test_valid_internal_path(self):
        assert _is_safe_redirect("/dashboard") is True

    def test_valid_nested_path(self):
        assert _is_safe_redirect("/extensions/edit/1") is True

    def test_rejects_absolute_external_url(self):
        assert _is_safe_redirect("https://evil.example/steal") is False

    def test_rejects_scheme_relative_url(self):
        assert _is_safe_redirect("//evil.example/steal") is False

    def test_rejects_javascript_scheme(self):
        assert _is_safe_redirect("javascript:alert(1)") is False

    def test_rejects_empty_string(self):
        assert _is_safe_redirect("") is False

    def test_rejects_none(self):
        # Coerce None to empty via default; passed explicitly as guard.
        assert _is_safe_redirect(None) is False

    def test_rejects_relative_without_slash(self):
        assert _is_safe_redirect("dashboard") is False

    def test_rejects_backslash_trick(self):
        # Some browsers normalise \ to / — urlparse sees netloc.
        assert _is_safe_redirect("\\evil.example") is False

    def test_rejects_data_uri(self):
        assert _is_safe_redirect("data:text/html,<h1>hi</h1>") is False
