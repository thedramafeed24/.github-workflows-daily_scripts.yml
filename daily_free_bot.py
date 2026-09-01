import asyncio
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import tempfile
import time
from typing import Dict, List

import edge_tts
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Credentials from GitHub Secrets / Environment
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_APP_PASSWORD = os.environ.get("SENDER_APP_PASSWORD")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")

# Voice selection
MALE_VOICE = "en-US-ChristopherNeural"
FEMALE_VOICE = "en-US-AvaNeural"

THEMES = [
    (
        "Fiancé and my maid of honor secretly conspired to drain my family trust after the wedding, "
        "unaware the prenup contains an immediate fault-based asset forfeiture clause."
    ),
    (
        "In-laws publicly humiliated me at an anniversary dinner to announce their golden child gets the family penthouse, "
        "unaware the deed, mortgage, and equity are owned by my private LLC."
    ),
    (
        "Spouse and their executive lover orchestrated my dismissal to hide their affair, "
        "blind to the fact that the forensic audit I triggered logged every transaction and hotel receipt."
    ),
    (
        "Mother-in-law secretly swapped my late mother's heirloom jewelry with fakes for her daughter's wedding, "
        "unaware the genuine pieces were vault-locked with micro-engraved serial tags."
    ),
    (
        "Husband's wealthy family staged a public paternity accusation at the baby shower to disinherit me, "
        "unaware the fertility clinic records on the projector proved he was sterile all along."
    ),
]

SYSTEM_PROMPT = """
You are a master short-form drama writer creating viral, hyper-realistic, 1st-person revenge stories for Shorts and Reels.

You will be given a list of 5 scenarios. Write a complete production-ready script for each scenario.

EACH SCRIPT MUST STRICTLY FOLLOW THIS ARCHITECTURE (Target: 130 to 145 words spoken text):
1. HOOK (0-3s): Drop straight into a specific, jaw-dropping moment of public betrayal, disrespect, or family humiliation.
2. ESCALATION (4-30s): The antagonist visibly celebrates their perceived victory, assuming the narrator is defenseless.
3. THE TRAP / TWIST (31-40s): Reveal the narrator anticipated the betrayal months prior using concrete leverage (forensic audits, property deeds, prenups, digital receipts, vault records, or legal traps).
4. THE RUIN (41-45s): Irreversible legal, financial, or social devastation for the antagonist.
5. INFINITE LOOP LINE (Last sentence): Craft the final phrase so it seamlessly flows syntactically straight back into the first sentence.

OUTPUT FORMAT: Strict JSON Array containing exactly 5 objects. No markdown backticks:
[
  {
    "index": 1,
    "title": "High-CTR click-worthy title under 45 characters",
    "visual_hook_text": "PUNCHY 4-7 WORD ALL-CAPS BANNER (e.g., 'THEY FORGOT WHO OWNS THE DEED 💀')",
    "narrator_gender": "female" or "male",
    "text": "Full narrative spoken voiceover text..."
  },
  ...
]
"""

MAX_WORDS = 150
GEMINI_MODEL = "gemini-3.6-flash"


def validate_env():
    missing = [
        name
        for name, val in (
            ("GEMINI_API_KEY", GEMINI_API_KEY),
            ("SENDER_EMAIL", SENDER_EMAIL),
            ("SENDER_APP_PASSWORD", SENDER_APP_PASSWORD),
            ("RECEIVER_EMAIL", RECEIVER_EMAIL),
        )
        if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def sanitize_filename(title: str) -> str:
    clean = re.sub(r"[^\w\s-]", "", title).strip()
    clean = re.sub(r"[-\s]+", "_", clean)
    return clean[:45].strip("_")


def enforce_word_limit(text: str, max_words: int = MAX_WORDS) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])


def generate_all_scripts() -> List[Dict]:
    """Generates all 5 scripts in a single API call to prevent rate limiting."""
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt_content = "Generate scripts for these 5 scenarios:\n"
    for i, theme in enumerate(THEMES, start=1):
        prompt_content += f"{i}. {theme}\n"

    logger.info("Requesting all 5 scripts in a single batch request...")

    # Retry with wait time if rate-limited
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_content,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.80,
                ),
            )
            raw_json = response.text.replace("```json", "").replace("```", "").strip()
            parsed_list = json.loads(raw_json)

            scripts = []
            for i, item in enumerate(parsed_list, start=1):
                theme = THEMES[i - 1] if i <= len(THEMES) else item.get("title", "")
                gender = item.get("narrator_gender", "female").strip().lower()
                text = enforce_word_limit(item.get("text", ""), MAX_WORDS)
                title = item.get("title", f"Revenge Story {i}").strip()
                visual_hook_text = item.get("visual_hook_text", "WAIT FOR THE END 💀").strip()

                voice = FEMALE_VOICE if "female" in gender else MALE_VOICE
                filename = f"{i}_{sanitize_filename(title)}.mp3"

                scripts.append({
                    "index": i,
                    "theme": theme,
                    "title": title,
                    "filename": filename,
                    "visual_hook_text": visual_hook_text,
                    "gender": gender,
                    "voice": voice,
                    "text": text,
                })

            logger.info("Successfully generated and parsed all 5 scripts!")
            return scripts

        except Exception as exc:
            logger.warning("Attempt %d failed: %s", attempt, exc)
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                logger.info("Rate limit hit. Sleeping for 60 seconds before retrying...")
                time.sleep(60)
            elif attempt == 3:
                raise RuntimeError("Failed to generate batch scripts after 3 attempts.") from exc
            else:
                time.sleep(5)


async def text_to_speech(
    text: str,
    output_path: Path,
    voice: str,
    rate: str = "+12%",
    pitch: str = "-1Hz",
):
    for attempt in range(1, 3):
        try:
            communicate = edge_tts.Communicate(
                text=text, voice=voice, rate=rate, pitch=pitch
            )
            await communicate.save(str(output_path))
            return
        except Exception:
            logger.exception("TTS attempt %d failed for %s", attempt, output_path)
            if attempt == 2:
                raise
            await asyncio.sleep(2)


def build_email_message(
    scripts: List[Dict], audio_files: List[Path], today_str: str
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = f"🔥 5 Production-Ready Scripts, Visual Hooks & Named Audio - {today_str}"

    html_parts = [
        f"<h2>Daily High-Retention Drama Production Batch ({today_str})</h2>",
        "<p>Titles, CapCut Hook Banners, and matching audio files ready for editing.</p><hr>",
    ]

    for item in scripts:
        idx = item["index"]
        gender_badge = (
            '<span style="background-color: #fce4ec; color: #c2185b; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">👩 Female Voice (Ava)</span>'
            if "female" in item["gender"]
            else '<span style="background-color: #e3f2fd; color: #1976d2; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">👨 Male Voice (Christopher)</span>'
        )
        html_parts.append(
            f"""
            <div style="margin-bottom: 28px; padding: 18px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <span style="font-size: 13px; font-weight: bold; color: #64748b;">STORY #{idx}</span>
                    {gender_badge}
                </div>
                
                <h3 style="margin: 0 0 12px 0; color: #0f172a; font-size: 18px;">📌 Title: {item['title']}</h3>
                
                <div style="background-color: #fef08a; color: #854d0e; padding: 10px 14px; border-radius: 6px; font-weight: bold; font-size: 14px; margin-bottom: 14px; border: 1px dashed #ca8a04;">
                    ⚡ CapCut Visual Hook (0-3s Banner): "{item['visual_hook_text']}"
                </div>

                <div style="background-color: #f8fafc; border-left: 4px solid #ef4444; padding: 14px; border-radius: 4px; margin-bottom: 12px;">
                    <strong style="color: #334155; display: block; margin-bottom: 6px; font-size: 13px;">🎙️ SPOKEN NARRATIVE:</strong>
                    <p style="margin: 0; font-size: 15px; line-height: 1.6; color: #1e293b;">
                        {item['text']}
                    </p>
                </div>
                
                <span style="font-size: 12px; color: #64748b;">📁 Audio Attachment: <strong style="color: #0f172a;">{item['filename']}</strong></span>
            </div>
            """
        )

    msg.attach(MIMEText("".join(html_parts), "html", "utf-8"))

    for path, item in zip(audio_files, scripts):
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="mp3")
        part.add_header("Content-Disposition", "attachment", filename=item["filename"])
        msg.attach(part)

    return msg


async def main():
    validate_env()
    today = datetime.now().strftime("%Y-%m-%d")

    logger.info("Starting batch script generation...")
    scripts = await asyncio.to_thread(generate_all_scripts)

    temp_files: List[Path] = []
    tts_tasks = []
    try:
        for item in scripts:
            tmp = Path(
                tempfile.mkstemp(prefix=f"{item['filename']}_", suffix=".mp3")[1]
            )
            temp_files.append(tmp)
            tts_tasks.append(text_to_speech(item["text"], tmp, item["voice"]))

        logger.info("Synthesizing voiceovers concurrently...")
        await asyncio.gather(*tts_tasks)

        msg = build_email_message(scripts, temp_files, today)

        logger.info("Sending email to %s...", RECEIVER_EMAIL)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        logger.info("Batch email sent successfully.")

    finally:
        for p in temp_files:
            try:
                p.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
