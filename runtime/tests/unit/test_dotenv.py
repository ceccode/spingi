import os

from spingi.dotenv import RUNTIME_DIR, default_env_file, load_dotenv, parse


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


def test_only_allow_listed_variables_are_set(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_BASE_URL=https://evil.example\nPYTHONPATH=/tmp/x\nSPINGI_TEST_OK=1\n")
    for k in ("ANTHROPIC_BASE_URL", "SPINGI_TEST_OK"):
        monkeypatch.delenv(k, raising=False)
    loaded, ignored = load_dotenv(env)
    assert loaded == ["SPINGI_TEST_OK"] and sorted(ignored) == ["ANTHROPIC_BASE_URL", "PYTHONPATH"]
    assert "ANTHROPIC_BASE_URL" not in os.environ
    monkeypatch.delenv("SPINGI_TEST_OK")


def test_exported_variables_win_over_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SPINGI_TEST_X=from_file\nSPINGI_TEST_Y=from_file\n")
    monkeypatch.setenv("SPINGI_TEST_X", "exported")
    monkeypatch.delenv("SPINGI_TEST_Y", raising=False)
    loaded, _ = load_dotenv(env)
    assert os.environ["SPINGI_TEST_X"] == "exported" and os.environ["SPINGI_TEST_Y"] == "from_file"
    assert loaded == ["SPINGI_TEST_Y"]
    monkeypatch.delenv("SPINGI_TEST_Y")


def test_a_dotenv_in_the_working_directory_is_never_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SPINGI_TEST_CWD=1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPINGI_TEST_CWD", raising=False)
    assert default_env_file() == RUNTIME_DIR / ".env"
    load_dotenv()
    assert "SPINGI_TEST_CWD" not in os.environ


def test_missing_file_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "missing.env") == ([], [])


def test_example_file_has_no_key_value():
    assert parse((RUNTIME_DIR / ".env.example").read_text()) == {"ANTHROPIC_API_KEY": ""}
