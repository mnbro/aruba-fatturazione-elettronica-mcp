from aruba_fatturazione_elettronica_mcp.annotations import READ_ONLY_TOOL
from aruba_fatturazione_elettronica_mcp.endpoints import ENDPOINTS
from aruba_fatturazione_elettronica_mcp.server import REGISTERED_BUSINESS_TOOLS


def test_every_business_tool_registered() -> None:
    assert set(REGISTERED_BUSINESS_TOOLS) == {endpoint.tool_name for endpoint in ENDPOINTS}


def test_read_only_annotation_constant() -> None:
    assert READ_ONLY_TOOL.readOnlyHint is True
    assert READ_ONLY_TOOL.destructiveHint is False
    assert READ_ONLY_TOOL.idempotentHint is True
    assert READ_ONLY_TOOL.openWorldHint is True
