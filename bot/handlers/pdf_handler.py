"""
Handler for PDF uploads and /pdf command.
"""
import html
import io

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Document, Message

from bot.services.pdf_processor import PDFProcessor
from bot.utils.logger import get_logger

logger = get_logger("pdf_handler")
router = Router()

MAX_TELEGRAM_MSG = 4000


def _split_message(text: str, limit: int = MAX_TELEGRAM_MSG):
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts


@router.message(Command("pdf"))
async def pdf_command_handler(message: Message) -> None:
    await message.reply(
        "📄 <b>PDF Analysis</b>\n\n"
        "Send me a PDF file as a document to analyze it.\n\n"
        "<b>Limits:</b>\n"
        "• Maximum file size: 20 MB\n"
        "• Must contain extractable text (not scanned images)\n\n"
        "<i>Send your PDF now!</i>",
    )


@router.message(F.document)
async def pdf_document_handler(
    message: Message,
    bot: Bot,
    pdf_processor: PDFProcessor,
) -> None:
    doc: Document = message.document
    filename = doc.file_name or "document.pdf"

    mime = doc.mime_type or ""
    if "pdf" not in mime.lower() and not filename.lower().endswith(".pdf"):
        await message.reply("⚠️ Only PDF files are supported. Please send a .pdf document.")
        return

    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)
    logger.info("PDF received from @{}: {} ({} bytes)", username, filename, doc.file_size)

    max_bytes = pdf_processor.max_pdf_bytes
    if doc.file_size and doc.file_size > max_bytes:
        await message.reply(
            f"❌ File too large ({doc.file_size / 1024 / 1024:.1f} MB). "
            f"Max: {max_bytes // 1024 // 1024} MB."
        )
        return

    safe_filename = html.escape(filename)
    status_msg = await message.reply(
        f"📄 Processing <b>{safe_filename}</b>...\n<i>Downloading...</i>",
    )

    try:
        file = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        pdf_bytes = buf.getvalue()

        await status_msg.edit_text(
            f"📄 Processing <b>{safe_filename}</b>...\n<i>Extracting and analyzing text...</i>",
        )

        result = await pdf_processor.process(pdf_bytes, filename=filename)
        await status_msg.edit_text("✅ PDF processed successfully!")

        header = (
            f"📄 <b>{html.escape(result['filename'])}</b>\n"
            f"📑 Pages: {result['page_count']} | 📝 Characters: {result['char_count']:,}\n\n"
        )

        full_response = header + html.escape(result["summary"])
        for part in _split_message(full_response):
            await message.reply(part)

        logger.info("PDF delivered to @{}: {} pages", username, result["page_count"])

    except ValueError as e:
        await status_msg.edit_text(f"❌ {html.escape(str(e))}")
    except Exception as e:
        logger.error("PDF failed for @{}: {}", username, e, exc_info=True)
        await status_msg.edit_text(
            "❌ Failed to process PDF. Ensure the file is not corrupted and contains extractable text."
        )
