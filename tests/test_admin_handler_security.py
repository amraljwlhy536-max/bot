from pathlib import Path


def test_admin_handler_checks_actor_authorization_before_admin_actions():
    source = Path(__file__).resolve().parents[1].joinpath("app", "handlers", "admin.py").read_text(encoding="utf-8")
    callback_start = source.index('    @router.callback_query(F.data.startswith("admin:"))')
    callback_end = source.index('    @router.message(AdminStates.search_user)', callback_start)
    callback_source = source[callback_start:callback_end]
    assert 'if not await authorized(callback):' in callback_source
    assert 'service.admin_ids' in source
    assert 'if not await authorized(message):' in source


def test_admin_actions_do_not_authorize_from_callback_data():
    source = Path(__file__).resolve().parents[1].joinpath("app", "handlers", "admin.py").read_text(encoding="utf-8")
    assert 'callback.data' in source
    assert 'if not await authorized(callback):' in source
    assert 'service.ban_user(callback.from_user.id' in source
    assert 'service.unban_user(callback.from_user.id' in source
