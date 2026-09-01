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

import random

THEME_POOL = [
    # 1. Wedding & Prenup Betrayals
    "Fiancé and maid of honor secretly conspired to drain family trust funds after the wedding, unaware the prenup contains an immediate fault-based asset forfeiture clause.",
    "Groom's family demanded I sign away my pre-marital properties 10 minutes before walking down the aisle, unaware the officiant and venue are under my private corporate name.",
    "Bride's parents secretly redirected the wedding reception budget to pay off their golden son's gambling debt, unaware the vendors were contracted under strict personal liability clauses.",
    "Fiancé staged a fake infidelity scenario to break off our engagement without returning the family heirloom ring, unaware private surveillance logged his staged meetup.",
    "In-laws attempted to force a post-nuptial agreement during the honeymoon, unaware all marital property had already been placed into an irrevocable blind trust.",

    # 2. Inheritance & Will Frauds
    "In-laws publicly humiliated me at an anniversary dinner to announce their favorite child gets the family penthouse, unaware the deed, mortgage, and equity are owned by my LLC.",
    "Siblings altered our late parent's living will while I was overseas, unaware the original recorded video deposition is vaulted with the state probate registry.",
    "Stepmother attempted to lock me out of the family estate reading, unaware my mother's original trust requires a mandatory 100% forensic audit prior to asset transfer.",
    "Relatives auctioned off my late grandmother's antique collection behind my back, unaware each authentic item was micro-chipped and tracked via registered insurance tags.",
    "Estranged father attempted to claim sole ownership of my childhood home upon remarriage, unaware the title deed was legally transferred to my name five years prior.",

    # 3. Infidelity & Secret Trapdoor Climax
    "Spouse and their executive partner orchestrated my termination to hide their affair, unaware the forensic audit I triggered logged every expense report and flight booking.",
    "Partner bought a luxury condo under a shell company to host their affair, unaware I am the primary equity stakeholder of the parent management entity.",
    "Spouse attempted to wipe joint bank accounts before filing for divorce, unaware the automated freeze trigger flagged the transfers as illegal dissipation of marital assets.",
    "Partner introduced their secret lover as a new corporate consultant, unaware our board of directors requires full conflict-of-interest background clearances.",
    "Spouse staged a fake home burglary to claim insurance on my high-end watch collection, unaware hidden indoor cameras uploaded high-resolution backups to the cloud.",

    # 4. Family Humiliation & Public Ambushes
    "Husband's wealthy family staged a public paternity accusation at the baby shower to disinherit me, unaware the fertility clinic records on the projector proved he was sterile all along.",
    "Mother-in-law secretly swapped my late mother's heirloom jewelry with replicas for her daughter's wedding, unaware the genuine pieces were vaulted with serial tags.",
    "In-laws excluded me from the annual family portrait in front of 50 guests, unaware the venue, catering, and staff were funded entirely by my credit line.",
    "Sister-in-law attempted to move into my vacation villa while I was traveling, unaware the smart-lock security grid automatically deadbolts unauthorized entries.",
    "Family staged an intervention demanding I pay off my brother's debt, unaware my private investigator documented his secret offshore luxury assets.",

    # 5. Elite Workplace & Financial Sabotage
    "Business partner secretly signed an exclusive vendor contract to siphon startup profits, unaware the partnership agreement requires unanimous board consent for all vendor agreements.",
    "Executive manager blocked my promotion while claiming credit for my patent build, unaware the repository timestamp logs verified my sole authorship.",
    "Co-worker deleted executive audit data from my workstation to frame me, unaware the enterprise shadow server creates an unalterable real-time ledger.",
    "Company founders tried to dilute my equity stake hours before an acquisition buyout, unaware my original vesting contract contains a non-dilution veto clause.",
    "Manager forced me to train their unqualified nephew before attempting to fire me, unaware the client retention agreement is legally tied directly to my personal consulting license."
]

def get_daily_themes(count: int = 5) -> List[str]:
    """Randomly selects distinct scenarios from different drama categories each run."""
    return random.sample(THEME_POOL, count)

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
  }
]
"""

MAX_WORDS = 150
PRIMARY_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
]


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


def get_available_models(client) -> List[str]:
    """Combines prioritized models with dynamic model discovery."""
    discovered = []
    try:
        for m in client.models.list():
            model_name = m.name.replace("models/", "")
            if "gemini" in model_name and model_name not in PRIMARY_MODELS:
                discovered.append(model_name)
    except Exception as e:
        logger.warning("Dynamic model listing skipped: %s", e)

    return PRIMARY_MODELS + discovered


def generate_all_scripts() -> List[Dict]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    models_to_try = get_available_models(client)
    selected_themes = get_daily_themes(5)

    prompt_content = "Generate scripts for these 5 scenarios:\n"
    for i, theme in enumerate(selected_themes, start=1):
        prompt_content += f"{i}. {theme}\n"

    logger.info("Selected Themes for today's run:\n%s", prompt_content)
    # ... rest of the single-call generation function remains the same ...

    for model_name in models_to_try:
        logger.info("Attempting generation using model: %s", model_name)
        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(
                    model=model_name,
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

                logger.info("Successfully generated all 5 scripts with %s!", model_name)
                return scripts

            except Exception as exc:
                err_str = str(exc)
                logger.warning("Attempt %d on %s failed: %s", attempt, model_name, err_str)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.info("Rate limit hit on %s. Switching to next model candidate...", model_name)
                    break  # Move immediately to next model candidate
                time.sleep(2)

    raise RuntimeError("All candidate models failed. If daily quota is exhausted, create a new project key in AI Studio.")


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
