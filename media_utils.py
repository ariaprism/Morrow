"""Small, dependency-free helpers for identifying uploaded image bytes."""


IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def detect_image_mime(blob):
    """Return the MIME encoded by common image signatures, or ``None``."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    head = bytes(blob[:16])
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_extension(mime):
    return IMAGE_EXTENSIONS.get(mime)
