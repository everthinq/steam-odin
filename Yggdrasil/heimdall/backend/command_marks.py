"""What the `odin` command-line tool needs to know about a route, written on the route itself.

Every route is a command (`GET /api/system/routes` lists them): its name is made from its
path by ``command_name`` in ``routes/system.py``. Three marks change that, put under the
route's ``@bp.route`` line:

- ``@command_verb('add')``: the last word of the command, when the path alone does not say
  what a POST does (adding to a list, saving settings, answering a confirmation).
- ``@spends_money``: the route can buy for real. odin sends it a dry run, or, with ``--real``
  typed by a person at a terminal, reads the plan (the path without ``/execute``; its
  ``real_purchase`` from ``plans.real_purchase``), shows it and buys only after the total is typed.
- ``@shows_secrets``: the answer holds passwords, tokens, emails or billing addresses. odin
  prints it only to a person at a terminal, never into a pipe or a file (an agent's shell).
"""


def command_verb(verb):
    def mark(view):
        view.command_verb = verb
        return view
    return mark


def spends_money(view):
    view.spends_money = True
    return view


def shows_secrets(view):
    view.shows_secrets = True
    return view
