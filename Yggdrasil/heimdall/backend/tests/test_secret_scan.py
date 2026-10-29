"""Secret scan (the pre-commit guard): which stored values it looks for and how it
reports them. Every value here is made up."""
from secret_scan import find_secrets, secret_values

CREDENTIALS = [
    {'login': 'alpha', 'password': 'not-a-real-pw-1', 'email': 'Alpha.Person@example.com',
     'billing_address': 'Test Person\nExample Street 12, Ap. 3\nTest City 1000\nTestland, Republic of'},
    {'login': 'beta', 'password': 'abc', 'email': '', 'billing_address': ''},
]
MAFILES = [{'shared_secret': 'fakeSharedSecret0000=', 'identity_secret': 'fakeIdentitySecret00=',
            'revocation_code': 'R12345', 'Session': {'RefreshToken': 'eyFakeRefreshToken.abcdefghijk'}}]


def test_secret_values_kinds():
    values = secret_values(CREDENTIALS, MAFILES)
    assert values['not-a-real-pw-1'] == 'a Mímir password'
    assert values['alpha.person@example.com'] == 'a Mímir email'
    assert values['test person'] == 'a Mímir billing address'
    assert values['example street 12, ap. 3'] == 'a Mímir billing address'
    assert values['test city 1000'] == 'a Mímir billing address'
    assert 'testland, republic of' not in values      # letters only: too common
    assert 'abc' not in values                        # too short to look for
    assert values['fakeSharedSecret0000='] == 'a maFile shared_secret'
    assert values['eyFakeRefreshToken.abcdefghijk'] == 'a maFile session RefreshToken'


def test_find_secrets_reports_line_and_kind_never_the_value():
    values = secret_values(CREDENTIALS, MAFILES)
    text = ('+harmless line\n'
            '+password = "not-a-real-pw-1"\n'
            '+contact: alpha.person@EXAMPLE.com\n'
            '+Ship to EXAMPLE STREET 12, Ap. 3 please\n'
            '+NOT-A-REAL-PW-1 in capitals is a different password\n'
            '+Testland, Republic of\n')
    assert find_secrets(text, values) == [(2, 'a Mímir password'), (3, 'a Mímir email'),
                                          (4, 'a Mímir billing address')]
    assert find_secrets('+nothing here\n', values) == []
