import os
import sys
import asyncio
import logging
from collections import defaultdict, deque

from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus
from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters
from pytgcalls.types import MediaStream

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("musicbot")


def get_env(name: str) -> str:
    """Read a required env var and fail loudly with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        log.error(
            "Missing environment variable: %s — "
            "Railway > Service > Variables ထဲမှာ %s ကို ထည့်ပေးပါ။",
            name, name,
        )
        sys.exit(1)
    return value


try:
    API_ID = int(get_env("API_ID"))
except ValueError:
    log.error("API_ID သည် ဂဏန်းဖြစ်ရပါမည် (ဥပမာ: 12345678)")
    sys.exit(1)

API_HASH = get_env("API_HASH")
BOT_TOKEN = get_env("BOT_TOKEN")
STRING_SESSION = get_env("STRING_SESSION")

bot = Client("music_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user = Client("music_user", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)
calls = PyTgCalls(user)

queues = defaultdict(deque)
current = {}

ADMINS = (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)


async def is_admin(message: Message) -> bool:
    # Anonymous group admin (sender_chat) is treated as admin
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    if not message.from_user:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ADMINS
    except Exception:
        return False


async def find_audio(query: str):
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "default_search": "ytsearch1",
    }

    loop = asyncio.get_running_loop()

    def extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = next((x for x in info["entries"] if x), None)
            if not info or not info.get("url"):
                return None
            return {"title": info.get("title", "Unknown"), "url": info["url"]}

    return await loop.run_in_executor(None, extract)


async def play_next(chat_id: int):
    if not queues[chat_id]:
        current.pop(chat_id, None)
        try:
            await calls.leave_call(chat_id)
        except Exception:
            pass
        return

    track = queues[chat_id].popleft()
    current[chat_id] = track

    try:
        await calls.play(chat_id, MediaStream(track["url"]))
        await bot.send_message(
            chat_id,
            f"🎵 **Now Playing**\n\n{track['title']}\n📋 Queue: {len(queues[chat_id])}"
        )
    except Exception as e:
        log.exception("play error")
        try:
            await bot.send_message(chat_id, f"❌ Play error: `{str(e)[:400]}`")
        except Exception:
            pass
        await play_next(chat_id)


@calls.on_update(tg_filters.stream_end)
async def on_stream_end(_, update):
    """သီချင်းတစ်ပုဒ်ပြီးသွားရင် နောက်တစ်ပုဒ် အလိုအလျောက်ဆက်ဖွင့်မယ်"""
    chat_id = getattr(update, "chat_id", None)
    if chat_id is not None:
        await play_next(chat_id)


@bot.on_message(filters.command("start"))
async def start(_, message: Message):
    await message.reply_text(
        "🎵 **Music Bot**\n\n"
        "/play <song> — သီချင်းရှာဖွင့်\n"
        "/queue — စာရင်းကြည့်\n"
        "/skip — ကျော် (admin)\n"
        "/pause — ခဏရပ် (admin)\n"
        "/resume — ပြန်ဖွင့် (admin)\n"
        "/stop — ရပ်ပြီး queue ရှင်း (admin)"
    )


@bot.on_message(filters.command("play"))
async def play(_, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("အသုံးပြုပုံ: `/play song name`")

    query = message.text.split(None, 1)[1].strip()
    msg = await message.reply_text("🔎 ရှာနေပါတယ်...")

    try:
        track = await find_audio(query)
        if not track:
            return await msg.edit_text("❌ သီချင်းမတွေ့ပါ။")

        chat_id = message.chat.id
        queues[chat_id].append(track)

        if chat_id not in current:
            await msg.edit_text("▶️ စတင်ဖွင့်နေပါတယ်...")
            await play_next(chat_id)
        else:
            await msg.edit_text(f"✅ Queue ထဲထည့်ပြီးပါပြီ\n🎵 {track['title']}")
    except Exception as e:
        log.exception("search/play error")
        await msg.edit_text(f"❌ Error: `{str(e)[:500]}`")


@bot.on_message(filters.command("queue"))
async def queue(_, message: Message):
    chat_id = message.chat.id
    lines = ["📋 **Queue**", ""]
    if chat_id in current:
        lines.append(f"▶️ {current[chat_id]['title']}")
    if queues[chat_id]:
        for i, track in enumerate(list(queues[chat_id])[:20], 1):
            lines.append(f"{i}. {track['title']}")
    elif chat_id not in current:
        lines.append("Queue empty.")
    await message.reply_text("\n".join(lines))


@bot.on_message(filters.command("skip"))
async def skip(_, message: Message):
    if not await is_admin(message):
        return await message.reply_text("❌ Admin only.")
    await message.reply_text("⏭️ Skipped.")
    await play_next(message.chat.id)


@bot.on_message(filters.command("pause"))
async def pause(_, message: Message):
    if not await is_admin(message):
        return await message.reply_text("❌ Admin only.")
    try:
        await calls.pause(message.chat.id)
        await message.reply_text("⏸️ Paused.")
    except Exception as e:
        await message.reply_text(f"❌ `{str(e)[:400]}`")


@bot.on_message(filters.command("resume"))
async def resume(_, message: Message):
    if not await is_admin(message):
        return await message.reply_text("❌ Admin only.")
    try:
        await calls.resume(message.chat.id)
        await message.reply_text("▶️ Resumed.")
    except Exception as e:
        await message.reply_text(f"❌ `{str(e)[:400]}`")


@bot.on_message(filters.command("stop"))
async def stop(_, message: Message):
    if not await is_admin(message):
        return await message.reply_text("❌ Admin only.")

    chat_id = message.chat.id
    queues[chat_id].clear()
    current.pop(chat_id, None)

    try:
        await calls.leave_call(chat_id)
    except Exception:
        pass

    await message.reply_text("⏹️ Stopped & queue cleared.")


async def main():
    await bot.start()
    await user.start()
    await calls.start()
    me = await bot.get_me()
    log.info("Started @%s", me.username or me.id)
    await idle()
    await calls.stop()
    await user.stop()
    await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
