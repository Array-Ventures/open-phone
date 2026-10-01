"""Shared source, runtime and native-bundle locations.

OPEN_PHONE_HOME changes the runtime location without silently migrating existing
credentials. OPEN_PHONE_NATIVE_BUNDLE selects a separately built native bundle.
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_HOME = Path(os.environ.get('OPEN_PHONE_HOME', PROJECT_ROOT)).expanduser().resolve()
NATIVE_BUNDLE = Path(os.environ.get('OPEN_PHONE_NATIVE_BUNDLE', PROJECT_ROOT / 'build/OpenPhone.app')).expanduser().resolve()
