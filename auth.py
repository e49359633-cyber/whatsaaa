"""
Скрипт для одноразовой авторизации userbot (Pyrogram).
Запустите ОДИН раз локально: python auth.py
Введите номер телефона и код из Telegram.
После этого появится файл userbot_session.session — загрузите его на хостинг вместе с bot.py
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()
from pyrogram import Client

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

async def main():
    app = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)
    async with app:
        me = await app.get_me()
        print(f"\n✅ Авторизация успешна!")
        print(f"👤 Аккаунт: {me.first_name} (@{me.username})")
        print(f"🆔 ID: {me.id}")
        print(f"\n📁 Файл сессии: userbot_session.session")
        print(f"Загрузите этот файл на хостинг в папку с bot.py")

if __name__ == "__main__":
    asyncio.run(main())
