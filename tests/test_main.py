import app.main as main_module


def test_fastapi_application_initialized():
    assert main_module.api is not None
    assert main_module.bot is not None
    assert main_module.dp is not None
