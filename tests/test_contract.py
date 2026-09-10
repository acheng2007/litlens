import json
from pathlib import Path

from litlens.main import app


def test_openapi_contract_matches_application():
    contract = json.loads(Path("docs/openapi.json").read_text())
    assert app.openapi() == contract
    for path, methods in contract["paths"].items():
        if path.startswith("/api/v1/"):
            assert all(method["security"] == [{"HTTPBearer": []}] for method in methods.values())
