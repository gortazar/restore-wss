"""``python -m restore_wss`` — same entry point as the installed ``restore-wss`` script.

Exists so the daemon can be started without installing the package: the tests do it, and so does
anyone running from a checkout.
"""

from .cli import main

raise SystemExit(main())
