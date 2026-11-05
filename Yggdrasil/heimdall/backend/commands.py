"""The one shape of a refused command, for every route: ``CommandError(code, message, status)``.

A service raises it when it refuses a command before anything was sent; the app-wide handler
(``error_handlers.install``) answers ``{error, code}`` with its status, so a route only returns its
answer and never catches it. ``code`` is for clients to act on (the page, the command line).
"""


class CommandError(Exception):
    """A command refused before anything was sent; `code` is for clients to act on."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def body(self):
        return {'error': self.message, 'code': self.code}
