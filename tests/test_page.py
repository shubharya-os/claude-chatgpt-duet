"""`duet page` without a browser: the rules, the lines, the exit codes.

Everything here is the half of `duet page` that is pure data — a rules file is
valid or it is not, a measurement is a fault line or it is not, a setup fault is
exit 3 and a page fault is exit 1. The half that needs a real Chrome is in
test_page_chrome.py, which skips when there is none, and the half a project sees
without ever typing the command — gate detection, the rules file as a test suite
— is in test_page_session.py.

The split matters for one reason in particular: "duet could not run the check"
and "this page is broken" are different answers, and a session that reads the
first as the second spends its rounds hunting a bug that is not there. Several
cases below exist only to hold that line.
"""

import argparse
import json

import pytest

from duet import chrome, page
from duet.chrome import SetupError
from duet.cli import main


def args(**kwargs):
    """The namespace argparse would hand check_cmd / shot_cmd."""
    base = {"page": "", "root": ".", "width": "", "allow_network": False,
            "rules": None, "out": None, "page_command": "check"}
    base.update(kwargs)
    return argparse.Namespace(**base)


# -- finding Chrome ----------------------------------------------------------
# A gate runs with a minimal PATH, so "no Chrome" on a machine that has Chrome
# is a wrong answer — and the wrong *kind* of answer, since it is not the page's
# fault either way.

def test_duet_chrome_is_obeyed_when_it_points_at_a_binary(tmp_path):
    binary = tmp_path / "my-chrome"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    assert chrome.find_chrome({"DUET_CHROME": str(binary)}) == str(binary)


def test_duet_chrome_pointing_nowhere_is_a_setup_fault(tmp_path):
    """Silently ignoring an explicit choice is how you debug the wrong browser."""
    with pytest.raises(SetupError) as caught:
        chrome.find_chrome({"DUET_CHROME": str(tmp_path / "not-here")})
    assert "DUET_CHROME" in str(caught.value)


def test_a_name_on_path_is_used_before_the_install_locations(monkeypatch):
    monkeypatch.setattr("duet.chrome.shutil.which",
                        lambda name: "/opt/bin/" + name if name == "chromium" else None)
    assert chrome.find_chrome({}) == "/opt/bin/chromium"


def test_chrome_is_found_where_it_installs_when_path_has_nothing(monkeypatch, tmp_path):
    installed = tmp_path / "Google Chrome"
    installed.write_text("")
    installed.chmod(0o755)
    monkeypatch.setattr("duet.chrome.shutil.which", lambda name: None)
    monkeypatch.setattr(chrome, "INSTALL_PATHS", (str(tmp_path / "gone"), str(installed)))
    assert chrome.find_chrome({}) == str(installed)


def test_no_chrome_at_all_is_a_setup_fault_naming_the_way_out(monkeypatch):
    monkeypatch.setattr("duet.chrome.shutil.which", lambda name: None)
    monkeypatch.setattr(chrome, "INSTALL_PATHS", ())
    with pytest.raises(SetupError) as caught:
        chrome.find_chrome({})
    message = str(caught.value)
    assert "no Chrome" in message
    assert "DUET_CHROME" in message


def test_windows_is_told_so_rather_than_failing_halfway(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    problem = chrome.unsupported_platform()
    assert problem and "Windows" in problem


def test_check_on_windows_is_exit_3(monkeypatch, capsys):
    monkeypatch.setattr(page, "unsupported_platform", lambda: "not here it is not")
    assert page.check_cmd(args(page="landing.html")) == 3
    assert page.shot_cmd(args(page="landing.html", page_command="shot")) == 3
    assert "not here it is not" in capsys.readouterr().err


# -- the rules file ----------------------------------------------------------

def test_the_documented_example_is_accepted():
    """The format in RULES_DOC is the format, checked rather than asserted."""
    text = page.RULES_DOC
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    rules = page.parse_rules(data)
    assert rules.page == "site/index.html"
    assert [r["comparator"] for r in rules.styles] == \
        ["equals", "at_least", "one_of", "at_most"]
    assert rules.styles[1] == {"selector": ".cta", "property": "min-height",
                               "comparator": "at_least", "value": 44.0}
    assert rules.text_colors == ["#7a0000", "rgb(122, 0, 0)"]
    assert rules.forbid_text == [r"\bcoming soon\b", "lorem ipsum"]
    assert bool(rules) is True


def test_an_empty_rules_file_is_valid_and_falsey():
    rules = page.parse_rules({})
    assert bool(rules) is False
    assert rules.payload() == {"styles": [], "text_colors": []}


def test_an_unknown_key_is_an_error_not_a_comment():
    """A mistyped rule that is ignored is worse than no rule: it looks checked."""
    with pytest.raises(page.RulesError) as caught:
        page.parse_rules({"stylez": []})
    message = str(caught.value)
    assert "'stylez'" in message
    assert "styles" in message                  # and what it should have been


def test_a_rules_error_is_a_setup_fault_so_it_exits_3():
    assert issubclass(page.RulesError, SetupError)


@pytest.mark.parametrize("data,expected", [
    ([], "must hold a JSON object"),
    ({"page": 7}, "must be a file or URL"),
    ({"styles": {}}, "must be a list"),
    ({"styles": ["h1"]}, "must be an object"),
    ({"styles": [{"selector": "h1", "property": "color"}]}, "exactly one of"),
    ({"styles": [{"selector": "h1", "property": "color",
                  "equals": "red", "at_least": 4}]}, "exactly one of"),
    ({"styles": [{"selector": "h1", "propertie": "color", "equals": "red"}]}, "unknown key"),
    ({"styles": [{"selector": "", "property": "color", "equals": "red"}]}, "'selector'"),
    ({"styles": [{"selector": "h1", "property": "min-height",
                  "at_least": "tall"}]}, "number of pixels"),
    ({"styles": [{"selector": "h1", "property": "color", "one_of": []}]}, "non-empty list"),
    ({"forbid_text_colors": "#fff"}, "must be a list"),
    ({"forbid_text_colors": [""]}, "not a colour"),
    ({"forbid_text": "soon"}, "must be a list"),
    ({"forbid_text": ["("]}, "not a valid regular expression"),
])
def test_what_the_rules_file_refuses(data, expected):
    with pytest.raises(page.RulesError) as caught:
        page.parse_rules(data)
    assert expected in str(caught.value)


def test_pixels_may_be_written_as_a_number_or_as_px():
    rules = page.parse_rules({"styles": [
        {"selector": ".cta", "property": "min-height", "at_least": "44px"},
        {"selector": ".badge", "property": "letter-spacing", "at_most": 0.5},
    ]})
    assert rules.styles[0]["value"] == 44.0
    assert rules.styles[1]["value"] == 0.5


def test_british_spelling_is_accepted_and_merged():
    rules = page.parse_rules({"forbid_text_colours": ["#7a0000"],
                              "forbid_text_colors": ["#7a0000", "red"]})
    assert rules.text_colors == ["#7a0000", "red"]


def test_a_rules_path_given_explicitly_must_exist(tmp_path):
    with pytest.raises(page.RulesError) as caught:
        page.load_rules(str(tmp_path / "nope.json"))
    assert "no rules file at" in str(caught.value)


def test_the_default_rules_file_need_not_exist(tmp_path):
    assert bool(page.load_rules(None, root=str(tmp_path))) is False


def test_rules_that_are_not_json_say_which_file(tmp_path):
    (tmp_path / page.PAGE_RULES).write_text("{not json", encoding="utf-8")
    with pytest.raises(page.RulesError) as caught:
        page.load_rules(None, root=str(tmp_path))
    assert page.PAGE_RULES in str(caught.value)
    assert "not valid JSON" in str(caught.value)


# -- widths, viewports, targets ---------------------------------------------

def test_the_default_widths_are_a_laptop_and_a_phone():
    assert page.DEFAULT_WIDTHS == (1440, 375)


def test_a_phone_width_is_emulated_at_exactly_that_width():
    """Headless Chrome will not make a window narrower than 500px, so the phone
    width has to be a device metrics override rather than a window size. If this
    ever silently became a window size again, 375 would render as 500."""
    width, height, mobile = page.viewport_for(375)
    assert (width, mobile) == (375, True)
    assert height == page.PHONE_HEIGHT
    assert page.viewport_for(1440) == (1440, page.DESKTOP_HEIGHT, False)


def test_widths_are_parsed_deduplicated_and_bounded():
    assert page.parse_widths("1440,375") == [1440, 375]
    assert page.parse_widths(" 1440 , 1440 ,375") == [1440, 375]
    for bad in ("wide", "12", "99999", ""):
        with pytest.raises(SetupError):
            page.parse_widths(bad)


def test_a_missing_file_is_a_setup_fault_and_a_url_is_taken_as_written(tmp_path):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    url = page.resolve_target("landing.html", root=str(tmp_path))
    assert url.startswith("file://") and url.endswith("/landing.html")
    assert page.resolve_target("http://localhost:8000/") == "http://localhost:8000/"
    with pytest.raises(SetupError) as caught:
        page.resolve_target("nope.html", root=str(tmp_path))
    assert "no page at nope.html" in str(caught.value)
    with pytest.raises(SetupError):
        page.resolve_target("")


# -- turning measurements into lines ----------------------------------------

def test_a_fault_line_leads_with_the_width():
    assert page.fault_line(375, "collapsed-control", "input.email is 240x20px") == \
        "375: collapsed-control: input.email is 240x20px"


def test_every_measured_fault_becomes_one_line_once():
    measured = {
        "faults": [{"rule": "empty-strip", "detail": "div.filler is 900x64px"},
                   {"rule": "empty-strip", "detail": "div.filler is 900x64px"},
                   {"rule": "dead-link", "detail": "a points at \"#pricing\""}],
        "exceptions": ["TypeError: x is not a function (app.js:12)"],
        "text": "",
    }
    lines = page.faults_for_width(measured, 1440, page.Rules())
    assert lines == [
        "1440: script-error: TypeError: x is not a function (app.js:12)",
        "1440: empty-strip: div.filler is 900x64px",
        "1440: dead-link: a points at \"#pricing\"",
    ]


def test_forbidden_text_is_matched_against_what_the_page_rendered():
    """Case-insensitively: a headline is written "Coming Soon", and a rule that
    quietly missed it would look like it was being checked."""
    rules = page.parse_rules({"forbid_text": [r"\bcoming soon\b", "never here"]})
    lines = page.faults_for_width({"text": "Pricing: Coming Soon"}, 375, rules)
    assert len(lines) == 1
    assert lines[0].startswith("375: forbidden-text: ")
    assert "Coming Soon" in lines[0]
    assert page.faults_for_width({"text": "Pricing"}, 375, rules) == []


def test_a_rule_can_still_insist_on_the_case():
    rules = page.parse_rules({"forbid_text": [r"(?-i:BETA)"]})
    assert page.faults_for_width({"text": "the BETA build"}, 375, rules) != []
    assert page.faults_for_width({"text": "the beta build"}, 375, rules) == []


def test_a_measurement_with_nothing_wrong_produces_nothing():
    assert page.faults_for_width({"faults": [], "exceptions": [], "text": "hi"},
                                 1440, page.Rules()) == []


# -- exit codes --------------------------------------------------------------

def test_a_missing_page_is_exit_3_not_exit_1(tmp_path, capsys):
    """The whole point of the third code: a session that reads this as a page
    fault rewrites CSS to fix a path that was simply wrong."""
    assert main(["page", "check", "nope.html", "-C", str(tmp_path)]) == 3
    assert "no page at nope.html" in capsys.readouterr().err


def test_no_page_named_anywhere_is_exit_3(tmp_path, capsys):
    assert main(["page", "check", "-C", str(tmp_path)]) == 3
    assert page.PAGE_RULES in capsys.readouterr().err


def test_a_broken_rules_file_is_exit_3(tmp_path, capsys):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    (tmp_path / page.PAGE_RULES).write_text(json.dumps({"stylez": []}), encoding="utf-8")
    assert main(["page", "check", "landing.html", "-C", str(tmp_path)]) == 3
    assert "'stylez'" in capsys.readouterr().err


def test_a_bad_width_is_exit_3(tmp_path, capsys):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    assert main(["page", "check", "landing.html", "--width", "tiny",
                 "-C", str(tmp_path)]) == 3
    assert "--width" in capsys.readouterr().err


def test_page_faults_are_exit_1_and_one_line_each(tmp_path, monkeypatch, capsys):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setattr(page, "check_page", lambda *a, **k: [
        page.fault_line(375, "collapsed-control", "input.email is 240x20px"),
        page.fault_line(375, "empty-strip", "div.filler is 375x64px"),
    ])
    assert main(["page", "check", "landing.html", "-C", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "375: collapsed-control: input.email is 240x20px" in out.out
    assert "375: empty-strip: div.filler is 375x64px" in out.out
    assert "2 faults" in out.err


def test_a_clean_page_is_exit_0(tmp_path, monkeypatch, capsys):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setattr(page, "check_page", lambda *a, **k: [])
    assert main(["page", "check", "landing.html", "-C", str(tmp_path)]) == 0
    assert "clean" in capsys.readouterr().out


def test_the_widths_asked_for_reach_the_check(tmp_path, monkeypatch):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    seen = {}

    def fake(target, widths, rules, allow_network=False, root="."):
        seen.update(target=target, widths=list(widths),
                    allow_network=allow_network, rules=rules)
        return []

    monkeypatch.setattr(page, "check_page", fake)
    assert main(["page", "check", "landing.html", "--width", "1280,414",
                 "--allow-network", "-C", str(tmp_path)]) == 0
    assert seen["widths"] == [1280, 414]
    assert seen["allow_network"] is True


def test_the_page_may_come_from_the_rules_file(tmp_path, monkeypatch):
    (tmp_path / "landing.html").write_text("<p>hi</p>", encoding="utf-8")
    (tmp_path / page.PAGE_RULES).write_text(
        json.dumps({"page": "landing.html"}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(page, "check_page",
                        lambda target, *a, **k: seen.setdefault("target", target) and [])
    assert main(["page", "check", "-C", str(tmp_path)]) == 0
    assert seen["target"] == "landing.html"


def test_page_with_no_subcommand_explains_the_format(capsys):
    assert main(["page"]) == 2
    out = capsys.readouterr().out
    assert "duet page check" in out
    assert "forbid_text_colors" in out           # the whole documented format


# -- the shot command that matches a gate -----------------------------------

@pytest.mark.parametrize("gate,expected", [
    ("duet page check site/index.html", "duet page shot site/index.html"),
    ("duet page check index.html --width 1440,375",
     "duet page shot index.html --width 1440,375"),
    # --rules is a check-only flag; --allow-network is not, and a page that
    # needs its own origin needs it to be shot as well.
    ("duet page check index.html --rules duet-page.json --allow-network",
     "duet page shot index.html --allow-network"),
    ("duet page check index.html --rules=brand.json", "duet page shot index.html"),
    ("/venv/bin/python -m duet page check index.html",
     "/venv/bin/python -m duet page shot index.html"),
    # Not a page gate, or not one this can safely rewrite.
    ("pytest -q", ""),
    ("", ""),
    ("duet page shot index.html", ""),
    ("duet page check index.html && pytest -q", ""),
    ("npm test -- --page", ""),
])
def test_the_shot_command_follows_the_gate(gate, expected):
    assert page.shot_command_for(gate) == expected
