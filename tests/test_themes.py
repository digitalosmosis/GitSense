"""Checks that the theme definitions in the frontend, the stylesheet and the backend agree."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gitsense import config as config_mod
from gitsense.config import Config, clean_theme, coerce_setting

WEB = Path(config_mod.__file__).resolve().parent / "web"
THEMES_JS = (WEB / "js" / "themes.js").read_text(encoding="utf-8")
TOKENS_CSS = (WEB / "css" / "tokens.css").read_text(encoding="utf-8")


def js_token_keys() -> list[str]:
    block = THEMES_JS.split("export const THEME_TOKENS = [", 1)[1].split("];", 1)[0]
    return re.findall(r"\{ key: '([a-z0-9-]+)'", block)


def js_themes() -> dict[str, dict]:
    block = THEMES_JS.split("export const BUILTIN_THEMES = [", 1)[1].split("].map(", 1)[0]
    themes = {}
    for chunk in re.split(r"\n  \{\n", block)[1:]:
        theme_id = re.search(r"id: '([a-z0-9-]+)'", chunk).group(1)
        themes[theme_id] = {
            "name": re.search(r"name: '([^']+)'", chunk).group(1),
            "kind": re.search(r"kind: '(light|dark)'", chunk).group(1),
            "colors": dict(re.findall(r"'([a-z0-9-]+)': '(#[0-9A-Fa-f]{6})'", chunk)),
        }
    return themes


def css_block(selector_start: str) -> dict[str, str]:
    body = TOKENS_CSS.split(selector_start, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});", body))


def luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    n = int(hex_color[1:], 16)
    return 0.2126 * channel(n >> 16 & 255) + 0.7152 * channel(n >> 8 & 255) + 0.0722 * channel(n & 255)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


THEMES = js_themes()


def test_backend_and_frontend_list_the_same_tokens():
    assert tuple(js_token_keys()) == config_mod.THEME_TOKENS


def test_there_are_several_builtin_themes_of_both_kinds():
    assert {"daylight", "graphite"} <= set(THEMES)
    assert len(THEMES) >= 5
    kinds = [t["kind"] for t in THEMES.values()]
    assert kinds.count("light") >= 2 and kinds.count("dark") >= 2
    assert not any(theme_id.startswith("custom-") for theme_id in THEMES)


def test_backend_knows_the_builtin_theme_ids():
    assert set(THEMES) == config_mod.BUILTIN_THEME_IDS


def test_frontend_defaults_match_the_backend_defaults():
    defaults = dict(re.findall(r"export const (DEFAULT_[A-Z]+) = '([a-z0-9-]+)';", THEMES_JS))
    assert defaults == {
        "DEFAULT_THEME": config_mod.DEFAULT_SETTINGS["theme"],
        "DEFAULT_LIGHT": config_mod.DEFAULT_SETTINGS["theme_light"],
        "DEFAULT_DARK": config_mod.DEFAULT_SETTINGS["theme_dark"],
    }
    assert THEMES[defaults["DEFAULT_LIGHT"]]["kind"] == "light"
    assert THEMES[defaults["DEFAULT_DARK"]]["kind"] == "dark"


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_builtin_theme_defines_every_token(theme_id):
    assert set(THEMES[theme_id]["colors"]) == set(config_mod.THEME_TOKENS)


@pytest.mark.parametrize(
    "theme_id, selector",
    [("graphite", ':root, :root[data-theme="dark"] {'), ("daylight", ':root[data-theme="light"] {')],
)
def test_stylesheet_defaults_match_the_default_themes(theme_id, selector):
    css = css_block(selector)
    for token, value in THEMES[theme_id]["colors"].items():
        assert css.get(token, "").upper() == value.upper(), token


# Text and background pairs every built-in theme must keep readable (WCAG ratio).
READABLE = [
    ("text", "bg", 4.5),
    ("text", "panel", 4.5),
    ("text", "raised", 4.5),
    ("text-strong", "bg", 7),
    ("text-strong", "selected", 4.5),
    ("text-strong", "highlight", 4.5),
    ("muted", "bg", 3.9),
    ("muted", "panel", 3.6),
    ("title", "bg", 4.5),
    ("link", "bg", 4.5),
    ("on-accent", "accent", 4.5),
    ("on-accent", "accent-hover", 4.0),
    ("on-accent-2", "accent-2", 3.0),
    ("on-brand", "brand-1", 4.5),
    ("on-brand", "brand-2", 4.5),
    ("on-brand-muted", "brand-1", 3.0),
    ("text-strong", "ok-bg", 4.5),
    ("text-strong", "danger-bg", 4.5),
    ("text-strong", "ok-bg-strong", 4.5),
    ("text-strong", "danger-bg-strong", 4.5),
    ("ok", "ok-bg", 3.0),
    ("danger", "danger-bg", 3.0),
    ("danger", "bg", 3.0),
    ("warn", "warn-bg", 3.0),
    ("on-danger", "danger", 4.5),
    ("lane-0", "bg", 3.0),
    ("lane-1", "bg", 3.0),
]


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_builtin_theme_is_readable(theme_id):
    colors = THEMES[theme_id]["colors"]
    problems = [
        f"{fg} on {bg}: {contrast(colors[fg], colors[bg]):.2f} < {minimum}"
        for fg, bg, minimum in READABLE
        if contrast(colors[fg], colors[bg]) < minimum
    ]
    # On a dark theme the second accent and accent text sit on the background;
    # on a light theme the accent itself is used as text.
    text_accent = "accent-2" if THEMES[theme_id]["kind"] == "dark" else "accent"
    if contrast(colors[text_accent], colors["bg"]) < 4.5:
        problems.append(f"{text_accent} as text on bg: {contrast(colors[text_accent], colors['bg']):.2f} < 4.5")
    if contrast(colors[text_accent], colors["panel"]) < 4.0:
        problems.append(f"{text_accent} as text on panel: {contrast(colors[text_accent], colors['panel']):.2f} < 4.0")
    assert not problems, "\n".join(problems)


# -- settings ---------------------------------------------------------------------


def good_theme(**over):
    theme = {"id": "custom-mine", "name": "Mine", "kind": "dark", "colors": {"bg": "#101010", "text": "#eeeeee"}}
    theme.update(over)
    return theme


def test_clean_theme_keeps_valid_fields_only():
    cleaned = clean_theme(good_theme(colors={"bg": "#101010", "nope": "#ffffff", "text": "red", "accent": "#abcdef"}, extra=1))
    assert cleaned == {"id": "custom-mine", "name": "Mine", "kind": "dark", "colors": {"bg": "#101010", "accent": "#ABCDEF"}}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "theme",
        good_theme(id="daylight"),
        good_theme(id="custom-Bad Id"),
        good_theme(kind="sepia"),
        good_theme(colors={}),
        good_theme(colors={"bg": "url(javascript:alert(1))"}),
        good_theme(colors="#fff"),
    ],
)
def test_clean_theme_rejects_bad_input(bad):
    assert clean_theme(bad) is None


def test_theme_settings_round_trip(tmp_path):
    c = Config(tmp_path / "cfg")
    s = c.update_settings({"custom_themes": [good_theme(), good_theme(), {"junk": True}], "theme": "custom-mine", "theme_dark": "graphite"})
    assert [t["id"] for t in s["custom_themes"]] == ["custom-mine"]
    assert s["theme"] == "custom-mine" and s["theme_dark"] == "graphite"
    again = Config(tmp_path / "cfg").settings()
    assert again["custom_themes"] == s["custom_themes"] and again["theme"] == "custom-mine"


def test_theme_setting_validation():
    assert coerce_setting("theme", "system") == (True, "system")
    assert coerce_setting("theme", "light") == (True, "daylight")  # value from before themes existed
    assert coerce_setting("theme", "dark") == (True, "graphite")
    assert coerce_setting("theme", "Not A Theme")[0] is False
    assert coerce_setting("theme", 7)[0] is False
    assert coerce_setting("theme_light", "system")[0] is False
    assert coerce_setting("custom_themes", "x")[0] is False


def test_custom_theme_list_is_capped(tmp_path):
    c = Config(tmp_path / "cfg")
    many = [good_theme(id=f"custom-t{i}") for i in range(config_mod.MAX_CUSTOM_THEMES + 20)]
    assert len(c.update_settings({"custom_themes": many})["custom_themes"]) == config_mod.MAX_CUSTOM_THEMES


def test_default_settings_are_not_shared_between_instances(tmp_path):
    a = Config(tmp_path / "a")
    a.update_settings({"custom_themes": [good_theme()]})
    assert Config(tmp_path / "b").settings()["custom_themes"] == []
    assert config_mod.DEFAULT_SETTINGS["custom_themes"] == []


# -- starting in the last theme ---------------------------------------------------


def test_last_theme_is_validated_and_persisted(tmp_path):
    c = Config(tmp_path / "cfg")
    assert c.last_theme() is None
    saved = c.set_last_theme({"kind": "dark", "colors": {"bg": "#101820", "text": "#eeeeee", "x": "#000000", "accent": "nope"}})
    assert saved == {"kind": "dark", "colors": {"bg": "#101820", "text": "#EEEEEE"}, "follow_system": False}
    followed = c.set_last_theme({"kind": "light", "colors": {"bg": "#FFFFFF"}, "follow_system": True})
    assert followed["follow_system"] is True
    saved = followed
    assert Config(tmp_path / "cfg").last_theme() == saved


@pytest.mark.parametrize(
    "bad",
    [None, {}, {"kind": "dark"}, {"kind": "blue", "colors": {"bg": "#000000"}}, {"kind": "dark", "colors": {"text": "#ffffff"}}],
)
def test_last_theme_rejects_bad_records(tmp_path, bad):
    c = Config(tmp_path / "cfg")
    assert c.set_last_theme(bad) is None
    assert c.last_theme() is None


def test_window_size_is_validated_and_persisted(tmp_path):
    c = Config(tmp_path / "cfg")
    assert c.window_size() is None
    c.set_window_size(1600, 1000)
    assert Config(tmp_path / "cfg").window_size() == (1600, 1000)
    c.set_window_size(10, 10)  # too small to be a real window: ignored
    c.set_window_size("wide", None)
    assert c.window_size() == (1600, 1000)


def test_first_paint_attributes_come_from_the_last_theme(tmp_path):
    from gitsense.server import App

    app = App(Config(tmp_path / "cfg"))
    assert app.first_paint_attrs() == ""
    assert app.ui_theme({"kind": "dark", "colors": {"bg": "#101820", "text": "#EEEEEE"}}) == {"ok": True}
    assert app.first_paint_attrs() == 'data-theme="dark" style="--bg:#101820;--text:#EEEEEE"'
    # Nothing but validated hex values can reach the page.
    assert app.ui_theme({"kind": "dark", "colors": {"bg": '"><script>'}}) == {"ok": False}
    assert "script" not in app.first_paint_attrs()


def test_index_page_carries_the_splash_and_the_first_paint_hook():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'data-first-paint=""' in html
    assert 'id="splash"' in html and html.index('id="splash"') < html.index('id="topbar"')
    assert html.index("css/splash.css") < html.index("css/base.css")
    # Places a mod can fill when the server serves the page.
    assert html.index("css/themes.css") < html.index("<!-- mod stylesheets -->") < html.index("</head>")
    for region in ("rider", "lines"):
        assert html.count(f"<!-- splash:{region} -->") == 1 and html.count(f"<!-- /splash:{region} -->") == 1


def test_theme_reports_reach_the_window_hook(tmp_path):
    from gitsense.server import App

    app = App(Config(tmp_path / "cfg"))
    seen = []
    app.on_theme_painted = seen.append
    app.ui_theme({"kind": "dark", "colors": {"bg": "#101820"}, "follow_system": True})
    app.ui_theme({"kind": "dark", "colors": {"bg": "nope"}})
    assert seen == [{"kind": "dark", "colors": {"bg": "#101820"}, "follow_system": True}]
    app.on_theme_painted = lambda theme: 1 / 0  # a failing hook must not break the request
    assert app.ui_theme({"kind": "light", "colors": {"bg": "#FFFFFF"}}) == {"ok": True}
