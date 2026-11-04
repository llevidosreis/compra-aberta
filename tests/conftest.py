from unittest.mock import ANY

import pytest


if not hasattr(pytest, "ANY"):
    pytest.ANY = ANY
