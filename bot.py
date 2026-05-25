import asyncio
import os
from io import BytesIO

import replicate
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REPLICATE_TOKEN = os.environ["REPLICATE_API_TOKEN"]
MODEL = os.getenv("REPLICATE_MODEL", "xai/grok-imagine-image-quality")

os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Envame un prompt y te genero la imagen con Grok Imagine.\n\n"
        "Ejemplo: <i>a cat wearing a wizard hat in a neon-lit cyberpunk alley</i>\n\n"
        "Tambien puedes enviar una <b>foto con caption</b> para editarla:\n"
        "la IA tomara tu imagen y aplicara los cambios que describas en el caption.\n\n"
        "Modelo: <b>xai/grok-imagine-image-quality</b> (2K)",
        parse_mode="HTML",
    )


@dp.message(lambda m: m.photo and m.caption)
async def handle_edit(message: types.Message):
    prompt = message.caption.strip()
    status_msg = await message.answer("Editando imagen...")

    try:
        file = await bot.get_file(message.photo[-1].file_id)
        file_bytes = await bot.download_file(file.file_path)
        file_bytes.seek(0)
        image_data = BytesIO(file_bytes.read())
        image_data.name = "image.jpg"

        input_data = {
            "prompt": prompt,
            "image": image_data,
        }

        output = await asyncio.to_thread(
            replicate.run,
            MODEL,
            input=input_data,
            file_encoding_strategy="base64",
        )

        if output is None:
            await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
            return

        image_url = output[0] if isinstance(output, list) else output

        if hasattr(image_url, "url"):
            image_url = image_url.url

        image_bytes = await download_image(str(image_url))
        photo = BufferedInputFile(image_bytes, filename="grok_edited.png")
        await message.answer_photo(photo, caption=f"<b>Edit:</b> {prompt}", parse_mode="HTML")
        await status_msg.delete()

    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de Replicate: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


@dp.message(lambda m: m.photo and not m.caption)
async def handle_photo_without_caption(message: types.Message):
    await message.answer(
        "Para editar una imagen, enviala con un <b>caption</b> describiendo los cambios que quieres.\n\n"
        "Ejemplo: envia tu foto con el texto <i>\"cambia el fondo a una playa al atardecer\"</i>",
    )


@dp.message(lambda m: m.text and m.reply_to_message and m.reply_to_message.photo)
async def handle_reply_edit(message: types.Message):
    prompt = message.text.strip()

    if len(prompt) < 3:
        await message.answer("El prompt es muy corto. Dame algo mas descriptivo.")
        return

    status_msg = await message.answer("Editando imagen...")

    try:
        replied_photo = message.reply_to_message.photo[-1]
        file = await bot.get_file(replied_photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        file_bytes.seek(0)
        image_data = BytesIO(file_bytes.read())
        image_data.name = "image.jpg"

        input_data = {
            "prompt": prompt,
            "image": image_data,
        }

        output = await asyncio.to_thread(
            replicate.run,
            MODEL,
            input=input_data,
            file_encoding_strategy="base64",
        )

        if output is None:
            await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
            return

        image_url = output[0] if isinstance(output, list) else output

        if hasattr(image_url, "url"):
            image_url = image_url.url

        image_bytes = await download_image(str(image_url))
        photo = BufferedInputFile(image_bytes, filename="grok_edited.png")
        await message.answer_photo(photo, caption=f"<b>Edit:</b> {prompt}", parse_mode="HTML")
        await status_msg.delete()

    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de Replicate: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


@dp.message(lambda m: m.text)
async def handle_prompt(message: types.Message):
    prompt = message.text.strip()

    if len(prompt) < 3:
        await message.answer("El prompt es muy corto. Dame algo mas descriptivo.")
        return

    status_msg = await message.answer("Generando imagen...")

    try:
        output = await asyncio.to_thread(
            replicate.run,
            MODEL,
            input={"prompt": prompt},
        )

        if output is None:
            await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
            return

        image_url = output[0] if isinstance(output, list) else output

        if hasattr(image_url, "url"):
            image_url = image_url.url

        image_bytes = await download_image(str(image_url))
        photo = BufferedInputFile(image_bytes, filename="grok_image.png")
        await message.answer_photo(photo, caption=f"<b>Prompt:</b> {prompt}", parse_mode="HTML")
        await status_msg.delete()

    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de Replicate: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


async def download_image(url: str) -> bytes:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
