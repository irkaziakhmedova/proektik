import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton,
    InlineKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler,
    CallbackQueryHandler, filters, ConversationHandler
)
from apscheduler.schedulers.background import BackgroundScheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация базы данных
conn = sqlite3.connect('tasks.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''CREATE TABLE IF NOT EXISTS tasks
                  (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, description TEXT,
                   deadline TEXT, priority INTEGER, status TEXT, notified INTEGER DEFAULT 0,
                   creation_date TEXT, is_deleted INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS user_activity
                  (id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT, timestamp TEXT)''')
conn.commit()

# Состояния для ConversationHandler
TITLE, DESCRIPTION, DEADLINE, PRIORITY = range(4)

# Главное меню
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📝 Добавить задачу", "📋 Список задач"],
        ["⏱ Таймер Помодоро", "🗑 Удалить задачу"],
        ["📊 Моя активность"]
    ], resize_keyboard=True)

# /start команда
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать в DeadlineBuddy! 🤖\nВыберите действие:",
        reply_markup=main_menu_keyboard()
    )

# Обработка кнопок меню
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📋 Список задач":
        await list_tasks(update, context)
    elif text == "🗑 Удалить задачу":
        await delete_task_prompt(update, context)
    elif text == "⏱ Таймер Помодоро":
        await pomodoro_menu(update, context)
    elif text == "📊 Моя активность":
        await show_user_activity(update, context)

# Добавление задачи через ConversationHandler
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название задачи:", reply_markup=ReplyKeyboardRemove())
    return TITLE

async def task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_task'] = {'title': update.message.text}
    await update.message.reply_text("Введите описание задачи:")
    return DESCRIPTION

async def task_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_task']['description'] = update.message.text
    await update.message.reply_text("Укажите дедлайн (ДД.ММ или ДД.ММ.ГГГГ, опционально ЧЧ:ММ):")
    return DEADLINE

async def task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        formatted_deadline = format_deadline(update.message.text)
        context.user_data['new_task']['deadline'] = formatted_deadline
        await update.message.reply_text("Введите приоритет (1 - низкий, 2 - средний, 3 - высокий, 4 - критический):")
        return PRIORITY
    except Exception as e:
        await update.message.reply_text(f"Ошибка в формате даты: {e}. Попробуйте снова.")
        return DEADLINE

async def task_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    priority = update.message.text
    if priority not in ['1', '2', '3', '4']:
        await update.message.reply_text("Приоритет должен быть числом от 1 до 4. Попробуйте ещё раз.")
        return PRIORITY

    context.user_data['new_task']['priority'] = int(priority)
    save_task(update.effective_user.id, context.user_data['new_task'])
    await update.message.reply_text("✅ Задача успешно создана!", reply_markup=main_menu_keyboard())
    context.user_data.pop('new_task')
    return ConversationHandler.END

# Сохранение задачи в БД
def save_task(user_id, task_data):
    cursor.execute('''INSERT INTO tasks (user_id, title, description, deadline, priority, status, creation_date)
                      VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (user_id, task_data['title'], task_data['description'],
                    task_data['deadline'], task_data['priority'], 'active', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()

# Форматирование дедлайна
def format_deadline(date_str):
    try:
        parts = date_str.split()
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else "23:59"
        now = datetime.now()

        date_components = date_part.split('.')
        if len(date_components) == 1:  # Только день указан
            day = int(date_components[0])
            month = now.month
            year = now.year

            if day < now.day:
                month += 1
                if month > 12:
                    month = 1
                    year += 1

            date_part = f"{day:02}.{month:02}.{year}"
        elif len(date_components) == 2:  # День и месяц
            day, month = map(int, date_components)
            year = now.year

            if month < now.month or (month == now.month and day < now.day):
                year += 1

            date_part = f"{day:02}.{month:02}.{year}"

        datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
        return f"{date_part} {time_part}"
    except ValueError:
        raise ValueError("Неверный формат даты. Используйте ДД или ДД.ММ или ДД.ММ.ГГГГ, опционально ЧЧ:ММ.")

# Просмотр задач
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT title, description, deadline, priority FROM tasks WHERE user_id = ? AND status = 'active' AND is_deleted = 0",
                   (update.effective_user.id,))
    tasks = cursor.fetchall()
    if not tasks:
        await update.message.reply_text("У вас нет активных задач.")
        return
    response = "\n\n".join([f"📌 *{task[0]}*\n📝 {task[1]}\n⏰ Дедлайн: {task[2]}\n🔥 Приоритет: {task[3]}"
                            for task in tasks])
    await update.message.reply_text(response, parse_mode='Markdown')

# Удаление задачи
async def delete_task_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT id, title FROM tasks WHERE user_id = ? AND status = 'active' AND is_deleted = 0", (update.effective_user.id,))
    tasks = cursor.fetchall()
    if not tasks:
        await update.message.reply_text("Нет задач для удаления.")
        return

    buttons = [[InlineKeyboardButton(f"{task[1]}", callback_data=f"delete_{task[0]}")] for task in tasks]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите задачу для удаления:", reply_markup=reply_markup)

async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.split('_')[1])
    cursor.execute("UPDATE tasks SET is_deleted = 1 WHERE id = ?", (task_id,))
    conn.commit()

    await query.edit_message_text(f"✅ Задача помечена как удалённая!")
    await query.message.reply_text("Возвращаюсь в главное меню...", reply_markup=main_menu_keyboard())

# Таймер Помодоро
async def pomodoro_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("Работа 25 минут", callback_data="work_25")],
        [InlineKeyboardButton("Отдых 5 минут", callback_data="rest_5")],
        [InlineKeyboardButton("Отдых 15 минут", callback_data="rest_15")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите режим таймера:", reply_markup=reply_markup)

async def handle_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = context.user_data

    if "timer_task" in user_data and not user_data["timer_task"].done():
        await query.edit_message_text("⏳ Таймер уже запущен! Остановите текущий таймер, чтобы начать новый.")
        return

    command = query.data
    if command.startswith("work_") or command.startswith("rest_"):
        minutes = int(command.split("_")[1])
        await start_timer(query, context, minutes)

async def start_timer(query, context, minutes):
    user_data = context.user_data
    stop_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ Остановить таймер", callback_data="stop_timer")]
    ])
    message = await query.edit_message_text(
        f"⏳ Таймер запущен на {minutes} минут.",
        reply_markup=stop_button
    )
    user_data["timer_task"] = asyncio.create_task(timer_task(message, context, minutes))

async def timer_task(message, context, minutes):
    user_data = context.user_data
    try:
        total_seconds = minutes * 60
        for remaining in range(total_seconds, -1, -1):
            minutes_left, seconds_left = divmod(remaining, 60)
            await message.edit_text(
                f"⏳ Осталось {minutes_left:02}:{seconds_left:02}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏹ Остановить таймер", callback_data="stop_timer")]
                ])
            )
            await asyncio.sleep(1)
        log_pomodoro(message.chat_id, minutes)
        await message.edit_text("⏰ Таймер завершён! Отличная работа! 💪")
    except asyncio.CancelledError:
        await message.edit_text("⏹ Таймер остановлен.")
        user_data.pop("timer_task", None)

async def stop_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = context.user_data

    if "timer_task" in user_data and not user_data["timer_task"].done():
        user_data["timer_task"].cancel()
        await query.edit_message_text("⏹ Таймер остановлен.")
    else:
        await query.edit_message_text("❌ Нет активного таймера для остановки.")

def log_pomodoro(user_id, minutes):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''INSERT INTO user_activity (user_id, action, timestamp)
                      VALUES (?, ?, ?)''', (user_id, f"Рабочий таймер {minutes} минут", timestamp))
    conn.commit()

# Гистограмма активности
def get_activity_data(user_id):
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Задачи за неделю
    cursor.execute('''SELECT COUNT(*) FROM tasks WHERE user_id = ? AND datetime(creation_date) >= ?''', (user_id, week_ago))
    tasks_week = cursor.fetchone()[0] or 0

    # Задачи за месяц
    cursor.execute('''SELECT COUNT(*) FROM tasks WHERE user_id = ? AND datetime(creation_date) >= ?''', (user_id, month_ago))
    tasks_month = cursor.fetchone()[0] or 0

    # Все задачи
    cursor.execute('''SELECT COUNT(*) FROM tasks WHERE user_id = ?''', (user_id,))
    tasks_all_time = cursor.fetchone()[0] or 0

    # Время Помодоро
    cursor.execute('''SELECT SUM(CAST(SUBSTR(action, 14) AS INTEGER)) FROM user_activity 
                      WHERE user_id = ? AND action LIKE "Рабочий таймер%"''', (user_id,))
    pomodoro_minutes = cursor.fetchone()[0] or 0

    return tasks_week, tasks_month, tasks_all_time, pomodoro_minutes

async def show_user_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tasks_week, tasks_month, tasks_all_time, pomodoro_minutes = get_activity_data(user_id)

    # Если данных нет, создаем заглушку
    if tasks_week == 0 and tasks_month == 0 and tasks_all_time == 0:
        await update.message.reply_text("📊 Нет данных для отображения активности. Создайте задачи или используйте Помодоро.")
        return

    # Форматируем текст для вывода
    activity_report = (
        f"📊 *Ваш отчет по активности*\n\n"
        f"📅 За последнюю неделю: {tasks_week} задач\n"
        f"📅 За последний месяц: {tasks_month} задач\n"
        f"📋 Всего задач: {tasks_all_time}\n"
        f"⏳ Время в Помодоро: {pomodoro_minutes} минут\n"
    )

    await update.message.reply_text(activity_report, parse_mode='Markdown')

# Основная функция
def main():
    app = Application.builder().token("7817193916:AAEzU7e5ymaRa2QmoO3xDgFvZeWP-jOhu18").build()

    task_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^📝 Добавить задачу$"), add_task)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_description)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_deadline)],
            PRIORITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_priority)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("activity", show_user_activity))
    app.add_handler(task_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
    app.add_handler(CallbackQueryHandler(handle_delete, pattern="^delete_.*"))
    app.add_handler(CallbackQueryHandler(handle_pomodoro, pattern="^(work|rest)_.*"))
    app.add_handler(CallbackQueryHandler(stop_timer, pattern="^stop_timer$"))

    app.run_polling()

if __name__ == "__main__":
    main()