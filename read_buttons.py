"""Скрипт для чтения callback_data кнопок из @KosmicheskiyAvtoVbiv_bot"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()
from pyrogram import Client

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
TARGET = "KosmicheskiyAvtoVbiv_bot"

async def main():
    app = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)
    async with app:
        print(f"\n=== Последние сообщения от @{TARGET} ===\n")
        async for msg in app.get_chat_history(TARGET, limit=10):
            if msg.outgoing:
                print(f"[ВЫ] {msg.text or '(без текста)'}")
            else:
                print(f"[БОТ] {msg.text or '(без текста)'}")
                if msg.reply_markup:
                    if hasattr(msg.reply_markup, 'inline_keyboard'):
                        print("  📌 ИНЛАЙН-КНОПКИ:")
                        for row in msg.reply_markup.inline_keyboard:
                            for btn in row:
                                print(f"    [{btn.text}] → callback_data='{btn.callback_data}'")
                    elif hasattr(msg.reply_markup, 'keyboard'):
                        print("  ⌨️ КЛАВИАТУРА:")
                        for row in msg.reply_markup.keyboard:
                            for btn in row:
                                print(f"    [{btn.text}]")
            print("---")

if __name__ == "__main__":
    asyncio.run(main())
