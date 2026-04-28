"""The web GUI is Phase 2; here we only verify the stub raises clearly."""
import pytest

from aafbrowser.web import app


def test_create_app_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="Phase 2"):
        app.create_app()
