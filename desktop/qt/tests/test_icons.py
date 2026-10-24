"""回归: 桌面端用到的 qfluentwidgets 图标名必须真实存在

起因 (3.6.0 事故): SystemPage 里写了 `FIF.SHIELD`, 而 PyQt-Fluent-Widgets 1.11.3 没有这个图标,
`Enum.__getattr__` 抛 AttributeError, 直接让 MainWindow.__init__ 崩掉 —— 用户双击图标毫无反应,
只在 error.log 里留下一段 traceback。

本测试不依赖 PyQt: 直接扫源码里的 ``FIF.XXX`` 并与打包用的 1.11.3 版本枚举表比对。
升级 qfluentwidgets 时如果删了某个图标, 这里会第一时间失败。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "fla_qt")

# qfluentwidgets 1.11.3 (desktop/qt/build_bundle.sh 里钉的版本) 的 FluentIcon 成员
FLUENT_ICON_MEMBERS = {
    "ACCEPT", "ACCEPT_MEDIUM", "ADD", "ADD_TO", "AIRPLANE", "ALBUM", "ALIGNMENT", "APPLICATION",
    "ARROW_DOWN", "ASTERISK", "BACKGROUND_FILL", "BACK_TO_WINDOW", "BASKETBALL", "BLUETOOTH",
    "BOOK_SHELF", "BRIGHTNESS", "BROOM", "BRUSH", "BUS", "CAFE", "CALENDAR", "CALORIES",
    "CAMERA", "CANCEL", "CANCEL_MEDIUM", "CAR", "CARE_DOWN_SOLID", "CARE_LEFT_SOLID",
    "CARE_RIGHT_SOLID", "CARE_UP_SOLID", "CERTIFICATE", "CHAT", "CHECKBOX", "CHEVRON_DOWN_MED",
    "CHEVRON_RIGHT", "CHEVRON_RIGHT_MED", "CLEAR_SELECTION", "CLIPPING_TOOL", "CLOSE", "CLOUD",
    "CLOUD_DOWNLOAD", "CODE", "COMMAND_PROMPT", "COMPLETED", "CONNECT", "CONSTRACT", "COPY",
    "CUT", "DATE_TIME", "DELETE", "DEVELOPER_TOOLS", "DICTIONARY", "DICTIONARY_ADD", "DOCUMENT",
    "DOWN", "DOWNLOAD", "EDIT", "EDUCATION", "EMBED", "EMOJI_TAB_SYMBOLS", "ERASE_TOOL",
    "EXPRESSIVE_INPUT_ENTRY", "FEEDBACK", "FILTER", "FINGERPRINT", "FIT_PAGE", "FLAG", "FOLDER",
    "FOLDER_ADD", "FONT", "FONT_INCREASE", "FONT_SIZE", "FRIGID", "FULL_SCREEN", "GAME",
    "GITHUB", "GLOBE", "HEADPHONE", "HEART", "HELP", "HIDE", "HIGHTLIGHT", "HISTORY", "HOME",
    "HOME_FILL", "IMAGE_EXPORT", "INFO", "IOT", "LABEL", "LANGUAGE", "LAYOUT", "LEAF",
    "LEFT_ARROW", "LIBRARY", "LIBRARY_FILL", "LINK", "MAIL", "MARKET", "MEDIA", "MEGAPHONE",
    "MENU", "MESSAGE", "MICROPHONE", "MINIMIZE", "MIX_VOLUMES", "MORE", "MOVE", "MOVIE",
    "MUSIC", "MUSIC_FOLDER", "MUTE", "PAGE_LEFT", "PAGE_RIGHT", "PALETTE", "PASTE", "PAUSE",
    "PAUSE_BOLD", "PENCIL_INK", "PEOPLE", "PHONE", "PHOTO", "PIE_SINGLE", "PIN", "PLAY",
    "PLAY_SOLID", "POWER_BUTTON", "PRINT", "PROJECTOR", "QRCODE", "QUESTION", "QUICK_NOTE",
    "QUIET_HOURS", "REMOVE", "REMOVE_FROM", "RETURN", "RIGHT_ARROW", "RINGER", "ROBOT",
    "ROTATE", "SAVE", "SAVE_AS", "SAVE_COPY", "SCROLL", "SEARCH", "SEARCH_MIRROR", "SEND",
    "SEND_FILL", "SETTING", "SHARE", "SHOPPING_CART", "SKIP_BACK", "SKIP_FORWARD", "SPEAKERS",
    "SPEED_HIGH", "SPEED_MEDIUM", "SPEED_OFF", "STOP_WATCH", "SYNC", "TAG", "TILES", "TRAIN",
    "TRANSPARENT", "UNIT", "UNPIN", "UP", "UPDATE", "VIDEO", "VIEW", "VOLUME", "VPN", "WIFI",
    "ZIP_FOLDER", "ZOOM", "ZOOM_IN", "ZOOM_OUT"
}


def _used_icons():
    used = {}
    for fn in sorted(os.listdir(PKG)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(PKG, fn)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for m in re.finditer(r"FIF\.([A-Z0-9_]+)", text):
            used.setdefault(m.group(1), set()).add(fn)
    return used


def test_all_used_fluent_icons_exist():
    used = _used_icons()
    assert used, "没有扫到任何 FIF. 用法, 检查路径"
    missing = {n: sorted(fs) for n, fs in used.items() if n not in FLUENT_ICON_MEMBERS}
    assert not missing, (
        "以下图标在 qfluentwidgets 1.11.3 里不存在, 运行时会 AttributeError 崩溃: %s" % missing
    )


def test_icon_allowlist_is_not_empty_and_unique():
    assert len(FLUENT_ICON_MEMBERS) > 100
    assert "SHIELD" not in FLUENT_ICON_MEMBERS      # 曾经踩过的坑
