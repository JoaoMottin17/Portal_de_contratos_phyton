# -*- coding: utf-8 -*-
"""Gera logo.png (versao verde-sobre-branco) a partir do PDF oficial do grupo."""
import io
import fitz  # PyMuPDF
from PIL import Image, ImageChops

PDF = r"C:\Users\PICHAU\OneDrive\Grupo FRT\LOGOS\Logotipo Final.pdf"
SAIDA = "logo.png"

doc = fitz.open(PDF)
page = doc[0]
r = page.rect
# a versao VERDE sobre branco fica na metade de cima da pagina
clip = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.47)
pix = page.get_pixmap(dpi=600, clip=clip, alpha=False)
img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

# corta as bordas brancas (deixa so o logo)
bg = Image.new("RGB", img.size, (255, 255, 255))
diff = ImageChops.difference(img, bg)
bbox = diff.getbbox()
if bbox:
    pad = 12
    bbox = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
            min(bbox[2] + pad, img.width), min(bbox[3] + pad, img.height))
    img = img.crop(bbox)

# fundo branco -> transparente (fica bom em qualquer tema)
img = img.convert("RGBA")
px = img.getdata()
nova = [(r_, g_, b_, 0) if (r_ > 245 and g_ > 245 and b_ > 245) else (r_, g_, b_, a_)
        for (r_, g_, b_, a_) in px]
img.putdata(nova)
img.save(SAIDA)
print(f"Gerado {SAIDA}: {img.width} x {img.height} px (fundo transparente)")
