"""FSM States for bot flows"""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    name = State()
    phone = State()


class ReceiptSubmission(StatesGroup):
    upload_qr = State()


class AdminBroadcast(StatesGroup):
    content = State()
    preview = State()
    schedule = State()


class AdminRaffle(StatesGroup):
    prize_name = State()
    winner_count = State()
    winner_message = State()
    loser_message = State()
    schedule = State()
    confirm = State()


class AdminManualReceipt(StatesGroup):
    user_id = State()
    confirm = State()
