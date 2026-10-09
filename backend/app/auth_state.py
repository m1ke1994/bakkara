"""Small, testable decisions for browser authorization probes."""


def classify_auth_probe(*, balance_marker: bool, login_surface: bool, check_error: bool = False) -> str:
    """A missing balance marker alone is ambiguous, never proof of logout."""
    if check_error:
        return "CHECK_ERROR"
    if login_surface:
        return "AUTH_LOST"
    if balance_marker:
        return "AUTHORIZED"
    return "TEMPORARILY_UNCONFIRMED"


def authorization_allows_observation(state: str) -> bool:
    """Allow read-only page navigation on ambiguous markers, never on known logout/error."""
    return state in {"AUTHORIZED", "TEMPORARILY_UNCONFIRMED"}
