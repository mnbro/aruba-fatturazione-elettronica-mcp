import aruba_fatturazione_elettronica_mcp.__main__ as module_main
from aruba_fatturazione_elettronica_mcp.cli import main


def test_cli_exports_main() -> None:
    assert callable(main)
    assert module_main.main is main
