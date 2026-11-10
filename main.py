"""Café com Tokens: podcast diário de IA.

Coleta as notícias de IA, escreve o roteiro (Gemini, reserva Groq), narra (Gemini TTS, reserva Edge-TTS),
gera o MP3 e atualiza o feed RSS em docs/feed.xml. O GitHub Actions publica o MP3 no Releases e o feed no Pages.

Uso:
  python main.py          rodada normal (gera episodio.mp3 e atualiza episodios.json e docs/feed.xml)
  python main.py --teste  gera só o MP3 e o roteiro em tmp/, sem mexer em histórico nem feed
  python main.py --feed   só reconstrói o feed a partir do episodios.json
"""
import os
import re
import sys
import json
import wave
import base64
import shutil
import datetime
import subprocess
from email.utils import format_datetime, parsedate_to_datetime
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
HISTORICO_PATH = os.path.join(BASE, "historico.json")
EPISODIOS_PATH = os.path.join(BASE, "episodios.json")
ULTIMO_PATH = os.path.join(BASE, "ultimo_episodio.txt")
FEED_PATH = os.path.join(BASE, "docs", "feed.xml")
DIAS_HISTORICO = 30
JANELA_HORAS = 48
BRT = datetime.timezone(datetime.timedelta(hours=-3))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


def carregar_chaves_locais():
    """No computador do Luty, lê as chaves de chaves\\ (no GitHub vêm dos Secrets)."""
    global GEMINI_API_KEY, GROQ_API_KEY
    chaves = os.path.normpath(os.path.join(BASE, "../../chaves"))
    try:
        if not GEMINI_API_KEY:
            GEMINI_API_KEY = json.load(open(os.path.join(chaves, "gemini-key.json"), encoding="utf-8-sig")).get("key")
    except Exception:
        pass
    try:
        if not GROQ_API_KEY:
            GROQ_API_KEY = json.load(open(os.path.join(chaves, "llm-keys.json"), encoding="utf-8-sig")).get("groq")
    except Exception:
        pass


carregar_chaves_locais()


def agora_brt():
    return datetime.datetime.now(BRT)


# ---------------------------------------------------------------- pronúncia
MAPA_FONETICO = [
    (r"\bAnthropic\b", "Antrópic"), (r"\bIPOs\b", "ai-pi-ôus"), (r"\bIPO\b", "ai-pi-ôu"),
    (r"\bOpenAI\b", "Ôpen Ei-Ái"), (r"\bDeepMind\b", "Díp-Máind"), (r"\bHugging Face\b", "Râguing Fêice"),
    (r"\bTechCrunch\b", "Ték-Crântch"), (r"\bThe Verge\b", "Dê Vêrdj"), (r"\bArs Technica\b", "Árs Técnica"),
    (r"\bWired\b", "Uáired"), (r"\bMIT Tech Review\b", "Ême-Ai-Ti Ték Reviú"), (r"\bMIT\b", "Ême-Ai-Ti"),
    (r"\bAPIs\b", "ei-pi-áis"), (r"\bAPI\b", "ei-pi-ái"), (r"\bLLMs\b", "éli-éli-êmes"), (r"\bLLM\b", "éli-éli-ême"),
    (r"\bAGI\b", "ei-dji-ái"), (r"\bGPUs\b", "gê-pê-us"), (r"\bGPU\b", "gê-pê-u"), (r"\bCPUs\b", "cê-pê-us"),
    (r"\bCPU\b", "cê-pê-u"), (r"\bCEOs\b", "ci-i-ôus"), (r"\bCEO\b", "ci-i-ôu"), (r"\bSaaS\b", "Sás"),
    (r"\bAWS\b", "ei-dâbliu-és"), (r"\bNVIDIA\b", "Êni-vídia"), (r"\bNvidia\b", "Êni-vídia"),
    (r"\bClaude\b", "Clóud"), (r"\bChatGPT\b", "Chát Gê-Pê-Tê"), (r"\bGPT\b", "Gê-Pê-Tê"),
    (r"\bopen[- ]source\b", "ôpen sórce"), (r"\bOpen Source\b", "ôpen sórce"),
    (r"\bbenchmarks\b", "bêntch-márks"), (r"\bbenchmark\b", "bêntch-márk"), (r"\bfine-tuning\b", "fáin-túning"),
    (r"\bstartups\b", "istartâps"), (r"\bstartup\b", "istartâp"), (r"\bStartups\b", "Istartâps"),
    (r"\bStartup\b", "Istartâp"), (r"\bframeworks\b", "frêim-uôrks"), (r"\bframework\b", "frêim-uôrk"),
    (r"\bhardware\b", "rárduér"), (r"\bsoftware\b", "sóftuér"), (r"\bworkflows\b", "uôrk-flôus"),
    (r"\bworkflow\b", "uôrk-flôu"),
]


def aplicar_fonetica(texto):
    for padrao, fon in MAPA_FONETICO:
        texto = re.sub(padrao, fon, texto)
    return texto.replace("—", ",")


# ---------------------------------------------------------------- histórico
def normalizar_link(link):
    return (link or "").strip().lower().split("#")[0].split("?")[0].rstrip("/")


def carregar_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def carregar_historico():
    limite = (datetime.date.today() - datetime.timedelta(days=DIAS_HISTORICO)).isoformat()
    return [i for i in carregar_json(HISTORICO_PATH, []) if i.get("data", "") >= limite]


def salvar_historico(historico, links_usados, artigos):
    usados = {normalizar_link(l) for l in links_usados}
    ja = {i["link"] for i in historico}
    hoje = agora_brt().date().isoformat()
    for a in artigos:
        link = normalizar_link(a["link"])
        if link in usados and link not in ja:
            historico.append({"data": hoje, "link": link, "titulo": a["titulo"]})
            ja.add(link)
    with open(HISTORICO_PATH, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- coleta
FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss"),
]
ATOM = "{http://www.w3.org/2005/Atom}"


def data_do_item(item):
    for tag in ("pubDate", ATOM + "published", ATOM + "updated", "published", "updated"):
        el = item.find(tag)
        if el is not None and el.text:
            txt = el.text.strip()
            try:
                dt = parsedate_to_datetime(txt)
            except Exception:
                try:
                    dt = datetime.datetime.fromisoformat(txt.replace("Z", "+00:00"))
                except Exception:
                    continue
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    return None


def coletar_noticias(links_enviados):
    artigos = []
    agora = datetime.datetime.now(datetime.timezone.utc)
    for fonte, url in FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            itens = root.findall(".//item") or root.findall(".//" + ATOM + "entry") or root.findall(".//entry")
            count = 0
            for item in itens:
                if count >= 5:
                    break
                t = item.find("title") if item.find("title") is not None else item.find(ATOM + "title")
                l = item.find("link") if item.find("link") is not None else item.find(ATOM + "link")
                d = next((x for x in (item.find("description"), item.find("summary"), item.find(ATOM + "summary")) if x is not None), None)
                titulo = t.text.strip() if t is not None and t.text else ""
                link = ((l.text or "").strip() or l.attrib.get("href", "")) if l is not None else ""
                resumo = BeautifulSoup(d.text, "html.parser").get_text(strip=True)[:250] if d is not None and d.text else ""
                dt = data_do_item(item)
                if dt and (agora - dt).total_seconds() > JANELA_HORAS * 3600:
                    continue
                if not titulo or normalizar_link(link) in links_enviados:
                    continue
                artigos.append({"fonte": fonte, "titulo": titulo, "link": link, "resumo": resumo})
                count += 1
        except Exception as e:
            print(f"Erro ao coletar {fonte}: {e}")

    try:
        r = requests.get("https://www.anthropic.com/news", headers=HEADERS, timeout=12)
        if r.status_code == 200:
            vistos, count = set(), 0
            for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
                href = a["href"]
                if "/news/" in href and href != "/news" and href not in vistos:
                    vistos.add(href)
                    texto = a.get_text(" ", strip=True)
                    link = "https://www.anthropic.com" + href if href.startswith("/") else href
                    if len(texto) > 15 and normalizar_link(link) not in links_enviados:
                        artigos.append({"fonte": "Anthropic", "titulo": texto[:120], "link": link,
                                        "resumo": "Novidade oficial da Anthropic."})
                        count += 1
                        if count >= 3:
                            break
    except Exception as e:
        print(f"Erro ao coletar Anthropic: {e}")
    return artigos


# ---------------------------------------------------------------- texto (LLM)
def extrair_json(texto):
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.split("\n", 1)[1].rsplit("```", 1)[0]
    ini, fim = texto.find("{"), texto.rfind("}")
    return json.loads(texto[ini:fim + 1])


def chamar_llm_json(prompt):
    erro = None
    if GEMINI_API_KEY:
        for modelo in ["gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.5, "responseMimeType": "application/json"}}
            try:
                print(f"Roteiro com {modelo}...")
                res = requests.post(url, json=payload, timeout=120)
                if res.status_code == 200:
                    return extrair_json(res.json()["candidates"][0]["content"]["parts"][0]["text"])
                erro = f"{modelo} {res.status_code}: {res.text[:200]}"
                print(erro)
            except Exception as e:
                erro = f"{modelo}: {e}"
                print(erro)
    if GROQ_API_KEY:
        try:
            print("Roteiro com Groq...")
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={"model": "openai/gpt-oss-120b", "temperature": 0.5,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            if res.status_code == 200:
                return extrair_json(res.json()["choices"][0]["message"]["content"])
            erro = f"Groq {res.status_code}: {res.text[:200]}"
        except Exception as e:
            erro = f"Groq: {e}"
    raise RuntimeError(f"Todos os modelos falharam. Último erro: {erro}")


def gerar_episodio(artigos, historico, numero):
    hoje = agora_brt()
    data_falada = f"{hoje.day} de {['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'][hoje.month-1]}"
    ja = "\n".join(f"- {i['titulo']}" for i in historico[-40:]) or "(nenhum)"
    dados = "\n\n".join(f"Fonte: {a['fonte']}\nTítulo: {a['titulo']}\nLink: {a['link']}\nPrévia: {a['resumo']}" for a in artigos[:45])

    prompt = f"""Você é o roteirista e apresentador do "Café com Tokens", podcast diário em português do Brasil sobre inteligência artificial.
Hoje é {data_falada} de {hoje.year}. Este é o episódio {numero}.

OUVINTE: qualquer pessoa curiosa por IA, de quem cria produtos e automações a quem só quer entender o que está acontecendo. Não é leigo total, mas não precisa saber jargão. Nunca chame o ouvinte pelo nome.

OBJETIVO: o ouvinte termina o episódio entendendo de verdade o que aconteceu, como a tecnologia funciona por baixo do capô e por que isso importa no mundo real.
TOM: divertido, inteligente, espirituoso e empolgante. Zero linguagem corporativa. Um amigo apaixonado por tecnologia trocando uma ideia boa, com analogias vivas do cotidiano.

NOTÍCIAS COLETADAS NAS FONTES (OpenAI, DeepMind, Anthropic, Hugging Face, MIT Tech Review, Wired, Ars Technica, The Verge, TechCrunch):
{dados}

JÁ CONTADAS NOS ÚLTIMOS DIAS (PROIBIDO REPETIR, nem com outro título ou outra fonte):
{ja}

SELEÇÃO: filtre impiedosamente e fique só com a nata. NÃO existe número fixo: entram TODAS as notícias realmente relevantes do mundo da IA hoje (modelos novos, movimentos grandes de mercado, avanços técnicos reais, regulação que muda o jogo) e NENHUMA de enchimento. Dia fraco pode ter 1 só. Critério: "quem acompanha IA perderia algo importante se não soubesse disso hoje?". Nunca duas notícias sobre o mesmo fato. Ordene da mais impactante para a menos.
DURAÇÃO: livre. Pode dar 4 ou 12 minutos, desde que cada frase valha a pena. Sem encher linguiça.

ROTEIRO FALADO:
- Abertura curta e animada: "Bom dia!" (sem nome), diga que é o Café com Tokens de {data_falada}, e dê uma prévia rápida do que vem.
- Corpo: conte cada notícia de forma fluida e conversada: o que rolou (com dados concretos), como funciona por baixo do capô (com uma analogia simples) e o que isso mexe no jogo. Sem ler rótulos ("número 1", "o que rolou:"). Transições naturais de apresentador.
- Encerramento redondo: por hoje é isso, os links de todas as notícias estão na descrição do episódio, convide a seguir o Café com Tokens para receber o próximo amanhã cedo, e se despeça até amanhã.
- Texto puro para sintetizador de voz: sem emojis, sem marcações de palco ("[pausa]", "Locutor:"), sem asteriscos, sem travessão. Números fáceis de falar. Parágrafos separados por linha em branco.

RESPONDA SÓ COM JSON, neste formato:
{{
 "titulo": "título do episódio, até 70 caracteres, instigante, com o assunto principal do dia (sem data e sem número)",
 "resumo": "1 a 2 frases que vendem o episódio, para a descrição no Spotify",
 "noticias": [{{"titulo": "título curto em português", "fonte": "nome da fonte", "link": "link exato da lista acima"}}],
 "roteiro": "o texto falado completo"
}}
Regra geral: nunca use travessão (—). Use vírgula, dois-pontos ou hífen."""
    ep = chamar_llm_json(prompt)
    for campo in ("titulo", "resumo", "noticias", "roteiro"):
        if not ep.get(campo):
            raise RuntimeError(f"Resposta sem o campo {campo}")
    ep["titulo"] = ep["titulo"].replace("—", "-").strip()
    ep["resumo"] = ep["resumo"].replace("—", ",").strip()
    return ep


# ---------------------------------------------------------------- áudio
def dividir_roteiro(texto, limite=2200):
    """O TTS do Gemini corta textos longos: narra em blocos de parágrafos."""
    blocos, atual = [], ""
    for par in [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]:
        if atual and len(atual) + len(par) > limite:
            blocos.append(atual)
            atual = par
        else:
            atual = f"{atual}\n\n{par}" if atual else par
    if atual:
        blocos.append(atual)
    return blocos


def tts_gemini(texto, wav_path):
    if not GEMINI_API_KEY:
        return False
    instrucao = ("Narre em português do Brasil como o apresentador de um podcast matinal de tecnologia: "
                 "voz calorosa, ritmo fluido, dicção clara, entonação natural de conversa entre amigos e energia positiva:\n\n")
    for modelo in ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": instrucao + texto}]}],
                   "generationConfig": {"responseModalities": ["AUDIO"],
                                        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": CONFIG["voz_gemini"]}}}}}
        try:
            res = requests.post(url, json=payload, timeout=180)
            if res.status_code != 200:
                print(f"TTS {modelo} {res.status_code}: {res.text[:160]}")
                continue
            raw = base64.b64decode(res.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            if raw[:4] == b"RIFF":
                open(wav_path, "wb").write(raw)
            else:
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(raw)
            print(f"  bloco narrado com {modelo}")
            return True
        except Exception as e:
            print(f"TTS {modelo}: {e}")
    return False


def tts_edge(texto, mp3_path):
    import asyncio
    import edge_tts

    async def falar():
        try:
            await edge_tts.Communicate(texto, "pt-BR-AntonioNeural").save(mp3_path)
        except Exception:
            await edge_tts.Communicate(texto, "pt-BR-ThalitaMultilingualNeural").save(mp3_path)
    asyncio.run(falar())
    return os.path.exists(mp3_path)


def narrar(roteiro, pasta, mp3_final):
    """Narra bloco a bloco com a mesma voz e junta tudo num MP3 de podcast (mono, 96 kbps, volume normalizado)."""
    os.makedirs(pasta, exist_ok=True)
    blocos = dividir_roteiro(roteiro)
    partes = []
    for i, bloco in enumerate(blocos):
        wav = os.path.join(pasta, f"parte_{i:02d}.wav")
        if not tts_gemini(bloco, wav):
            partes = None
            break
        partes.append(wav)

    if partes is None:
        print("Gemini TTS falhou: narrando tudo com Edge-TTS (uma voz só no episódio inteiro).")
        mp3 = os.path.join(pasta, "edge.mp3")
        tts_edge(roteiro, mp3)
        partes = [mp3]

    lista = os.path.join(pasta, "lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for p in partes:
            f.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "44100",
           "-c:a", "libmp3lame", "-b:a", "96k", mp3_final]
    subprocess.run(cmd, check=True)
    return duracao_segundos(mp3_final)


def duracao_segundos(caminho):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", caminho],
                         capture_output=True, text=True).stdout.strip()
    return int(float(out or 0))


# ---------------------------------------------------------------- feed RSS
def descricao_html(ep):
    itens = "".join(f'<li><a href="{escape(n["link"])}">{escape(n["titulo"])}</a> ({escape(n.get("fonte", ""))})</li>'
                    for n in ep["noticias"])
    return (f"<p>{escape(ep['resumo'])}</p><p><strong>As notícias de hoje:</strong></p><ul>{itens}</ul>"
            f"<p>Café com Tokens: IA todo dia, direto ao ponto. Episódio novo toda manhã.</p>")


def hms(seg):
    return f"{seg // 3600:02d}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def montar_feed(episodios):
    c = CONFIG
    site = c["site_url"].rstrip("/")
    itens = []
    for ep in sorted(episodios, key=lambda e: e["numero"], reverse=True):
        itens.append(f"""    <item>
      <title>{escape(ep['titulo'])}</title>
      <description><![CDATA[{ep['descricao_html']}]]></description>
      <content:encoded><![CDATA[{ep['descricao_html']}]]></content:encoded>
      <enclosure url="{escape(ep['url'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(ep['guid'])}</guid>
      <pubDate>{ep['pub_date']}</pubDate>
      <itunes:duration>{hms(ep['duracao'])}</itunes:duration>
      <itunes:episode>{ep['numero']}</itunes:episode>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    ultimo = episodios[-1]["pub_date"] if episodios else format_datetime(agora_brt())
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(c['titulo'])}</title>
    <link>{escape(site)}/</link>
    <atom:link href="{escape(site)}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>pt-br</language>
    <copyright>{escape(c['autor'])}</copyright>
    <description><![CDATA[{c['descricao']}]]></description>
    <itunes:summary><![CDATA[{c['descricao']}]]></itunes:summary>
    <itunes:author>{escape(c['autor'])}</itunes:author>
    <itunes:owner>
      <itunes:name>{escape(c['autor'])}</itunes:name>
      <itunes:email>{escape(c['email_dono'])}</itunes:email>
    </itunes:owner>
    <itunes:image href="{escape(site)}/capa.jpg"/>
    <image><url>{escape(site)}/capa.jpg</url><title>{escape(c['titulo'])}</title><link>{escape(site)}/</link></image>
    <itunes:category text="Technology"/>
    <itunes:category text="News"><itunes:category text="Tech News"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <lastBuildDate>{ultimo}</lastBuildDate>
{chr(10).join(itens)}
  </channel>
</rss>
"""
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(feed)
    ET.parse(FEED_PATH)  # garante XML válido
    print(f"Feed atualizado: {len(episodios)} episódios.")


# ---------------------------------------------------------------- principal
def main():
    teste = "--teste" in sys.argv
    if "--feed" in sys.argv:
        montar_feed(carregar_json(EPISODIOS_PATH, []))
        return

    hoje = agora_brt().date().isoformat()
    if not teste and os.environ.get("GITHUB_EVENT_NAME") == "schedule" and os.path.exists(ULTIMO_PATH):
        if open(ULTIMO_PATH, encoding="utf-8").read().strip() == hoje:
            print("Episódio de hoje já saiu. Rodada de reserva encerrada.")
            return

    historico = carregar_historico()
    episodios = carregar_json(EPISODIOS_PATH, [])
    numero = (episodios[-1]["numero"] + 1) if episodios else 1

    artigos = coletar_noticias(frozenset(i["link"] for i in historico))
    print(f"{len(artigos)} notícias novas coletadas.")
    if not artigos:
        print("Nada novo hoje: sem episódio.")
        return

    ep = gerar_episodio(artigos, historico, numero)
    print(f"Episódio {numero}: {ep['titulo']} ({len(ep['noticias'])} notícias, {len(ep['roteiro'])} caracteres)")

    pasta = os.path.join(BASE, "tmp")
    nome = f"cafe-com-tokens-{hoje}.mp3"
    mp3 = os.path.join(BASE, nome)
    duracao = narrar(aplicar_fonetica(ep["roteiro"]), pasta, mp3)
    print(f"Áudio pronto: {nome} ({hms(duracao)})")

    if teste:
        with open(os.path.join(pasta, "roteiro.json"), "w", encoding="utf-8") as f:
            json.dump(ep, f, ensure_ascii=False, indent=1)
        print("Teste: roteiro em tmp/roteiro.json. Histórico e feed não foram mexidos.")
        return

    repo = os.environ.get("GITHUB_REPOSITORY", CONFIG["repositorio"])
    tag = f"ep{numero:04d}"
    episodios.append({
        "numero": numero, "data": hoje, "titulo": ep["titulo"], "resumo": ep["resumo"],
        "noticias": ep["noticias"], "descricao_html": descricao_html(ep),
        "arquivo": nome, "tag": tag,
        "url": f"https://github.com/{repo}/releases/download/{tag}/{nome}",
        "bytes": os.path.getsize(mp3), "duracao": duracao,
        "guid": f"cafe-com-tokens-{tag}", "pub_date": format_datetime(agora_brt()),
    })
    with open(EPISODIOS_PATH, "w", encoding="utf-8") as f:
        json.dump(episodios, f, ensure_ascii=False, indent=1)
    salvar_historico(historico, [n["link"] for n in ep["noticias"]], artigos)
    montar_feed(episodios)
    with open(ULTIMO_PATH, "w", encoding="utf-8") as f:
        f.write(hoje)

    # Passa para o GitHub Actions o que publicar no Releases
    saida = os.environ.get("GITHUB_OUTPUT")
    if saida:
        with open(saida, "a", encoding="utf-8") as f:
            f.write(f"tag={tag}\narquivo={nome}\ntitulo=Ep. {numero}: {ep['titulo']}\n")
    shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    main()
