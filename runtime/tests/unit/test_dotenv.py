import os

from spingi.dotenv import load_dotenv, parse


def test_parse_handles_comments_export_quotes_and_inline_comments():
    text = """
# comment
ANTHROPIC_API_KEY=sk-ant-123
export SPINGI_A = 'quoted value'
SPINGI_B="double # not a comment"
SPINGI_C=plain # trailing comment
not a line
=missing key
"""
    assert parse(text) == {
        "ANTHROPIC_API_KEY": "sk-ant-123",
        "SPINGI_A": "quoted value",
        "SPINGI_B": "double # not a comment",
        "SPINGI_C": "plain",
    }


def test_exported_variables_win_over_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SPINGI_TEST_X=from_file\nSPINGI_TEST_Y=from_file\n")
    monkeypatch.setenv("SPINGI_TEST_X", "exported")
    monkeypatch.delenv("SPINGI_TEST_Y", raising=False)
    loaded = load_dotenv(env)
    assert os.environ["SPINGI_TEST_X"] == "exported" and os.environ["SPINGI_TEST_Y"] == "from_file"
    assert loaded == ["SPINGI_TEST_Y"]
    monkeypatch.delenv("SPINGI_TEST_Y")


def test_first_existing_file_is_used_and_missing_files_are_fine(tmp_path, monkeypatch):
    monkeypatch.delenv("SPINGI_TEST_Z", raising=False)
    second = tmp_path / "b.env"
    second.write_text("SPINGI_TEST_Z=second\n")
    assert load_dotenv(tmp_path / "missing.env", second) == ["SPINGI_TEST_Z"]
    assert load_dotenv(tmp_path / "missing.env") == []
    monkeypatch.delenv("SPINGI_TEST_Z")


def test_example_file_has_no_key_value():
    from spingi.dotenv import RUNTIME_DIR

    values = parse((RUNTIME_DIR / ".env.example").read_text())
    assert values == {"ANTHROPIC_API_KEY": ""}
