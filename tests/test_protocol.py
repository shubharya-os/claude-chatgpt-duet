from duet.protocol import Envelope, parse_envelope, iter_json_objects


def test_reads_a_fenced_envelope():
    env = parse_envelope(
        'Here is my take.\n\n```json\n{"message": "done it", "verdict": "DONE", "confidence": 0.8}\n```',
        agent="gpt", round_no=1,
    )
    assert env.parse_ok and env.verdict == "DONE"
    assert env.message == "done it"
    assert env.confidence == 0.8


def test_prefers_the_last_envelope_when_the_reply_shows_an_example_first():
    text = (
        'The format is {"message": "example", "verdict": "CONTINUE"} roughly.\n'
        '```json\n{"message": "real one", "verdict": "DONE"}\n```'
    )
    assert parse_envelope(text).message == "real one"


def test_bare_json_without_a_fence_still_parses():
    env = parse_envelope('{"message": "no fence", "verdict": "continue"}')
    assert env.parse_ok and env.verdict == "CONTINUE"


def test_prose_only_reply_is_continue_not_agreement():
    env = parse_envelope("Looks good to me, ship it!")
    assert env.parse_ok is False
    assert env.verdict == "CONTINUE"  # silence is never taken as a sign-off
    assert "no JSON envelope" in env.notes[0]


def test_done_while_raising_a_blocker_is_downgraded():
    env = parse_envelope(
        '{"message": "m", "verdict": "DONE", "issues": [{"title": "leaks a fd", "severity": "blocker"}]}'
    )
    assert env.verdict == "CONTINUE"
    assert any("downgraded" in n for n in env.notes)


def test_done_alongside_a_minor_nit_is_allowed():
    env = parse_envelope(
        '{"message": "m", "verdict": "DONE", "issues": [{"title": "naming", "severity": "nit"}]}'
    )
    assert env.verdict == "DONE"
    assert env.issues[0].severity == "minor"


def test_severity_synonyms_and_bad_values_normalise():
    env = parse_envelope(
        '{"message": "m", "verdict": "CONTINUE", "issues": ['
        '{"id": "a", "title": "x", "severity": "critical"},'
        '{"id": "b", "title": "y", "severity": "banana"}]}'
    )
    assert env.issues[0].severity == "blocker"
    assert env.issues[1].severity == "major"


def test_issue_ids_are_slugged_and_deduped():
    env = parse_envelope(
        '{"message": "m", "verdict": "CONTINUE", "issues": ['
        '{"title": "Empty Name Accepted!"}, {"title": "Empty Name Accepted!"}]}'
    )
    assert env.issues[0].id == "empty-name-accepted"
    assert env.issues[1].id != env.issues[0].id


def test_patches_and_reads_are_captured():
    env = parse_envelope(
        '{"message": "m", "verdict": "CONTINUE",'
        ' "patches": [{"path": "a.py", "action": "create", "content": "x = 1"}],'
        ' "reads": ["b.py"]}'
    )
    assert env.patches[0].action == "write" and env.patches[0].path == "a.py"
    assert env.meta["reads"] == ["b.py"]


def test_garbage_confidence_does_not_raise():
    env = parse_envelope('{"message": "m", "verdict": "DONE", "confidence": "very"}')
    assert env.confidence == 0.5


def test_iter_json_objects_skips_broken_braces():
    found = iter_json_objects('{not json} then {"a": 1} tail')
    assert [obj for obj, _ in found] == [{"a": 1}]
