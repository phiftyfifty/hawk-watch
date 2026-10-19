import pytest

from hawk.redact import SecretLeak, assert_no_secrets

FORBIDDEN_HINTS = ["PASSWORD", "SECRET", "_KEY", "TOKEN"]


def test_clean_snapshot_passes():
    assert_no_secrets({"checks": {"tables": {"tables": {"orders": {"count": 1}}}}})


def test_key_named_like_a_secret_is_rejected():
    with pytest.raises(SecretLeak):
        assert_no_secrets({"checks": {"env": {"DB_PASSWORD": "hunter2"}}})


def test_value_containing_a_secret_marker_is_rejected():
    with pytest.raises(SecretLeak):
        assert_no_secrets({"checks": {"note": "SECRET=abc123"}})


def test_nested_list_is_searched():
    with pytest.raises(SecretLeak):
        assert_no_secrets({"checks": [{"deep": [{"API_TOKEN": "x"}]}]})


def test_benign_word_key_is_not_a_false_positive():
    assert_no_secrets({"checks": {"tables": {"monkey": {"count": 3}}}})


@pytest.mark.parametrize("hint", FORBIDDEN_HINTS)
def test_every_forbidden_hint_is_detected(hint):
    with pytest.raises(SecretLeak):
        assert_no_secrets({"checks": {hint.lower(): f"value {hint} here"}})
