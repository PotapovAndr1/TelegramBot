import logging
import html # Для экранирования вывода
from telebot import TeleBot, types
from telebot.storage import StateMemoryStorage
# from telebot.handler_backends import State, StatesGroup

# --- Попытка импорта и проверки DB ---
db = None # Определяем заранее
try:
    from db import db as imported_db # Импортируем под другим именем
    db = imported_db # Присваиваем глобальной переменной
    # Проверка после импорта
    if db is None or db.conn is None or db.conn.closed:
        raise ImportError("Database connection is not available after import.")
    logger_db_check = logging.getLogger(__name__)
    logger_db_check.info("Database object imported and connection verified successfully.")
except Exception as e:
    logging.basicConfig(level=logging.CRITICAL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    logger.critical(f"CRITICAL ERROR: Could not import or verify database connection. Bot cannot start. Error: {e}", exc_info=True)
    db = None # Явно ставим None, чтобы проверки ниже работали

# --- Импорт конфигурации ---
BOT_TOKEN = None
if db: # Только если db успешно импортирован
    try:
        from config import BOT_TOKEN
        if not BOT_TOKEN:
             raise ValueError("BOT_TOKEN is empty or not set in config.py")
    except (ImportError, ValueError, Exception) as config_e:
        logger.critical(f"CRITICAL ERROR: Failed to load BOT_TOKEN from config: {config_e}", exc_info=True)
        db = None # Считаем запуск невозможным
        BOT_TOKEN = None

# --- Настройка основного логгера ---
if not logging.getLogger().hasHandlers():
     logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
logger = logging.getLogger(__name__)

# --- Инициализация бота (только если все ОК) ---
bot = None
if db and BOT_TOKEN:
    logger.info("Initializing TeleBot...")
    try:
        state_storage = StateMemoryStorage()
        bot = TeleBot(BOT_TOKEN, state_storage=state_storage, parse_mode='HTML')
        logger.info("TeleBot initialized successfully.")
    except Exception as bot_init_err:
         logger.critical(f"CRITICAL ERROR: Failed to initialize TeleBot: {bot_init_err}", exc_info=True)
         bot = None # Считаем запуск невозможным
else:
    logger.critical("Bot initialization skipped due to previous critical errors (DB or Config).")


# --- Константы ---
BUTTON_NEXT_CARD = "Дальше ▶▶"
BUTTON_ADD_WORD = "Добавить слово +"
BUTTON_DELETE_WORD = "Удалить слово"

# --- Проверка доступности бота ---
def is_bot_available(message_or_call):
    """Проверяет, инициализирован ли бот и доступна ли БД."""
    is_call = isinstance(message_or_call, types.CallbackQuery)
    user_id = message_or_call.from_user.id

    if bot is None or db is None or db.conn is None or db.conn.closed:
        logger.error(f"Bot or DB unavailable. Request from user {user_id}")
        try:
            if bot: # Если объект бота еще существует
                if is_call:
                    bot.answer_callback_query(message_or_call.id, "😔 Бот временно недоступен", show_alert=True)
                else:
                    bot.reply_to(message_or_call, "😔 Бот временно недоступен из-за внутренней ошибки. Пожалуйста, попробуйте позже.")
        except Exception as send_err:
             logger.error(f"Could not even send unavailability message: {send_err}")
        return False
    return True

# --- Обработчики команд ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Обрабатывает команды /start и /help."""
    if not is_bot_available(message): return

    user_id = message.from_user.id
    chat_id = message.chat.id
    command = message.text.strip('/')
    logger.info(f"User {user_id} used /{command}")

    # Сброс состояния пользователя
    try:
       bot.set_state(user_id, state=None, chat_id=chat_id)
       with bot.retrieve_data(user_id, chat_id) as data: data.clear()
       logger.debug(f"Cleared state and data for user {user_id}")
    except Exception as e:
       logger.error(f"Error clearing state/data for user {user_id} in /{command}: {e}", exc_info=True)

    # Приветственное сообщение
    welcome_text = """
🎓 <b>Английский с ботом - легко!</b>

Привет! Я помогу тебе выучить новые английские слова.
Базовый набор слов уже доступен.

<b>Команды:</b>
/cards - Начать тренировку 🧠
/add_word - Добавить свое слово ➕
/delete_word - Удалить свое слово ➖
/my_words - Показать мои слова 📖
/stats - Моя статистика 📊
/input_mode - Выбрать режим ввода ответа ⌨️
/help - Показать это сообщение помощи ℹ️
"""
    try:
        # Убираем клавиатуру от предыдущих действий, если была
        bot.reply_to(message, welcome_text, reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
         logger.error(f"Failed to send welcome message to user {user_id}: {e}", exc_info=True)

@bot.message_handler(commands=['cards'])
def handle_cards(message):
    """Начинает сессию карточек, учитывая режим ввода."""
    if not is_bot_available(message): return

    user_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"User {user_id} requested /cards")

    try:
        bot.set_state(user_id, state=None, chat_id=chat_id)
        with bot.retrieve_data(user_id, chat_id) as data: data.clear()

        total_words = db.count_total_words(user_id)
        if total_words == 0:
             bot.send_message(chat_id, "📭 Ваш словарь пуст! Добавьте слова: /add_word", reply_markup=types.ReplyKeyboardRemove())
             return

        card = db.get_random_card(user_id)
        if card is None:
             bot.send_message(chat_id, "⚠️ Не удалось получить карточку. Попробуйте позже.", reply_markup=types.ReplyKeyboardRemove())
             logger.warning(f"get_random_card returned None for user {user_id}, total_words: {total_words}")
             return

        input_mode = db.get_user_input_mode(user_id)
        logger.debug(f"User {user_id} input mode: '{input_mode}' for card '{card['en_word']}'")

        card_data_to_store = {'correct_answer': card['en_word'], 'word_type': card['word_type'], 'word_ref_id': card['word_ref_id']}
        message_text = f"🇷🇺 {html.escape(card['ru_word'])}" # Текст вопроса общий

        reply_markup = None # По умолчанию клавиатуры нет
        if input_mode == 'buttons':
            message_text = f"🤔 <b>Выбери перевод слова:</b>\n{message_text}"
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
            options = card['options']
            option_buttons = [types.KeyboardButton(word) for word in options]
            rows = [option_buttons[i:i + 2] for i in range(0, len(option_buttons), 2)]
            for row in rows: markup.row(*row)
            markup.row(types.KeyboardButton(BUTTON_NEXT_CARD), types.KeyboardButton(BUTTON_ADD_WORD))
            markup.row(types.KeyboardButton(BUTTON_DELETE_WORD))
            reply_markup = markup

        elif input_mode == 'keyboard':
            message_text = f"📝 <b>Введите перевод слова:</b>\n{message_text}"
            reply_markup = types.ReplyKeyboardRemove() # Убираем предыдущую клавиатуру

        else: # Неизвестный режим - используем безопасный вариант
             logger.error(f"Unknown input mode '{input_mode}' for user {user_id}. Defaulting to keyboard.")
             message_text = f"📝 <b>Введите перевод слова (режим по умолчанию):</b>\n{message_text}"
             reply_markup = types.ReplyKeyboardRemove()

        bot.send_message(chat_id, message_text, reply_markup=reply_markup)

        with bot.retrieve_data(user_id, chat_id) as data:
            data.update(card_data_to_store)
        logger.debug(f"Stored card data for user {user_id}: {card_data_to_store}")

    except Exception as e:
        logger.error(f"Error in handle_cards for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "⚠️ Произошла ошибка при получении карточки. Попробуйте /cards еще раз.", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(commands=['input_mode'])
def handle_input_mode_command(message):
    """Показывает настройки режима ввода."""
    if not is_bot_available(message): return
    user_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"User {user_id} requested /input_mode")
    try:
        current_mode = db.get_user_input_mode(user_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        button_text_buttons = f"{'✅ ' if current_mode == 'buttons' else ''}Кнопки 🔘"
        button_text_keyboard = f"{'✅ ' if current_mode == 'keyboard' else ''}Клавиатура ⌨️"
        markup.add(
            types.InlineKeyboardButton(button_text_buttons, callback_data="set_mode_buttons"),
            types.InlineKeyboardButton(button_text_keyboard, callback_data="set_mode_keyboard")
        )
        bot.send_message(
            chat_id,
            "⚙️ <b>Выберите режим ввода ответа:</b>\n\n"
            "🔘 <b>Кнопки:</b> Показываются варианты ответа.\n"
            "⌨️ <b>Клавиатура:</b> Нужно ввести перевод самостоятельно.",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Error handling /input_mode for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "❌ Ошибка отображения настроек.")


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_mode_'))
def callback_set_input_mode(call):
    """Обрабатывает нажатие Inline кнопок для смены режима ввода."""
    if not is_bot_available(call): return # Проверяем доступность

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    requested_mode = call.data.replace('set_mode_', '') # 'buttons' или 'keyboard'
    logger.info(f"User {user_id} requested set mode to '{requested_mode}' via callback.")

    if requested_mode not in ('buttons', 'keyboard'):
        logger.warning(f"Invalid mode '{requested_mode}' in callback data from user {user_id}.")
        bot.answer_callback_query(call.id, "Ошибка: Неверный режим", show_alert=True)
        return

    try:
        success = db.set_user_input_mode(user_id, requested_mode)
        if success:
            new_markup = types.InlineKeyboardMarkup(row_width=1)
            button_text_buttons = f"{'✅ ' if requested_mode == 'buttons' else ''}Кнопки 🔘"
            button_text_keyboard = f"{'✅ ' if requested_mode == 'keyboard' else ''}Клавиатура ⌨️"
            confirmation_text = f"Выбран режим: <b>{'Кнопки 🔘' if requested_mode == 'buttons' else 'Клавиатура ⌨️'}</b>"
            new_markup.add(
                types.InlineKeyboardButton(button_text_buttons, callback_data="set_mode_buttons"),
                types.InlineKeyboardButton(button_text_keyboard, callback_data="set_mode_keyboard")
            )
            bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f"⚙️ <b>Режим ввода обновлен.</b>\n\n{confirmation_text}\n\n"
                     "🔘 <b>Кнопки:</b> Показываются варианты ответа.\n"
                     "⌨️ <b>Клавиатура:</b> Нужно ввести перевод самостоятельно.",
                reply_markup=new_markup
            )
            bot.answer_callback_query(call.id) # Убираем часики
            logger.info(f"Mode updated to '{requested_mode}' for user {user_id}. Message edited.")
        else:
            bot.answer_callback_query(call.id, "Ошибка сохранения настройки!", show_alert=True)
            logger.error(f"Failed to set mode '{requested_mode}' in DB for user {user_id}.")
    except Exception as e:
        logger.error(f"Error processing set_mode callback for user {user_id}: {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Произошла ошибка!", show_alert=True)
        except Exception as ans_err: logger.error(f"Could not answer callback query about error: {ans_err}")


@bot.message_handler(content_types=['text'], func=lambda message: bot is not None and not message.text.startswith('/'))
def handle_non_command_text(message):
    """Обрабатывает текстовые сообщения: ответы, кнопки, ввод для add/delete."""
    if not is_bot_available(message): return

    user_id = message.from_user.id
    chat_id = message.chat.id
    user_text = message.text.strip()
    logger.debug(f"Received text '{user_text}' from user {user_id}")

    # 1. Проверка кнопок ReplyKeyboard
    if user_text == BUTTON_NEXT_CARD:
        logger.info(f"User {user_id} clicked '{BUTTON_NEXT_CARD}'.")
        handle_cards(message)
        return
    if user_text == BUTTON_ADD_WORD:
        logger.info(f"User {user_id} clicked '{BUTTON_ADD_WORD}'.")
        start_adding_word_process(message)
        return
    if user_text == BUTTON_DELETE_WORD:
        logger.info(f"User {user_id} clicked '{BUTTON_DELETE_WORD}'.")
        start_deleting_word_process(message)
        return

    # 2. Проверка состояний ожидания ввода (add/delete)
    try:
        with bot.retrieve_data(user_id, chat_id) as data:
            if data is None: logger.warning(f"Data is None for user {user_id}"); return

            current_step = data.get('next_step')
            if current_step == 'add_word':
                logger.info(f"Processing input for 'add_word'.")
                data.pop('next_step', None); bot.set_state(user_id, state=None, chat_id=chat_id)
                process_word_addition(message)
                return
            elif current_step == 'delete_word':
                logger.info(f"Processing input for 'delete_word'.")
                data.pop('next_step', None); bot.set_state(user_id, state=None, chat_id=chat_id)
                process_word_deletion(message)
                return

            # 3. Проверка ответа на карточку
            correct_answer = data.get('correct_answer')
            word_type = data.get('word_type')
            word_ref_id = data.get('word_ref_id')

            if correct_answer is None or word_type is None or word_ref_id is None:
                 logger.debug(f"Received text '{user_text}' from user {user_id}, but no active card data or expected step.")
                 # Можно отправить "Используйте /cards или /help"
                 return

            # --- Обработка ответа на карточку ---
            is_correct = user_text.lower() == correct_answer.lower()
            logger.info(f"User {user_id} answered card. Input: '{user_text}', Correct: '{correct_answer}', Result: {is_correct}")

            # Формируем ответ
            input_mode = db.get_user_input_mode(user_id) # Получаем режим для подсказки
            next_action_prompt = f"Нажмите '{BUTTON_NEXT_CARD}'" if input_mode == 'buttons' else "Используйте /cards"

            if is_correct:
                reply = f"✅ <b>Верно!</b>\n\n{next_action_prompt}, чтобы продолжить."
            else:
                reply = f"❌ <b>Неверно!</b> Правильно: <b>{html.escape(correct_answer)}</b>\n\n{next_action_prompt}, чтобы продолжить."

            # Отправляем результат (клавиатура зависит от режима, не меняем ее здесь)
            bot.send_message(chat_id, reply)

            # Записываем результат в БД
            db.record_answer(user_id, word_type, word_ref_id, is_correct)

            # Очищаем данные *только* этой карточки из data
            data.pop('correct_answer', None)
            data.pop('word_type', None)
            data.pop('word_ref_id', None)
            logger.debug(f"Cleared card data for user {user_id} after recording answer.")

    except Exception as e:
        logger.error(f"Error processing non-command text for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "⚠️ Произошла ошибка при обработке вашего ответа.")


# --- Функции процессов добавления/удаления ---

def start_adding_word_process(message):
    """Начинает процесс добавления слова."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id
    logger.info(f"Starting add word process for user {user_id}")
    try:
       bot.set_state(user_id, state=None, chat_id=chat_id)
       with bot.retrieve_data(user_id, chat_id) as data:
           data.clear(); data['next_step'] = 'add_word'
       logger.debug(f"Set 'next_step' to 'add_word' for user {user_id}")
       bot.send_message(chat_id, "📝 Введите: <code>англ. слово - рус. перевод</code>\n(Пример: <code>example - пример</code>)", reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
       logger.error(f"Error starting add word process for user {user_id}: {e}", exc_info=True)
       bot.send_message(chat_id, "❌ Ошибка. Попробуйте /add_word снова.")

def process_word_addition(message):
    """Обрабатывает ввод пользователя для добавления слова."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id; text = message.text.strip()
    try:
        if '-' not in text: raise ValueError("Отсутствует разделитель '-'")
        parts = text.split('-', 1); en_word = parts[0].strip(); ru_word = parts[1].strip()
        if not en_word or not ru_word: raise ValueError("Английское или русское слово пустое")

        added = db.add_user_word(user_id, en_word, ru_word)
        en_safe, ru_safe = html.escape(en_word), html.escape(ru_word)
        reply = f"✅ Добавлено: <code>{en_safe} - {ru_safe}</code>!" if added else f"⚠️ Слово <code>{en_safe}</code> уже есть."
        bot.send_message(chat_id, reply)
        bot.send_message(chat_id, "Что дальше? /cards, /add_word, /my_words")
    except ValueError as ve:
        logger.warning(f"User {user_id} add format error: '{text}' - {ve}")
        bot.send_message(chat_id, f"❌ <b>Ошибка формата:</b> {ve}.\nНужно: <code>слово - перевод</code>\nПопробуйте /add_word снова.")
    except Exception as e:
        logger.error(f"Error processing word addition for user {user_id}, input '{text}': {e}", exc_info=True)
        bot.send_message(chat_id, "❌ Ошибка добавления слова. Попробуйте позже.")

def start_deleting_word_process(message):
    """Начинает процесс удаления слова."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id
    logger.info(f"Starting delete word process for user {user_id}")
    try:
       bot.set_state(user_id, state=None, chat_id=chat_id)
       with bot.retrieve_data(user_id, chat_id) as data:
           data.clear(); data['next_step'] = 'delete_word'
       logger.debug(f"Set 'next_step' to 'delete_word' for user {user_id}")
       bot.send_message(chat_id, "🗑️ Введите английское слово для удаления:", reply_markup=types.ReplyKeyboardRemove())
    except Exception as e:
       logger.error(f"Error starting delete word process for user {user_id}: {e}", exc_info=True)
       bot.send_message(chat_id, "❌ Ошибка. Попробуйте /delete_word снова.")

def process_word_deletion(message):
    """Обрабатывает ввод пользователя для удаления слова."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id; en_word = message.text.strip()
    if not en_word:
        bot.send_message(chat_id, "❌ Не введено слово. Попробуйте /delete_word снова."); return
    try:
        deleted = db.delete_user_word(user_id, en_word)
        en_safe = html.escape(en_word)
        reply = f"✅ Слово <code>{en_safe}</code> удалено." if deleted else f"⚠️ Слово <code>{en_safe}</code> не найдено в вашем словаре."
        bot.send_message(chat_id, reply)
        bot.send_message(chat_id, "Что дальше? /cards, /delete_word, /my_words")
    except Exception as e:
        logger.error(f"Error processing word deletion for user {user_id}, input '{en_word}': {e}", exc_info=True)
        bot.send_message(chat_id, f"❌ Ошибка при удалении слова <code>{html.escape(en_word)}</code>.")


# --- Обработчики команд add/delete (вызывают start_..._process) ---
@bot.message_handler(commands=['add_word'])
def handle_add_word_command(message):
    if not is_bot_available(message): return
    start_adding_word_process(message)

@bot.message_handler(commands=['delete_word'])
def handle_delete_word_command(message):
    if not is_bot_available(message): return
    start_deleting_word_process(message)

# --- Обработчики /my_words и /stats ---
@bot.message_handler(commands=['my_words'])
def show_my_words(message):
    """Показывает слова, добавленные пользователем."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id
    logger.info(f"User {user_id} requested /my_words")
    try:
        user_words = db.get_user_words(user_id)
        if not user_words:
            bot.send_message(chat_id, "📖 У вас нет добавленных слов. Используйте /add_word."); return
        response_lines = ["📖 <b>Ваши слова:</b>\n"]
        for i, (en, ru) in enumerate(user_words, 1):
             response_lines.append(f"{i}. <code>{html.escape(en)}</code> - {html.escape(ru)}")
        full_response = "\n".join(response_lines)
        # TODO: Добавить пагинацию для > 4096 символов
        if len(full_response) > 4096:
            safe_limit = full_response.rfind('\n', 0, 4050); safe_limit = 4050 if safe_limit == -1 else safe_limit
            full_response = full_response[:safe_limit] + "\n\n[... список слишком длинный ...]"
        bot.send_message(chat_id, full_response)
    except Exception as e:
        logger.error(f"Error showing user words for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "❌ Ошибка получения списка слов.")

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Показывает статистику пользователя."""
    if not is_bot_available(message): return
    user_id = message.from_user.id; chat_id = message.chat.id
    logger.info(f"User {user_id} requested /stats")
    try:
        stats = db.get_user_stats(user_id)
        user_words_count = len(db.get_user_words(user_id))
        available_words_count = db.count_total_words(user_id)
        if stats['words_practiced'] == 0 and stats['total_correct'] == 0 and stats['total_incorrect'] == 0:
             response = (f"📊 <b>Статистика:</b>\n\n"
                         f"Вы еще не отвечали!\n"
                         f"📖 Добавлено вами: {user_words_count}\n"
                         f"📚 Всего доступно: {available_words_count}\n\n"
                         f"Начните: /cards")
        else:
            total_answers = stats['total_correct'] + stats['total_incorrect']
            accuracy = (stats['total_correct'] / total_answers * 100) if total_answers > 0 else 0
            response = f"""
📊 <b>Статистика:</b>
🧠 Слов с ответами: {stats['words_practiced']}
✅ Всего правильных: {stats['total_correct']}
❌ Всего неправильных: {stats['total_incorrect']}
🎯 Точность: {accuracy:.1f}%

📖 Добавлено вами: {user_words_count}
📚 Всего доступно: {available_words_count}
"""
        bot.send_message(chat_id, response)
    except Exception as e:
        logger.error(f"Error showing stats for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "❌ Ошибка получения статистики.")

# --- Запуск бота ---
if __name__ == "__main__":
    if bot is None:
        logger.critical("Bot object is None. Cannot start polling. Check logs for DB or Config errors.")
    else:
        logger.info("Starting bot polling...")
        try:
            # Добавляем обработку KeyboardInterrupt для корректной остановки Ctrl+C
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except KeyboardInterrupt:
             logger.info("Bot polling stopped manually via KeyboardInterrupt.")
        except Exception as e:
            logger.critical(f"Bot polling stopped due to unexpected error: {e}", exc_info=True)
        finally:
            # Корректное закрытие соединения с БД при остановке
            if db and db.conn and not db.conn.closed:
                logger.info("Closing database connection...")
                db.close()
            logger.info("Bot stopped.")