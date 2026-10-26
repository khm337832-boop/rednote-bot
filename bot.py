import asyncio
import base64
import glob
import hashlib
import html
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import wave
from collections import deque
from contextlib import asynccontextmanager

import edge_tts
import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rednote-mm-bot")

def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v4").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_VOICE_NAME = os.environ.get("ELEVENLABS_VOICE_NAME", "Adam").strip()
GEMINI_TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Charon").strip() or "Charon"
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "auto").strip().lower()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_CLONE_ENABLED = os.environ.get("VOICE_CLONE_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
VOICE_CLONE_REFERENCE = os.environ.get(
    "VOICE_CLONE_REFERENCE",
    os.path.join(BASE_DIR, "audio", "voice01_clone_reference.wav"),
).strip()
VOICE_CLONE_CACHE_PATH = os.environ.get(
    "VOICE_CLONE_CACHE_PATH",
    os.path.join(BASE_DIR, "data", "voice_clones.json"),
).strip()
VOICE_CLONE_MAX_BYTES = env_int("VOICE_CLONE_MAX_BYTES", 20_000_000, 1_000_000)
VOICE_CLONE_MAX_SECONDS = env_int("VOICE_CLONE_MAX_SECONDS", 180, 30)
GEMINI_TTS_MODELS = [
    model.strip()
    for model in os.environ.get(
        "GEMINI_TTS_MODELS",
        "gemini-3.8-flash-tts,gemini-2.5-flash-tts",
    ).split(",")
    if model.strip()
]

MALE_VOICE = "my-MM-ThihaNeural"
SAMPLE_RATE = 24000

# Large videos are limited by available disk/API quotas rather than an arbitrary
# 10-minute application cap. Set MAX_VIDEO_MINUTES > 0 to impose a limit again.
MAX_VIDEO_MINUTES = env_float("MAX_VIDEO_MINUTES", 0.0)
DOWNLOAD_TIMEOUT = env_int("DOWNLOAD_TIMEOUT", 7200, 60)
FFMPEG_TIMEOUT = env_int("FFMPEG_TIMEOUT", 7200, 60)
TELEGRAM_UPLOAD_TIMEOUT = env_int("TELEGRAM_UPLOAD_TIMEOUT", 7200, 60)
GEMINI_INLINE_AUDIO_LIMIT = env_int("GEMINI_INLINE_AUDIO_LIMIT", 15 * 1024 * 1024, 1024)
FINAL_SIZE_RATIO = min(1.0, max(0.05, env_float("FINAL_SIZE_RATIO", 0.8)))
SCRIPT_DURATION_RATIO = min(1.0, max(0.1, env_float("SCRIPT_DURATION_RATIO", 0.8)))
TELEGRAM_PART_LIMIT_BYTES = min(
    48_000_000,
    env_int("TELEGRAM_PART_LIMIT_BYTES", 48_000_000, 1024 * 1024),
)
PROGRESS_EDIT_INTERVAL = env_float("PROGRESS_EDIT_INTERVAL", 3.0)
PROGRESS_WATCH_INTERVAL = env_int("PROGRESS_WATCH_INTERVAL", 5, 1)
PROGRESS_STALL_SECONDS = env_int("PROGRESS_STALL_SECONDS", 60, 15)
JOB_MAX_ATTEMPTS = env_int("JOB_MAX_ATTEMPTS", 3, 1)
JOB_RETRY_DELAY_SECONDS = env_int("JOB_RETRY_DELAY_SECONDS", 5, 1)
RETRY_JOB_TTL_SECONDS = env_int("RETRY_JOB_TTL_SECONDS", 86400, 60)
GEMINI_SCRIPT_MAX_PARALLEL = min(3, env_int("GEMINI_SCRIPT_MAX_PARALLEL", 3, 1))
GEMINI_SCRIPT_HELPER_DELAY_SECONDS = env_int("GEMINI_SCRIPT_HELPER_DELAY_SECONDS", 20, 1)
GEMINI_SCRIPT_MODEL_TIMEOUT = env_int("GEMINI_SCRIPT_MODEL_TIMEOUT", 180, 30)
DEEPSEEK_POLISH_TIMEOUT = env_int("DEEPSEEK_POLISH_TIMEOUT", 60, 5)
SOURCE_CREDIT_ENABLED = os.environ.get("SOURCE_CREDIT_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
COPYRIGHT_NOTICE_ENABLED = os.environ.get(
    "COPYRIGHT_NOTICE_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}

processed_updates = deque(maxlen=500)
job_semaphore = asyncio.Semaphore(1)
voice_clone_lock = asyncio.Lock()
background_jobs = set()
retry_jobs = {}
voice01_clone_id = ""


def schedule_background(coroutine):
    """Keep a strong reference so long video jobs are not garbage-collected."""
    task = asyncio.create_task(coroutine)
    background_jobs.add(task)
    task.add_done_callback(background_jobs.discard)
    return task


PREFERRED_MODELS = [
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
]


async def tg_api(method: str, data: dict = None, files: dict = None, timeout: float = 120.0):
    """Call Telegram with bounded retries, including 429 and large-upload errors."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    last_error = "Unknown Telegram error"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(4):
            try:
                if files:
                    # A failed upload may have consumed the stream; rewind before retrying.
                    for file_value in files.values():
                        if isinstance(file_value, tuple) and len(file_value) > 1:
                            stream = file_value[1]
                            if hasattr(stream, "seek"):
                                stream.seek(0)

                if files:
                    resp = await client.post(url, data=data or {}, files=files)
                else:
                    resp = await client.post(url, json=data or {})

                try:
                    result = resp.json()
                    if not isinstance(result, dict):
                        result = {"ok": False, "error": str(result)[:300]}
                except Exception:
                    result = {"ok": False, "error": resp.text[:300]}

                retry_after = result.get("parameters", {}).get("retry_after")
                if resp.status_code == 429 or result.get("error_code") == 429:
                    last_error = result.get("description", "Telegram rate limit")
                    if attempt < 3:
                        await asyncio.sleep(min(60.0, float(retry_after or (2 ** attempt + 1))))
                        continue
                elif resp.status_code >= 500 and attempt < 3:
                    last_error = result.get("description", f"HTTP {resp.status_code}")
                    await asyncio.sleep(2 ** attempt + 1)
                    continue

                if resp.status_code >= 400 and "ok" not in result:
                    result["ok"] = False
                return result
            except (httpx.HTTPError, OSError, ValueError) as exc:
                last_error = str(exc)
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt + 1)
                    continue
                logger.error("Telegram %s failed after retries: %s", method, last_error)

    return {"ok": False, "description": last_error}


def bot_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🎬 Recap စလုပ်မယ်", "callback_data": "new_recap"}],
            [
                {"text": "🎙️ Clone Voice", "callback_data": "clone_voice"},
                {"text": "🗣️ Voice-01", "callback_data": "voice01"},
            ],
            [
                {"text": "🎧 Voice Test", "callback_data": "voice_test"},
                {"text": "✨ Voice Lab", "callback_data": "voice_lab"},
            ],
            [
                {"text": "📖 အသုံးပြုပုံ", "callback_data": "help"},
                {"text": "🟢 Bot Status", "callback_data": "status"},
            ],
        ]
    }


def voice_lab_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🎙️ Clone Voice", "callback_data": "clone_voice"},
                {"text": "🎧 Voice Test", "callback_data": "voice_test"},
            ],
            [
                {"text": "🗣️ Voice-01", "callback_data": "voice01"},
                {"text": "🎚️ Charon Deep", "callback_data": "usecharon"},
            ],
            [
                {"text": "🏠 Main Menu", "callback_data": "home"},
                {"text": "🟢 Status", "callback_data": "status"},
            ],
        ]
    }


def welcome_message() -> str:
    return (
        "<b>🕵️ DYY AGENT · ALWAYS ON</b>\n"
        "<b>🎬 REDNOTE MM RECAP</b>\n"
        "<i>ဆရာ့ရဲ့ live Recap guide & monitor</i>\n\n"
        "မင်္ဂလာပါ ဆရာ။ ကျွန်တော် DYY ပါ။ ဆရာ ဘာမှ command ထပ်ရိုက်စရာမလိုပါဘူး။\n\n"
        "<b>ဆရာ အသုံးပြုရမယ့်ပုံ</b>\n"
        "<b>၁။</b> Rednote App ထဲက <b>Share → Copy Link</b> ကိုနှိပ်ပါ။\n"
        "<b>၂။</b> ရလာတဲ့ video link ကို ဒီ chat ထဲမှာ တစ်ခုချင်း ပို့ပါ။\n"
        "<b>၃။</b> Link ရောက်ပြီဆိုတာနဲ့ ကျန်တာအကုန် ကျွန်တော်က ဆက်လုပ်ပေးပါမယ်။\n\n"
        "<b>🤖 DYY က အလိုအလျောက် လုပ်ပေးမယ့်အရာတွေ</b>\n"
        "┣ 📥 Video download နဲ့ MB progress ကို စောင့်ကြည့်မယ်\n"
        "┣ ✍️ Main character ရဲ့ <b>First-person</b> မြန်မာ Recap ရေးမယ်\n"
        "┣ 🎙️ Voice-01 clone / Charon fallback နဲ့ narration လုပ်မယ်\n"
        "┣ 🎞️ Final size <b>၈၀% ခန့်</b> ထိန်းပြီး Telegram-safe MP4 ပြန်ပို့မယ်\n"
        "┣ 🛡️ 60s watchdog၊ auto retry နဲ့ error guidance ကို ကိုင်တွယ်မယ်\n"
        "┗ 📣 Stage၊ retry၊ error၊ completion အခြေအနေတွေကို ဆရာ့ကို live ပြန်ပြောမယ်\n\n"
        "ဆရာ စောင့်ရုံပါ။ Progress မရပ်သွားတာကို ကျွန်တော်က ဆက်စစ်နေပါမယ်။\n"
        "<b>📮 Link ပုံစံ:</b> <code>http://xhslink.com/...</code>"
    )


def new_recap_message() -> str:
    return (
        "<b>🕵️ DYY AGENT · NEW RECAP</b>\n"
        "<i>ဆရာ့ရဲ့ နောက်ထပ် first-person မြန်မာဇာတ်လမ်းကို စလုပ်ကြရအောင်။</i>\n\n"
        "ဆရာ၊ Rednote App ထဲက <b>Share → Copy Link</b> ကိုရွေးပြီး ရလာတဲ့ link ကို "
        "ဒီ chat ထဲမှာ ပို့လိုက်ပါ။ Link ရောက်ပြီဆိုတာနဲ့ Script၊ narration၊ final edit၊ "
        "Telegram delivery နဲ့ live monitoring ကို ဆရာ ဘာမှထပ်ပြောစရာမလိုဘဲ "
        "ကျွန်တော်က အလိုအလျောက် လုပ်ပေးပါမယ်။\n\n"
        "<code>http://xhslink.com/...</code>"
    )


def voice_clone_instructions() -> str:
    return (
        "<b>🎙️ VOICE CLONE</b>\n"
        "<i>Create your own natural recap narrator.</i>\n\n"
        "တစ်ယောက်တည်းပြောထားတဲ့ voice message / audio file ကို "
        "<code>/clonevoice</code> caption နဲ့ ပို့ပါ။ အဲဒီ audio ကို reply လုပ်ပြီး "
        "<code>/clonevoice</code> လို့လည်း ပို့နိုင်ပါတယ်။\n\n"
        "<b>အကောင်းဆုံးနမူနာ:</b> ၁–၂ မိနစ်၊ background music မပါ၊ အသံရှင်း။ "
        "အနည်းဆုံး ၁၀ စက္ကန့် လိုအပ်ပြီး ၃ မိနစ်ထက် ကြီးရင် ပထမ ၃ မိနစ်ကို သုံးပါမယ်။\n"
        "<i>သင့်အသံ သို့မဟုတ် ခွင့်ပြုချက်ရထားတဲ့ အသံကိုသာ clone လုပ်ပါ။</i>"
    )


def voice_lab_message(chat_id: int) -> str:
    active_voice = html.escape(get_chat_voice_label(chat_id))
    return (
        "<b>✨ VOICE LAB</b>\n"
        "<i>Choose it. Clone it. Hear it before your recap.</i>\n\n"
        f"┣ <b>Active narrator:</b> {active_voice}\n"
        "┣ <b>Voice Test:</b> ရွေးထားတဲ့အသံနဲ့ sample ချက်ချင်း နားထောင်နိုင်တယ်\n"
        "┣ <b>Clone Voice:</b> ကိုယ်ပိုင်/ခွင့်ပြုထားတဲ့ အသံကို clone လုပ်နိုင်တယ်\n"
        "┗ <b>Quick switch:</b> Voice-01 နဲ့ Charon Deep ကို တစ်ချက်ပြောင်းနိုင်တယ်"
    )


def help_message() -> str:
    return (
        "<b>📖 အသုံးပြုပုံ</b>\n\n"
        "<b>၁။</b> Rednote App ထဲက <b>Share → Copy Link</b> ကို ရွေးပါ။\n"
        "<b>၂။</b> ရလာတဲ့ link ကို ဒီ chat ထဲမှာ ပို့ပါ။\n"
        "<b>၃။</b> Script၊ အသံနဲ့ edit ပြီးသွားရင် final video ကို ဒီမှာ ရပါမယ်။\n\n"
        "<b>⌨️ Commands</b>\n"
        "┣ /start — Main menu ကို ပြန်ဖွင့်ရန်\n"
        "┣ /help — အသုံးပြုပုံကို ကြည့်ရန်\n"
        "┣ /status — Bot အခြေအနေကို ကြည့်ရန်\n"
        "┣ /clonevoice — ကိုယ်ပိုင်/ခွင့်ပြုထားတဲ့ အသံကို clone လုပ်ရန်\n"
        "┣ /voicetest — ရွေးထားတဲ့ narrator နဲ့ sample နားထောင်ရန်\n"
        "┣ /voice01 — Voice-01 default clone ကို ပြန်သုံးရန်\n"
        "┗ /usecharon — Charon Deep voice ကို ပြောင်းသုံးရန်\n\n"
        "<b>✨ Inline buttons</b>\n"
        "Main menu က <b>Clone Voice, Voice Test, Voice Lab, New Recap</b> ခလုတ်တွေကိုလည်း "
        "တစ်ချက်နှိပ်ပြီး အသုံးပြုနိုင်ပါတယ်။\n\n"
        "<b>🎙️ Voice clone</b>\n"
        "Voice message သို့မဟုတ် audio file ကို <code>/clonevoice</code> caption နဲ့ ပို့ပါ။ "
        "တစ်ယောက်တည်း၊ background music မပါတဲ့ ၁–၂ မိနစ် sample က အကောင်းဆုံးပါ။ "
        "သင့်အသံ သို့မဟုတ် ခွင့်ပြုချက်ရထားတဲ့ အသံကိုသာ သုံးပါ။\n\n"
        "<b>🛡️ Copyright Note</b>\n"
        "First-person recap/commentary နဲ့ source credit ထည့်ပေးပေမယ့် ၎င်းက copyright-free "
        "ဖြစ်ကြောင်း သို့မဟုတ် claim လုံးဝမဖြစ်ကြောင်း အာမမခံပါဘူး။ အသုံးပြုခွင့်ရှိတဲ့ content ကိုသာ သုံးပါ။\n\n"
        "<b>🕵️ DYY Always-On Agent</b>\n"
        "DYY ကို ON လုပ်စရာမလိုပါဘူး။ /start ကအစ link လက်ခံ၊ stage changes၊ retries၊ "
        "watchdog နဲ့ completion အထိ ဆရာ့ကို context-aware live guidance ပေးနေပါမယ်။\n\n"
        "<b>💡 Progress & Retry</b>\n"
        "Job card မှာ stage တစ်ဆင့်ချင်းနဲ့ MB/segment progress ကို ပြပါတယ်။ "
        "Progress silence ၆၀ စက္ကန့်ရှိရင် watchdog စစ်ပါတယ်။ Transient error ဆို auto retry၊ "
        "job ရပ်ရင် <b>🔄 Retry Job</b> ခလုတ်နဲ့ ပြန်စနိုင်ပါတယ်။\n\n"
        "Video ကြီးရင် progress update ကို စောင့်ပေးပါနော်။ တစ်ပြိုင်နက် job တစ်ခုချင်းစီ လုပ်ဆောင်ပါတယ်။"
    )


def status_message(chat_id: int = None) -> str:
    queue_status = "🟠 Processing" if job_semaphore.locked() else "🟢 Ready"
    deepseek_status = "On" if DEEPSEEK_API_KEY else "Off"
    elevenlabs_status = "On" if ELEVENLABS_API_KEY else "Off"
    if not VOICE_CLONE_ENABLED:
        clone_status = "Off"
        voice_label = f"{GEMINI_TTS_VOICE} + fallback"
    elif not ELEVENLABS_API_KEY:
        clone_status = "Needs ElevenLabs key"
        voice_label = f"{GEMINI_TTS_VOICE} + fallback"
    elif not os.path.isfile(VOICE_CLONE_REFERENCE):
        clone_status = "Reference missing"
        voice_label = f"{GEMINI_TTS_VOICE} + fallback"
    else:
        clone_status = "Voice-01 ready / auto-clone"
        voice_label = "Voice-01 clone → Charon fallback"
    personal_voice_line = (
        f"┣ <b>Your narrator:</b> {html.escape(get_chat_voice_label(chat_id))}\n"
        if chat_id is not None
        else ""
    )
    return (
        "<b>🟢 BOT STATUS</b>\n\n"
        f"┣ <b>System:</b> Online\n"
        f"┣ <b>Queue:</b> {queue_status}\n"
        f"┣ <b>Voice:</b> {html.escape(voice_label)}\n"
        f"{personal_voice_line}"
        f"┣ <b>Voice clone:</b> {clone_status}\n"
        f"┣ <b>Script timing:</b> Video ရဲ့ {int(SCRIPT_DURATION_RATIO * 100)}% ခန့်\n"
        f"┣ <b>DeepSeek polish:</b> {deepseek_status}\n"
        f"┣ <b>ElevenLabs fallback:</b> {elevenlabs_status}\n"
        "┣ <b>DYY Agent:</b> Always On · Live\n"
        f"┣ <b>Watchdog / Retry:</b> {PROGRESS_STALL_SECONDS}s / {JOB_MAX_ATTEMPTS} attempts\n"
        f"┗ <b>Final size:</b> မူရင်းရဲ့ {int(FINAL_SIZE_RATIO * 100)}% ခန့်\n\n"
        "<i>Link တစ်ခု ပို့ပြီး Recap စတင်နိုင်ပါပြီ။</i>"
    )


async def send_text(chat_id: int, text: str, reply_to: int = None, reply_markup: dict = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = await tg_api("sendMessage", payload)
    return res.get("result", {}).get("message_id")


async def edit_text(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    if not message_id:
        return
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        await tg_api("editMessageText", payload)
    except Exception:
        pass


async def register_webhook(base_url: str) -> dict:
    if not BOT_TOKEN or not base_url:
        return {"ok": False, "reason": "Missing BOT_TOKEN or base_url"}
    clean_base = base_url.rstrip("/")
    if clean_base.startswith("http://") and "localhost" not in clean_base and "127.0.0.1" not in clean_base:
        clean_base = "https://" + clean_base[len("http://"):]
    webhook_url = f"{clean_base}/webhook"
    res = await tg_api("setWebhook", {"url": webhook_url, "drop_pending_updates": False})
    if res.get("ok"):
        command_res = await tg_api(
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "🎬 Main menu စဖွင့်မယ်"},
                    {"command": "help", "description": "📖 အသုံးပြုပုံကြည့်မယ်"},
                    {"command": "status", "description": "🟢 Bot အခြေအနေကြည့်မယ်"},
                    {"command": "clonevoice", "description": "🎙️ ကိုယ်ပိုင်အသံ clone လုပ်မယ်"},
                    {"command": "voicetest", "description": "🎧 Narrator sample ကို နားထောင်မယ်"},
                    {"command": "voice01", "description": "🗣️ Voice-01 default ကို သုံးမယ်"},
                    {"command": "usecharon", "description": "🎚️ Charon Deep voice ကို သုံးမယ်"},
                ]
            },
        )
        if not command_res.get("ok"):
            logger.warning("Could not register Telegram command menu: %s", command_res)
    logger.info(f"Webhook set to {webhook_url}: {res}")
    return res


@asynccontextmanager
async def lifespan(app: FastAPI):
    if RENDER_EXTERNAL_URL and BOT_TOKEN:
        try:
            await register_webhook(RENDER_EXTERNAL_URL)
        except Exception as e:
            logger.error(f"Startup webhook error: {e}")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Lightweight Render health check; do not call Telegram on every probe."""
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def index(request: Request):
    base = (RENDER_EXTERNAL_URL or str(request.base_url)).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://") :]
    webhook_url = f"{base}/webhook"
    wh_status = "BOT_TOKEN မထည့်ရသေးပါ"
    wh_class = "warn"

    if BOT_TOKEN:
        try:
            res = await tg_api("getWebhookInfo")
            current_url = res.get("result", {}).get("url", "")
            if res.get("ok") and current_url == webhook_url:
                wh_status = "Webhook Active"
                wh_class = "good"
            elif res.get("ok"):
                wh_status = "Webhook Setup Pending"
            else:
                wh_status = "Telegram Check Failed"
        except Exception:
            wh_status = "Telegram Check Failed"

    page = """<!doctype html>
<html lang="my">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#090d18">
  <title>Rednote MM Recap Bot</title>
  <style>
    :root{color-scheme:dark;--bg:#090d18;--card:rgba(20,27,46,.82);--line:rgba(255,255,255,.09);--text:#f5f7ff;--muted:#a8b1c7;--purple:#9c7cff;--cyan:#54dded;--green:#58e5a5}
    *{box-sizing:border-box}
    body{margin:0;min-height:100vh;display:grid;place-items:center;padding:32px 18px;font-family:Inter,"Pyidaungsu",system-ui,-apple-system,sans-serif;color:var(--text);background:radial-gradient(circle at 12% 10%,rgba(124,88,255,.23),transparent 34%),radial-gradient(circle at 90% 80%,rgba(41,198,219,.16),transparent 35%),var(--bg)}
    .shell{width:min(920px,100%)}
    .hero{position:relative;overflow:hidden;padding:34px;border:1px solid var(--line);border-radius:28px;background:var(--card);box-shadow:0 28px 90px rgba(0,0,0,.35);backdrop-filter:blur(18px)}
    .hero:after{content:"";position:absolute;width:220px;height:220px;right:-100px;top:-110px;border-radius:50%;background:linear-gradient(135deg,var(--purple),var(--cyan));filter:blur(18px);opacity:.18}
    .top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
    .brand{display:flex;align-items:center;gap:14px}
    .logo{display:grid;place-items:center;width:52px;height:52px;border-radius:16px;font-size:26px;background:linear-gradient(145deg,rgba(156,124,255,.28),rgba(84,221,237,.14));border:1px solid rgba(255,255,255,.13)}
    .eyebrow{margin:0 0 4px;color:var(--cyan);font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}
    h1{margin:0;font-size:clamp(23px,4vw,34px);letter-spacing:-.04em}
    .pill{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid rgba(88,229,165,.2);border-radius:999px;background:rgba(88,229,165,.08);color:var(--green);font-size:12px;font-weight:800;letter-spacing:.06em}
    .dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 12px currentColor}
    .lead{max-width:630px;margin:24px 0 0;color:var(--muted);font-size:15px;line-height:1.85}
    .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:28px}
    .feature{padding:18px 16px;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.025)}
    .feature b{display:block;margin:9px 0 5px;font-size:13px}
    .feature span{color:var(--muted);font-size:12px;line-height:1.6}
    .icon{font-size:22px}
    .system{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:14px;padding:16px 18px;border:1px solid var(--line);border-radius:16px;background:rgba(0,0,0,.14);font-size:12px}
    .system .label{color:var(--muted)}
    .system strong{display:block;margin-top:5px;font-size:13px}
    .state{text-align:right;font-weight:800}
    .state.good{color:var(--green)}
    .state.warn{color:#ffd47c}
    .endpoint{margin-top:12px;padding:14px 18px;border-radius:14px;background:rgba(0,0,0,.2);color:var(--muted);font-size:11px;overflow-wrap:anywhere}
    code{color:#c9bcff;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
    footer{padding:18px 6px 0;text-align:center;color:#717c94;font-size:11px;letter-spacing:.04em}
    @media(max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}.hero{padding:24px}.system{align-items:flex-start;flex-direction:column}.state{text-align:left}}
    @media(max-width:390px){.grid{grid-template-columns:1fr}.hero{padding:20px;border-radius:22px}}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="top">
        <div class="brand">
          <div class="logo">🎬</div>
          <div><p class="eyebrow">AI Video Studio</p><h1>Rednote MM Recap</h1></div>
        </div>
        <div class="pill"><span class="dot"></span> SYSTEM ONLINE</div>
      </div>
      <p class="lead">Rednote link တစ်ခုထည့်လိုက်ရုံနဲ့ <b>First-person မြန်မာ Recap</b>၊ Voice-01 clone နဲ့ Telegram-ready final video ကို အလိုအလျောက် ပြုလုပ်ပေးပါတယ်။</p>
      <div class="grid">
        <div class="feature"><div class="icon">✍️</div><b>First-person Script</b><span>First-person နဲ့ ဇာတ်လမ်းအပြည့်ကို ၈၀% narration budget နဲ့ အကျဉ်းချုပ်ပါတယ်။</span></div>
        <div class="feature"><div class="icon">🎙️</div><b>Voice Clone</b><span>Voice-01 / custom clone၊ Voice Test နဲ့ Charon / Edge fallback ပါပါတယ်။</span></div>
        <div class="feature"><div class="icon">🎞️</div><b>Smart Export</b><span>Duration မလျှော့ဘဲ final size ကို ၈၀% ခန့် ထိန်းပါတယ်။</span></div>
        <div class="feature"><div class="icon">📤</div><b>Auto Delivery</b><span>50MB ကျော်ရင် playable MP4 အပိုင်းလိုက် ပို့ပါတယ်။</span></div>
      </div>
      <div class="system">
        <div><span class="label">TELEGRAM CONNECTION</span><strong>Bot API / Webhook</strong></div>
        <div class="state __WH_CLASS__">__WH_STATUS__</div>
      </div>
      <div class="endpoint">WEBHOOK ENDPOINT<br><code>__WEBHOOK_URL__</code></div>
    </section>
    <footer>FASTAPI · RENDER · TELEGRAM BOT API</footer>
  </main>
</body>
</html>"""
    page = page.replace("__WH_CLASS__", wh_class)
    page = page.replace("__WH_STATUS__", html.escape(wh_status))
    page = page.replace("__WEBHOOK_URL__", html.escape(webhook_url))
    return HTMLResponse(content=page)


def find_telegram_voice_sample(message: dict) -> dict:
    """Accept Telegram voice/audio messages and audio documents only."""
    if not isinstance(message, dict):
        return {}
    for media_type in ("voice", "audio"):
        media = message.get(media_type) or {}
        if media.get("file_id"):
            return {
                "file_id": media["file_id"],
                "file_size": media.get("file_size") or 0,
                "file_name": media.get("file_name") or f"telegram_{media_type}.ogg",
                "mime_type": media.get("mime_type") or "audio/ogg",
            }

    document = message.get("document") or {}
    file_name = str(document.get("file_name") or "")
    mime_type = str(document.get("mime_type") or "")
    audio_extension = os.path.splitext(file_name.lower())[1] in {
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
    }
    if document.get("file_id") and (mime_type.startswith("audio/") or audio_extension):
        return {
            "file_id": document["file_id"],
            "file_size": document.get("file_size") or 0,
            "file_name": file_name or "telegram_audio",
            "mime_type": mime_type or "application/octet-stream",
        }
    return {}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    update_id = update.get("update_id")
    if update_id in processed_updates:
        return JSONResponse({"ok": True})
    if update_id is not None:
        processed_updates.append(update_id)

    callback = update.get("callback_query")
    if callback:
        callback_id = callback.get("id")
        if callback_id:
            await tg_api("answerCallbackQuery", {"callback_query_id": callback_id})
        callback_message = callback.get("message") or {}
        chat_id = callback_message.get("chat", {}).get("id")
        action = callback.get("data")
        if chat_id:
            reply_id = callback_message.get("message_id")
            if action and action.startswith("retry:"):
                retry_id = action.split(":", 1)[1]
                retry_data = get_retry_job(retry_id, chat_id)
                if retry_data:
                    retry_jobs.pop(retry_id, None)
                    schedule_background(
                        handle_video_job(
                            chat_id,
                            retry_data["reply_msg_id"],
                            retry_data["url"],
                            job_id=retry_id,
                        )
                    )
                else:
                    schedule_background(
                        send_text(
                            chat_id,
                            "<b>⌛ RETRY EXPIRED</b>\n"
                            "ဒီ retry link က သက်တမ်းကုန် သို့မဟုတ် server restart ဖြစ်သွားပါတယ်။ "
                            "Rednote link ကို ပြန်ပို့ပြီး job အသစ်စတင်ပါ။",
                            reply_markup=bot_menu_keyboard(),
                        )
                    )
            elif action == "home":
                schedule_background(handle_dyy_start(chat_id, reply_id))
            elif action in {"help", "status"}:
                text = status_message(chat_id) if action == "status" else help_message()
                schedule_background(
                    send_text(
                        chat_id,
                        text,
                        reply_markup=bot_menu_keyboard(),
                    )
                )
            elif action == "new_recap":
                schedule_background(
                    send_text(chat_id, new_recap_message(), reply_markup=bot_menu_keyboard())
                )
            elif action == "clone_voice":
                schedule_background(
                    send_text(
                        chat_id,
                        voice_clone_instructions(),
                        reply_markup=voice_lab_keyboard(),
                    )
                )
            elif action == "voice_lab":
                schedule_background(
                    send_text(
                        chat_id,
                        voice_lab_message(chat_id),
                        reply_markup=voice_lab_keyboard(),
                    )
                )
            elif action == "voice_test":
                schedule_background(handle_voice_preview(chat_id, reply_id))
            elif action == "voice01":
                schedule_background(handle_select_voice01(chat_id, reply_id))
            elif action == "usecharon":
                schedule_background(handle_select_charon(chat_id, reply_id))
        return JSONResponse({"ok": True})

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return JSONResponse({"ok": True})

    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    msg_id = msg.get("message_id")
    if not chat_id or not text:
        return JSONResponse({"ok": True})

    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command == "/start":
        schedule_background(handle_dyy_start(chat_id, msg_id))
        return JSONResponse({"ok": True})

    if command in {"/help", "/status"}:
        text = status_message(chat_id) if command == "/status" else help_message()
        schedule_background(
            send_text(
                chat_id,
                text,
                reply_to=msg_id,
                reply_markup=bot_menu_keyboard(),
            )
        )
        return JSONResponse({"ok": True})

    if command == "/clonevoice":
        sample = find_telegram_voice_sample(msg)
        if not sample:
            sample = find_telegram_voice_sample(msg.get("reply_to_message") or {})
        if sample:
            schedule_background(handle_voice_clone_job(chat_id, msg_id, sample))
        else:
            schedule_background(
                send_text(
                    chat_id,
                    voice_clone_instructions(),
                    reply_to=msg_id,
                    reply_markup=voice_lab_keyboard(),
                )
            )
        return JSONResponse({"ok": True})

    if command == "/voice01":
        schedule_background(handle_select_voice01(chat_id, msg_id))
        return JSONResponse({"ok": True})

    if command == "/usecharon":
        schedule_background(handle_select_charon(chat_id, msg_id))
        return JSONResponse({"ok": True})

    if command == "/voicetest":
        schedule_background(handle_voice_preview(chat_id, msg_id))
        return JSONResponse({"ok": True})

    url_match = re.search(r"https?://[^\s，。！？,<>\"'；：）】]+", text)
    if not url_match:
        schedule_background(
            send_text(
                chat_id,
                "<b>🕵️ DYY AGENT · LIVE GUIDE</b>\n"
                "ဆရာ၊ Recap စလုပ်ဖို့ <b>Rednote video link</b> လိုပါတယ်။\n\n"
                "Rednote App ထဲက <b>Share → Copy Link</b> နှိပ်ပြီး "
                "ရလာတဲ့ link ကို ဒီထဲမှာ ပို့ပါ။ Link ရောက်ရင် ဆရာ ဘာမှထပ်ပြောစရာမလိုဘဲ "
                "ကျွန်တော်က အလိုအလျောက် ဆက်လုပ်ပေးပါမယ်။\n"
                "<code>http://xhslink.com/...</code>",
                reply_to=msg_id,
                reply_markup=bot_menu_keyboard(),
            )
        )
        return JSONResponse({"ok": True})

    raw_url = url_match.group(0).rstrip(".,!?;:)")
    schedule_background(handle_video_job(chat_id, msg_id, raw_url))
    return JSONResponse({"ok": True})


def run_cmd(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


async def download_telegram_voice_sample(sample: dict, out_path: str) -> None:
    """Download a bounded Telegram voice sample without exposing the bot token."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required to download a voice sample")
    declared_size = int(sample.get("file_size") or 0)
    if declared_size > VOICE_CLONE_MAX_BYTES:
        raise RuntimeError("Voice sample must be 20MB or smaller")

    file_info = await tg_api("getFile", {"file_id": sample["file_id"]}, timeout=60.0)
    if not file_info.get("ok"):
        raise RuntimeError("Telegram could not provide this voice sample")
    result = file_info.get("result") or {}
    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        raise RuntimeError("Telegram returned no downloadable voice file")
    if int(result.get("file_size") or 0) > VOICE_CLONE_MAX_BYTES:
        raise RuntimeError("Voice sample must be 20MB or smaller")

    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", download_url) as response:
                if response.status_code != 200:
                    raise RuntimeError("Telegram voice sample download failed")
                total_bytes = 0
                with open(out_path, "wb") as output_file:
                    async for chunk in response.aiter_bytes(65536):
                        total_bytes += len(chunk)
                        if total_bytes > VOICE_CLONE_MAX_BYTES:
                            raise RuntimeError("Voice sample must be 20MB or smaller")
                        output_file.write(chunk)
    except (httpx.HTTPError, OSError):
        raise RuntimeError("Telegram voice sample download failed") from None
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1000:
        raise RuntimeError("The downloaded voice sample is empty or unsupported")


async def download_rednote_video(raw_url: str, out_path: str) -> bool:
    mobile_headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    desktop_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    expanded_url = raw_url
    direct_video_urls = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        for hdrs in (mobile_headers, desktop_headers):
            try:
                r = await client.get(expanded_url, headers=hdrs)
                expanded_url = str(r.url)
                html = r.text

                # 1. Check og:video meta tag
                og_matches = re.findall(
                    r'<meta[^>]+(?:name|property)=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']',
                    html,
                    flags=re.I,
                )
                og_matches += re.findall(
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']og:video["\']',
                    html,
                    flags=re.I,
                )
                for u in og_matches:
                    u_clean = u.replace("\\u002F", "/").replace("&amp;", "&")
                    if u_clean.startswith("http"):
                        direct_video_urls.append(u_clean)

                # 2. Check masterUrl / originVideoKey inside __INITIAL_STATE__
                master_matches = re.findall(r'"masterUrl"\s*:\s*"([^"]+)"', html)
                for m in master_matches:
                    u_clean = m.encode("utf-8").decode("unicode_escape", errors="ignore").replace("\\u002F", "/")
                    if u_clean.startswith("http"):
                        direct_video_urls.append(u_clean)

                origin_keys = re.findall(r'"originVideoKey"\s*:\s*"([^"]+)"', html)
                for key in origin_keys:
                    k_clean = key.encode("utf-8").decode("unicode_escape", errors="ignore").replace("\\u002F", "/")
                    direct_video_urls.append(f"https://sns-video-bd.xhscdn.com/{k_clean}")

                cdn_matches = re.findall(r"https?:(?:\\u002F|/){2}sns-video[^\s\"'<>\\]+", html)
                for c in cdn_matches:
                    direct_video_urls.append(c.replace("\\u002F", "/"))

                if direct_video_urls:
                    break
            except Exception as e:
                logger.warning(f"HTML scrape attempt warning: {e}")

        # Try downloading extracted direct CDN URLs first
        seen = set()
        for vurl in direct_video_urls:
            if vurl in seen:
                continue
            seen.add(vurl)
            try:
                dl_headers = {
                    "User-Agent": mobile_headers["User-Agent"],
                    "Referer": "https://www.xiaohongshu.com/",
                }
                async with client.stream("GET", vurl, headers=dl_headers) as resp:
                    if resp.status_code in (200, 206):
                        with open(out_path, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 20_000:
                            logger.info(f"Downloaded directly from CDN: {vurl[:80]}")
                            return True
            except Exception as e:
                logger.warning(f"Direct CDN download failed: {e}")

    # Fallback to yt-dlp (try expanded_url and xiaohongshu domain normalized URL)
    candidate_urls = [expanded_url]
    if "rednote.com" in expanded_url:
        candidate_urls.append(expanded_url.replace("rednote.com", "xiaohongshu.com"))
    if raw_url not in candidate_urls:
        candidate_urls.append(raw_url)

    for u in candidate_urls:
        if os.path.exists(out_path):
            os.remove(out_path)
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "-f",
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "-o",
            out_path,
            u,
        ]
        proc = await asyncio.to_thread(run_cmd, cmd, DOWNLOAD_TIMEOUT)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 20_000:
            logger.info(f"Downloaded via yt-dlp: {u[:80]}")
            return True
        logger.warning(f"yt-dlp failed for {u}: {proc.stderr.decode(errors='ignore')[:250]}")

    return False


def get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = run_cmd(cmd, 30)
    try:
        return float(proc.stdout.decode().strip())
    except Exception:
        return 0.0


async def prepare_voice_clone_sample(source_path: str, out_path: str) -> float:
    """Normalize a Telegram sample to a clean mono MP3 and cap cloning cost."""
    proc = await asyncio.to_thread(
        run_cmd,
        [
            "ffmpeg",
            "-y",
            "-i",
            source_path,
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            str(VOICE_CLONE_MAX_SECONDS),
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            out_path,
        ],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0 or not os.path.isfile(out_path):
        logger.warning("Voice sample conversion failed: %s", proc.stderr[-200:])
        raise RuntimeError("This audio format could not be converted for voice cloning")
    duration = await asyncio.to_thread(get_media_duration, out_path)
    if duration < 10.0:
        raise RuntimeError("Voice sample must contain at least 10 seconds of clear speech")
    return duration


def estimate_segment_pitch_hz(wav_samples_8k: np.ndarray, start: float, end: float) -> float:
    sr = 8000
    s_idx = max(0, int(start * sr))
    e_idx = min(len(wav_samples_8k), int(end * sr))
    seg = wav_samples_8k[s_idx:e_idx].astype(np.float32)
    if np.issubdtype(wav_samples_8k.dtype, np.integer):
        seg /= float(np.iinfo(wav_samples_8k.dtype).max) + 1.0
    if len(seg) < sr // 4:
        return 0.0
    frame_len = int(0.04 * sr)  # 320 samples (40ms)
    hop = int(0.02 * sr)
    min_lag = int(sr / 300)  # 300 Hz max F0
    max_lag = int(sr / 75)   # 75 Hz min F0
    f0s = []
    for idx in range(0, len(seg) - frame_len, hop):
        frame = seg[idx : idx + frame_len]
        frame = frame - np.mean(frame)
        energy = np.mean(frame * frame)
        if energy < 1e-4:
            continue
        corr = np.correlate(frame, frame, mode="full")[frame_len - 1 :]
        if max_lag >= len(corr):
            continue
        sub = corr[min_lag:max_lag]
        if len(sub) == 0:
            continue
        peak_idx = int(np.argmax(sub)) + min_lag
        if corr[peak_idx] > 0.32 * corr[0]:
            f0s.append(sr / peak_idx)
    if len(f0s) < 3:
        return 0.0
    return float(np.median(f0s))


MYANMAR_DIGITS = str.maketrans("0123456789", "၀၁၂၃၄၅၆၇၈၉")


def clean_burmese_for_tts(text: str) -> str:
    t = re.sub(r"[*_#`~<>[\]()]", " ", text)
    t = t.translate(MYANMAR_DIGITS)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def build_recap_script_prompt(total_dur: float) -> str:
    """Build a recap told entirely from the protagonist's first-person view."""
    target_narration_dur = total_dur * SCRIPT_DURATION_RATIO
    return f"""
You are a professional Myanmar story-recap scriptwriter and audio analyst.
Listen to the ENTIRE {total_dur:.1f}-second audio clip before writing anything.
Create an ORIGINAL Myanmar recap narration, NOT a line-by-line translation,
subtitle file, or summary of only the dialogue.
Your narration-time budget is approximately {target_narration_dur:.1f} seconds,
which is {int(SCRIPT_DURATION_RATIO * 100)}% of the source video. Condense the
COMPLETE story into that budget; this is not permission to stop before the ending.

NARRATIVE STYLE:
1. Open with a short, gripping hook based on a crucial event or consequence from
   the story. Then smoothly rewind to the background and tell the rest in clear
   chronological order. Do not use a greeting or a channel introduction.
2. Tell the ENTIRE recap from the main protagonist's FIRST-PERSON point of view,
   from the hook through the ending. The protagonist is telling their own story.
   Use "ကျွန်တော်/ငါ" for a male protagonist or "ကျွန်မ/ငါ" for a female
   protagonist consistently. If gender is unclear, use "ငါ" consistently.
3. NEVER switch to an outside or third-person narrator. Narration, backstory,
   explanations, inner thoughts, and the protagonist's dialogue must all keep
   the same first-person voice. Do not refer to the protagonist as "သူ/သူမ" as
   if someone else were narrating. Refer to other people by name or relationship,
   and summarize their dialogue through the protagonist's first-person account.
4. Retell the complete cause-and-effect story: important background, conflicts,
   turning points, discoveries, and the ending. Combine repetitive lines and
   remove filler. Never invent events, motives, dialogue, powers, or facts.
5. Write 100% natural, smooth colloquial spoken Burmese
   (မြန်မာအပြောစကားစစ်စစ်), with emotional reactions and natural transitions
   where appropriate. Add tasteful rhetorical questions, reactions, and smooth
   transitions when they improve the recap, but do not add unsupported jokes,
   opinions, events, or dialogue.
6. NEVER use stiff literary Burmese ("သည်", "၏", "၌", "၎င်း", "သောကြောင့်",
   "ဖြစ်ပေသည်"). Prefer natural spoken endings and connectors such as "တယ်",
   "နော်", "ပေါ့", "လေ", "အဲဒါကြောင့်", and "ဆိုတော့" only where they fit.
7. Write names, numbers, English words, and brands phonetically in Myanmar
   Unicode script. Keep each character's name consistent throughout the recap.
8. Do not write production notes, scene descriptions, headings, explanations,
   "ဒီဗီဒီယိုမှာ", like/subscribe requests, or a closing call to action.

TIMESTAMPS AND VOICES:
1. Divide the FINISHED recap into natural narration blocks aligned as closely as
   possible with the corresponding source moments. Each block should normally
   cover about 3-10 seconds. Use ordered, non-overlapping `start` and `end`
   timestamps in seconds within 0.0 to {total_dur:.1f}. Aim for the SUM of all
   block durations to be about {target_narration_dur:.1f} seconds. Spread blocks
   across the FULL source timeline and leave the remaining time for natural
   pauses/transitions; do not compress everything into only the opening portion.
2. Each block's Burmese text must be concise enough to speak naturally within
   `(end - start)` seconds. Do not output the whole recap as one huge block.
3. For EACH block, label `gender` as `"male"`. The same Charon deep-voiced
   first-person narrator reads the entire recap without switching narrators.
4. If the audio contains no speech or usable story information, return `[]`.

Return ONLY valid JSON matching this exact structure (no markdown fences):
[
  {{
    "start": 0.0,
    "end": 6.5,
    "gender": "male",
    "burmese": "ကျွန်တော်က အဲဒီအဖြေကို လက်မခံဘူး။ ကျွန်တော် ပြန်စမ်းဦးမယ်လို့ စိတ်ထဲမှာ ဆုံးဖြတ်လိုက်တယ်။"
  }}
]
""".strip()


def fit_recap_narration_windows(
    segments: list[dict],
    total_dur: float,
    ratio: float = SCRIPT_DURATION_RATIO,
) -> list[dict]:
    """Keep narration windows within budget while retaining full-timeline coverage."""
    if not segments or total_dur <= 0 or ratio >= 1.0:
        return segments

    durations = [max(0.05, float(seg["end"]) - float(seg["start"])) for seg in segments]
    allocated = sum(durations)
    target = max(0.05, total_dur * ratio)
    if allocated <= target:
        return segments

    scale = target / allocated
    new_durations = [duration * scale for duration in durations]
    gaps = [max(0.0, float(segments[0]["start"]))]
    for previous, current in zip(segments, segments[1:]):
        gaps.append(max(0.0, float(current["start"]) - float(previous["end"])))
    gaps.append(max(0.0, total_dur - float(segments[-1]["end"])))

    remaining = max(0.0, total_dur - target)
    original_gap_total = sum(gaps)
    if original_gap_total > 0:
        new_gaps = [gap / original_gap_total * remaining for gap in gaps]
    else:
        new_gaps = [remaining / (len(segments) + 1)] * (len(segments) + 1)

    fitted = []
    cursor = new_gaps[0]
    for index, (segment, duration) in enumerate(zip(segments, new_durations)):
        start = min(total_dur, cursor)
        end = min(total_dur, start + duration)
        fitted.append({**segment, "start": start, "end": end})
        cursor = end + new_gaps[index + 1]
    return fitted


def parse_json_payload(raw_text: str):
    """Parse model JSON while tolerating Markdown fences and surrounding text."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [idx for idx in (cleaned.find("["), cleaned.find("{")) if idx >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("]"), cleaned.rfind("}"))
        if end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


async def prepare_gemini_audio(client, audio_mp3_path: str):
    """Use inline audio for small files and Gemini Files API for large uploads."""
    if os.path.getsize(audio_mp3_path) <= GEMINI_INLINE_AUDIO_LIMIT:
        with open(audio_mp3_path, "rb") as audio_file:
            audio_bytes = audio_file.read()
        return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mp3"), None

    uploaded = await asyncio.to_thread(client.files.upload, file=audio_mp3_path)
    uploaded_name = getattr(uploaded, "name", None)
    if not uploaded_name:
        raise RuntimeError("Gemini Files API did not return an uploaded file name")

    try:
        # Audio is often ACTIVE immediately. Poll only while the provider says it
        # is processing, and pass the file object rather than memory bytes.
        for _ in range(150):
            state = str(getattr(uploaded, "state", "")).upper()
            if "FAILED" in state:
                raise RuntimeError(f"Gemini audio upload processing failed: {state}")
            if "PROCESSING" not in state:
                logger.info("Using Gemini Files API for a large audio input")
                return uploaded, uploaded_name
            await asyncio.sleep(2)
            uploaded = await asyncio.to_thread(client.files.get, name=uploaded_name)

        raise TimeoutError("Timed out while waiting for Gemini audio processing")
    except Exception:
        # If polling fails, ownership has not been handed to the caller yet.
        try:
            await asyncio.to_thread(client.files.delete, name=uploaded_name)
        except Exception:
            pass
        raise


async def request_gemini_recap_model(client, audio_part, prompt: str, cfg, model_name: str) -> str:
    """Use the async SDK so slow helper requests can actually be cancelled."""
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=model_name,
            contents=[audio_part, prompt],
            config=cfg,
        ),
        timeout=GEMINI_SCRIPT_MODEL_TIMEOUT,
    )
    text = str(getattr(response, "text", "") or "").strip() if response else ""
    if not text:
        raise ValueError("Gemini returned an empty recap")
    payload = parse_json_payload(text)
    items = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Gemini recap response is not a JSON segment array")
    return text


async def race_gemini_recap_models(
    client,
    audio_part,
    prompt: str,
    cfg,
    model_names: list[str],
    progress_callback=None,
) -> str:
    """Start helpers only for slow/failed models; first successful draft wins."""
    candidates = list(dict.fromkeys(name for name in model_names if name))
    if not candidates:
        raise RuntimeError("No Gemini recap models are available")

    pending = {}
    errors = []
    next_index = 0

    async def start_next_model():
        nonlocal next_index
        if next_index >= len(candidates):
            return False
        model_name = candidates[next_index]
        next_index += 1
        task = asyncio.create_task(
            request_gemini_recap_model(client, audio_part, prompt, cfg, model_name)
        )
        pending[task] = model_name
        if progress_callback:
            try:
                await progress_callback(model_name, len(pending))
            except Exception as exc:
                logger.warning("Script progress update failed: %s", str(exc)[:100])
        return True

    try:
        await start_next_model()
        while pending:
            done, _ = await asyncio.wait(
                set(pending),
                timeout=GEMINI_SCRIPT_HELPER_DELAY_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # Do not spend extra quota on a fast primary. A helper is added
                # only after the configured slow-response delay.
                if len(pending) < GEMINI_SCRIPT_MAX_PARALLEL:
                    await start_next_model()
                continue

            successful_text = ""
            successful_model = ""
            failure_count = 0
            for task in done:
                model_name = pending.pop(task)
                try:
                    text = task.result()
                    if not successful_text:
                        successful_text = text
                        successful_model = model_name
                except Exception as exc:
                    failure_count += 1
                    errors.append(f"{model_name}: {str(exc)[:140]}")
                    logger.warning("Model %s failed: %s", model_name, str(exc)[:160])

            if successful_text:
                logger.info("Gemini recap succeeded with model: %s", successful_model)
                return successful_text

            # Replace failures immediately; healthy in-flight requests continue.
            for _ in range(failure_count):
                if not await start_next_model():
                    break

    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    raise RuntimeError("; ".join(errors[-3:]) or "All Gemini recap models failed")


async def generate_recap_response(
    client,
    audio_part,
    prompt: str,
    cfg,
    progress_callback=None,
) -> str:
    last_err = None
    try:
        return await race_gemini_recap_models(
            client,
            audio_part,
            prompt,
            cfg,
            list(PREFERRED_MODELS),
            progress_callback=progress_callback,
        )
    except Exception as exc:
        last_err = exc
        logger.warning("Preferred Gemini recap models failed: %s", str(exc)[:180])

    # Discover an available non-TTS flash model if the preferred names change.
    try:
        available = await asyncio.to_thread(client.models.list)
        discovered = []
        for model in available:
            model_name = getattr(model, "name", "").replace("models/", "")
            if (
                "flash" in model_name
                and "tts" not in model_name
                and "image" not in model_name
                and model_name not in PREFERRED_MODELS
            ):
                discovered.append(model_name)
        if discovered:
            return await race_gemini_recap_models(
                client,
                audio_part,
                prompt,
                cfg,
                discovered,
                progress_callback=progress_callback,
            )
    except Exception as exc:
        last_err = exc

    raise RuntimeError(f"Gemini API failed: {last_err}")


async def polish_recap_with_deepseek(segments: list[dict], total_dur: float) -> list[dict]:
    """Optionally polish Burmese prose without changing timing or source facts."""
    if not DEEPSEEK_API_KEY or not segments:
        return segments

    source_json = json.dumps(segments, ensure_ascii=False, separators=(",", ":"))
    if len(source_json) > 180_000:
        logger.info("Skipping DeepSeek polish for an exceptionally long recap")
        return segments

    target_narration_dur = total_dur * SCRIPT_DURATION_RATIO
    instruction = f"""
You are a senior Myanmar recap editor. Edit the `burmese` fields in the JSON below.
Keep the same number of items, order, `start`, `end`, and `gender` values.
The total video duration is {total_dur:.1f} seconds.

Style requirements:
- The ENTIRE recap is the main protagonist telling their own story in FIRST PERSON
  (ကျွန်တော် / ကျွန်မ / ငါ), from beginning to end.
- Never use an outside or third-person narrator. Keep narration, backstory,
  explanations, inner thoughts, and dialogue in the same first-person voice.
  Do not refer to the protagonist as သူ/သူမ as if someone else were narrating.
- Use smooth, engaging, colloquial spoken Burmese, not stiff literary Burmese.
- Preserve every source fact. Do not add events, dialogue, names, or opinions.
- Keep each line short enough for its existing `(end - start)` timing. The total
  narration budget is about {target_narration_dur:.1f} seconds; shorten verbose
  prose where needed while preserving the complete story and its ending.
- Use Myanmar Unicode for names, numbers, English words, and brands.
Return ONLY the edited JSON array, with no Markdown or explanation.

Input JSON:
{source_json}
""".strip()

    models = list(dict.fromkeys([DEEPSEEK_MODEL, "deepseek-chat"]))
    async with httpx.AsyncClient(timeout=240.0) as client:
        for model in models:
            if not model:
                continue
            try:
                response = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": instruction}],
                        "temperature": 0.3,
                        "max_tokens": min(16384, max(4096, len(segments) * 90)),
                        "stream": False,
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
                payload = response.json()
                raw_text = payload["choices"][0]["message"]["content"]
                edited = parse_json_payload(raw_text)
                if isinstance(edited, dict):
                    edited = edited.get("segments", [])
                if not isinstance(edited, list) or len(edited) != len(segments):
                    raise ValueError("DeepSeek changed the recap item count")

                polished = []
                for original, candidate in zip(segments, edited):
                    burmese = clean_burmese_for_tts(str(candidate.get("burmese", "")))
                    polished.append({**original, "burmese": burmese or original["burmese"]})
                logger.info("DeepSeek recap polish succeeded with model: %s", model)
                return polished
            except Exception as exc:
                logger.warning("DeepSeek polish with %s failed: %s", model, str(exc)[:160])

    return segments


async def polish_recap_with_deadline(segments: list[dict], total_dur: float) -> list[dict]:
    """Never let optional prose polishing hold the completed Gemini draft hostage."""
    if not DEEPSEEK_API_KEY or not segments:
        return segments
    try:
        return await asyncio.wait_for(
            polish_recap_with_deepseek(segments, total_dur),
            timeout=DEEPSEEK_POLISH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "DeepSeek polish exceeded %ss; using the completed Gemini draft",
            DEEPSEEK_POLISH_TIMEOUT,
        )
        return segments


async def create_recap_script_gemini(
    audio_mp3_path: str,
    total_dur: float,
    wav_8k: np.ndarray,
    progress_callback=None,
) -> list[dict]:
    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options={"timeout": GEMINI_SCRIPT_MODEL_TIMEOUT * 1000},
    )
    prompt = build_recap_script_prompt(total_dur)
    cfg = types.GenerateContentConfig(
        temperature=0.25,
        response_mime_type="application/json",
    )

    uploaded_name = None

    async def model_progress(model_name, active_requests):
        if progress_callback:
            await progress_callback(
                f"Gemini draft · {model_name} · {active_requests} active request(s)…"
            )

    try:
        audio_part, uploaded_name = await prepare_gemini_audio(client, audio_mp3_path)
        raw_json_text = await generate_recap_response(
            client,
            audio_part,
            prompt,
            cfg,
            progress_callback=model_progress if progress_callback else None,
        )
        if progress_callback:
            await progress_callback("Gemini draft received · validating story and timestamps…")
    finally:
        if uploaded_name:
            try:
                await asyncio.to_thread(client.files.delete, name=uploaded_name)
            except Exception as exc:
                logger.warning("Could not delete temporary Gemini upload: %s", str(exc)[:120])

    segments = parse_json_payload(raw_json_text)
    if not isinstance(segments, list):
        segments = segments.get("segments", []) if isinstance(segments, dict) else []

    valid_segments = []
    for seg in segments:
        try:
            start = max(0.0, float(seg.get("start", 0.0)))
            end = min(total_dur, float(seg.get("end", start + 2.0)))
            if end <= start:
                end = min(total_dur, start + 1.5)
            burmese = clean_burmese_for_tts(str(seg.get("burmese", "")))
            if not burmese:
                continue

            ai_gender = str(seg.get("gender", "male")).lower().strip()
            pitch_hz = estimate_segment_pitch_hz(wav_8k, start, end)

            # Retain the original acoustic check for optional character voices.
            if ai_gender in ("male", "m"):
                gender = "male"
                if pitch_hz > 215.0:
                    gender = "female"
            else:
                gender = "female"
                if 75.0 <= pitch_hz <= 145.0:
                    gender = "male"

            valid_segments.append(
                {
                    "start": start,
                    "end": end,
                    "gender": gender,
                    "burmese": burmese,
                }
            )
        except Exception as exc:
            logger.warning("Skipping malformed segment %s: %s", seg, exc)

    valid_segments.sort(key=lambda item: item["start"])
    valid_segments = fit_recap_narration_windows(valid_segments, total_dur)
    if DEEPSEEK_API_KEY and valid_segments and progress_callback:
        await progress_callback(
            f"Gemini draft ready · optional DeepSeek polish (max {DEEPSEEK_POLISH_TIMEOUT}s)…"
        )
    return await polish_recap_with_deadline(valid_segments, total_dur)


def extract_gemini_audio_bytes(response) -> bytes:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if not data:
                continue
            if isinstance(data, str):
                return base64.b64decode(data)
            return bytes(data)
    return b""


async def synthesize_gemini_tts(text: str, out_path: str) -> bool:
    """Generate the requested Charon narration with Gemini TTS."""
    if not GEMINI_API_KEY:
        return False

    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": text,
                    "speech_metadata": {
                        "style": (
                            "Deep, low, calm and authoritative male narration. "
                            "Natural Burmese pronunciation, steady cinematic recap pace."
                        )
                    },
                }
            ],
        }
    ]
    cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config={
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": GEMINI_TTS_VOICE},
            }
        },
    )

    for model in GEMINI_TTS_MODELS:
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                # Gemini 3.8 uses structured speech metadata; older fallback
                # models expect the verbatim text without that new field.
                contents=contents if "3.8" in model else text,
                config=cfg,
            )
            audio_bytes = extract_gemini_audio_bytes(response)
            if len(audio_bytes) < 200:
                raise ValueError("Gemini TTS returned no usable audio")
            if audio_bytes.startswith(b"RIFF"):
                with open(out_path, "wb") as output_file:
                    output_file.write(audio_bytes)
            else:
                # Gemini 2.5 can return headerless 24kHz mono PCM (L16).
                with wave.open(out_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(SAMPLE_RATE)
                    wav_file.writeframes(audio_bytes)
            logger.info("Gemini TTS succeeded with %s / %s", model, GEMINI_TTS_VOICE)
            return True
        except Exception as exc:
            logger.warning("Gemini TTS model %s failed: %s", model, str(exc)[:160])
    return False


def load_voice_clone_state() -> dict:
    """Load only voice IDs/cache metadata; reference audio is never persisted here."""
    if not VOICE_CLONE_CACHE_PATH:
        return {"chats": {}}
    try:
        with open(VOICE_CLONE_CACHE_PATH, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        if not isinstance(state, dict):
            raise ValueError("Voice clone cache must be a JSON object")
        if not isinstance(state.get("chats"), dict):
            state["chats"] = {}
        return state
    except FileNotFoundError:
        return {"chats": {}}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Could not read voice clone cache: %s", str(exc)[:120])
        return {"chats": {}}


def save_voice_clone_state(state: dict) -> None:
    if not VOICE_CLONE_CACHE_PATH:
        return
    directory = os.path.dirname(VOICE_CLONE_CACHE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary_path = f"{VOICE_CLONE_CACHE_PATH}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, VOICE_CLONE_CACHE_PATH)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def create_elevenlabs_voice_clone(reference_path: str, name: str) -> str:
    """Create an Instant Voice Clone from audio the user is allowed to use."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is required for voice cloning")
    if not os.path.isfile(reference_path) or os.path.getsize(reference_path) < 1000:
        raise RuntimeError("A usable voice reference audio file is required")

    reference_mime = {
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".oga": "audio/ogg",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
    }.get(os.path.splitext(reference_path)[1].lower(), "application/octet-stream")
    data = {
        "name": name[:100],
        "description": "Natural male first-person Burmese recap narrator",
        "remove_background_noise": "false",
        "labels": json.dumps({"language": "my", "gender": "male", "use_case": "narration"}),
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(reference_path, "rb") as reference_file:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/voices/add",
                    headers={"xi-api-key": ELEVENLABS_API_KEY, "Accept": "application/json"},
                    data=data,
                    files={
                        "files": (
                            os.path.basename(reference_path),
                            reference_file,
                            reference_mime,
                        )
                    },
                )
        if response.status_code != 200:
            raise RuntimeError(
                f"ElevenLabs voice clone HTTP {response.status_code}: {response.text[:180]}"
            )
        payload = response.json()
        voice_id = str(payload.get("voice_id", "")).strip()
        if not voice_id:
            raise RuntimeError("ElevenLabs did not return a cloned voice ID")
        if payload.get("requires_verification"):
            raise RuntimeError("This voice clone requires verification before it can be used")
        logger.info("Created ElevenLabs voice clone named %s", name)
        return voice_id
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise RuntimeError(f"Voice clone request failed: {str(exc)[:160]}") from None


async def ensure_voice01_clone() -> str:
    """Clone the selected Arena voice-01 reference once, then cache its provider ID."""
    global voice01_clone_id
    if not VOICE_CLONE_ENABLED or not ELEVENLABS_API_KEY or not VOICE_CLONE_REFERENCE:
        return ""
    if voice01_clone_id:
        return voice01_clone_id
    if not os.path.isfile(VOICE_CLONE_REFERENCE):
        logger.warning("Voice-01 reference was not found; using the standard TTS fallback")
        return ""

    async with voice_clone_lock:
        if voice01_clone_id:
            return voice01_clone_id
        fingerprint = await asyncio.to_thread(file_sha256, VOICE_CLONE_REFERENCE)
        state = load_voice_clone_state()
        cached = state.get("voice01") or {}
        cached_id = str(cached.get("voice_id", "")).strip()
        if cached_id and cached.get("reference_sha256") == fingerprint:
            voice01_clone_id = cached_id
            return voice01_clone_id

        cloned_id = await create_elevenlabs_voice_clone(
            VOICE_CLONE_REFERENCE,
            "Rednote MM Voice 01",
        )
        state["voice01"] = {
            "voice_id": cloned_id,
            "reference_sha256": fingerprint,
        }
        await asyncio.to_thread(save_voice_clone_state, state)
        voice01_clone_id = cloned_id
        return voice01_clone_id


async def get_narration_clone_voice_id(chat_id: int) -> str:
    """Resolve per-chat custom clone, explicit custom ID, then the voice-01 default."""
    if not VOICE_CLONE_ENABLED or not ELEVENLABS_API_KEY:
        return ""
    state = load_voice_clone_state()
    chat_clone = state.get("chats", {}).get(str(chat_id)) or {}
    if chat_clone.get("use_charon"):
        return ""
    custom_id = str(chat_clone.get("voice_id", "")).strip()
    if custom_id:
        return custom_id
    if ELEVENLABS_VOICE_ID and not chat_clone.get("use_voice01"):
        return ELEVENLABS_VOICE_ID
    try:
        return await ensure_voice01_clone()
    except Exception as exc:
        # A cloning/quota problem must not prevent the normal Charon/Edge fallback.
        logger.warning("Voice-01 clone unavailable; using fallback TTS: %s", str(exc)[:160])
        return ""


async def set_chat_voice_clone(chat_id: int, voice_id: str) -> None:
    async with voice_clone_lock:
        state = load_voice_clone_state()
        state.setdefault("chats", {})[str(chat_id)] = {
            "voice_id": voice_id,
            "source": "telegram-reference",
        }
        await asyncio.to_thread(save_voice_clone_state, state)


async def reset_chat_voice_clone(chat_id: int) -> None:
    async with voice_clone_lock:
        state = load_voice_clone_state()
        state.setdefault("chats", {})[str(chat_id)] = {"use_voice01": True}
        await asyncio.to_thread(save_voice_clone_state, state)


async def set_chat_charon_voice(chat_id: int) -> None:
    async with voice_clone_lock:
        state = load_voice_clone_state()
        state.setdefault("chats", {})[str(chat_id)] = {"use_charon": True}
        await asyncio.to_thread(save_voice_clone_state, state)


def get_chat_voice_label(chat_id: int) -> str:
    """Return a human-readable active narrator without making an API request."""
    if not VOICE_CLONE_ENABLED:
        return f"{GEMINI_TTS_VOICE} deep voice"
    chat_clone = load_voice_clone_state().get("chats", {}).get(str(chat_id)) or {}
    if chat_clone.get("voice_id"):
        return "Custom voice clone"
    if chat_clone.get("use_charon"):
        return f"{GEMINI_TTS_VOICE} deep voice"
    if chat_clone.get("use_voice01"):
        return "Voice-01 clone"
    if ELEVENLABS_VOICE_ID:
        return "Configured ElevenLabs voice"
    return "Voice-01 clone (default)"


def choose_elevenlabs_voice(voices: list[dict], requested_name: str) -> str:
    exact = requested_name.casefold()
    for voice in voices:
        if str(voice.get("name", "")).casefold() == exact and voice.get("voice_id"):
            return voice["voice_id"]

    def voice_score(voice: dict) -> int:
        searchable = " ".join(
            [
                str(voice.get("name", "")),
                str(voice.get("description", "")),
                json.dumps(voice.get("labels", {}), ensure_ascii=False),
            ]
        ).casefold()
        score = 0
        if "male" in searchable and "female" not in searchable:
            score += 3
        if "deep" in searchable or "low" in searchable or "bass" in searchable:
            score += 3
        if "narrat" in searchable:
            score += 1
        return score

    ranked = sorted(
        (voice for voice in voices if voice.get("voice_id")),
        key=voice_score,
        reverse=True,
    )
    return ranked[0]["voice_id"] if ranked else ""


async def resolve_elevenlabs_voice_id(client: httpx.AsyncClient) -> str:
    if ELEVENLABS_VOICE_ID:
        return ELEVENLABS_VOICE_ID

    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    for query in dict.fromkeys([ELEVENLABS_VOICE_NAME, "deep male narrator", "Adam"]):
        if not query:
            continue
        try:
            response = await client.get(
                "https://api.elevenlabs.io/v2/voices",
                headers=headers,
                params={"search": query, "page_size": 20},
            )
            if response.status_code != 200:
                continue
            voices = response.json().get("voices", [])
            voice_id = choose_elevenlabs_voice(voices, query)
            if voice_id:
                return voice_id
        except Exception as exc:
            logger.warning("ElevenLabs voice search failed: %s", str(exc)[:120])

    # Public premade Adam voice is a final deep-male fallback, not a secret.
    return "pNInz6obpgDQGcFmaJgB"


async def synthesize_elevenlabs_tts(text: str, out_path: str, voice_id: str = None) -> str:
    """Return the voice ID on success, otherwise an empty string."""
    if not ELEVENLABS_API_KEY:
        return ""

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "language_code": "my",
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 0.85,
            "style": 0.2,
            "use_speaker_boost": True,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            resolved_voice = voice_id or await resolve_elevenlabs_voice_id(client)
            if not resolved_voice:
                return ""
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{resolved_voice}"
            async with client.stream(
                "POST",
                url,
                headers=headers,
                params={"output_format": "mp3_24000_48"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise RuntimeError(f"HTTP {response.status_code}: {error_body[:160]!r}")
                with open(out_path, "wb") as output_file:
                    async for chunk in response.aiter_bytes(65536):
                        output_file.write(chunk)
        if os.path.getsize(out_path) >= 200:
            logger.info("ElevenLabs Burmese TTS succeeded")
            return resolved_voice
    except Exception as exc:
        logger.warning("ElevenLabs TTS failed: %s", str(exc)[:160])
        try:
            os.remove(out_path)
        except OSError:
            pass
    return ""


async def synthesize_narration(
    text: str,
    work_dir: str,
    index: int,
    preferred_provider: str = None,
    elevenlabs_voice_id: str = None,
):
    default_order = ["gemini", "elevenlabs", "edge"]
    if preferred_provider in default_order:
        default_order.remove(preferred_provider)
        default_order.insert(0, preferred_provider)
    if TTS_PROVIDER in default_order:
        default_order.remove(TTS_PROVIDER)
        default_order.insert(0, TTS_PROVIDER)

    errors = []
    for provider in default_order:
        raw_path = os.path.join(work_dir, f"seg_{index}_{provider}.audio")
        try:
            if provider == "gemini":
                if await synthesize_gemini_tts(text, raw_path):
                    return raw_path, provider, elevenlabs_voice_id
            elif provider == "elevenlabs":
                resolved_voice = await synthesize_elevenlabs_tts(
                    text,
                    raw_path,
                    elevenlabs_voice_id,
                )
                if resolved_voice:
                    return raw_path, provider, resolved_voice
            else:
                # Edge is the final availability fallback; Thiha keeps a low male
                # narrator instead of changing voices between recap segments.
                communicate = edge_tts.Communicate(text, MALE_VOICE)
                await communicate.save(raw_path)
                if os.path.exists(raw_path) and os.path.getsize(raw_path) >= 200:
                    return raw_path, provider, elevenlabs_voice_id
        except Exception as exc:
            errors.append(f"{provider}: {str(exc)[:100]}")
            logger.warning("%s TTS segment %s failed: %s", provider, index, str(exc)[:120])

    raise RuntimeError("All TTS providers failed; " + "; ".join(errors))


async def synthesize_dubbed_track(
    segments: list[dict],
    total_dur: float,
    work_dir: str,
    out_audio_path: str,
    clone_voice_id: str = None,
    progress_callback=None,
):
    """Build a long narration track with disk-backed PCM instead of huge RAM arrays."""
    # The extra tail room preserves the previous 1.2-second allowance without
    # ever growing a multi-GB in-memory array with np.concatenate.
    total_samples = int(np.ceil((total_dur + 2.0) * SAMPLE_RATE))
    raw_master_path = os.path.join(work_dir, "master_float.pcm")
    master_s16_path = os.path.join(work_dir, "master_s16.pcm")
    master_pcm = np.memmap(
        raw_master_path,
        dtype=np.float32,
        mode="w+",
        shape=(total_samples,),
    )
    init_chunk = SAMPLE_RATE * 60
    for offset in range(0, total_samples, init_chunk):
        master_pcm[offset : offset + init_chunk] = 0.0

    cursor_sample = 0
    synthesized_count = 0
    selected_provider = "elevenlabs" if clone_voice_id else None
    selected_eleven_voice = clone_voice_id or None

    for idx, seg in enumerate(segments):
        raw_tts_path = None
        trimmed_wav = os.path.join(work_dir, f"seg_{idx}_trim.wav")
        final_pcm_path = os.path.join(work_dir, f"seg_{idx}.pcm")
        try:
            raw_tts_path, actual_provider, selected_eleven_voice = await synthesize_narration(
                seg["burmese"],
                work_dir,
                idx,
                selected_provider,
                selected_eleven_voice,
            )
            selected_provider = actual_provider

            trim_filter = (
                "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-45dB,"
                "areverse,"
                "silenceremove=start_periods=1:start_silence=0.06:start_threshold=-45dB,"
                "areverse"
            )
            await asyncio.to_thread(
                run_cmd,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    raw_tts_path,
                    "-af",
                    trim_filter,
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    trimmed_wav,
                ],
                90,
            )
            if not os.path.exists(trimmed_wav) or os.path.getsize(trimmed_wav) < 200:
                await asyncio.to_thread(
                    run_cmd,
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        raw_tts_path,
                        "-ac",
                        "1",
                        "-ar",
                        str(SAMPLE_RATE),
                        trimmed_wav,
                    ],
                    90,
                )

            tts_dur = await asyncio.to_thread(get_media_duration, trimmed_wav)
            if tts_dur <= 0.05:
                continue

            start_sec = max(seg["start"], cursor_sample / SAMPLE_RATE)
            segment_end = float(seg.get("end", total_dur))
            if idx + 1 < len(segments):
                next_start = max(start_sec + 0.4, segments[idx + 1]["start"])
                window_end = min(next_start, segment_end)
                avail_dur = max(0.35, window_end - start_sec - 0.03)
            else:
                avail_dur = max(0.35, segment_end - start_sec)

            speed_ratio = tts_dur / avail_dur if avail_dur > 0 else 1.0
            ff_cmd = ["ffmpeg", "-y", "-i", trimmed_wav]
            if speed_ratio > 1.03:
                # atempo remains capped so long narration does not become unintelligible.
                ff_cmd += ["-af", f"atempo={min(1.45, speed_ratio):.3f}"]
            ff_cmd += [
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                final_pcm_path,
            ]
            await asyncio.to_thread(run_cmd, ff_cmd, 90)

            if not os.path.exists(final_pcm_path):
                continue
            seg_pcm = np.fromfile(final_pcm_path, dtype=np.int16).astype(np.float32) / 32768.0
            if len(seg_pcm) == 0:
                continue

            fade_len = min(int(0.015 * SAMPLE_RATE), len(seg_pcm) // 4)
            if fade_len > 1:
                seg_pcm[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
                seg_pcm[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

            place_idx = max(0, int(start_sec * SAMPLE_RATE))
            if place_idx >= total_samples:
                continue
            end_idx = min(place_idx + len(seg_pcm), total_samples)
            seg_pcm = seg_pcm[: end_idx - place_idx]
            master_pcm[place_idx:end_idx] += seg_pcm
            cursor_sample = min(total_samples, end_idx + int(0.05 * SAMPLE_RATE))
            synthesized_count += 1
        except Exception as exc:
            logger.warning("Skipping TTS segment %s after provider fallback: %s", idx, str(exc)[:180])
        finally:
            # Do not retain hundreds of per-segment files during a long job.
            for temporary_path in (raw_tts_path, trimmed_wav, final_pcm_path):
                if temporary_path:
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass
            if progress_callback:
                try:
                    await progress_callback(idx + 1, len(segments), synthesized_count)
                except Exception as callback_error:
                    logger.warning("Voice progress callback failed: %s", str(callback_error)[:100])

    if synthesized_count == 0:
        del master_pcm
        raise RuntimeError("No narration segments could be synthesized")

    target_len = min(int(total_dur * SAMPLE_RATE), total_samples)
    if cursor_sample > target_len:
        target_len = min(cursor_sample, int((total_dur + 1.2) * SAMPLE_RATE), total_samples)

    # Normalize and convert in bounded chunks so multi-hour audio does not get
    # duplicated into several full-size float32/int16 RAM arrays.
    peak = 0.0
    io_chunk = SAMPLE_RATE * 60
    for offset in range(0, target_len, io_chunk):
        chunk = master_pcm[offset : min(offset + io_chunk, target_len)]
        if len(chunk):
            peak = max(peak, float(np.max(np.abs(chunk))))

    gain = 0.92 / peak if peak > 0.01 else 1.0
    with open(master_s16_path, "wb") as output_file:
        for offset in range(0, target_len, io_chunk):
            chunk = master_pcm[offset : min(offset + io_chunk, target_len)]
            pcm_s16 = np.clip(chunk * gain * 32767.0, -32768, 32767).astype(np.int16)
            pcm_s16.tofile(output_file)

    master_pcm.flush()
    del master_pcm
    try:
        os.remove(raw_master_path)
    except OSError:
        pass

    await asyncio.to_thread(
        run_cmd,
        [
            "ffmpeg",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-i",
            master_s16_path,
            "-c:a",
            "libmp3lame",
            "-b:a",
            "96k",
            out_audio_path,
        ],
        FFMPEG_TIMEOUT,
    )
    try:
        os.remove(master_s16_path)
    except OSError:
        pass

    if not os.path.exists(out_audio_path) or os.path.getsize(out_audio_path) < 200:
        raise RuntimeError("Could not create the final narration track")
    logger.info("Narration completed with %s using %s segments", selected_provider, synthesized_count)
    return selected_provider or "edge"


def human_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.2f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{max(0, size_bytes) / 1024:.0f} KB"


JOB_STAGES = [
    ("download", "📥 Download"),
    ("script", "✍️ Script"),
    ("voice", "🎙️ Voiceover"),
    ("render", "🎞️ Rendering"),
    ("upload", "📤 Uploading"),
]


class PermanentJobError(RuntimeError):
    """An input/configuration error that should not be retried automatically."""


class RetryableJobError(RuntimeError):
    """A transient stage failure that can be retried safely."""


def format_mb(size_bytes) -> str:
    if size_bytes is None:
        return "—"
    return f"{max(0.0, float(size_bytes)) / 1_000_000:.1f} MB"


def make_progress_bar(current, total, width: int = 12) -> str:
    if not total or total <= 0:
        return "░" * width
    ratio = min(1.0, max(0.0, float(current or 0) / float(total)))
    filled = min(width, max(0, round(ratio * width)))
    return f"{'█' * filled}{'░' * (width - filled)} {ratio * 100:.0f}%"


def retry_keyboard(job_id: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔄 Retry Job", "callback_data": f"retry:{job_id}"}],
            [{"text": "🟢 Bot Status", "callback_data": "status"}],
        ]
    }


def remember_retry_job(job_id: str, chat_id: int, reply_msg_id: int, url: str) -> None:
    now = time.time()
    for key, value in list(retry_jobs.items()):
        if now - value.get("created", now) > RETRY_JOB_TTL_SECONDS:
            retry_jobs.pop(key, None)
    while len(retry_jobs) >= 100:
        retry_jobs.pop(next(iter(retry_jobs)))
    retry_jobs[job_id] = {
        "chat_id": chat_id,
        "reply_msg_id": reply_msg_id,
        "url": url,
        "created": now,
    }


def get_retry_job(job_id: str, chat_id: int) -> dict:
    value = retry_jobs.get(job_id) or {}
    if not value or value.get("chat_id") != chat_id:
        return {}
    if time.time() - value.get("created", 0) > RETRY_JOB_TTL_SECONDS:
        retry_jobs.pop(job_id, None)
        return {}
    return value


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, PermanentJobError):
        return False
    if isinstance(exc, RetryableJobError):
        return True
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            subprocess.TimeoutExpired,
            httpx.TimeoutException,
            httpx.NetworkError,
            ConnectionError,
        ),
    ):
        return True
    message = str(exc).lower()
    permanent_markers = (
        "api key",
        "no audio",
        "no speech",
        "disk space",
        "duration limit",
        "unsupported",
        "permission",
        "verification",
    )
    if any(marker in message for marker in permanent_markers):
        return False
    transient_markers = (
        "timeout",
        "timed out",
        "temporarily",
        "connection",
        "network",
        "rate limit",
        "429",
        "http 5",
        "503",
        "502",
        "500",
        "download failed",
    )
    return any(marker in message for marker in transient_markers)


async def run_with_retries(operation, progress, stage: str):
    """Retry only transient failures; permanent input/quota errors fail immediately."""
    last_error = None
    for attempt in range(1, JOB_MAX_ATTEMPTS + 1):
        progress.attempt = attempt
        try:
            result = await operation()
            progress.notice = ""
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= JOB_MAX_ATTEMPTS or not is_transient_error(exc):
                raise
            logger.warning(
                "Retrying job %s stage %s after attempt %s: %s",
                progress.job_id,
                stage,
                attempt,
                str(exc)[:140],
            )
            progress.notice = f"Attempt {attempt} failed; automatic retry is starting."
            await progress.emit(
                "retry",
                detail=f"{stage} attempt {attempt} failed: {str(exc)[:180]}",
                stage=stage,
            )
            await progress.update(
                detail=f"Attempt {attempt} failed · retrying {stage}…",
                force=True,
            )
            await asyncio.sleep(JOB_RETRY_DELAY_SECONDS * attempt)
    raise last_error


def dyy_next_action(event: dict) -> str:
    """Return event-aware next steps without calling another AI service."""
    kind = event.get("event", "stage")
    stage = event.get("stage", "")
    detail = str(event.get("detail", ""))

    if kind == "queued":
        return (
            "ဆရာ ဘာမှထပ်ပို့စရာမလိုပါဘူး။ Worker အလင်းရတာနဲ့ download ကိုစပြီး "
            "အဆင့်တိုင်းကို ကျွန်တော်က live ဆက်ပြောပါမယ်။"
        )
    if kind == "retry":
        return (
            "Link ထပ်ပို့စရာ၊ command ထပ်ရိုက်စရာ မလိုပါဘူး။ ယာယီအခက်အခဲကို "
            "automatic retry လုပ်ပြီး မူရင်း job ကနေတော့ ဆက်လုပ်ပါမယ်။"
        )
    if kind == "watchdog":
        return (
            "ဆရာ စောင့်နိုင်ပါတယ်။ Job task နဲ့ file-size activity ကို ဆက်စစ်နေပြီး "
            "တကယ့် failure ဖြစ်မှသာ retry/error guidance ပေးပါမယ်။"
        )
    if kind == "success":
        return (
            "Video delivery အထိ အားလုံးပြီးပါပြီ။ နောက် Recap လိုရင် Rednote link အသစ် "
            "တစ်ခုချင်းပို့ပါ။ DYY က အလိုအလျောက် ပြန်စပါမယ်။"
        )
    if kind == "fail":
        message = detail.lower()
        if any(marker in message for marker in ("no usable speech", "no speech", "no audio")):
            return (
                "ဒီ video မှာ သုံးလို့ရတဲ့ အသံတွေ့ပါဘူး။ အသံပါတဲ့ မူရင်း video link ကို "
                "ပြန်စစ်ပြီး အဲဒီ link ကို ပို့ပါ။"
            )
        if "photo post" in message or "no playable video" in message:
            return "Photo post မဟုတ်ဘဲ play လို့ရတဲ့ Rednote video link ကို ပို့ပါ။"
        if "disk" in message or "space" in message:
            return (
                "Server disk နေရာလိုနေပါတယ်။ Video ပိုသေးတဲ့ link ကို စမ်းနိုင်ပြီး၊ "
                "bot owner အနေနဲ့ server storage ကိုလည်း စစ်ရပါမယ်။"
            )
        if "api key" in message or "not configured" in message:
            next_step = (
                "ပြင်ပြီးရင် Retry Job ခလုတ်ကိုသုံးပါ။"
                if event.get("retry_available", True)
                else "ပြင်ပြီးရင် မူရင်း link ကို ပြန်ပို့ပါ။"
            )
            return (
                "Provider configuration ပြဿနာဖြစ်နေပါတယ်။ Bot owner က API key ကို chat ထဲ "
                f"မထည့်ဘဲ Render environment variables မှာ ပြင်ပါ။ {next_step}"
            )
        if any(marker in message for marker in ("private", "permission", "not found")):
            return (
                "Link ကို bot က access မရပါဘူး။ Public/viewable Rednote video link အသစ် "
                "ရယူပြီး ပြန်ပို့ပါ။"
            )
        if event.get("retry_available", True):
            return (
                "ဆရာ link ထပ်ရိုက်စရာမလိုဘဲ Job card ရဲ့ <b>🔄 Retry Job</b> ခလုတ်ကိုသုံးပါ။ "
                "မူရင်း link ကနေ ပြန်စပေးပါမယ်။"
            )
        return "ဒီ input ကို auto retry မလုပ်ပါဘူး။ ပြင်ပြီးတဲ့ Rednote video link အသစ်ကို ပို့ပါ။"

    if kind == "complete":
        next_steps = {
            "download": "Video ရပြီမို့ speech analysis နဲ့ first-person script ကို အလိုအလျောက် ဆက်လုပ်ပါမယ်။",
            "script": "Script ပြီးပါပြီ။ ရွေးထားတဲ့ narrator နဲ့ segment အလိုက် အသံသွင်းပါမယ်။",
            "voice": "Narration ပြီးပါပြီ။ မူရင်း video နဲ့ပေါင်းပြီး target size အတိုင်း render လုပ်ပါမယ်။",
            "render": "Final video ရပါပြီ။ Telegram limit အလိုက် ခွဲ/မခွဲ စစ်ပြီး အစဉ်လိုက် ပို့ပါမယ်။",
            "upload": "Telegram delivery ပြီးပါပြီ။ Final completion ကို ကျွန်တော်က အတည်ပြုပါမယ်။",
        }
        return next_steps.get(stage, "ပြီးတဲ့အဆင့်ကို မှတ်ထားပြီး နောက်အဆင့်ကို အလိုအလျောက် ဆက်လုပ်ပါမယ်။")

    stage_actions = {
        "download": "Source ကိုရှာပြီး video ဆွဲနေပါတယ်။ Download ပြီးရင် script analysis သို့ auto ဆက်ပါမယ်။",
        "script": "Speech နဲ့ ဇာတ်လမ်းအစ/အဆုံးကို ခွဲခြမ်းပြီး first-person မြန်မာ Recap ရေးနေပါတယ်။",
        "voice": "Voice-01 clone ကို ဦးစားပေးပြီး မရရင် fallback အလိုအလျောက်သုံးကာ narration လုပ်နေပါတယ်။",
        "render": "Duration မဖြတ်ဘဲ narration ပေါင်းပြီး final file size ၈၀% ခန့်သို့ encode လုပ်နေပါတယ်။",
        "upload": "Telegram-safe MP4 parts တွေကို အစဉ်လိုက် ပို့နေပါတယ်။ Part တိုင်းပြီးတဲ့ MB ကို main card မှာ ပြပါမယ်။",
    }
    return stage_actions.get(
        stage,
        "လက်ရှိ job event ကို ကျွန်တော်က စောင့်ကြည့်နေပြီး အပြောင်းအလဲရရင် ဆရာ့ကို ဆက်ပြောပါမယ်။",
    )


class DYYAgent:
    """Always-on, event-driven guide and job observer; no preference or kill switch."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id

    async def start(self, reply_msg_id: int = None) -> None:
        await send_text(
            self.chat_id,
            welcome_message(),
            reply_to=reply_msg_id,
            reply_markup=bot_menu_keyboard(),
        )

    async def report(self, event: dict) -> None:
        kind = event.get("event", "stage")
        titles = {
            "queued": "Rednote link ကို လက်ခံရရှိပါပြီ။",
            "stage": "လက်ရှိအဆင့်ကို စတင်လုပ်ဆောင်နေပါတယ်။",
            "complete": "အဆင့်တစ်ခု ပြီးစီးသွားပါပြီ။",
            "retry": "ယာယီအခက်အခဲကြောင့် automatic retry လုပ်နေပါတယ်။",
            "watchdog": "Progress silence ကို watchdog စစ်ဆေးနေပါတယ်။",
            "fail": "Job ရပ်သွားပါတယ်။",
            "success": "Recap အပြည့်အစုံ ပြီးစီးပါပြီ။",
        }
        title = titles.get(kind, "Job အခြေအနေကို စောင့်ကြည့်နေပါတယ်။")
        stage = event.get("stage", "")
        stage_label = dict(JOB_STAGES).get(stage, "Queue" if stage == "queue" else stage or "General")
        detail = str(event.get("detail", ""))[:320]
        lines = [
            f"<b>🕵️ DYY AGENT · LIVE</b> · <code>#{html.escape(str(event.get('job_id', '')))}</code>",
            f"ဆရာ၊ <b>{html.escape(title)}</b>",
            "",
            f"┣ <b>Stage:</b> {html.escape(stage_label)}",
            f"┣ <b>အသေးစိတ်:</b> {html.escape(detail)}",
            f"┣ <b>Attempt:</b> {event.get('attempt', 1)}",
        ]
        current = event.get("current")
        total = event.get("total")
        if current is not None:
            metric = format_mb(current)
            if total:
                metric += f" / {format_mb(total)}"
            lines.append(f"┣ <b>Data:</b> {metric}")
        if kind == "watchdog":
            lines.append(
                f"┣ <b>Silence:</b> {int(event.get('idle_seconds', 0))}s · "
                "task/byte activity ကို ဆက်စစ်နေပါတယ်။"
            )
        lines.append(f"┗ <b>DYY လမ်းညွှန်ချက်:</b> {dyy_next_action(event)}")
        lines.append("<i>Always On · no activation command required</i>")
        await send_text(self.chat_id, "\n".join(lines))


async def handle_dyy_start(chat_id: int, reply_msg_id: int = None) -> None:
    """Start every session with DYY's always-on, actionable usage guide."""
    await DYYAgent(chat_id).start(reply_msg_id)


def copyright_caption(url: str, part_number: int) -> str:
    """Add attribution/rights context without claiming copyright immunity."""
    if part_number != 1:
        return ""
    lines = []
    if SOURCE_CREDIT_ENABLED:
        if len(url) <= 450:
            safe_url = html.escape(url, quote=True)
            lines.append(f'🔗 <a href="{safe_url}">မူရင်း video</a>')
        else:
            lines.append("🔗 မူရင်း Rednote video")
    if COPYRIGHT_NOTICE_ENABLED:
        lines.append(
            "<i>First-person recap/commentary။ မူရင်းပိုင်ဆိုင်မှုများသည် "
            "မူရင်းပိုင်ရှင်များအတွက်သာ။</i>"
        )
    return "\n\n" + "\n".join(lines) if lines else ""


class JobProgress:
    """A throttled Telegram progress card with byte monitoring and a 60s watchdog."""

    def __init__(self, chat_id: int, message_id: int, job_id: str, event_callback=None):
        self.chat_id = chat_id
        self.message_id = message_id
        self.job_id = job_id
        self.event_callback = event_callback
        self.stage = "download"
        self.detail = "Waiting for a worker…"
        self.details = {}
        self.completed = set()
        self.current = None
        self.total = None
        self.attempt = 1
        self.error = ""
        self.finished = False
        self.retry_available = True
        self.notice = ""
        self.watchdog_checks = 0
        self.last_activity = time.monotonic()
        self.last_edit = 0.0
        self.last_watchdog_notice = 0.0
        self.last_text = ""
        self.watch_path = None
        self.watch_glob = None
        self.job_task = None
        self.tasks = []
        self._edit_lock = asyncio.Lock()

    @property
    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_activity)

    async def emit(self, event: str, detail: str = "", stage: str = None) -> None:
        if not self.event_callback:
            return
        payload = {
            "event": event,
            "job_id": self.job_id,
            "stage": stage or self.stage,
            "detail": detail or self.detail,
            "current": self.current,
            "total": self.total,
            "attempt": self.attempt,
            "idle_seconds": self.idle_seconds,
            "retry_available": self.retry_available,
        }
        try:
            await self.event_callback(payload)
        except Exception as exc:
            # Monitoring must never interrupt the actual recap job.
            logger.warning("DYY report failed: %s", str(exc)[:120])

    def render(self) -> str:
        if self.error:
            heading = "❌ JOB STOPPED"
        elif self.finished:
            heading = "✅ RECAP COMPLETE"
        else:
            heading = "🎬 RECAP JOB"
        lines = [
            f"<b>{heading}</b> <code>#{html.escape(self.job_id)}</code>",
            f"<i>Attempt {self.attempt} · 60-second watchdog enabled</i>",
            "",
        ]
        for key, label in JOB_STAGES:
            if key in self.completed:
                icon = "✅"
                detail = self.details.get(key, "Done")
            elif key == self.stage:
                icon = "❌" if self.error else ("✅" if self.finished else "🔄")
                detail = self.detail
            else:
                icon = "⏳"
                detail = "Waiting"
            lines.append(f"{icon} <b>{label}</b> · {html.escape(str(detail))}")
            if key == self.stage and key not in self.completed and self.current is not None:
                current_text = format_mb(self.current)
                if self.total:
                    lines.append(
                        f"   └ <code>{make_progress_bar(self.current, self.total)}</code> "
                        f"{current_text} / {format_mb(self.total)}"
                    )
                else:
                    lines.append(f"   └ <code>{current_text}</code>")
        lines.append("")
        if self.error:
            lines.append(f"<b>Error:</b> {html.escape(self.error[:220])}")
            if self.retry_available:
                lines.append("<i>Use Retry Job to restart safely from the link.</i>")
        elif self.finished:
            lines.append(f"<b>{html.escape(self.detail)}</b>")
        else:
            lines.append(
                f"<i>Last progress: {int(self.idle_seconds)}s ago · "
                f"Watchdog checks after {PROGRESS_STALL_SECONDS}s of silence.</i>"
            )
        if self.notice and not self.error:
            lines.append(f"<i>🛡️ {html.escape(self.notice)}</i>")
        return "\n".join(lines)

    async def refresh(self, force: bool = False) -> None:
        async with self._edit_lock:
            now = time.monotonic()
            if not force and now - self.last_edit < PROGRESS_EDIT_INTERVAL:
                return
            text = self.render()
            if text == self.last_text:
                return
            self.last_edit = now
            self.last_text = text
            markup = retry_keyboard(self.job_id) if self.error and self.retry_available else None
            await edit_text(self.chat_id, self.message_id, text, reply_markup=markup)

    async def set_stage(self, stage: str, detail: str, current=None, total=None, force=True):
        self.stage = stage
        self.detail = detail
        self.current = current
        self.total = total
        self.last_activity = time.monotonic()
        self.notice = ""
        await self.refresh(force=force)
        await self.emit("stage", detail=detail, stage=stage)

    async def update(self, detail: str = None, current=None, total=None, force: bool = False):
        changed = False
        if detail is not None and detail != self.detail:
            self.detail = detail
            changed = True
        if current is not None and current != self.current:
            self.current = current
            changed = True
        if total is not None and total != self.total:
            self.total = total
            changed = True
        if changed:
            self.last_activity = time.monotonic()
        await self.refresh(force=force)

    async def complete(self, stage: str, detail: str, force: bool = True):
        self.completed.add(stage)
        self.details[stage] = detail
        self.last_activity = time.monotonic()
        await self.refresh(force=force)
        await self.emit("complete", detail=detail, stage=stage)

    def watch_file(self, path: str, total=None) -> None:
        self.watch_path = path
        self.watch_glob = None
        if total is not None:
            self.total = total

    def watch_pattern(self, pattern: str, total=None) -> None:
        self.watch_path = None
        self.watch_glob = pattern
        if total is not None:
            self.total = total

    def unwatch(self) -> None:
        self.watch_path = None
        self.watch_glob = None

    def watched_size(self):
        try:
            if self.watch_path and os.path.isfile(self.watch_path):
                return os.path.getsize(self.watch_path)
            if self.watch_glob:
                paths = [path for path in glob.glob(self.watch_glob) if os.path.isfile(path)]
                if paths:
                    newest = max(paths, key=os.path.getmtime)
                    return os.path.getsize(newest)
        except OSError:
            return None
        return None

    async def _monitor_loop(self):
        while True:
            size = self.watched_size()
            if size is not None and size != self.current:
                self.current = size
                self.last_activity = time.monotonic()
                await self.refresh()
            await asyncio.sleep(2.0)

    async def check_watchdog(self) -> bool:
        now = time.monotonic()
        if self.finished or self.error or self.idle_seconds < PROGRESS_STALL_SECONDS:
            return False
        if now - self.last_watchdog_notice < PROGRESS_STALL_SECONDS:
            return False
        self.last_watchdog_notice = now
        self.watchdog_checks += 1
        task_alive = self.job_task is not None and not self.job_task.done()
        size = self.watched_size()
        size_text = f" · watched file {format_mb(size)}" if size is not None else ""
        self.notice = (
            f"Watchdog check {self.watchdog_checks}: {int(self.idle_seconds)}s without new progress; "
            f"job task is {'still active' if task_alive else 'stopped'}{size_text}. "
            "Retry is offered if the stage actually fails."
        )
        await self.refresh(force=True)
        await self.emit("watchdog", detail=self.notice, stage=self.stage)
        return True

    async def _watchdog_loop(self):
        while True:
            await asyncio.sleep(PROGRESS_WATCH_INTERVAL)
            await self.check_watchdog()

    async def start(self):
        self.job_task = asyncio.current_task()
        self.tasks = [
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._watchdog_loop()),
        ]
        await self.refresh(force=True)

    async def fail(self, message: str, allow_retry: bool = True):
        self.error = str(message)
        self.retry_available = allow_retry
        self.finished = True
        self.unwatch()
        await self.refresh(force=True)
        await self.emit("fail", detail=self.error, stage=self.stage)

    async def succeed(self, detail: str):
        for key, _ in JOB_STAGES:
            self.completed.add(key)
            self.details.setdefault(key, "Done")
        self.finished = True
        self.detail = detail
        self.unwatch()
        await self.refresh(force=True)
        await self.emit("success", detail=detail, stage=self.stage)

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []


def calculate_encoding_bitrates(target_bytes: int, duration: float) -> tuple[int, int]:
    """Calculate ABR video/audio rates for a target container size."""
    if target_bytes <= 0 or duration <= 0:
        raise ValueError("Target size and duration must be positive")

    # Reserve room for MP4 tables, faststart metadata, and normal ABR fluctuation.
    usable_total_bps = target_bytes * 8 * 0.965 / duration
    if usable_total_bps >= 400_000:
        audio_bps = 96_000
    elif usable_total_bps >= 200_000:
        audio_bps = 64_000
    else:
        audio_bps = 40_000

    video_bps = int(usable_total_bps - audio_bps)
    if video_bps < 30_000:
        audio_bps = 32_000
        video_bps = int(usable_total_bps - audio_bps)
    if video_bps < 20_000:
        raise ValueError("The requested target size is too small for the video duration")
    return video_bps, audio_bps


def process_cpu_seconds(pid: int):
    """Read Linux process CPU time so a quiet but busy encoder is not killed."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat_file:
            data = stat_file.read()
        # The executable name is parenthesized and may itself contain spaces.
        fields = data[data.rfind(b")") + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


async def stop_subprocess(process) -> None:
    """Terminate a stuck child and reap it before a retry starts."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def run_ffmpeg_with_progress(cmd: list[str], timeout: int, progress_callback=None):
    """Run FFmpeg with machine-readable stats and recover from a genuine stall."""
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    started = time.monotonic()
    last_activity = started
    last_signature = None
    last_cpu = process_cpu_seconds(process.pid)
    stats = {}
    stderr_buffer = bytearray()
    readline_task = None
    completed = False

    async def drain_stderr():
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            stderr_buffer.extend(chunk)
            if len(stderr_buffer) > 65_536:
                del stderr_buffer[:-65_536]

    stderr_task = asyncio.create_task(drain_stderr())
    poll_interval = min(2.0, max(0.05, float(PROGRESS_WATCH_INTERVAL)))
    try:
        readline_task = asyncio.create_task(process.stdout.readline())
        while True:
            done, _ = await asyncio.wait({readline_task}, timeout=poll_interval)
            now = time.monotonic()
            if now - started > timeout:
                raise RetryableJobError(f"FFmpeg render exceeded the {timeout}s timeout")
            if not done:
                if now - last_activity < PROGRESS_STALL_SECONDS:
                    continue
                current_cpu = process_cpu_seconds(process.pid)
                if (
                    current_cpu is not None
                    and last_cpu is not None
                    and current_cpu > last_cpu + 0.01
                ):
                    # No stats can be normal during a hard frame or MP4 metadata
                    # flush. Advancing CPU time proves the encoder is still busy.
                    last_cpu = current_cpu
                    last_activity = now
                    continue
                raise RetryableJobError(
                    f"FFmpeg made no progress and used no CPU for {PROGRESS_STALL_SECONDS}s"
                )

            line = readline_task.result()
            if not line:
                break
            readline_task = asyncio.create_task(process.stdout.readline())
            text = line.decode("utf-8", errors="replace").strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            stats[key.strip()] = value.strip()
            if key.strip() != "progress":
                continue

            signature = (
                stats.get("frame"),
                stats.get("out_time_ms"),
                stats.get("total_size"),
            )
            if signature != last_signature:
                last_signature = signature
                last_activity = time.monotonic()
            if progress_callback:
                await progress_callback(dict(stats))
            stats = {}

        remaining = max(0.1, timeout - (time.monotonic() - started))
        try:
            await asyncio.wait_for(process.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            raise RetryableJobError(f"FFmpeg render exceeded the {timeout}s timeout") from None
        await stderr_task
        completed = True
        return subprocess.CompletedProcess(
            cmd,
            process.returncode,
            stdout=None,
            stderr=bytes(stderr_buffer),
        )
    finally:
        if not completed and process.returncode is None:
            await stop_subprocess(process)
        if readline_task and not readline_task.done():
            readline_task.cancel()
        if not stderr_task.done():
            stderr_task.cancel()
        cleanup_tasks = [stderr_task]
        if readline_task is not None:
            cleanup_tasks.append(readline_task)
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)


async def encode_video_to_target_size(
    input_path: str,
    output_path: str,
    target_bytes: int,
    duration: float,
    audio_path: str = None,
    progress_callback=None,
) -> int:
    """Encode near a byte target, with live FFmpeg stats and one corrective pass."""
    base_video_bps, audio_bps = calculate_encoding_bitrates(target_bytes, duration)
    video_bps = base_video_bps
    best_path = None
    best_difference = None
    best_size = 0

    for attempt in range(2):
        attempt_path = f"{os.path.splitext(output_path)[0]}.attempt{attempt}.mp4"
        if video_bps < 300_000:
            max_width = 640
        elif video_bps < 700_000:
            max_width = 854
        elif video_bps < 1_500_000:
            max_width = 1280
        else:
            max_width = 1920

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "warning",
            "-progress",
            "pipe:1",
            "-y",
            "-i",
            input_path,
        ]
        if audio_path:
            cmd += ["-i", audio_path]
        cmd += ["-map", "0:v:0", "-map", "1:a:0" if audio_path else "0:a:0?"]
        cmd += [
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"scale='min({max_width},iw)':-2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            str(video_bps),
            "-maxrate",
            str(int(video_bps * 1.12)),
            "-bufsize",
            str(video_bps * 2),
            # Short GOPs make later stream-copy MP4 splitting accurate.
            "-g",
            "48",
            "-keyint_min",
            "48",
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            str(audio_bps),
            "-ac",
            "1",
            "-max_muxing_queue_size",
            "1024",
            "-movflags",
            "+faststart",
            attempt_path,
        ]
        async def report_attempt_progress(stats):
            if progress_callback:
                await progress_callback(stats, attempt + 1)

        proc = await run_ffmpeg_with_progress(
            cmd,
            FFMPEG_TIMEOUT,
            progress_callback=report_attempt_progress if progress_callback else None,
        )
        if proc.returncode != 0:
            logger.warning("Target encode failed: %s", proc.stderr.decode(errors="ignore")[-300:])
            continue
        if not os.path.exists(attempt_path) or os.path.getsize(attempt_path) < 1000:
            continue

        actual_duration = await asyncio.to_thread(get_media_duration, attempt_path)
        if actual_duration < max(0.5, duration * 0.95):
            logger.warning("Discarding incomplete target encode")
            continue

        actual_size = os.path.getsize(attempt_path)
        difference = abs(actual_size - target_bytes)
        if best_difference is None or difference < best_difference:
            if best_path and best_path != attempt_path:
                try:
                    os.remove(best_path)
                except OSError:
                    pass
            best_path = attempt_path
            best_difference = difference
            best_size = actual_size
        else:
            try:
                os.remove(attempt_path)
            except OSError:
                pass

        # A 90%-103% window is close enough to the requested 80% original size.
        if target_bytes * 0.90 <= actual_size <= target_bytes * 1.03:
            break
        if attempt == 0:
            correction = target_bytes / actual_size
            video_bps = max(20_000, int(video_bps * correction * 0.985))

    if not best_path:
        raise RuntimeError("FFmpeg could not create the requested final video size")

    if os.path.abspath(best_path) != os.path.abspath(output_path):
        os.replace(best_path, output_path)
    logger.info(
        "Encoded %s toward %s target; final size is %s",
        human_file_size(best_size),
        human_file_size(target_bytes),
        human_file_size(best_size),
    )
    return best_size


async def split_video_for_telegram(final_video: str, work_dir: str) -> list[str]:
    """Split a large final MP4 into playable stream-copied parts below Bot API limits."""
    if os.path.getsize(final_video) <= TELEGRAM_PART_LIMIT_BYTES:
        return [final_video]

    queue = [final_video]
    completed = []
    split_round = 0

    while queue:
        current = queue.pop(0)
        current_size = os.path.getsize(current)
        if current_size <= TELEGRAM_PART_LIMIT_BYTES:
            completed.append(current)
            continue

        duration = await asyncio.to_thread(get_media_duration, current)
        if duration <= 0:
            raise RuntimeError("Cannot determine duration while splitting final video")

        split_round += 1
        if split_round > 10_000:
            raise RuntimeError("Video splitting did not converge")
        split_dir = tempfile.mkdtemp(prefix=f"split_{split_round}_", dir=work_dir)
        pattern = os.path.join(split_dir, "piece_%04d.mp4")
        desired_pieces = max(2, int(np.ceil(current_size / (TELEGRAM_PART_LIMIT_BYTES * 0.82))))
        segment_time = max(1.0, duration / desired_pieces)
        proc = await asyncio.to_thread(
            run_cmd,
            [
                "ffmpeg",
                "-y",
                "-i",
                current,
                "-map",
                "0",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                f"{segment_time:.3f}",
                "-reset_timestamps",
                "1",
                "-movflags",
                "+faststart",
                pattern,
            ],
            FFMPEG_TIMEOUT,
        )
        pieces = sorted(
            os.path.join(split_dir, name)
            for name in os.listdir(split_dir)
            if name.startswith("piece_") and name.endswith(".mp4")
        )
        pieces = [piece for piece in pieces if os.path.getsize(piece) > 1000]

        if proc.returncode == 0 and len(pieces) > 1:
            # The split copies are now authoritative; free the larger input early.
            try:
                os.remove(current)
            except OSError:
                pass
            queue = pieces + queue
            continue

        # Never collapse a large multi-part video into one heavily compressed
        # 48MB file just because stream-copy segmentation failed.
        if current_size > TELEGRAM_PART_LIMIT_BYTES * 1.5:
            raise RuntimeError("FFmpeg could not split a large video part safely")

        # A small, keyframe-poor piece may not split with stream copy. Re-encode
        # only that piece rather than degrading the whole final video.
        for piece in pieces:
            try:
                os.remove(piece)
            except OSError:
                pass
        reencoded = os.path.join(split_dir, "small.mp4")
        await encode_video_to_target_size(
            current,
            reencoded,
            int(TELEGRAM_PART_LIMIT_BYTES * 0.85),
            duration,
        )
        if os.path.getsize(reencoded) > TELEGRAM_PART_LIMIT_BYTES:
            raise RuntimeError("A Telegram video part is still above the 50MB API limit")
        try:
            os.remove(current)
        except OSError:
            pass
        completed.append(reencoded)

    delivery_dir = os.path.join(work_dir, "delivery")
    os.makedirs(delivery_dir, exist_ok=True)
    delivered = []
    for index, part in enumerate(completed, 1):
        destination = os.path.join(delivery_dir, f"part_{index:04d}.mp4")
        os.replace(part, destination)
        if os.path.getsize(destination) > TELEGRAM_PART_LIMIT_BYTES:
            raise RuntimeError("Telegram part exceeds the configured byte limit")
        delivered.append(destination)

    logger.info("Split final video into %s Telegram-ready parts", len(delivered))
    return delivered


async def handle_select_charon(chat_id: int, reply_msg_id: int) -> None:
    """Let a chat bypass voice clones and use the Gemini Charon narrator."""
    try:
        await set_chat_charon_voice(chat_id)
        await send_text(
            chat_id,
            "<b>🎚️ CHARON DEEP SELECTED</b>\n"
            "ဒီ chat ရဲ့ နောက် Recap တွေအတွက် Charon Deep voice ကို ရွေးပြီးပါပြီ။\n"
            "<b>🎧 Voice Test</b> ခလုတ်နဲ့ အသံကို အရင်စမ်းနားထောင်နိုင်ပါတယ်။",
            reply_to=reply_msg_id,
            reply_markup=voice_lab_keyboard(),
        )
    except Exception as exc:
        logger.warning("Could not select Charon: %s", str(exc)[:120])
        await send_text(chat_id, "<b>⚠️ Charon voice ကို ရွေးလို့မရသေးပါဘူး။</b>")


async def handle_voice_preview(chat_id: int, reply_msg_id: int) -> None:
    """Generate and upload a real preview using the chat's active narrator."""
    status_id = await send_text(
        chat_id,
        "<b>🎧 VOICE TEST</b>\nရွေးထားတဲ့ narrator နဲ့ မြန်မာစာကြောင်း အသံထွက်နေပါတယ်...",
        reply_to=reply_msg_id,
    )
    work_dir = tempfile.mkdtemp(prefix="voice_preview_")
    try:
        clone_voice_id = await get_narration_clone_voice_id(chat_id)
        preferred_provider = "elevenlabs" if clone_voice_id else "gemini"
        raw_path, provider, _ = await synthesize_narration(
            "ကျွန်တော့်အသံကို စမ်းနားထောင်ကြည့်ပါ။ နောက်ထပ် Recap video တွေမှာ ဒီအသံနဲ့ ပြောပြပေးသွားပါမယ်။",
            work_dir,
            0,
            preferred_provider,
            clone_voice_id,
        )
        preview_path = os.path.join(work_dir, "voice_test.mp3")
        proc = await asyncio.to_thread(
            run_cmd,
            [
                "ffmpeg",
                "-y",
                "-i",
                raw_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                preview_path,
            ],
            FFMPEG_TIMEOUT,
        )
        if proc.returncode != 0 or not os.path.isfile(preview_path):
            raise RuntimeError("Voice preview audio conversion failed")

        if provider == "elevenlabs":
            voice_label = "Selected voice clone" if clone_voice_id else "ElevenLabs deep male"
        elif provider == "gemini":
            voice_label = f"{GEMINI_TTS_VOICE} deep voice"
        else:
            voice_label = "Edge Thiha voice"
        caption = f"<b>🎧 VOICE TEST</b>\n{html.escape(voice_label)}"
        payload = {
            "chat_id": str(chat_id),
            "caption": caption,
            "parse_mode": "HTML",
            "title": "Rednote MM narrator preview",
        }
        if reply_msg_id:
            payload["reply_to_message_id"] = str(reply_msg_id)
        with open(preview_path, "rb") as preview_file:
            result = await tg_api(
                "sendAudio",
                data=payload,
                files={
                    "audio": ("voice_test.mp3", preview_file, "audio/mpeg"),
                },
                timeout=180.0,
            )
        if not result.get("ok"):
            raise RuntimeError("Telegram could not send the voice preview")
        await edit_text(
            chat_id,
            status_id,
            f"<b>✅ VOICE TEST READY</b>\n{html.escape(voice_label)} နဲ့ sample ကို ပို့ပြီးပါပြီ။",
        )
    except Exception as exc:
        logger.exception("Voice preview failed")
        await edit_text(
            chat_id,
            status_id,
            f"<b>❌ VOICE TEST FAILED</b>\n{html.escape(str(exc)[:180])}",
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def handle_select_voice01(chat_id: int, reply_msg_id: int) -> None:
    """Select the bundled voice-01 reference over any per-chat custom clone."""
    try:
        await reset_chat_voice_clone(chat_id)
        if not VOICE_CLONE_ENABLED:
            message = "Voice clone system ကို <code>VOICE_CLONE_ENABLED=false</code> နဲ့ ပိတ်ထားပါတယ်။"
        elif not ELEVENLABS_API_KEY:
            message = (
                "Voice-01 ကို ရွေးထားပါတယ်။ Clone လုပ်ဖို့ Render environment မှာ "
                "<code>ELEVENLABS_API_KEY</code> လိုအပ်ပါတယ်။"
            )
        else:
            message = (
                "<b>Voice-01 ကို ရွေးပြီးပါပြီ။</b> နောက် Recap စတင်ရင် 01 reference ကို "
                "clone လုပ်ပြီး အဲဒီအသံကို ဦးစားပေးသုံးပါမယ်။\n"
                "<b>🎧 Voice Test</b> ခလုတ်နဲ့ အသံကို ချက်ချင်း စမ်းနားထောင်နိုင်ပါတယ်။"
            )
        await send_text(
            chat_id,
            f"<b>🗣️ VOICE-01</b>\n{message}",
            reply_to=reply_msg_id,
            reply_markup=voice_lab_keyboard(),
        )
    except Exception as exc:
        logger.warning("Could not select voice-01: %s", str(exc)[:120])
        await send_text(chat_id, "<b>⚠️ Voice-01 ကို ရွေးလို့မရသေးပါဘူး။</b>")


async def handle_voice_clone_job(chat_id: int, reply_msg_id: int, sample: dict) -> None:
    """Clone an authorized Telegram sample, activate it, and send a preview."""
    status_id = await send_text(
        chat_id,
        "<b>🎙️ VOICE CLONING</b>\nVoice sample ကို ဒေါင်းလုဒ်ဆွဲပြီး စစ်ဆေးနေပါတယ်...",
        reply_to=reply_msg_id,
    )
    work_dir = tempfile.mkdtemp(prefix="voice_clone_")
    try:
        if not VOICE_CLONE_ENABLED:
            raise RuntimeError("Voice cloning is disabled by VOICE_CLONE_ENABLED")
        if not ELEVENLABS_API_KEY:
            raise RuntimeError("ELEVENLABS_API_KEY is required for voice cloning")

        source_path = os.path.join(work_dir, "telegram_reference.audio")
        reference_path = os.path.join(work_dir, "clone_reference.mp3")
        await download_telegram_voice_sample(sample, source_path)
        duration = await prepare_voice_clone_sample(source_path, reference_path)
        await edit_text(
            chat_id,
            status_id,
            "<b>🧬 CREATING VOICE CLONE</b>\n"
            f"{duration:.0f} စက္ကန့် reference ကနေ natural narration voice ဆောက်နေပါတယ်...",
        )
        cloned_id = await create_elevenlabs_voice_clone(
            reference_path,
            "Rednote MM Custom Narrator",
        )
        await set_chat_voice_clone(chat_id, cloned_id)
        await edit_text(
            chat_id,
            status_id,
            "<b>✅ CUSTOM VOICE CLONE ACTIVE</b>\n"
            "ဒီ chat ရဲ့ နောက် Recap တွေမှာ clone အသံကို ဦးစားပေးသုံးပါမယ်။ "
            "Voice-01 ပြန်သုံးချင်ရင် /voice01 ကို နှိပ်ပါ။\n"
            "<i>စမ်းနားထောင်ဖို့ short preview လည်း ပို့ပေးပါမယ်။</i>",
        )

        preview_path = os.path.join(work_dir, "clone_preview.mp3")
        preview_voice = await synthesize_elevenlabs_tts(
            "ကျွန်တော့်အသံကို စမ်းနားထောင်ကြည့်ပါ။ နောက်ထပ် Recap video တွေကို ဒီအသံနဲ့ ပြောပြပေးသွားပါမယ်။",
            preview_path,
            cloned_id,
        )
        if preview_voice:
            with open(preview_path, "rb") as preview_file:
                await tg_api(
                    "sendAudio",
                    data={
                        "chat_id": str(chat_id),
                        "caption": "<b>🎧 CUSTOM VOICE PREVIEW</b>",
                        "parse_mode": "HTML",
                        "title": "Burmese recap voice clone preview",
                        "reply_to_message_id": str(reply_msg_id),
                    },
                    files={
                        "audio": (
                            "custom_voice_preview.mp3",
                            preview_file,
                            "audio/mpeg",
                        )
                    },
                    timeout=180.0,
                )
        else:
            logger.warning("Custom voice was cloned, but its preview could not be synthesized")
    except Exception as exc:
        logger.exception("Voice clone job failed")
        await edit_text(
            chat_id,
            status_id,
            f"<b>❌ VOICE CLONE FAILED</b>\n{html.escape(str(exc)[:180])}",
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def handle_video_job(chat_id: int, reply_msg_id: int, url: str, job_id: str = None):
    job_id = job_id or secrets.token_hex(4)
    status_id = await send_text(
        chat_id,
        f"<b>⏳ RECAP QUEUED</b> <code>#{job_id}</code>\n"
        "Worker အလင်းရရင် stage-by-stage MB progress ကို ဒီ card မှာ ပြပါမယ်။",
        reply_to=reply_msg_id,
    )
    dyy_agent = DYYAgent(chat_id)
    progress = JobProgress(
        chat_id,
        status_id,
        job_id,
        event_callback=dyy_agent.report,
    )
    await progress.emit(
        "queued",
        detail="Rednote link accepted; DYY is monitoring the queue and worker.",
        stage="queue",
    )

    async with job_semaphore:
        work_dir = None
        await progress.start()
        try:
            work_dir = tempfile.mkdtemp(prefix="xhs_mm_")
            orig_video = os.path.join(work_dir, "orig.mp4")
            await progress.set_stage(
                "download",
                "Finding the source and downloading video…",
                current=0,
            )
            progress.watch_pattern(os.path.join(work_dir, "orig*"))

            async def download_stage():
                downloaded = await download_rednote_video(url, orig_video)
                if not downloaded:
                    raise RetryableJobError("Download failed after all available methods")

            await run_with_retries(download_stage, progress, "download")
            progress.unwatch()
            original_size = os.path.getsize(orig_video)
            await progress.complete("download", f"{format_mb(original_size)} downloaded")

            dur = await asyncio.to_thread(get_media_duration, orig_video)
            if dur <= 0.5:
                raise PermanentJobError("No playable video was found; this may be a photo post")
            if MAX_VIDEO_MINUTES > 0 and dur > MAX_VIDEO_MINUTES * 60:
                raise PermanentJobError(
                    f"Duration limit: this server accepts up to {MAX_VIDEO_MINUTES:g} minutes"
                )

            target_size = max(1000, int(original_size * FINAL_SIZE_RATIO))
            estimated_audio_scratch = int(dur * SAMPLE_RATE * 6)
            free_bytes = shutil.disk_usage(work_dir).free
            if free_bytes < target_size + estimated_audio_scratch:
                raise PermanentJobError(
                    "Server disk space is insufficient; about "
                    f"{human_file_size(target_size + estimated_audio_scratch)} more is required"
                )

            audio_mp3 = os.path.join(work_dir, "speech.mp3")
            audio_8k_pcm = os.path.join(work_dir, "speech8k.pcm")
            await progress.set_stage(
                "script",
                "Extracting speech for first-person script analysis…",
                current=0,
            )
            progress.watch_file(audio_mp3)

            async def extract_analysis_audio():
                audio_proc = await asyncio.to_thread(
                    run_cmd,
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        orig_video,
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-b:a",
                        "32k",
                        audio_mp3,
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-f",
                        "s16le",
                        "-acodec",
                        "pcm_s16le",
                        "-ac",
                        "1",
                        "-ar",
                        "8000",
                        audio_8k_pcm,
                    ],
                    FFMPEG_TIMEOUT,
                )
                if audio_proc.returncode != 0 or not os.path.exists(audio_mp3):
                    raise PermanentJobError("No usable speech audio was found in this video")

            await run_with_retries(extract_analysis_audio, progress, "script")
            progress.unwatch()
            await progress.set_stage(
                "script",
                "Gemini is writing the complete first-person Myanmar recap…",
            )
            wav_8k = (
                np.memmap(audio_8k_pcm, dtype=np.int16, mode="r")
                if os.path.exists(audio_8k_pcm) and os.path.getsize(audio_8k_pcm) > 0
                else np.zeros(8000, dtype=np.int16)
            )

            async def script_progress(detail):
                await progress.update(detail=detail, force=True)

            segments = await run_with_retries(
                lambda: create_recap_script_gemini(
                    audio_mp3,
                    dur,
                    wav_8k,
                    progress_callback=script_progress,
                ),
                progress,
                "script",
            )
            wav_8k = None
            for analysis_path in (audio_mp3, audio_8k_pcm):
                try:
                    os.remove(analysis_path)
                except OSError:
                    pass
            if not segments:
                raise PermanentJobError("No speech was found for a recap")
            await progress.complete("script", f"{len(segments)} first-person segments")

            await progress.set_stage(
                "voice",
                f"0/{len(segments)} segments · selecting narrator…",
            )

            voice_stats = {"successful": 0}

            async def voice_progress(processed, total, successful):
                voice_stats["successful"] = successful
                await progress.update(
                    detail=f"{processed}/{total} processed · {successful} synthesized",
                    force=processed == 1 or processed == total or processed % 3 == 0,
                )

            clone_voice_id = await run_with_retries(
                lambda: get_narration_clone_voice_id(chat_id),
                progress,
                "voice",
            )
            dub_mp3 = os.path.join(work_dir, "burmese_dub.mp3")

            async def synthesize_stage():
                return await synthesize_dubbed_track(
                    segments,
                    dur,
                    work_dir,
                    dub_mp3,
                    clone_voice_id,
                    progress_callback=voice_progress,
                )

            tts_provider = await run_with_retries(synthesize_stage, progress, "voice")
            dub_size = os.path.getsize(dub_mp3) if os.path.exists(dub_mp3) else 0
            await progress.complete(
                "voice",
                f"{voice_stats['successful']}/{len(segments)} synthesized · "
                f"{format_mb(dub_size)} · {tts_provider}",
            )

            await progress.set_stage(
                "render",
                f"Encoding toward {format_mb(target_size)} ({int(FINAL_SIZE_RATIO * 100)}%)…",
                current=0,
                total=target_size,
            )
            final_video = os.path.join(work_dir, "final_mm.mp4")
            progress.watch_pattern(os.path.join(work_dir, "final_mm*.mp4"))

            async def render_progress(stats, encode_pass):
                try:
                    encoded_seconds = max(0.0, float(stats.get("out_time_ms", 0)) / 1_000_000)
                except (TypeError, ValueError):
                    encoded_seconds = 0.0
                try:
                    written_bytes = max(0, int(stats.get("total_size", 0)))
                except (TypeError, ValueError):
                    written_bytes = None
                timeline_percent = min(100, int(encoded_seconds / max(dur, 0.001) * 100))
                speed = str(stats.get("speed", "")).strip()
                pass_label = f"Corrective pass {encode_pass}/2" if encode_pass > 1 else "Encoding"
                detail = f"{pass_label} · {timeline_percent}% timeline"
                if speed and speed != "N/A":
                    detail += f" · {speed}"
                detail += f" · {int(FINAL_SIZE_RATIO * 100)}% size target"
                await progress.update(
                    detail=detail,
                    current=written_bytes,
                    total=target_size,
                )

            async def render_stage():
                return await encode_video_to_target_size(
                    orig_video,
                    final_video,
                    target_size,
                    dur,
                    dub_mp3,
                    progress_callback=render_progress,
                )

            final_size = await run_with_retries(render_stage, progress, "render")
            progress.unwatch()
            await progress.complete("render", f"{format_mb(final_size)} final video")

            for intermediate in (orig_video, dub_mp3):
                try:
                    os.remove(intermediate)
                except OSError:
                    pass

            free_for_parts = shutil.disk_usage(work_dir).free
            if final_size > TELEGRAM_PART_LIMIT_BYTES and free_for_parts < final_size:
                raise PermanentJobError(
                    f"Server needs about {human_file_size(final_size)} free space to split this video"
                )

            await progress.set_stage(
                "upload",
                "Preparing playable Telegram-safe MP4 parts…",
                current=0,
                total=final_size,
            )
            parts = await run_with_retries(
                lambda: split_video_for_telegram(final_video, work_dir),
                progress,
                "upload",
            )
            total_parts = len(parts)
            total_upload_bytes = sum(os.path.getsize(part) for part in parts)
            uploaded_bytes = 0
            await progress.update(
                detail=f"0/{total_parts} parts uploaded",
                current=0,
                total=total_upload_bytes,
                force=True,
            )

            for part_number, part_path in enumerate(parts, 1):
                part_size = os.path.getsize(part_path)
                await progress.update(
                    detail=f"Part {part_number}/{total_parts} · {format_mb(part_size)} uploading",
                    current=uploaded_bytes,
                    total=total_upload_bytes,
                    force=True,
                )
                caption = (
                    f"<b>✅ မြန်မာ Recap</b> • မူရင်း size ရဲ့ {int(FINAL_SIZE_RATIO * 100)}%"
                )
                if total_parts > 1:
                    caption += f" • Part {part_number}/{total_parts}"
                caption += copyright_caption(url, part_number)

                async def upload_part(part_path=part_path, part_number=part_number, caption=caption):
                    with open(part_path, "rb") as video_file:
                        send_res = await tg_api(
                            "sendVideo",
                            data={
                                "chat_id": str(chat_id),
                                "caption": caption,
                                "parse_mode": "HTML",
                                "reply_to_message_id": str(reply_msg_id),
                                "supports_streaming": "true",
                            },
                            files={
                                "video": (
                                    f"rednote_myanmar_part_{part_number:04d}.mp4",
                                    video_file,
                                    "video/mp4",
                                )
                            },
                            timeout=TELEGRAM_UPLOAD_TIMEOUT,
                        )
                    if not send_res.get("ok"):
                        description = (
                            send_res.get("description")
                            or send_res.get("error")
                            or "Telegram upload failed"
                        )
                        if is_transient_error(RuntimeError(str(description))):
                            raise RetryableJobError(str(description))
                        raise RuntimeError(str(description))

                await run_with_retries(upload_part, progress, "upload")
                uploaded_bytes += part_size
                await progress.update(
                    detail=f"Part {part_number}/{total_parts} uploaded",
                    current=uploaded_bytes,
                    total=total_upload_bytes,
                    force=True,
                )
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                await asyncio.sleep(0.35)

            await progress.complete(
                "upload",
                f"{total_parts} parts · {format_mb(uploaded_bytes)} delivered",
            )
            retry_jobs.pop(job_id, None)
            await progress.succeed(
                f"{format_mb(final_size)} · {total_parts} Telegram part(s) · Done"
            )
            logger.info(
                "Completed job %s for chat %s: %s final, %s parts, TTS=%s",
                job_id,
                chat_id,
                human_file_size(final_size),
                total_parts,
                tts_provider,
            )

        except Exception as exc:
            logger.exception("Job %s processing error", job_id)
            allow_retry = not isinstance(exc, PermanentJobError)
            if allow_retry:
                remember_retry_job(job_id, chat_id, reply_msg_id, url)
            message = str(exc) or exc.__class__.__name__
            await progress.fail(message, allow_retry=allow_retry)
        finally:
            await progress.stop()
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
