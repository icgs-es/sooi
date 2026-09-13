from pathlib import Path

import environ

# Local convenience only. Production never reads this file implicitly.
environ.Env.read_env(Path(__file__).resolve().parents[3] / "infra" / "env" / "dev.env")

from .base import *  # noqa: E402,F403

DEBUG = env.bool("DJANGO_DEBUG", default=True)
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/"
