"""
MILEAN Telegram Bot — Юридический ИИ-ассистент
Full-featured: AI chat, file analysis, RAG, web search, instruction slots
"""

import asyncio
import json
import logging
import os
import re
import ssl
import tempfile
from io import BytesIO
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, Message, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ─── CONFIG ───
BOT_TOKEN = "8659962856:AAFkP4ziNKov5KfRwXJuYdIAnfF8zcxSqQw"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_KEY = "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"
MODEL = "qwen/qwen3.5-397b-a17b"
MAX_HISTORY = 10
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
CHUNK_SIZE = 500
RAG_TOP_K = 8

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("milean-bot")

# ─── DEFAULT INSTRUCTION ───
MILEAN_INSTR = """Ты — высококлассный российский юрист и адвокат с практикой более 20 лет.
Специализация:
— мошенничество с недвижимостью
— подделка подписей и документов
— злоупотребление доверенностями
— отчуждение долей, ПДКП, ничтожные сделки
— гражданские и уголовные дела (в т.ч. 159, 160, 327 УК РФ)

🎯 ЦЕЛЬ РАБОТЫ
Комплексно разобрать все эпизоды дела.
Выявить и зафиксировать:
• ничтожность и оспоримость сделок
• подделку подписей и использование факсимиле без полномочий
• злоупотребление доверенностями
• незаконное распоряжение денежными средствами

⚖️ ОБЯЗАТЕЛЬНЫЕ ИСТОЧНИКИ ПРАВА
ГК РФ, УК РФ (159, 160, 165, 327), ФЗ-218, Постановления Пленума ВС РФ

🧠 ПРАВИЛА РАБОТЫ
• Жёстко разделяй: факт / правовая оценка / гипотеза
• Любой вывод — через норму права
• Не допускать фантазий и домыслов

📊 ФОРМАТ ОТВЕТА
1️⃣ КРАТКИЙ ВЫВОД (5–10 строк)
2️⃣ ТАБЛИЦА НАРУШЕНИЙ
3️⃣ УГОЛОВНО-ПРАВОВАЯ ОЦЕНКА
4️⃣ ЭКСПЕРТИЗЫ
5️⃣ ПРОЦЕССУАЛЬНЫЕ ШАГИ"""

# ─── USER DATA STORAGE (in-memory) ───
users = {}  # user_id -> {hist, files, chunks, instr, web_on, think_on, active_slot}


def get_user(uid: int) -> dict:
    if uid not in users:
        users[uid] = {
            "hist": [],
            "files": [],  # [{name, chunks, chars}]
            "chunks": [],  # [{text, file}]
            "instr": MILEAN_INSTR,
            "web_on": False,
            "think_on": True,
            "active_slot": "milean",
            "slots": {},  # {slot_id: {name, text}}
        }
    return users[uid]


# ─── TEXT EXTRACTION ───
def extract_pdf(data: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(BytesIO(data))
    text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text += t + "\n"
    return text


def extract_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_txt(data: bytes) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "pdf":
        return extract_pdf(data)
    elif ext in ("docx", "doc"):
        return extract_docx(data)
    else:
        return extract_txt(data)


# ─── CHUNKING & RAG ───
def chunk_text(text: str, size: int = CHUNK_SIZE) -> list:
    chunks = []
    sentences = re.split(r'(?<=[.!?。\n])\s+', text)
    cur = ""
    for s in sentences:
        if len(cur + s) > size and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += s + " "
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def local_search(query: str, chunks: list, top_k: int = RAG_TOP_K) -> list:
    if not chunks:
        return []
    q_words = re.sub(r'[^\w\s]', '', query.lower()).split()
    q_words = [w for w in q_words if len(w) > 2]
    if not q_words:
        return chunks[:top_k]

    scored = []
    for ch in chunks:
        txt = ch["text"].lower()
        score = 0
        for w in q_words:
            idx = 0
            while True:
                idx = txt.find(w, idx)
                if idx == -1:
                    break
                score += 1
                idx += len(w)
        if score > 0:
            scored.append((ch, score))

    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:top_k]]


# ─── DUCKDUCKGO SEARCH ───
async def web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append(f"• {r['title']}: {r['body']}")
        if results:
            return "🌐 Результаты веб-поиска:\n" + "\n".join(results)
        return ""
    except Exception as e:
        log.error(f"Web search error: {e}")
        return ""


# ─── NVIDIA API CALL ───
async def call_nvidia(messages: list, think: bool = True) -> str:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 16384,
        "stream": False
    }

    if think:
        payload["thinking"] = {"type": "enabled", "budget_tokens": 8096}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {NVIDIA_KEY}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_URL,
            json=payload,
            headers=headers,
            ssl=ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=180)
        ) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise Exception(f"NVIDIA API {resp.status}: {err[:300]}")
            data = await resp.json()

    choice = data["choices"][0]["message"]
    content = choice.get("content", "")

    # Extract thinking if present
    thinking = ""
    if "reasoning_content" in choice and choice["reasoning_content"]:
        thinking = choice["reasoning_content"]

    return content, thinking


# ─── TELEGRAM BOT ───
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()


class EditInstr(StatesGroup):
    waiting = State()


class SaveSlot(StatesGroup):
    waiting_name = State()
    slot_id = State()


# ─── COMMANDS ───
@router.message(CommandStart())
async def cmd_start(msg: Message):
    u = get_user(msg.from_user.id)
    kb = get_main_keyboard(u)
    await msg.answer(
        "⚖️ <b>MILEAN — Юридическая Компания</b>\n\n"
        "Я ваш юридический ИИ-ассистент.\n\n"
        "📎 Отправьте файлы (PDF, DOCX, TXT) для анализа\n"
        "💬 Задайте вопрос — я отвечу с учётом загруженных документов\n"
        "🌐 Включите веб-поиск для актуальной информации\n\n"
        "Используйте /help для списка команд",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@router.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "📋 <b>Команды MILEAN:</b>\n\n"
        "⚖️ /milean — загрузить стандартную инструкцию\n"
        "📝 /instr — показать текущую инструкцию\n"
        "✏️ /setinstr — написать свою инструкцию\n"
        "💾 /saveslot <i>N</i> — сохранить инструкцию в слот (1-10)\n"
        "📌 /loadslot <i>N</i> — загрузить инструкцию из слота\n"
        "📋 /slots — список слотов\n"
        "🗑 /clearinstr — очистить инструкцию\n\n"
        "📎 /files — список загруженных файлов\n"
        "🗑 /clearfiles — удалить все файлы\n\n"
        "🌐 /web — вкл/выкл веб-поиск\n"
        "🧠 /think — вкл/выкл режим Think\n"
        "🔄 /clear — очистить историю чата\n"
        "⚙️ /settings — текущие настройки\n"
        "❓ /help — эта справка",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("milean"))
async def cmd_milean(msg: Message):
    u = get_user(msg.from_user.id)
    u["instr"] = MILEAN_INSTR
    u["active_slot"] = "milean"
    await msg.answer("⚖️ Инструкция MILEAN загружена!", reply_markup=get_main_keyboard(u))


@router.message(Command("instr"))
async def cmd_instr(msg: Message):
    u = get_user(msg.from_user.id)
    instr = u["instr"]
    if instr:
        text = f"📝 <b>Текущая инструкция</b> ({u['active_slot']}):\n\n{instr[:3000]}"
        if len(instr) > 3000:
            text += "\n\n<i>...обрезано</i>"
    else:
        text = "📝 Инструкция не задана. Используйте /milean или /setinstr"
    await msg.answer(text, parse_mode=ParseMode.HTML)


@router.message(Command("setinstr"))
async def cmd_setinstr(msg: Message, state: FSMContext):
    await state.set_state(EditInstr.waiting)
    await msg.answer(
        "✏️ Отправьте текст новой инструкции.\n"
        "Или /cancel для отмены.",
        parse_mode=ParseMode.HTML
    )


@router.message(EditInstr.waiting)
async def process_setinstr(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/cancel"):
        await state.clear()
        await msg.answer("❌ Отменено")
        return
    u = get_user(msg.from_user.id)
    u["instr"] = msg.text or ""
    u["active_slot"] = "custom"
    await state.clear()
    await msg.answer("✅ Инструкция обновлена!", reply_markup=get_main_keyboard(u))


@router.message(Command("clearinstr"))
async def cmd_clearinstr(msg: Message):
    u = get_user(msg.from_user.id)
    u["instr"] = ""
    u["active_slot"] = "empty"
    await msg.answer("🗑 Инструкция очищена", reply_markup=get_main_keyboard(u))


@router.message(Command("saveslot"))
async def cmd_saveslot(msg: Message):
    u = get_user(msg.from_user.id)
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer("Использование: /saveslot <i>N</i> <i>название</i>\nПример: /saveslot 1 Бухгалтерия", parse_mode=ParseMode.HTML)
        return
    try:
        slot_id = int(parts[1])
        if not 1 <= slot_id <= 10:
            raise ValueError
    except:
        await msg.answer("❌ Номер слота от 1 до 10")
        return
    if not u["instr"]:
        await msg.answer("❌ Инструкция пуста — нечего сохранять")
        return
    name = parts[2] if len(parts) > 2 else f"Слот {slot_id}"
    u["slots"][str(slot_id)] = {"name": name, "text": u["instr"]}
    await msg.answer(f"💾 Сохранено в слот {slot_id}: <b>{name}</b>", parse_mode=ParseMode.HTML)


@router.message(Command("loadslot"))
async def cmd_loadslot(msg: Message):
    u = get_user(msg.from_user.id)
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Использование: /loadslot <i>N</i>", parse_mode=ParseMode.HTML)
        return
    try:
        slot_id = int(parts[1])
    except:
        await msg.answer("❌ Укажите номер слота (1-10)")
        return
    slot = u["slots"].get(str(slot_id))
    if not slot:
        await msg.answer(f"❌ Слот {slot_id} пуст")
        return
    u["instr"] = slot["text"]
    u["active_slot"] = f"slot_{slot_id}"
    await msg.answer(f"📌 Загружен слот {slot_id}: <b>{slot['name']}</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(u))


@router.message(Command("slots"))
async def cmd_slots(msg: Message):
    u = get_user(msg.from_user.id)
    lines = ["📋 <b>Слоты инструкций:</b>\n"]
    for i in range(1, 11):
        slot = u["slots"].get(str(i))
        if slot:
            lines.append(f"  {i}. 📌 <b>{slot['name']}</b> ({len(slot['text'])} симв.)")
        else:
            lines.append(f"  {i}. <i>пусто</i>")
    lines.append(f"\n⚖️ MILEAN: {'✅ активна' if u['active_slot'] == 'milean' else 'доступна'}")
    await msg.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("web"))
async def cmd_web(msg: Message):
    u = get_user(msg.from_user.id)
    u["web_on"] = not u["web_on"]
    status = "✅ включён" if u["web_on"] else "❌ выключен"
    await msg.answer(f"🌐 Веб-поиск: {status}", reply_markup=get_main_keyboard(u))


@router.message(Command("think"))
async def cmd_think(msg: Message):
    u = get_user(msg.from_user.id)
    u["think_on"] = not u["think_on"]
    status = "✅ включён" if u["think_on"] else "❌ выключен"
    await msg.answer(f"🧠 Think режим: {status}", reply_markup=get_main_keyboard(u))


@router.message(Command("clear"))
async def cmd_clear(msg: Message):
    u = get_user(msg.from_user.id)
    u["hist"] = []
    await msg.answer("🔄 История чата очищена", reply_markup=get_main_keyboard(u))


@router.message(Command("files"))
async def cmd_files(msg: Message):
    u = get_user(msg.from_user.id)
    if not u["files"]:
        await msg.answer("📎 Нет загруженных файлов.\nОтправьте PDF, DOCX или TXT для анализа.")
        return
    lines = ["📎 <b>Загруженные файлы:</b>\n"]
    total_chunks = 0
    total_chars = 0
    for f in u["files"]:
        lines.append(f"  📄 <b>{f['name']}</b> — {f['chunks']} чанков, {f['chars']} симв.")
        total_chunks += f["chunks"]
        total_chars += f["chars"]
    lines.append(f"\n📊 Всего: {len(u['files'])} файлов, {total_chunks} чанков, {total_chars} символов")
    await msg.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("clearfiles"))
async def cmd_clearfiles(msg: Message):
    u = get_user(msg.from_user.id)
    u["files"] = []
    u["chunks"] = []
    await msg.answer("🗑 Все файлы удалены", reply_markup=get_main_keyboard(u))


@router.message(Command("settings"))
async def cmd_settings(msg: Message):
    u = get_user(msg.from_user.id)
    slot_name = u["active_slot"]
    if slot_name == "milean":
        slot_name = "⚖️ MILEAN"
    elif slot_name == "empty":
        slot_name = "⛔ Пусто"
    elif slot_name.startswith("slot_"):
        sid = slot_name.split("_")[1]
        s = u["slots"].get(sid, {})
        slot_name = f"📌 {s.get('name', 'Слот ' + sid)}"
    else:
        slot_name = "✏️ Своя"

    await msg.answer(
        "⚙️ <b>Настройки:</b>\n\n"
        f"📝 Инструкция: {slot_name}\n"
        f"🧠 Think: {'✅' if u['think_on'] else '❌'}\n"
        f"🌐 Веб-поиск: {'✅' if u['web_on'] else '❌'}\n"
        f"📎 Файлов: {len(u['files'])}\n"
        f"💬 История: {len(u['hist'])//2} сообщений\n"
        f"📊 Чанков в RAG: {len(u['chunks'])}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(u)
    )


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Отменено")


# ─── FILE HANDLER ───
@router.message(F.document)
async def handle_document(msg: Message):
    u = get_user(msg.from_user.id)
    doc = msg.document

    if doc.file_size > MAX_FILE_SIZE:
        await msg.answer(f"❌ Файл слишком большой (макс. {MAX_FILE_SIZE // 1024 // 1024}MB)")
        return

    ext = (doc.file_name or "").lower().rsplit(".", 1)[-1] if doc.file_name and "." in doc.file_name else ""
    supported = ("pdf", "docx", "doc", "txt", "py", "js", "json", "csv", "md", "html", "xml", "log")
    if ext not in supported:
        await msg.answer(f"❌ Формат .{ext} не поддерживается.\nПоддерживаются: {', '.join(supported)}")
        return

    status_msg = await msg.answer(f"📄 <b>{doc.file_name}</b>\n⏳ Загрузка и анализ...", parse_mode=ParseMode.HTML)
    await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)

    try:
        file = await bot.get_file(doc.file_id)
        data = BytesIO()
        await bot.download_file(file.file_path, data)
        file_bytes = data.getvalue()

        text = extract_text(doc.file_name, file_bytes)
        if not text.strip():
            await status_msg.edit_text(f"📄 <b>{doc.file_name}</b>\n⚠️ Не удалось извлечь текст", parse_mode=ParseMode.HTML)
            return

        chunks = chunk_text(text)
        for ch in chunks:
            u["chunks"].append({"text": ch, "file": doc.file_name})

        fobj = {"name": doc.file_name, "chunks": len(chunks), "chars": len(text)}
        u["files"].append(fobj)

        await status_msg.edit_text(
            f"✅ <b>{doc.file_name}</b> обработан!\n"
            f"📊 {len(chunks)} чанков · {len(text)} символов\n"
            f"📎 Всего файлов: {len(u['files'])}",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        log.error(f"File processing error: {e}")
        await status_msg.edit_text(f"❌ Ошибка обработки: {e}", parse_mode=ParseMode.HTML)


# ─── MAIN MESSAGE HANDLER ───
@router.message(F.text & ~F.text.startswith("/"))
async def handle_message(msg: Message, state: FSMContext):
    u = get_user(msg.from_user.id)
    query = msg.text.strip()
    if not query:
        return

    await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    status_msg = await msg.answer("⏳ Обработка...")

    try:
        # Build system message
        sys_parts = []

        # Instruction
        if u["instr"]:
            sys_parts.append(u["instr"])

        # Web search
        web_context = ""
        if u["web_on"]:
            await status_msg.edit_text("🌐 Поиск в интернете...")
            web_context = await web_search(query)
            if web_context:
                sys_parts.append(web_context)

        # RAG search
        rag_context = ""
        if u["chunks"]:
            await status_msg.edit_text("🔍 RAG поиск по файлам...")
            relevant = local_search(query, u["chunks"])
            if relevant:
                rag_parts = []
                for ch in relevant:
                    rag_parts.append(f"[{ch['file']}]: {ch['text']}")
                rag_context = "📎 Контекст из файлов:\n" + "\n---\n".join(rag_parts)
                sys_parts.append(rag_context)

        await status_msg.edit_text("🧠 Генерация ответа...")

        # Build messages
        messages = []
        if sys_parts:
            messages.append({"role": "system", "content": "\n\n".join(sys_parts)})

        # Add history
        for h in u["hist"][-MAX_HISTORY:]:
            messages.append(h)

        messages.append({"role": "user", "content": query})

        # Call AI
        content, thinking = await call_nvidia(messages, think=u["think_on"])

        # Save to history
        u["hist"].append({"role": "user", "content": query})
        u["hist"].append({"role": "assistant", "content": content})

        # Trim history
        if len(u["hist"]) > MAX_HISTORY * 2:
            u["hist"] = u["hist"][-MAX_HISTORY * 2:]

        # Format response
        response = ""
        if thinking and u["think_on"]:
            think_short = thinking[:500] + "..." if len(thinking) > 500 else thinking
            response += f"<blockquote>💭 <b>Размышление:</b>\n{_escape(think_short)}</blockquote>\n\n"

        response += content

        # Send response (split if too long)
        await status_msg.delete()
        await _send_long(msg.chat.id, response, msg.message_id)

    except Exception as e:
        log.error(f"Message handling error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")


# ─── CALLBACK HANDLERS ───
@router.callback_query(F.data == "toggle_web")
async def cb_web(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    u["web_on"] = not u["web_on"]
    await cb.answer(f"🌐 Веб-поиск: {'ВКЛ' if u['web_on'] else 'ВЫКЛ'}")
    await cb.message.edit_reply_markup(reply_markup=get_main_keyboard(u))


@router.callback_query(F.data == "toggle_think")
async def cb_think(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    u["think_on"] = not u["think_on"]
    await cb.answer(f"🧠 Think: {'ВКЛ' if u['think_on'] else 'ВЫКЛ'}")
    await cb.message.edit_reply_markup(reply_markup=get_main_keyboard(u))


@router.callback_query(F.data == "load_milean")
async def cb_milean(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    u["instr"] = MILEAN_INSTR
    u["active_slot"] = "milean"
    await cb.answer("⚖️ MILEAN загружена")
    await cb.message.edit_reply_markup(reply_markup=get_main_keyboard(u))


@router.callback_query(F.data == "clear_instr")
async def cb_clear_instr(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    u["instr"] = ""
    u["active_slot"] = "empty"
    await cb.answer("🗑 Инструкция очищена")
    await cb.message.edit_reply_markup(reply_markup=get_main_keyboard(u))


@router.callback_query(F.data == "show_settings")
async def cb_settings(cb: CallbackQuery):
    await cb.answer()
    u = get_user(cb.from_user.id)
    await cb.message.answer(
        f"⚙️ Think: {'✅' if u['think_on'] else '❌'} | "
        f"Web: {'✅' if u['web_on'] else '❌'} | "
        f"Файлов: {len(u['files'])} | "
        f"Чанков: {len(u['chunks'])}"
    )


# ─── KEYBOARDS ───
def get_main_keyboard(u: dict) -> InlineKeyboardMarkup:
    web_label = "🌐 Web: ✅" if u["web_on"] else "🌐 Web: ❌"
    think_label = "🧠 Think: ✅" if u["think_on"] else "🧠 Think: ❌"
    instr_label = "⚖️ MILEAN ✅" if u["active_slot"] == "milean" else "⚖️ MILEAN"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=web_label, callback_data="toggle_web"),
            InlineKeyboardButton(text=think_label, callback_data="toggle_think"),
        ],
        [
            InlineKeyboardButton(text=instr_label, callback_data="load_milean"),
            InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_instr"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Статус", callback_data="show_settings"),
        ]
    ])


# ─── HELPERS ───
def _escape(text: str) -> str:
    """Escape HTML special chars"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _send_long(chat_id: int, text: str, reply_to: int = None):
    """Send long messages by splitting at 4096 chars"""
    MAX_LEN = 4000
    if len(text) <= MAX_LEN:
        try:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_to_message_id=reply_to)
        except:
            # Fallback without HTML if parsing fails
            await bot.send_message(chat_id, text, reply_to_message_id=reply_to)
        return

    # Split by paragraphs
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN:
            if current:
                parts.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        parts.append(current)

    for i, part in enumerate(parts):
        try:
            await bot.send_message(
                chat_id, part,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to if i == 0 else None
            )
        except:
            await bot.send_message(
                chat_id, part,
                reply_to_message_id=reply_to if i == 0 else None
            )
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)


# ─── SETUP BOT COMMANDS ───
async def set_commands():
    commands = [
        BotCommand(command="start", description="🚀 Начать работу"),
        BotCommand(command="help", description="📋 Список команд"),
        BotCommand(command="milean", description="⚖️ Загрузить инструкцию MILEAN"),
        BotCommand(command="instr", description="📝 Показать инструкцию"),
        BotCommand(command="setinstr", description="✏️ Написать свою инструкцию"),
        BotCommand(command="slots", description="📋 Список слотов"),
        BotCommand(command="web", description="🌐 Вкл/выкл веб-поиск"),
        BotCommand(command="think", description="🧠 Вкл/выкл Think"),
        BotCommand(command="files", description="📎 Список файлов"),
        BotCommand(command="clear", description="🔄 Очистить историю"),
        BotCommand(command="settings", description="⚙️ Настройки"),
    ]
    await bot.set_my_commands(commands)


# ─── MAIN ───
async def main():
    dp.include_router(router)
    await set_commands()
    log.info("🚀 MILEAN Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
