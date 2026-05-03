from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/"