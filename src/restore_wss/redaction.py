"""Keeping secrets out of the snapshot.

A captured command line is untrusted input that the user did not think about when they typed it,
and the snapshot is a file that sits on disk for ever. So arguments that look like credentials are
replaced **at capture time** — the secret is never written, not written-then-filtered — and the
positions that were replaced are recorded, so that:

* the user can see that something was withheld rather than wondering why a command looks odd;
* restore knows the command is incomplete and refuses to re-run it automatically, whatever the
  policy says (``policy.py``).

This is heuristic and it is meant to over-redact. A false positive costs a command that has to be
re-typed; a false negative writes a password to a file the user forgot they had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER = "<redacted>"

#: Option names whose *value* is a secret, matched case-insensitively with any number of dashes.
SECRET_OPTIONS = (
    "password",
    "passwd",
    "pass",
    "token",
    "secret",
    "api-key",
    "apikey",
    "api_key",
    "access-key",
    "access_key",
    "private-key",
    "auth",
    "credential",
    "credentials",
    "bearer",
    "session-token",
    "client-secret",
)

_OPTION_RE = re.compile(
    r"^--?(" + "|".join(re.escape(name) for name in SECRET_OPTIONS) + r")(=(?P<value>.*))?$",
    re.IGNORECASE,
)

#: Values that look like a secret wherever they appear: long random-looking strings, and the token
#: shapes that are recognisable on sight.
_VALUE_PATTERNS = (
    re.compile(r"^(gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}$"),  # GitHub
    re.compile(r"^sk-[A-Za-z0-9_-]{16,}$"),  # OpenAI-style
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"),  # Slack
    re.compile(r"^AKIA[0-9A-Z]{16}$"),  # AWS access key id
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
)

#: An environment-variable assignment on the command line: FOO_TOKEN=... is a secret too.
_ENV_ASSIGNMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$",
)
_SECRET_NAME_RE = re.compile(
    r"(PASS|PASSWORD|TOKEN|SECRET|APIKEY|API_KEY|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY)",
    re.IGNORECASE,
)


@dataclass
class RedactionResult:
    argv: list[str] = field(default_factory=list)
    #: Indices in ``argv`` that were replaced.
    redacted: list[int] = field(default_factory=list)


def looks_secret(value: str) -> bool:
    return any(pattern.match(value) for pattern in _VALUE_PATTERNS)


def redact(argv: list[str], extra_options: tuple[str, ...] = ()) -> RedactionResult:
    """Replace credential-shaped arguments with a placeholder."""
    option_re = _OPTION_RE
    if extra_options:
        names = list(SECRET_OPTIONS) + list(extra_options)
        option_re = re.compile(
            r"^--?(" + "|".join(re.escape(name) for name in names) + r")(=(?P<value>.*))?$",
            re.IGNORECASE,
        )

    out: list[str] = []
    redacted: list[int] = []
    expect_value = False

    for argument in argv:
        if expect_value:
            out.append(PLACEHOLDER)
            redacted.append(len(out) - 1)
            expect_value = False
            continue

        match = option_re.match(argument)
        if match:
            if match.group("value") is not None:
                # --token=abc123 — keep the option name, drop the value.
                name = argument.split("=", 1)[0]
                out.append(f"{name}={PLACEHOLDER}")
                redacted.append(len(out) - 1)
            else:
                # --token abc123 — the next argument is the secret.
                out.append(argument)
                expect_value = True
            continue

        assignment = _ENV_ASSIGNMENT_RE.match(argument)
        if assignment and _SECRET_NAME_RE.search(assignment.group("name")):
            out.append(f"{assignment.group('name')}={PLACEHOLDER}")
            redacted.append(len(out) - 1)
            continue

        if looks_secret(argument):
            out.append(PLACEHOLDER)
            redacted.append(len(out) - 1)
            continue

        out.append(argument)

    if expect_value:
        # A trailing --password with nothing after it: nothing to redact, and nothing to hide.
        pass

    return RedactionResult(argv=out, redacted=redacted)
