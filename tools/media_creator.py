async def run(context: dict) -> str:
    """Generate images (gpt-image-1) or videos (sora) via OpenAI API."""
    import base64
    import os
    import tempfile
    import logging

    logger = logging.getLogger(__name__)

    args = context.get("args", []) or []
    raw_extra = context.get("raw_extra", "") or " ".join(args)
    prompt = raw_extra.strip()

    if not prompt:
        return "Please provide a description of what to create. Example: create a photo of a sunset over the sea"

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    if not OPENAI_API_KEY:
        return "OpenAI API key not configured."

    # Detect video intent
    _VIDEO_WORDS = {
        "video", "видео", "ролик", "клип", "анимацию", "анимация",
        "animate", "animation", "motion", "clip",
    }
    prompt_words = set(prompt.lower().split())
    is_video = bool(prompt_words & _VIDEO_WORDS)

    if is_video:
        return await _generate_video(prompt, OPENAI_API_KEY)
    else:
        return await _generate_image(prompt, OPENAI_API_KEY)


async def _generate_image(prompt: str, api_key: str) -> str:
    """Generate an image with gpt-image-1 and return __IMAGE_FILE__: marker."""

    def _compress_if_needed(data: bytes, max_bytes: int) -> bytes:
        """Compress PNG to JPEG with decreasing quality until under max_bytes."""
        if len(data) <= max_bytes:
            return data
        try:
            from io import BytesIO
            from PIL import Image
            img = Image.open(BytesIO(data)).convert("RGB")
            for quality in (85, 70, 55, 40):
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()
            # Last resort: resize down
            img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=60)
            return buf.getvalue()
        except ImportError:
            return data  # Pillow not installed, return as-is

    import aiohttp
    import base64
    import json
    import tempfile
    import logging

    MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

    logger = logging.getLogger(__name__)

    url = "https://api.openai.com/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-image-1",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
    }

    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error("Image generation failed: HTTP %d — %s", resp.status, body[:500])
                    return f"Image generation failed (HTTP {resp.status}). Please try again."
                data = json.loads(body)
    except Exception as e:
        logger.error("Image generation request error: %s", e)
        return f"Image generation error: {e}"

    # gpt-image-1 returns base64 data
    items = data.get("data", [])
    if not items:
        return "No image was generated. Please try a different prompt."

    item = items[0]
    b64_data = item.get("b64_json", "")
    image_url = item.get("url", "")

    if b64_data:
        img_bytes = base64.b64decode(b64_data)
        img_bytes = _compress_if_needed(img_bytes, MAX_IMAGE_BYTES)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(img_bytes)
        tmp.close()
        return f"__IMAGE_FILE__:{tmp.name}:{prompt[:100]}"
    elif image_url:
        # Download the image from URL
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as img_resp:
                    if img_resp.status == 200:
                        img_bytes = await img_resp.read()
                        img_bytes = _compress_if_needed(img_bytes, MAX_IMAGE_BYTES)
                        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        tmp.write(img_bytes)
                        tmp.close()
                        return f"__IMAGE_FILE__:{tmp.name}:{prompt[:100]}"
        except Exception as e:
            logger.error("Failed to download generated image: %s", e)
        return f"Image was generated but could not be downloaded."
    else:
        return "Unexpected response format from image generation API."


async def _generate_video(prompt: str, api_key: str) -> str:
    """Generate a video with sora-2 and return __VIDEO_FILE__: marker."""
    import aiohttp
    import json
    import tempfile
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Step 1: Create video generation job
    create_url = "https://api.openai.com/v1/videos"
    payload = {
        "model": "sora-2",
        "prompt": prompt,
        "size": "1280x720",
        "seconds": "8",
    }

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(create_url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status not in (200, 201, 202):
                    logger.error("Video generation failed: HTTP %d — %s", resp.status, body[:500])
                    return f"Video generation failed (HTTP {resp.status}). Sora may not be available on your plan."
                data = json.loads(body)
    except Exception as e:
        logger.error("Video generation request error: %s", e)
        return f"Video generation error: {e}"

    video_id = data.get("id", "")
    if not video_id:
        return "Unexpected response from video API — no job ID returned."

    # Step 2: Poll for completion
    poll_url = f"https://api.openai.com/v1/videos/{video_id}"
    poll_timeout = aiohttp.ClientTimeout(total=15)
    for _ in range(60):  # up to ~5 minutes
        await asyncio.sleep(5)
        try:
            async with aiohttp.ClientSession(timeout=poll_timeout) as session:
                async with session.get(poll_url, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        continue
                    status_data = json.loads(body)
        except Exception:
            continue

        status = status_data.get("status", "")
        if status == "completed":
            # Step 3: Download the MP4
            return await _download_video(video_id, api_key, prompt)
        elif status in ("failed", "cancelled"):
            error_msg = status_data.get("error", {}).get("message", "Unknown error") if isinstance(status_data.get("error"), dict) else str(status_data.get("error", "Unknown error"))
            return f"Video generation {status}: {error_msg}"

    return "Video generation timed out. Please try again later."


async def _download_video(video_id: str, api_key: str, prompt: str) -> str:
    """Download video content and return __VIDEO_FILE__: marker."""
    import aiohttp
    import tempfile

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"https://api.openai.com/v1/videos/{video_id}/content"

    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    video_bytes = await resp.read()
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    tmp.write(video_bytes)
                    tmp.close()
                    return f"__VIDEO_FILE__:{tmp.name}:{prompt[:100]}"
                else:
                    body = await resp.text()
                    return f"Failed to download video (HTTP {resp.status}): {body[:200]}"
    except Exception as e:
        return f"Failed to download video: {e}"
