"""Tests for the console page: what FastAPI serves at /, and rules the page's own code must follow."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIC = Path(__file__).resolve().parent.parent / "static"
FIELDS = {"input", "select", "textarea"}


class _Page(HTMLParser):
    """Collects the attributes the tests check."""

    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.field_ids: list[str | None] = []
        self.labelled_ids: set[str] = set()
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for name in ("src", "href"):
            if attributes.get(name):
                self.references.append(attributes[name] or "")
        if tag in FIELDS and attributes.get("type") not in {"hidden", "submit", "button"}:
            self.field_ids.append(attributes.get("id"))
        if tag == "label" and attributes.get("for"):
            self.labelled_ids.add(attributes["for"] or "")
        if tag == "meta" and attributes.get("name"):
            self.meta[attributes["name"] or ""] = attributes.get("content") or ""


@pytest.fixture(scope="module")
def page() -> tuple[str, dict[str, str]]:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text, dict(response.headers)


def _parse(html: str) -> _Page:
    parser = _Page()
    parser.feed(html)
    return parser


def test_the_console_is_served_at_the_root(page: tuple[str, dict[str, str]]) -> None:
    html, _ = page

    assert "<title>LLM Gateway console</title>" in html


def test_the_page_may_only_load_from_its_own_origin(page: tuple[str, dict[str, str]]) -> None:
    _, headers = page
    policy = headers["content-security-policy"]

    for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'", "connect-src 'self'"):
        assert directive in policy
    assert "unsafe-inline" not in policy
    assert "http" not in policy


def test_the_page_references_only_local_assets(page: tuple[str, dict[str, str]]) -> None:
    html, _ = page

    for reference in _parse(html).references:
        assert reference.startswith("/"), reference
        assert not reference.startswith("//"), reference


@pytest.mark.parametrize("path", ["/static/console.css", "/static/console.js"])
def test_the_page_assets_are_served(path: str) -> None:
    assert TestClient(app).get(path).status_code == 200


def test_no_page_file_points_at_another_origin() -> None:
    for asset in STATIC.iterdir():
        assert not re.search(r"https?://", asset.read_text()), asset.name


def test_the_scripts_never_build_html_from_strings() -> None:
    for script in STATIC.glob("*.js"):
        source = script.read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in source, f"{script.name} uses {sink}"


def test_every_form_field_has_a_label(page: tuple[str, dict[str, str]]) -> None:
    parsed = _parse(page[0])

    assert parsed.field_ids, "the page should have form fields"
    for field_id in parsed.field_ids:
        assert field_id is not None and field_id in parsed.labelled_ids, field_id


def test_the_page_is_responsive_and_supports_both_color_schemes(page: tuple[str, dict[str, str]]) -> None:
    meta = _parse(page[0]).meta

    assert meta["viewport"] == "width=device-width, initial-scale=1"
    assert meta["color-scheme"] == "light dark"
    assert "prefers-color-scheme: dark" in (STATIC / "console.css").read_text()


@pytest.mark.parametrize("headers", [{}, {"X-API-KEY": "free-tier-key"}])
def test_the_v1_protected_endpoint_is_retired(headers: dict[str, str]) -> None:
    # The v1 dashboard was its only user; the console calls the gateway itself.
    assert TestClient(app).get("/protected", headers=headers).status_code == 404
