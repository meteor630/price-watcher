"""Токены дизайна: полнота, согласованность тем, контраст WCAG AA."""

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parents[2] / "src" / "pricewatch" / "web" / "static" / "css" / "tokens.css"

# SITE_DESIGN_SPEC.md, раздел 2: фон, поверхность, текст, вторичный текст, рамка, акцент, рост, снижение.
REQUIRED = {
    "--color-bg",
    "--color-surface",
    "--color-surface-2",
    "--color-fg",
    "--color-muted",
    "--color-line",
    "--color-accent",
    "--color-accent-ink",
    "--color-up",
    "--color-down",
    "--color-neutral",
    "--font-display",
    "--font-body",
    "--font-mono",
    "--tap-min",
    "--duration-fast",
    "--duration-panel",
    "--duration-segment",
}
TEXT_COLORS = ["--color-fg", "--color-muted", "--color-accent", "--color-down", "--color-up", "--color-neutral"]
BACKGROUNDS = ["--color-bg", "--color-surface", "--color-surface-2"]
STORES = [f"--store-{n}" for n in range(1, 9)]
AA_TEXT = 4.5
AA_GRAPHICS = 3.0


def parse_blocks(css: str) -> dict[str, dict[str, str]]:
    """Блоки вида {"@media (...) > селектор": {переменная: значение}}."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    blocks: dict[str, dict[str, str]] = {}
    stack: list[str] = []
    position = 0
    for match in re.finditer(r"[{}]", css):
        chunk = css[position : match.start()]
        position = match.end()
        if match.group() == "{":
            stack.append(" ".join(chunk.split()))
            continue
        declarations = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", chunk))
        if declarations:
            blocks[" > ".join(stack)] = declarations
        stack.pop()
    return blocks


BLOCKS = parse_blocks(TOKENS.read_text(encoding="utf-8"))
DARK = BLOCKS[":root"]
LIGHT_SYSTEM = BLOCKS['@media (prefers-color-scheme: light) > :root:not([data-theme="dark"])']
LIGHT_MANUAL = BLOCKS[':root[data-theme="light"]']
REDUCED = BLOCKS["@media (prefers-reduced-motion: reduce) > :root"]
LIGHT = DARK | LIGHT_SYSTEM


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted([luminance(first), luminance(second)], reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_required_tokens_present() -> None:
    assert REQUIRED <= DARK.keys()


def test_both_light_blocks_are_identical() -> None:
    assert LIGHT_SYSTEM == LIGHT_MANUAL


def test_light_theme_redefines_every_color() -> None:
    colors = {name for name in DARK if name.startswith(("--color-", "--store-", "--bg-glow-"))}

    assert colors == LIGHT_SYSTEM.keys()


def test_store_palette_is_unique_per_theme() -> None:
    for theme in (DARK, LIGHT):
        values = [theme[name] for name in STORES]
        assert len(set(values)) == len(values)


@pytest.mark.parametrize("theme_name", ["dark", "light"])
@pytest.mark.parametrize("text", TEXT_COLORS)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_text_contrast_meets_aa(theme_name: str, text: str, background: str) -> None:
    theme = DARK if theme_name == "dark" else LIGHT

    assert contrast(theme[text], theme[background]) >= AA_TEXT


@pytest.mark.parametrize("theme_name", ["dark", "light"])
@pytest.mark.parametrize("fill", ["--color-accent", "--color-accent-from", "--color-accent-to"])
def test_accent_ink_readable_on_accent_fills(theme_name: str, fill: str) -> None:
    theme = DARK if theme_name == "dark" else LIGHT

    assert contrast(theme["--color-accent-ink"], theme[fill]) >= AA_TEXT


@pytest.mark.parametrize("theme_name", ["dark", "light"])
@pytest.mark.parametrize("store", STORES)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_store_colors_visible_on_backgrounds(theme_name: str, store: str, background: str) -> None:
    theme = DARK if theme_name == "dark" else LIGHT

    assert contrast(theme[store], theme[background]) >= AA_GRAPHICS


def test_reduced_motion_removes_movement() -> None:
    durations = {name for name in DARK if name.startswith("--duration-")}

    assert durations <= REDUCED.keys()
    assert all(REDUCED[name] == "0ms" for name in durations)
    assert REDUCED["--lift-sm"] == REDUCED["--lift-md"] == "0px"
    assert REDUCED["--press-scale"] == "1"


def test_contrast_formula_matches_wcag_reference() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
