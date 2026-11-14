from pathlib import Path
from PIL import Image, ImageDraw

res = Path(__file__).parent / "res"
(res / "mipmap-xxxhdpi").mkdir(parents=True, exist_ok=True)
(res / "drawable-xxhdpi").mkdir(parents=True, exist_ok=True)

S = 192
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=44, fill=(30, 100, 200, 255))
d.arc((44, 44, 148, 148), 40, 320, fill="white", width=18)          # "C"
d.ellipse((118, 56, 150, 88), fill=(255, 193, 7, 255))             # bildirim noktası
img.save(res / "mipmap-xxxhdpi" / "ic_launcher.png")

T = 72  # bildirim ikonu: şeffaf zemin üzerine beyaz
st = Image.new("RGBA", (T, T), (0, 0, 0, 0))
ImageDraw.Draw(st).arc((10, 10, 62, 62), 40, 320, fill="white", width=10)
st.save(res / "drawable-xxhdpi" / "ic_stat.png")
