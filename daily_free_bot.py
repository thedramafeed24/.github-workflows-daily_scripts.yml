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

# Credentials from GitHub Secrets / Environment
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")

VOICE = os.environ.get("VOICE", "en-US-ChristopherNeural")

THEMES = [
    "Boss takes credit for my multi-million dollar software build in front of the CEO",
    "Business partner secretly signs an exclusive deal to cut me out of our startup",
    "Co-worker tries to get me fired by tampering with an executive audit report",
    "Company owner denies my promised equity right before an acquisition",
    "Manager blocks my transfer while demanding I train their unqualified relative"
]

SYSTEM_PROMPT = """
You are an elite short-form drama writer. Create an original, hyper-realistic, 1st-person revenge/drama script under 220 words.

Rules:
- DO NOT use cliché tropes (e.g., throwing a ring into a glass, leaving someone with a restaurant bill, or pouring a drink).
- The revenge must be clever, legal, calculated, and modern (involving contracts, digital footprints, family politics, or financial leverage).
- Tone: Cold, composed, and devastating.
- Hook (0-3s): Drop straight into a specific, shocking moment of betrayal or disrespect without throat-clearing.
- Climax: An immediate, unexpected twist where the narrator reveals they held all the cards from the start.

Output ONLY the raw spoken text. No titles, intro phrases, or bracketed directions.
"""

MAX_WORDS = 220
GEMINI_MODEL = "gemini-1.5-flash-latest"
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
        system_instruction=SYSTEM_PROMPT,
        generation_config={"temperature": 0.85}
    )
    
    scripts = []
    for i, theme in enumerate(THEMES, start=1):
        prompt = f"Write an unpredictable, cold revenge script based on this situation: {theme}"
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
                if attempt == GENERATION_RETRIES:
                    raise RuntimeError(f"Failed to generate script for theme '{theme}'") from exc
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
