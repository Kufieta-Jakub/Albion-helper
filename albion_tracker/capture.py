import mss
from PIL import Image


def grab_screen(monitor: int = 1) -> Image.Image:
    with mss.mss() as sct:
        mon = sct.monitors[monitor]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def crop_fraction(img: Image.Image, region) -> Image.Image:
    """region = [left, top, right, bottom] jako ulamki 0..1 rozmiaru obrazu."""
    w, h = img.size
    l, t, r, b = region
    return img.crop((int(l * w), int(t * h), int(r * w), int(b * h)))
