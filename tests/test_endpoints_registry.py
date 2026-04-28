from aruba_fatturazione_elettronica_mcp.endpoints import ENDPOINTS


def test_all_registry_entries_are_get_only() -> None:
    assert ENDPOINTS
    assert {endpoint.method for endpoint in ENDPOINTS} == {"GET"}


def test_expected_endpoint_count_and_paths() -> None:
    assert len(ENDPOINTS) == 23
    paths = {endpoint.path_template for endpoint in ENDPOINTS}
    assert "/services/invoice/in/pdd" in paths
    assert "/services/invoice/in/sendEsitoCommittente/{filename}" in paths


def test_no_mutating_business_tool_names() -> None:
    forbidden = (
        "upload",
        "create",
        "update",
        "delete",
        "patch",
        "post",
        "remove",
        "archive",
        "pdd_upload",
    )
    for endpoint in ENDPOINTS:
        assert not any(word in endpoint.tool_name for word in forbidden)
