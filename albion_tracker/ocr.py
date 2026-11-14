from PIL import Image
import numpy as np

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def read(img: Image.Image, upscale: float = 1.0, text_score: float | None = None) -> list[dict]:
    """Zwraca liste {text, score, x0, y0, x1, y1, cx, cy} (wspolrzedne w pikselach img)."""
    if upscale != 1.0:
        img = img.resize((int(img.width * upscale), int(img.height * upscale)), Image.LANCZOS)
    kwargs = {"text_score": text_score} if text_score is not None else {}
    result, _ = _get_engine()(np.array(img.convert("RGB")), **kwargs)
    out = []
    for box, text, score in result or []:
        xs = [p[0] / upscale for p in box]
        ys = [p[1] / upscale for p in box]
        out.append({
            "text": text, "score": float(score),
            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
            "cx": (min(xs) + max(xs)) / 2, "cy": (min(ys) + max(ys)) / 2,
        })
    return out


def recognize(imgs: list[Image.Image]) -> list[tuple[str, float]]:
    """Samo rozpoznawanie tekstu (bez detekcji) dla ciasno przycietych obrazow jednej linii.
    Duzo szybsze niz read() - cala partia idzie jednym wywolaniem modelu."""
    if not imgs:
        return []
    arrs = [np.array(i.convert("RGB"))[:, :, ::-1].copy() for i in imgs]
    eng = _get_engine()
    # w rapidocr-onnxruntime <= 1.2 element nazywa sie text_recognizer, w 1.3+ text_rec
    rec_model = getattr(eng, "text_recognizer", None) or getattr(eng, "text_rec")
    rec, _ = rec_model(arrs)
    return [(r[0], float(r[1])) for r in rec]
