"""Keyboard builders - simplified"""
from aiogram.types import KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
import config


def _reply(*buttons, cols=2) -> ReplyKeyboardBuilder:
    b = ReplyKeyboardBuilder()
    for text in buttons:
        if isinstance(text, KeyboardButton):
            b.add(text)
        else:
            b.add(KeyboardButton(text=text))
    b.adjust(cols)
    return b.as_markup(resize_keyboard=True)


def get_start_keyboard():
    return _reply("🚀 Начать", cols=1)


def get_contact_keyboard():
    return _reply(
        KeyboardButton(text="📱 Отправить номер", request_contact=True),
        "❌ Отмена", cols=1
    )


def get_cancel_keyboard():
    return _reply("❌ Отмена", cols=1)


def get_confirm_keyboard():
    return _reply("✅ Подтвердить", "❌ Отмена")


def get_schedule_keyboard():
    return _reply("🚀 Сейчас", "❌ Отмена")


def get_receipt_continue_keyboard():
    return _reply("🧾 Ещё чек", "🏠 В меню")


def get_main_keyboard(is_admin: bool = False):
    buttons = [
        "🧾 Загрузить чек", "👤 Мой профиль",
        "📋 Мои чеки", "ℹ️ FAQ", "🆘 Поддержка"
    ]
    if is_admin:
        buttons.extend([
            "📊 Статистика", "📢 Рассылка", "🎁 Розыгрыш",
            "🏆 Победители", "📥 Экспорт победителей", "➕ Ручное добавление"
        ])
    return _reply(*buttons)


# Inline keyboards
def get_support_keyboard():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(
        text="🆘 Написать в поддержку",
        url=f"https://t.me/{config.SUPPORT_TELEGRAM.replace('@', '')}"
    ))
    return b.as_markup()


def get_faq_keyboard():
    b = InlineKeyboardBuilder()
    items = [
        ("🎯 Как участвовать?", "faq_how"),
        ("🧾 Сколько чеков?", "faq_limit"),
        ("🏆 Как узнать о выигрыше?", "faq_win"),
        ("❌ Чек не принят?", "faq_reject"),
        ("📅 Сроки акции", "faq_dates"),
        ("🎁 Какие призы?", "faq_prizes"),
    ]
    for text, data in items:
        b.add(InlineKeyboardButton(text=text, callback_data=data))
    b.adjust(2)
    return b.as_markup()


def get_faq_back_keyboard():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="◀️ Назад", callback_data="faq_back"))
    return b.as_markup()


def get_admin_broadcast_preview_keyboard():
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_send"))
    b.add(InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast_edit"))
    b.add(InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel"))
    b.adjust(2)
    return b.as_markup()


def get_receipts_pagination_keyboard(page: int, total_pages: int):
    b = InlineKeyboardBuilder()
    if page > 1:
        b.add(InlineKeyboardButton(text="◀️", callback_data=f"receipts_page_{page-1}"))
    b.add(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="receipts_current"))
    if page < total_pages:
        b.add(InlineKeyboardButton(text="▶️", callback_data=f"receipts_page_{page+1}"))
    b.adjust(3)
    return b.as_markup()
