"""Шаблоны сайта: экранирование, строгие переменные, метки статики, совместимость с CSP, шапка вошедшего."""

import re
from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError

from pricewatch.web.templates import STATIC_DIR, TEMPLATES_DIR, environment, render, static_url


def test_static_url_has_content_mark() -> None:
    url = static_url("css/base.css")

    assert re.fullmatch(r"/static/css/base\.css\?v=[0-9a-f]{12}", url)


@pytest.mark.parametrize("path", ["css/missing.css", "../templates.py", "../../pyproject.toml"])
def test_static_url_refuses_missing_and_outside_files(path: str) -> None:
    with pytest.raises(FileNotFoundError):
        static_url(path)


def test_vendor_files_have_licenses() -> None:
    assert (STATIC_DIR / "vendor" / "htmx-2.0.10.min.js").is_file()
    assert "Zero-Clause BSD" in (STATIC_DIR / "vendor" / "htmx.LICENSE.txt").read_text(encoding="utf-8")


def test_data_is_escaped() -> None:
    html = render("fragments/flash.html", code="NOT_FOUND", message="<img src=x onerror=alert(1)>", error_id=None)

    assert "<img" not in html and "&lt;img" in html


def test_unknown_variable_is_an_error() -> None:
    with pytest.raises(UndefinedError):
        environment.from_string("{{ missing }}").render()


def test_signed_in_header_has_name_logout_and_csrf() -> None:
    user = SimpleNamespace(user=SimpleNamespace(display_name="<b>Аня</b>"), csrf_token="ab" * 32)

    html = render("error.html", code="NOT_FOUND", status=404, message="Нет", error_id=None, session_user=user)

    assert 'hx-headers=\'{"X-CSRF-Token": "' + "ab" * 32 + '"}\'' in html
    assert "&lt;b&gt;Аня&lt;/b&gt;" in html
    assert 'hx-post="/web/logout"' in html and "data-logout" in html


def test_anonymous_header_has_no_logout() -> None:
    html = render("error.html", code="NOT_FOUND", status=404, message="Нет", error_id=None)

    assert "data-logout" not in html and "hx-headers" not in html


@pytest.mark.parametrize("template", sorted(path.relative_to(TEMPLATES_DIR).as_posix() for path in TEMPLATES_DIR.rglob("*.html")))
def test_templates_have_no_inline_code(template: str) -> None:
    source = (TEMPLATES_DIR / template).read_text(encoding="utf-8")

    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", source), "встроенный скрипт запрещён CSP"
    assert not re.search(r"\sstyle=", source), "атрибут style запрещён CSP"
    assert not re.search(r"\son[a-z]+=", source), "обработчики on…= запрещены CSP"
    assert "|safe" not in source.replace(" ", ""), "отключать экранирование нельзя"


def test_template_variable_may_be_called_name() -> None:
    assert 'value="Дом"' in render("fragments/rename_form.html", action="/web/x", name="Дом", version=1)
