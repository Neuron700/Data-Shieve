"""Fast, isolated settings for the automated test suite."""

import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver", "localhost"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
PRIVATE_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="datasieve-tests-"))
TEMP_UPLOAD_ROOT = PRIVATE_MEDIA_ROOT / "tmp"
DATASET_STORAGE_ROOT = PRIVATE_MEDIA_ROOT / "datasets"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
