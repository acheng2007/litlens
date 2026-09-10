import httpx
import respx
from typer.testing import CliRunner

from litlens.cli import app


@respx.mock
def test_cli_uses_authenticated_api(monkeypatch):
    monkeypatch.setenv("LITLENS_API_URL", "http://api.test")
    monkeypatch.setenv("LITLENS_API_TOKEN", "alice-token")
    route = respx.post("http://api.test/api/v1/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    result = CliRunner().invoke(app, ["search", "hybrid retrieval", "--library-id", "lib"])
    assert result.exit_code == 0
    assert route.calls[0].request.headers["Authorization"] == "Bearer alice-token"


@respx.mock
def test_cli_reports_failure(monkeypatch):
    monkeypatch.setenv("LITLENS_API_URL", "http://api.test")
    respx.get("http://api.test/ready").mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 1 and "503" in result.output
