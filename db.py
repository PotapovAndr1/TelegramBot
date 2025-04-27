import psycopg2
import psycopg2.errors
import random
import logging
from datetime import datetime, timezone # Импортируем datetime и timezone для TIMESTAMPTZ
from config import DB_CONFIG

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = None
        self.connect()
        if self.conn:
            try:
                # Инициализируем все таблицы (включая user_preferences)
                self.init_tables()
                logger.info("Table initialization successful.")

                # Загружаем стандартные слова
                logger.info("Attempting to load/verify initial words...")
                self.load_initial_words()

                logger.info("Database initialization and initial word loading process completed.")

            except Exception as e:
                 logger.critical(f"Database initialization process failed: {e}", exc_info=True)
                 try:
                    if self.conn and not self.conn.closed:
                        self.conn.close()
                 except Exception as close_e:
                    logger.error(f"Error closing connection after initialization failure: {close_e}", exc_info=True)
                 raise

        else:
            logger.critical("Failed to connect to database on startup. Bot will not be functional.")
            raise ConnectionError("Database connection failed.")

    def connect(self):
        """Устанавливает соединение с базой данных"""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            self.conn.autocommit = False # Управляем транзакциями явно
            logger.info("Successfully connected to database")
        except Exception as e:
            logger.error(f"Connection error: {e}", exc_info=True)
            self.conn = None

    def init_tables(self):
        """Инициализирует таблицы: common_words, user_words, user_word_progress, user_preferences."""
        if self.conn is None:
            logger.error("Cannot initialize tables: Database connection is not established.")
            raise ConnectionError("Database connection is not established.")

        # Команды создания ЧЕТЫРЕХ таблиц
        create_table_commands = [
            """
            CREATE TABLE IF NOT EXISTS common_words (
                id SERIAL PRIMARY KEY,
                en_word VARCHAR(100) UNIQUE NOT NULL,
                ru_word VARCHAR(100) NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS user_words (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                en_word VARCHAR(100) NOT NULL,
                ru_word VARCHAR(100) NOT NULL
                -- constraint uq_user_word будет добавлен ниже
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS user_word_progress (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                word_type VARCHAR(10) NOT NULL CHECK (word_type IN ('common', 'user')),
                word_ref_id BIGINT NOT NULL,
                correct_count INT DEFAULT 0 NOT NULL CHECK (correct_count >= 0),
                incorrect_count INT DEFAULT 0 NOT NULL CHECK (incorrect_count >= 0),
                last_tested TIMESTAMPTZ,
                UNIQUE (user_id, word_type, word_ref_id)
            );
            """,
            # --- ТАБЛИЦА НАСТРОЕК ---
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id BIGINT PRIMARY KEY,
                input_mode VARCHAR(10) DEFAULT 'buttons' NOT NULL CHECK (input_mode IN ('buttons', 'keyboard'))
                -- Можно добавить другие настройки позже
            );
            """
        ]
        logger.info("Attempting to create/verify tables (common_words, user_words, user_word_progress, user_preferences)...")
        try:
            with self.conn.cursor() as cur:
                for command in create_table_commands:
                    if command and command.strip(): # Пропускаем пустые команды
                        cur.execute(command)
            self.conn.commit()
            logger.info("Tables creation commands committed successfully.")
        except Exception as e:
            self.conn.rollback()
            logger.error(f"FATAL: Error creating tables: {e}", exc_info=True)
            raise

        # Добавление UNIQUE constraint для user_words
        add_unique_constraint_command = """
        ALTER TABLE user_words ADD CONSTRAINT uq_user_word UNIQUE (user_id, en_word);
        """
        logger.info("Attempting to add unique constraint 'uq_user_word' to user_words...")
        try:
            with self.conn.cursor() as cur: cur.execute(add_unique_constraint_command)
            self.conn.commit()
            logger.info("Unique constraint 'uq_user_word' added successfully.")
        except (psycopg2.errors.DuplicateTable, psycopg2.errors.DuplicateObject) as e:
            logger.info(f"Unique constraint 'uq_user_word' already exists ({type(e).__name__}). No action needed.")
            self.conn.rollback()
        except Exception as e:
            self.conn.rollback()
            logger.error(f"FATAL: Unexpected error adding constraint 'uq_user_word': {e}", exc_info=True)
            raise

        # Создание индексов
        create_index_commands = [
            "CREATE INDEX IF NOT EXISTS idx_user_words_user ON user_words(user_id);",
            "CREATE INDEX IF NOT EXISTS idx_progress_user_word ON user_word_progress(user_id, word_type, word_ref_id);",
            "CREATE INDEX IF NOT EXISTS idx_preferences_user ON user_preferences(user_id);" # Для user_preferences
        ]
        logger.info("Attempting to create/verify indexes...")
        try:
            with self.conn.cursor() as cur:
                for command in create_index_commands:
                    if command and command.strip():
                        cur.execute(command)
            self.conn.commit()
            logger.info("Indexes ensured and committed successfully.")
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Warning: Error creating indexes: {e}", exc_info=True)

        logger.info("Database schema initialization completed successfully.")

    def load_initial_words(self):
        """Загружает стартовый набор слов (или догружает недостающие)"""
        if self.conn is None:
            logger.error("Cannot load initial words: DB connection not established.")
            return False
        default_words = [
            ('apple', 'яблоко'), ('book', 'книга'), ('cat', 'кот'), ('dog', 'собака'),
            ('house', 'дом'), ('sun', 'солнце'), ('tree', 'дерево'), ('water', 'вода'),
            ('hello', 'привет'), ('goodbye', 'пока')
        ]
        logger.info(f"Attempting to load/verify {len(default_words)} default words into common_words...")
        try:
            with self.conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO common_words (en_word, ru_word)
                    VALUES (%s, %s)
                    ON CONFLICT (en_word) DO NOTHING
                """, default_words)
                inserted_count = cur.rowcount
                self.conn.commit()
            if inserted_count > 0:
                logger.info(f"Loaded {inserted_count} new default words.")
            else:
                logger.info("All default words previously existed in common_words.")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error loading default words: {e}", exc_info=True)
            return False

    def get_random_card(self, user_id):
        """
        Генерирует карточку для обучения с использованием взвешенного выбора.
        Приоритет: Новые слова > Слова с ошибками > Правильно отвеченные слова.
        Возвращает en_word, ru_word, options, word_type, word_ref_id.
        """
        if self.conn is None:
            logger.error("Cannot get random card: DB connection not established.")
            return None
        try:
            with self.conn.cursor() as cur:
                # 1. Получаем ВСЕ доступные слова пользователя ВМЕСТЕ с их прогрессом
                sql = """
                    SELECT
                        w.en_word, w.ru_word, w.word_type, w.word_ref_id,
                        p.correct_count, p.incorrect_count
                    FROM (
                        SELECT en_word, ru_word, 'common' AS word_type, id AS word_ref_id FROM common_words
                        UNION
                        SELECT en_word, ru_word, 'user' AS word_type, id AS word_ref_id FROM user_words WHERE user_id = %(user_id)s
                    ) AS w
                    LEFT JOIN user_word_progress p ON w.word_ref_id = p.word_ref_id
                                                   AND w.word_type = p.word_type
                                                   AND p.user_id = %(user_id)s
                """
                cur.execute(sql, {'user_id': user_id})
                words_data_with_progress = cur.fetchall()

                if not words_data_with_progress:
                    logger.info(f"No words found for user {user_id} to select a card from.")
                    return None

                # 2. Рассчитываем веса
                population = []
                weights = []
                for row in words_data_with_progress:
                    en_word, ru_word, word_type, word_ref_id, correct_c, incorrect_c = row
                    word_info = {'en_word': en_word, 'ru_word': ru_word, 'word_type': word_type, 'word_ref_id': word_ref_id}
                    population.append(word_info)
                    correct_count = correct_c if correct_c is not None else 0
                    incorrect_count = incorrect_c if incorrect_c is not None else 0
                    weight = 1
                    if correct_count == 0 and incorrect_count == 0: weight = 10
                    elif incorrect_count > 0: weight = 5 + incorrect_count * 2 - correct_count
                    elif correct_count > 0: weight = max(1, 5 - correct_count)
                    final_weight = max(1, weight)
                    weights.append(final_weight)

                if not population:
                     logger.error(f"Word population is empty after processing for user {user_id}")
                     return None
                if len(population) != len(weights):
                     logger.error(f"Mismatch population/weights count for user {user_id}. Fallback to random.")
                     selected_word_info = random.choice(population)
                else:
                    # 3. Выполняем взвешенный случайный выбор
                    try:
                         logger.debug(f"Weights for user {user_id}: {list(zip([p['en_word'] for p in population], weights))}")
                         selected_word_info = random.choices(population=population, weights=weights, k=1)[0]
                    except ValueError as ve:
                         logger.error(f"Error during weighted choice for user {user_id}: {ve}. Fallback to random.")
                         selected_word_info = random.choice(population)

                # 4. Формируем варианты ответов
                target_en_word = selected_word_info['en_word']
                all_available_en_words = [row[0] for row in words_data_with_progress]
                other_en_words = [w for w in all_available_en_words if w != target_en_word]
                num_options = min(3, len(other_en_words))
                options = random.sample(other_en_words, num_options) + [target_en_word]
                random.shuffle(options)

                # 5. Возвращаем результат
                return {
                    'en_word': selected_word_info['en_word'],
                    'ru_word': selected_word_info['ru_word'],
                    'options': options,
                    'word_type': selected_word_info['word_type'],
                    'word_ref_id': selected_word_info['word_ref_id']
                }
        except psycopg2.Error as db_err:
             logger.error(f"Database error getting weighted card for user {user_id}: {db_err}", exc_info=True)
             try: self.conn.rollback()
             except Exception as rb_err: logger.error(f"Error during rollback: {rb_err}")
             return None
        except Exception as e:
            logger.error(f"Unexpected error getting weighted card for user {user_id}: {e}", exc_info=True)
            return None

    def add_user_word(self, user_id, en_word, ru_word):
        """Добавляет пользовательское слово."""
        if self.conn is None: return False
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_words (user_id, en_word, ru_word)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, en_word) DO NOTHING
                    RETURNING id
                """, (user_id, en_word.lower(), ru_word.lower()))
                result = cur.fetchone()
                inserted = result is not None
                self.conn.commit()
                logger.info(f"Add word '{en_word}' for user {user_id}: {'Success' if inserted else 'Already exists'}")
                return inserted
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error adding word '{en_word}' for user {user_id}: {e}", exc_info=True)
            return False

    def delete_user_word(self, user_id, en_word):
        """Удаляет пользовательское слово И связанную с ним статистику."""
        if self.conn is None: return False
        word_id_to_delete = None
        en_word_lower = en_word.lower()
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT id FROM user_words WHERE user_id = %s AND en_word = %s", (user_id, en_word_lower))
                result = cur.fetchone()
                if not result:
                    logger.warning(f"Word '{en_word}' not found for user {user_id} to delete.")
                    return False
                word_id_to_delete = result[0]
                cur.execute(
                    "DELETE FROM user_word_progress WHERE user_id = %s AND word_type = 'user' AND word_ref_id = %s",
                    (user_id, word_id_to_delete)
                )
                progress_deleted_count = cur.rowcount
                cur.execute("DELETE FROM user_words WHERE id = %s", (word_id_to_delete,))
                deleted_count = cur.rowcount
            self.conn.commit()
            logger.info(f"Deleted word '{en_word}' (id: {word_id_to_delete}), its progress ({progress_deleted_count} rows) for user {user_id}. Success: {deleted_count > 0}")
            return deleted_count > 0
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error deleting word '{en_word}' for user {user_id}: {e}", exc_info=True)
            return False

    def record_answer(self, user_id, word_type, word_ref_id, is_correct):
        """Записывает результат ответа пользователя в user_word_progress."""
        if self.conn is None: return False
        try:
            with self.conn.cursor() as cur:
                now_utc = datetime.now(timezone.utc)
                if is_correct:
                    update_sql = """
                    INSERT INTO user_word_progress (user_id, word_type, word_ref_id, correct_count, incorrect_count, last_tested)
                    VALUES (%s, %s, %s, 1, 0, %s)
                    ON CONFLICT (user_id, word_type, word_ref_id) DO UPDATE SET
                        correct_count = user_word_progress.correct_count + 1, last_tested = EXCLUDED.last_tested;
                    """
                else:
                    update_sql = """
                    INSERT INTO user_word_progress (user_id, word_type, word_ref_id, correct_count, incorrect_count, last_tested)
                    VALUES (%s, %s, %s, 0, 1, %s)
                    ON CONFLICT (user_id, word_type, word_ref_id) DO UPDATE SET
                        incorrect_count = user_word_progress.incorrect_count + 1, last_tested = EXCLUDED.last_tested;
                    """
                cur.execute(update_sql, (user_id, word_type, word_ref_id, now_utc))
                self.conn.commit()
                logger.debug(f"Recorded answer for user {user_id}, word {word_type}/{word_ref_id}, correct: {is_correct}")
                return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error recording answer for user {user_id}, word {word_type}/{word_ref_id}: {e}", exc_info=True)
            return False

    def get_user_stats(self, user_id):
        """Получает общую статистику пользователя."""
        if self.conn is None: return {'total_correct': 0, 'total_incorrect': 0, 'words_practiced': 0}
        try:
            with self.conn.cursor() as cur:
                 cur.execute("""
                    SELECT COALESCE(SUM(correct_count), 0), COALESCE(SUM(incorrect_count), 0), COUNT(*)
                    FROM user_word_progress WHERE user_id = %s
                """, (user_id,))
                 stats = cur.fetchone()
                 return {'total_correct': stats[0], 'total_incorrect': stats[1], 'words_practiced': stats[2]}
        except Exception as e:
            logger.error(f"Error getting stats for user {user_id}: {e}", exc_info=True)
            return {'total_correct': 0, 'total_incorrect': 0, 'words_practiced': 0}

    def get_user_words(self, user_id):
        """Возвращает список слов (en_word, ru_word), добавленных пользователем."""
        if self.conn is None: return []
        try:
            with self.conn.cursor() as cur:
                 cur.execute("SELECT en_word, ru_word FROM user_words WHERE user_id = %s ORDER BY en_word", (user_id,))
                 user_words = cur.fetchall()
                 logger.debug(f"Retrieved {len(user_words)} words for user {user_id}")
                 return user_words
        except Exception as e:
            logger.error(f"Error getting words for user {user_id}: {e}", exc_info=True)
            return []

    def count_total_words(self, user_id):
        """Считает общее количество УНИКАЛЬНЫХ английских слов, доступных пользователю."""
        if self.conn is None: return 0
        try:
            with self.conn.cursor() as cur:
                 cur.execute("""
                    SELECT COUNT(DISTINCT en_word) FROM (
                        SELECT en_word FROM common_words
                        UNION
                        SELECT en_word FROM user_words WHERE user_id = %s
                    ) AS all_words
                """, (user_id,))
                 count = cur.fetchone()[0]
                 logger.debug(f"Total unique words count for user {user_id}: {count}")
                 return count
        except Exception as e:
            logger.error(f"Error counting total words for user {user_id}: {e}", exc_info=True)
            return 0

    # --- Функции для настроек режима ввода ---

    def get_user_input_mode(self, user_id):
        """Получает режим ввода пользователя. Возвращает 'buttons' или 'keyboard'."""
        if self.conn is None:
            logger.error("Cannot get user input mode: DB connection lost.")
            return 'buttons' # Дефолт при ошибке
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT input_mode FROM user_preferences WHERE user_id = %s", (user_id,))
                result = cur.fetchone()
                return result[0] if result else 'buttons' # Дефолт, если записи нет
        except Exception as e:
            logger.error(f"Error getting input mode for user {user_id}: {e}", exc_info=True)
            return 'buttons' # Дефолт при ошибке

    def set_user_input_mode(self, user_id, mode):
        """Устанавливает режим ввода для пользователя ('buttons' или 'keyboard')."""
        if self.conn is None: return False
        if mode not in ('buttons', 'keyboard'): return False
        try:
            with self.conn.cursor() as cur:
                sql = """
                    INSERT INTO user_preferences (user_id, input_mode) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET input_mode = EXCLUDED.input_mode;
                """
                cur.execute(sql, (user_id, mode))
                self.conn.commit()
                logger.info(f"Set input mode to '{mode}' for user {user_id}")
                return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error setting input mode for user {user_id}: {e}", exc_info=True)
            return False

    def close(self):
        """Закрывает соединение с базой"""
        if self.conn and not self.conn.closed:
            try:
                 self.conn.close()
                 logger.info("Database connection closed.")
            except Exception as e:
                 logger.error(f"Error closing database connection: {e}", exc_info=True)
        elif self.conn is None:
             logger.warning("Attempted to close a non-existent database connection.")

# --- Инициализация глобального объекта базы данных ---
db = None
try:
    db = Database()
    if db.conn is None:
        raise ConnectionError("DB object created, but connection is None.")
except Exception as global_db_error:
    logger.critical(f"FATAL: Failed to initialize the Database object or connection: {global_db_error}", exc_info=True)
    raise # Пробрасываем, чтобы bot.py не запустился