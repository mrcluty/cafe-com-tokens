# Café com Tokens | identidade minimalista (capa do programa, capa por episódio, avatar, logos).
# Fonte única do desenho: o main.py usa capa_episodio() todo dia no Actions e o
# identidade-visual\cafe-com-tokens\Logos\Projeto\gerar_identidade.py chama gerar_identidade().
# Uso direto: python capas.py identidade <pasta>   |   python capas.py episodio <numero> <AAAA-MM-DD> <saida.jpg>
import os, sys
from PIL import Image, ImageDraw, ImageFont

AQUI = os.path.dirname(os.path.abspath(__file__))
FONTES = os.path.join(AQUI, "fontes")

# Paleta (linguagem Notion/Apple: papel, tinta e um acento só)
PAPEL = "#FBFAF7"
TINTA = "#111111"
CINZA = "#5B5E6B"
ACENTO = "#E2580E"   # laranja café: só nos tokens do vapor e no "#"

SS = 2  # supersampling: desenha em 2x e reduz, para bordas lisas
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def fonte(peso, px):
    return ImageFont.truetype(os.path.join(FONTES, f"Inter-{peso}.ttf"), int(px))


def hex_rgba(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


# ---------------------------------------------------------------- símbolo
# Xícara sólida (estilo ícone de sistema) com vapor de 3 tokens quadrados.
# Geometria numa caixa de 100 x 100 unidades; u = tamanho da caixa em px / 100.
def simbolo_formas():
    return {
        "tokens": [(30, 4, 12), (46, 18, 12), (30, 32, 12)],   # (x, y, lado): zigue-zague subindo
        "corpo": (14, 50, 70, 88),                             # x0, y0, x1, y1 (fundo arredondado)
        "raio_corpo": 22,
        "alca": (62, 56, 86, 78),                              # anel da alça
        "esp_alca": 7,
        "pires": (6, 93, 94, 100),
    }


def desenhar_simbolo(d, x, y, tam, cor=TINTA, acento=ACENTO):
    u = tam / 100
    f = simbolo_formas()
    P = lambda a, b: (x + a * u, y + b * u)
    for tx, ty, lado in f["tokens"]:
        d.rounded_rectangle([P(tx, ty), P(tx + lado, ty + lado)], radius=lado * u * 0.22, fill=acento)
    ax0, ay0, ax1, ay1 = f["alca"]
    d.ellipse([P(ax0, ay0), P(ax1, ay1)], outline=cor, width=int(f["esp_alca"] * u))
    x0, y0, x1, y1 = f["corpo"]
    r = f["raio_corpo"] * u
    d.rounded_rectangle([P(x0, y0), P(x1, y1)], radius=r, fill=cor, corners=(False, False, True, True))
    px0, py0, px1, py1 = f["pires"]
    d.rounded_rectangle([P(px0, py0), P(px1, py1)], radius=(py1 - py0) * u / 2, fill=cor)


def simbolo_svg(cor=TINTA, acento=ACENTO):
    f = simbolo_formas()
    partes = [f'<rect x="{tx}" y="{ty}" width="{l}" height="{l}" rx="{l*0.22:.2f}" fill="{acento}"/>' for tx, ty, l in f["tokens"]]
    ax0, ay0, ax1, ay1 = f["alca"]
    e = f["esp_alca"]
    partes.append(f'<ellipse cx="{(ax0+ax1)/2}" cy="{(ay0+ay1)/2}" rx="{(ax1-ax0-e)/2}" ry="{(ay1-ay0-e)/2}" fill="none" stroke="{cor}" stroke-width="{e}"/>')
    x0, y0, x1, y1 = f["corpo"]
    r = f["raio_corpo"]
    partes.append(f'<path d="M{x0} {y0} H{x1} V{y1-r} A{r} {r} 0 0 1 {x1-r} {y1} H{x0+r} A{r} {r} 0 0 1 {x0} {y1-r} Z" fill="{cor}"/>')
    px0, py0, px1, py1 = f["pires"]
    partes.append(f'<rect x="{px0}" y="{py0}" width="{px1-px0}" height="{py1-py0}" rx="{(py1-py0)/2}" fill="{cor}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="1000" height="1000">{"".join(partes)}</svg>'


# ---------------------------------------------------------------- texto
def texto_centro(d, cx, topo, txt, fnt, cor, track=0.0):
    """Desenha texto centralizado em cx, com o topo das letras em `topo`. track em fração do tamanho."""
    esp = fnt.size * track
    larg = sum(fnt.getlength(ch) for ch in txt) + esp * (len(txt) - 1)
    x = cx - larg / 2
    _, t, _, _ = fnt.getbbox("H")
    for ch in txt:
        d.text((x, topo - t), ch, font=fnt, fill=cor)
        x += fnt.getlength(ch) + esp
    return larg


def largura(txt, fnt, track=0.0):
    return sum(fnt.getlength(ch) for ch in txt) + fnt.size * track * (len(txt) - 1)


def altura_caps(fnt):
    _, t, _, b = fnt.getbbox("H")
    return b - t


def tela(lado, fundo=PAPEL):
    img = Image.new("RGBA", (lado * SS, lado * SS), hex_rgba(fundo) if fundo else (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def finalizar(img, lado):
    return img.resize((lado, lado), Image.LANCZOS)


# ---------------------------------------------------------------- peças
def capa_programa(lado=3000):
    """Capa do show. Tudo dentro do círculo inscrito (Spotify recorta redondo) e peso puxado para cima."""
    img, d = tela(lado)
    W = lado * SS
    cx = W / 2
    tam_simb = W * 0.25
    f_tit = fonte("Bold", W * 0.135)
    f_tag = fonte("Medium", W * 0.034)
    h_tit = altura_caps(f_tit)
    gap_simb, gap_linha, gap_tag = W * 0.075, h_tit * 0.42, W * 0.06
    bloco = tam_simb + gap_simb + h_tit * 2 + gap_linha + gap_tag + altura_caps(f_tag)
    y = W * 0.47 - bloco / 2          # centro do bloco 3% acima do meio
    desenhar_simbolo(d, cx - tam_simb / 2, y, tam_simb)
    y += tam_simb + gap_simb
    texto_centro(d, cx, y, "Café com", f_tit, TINTA, -0.035)
    y += h_tit + gap_linha
    texto_centro(d, cx, y, "Tokens", f_tit, TINTA, -0.035)
    y += h_tit + gap_tag
    texto_centro(d, cx, y, "IA todo dia, direto ao ponto", f_tag, CINZA, -0.01)
    return finalizar(img, lado)


def data_curta(iso):
    a, m, dd = iso.split("-")
    return f"{int(dd)} {MESES[int(m) - 1]} {a}"


def capa_episodio(numero, data_iso, lado=3000):
    """Capa do episódio. Quadrada (Apple exige), mas o Spotify corta em 16:9 no card do episódio:
    tudo fica na faixa central de 56% da altura (y 22% a 78%), com folga, e funciona nos dois formatos."""
    img, d = tela(lado)
    W = lado * SS
    cx = W / 2
    # marca: símbolo + nome em uma linha
    f_marca = fonte("Bold", W * 0.042)
    h_m = altura_caps(f_marca)
    s = h_m * 1.55
    nome = "Café com Tokens"
    l_nome = largura(nome, f_marca, -0.02)
    gap = s * 0.35
    total = s + gap + l_nome
    y_m = W * 0.25
    desenhar_simbolo(d, cx - total / 2, y_m + h_m - s, s)
    texto_centro(d, cx - total / 2 + s + gap + l_nome / 2, y_m, nome, f_marca, TINTA, -0.02)
    # número: maior possível dentro de 80% da largura
    num = str(numero)
    px = W * 0.40
    while True:
        f_num = fonte("Bold", px)
        f_hash = fonte("Bold", px * 0.42)
        lh = largura("#", f_hash, 0) + px * 0.05
        ln = largura(num, f_num, -0.05)
        if lh + ln <= W * 0.70:
            break
        px *= 0.95
    h_num = altura_caps(f_num)
    topo = W * 0.505 - h_num / 2
    x0 = cx - (lh + ln) / 2
    _, t_h, _, b_h = f_hash.getbbox("#")
    d.text((x0, topo + h_num - b_h), "#", font=f_hash, fill=ACENTO)   # base do # na base do número
    texto_centro(d, x0 + lh + ln / 2, topo, num, f_num, TINTA, -0.05)
    # data
    f_data = fonte("Medium", W * 0.036)
    texto_centro(d, cx, W * 0.75 - altura_caps(f_data), data_curta(data_iso), f_data, CINZA, 0)
    return finalizar(img, lado)


def avatar(lado=1000):
    img, d = tela(lado)
    W = lado * SS
    tam = W * 0.5
    desenhar_simbolo(d, (W - tam) / 2, (W - tam) / 2, tam)
    return finalizar(img, lado)


def simbolo_png(lado=1000):
    img, d = tela(lado, None)
    desenhar_simbolo(d, 0, 0, lado * SS)
    return finalizar(img, lado)


def logo_png(larg=2400):
    """Logo horizontal: símbolo + 'Café com Tokens', fundo transparente."""
    f = fonte("Bold", 200 * SS)
    h = altura_caps(f)
    s = h * 1.55
    l_nome = largura("Café com Tokens", f, -0.02)
    gap = s * 0.35
    pad = 40 * SS
    Wt, Ht = int(s + gap + l_nome + pad * 2), int(s + pad * 2)
    img = Image.new("RGBA", (Wt, Ht), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    desenhar_simbolo(d, pad, pad, s)
    texto_centro(d, pad + s + gap + l_nome / 2, pad + s - h, "Café com Tokens", f, TINTA, -0.02)
    return img.resize((larg, int(larg * Ht / Wt)), Image.LANCZOS)


def salvar_jpg(img, caminho, qualidade=90):
    img.convert("RGB").save(caminho, "JPEG", quality=qualidade, optimize=True, progressive=True)


def gerar_identidade(marca):
    """Gera tudo em identidade-visual\\cafe-com-tokens (Perfis, Logos\\PNG, Logos\\Vetor)."""
    perfis, png, vetor = (os.path.join(marca, p) for p in ("Perfis", os.path.join("Logos", "PNG"), os.path.join("Logos", "Vetor")))
    for p in (perfis, png, vetor):
        os.makedirs(p, exist_ok=True)
    capa = capa_programa()
    capa.convert("RGB").save(os.path.join(perfis, "capa-cafe-com-tokens.png"))
    salvar_jpg(capa, os.path.join(AQUI, "docs", "capa.jpg"))
    avatar().convert("RGB").save(os.path.join(perfis, "avatar-cafe-com-tokens.png"))
    simbolo_png().save(os.path.join(png, "simbolo-cafe-com-tokens.png"))
    logo_png().save(os.path.join(png, "logo-cafe-com-tokens.png"))
    with open(os.path.join(vetor, "simbolo-cafe-com-tokens.svg"), "w", encoding="utf-8") as f:
        f.write(simbolo_svg())
    print("Identidade gerada em", marca)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "identidade":
        gerar_identidade(sys.argv[2])
    elif len(sys.argv) >= 5 and sys.argv[1] == "episodio":
        salvar_jpg(capa_episodio(int(sys.argv[2]), sys.argv[3]), sys.argv[4])
    else:
        print(__doc__ or "uso: veja o topo do arquivo")
