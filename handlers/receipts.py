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
        promo_ended_msg = config_manager.get_message(
            'promo_ended',
            "🏁 Акция завершена {date}\n\nСпасибо за участие!"
        ).format(date=config.PROMO_END_DATE)
        await message.answer(
            promo_ended_msg,
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
    
    # Show tickets count instead of receipts
    tickets_count = user.get('total_tickets', user['valid_receipts'])
    
    upload_instruction = config_manager.get_message(
        'upload_instruction',
        "📸 Отправьте фото QR-кода с чека\n\nВаших билетов: {count}\n\n💡 QR-код должен быть чётким и полностью в кадре"
    ).format(count=tickets_count)
    
    await message.answer(
        upload_instruction,
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
    
    scanning_msg = config_manager.get_message('scanning', "⏳ Сканирую QR... (3 сек)")
    processing_msg = await message.answer(scanning_msg)
    
    photo = message.photo[-1]
    if photo.file_size and photo.file_size > 5 * 1024 * 1024:
        file_too_big_msg = config_manager.get_message('file_too_big', "❌ Файл слишком большой. Максимум 5MB.")
        await processing_msg.edit_text(file_too_big_msg)
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
        processing_error_msg = config_manager.get_message('processing_error', "⚠️ Ошибка обработки. Попробуйте ещё раз")
        await processing_msg.edit_text(processing_error_msg)
        await state.clear()
        return
    
    try:
        await processing_msg.delete()
    except Exception:
        pass
    
    if not result:
        check_failed_msg = config_manager.get_message('check_failed', "⚠️ Не удалось проверить чек. Попробуйте ещё раз.")
        await message.answer(check_failed_msg)
        await state.clear()
        return
    
    code = result.get("code")
    msg = result.get("message", "")
    logger.info(f"🧾 API Check Result: user={message.from_user.id} code={code} msg='{msg}'")
    
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
    elif code in (0, 3, 4, 5):
        # Code 0: Check incorrect (invalid QR)
        # Code 5: Other/Data not received
        # Code 3/4: Rate limit (User requested to treat this as "No QR found" since valid QRs work)
        scan_failed_msg = config_manager.get_message(
            'scan_failed',
            "🔍 Не удалось распознать чек\n\n• Сфотографируйте ближе\n• Улучшите освещение\n\n💡 Свежий чек? Подождите 5-10 минут"
        )
        await message.answer(
            scan_failed_msg,
            reply_markup=get_support_keyboard()
        )
    elif code == 2:
        fns_wait_msg = config_manager.get_message(
            'fns_wait',
            "🧾 Чек найден в ФНС, но данные еще не загрузились.\nПожалуйста, попробуйте отправить его повторно через час."
        )
        await message.answer(
            fns_wait_msg,
            reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
        )
    else:
        # Code -1 (Internal error) or unknown
        service_unavailable_msg = config_manager.get_message('service_unavailable', "⚠️ Сервис временно недоступен")
        await message.answer(service_unavailable_msg, reply_markup=get_support_keyboard())
        await state.clear()


async def _handle_valid_receipt(message: Message, state: FSMContext, result: dict, user_db_id: int):
    receipt_data = result.get("data", {}).get("json", {})
    items = receipt_data.get("items", [])
    
    # Get dynamic keywords
    target_keywords = get_target_keywords()
    
    # Check for target products and count total quantity (tickets)
    found_items = []
    total_tickets = 0
    
    for item in items:
        item_name = item.get("name", "")
        if any(kw in item_name.lower() for kw in target_keywords):
            # Get quantity - it can be float (e.g., 2.0) or int
            quantity = item.get("quantity", 1)
            try:
                quantity = int(float(quantity))  # Convert 2.0 -> 2
            except (TypeError, ValueError):
                quantity = 1
            
            # Ensure at least 1 ticket per item
            quantity = max(1, quantity)
            total_tickets += quantity
            
            found_items.append({
                "name": item_name,
                "quantity": quantity,
                "sum": item.get("sum")
            })
    
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
    
    # Save receipt with tickets count
    try:
        receipt_id = await add_receipt(
            user_id=user_db_id,
            status="valid",
            data={
                "dateTime": receipt_data.get("dateTime"),
                "totalSum": receipt_data.get("totalSum"),
                "promo_items": [{"name": i["name"], "quantity": i["quantity"], "sum": i["sum"]} 
                               for i in found_items][:10]
            },
            fiscal_drive_number=fn,
            fiscal_document_number=fd,
            fiscal_sign=fp,
            total_sum=receipt_data.get("totalSum", 0),
            raw_qr="photo_upload",
            product_name=found_items[0]["name"][:100] if found_items else None,
            tickets=total_tickets
        )
    except Exception as e:
        # Check for unique violation (asyncpg error)
        if "unique constraint" in str(e).lower():
            duplicate_msg = config_manager.get_message(
                'receipt_duplicate',
                "ℹ️ Этот чек уже загружен"
            )
            await message.answer(duplicate_msg, reply_markup=get_cancel_keyboard())
            return
        logger.error(f"Receipt save error: {e}")
        receipt_id = None

    if not receipt_id:
        receipt_save_error = config_manager.get_message('receipt_save_error', "Не удалось сохранить чек")
        await message.answer(receipt_save_error, reply_markup=get_cancel_keyboard())
        return
    
    await increment_rate_limit(message.from_user.id)
    
    # Get total tickets for user (not just receipts count)
    from database import get_user_tickets_count
    total_user_tickets = await get_user_tickets_count(user_db_id)
    
    # Show tickets info to user
    if total_user_tickets == total_tickets:  # First receipt
        first_msg = config_manager.get_message(
            'receipt_first',
            "🎉 Поздравляем с первым чеком!\n\nВы в розыгрыше! Загружайте ещё 🎯"
        )
        if total_tickets > 1:
            first_msg = f"🎉 Поздравляем! +{total_tickets} билетов!\n\nВсего билетов: {total_user_tickets} 🎯"
        await message.answer(first_msg, reply_markup=get_receipt_continue_keyboard())
    else:
        if total_tickets > 1:
            valid_msg = f"✅ Чек принят! +{total_tickets} билетов!\n\nВсего билетов: {total_user_tickets} 🎯"
        else:
            valid_msg = config_manager.get_message(
                'receipt_valid',
                "✅ Чек принят!\n\nВсего билетов: {count} 🎯"
            ).format(count=total_user_tickets)
        await message.answer(valid_msg, reply_markup=get_receipt_continue_keyboard())
    
    await state.set_state(ReceiptSubmission.upload_qr)


@router.message(ReceiptSubmission.upload_qr)
async def process_receipt_invalid_type(message: Message, state: FSMContext):
    if message.text in ("❌ Отмена", "🏠 В меню"):
        await state.clear()
        user = await get_user_with_stats(message.from_user.id)
        count = user.get('total_tickets', user['valid_receipts']) if user else 0
        
        cancel_msg = config_manager.get_message(
            'cancel_msg',
            "Выберите действие 👇\nВаших билетов: {count}"
        ).format(count=count)
        
        await message.answer(
            cancel_msg,
            reply_markup=get_main_keyboard(config.is_admin(message.from_user.id))
        )
        return
    
    upload_qr_prompt = config_manager.get_message('upload_qr_prompt', "📷 Отправьте фотографию QR-кода")
    await message.answer(upload_qr_prompt)
