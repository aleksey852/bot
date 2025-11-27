"""Receipt upload handler"""
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
import io
import logging

from utils.states import ReceiptSubmission
from utils.config_manager import config_manager
from keyboards import get_main_keyboard, get_cancel_keyboard, get_receipt_continue_keyboard, get_support_keyboard
from utils.api import check_receipt
from utils.rate_limiter import check_rate_limit, increment_rate_limit
from database import add_receipt, get_user_with_stats, is_receipt_exists, get_user_receipts_count, update_username
import config

logger = logging.getLogger(__name__)
router = Router()


def get_target_keywords():
    """Get keywords from config_manager or fallback to config.py"""
    keywords_str = config_manager.get_setting('TARGET_KEYWORDS', ','.join(config.TARGET_KEYWORDS))
    return [kw.strip().lower() for kw in keywords_str.split(',') if kw.strip()]


@router.message(F.text == "🧾 Загрузить чек")
@router.message(F.text == "🧾 Ещё чек")
async def start_receipt_upload(message: Message, state: FSMContext):
    if not config.is_promo_active():
        await message.answer(
            f"🏁 Акция завершена {config.PROMO_END_DATE}\n\nСпасибо за участие!",
            reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
        )
        return
    
    user = await get_user_with_stats(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    
    if message.from_user.username != user.get('username'):
        await update_username(message.from_user.id, message.from_user.username or "")
    
    allowed, limit_msg = await check_rate_limit(message.from_user.id)
    if not allowed:
        await message.answer(limit_msg, reply_markup=get_main_keyboard(config.is_admin(message.from_user.id)))
        return
    
    await state.update_data(user_db_id=user['id'])
    await message.answer(
        f"📸 Отправьте фото QR-кода с чека\n\nВаших чеков: {user['valid_receipts']}\n\n"
        "💡 QR-код должен быть чётким и полностью в кадре",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(ReceiptSubmission.upload_qr)


@router.message(ReceiptSubmission.upload_qr, F.photo)
async def process_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    allowed, limit_msg = await check_rate_limit(message.from_user.id)
    if not allowed:
        await message.answer(limit_msg, reply_markup=get_main_keyboard(config.is_admin(message.from_user.id)))
        await state.clear()
        return
    
    processing_msg = await message.answer("⏳ Сканирую QR... (3 сек)")
    
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > 5 * 1024 * 1024:
        await processing_msg.edit_text("❌ Файл слишком большой. Максимум 5MB.")
        await state.clear()
        return
    
    try:
        file_io = io.BytesIO()
        await bot.download(photo, destination=file_io)
        file_io.seek(0)
        result = await check_receipt(qr_file=file_io, user_id=message.from_user.id)
        file_io.close()
    except Exception as e:
        logger.error(f"Photo processing error: {e}")
        await processing_msg.edit_text("⚠️ Ошибка обработки. Попробуйте ещё раз")
        await state.clear()
        return
    
    try:
        await processing_msg.delete()
    except:
        pass
    
    if not result:
        await message.answer("⚠️ Не удалось проверить чек. Попробуйте ещё раз.")
        await state.clear()
        return
    
    code = result.get("code")
    data = await state.get_data()
    user_db_id = data.get("user_db_id")
    
    if not user_db_id:
        user = await get_user_with_stats(message.from_user.id)
        user_db_id = user['id'] if user else None
        if not user_db_id:
            await message.answer("Ошибка. Попробуйте /start")
            await state.clear()
            return
    
    if code == 1:
        await _handle_valid_receipt(message, state, result, user_db_id)
    elif code == 0:
        await message.answer(
            "🔍 Не удалось распознать чек\n\n• Сфотографируйте ближе\n• Улучшите освещение\n\n"
            "💡 Свежий чек? Подождите 5-10 минут",
            reply_markup=get_support_keyboard()
        )
    elif code == 2:
        await message.answer(
            "🧾 Чек найден в ФНС, но данные еще не загрузились.\n"
            "Пожалуйста, попробуйте отправить его повторно через час.",
            reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
        )
    elif code in (3, 4):
        await message.answer("⚠️ Слишком много запросов. Подождите", reply_markup=get_cancel_keyboard())
    else:
        await message.answer("⚠️ Сервис временно недоступен", reply_markup=get_support_keyboard())
        await state.clear()


async def _handle_valid_receipt(message: Message, state: FSMContext, result: dict, user_db_id: int):
    receipt_data = result.get("data", {}).get("json", {})
    items = receipt_data.get("items", [])
    
    # Get dynamic keywords
    target_keywords = get_target_keywords()
    
    # Check for target products
    found_items = [
        item.get("name", "Товар")
        for item in items
        if any(kw in item.get("name", "").lower() for kw in target_keywords)
    ]
    
    if not found_items:
        no_product_msg = config_manager.get_message(
            'receipt_no_product',
            "😔 В чеке нет акционных товаров"
        )
        await message.answer(no_product_msg, reply_markup=get_cancel_keyboard())
        return
    
    # Check duplicates
    fn = str(receipt_data.get("fiscalDriveNumber", ""))
    fd = str(receipt_data.get("fiscalDocumentNumber", ""))
    fp = str(receipt_data.get("fiscalSign", ""))
    
    if fn and fd and fp and await is_receipt_exists(fn, fd, fp):
        duplicate_msg = config_manager.get_message(
            'receipt_duplicate',
            "ℹ️ Этот чек уже загружен"
        )
        await message.answer(duplicate_msg, reply_markup=get_cancel_keyboard())
        return
    
    # Save receipt
    receipt_id = await add_receipt(
        user_id=user_db_id,
        status="valid",
        data={
            "dateTime": receipt_data.get("dateTime"),
            "totalSum": receipt_data.get("totalSum"),
            "promo_items": [{"name": item.get("name"), "sum": item.get("sum")} 
                           for item in items if any(kw in item.get("name", "").lower() for kw in target_keywords)][:10]
        },
        fiscal_drive_number=fn,
        fiscal_document_number=fd,
        fiscal_sign=fp,
        total_sum=receipt_data.get("totalSum", 0),
        raw_qr="photo_upload",
        product_name=found_items[0][:100] if found_items else None
    )
    
    if not receipt_id:
        await message.answer("Не удалось сохранить чек", reply_markup=get_cancel_keyboard())
        return
    
    await increment_rate_limit(message.from_user.id)
    total_valid = await get_user_receipts_count(user_db_id)
    
    if total_valid == 1:
        first_msg = config_manager.get_message(
            'receipt_first',
            "🎉 Поздравляем с первым чеком!\n\nВы в розыгрыше! Загружайте ещё 🎯"
        )
        await message.answer(first_msg, reply_markup=get_receipt_continue_keyboard())
    else:
        valid_msg = config_manager.get_message(
            'receipt_valid',
            "✅ Чек принят!\n\nВсего чеков: {count} 🎯"
        ).format(count=total_valid)
        await message.answer(valid_msg, reply_markup=get_receipt_continue_keyboard())
    
    await state.set_state(ReceiptSubmission.upload_qr)


@router.message(ReceiptSubmission.upload_qr)
async def process_receipt_invalid_type(message: Message, state: FSMContext):
    if message.text in ("❌ Отмена", "🏠 В меню"):
        await state.clear()
        user = await get_user_with_stats(message.from_user.id)
        count = user['valid_receipts'] if user else 0
        await message.answer(
            f"Выберите действие 👇\nВаших чеков: {count}",
            reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
        )
        return
    
    await message.answer("📷 Отправьте фотографию QR-кода")
