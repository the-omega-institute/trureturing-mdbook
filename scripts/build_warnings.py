"""GitHub workflow warnings for content that the site shows in degraded form.

A content defect in one upstream file (a formula, a link, a dossier) must not keep the
whole site from publishing. The affected unit is shown in a degraded but honest form,
and the build log carries one warning per unit naming the upstream file to fix.
"""

from __future__ import annotations

import sys


def workflow_warning(title: str, message: str) -> str:
    # GitHub workflow command; %, CR and LF must be escaped in the message, and the
    # title is a property value, where ',' and ':' are escaped as well.
    text = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = (title.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
             .replace(":", "%3A").replace(",", "%2C"))
    return f"::warning title={title}::{text}"


def warn(title: str, message: str) -> None:
    print(workflow_warning(title, message), file=sys.stderr, flush=True)
