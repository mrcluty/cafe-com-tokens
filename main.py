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
import time
import datetime
import subprocess
from email.utils import format_datetime, parsedate_to_datetime
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8-sig"))
HISTORICO_PATH = os.path.join(BASE, "historico.json")
EPISODIOS_PATH = os.path.join(BASE, "episodios.json")
ULTIMO_PATH = os.path.join(BASE, "ultimo_episodio.txt")
FEED_PATH = os.path.join(BASE, "docs", "feed.xml")
CAPAS_DIR = os.path.join(BASE, "docs", "capas")  # capa de cada episódio (publicada pelo Pages)
DIAS_HISTORICO = 30
JANELA_HORAS = 48
BRT = datetime.timezone(datetime.timedelta(hours=-3))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def achar_ffmpeg():
    """ffmpeg do sistema se houver; senão o do pacote imageio-ffmpeg (vem pelo pip, sem depender do apt,
    que ficou fora do ar em 11/11/2026 e travou o episódio)."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = achar_ffmpeg()
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
# (fonte, url, janela em horas, tipo). "noticia" = a nata do dia; "case" = negócios e empresas usando IA,
# que não envelhece rápido e entra quando o dia tem pouca notícia forte.
FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml", JANELA_HORAS, "noticia"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", JANELA_HORAS, "noticia"),
    ("Google AI", "https://blog.google/technology/ai/rss/", JANELA_HORAS, "noticia"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", JANELA_HORAS, "noticia"),
    ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed/", JANELA_HORAS, "noticia"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", JANELA_HORAS, "noticia"),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", JANELA_HORAS, "noticia"),
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", JANELA_HORAS, "noticia"),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss", JANELA_HORAS, "noticia"),
    ("The Decoder", "https://the-decoder.com/feed/", JANELA_HORAS, "noticia"),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/", 7 * 24, "case"),
    ("AI Business", "https://aibusiness.com/rss.xml", 7 * 24, "case"),
    ("NVIDIA Blog", "https://blogs.nvidia.com/feed/", 7 * 24, "case"),
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
    for fonte, url, janela, tipo in FEEDS:
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
                if dt and (agora - dt).total_seconds() > janela * 3600:
                    continue
                if not titulo or normalizar_link(link) in links_enviados:
                    continue
                artigos.append({"fonte": fonte, "titulo": titulo, "link": link, "resumo": resumo, "tipo": tipo})
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
                                        "resumo": "Novidade oficial da Anthropic.", "tipo": "noticia"})
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
    ja = "\n".join(f"- {i['titulo']}" for i in historico) or "(nenhum)"

    def bloco(tipo, limite):
        sel = [a for a in artigos if a.get("tipo") == tipo][:limite]
        return "\n\n".join(f"Fonte: {a['fonte']}\nTítulo: {a['titulo']}\nLink: {a['link']}\nPrévia: {a['resumo']}" for a in sel) or "(nenhum)"
    dados = bloco("noticia", 45)
    cases = bloco("case", 25)

    prompt = f"""Você é o roteirista e apresentador do "Café com Tokens", podcast diário em português do Brasil sobre inteligência artificial.
Hoje é {data_falada} de {hoje.year}. Este é o episódio {numero}.

OUVINTE: qualquer pessoa curiosa por IA, de quem cria produtos e automações a quem só quer entender o que está acontecendo. Não é leigo total, mas não precisa saber jargão. Nunca chame o ouvinte pelo nome.

OBJETIVO: o ouvinte termina o episódio entendendo de verdade o que aconteceu, o que isso significa em linguagem simples e por que isso importa no mundo real.
TOM (o mais importante: é o começo do dia de quem ouve): leve, engraçado, informal e gostoso de ouvir, como um amigo bem-humorado contando as novidades no café da manhã. Piadas rápidas, comentários espirituosos, ironia leve, referências do dia a dia brasileiro. Nada pesado nem de palestra: se um trecho soar técnico demais, alivie com humor. Zero linguagem corporativa. Inteligente sem ser chato; a informação continua precisa.

NOTÍCIAS DAS ÚLTIMAS 48 HORAS (OpenAI, Google, DeepMind, Anthropic, Hugging Face, MIT Tech Review, Wired, Ars Technica, The Verge, TechCrunch, The Decoder):
{dados}

CASES E NEGÓCIOS DA SEMANA (empresas usando IA de verdade, resultados, mercado):
{cases}

JÁ CONTADAS NOS ÚLTIMOS 30 DIAS: PROIBIDO REPETIR, JAMAIS. Nem com outro título, outra fonte ou outro ângulo; nem uma "atualização" do mesmo fato, a não ser que seja um fato novo e grande:
{ja}

SELEÇÃO:
1. Primeiro a nata da IA: entram TODAS as notícias realmente relevantes de hoje (modelos novos, movimentos grandes de mercado, avanços técnicos reais, regulação que muda o jogo) e NENHUMA de enchimento. Critério: "quem acompanha IA perderia algo importante se não soubesse disso hoje?". Nunca duas sobre o mesmo fato. Ordene da mais impactante para a menos.
2. Se a nata não sustentar o episódio (dia fraco), abra um pouco o campo, sempre começando por uma notícia de IA: traga 1 ou 2 itens da lista de CASES, contando como uma empresa real está usando IA, o que ela fez, o resultado e o que outros negócios podem copiar. Use só o que está nas listas; nunca invente empresa, número ou resultado.
3. Dia cheio de notícia forte: não precisa de case.
DURAÇÃO: o episódio tem de 10 a 12 minutos falados, o tempo de chegar no trabalho. Isso dá entre 1.800 e 2.000 palavras no roteiro (uns 11.000 caracteres): confira antes de responder.
- Você monta a programação do dia (ordem, ritmo, quanto tempo cada assunto ganha). As notícias principais ganham de 1 minuto e meio a 2 minutos (uns 250 a 320 palavras): contexto (de onde isso vem, quem são os envolvidos), o fato com números concretos, o que muda na prática para quem usa ou cria com IA e uma opinião ou provocação para o ouvinte pensar.

QUADRO "SIMPLIFICANDO A VIDA" (marca registrada do programa, com humor):
- Quando uma notícia trouxer uma ideia complexa ou técnica, depois de contar o fato o apresentador chama o quadro com uma frase divertida, variando a cada vez, por exemplo "Tá, tudo muito bonito, mas vamos simplificar a vida?" ou "Agora, simplificando a vida:" ou "Traduzindo para simplificar a vida:", e explica de forma simples, com uma analogia do cotidiano, o que aquilo é e o que mudou de verdade. O ouvinte aprende que, quando vem a parte técnica, logo depois vem o momento de digerir.
- Use só nas notícias que precisam (em geral 1 a 3 por episódio), nunca em todas.
- O ouvinte já tem um conhecimento razoável de IA: NUNCA explique o básico (o que é token, o que é LLM, o que é um modelo). Simplifique a ideia nova e o impacto dela, não o vocabulário de sempre.
- Dia com poucas notícias relevantes: NÃO invente nem encha com notícia fraca. Use os cases e aprofunde as que existem (história, concorrentes, o que vem a seguir, exemplos práticos de uso) até chegar aos 10 minutos.
- Profundidade não é enrolação: nada de repetir a mesma ideia com outras palavras.

ROTEIRO FALADO (três vozes, como uma redação de rádio):
- LUTY é o apresentador e âncora: fala em primeira pessoa, abre, faz as transições e fecha. Abertura curta e animada: "Bom dia!", se apresente ("aqui é o Luty"), diga que é o Café com Tokens de {data_falada} e dê uma prévia rápida do que vem.
- BIA e TÉO são os repórteres: cada notícia (ou case) é contada INTEIRA por um deles, alternando Bia, Téo, Bia, Téo... O repórter conta o que rolou (com dados concretos), faz o "Simplificando a vida" quando fizer sentido e diz o que isso mexe no jogo.
- Luty chama cada repórter pelo nome antes da notícia ("e quem traz essa é a Bia", "Téo, conta aí", variando) e pode comentar em uma frase depois, com humor. O repórter pode responder ao Luty com leveza ("valeu, Luty"). Clima de equipe que se diverte, sem virar bate-papo longo: a notícia é a estrela.
- Sem ler rótulos ("número 1", "o que rolou:") e sem a expressão "por baixo do capô".
- Encerramento com o Luty: por hoje é isso, os links de todas as notícias estão na descrição do episódio, convide a seguir o Café com Tokens para receber o próximo amanhã cedo, e se despeça até amanhã.
- FORMATO OBRIGATÓRIO: cada parágrafo começa com a marca de quem fala, [LUTY], [BIA] ou [TEO], seguida do texto. Parágrafos separados por linha em branco. Essa marca é a ÚNICA marcação permitida (ela some antes da narração).
- Texto puro para sintetizador de voz: sem emojis, sem outras marcações de palco ("[pausa]", "Locutor:"), sem asteriscos, sem travessão e sem indicação de tom ou emoção (nada de "(rindo)" ou "animado:"): tudo o que estiver no texto vai ser falado em voz alta. Números fáceis de falar.

RESPONDA SÓ COM JSON, neste formato:
{{
 "titulo": "título do episódio (sem data e sem número, o número entra sozinho), seguindo a REGRA DO TÍTULO abaixo",
 "resumo": "1 a 2 frases que vendem o episódio, para a descrição no Spotify",
 "noticias": [{{"titulo": "título curto em português", "fonte": "nome da fonte", "link": "link exato da lista acima"}}],
 "roteiro": "o texto falado completo, cada parágrafo começando com [LUTY], [BIA] ou [TEO]"
}}
REGRA DO TÍTULO (é o que faz alguém clicar no Spotify e achar o episódio na busca):
- Até 65 caracteres. Comece pelo nome que as pessoas buscam: a empresa ou o produto mais famoso do dia (ChatGPT, OpenAI, Google, Gemini, Claude, Anthropic, Nvidia, Meta, Apple...).
- Chamativo de verdade: provoque curiosidade, conflito, número forte ou consequência ("Por que...", "acabou de...", "ninguém esperava", "vai mudar", "US$ 50 bilhões"). Pode apelar para a emoção, sem mentir nem inventar.
- Se couber, puxe a 2ª notícia mais forte com "e" ou vírgula.
- Exemplos do tom: "ChatGPT agora faz compras sozinho, e o varejo entrou em pânico" / "Anthropic vai pra bolsa: quanto vale o Claude?" / "Nvidia em crise: quem vai mandar no chip da IA?"

Regra geral: nunca use travessão (—). Use vírgula, dois-pontos ou hífen."""
    ep = chamar_llm_json(prompt)
    # Trava de duração: ~17 caracteres por segundo narrado (#2: 9.035 = 8min57); abaixo de 10.500 fica perto ou abaixo de 10 minutos.
    # Se vier curto, manda o próprio roteiro de volta para EXPANDIR (reescrever do zero sai curto de novo).
    for _ in range(2):
        tam = len(ep.get("roteiro", ""))
        if tam >= 10500:
            break
        print(f"Roteiro curto ({tam} caracteres, uns {tam // 1010} min): pedindo para expandir...")
        try:
            maior = chamar_llm_json(
                prompt + "\n\nVOCÊ JÁ ESCREVEU ESTA VERSÃO, MAS ELA FICOU CURTA "
                f"({len(ep['roteiro'].split())} palavras, uns {tam // 1010} minutos):\n"
                + json.dumps(ep, ensure_ascii=False)
                + "\n\nDevolva o mesmo JSON com o roteiro EXPANDIDO para 1.900 a 2.100 palavras: mantenha o que está bom e acrescente, "
                "em cada notícia, mais contexto, um 'Simplificando a vida' onde couber, exemplos práticos, uma piada ou comentário espirituoso "
                "e uma provocação final. Se ainda faltar, inclua um case da lista. Sem repetir ideias.")
            if len(maior.get("roteiro", "")) > tam:
                ep = maior
        except Exception as e:
            print(f"Expansão falhou: {e}")
            break
    for campo in ("titulo", "resumo", "noticias", "roteiro"):
        if not ep.get(campo):
            raise RuntimeError(f"Resposta sem o campo {campo}")
    ep["titulo"] = ep["titulo"].replace("—", "-").strip()
    ep["resumo"] = ep["resumo"].replace("—", ",").strip()
    return ep


# ---------------------------------------------------------------- áudio
MARCA_VOZ = re.compile(r"^\s*\[(LUTY|BIA|TEO|TÉO)\]\s*:?\s*", re.I)


def dividir_roteiro(texto, limite=2200):
    """Divide por quem fala ([LUTY], [BIA], [TEO]) e, dentro de cada fala, em blocos que o TTS aguenta.
    Devolve [(voz, texto)] sem as marcas. Parágrafo sem marca continua com a voz anterior."""
    blocos, voz, atual = [], "LUTY", ""
    for par in [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]:
        m = MARCA_VOZ.match(par)
        nova = m.group(1).upper().replace("TÉO", "TEO") if m else voz
        par = MARCA_VOZ.sub("", par).strip()
        if not par:
            continue
        if atual and (nova != voz or len(atual) + len(par) > limite):
            blocos.append((voz, atual))
            atual = ""
        voz = nova
        atual = f"{atual}\n\n{par}" if atual else par
    if atual:
        blocos.append((voz, atual))
    return blocos


MODELOS_TTS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]


def tts_gemini(texto, wav_path, modelo, voz, chave=None):
    # Só o texto falado vai para o TTS: instrução de tom junto do texto acaba sendo LIDA em voz alta (aconteceu no #2).
    chave = chave or GEMINI_API_KEY
    if not chave:
        return False
    for _tentativa in range(3):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}"
        payload = {"contents": [{"parts": [{"text": texto}]}],
                   "generationConfig": {"responseModalities": ["AUDIO"],
                                        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": CONFIG["vozes"][voz]["gemini"]}}}}}
        try:
            res = requests.post(url, json=payload, timeout=180)
            if res.status_code == 429 and "PerDay" not in res.text:
                print(f"TTS {modelo}: limite por minuto, esperando 30 s")
                time.sleep(30)
                continue
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
            print(f"  {voz} narrado com {modelo}")
            return True
        except Exception as e:
            print(f"TTS {modelo}: {e}")
    return False


def tts_edge(texto, mp3_path, voz="LUTY"):
    import asyncio
    import edge_tts

    async def falar():
        try:
            await edge_tts.Communicate(texto, CONFIG["vozes"][voz]["edge"]).save(mp3_path)
        except Exception:
            await edge_tts.Communicate(texto, "pt-BR-AntonioNeural").save(mp3_path)
    asyncio.run(falar())
    return os.path.exists(mp3_path)


def narrar(roteiro, pasta, mp3_final):
    """Narra fala a fala (cada pessoa com a sua voz) e junta tudo num MP3 de podcast (mono, 96 kbps, volume normalizado)."""
    os.makedirs(pasta, exist_ok=True)
    blocos = dividir_roteiro(roteiro)
    # Cadeia (chave, modelo): primeiro todos os modelos na chave do podcast, depois na chave reserva.
    # Nunca recomeça do zero (isso queimava a cota do dia): a fala que falha passa para o próximo da cadeia
    # e o que já foi narrado fica. Como cada notícia é de uma pessoa, a troca de modelo quase não aparece.
    chaves = [k for k in (GEMINI_API_KEY, os.environ.get("GEMINI_API_KEY_2")) if k]
    cadeia = [(k, m) for k in chaves for m in MODELOS_TTS]
    pos, partes = 0, []
    for i, (voz, bloco) in enumerate(blocos):
        wav = os.path.join(pasta, f"parte_{i:02d}.wav")
        while pos < len(cadeia) and not tts_gemini(bloco, wav, cadeia[pos][1], voz, cadeia[pos][0]):
            pos += 1
            if pos < len(cadeia):
                print(f"Passando para {cadeia[pos][1]} (chave {chaves.index(cadeia[pos][0]) + 1}).")
        if pos >= len(cadeia):
            print(f"Gemini sem cota: fala {i} ({voz}) com Edge-TTS.")
            mp3 = os.path.join(pasta, f"edge_{i:02d}.mp3")
            tts_edge(bloco, mp3, voz)
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", mp3, "-ac", "1", "-ar", "24000",
                            "-c:a", "pcm_s16le", wav], check=True)
        partes.append(wav)

    # Respiro de 0,4 s entre as falas (só no áudio do Gemini, que sai todo em WAV com o mesmo formato).
    if len(partes) > 1 and all(p.endswith(".wav") for p in partes):
        pausa = os.path.join(pasta, "pausa.wav")
        with wave.open(partes[0]) as w:
            canais, largura, taxa = w.getnchannels(), w.getsampwidth(), w.getframerate()
        with wave.open(pausa, "wb") as w:
            w.setnchannels(canais)
            w.setsampwidth(largura)
            w.setframerate(taxa)
            w.writeframes(b"\0" * (int(taxa * 0.4) * canais * largura))
        partes = [x for p in partes for x in (p, pausa)][:-1]

    lista = os.path.join(pasta, "lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for p in partes:
            f.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur=2.5", "-ac", "1", "-ar", "44100",
           "-c:a", "libmp3lame", "-b:a", "96k", mp3_final]
    subprocess.run(cmd, check=True)  # apad: 2,5 s de silêncio no fim, nada de corte seco
    return duracao_segundos(mp3_final)


def duracao_segundos(caminho):
    # Sem ffprobe (o ffmpeg do pip não traz): lê o "Duration:" que o próprio ffmpeg imprime.
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", caminho], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    return int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0


# ---------------------------------------------------------------- feed RSS
def descricao_html(ep):
    itens = "".join(f'<li><a href="{escape(n["link"])}">{escape(n["titulo"])}</a> ({escape(n.get("fonte", ""))})</li>'
                    for n in ep["noticias"])
    return (f"<p>{escape(ep['resumo'])}</p><p><strong>As notícias de hoje:</strong></p><ul>{itens}</ul>"
            f"<p>Café com Tokens: IA todo dia, direto ao ponto. Episódio novo toda manhã.</p>")


def hms(seg):
    return f"{seg // 3600:02d}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def garantir_capa(ep):
    """Gera docs/capas/<tag>.jpg se ainda não existe (desenho em capas.py)."""
    import capas
    caminho = os.path.join(CAPAS_DIR, f"{ep['tag']}.jpg")
    if not os.path.exists(caminho):
        os.makedirs(CAPAS_DIR, exist_ok=True)
        capas.salvar_jpg(capas.capa_episodio(ep["numero"], ep["data"]), caminho)
        print(f"Capa do episódio: {caminho}")
    return f"capas/{ep['tag']}.jpg?v=3"  # ?v= muda quando o desenho mudar (fura o cache do Spotify)


def montar_feed(episodios):
    c = CONFIG
    site = c["site_url"].rstrip("/")
    itens = []
    for ep in sorted(episodios, key=lambda e: e["numero"], reverse=True):
        capa = garantir_capa(ep)
        itens.append(f"""    <item>
      <title>#{ep['numero']} | {escape(ep['titulo'])}</title>
      <description><![CDATA[{ep['descricao_html']}]]></description>
      <content:encoded><![CDATA[{ep['descricao_html']}]]></content:encoded>
      <enclosure url="{escape(ep['url'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(ep['guid'])}</guid>
      <pubDate>{ep['pub_date']}</pubDate>
      <itunes:image href="{escape(site)}/{capa}"/>
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
    <itunes:image href="{escape(site)}/capa.jpg?v=2"/>
    <image><url>{escape(site)}/capa.jpg?v=2</url><title>{escape(c['titulo'])}</title><link>{escape(site)}/</link></image>
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


# ---------------------------------------------------------------- refazer
def refazer(numero):
    """Regrava o episódio N com as MESMAS notícias no formato atual. Mantém título, data, guid e capa;
    troca só o áudio (arquivo -vK.mp3 no mesmo Release). Uso: REFAZER=N (aba Actions > Run workflow)."""
    episodios = carregar_json(EPISODIOS_PATH, [])
    ep_antigo = next(e for e in episodios if e["numero"] == numero)
    links = {normalizar_link(n["link"]) for n in ep_antigo["noticias"]}
    historico = [i for i in carregar_historico() if normalizar_link(i["link"]) not in links]

    artigos = [a for a in coletar_noticias(frozenset()) if normalizar_link(a["link"]) in links]
    achados = {normalizar_link(a["link"]) for a in artigos}
    for n in ep_antigo["noticias"]:  # notícia que já saiu da janela dos feeds: entra com o que temos
        if normalizar_link(n["link"]) not in achados:
            artigos.append({"fonte": n.get("fonte", ""), "titulo": n["titulo"], "link": n["link"], "resumo": "", "tipo": "noticia"})
    print(f"Refazendo o #{numero} com {len(artigos)} notícias.")

    ep = gerar_episodio(artigos, historico, numero)
    print(f"Roteiro novo: {len(ep['roteiro'])} caracteres")

    m = re.search(r"-v(\d+)\.mp3$", ep_antigo["arquivo"])
    k = int(m.group(1)) + 1 if m else 2
    nome = f"cafe-com-tokens-{ep_antigo['data']}-v{k}.mp3"
    pasta = os.path.join(BASE, "tmp")
    mp3 = os.path.join(BASE, nome)
    duracao = narrar(aplicar_fonetica(ep["roteiro"]), pasta, mp3)
    print(f"Áudio novo: {nome} ({hms(duracao)})")

    repo = os.environ.get("GITHUB_REPOSITORY", CONFIG["repositorio"])
    ep_antigo.update({"arquivo": nome, "url": f"https://github.com/{repo}/releases/download/{ep_antigo['tag']}/{nome}",
                      "bytes": os.path.getsize(mp3), "duracao": duracao})
    with open(EPISODIOS_PATH, "w", encoding="utf-8") as f:
        json.dump(episodios, f, ensure_ascii=False, indent=1)
    montar_feed(episodios)

    saida = os.environ.get("GITHUB_OUTPUT")
    if saida:
        with open(saida, "a", encoding="utf-8") as f:
            f.write(f"tag={ep_antigo['tag']}\narquivo={nome}\nrefeito=sim\n")
    shutil.rmtree(pasta, ignore_errors=True)


# ---------------------------------------------------------------- principal
def main():
    teste = "--teste" in sys.argv
    if os.environ.get("REFAZER", "").strip().isdigit():
        refazer(int(os.environ["REFAZER"]))
        return
    if "--feed" in sys.argv:
        montar_feed(carregar_json(EPISODIOS_PATH, []))
        return

    hoje = agora_brt().date().isoformat()
    if not teste and (os.environ.get("GITHUB_EVENT_NAME") == "schedule" or os.environ.get("AUTOMATICO") == "true") and os.path.exists(ULTIMO_PATH):
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
