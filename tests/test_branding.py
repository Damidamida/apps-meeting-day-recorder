from app.branding import (
    APP_DISPLAY_NAME,
    APP_EXECUTABLE_NAME,
    APP_INSTALL_DIR_NAME,
    APP_PUBLISHER,
    WINDOWS_APP_ID,
)


def test_branding_uses_final_user_visible_name() -> None:
    assert APP_DISPLAY_NAME == "BK Scribe"
    assert APP_EXECUTABLE_NAME == "BK Scribe.exe"
    assert APP_INSTALL_DIR_NAME == "BK Scribe"
    assert APP_PUBLISHER == "BK"
    assert WINDOWS_APP_ID == "BK.BKScribe"
