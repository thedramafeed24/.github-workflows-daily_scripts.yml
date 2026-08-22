import os
import asyncio
import smtplib
import tempfile
import logging
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import List, Dict

import google.generativeai as genai
import edge_tts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Credentials from GitHub Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")

VOICE = os.environ.get("VOICE", "en-US-ChristopherNeural")

THEMES = [
    "Partner humiliates narrator at dinner with friends",
    "Fiancé mocks narrator during a birthday toast",
    "In-laws disrespect narrator's career at a family gathering",
    "Partner brags online about settling for the narrator",
    "Partner mocks narrator during a housewarming party"
]

SYSTEM_PROMPT = """
You are a viral short-form fiction narrator. Write a 1st-person Reddit-style revenge/drama script under 220 words.
Structure:
- Hook (0-3s): Direct public humiliation or disrespect in front of peers/family.
- Action: Cold, calm, immediate consequence from narrator.
- Fallout: Offender spiraling, third parties siding with narrator.
- Outro: Punchy closing statement on self-respect.

Output ONLY the raw spoken text. Do NOT include titles, speaker tags, or scene brackets.
"""

MAX_WORDS = 220
GEMINI_MODEL = "gemini-1.5-flash"
GENERATION_RETRIES = 2
TTS_RETRIES = 2

def validate_env():
    missing = [name for name, val in (
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("SENDER_EMAIL", SENDER_EMAIL),
        ("SENDER_APP_PASSWORD", SENDER_APP_PASSWORD),
        ("RECEIVER_EMAIL", RECEIVER_EMAIL),
    ) if not val]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

def enforce_word_limit(text: str, max_words: int = MAX_WORDS) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])

def generate_scripts() -> List[Dict]:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT
    )
    
    scripts = []
    for i, theme in enumerate(THEMES, start=1):
        prompt = f"Scenario: {theme}"
        logger.info("Generating script %d/%d for theme: %s", i, len(THEMES), theme)
        for attempt in range(1, GENERATION_RETRIES + 1):
            try:
                response = model.generate_content(prompt)
                text = response.text.strip()
                text = enforce_word_limit(text, MAX_WORDS)
                scripts.append({"index": i, "theme": theme, "text": text})
                break
            except Exception as exc:
                logger.exception("Generation attempt %d failed for theme %s", attempt, theme)
        else:
            raise RuntimeError(f"Failed to generate script for theme '{theme}'")
    return scripts

async def text_to_speech(text: str, output_path: Path, voice: str = VOICE):
    for attempt in range(1, TTS_RETRIES + 1):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))
            return
        except Exception:
            logger.exception("TTS attempt %d failed for %s", attempt, output_path)
            if attempt == TTS_RETRIES:
                raise
            await asyncio.sleep(1 + attempt)

def build_email_message(scripts: List[Dict], audio_files: List[Path], today_str: str) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = f"🔥 Your 5 Daily Drama Audio Files & Scripts - {today_str}"

    email_body_text = f"Here are your {len(scripts)} daily scripts and audio voiceovers for {today_str}:\n\n"
    for item in scripts:
        idx = item["index"]
        email_body_text += f"=== SCRIPT {idx}: {item['theme'].upper()} ===\n{item['text']}\n\n" + ("-" * 40) + "\n\n"

    msg.attach(MIMEText(email_body_text, "plain", "utf-8"))

    for path in audio_files:
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="mp3")
        filename = path.name
        part.add_header("Content-Disposition", 'attachment', filename=filename)
        msg.attach(part)

    return msg

async def main():
    validate_env()
    today = datetime.now().strftime("%Y-%m-%d")

    logger.info("Starting generation of scripts...")
    scripts = await asyncio.to_thread(generate_scripts)

    temp_files: List[Path] = []
    tts_tasks = []
    try:
        for item in scripts:
            tmp = Path(tempfile.mkstemp(prefix=f"story_{item['index']}_", suffix=".mp3")[1])
            temp_files.append(tmp)
            tts_tasks.append(text_to_speech(item["text"], tmp, VOICE))

        logger.info("Converting %d scripts to TTS concurrently...", len(tts_tasks))
        await asyncio.gather(*tts_tasks)

        msg = build_email_message(scripts, temp_files, today)

        logger.info("Sending email to %s...", RECEIVER_EMAIL)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        logger.info("Email sent successfully.")

    finally:
        for p in temp_files:
            try:
                p.unlink()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
