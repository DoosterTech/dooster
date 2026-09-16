"""
Which environment a build is for: 'production' (the live site) or 'dev'.

    SITE_ENV=production|dev    optional explicit override
    AWS_BRANCH                 set by Amplify on every build

SITE_ENV wins when it is set. Otherwise the Amplify branch decides: main is
production, every other branch — and any local run — is dev. So nothing needs
setting in the Amplify console for this to work.

The one combination this refuses is the main branch building as dev. That build
would tell search engines to drop the live site and would ship the internal
review pages, so it stops the build instead.
"""

import os

VALID = ("production", "dev")


def resolve(environ=os.environ):
    branch = environ.get("AWS_BRANCH", "")
    explicit = environ.get("SITE_ENV", "").strip().lower()

    if explicit and explicit not in VALID:
        raise SystemExit(f"SITE_ENV must be one of {VALID}, not {explicit!r}")

    env = explicit or ("production" if branch == "main" else "dev")

    if branch == "main" and env != "production":
        raise SystemExit(
            "Refusing to build the main branch as dev: it would de-index the "
            "live site. Remove SITE_ENV from the main branch in Amplify.")
    return env


SITE_ENV = resolve()
IS_PRODUCTION = SITE_ENV == "production"
