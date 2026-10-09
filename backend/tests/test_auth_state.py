from app.auth_state import authorization_allows_observation, classify_auth_probe


def test_missing_balance_marker_is_temporary_not_logout():
    assert classify_auth_probe(balance_marker=False, login_surface=False) == "TEMPORARILY_UNCONFIRMED"


def test_explicit_login_surface_confirms_logout():
    assert classify_auth_probe(balance_marker=False, login_surface=True) == "AUTH_LOST"


def test_balance_marker_confirms_login_and_check_failure_stays_separate():
    assert classify_auth_probe(balance_marker=True, login_surface=False) == "AUTHORIZED"
    assert classify_auth_probe(balance_marker=False, login_surface=False, check_error=True) == "CHECK_ERROR"


def test_observation_allows_ambiguous_read_only_scan_but_never_bypasses_logout_or_error():
    assert authorization_allows_observation("TEMPORARILY_UNCONFIRMED")
    assert not authorization_allows_observation("CHECK_ERROR")
    assert not authorization_allows_observation("AUTH_LOST")
    assert authorization_allows_observation("AUTHORIZED")
