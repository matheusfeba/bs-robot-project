import logging
import os
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright


BASE_URL = os.getenv("BS_ROBOT_BASE_URL", "https://bradescoseguros.neowrk.com")
BOOKING_URL = os.getenv("BS_ROBOT_BOOKING_URL", "https://bradescoseguros.neowrk.com/desk4me/desk/booking")

EMAIL = os.getenv("BS_ROBOT_EMAIL", "isabelle.ferreira@bradescoseguros.com.br")
SENHA = os.getenv("BS_ROBOT_PASS", "Pass2025!")

POSICAO_DESEJADA = os.getenv("BS_ROBOT_POSICAO", "P-06-156")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


setup_logging()
logger = logging.getLogger(__name__)


def find_and_click(page, selectors, timeout=8000, retries=3, force=False):
    last_exc = None
    for attempt in range(1, retries + 1):
        for selector in selectors:
            try:
                elem = page.locator(selector).first
                elem.wait_for(state="visible", timeout=timeout)
                elem.click(timeout=timeout, force=force)
                return True
            except Exception as exc:
                last_exc = exc
        if attempt < retries:
            sleep = 0.5 * attempt
            logger.debug("find_and_click retry %s after %s seconds", attempt, sleep)
            time.sleep(sleep)
    raise RuntimeError(f"Não consegui clicar em nenhum seletor {selectors}. Erro: {last_exc}")

# Arquivo do template (imagem referência)
TEMPLATE_PATH = Path(__file__).parent / "images" / "posição.png"
if not TEMPLATE_PATH.exists():
    alt = Path(__file__).parent / "images" / "posicao.png"
    if alt.exists():
        TEMPLATE_PATH = alt
    else:
        raise FileNotFoundError(
            f"Template não encontrado. Coloque 'posição.png' (ou 'posicao.png') na pasta do script. "
            f"Procurei em: {TEMPLATE_PATH} e {alt}"
        )

# Ajustes do template matching (sem OpenCV)
TEMPLATE_SCALE = 0.25      # reduz canvas/template pra ficar rápido (0.25 costuma ser ótimo)
ROI_PADDING_PX = 120       # margem ao redor do match (em pixels do canvas original)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        context = browser.new_context(
            locale="pt-BR",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        # 1) Acessa site
        page.goto(BASE_URL, wait_until="domcontentloaded")

        # 2) Clica em "Entrar"
        find_and_click(page, ["button:has-text('Entrar')", "text=Entrar"], timeout=8000)

        # 3) Login
        page.get_by_placeholder("Email").fill(EMAIL, timeout=8000)
        page.get_by_placeholder("Senha").fill(SENHA, timeout=8000)

        find_and_click(page, ["form button:has-text('Entrar')", "button:has-text('Entrar')"], timeout=8000)

        page.wait_for_load_state("networkidle", timeout=20000)

        # 4) Vai direto para booking
        page.goto(BOOKING_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=20000)

        # 5) Seleciona data +7 e confirma OK
        selecionar_data_mais_7_dias(page)

        # 6) Busca ROI baseado no template (posição.png) e tenta reservar só naquela região
        procurar_e_reservar_por_template_numpy(
            page,
            posicao_desejada=POSICAO_DESEJADA,
            template_path=str(TEMPLATE_PATH),
            canvas_selector="canvas",
            scale=TEMPLATE_SCALE,
            roi_padding_px=ROI_PADDING_PX,
            max_tentativas=None,  # tenta todos os pontos verdes dentro da ROI
        )

        logger.info("Fluxo concluído (reserva acionada na posição desejada).")
        input("Pressione ENTER no terminal para encerrar o navegador...")

        context.close()
        browser.close()


# -------------------------
# CALENDÁRIO (PrimeVue)
# -------------------------

def selecionar_data_mais_7_dias(page):
    alvo = datetime.today() + timedelta(days=7)
    dia_alvo = str(alvo.day)
    mes_alvo = alvo.month
    ano_alvo = alvo.year

    abrir_calendario(page)

    datepicker = page.locator(".p-datepicker").first
    datepicker.wait_for(state="visible", timeout=12000)

    def ler_mes_ano():
        mes_txt = datepicker.locator(".p-datepicker-month").first.inner_text().strip()
        ano_txt = datepicker.locator(".p-datepicker-year").first.inner_text().strip()
        return _mes_pt(mes_txt), int(ano_txt)

    mes_vis, ano_vis = ler_mes_ano()

    for _ in range(36):
        if (ano_vis, mes_vis) == (ano_alvo, mes_alvo):
            break

        if (ano_vis, mes_vis) < (ano_alvo, mes_alvo):
            datepicker.locator("button.p-datepicker-next").click(timeout=5000)
        else:
            datepicker.locator("button.p-datepicker-prev").click(timeout=5000)

        page.wait_for_timeout(200)
        mes_vis, ano_vis = ler_mes_ano()
    else:
        raise RuntimeError("Não consegui navegar até o mês/ano alvo no calendário.")

    dia_locator = datepicker.locator(
        "td:not(.p-datepicker-other-month) span:not(.p-disabled)"
    ).filter(has_text=dia_alvo).first

    dia_locator.wait_for(state="visible", timeout=8000)
    dia_locator.click(timeout=8000)

    clicar_ok_calendario(page)

    logger.info("Data confirmada: %s", alvo.strftime('%d/%m/%Y'))


def abrir_calendario(page):
    # clica no ícone calendar_today ao lado do campo Data
    page.locator("div").filter(
        has=page.get_by_text("Data", exact=True)
    ).locator(
        "i.md-icon", has_text="calendar_today"
    ).first.click(timeout=8000, force=True)

    page.locator(".p-datepicker").first.wait_for(state="visible", timeout=12000)


def _mes_pt(nome_mes: str) -> int:
    meses = {
        "Janeiro": 1, "Fevereiro": 2, "Março": 3, "Abril": 4,
        "Maio": 5, "Junho": 6, "Julho": 7, "Agosto": 8,
        "Setembro": 9, "Outubro": 10, "Novembro": 11, "Dezembro": 12,
    }
    nome_mes = nome_mes.strip()
    if nome_mes not in meses:
        raise ValueError(f"Nome do mês inesperado no calendário: '{nome_mes}'")
    return meses[nome_mes]


def clicar_ok_calendario(page):
    candidatos = [
        "button:has-text('OK')",
        "button.p-button:has-text('OK')",
        "span.p-button-label:has-text('OK')",
        "div.md-dialog-content button:has-text('OK')",
    ]
    try:
        find_and_click(page, candidatos, timeout=6000)
    except RuntimeError as exc:
        logger.error("clicar_ok_calendario falhou: %s", exc)
        raise


# -------------------------
# TEMPLATE MATCHING (sem cv2): SSD em imagem reduzida
# -------------------------

def _to_gray_array(png_bytes: bytes) -> np.ndarray:
    img = Image.open(BytesIO(png_bytes)).convert("L")  # grayscale
    return np.array(img, dtype=np.float32)


def _to_gray_array_from_path(path: str) -> np.ndarray:
    img = Image.open(path).convert("L")
    return np.array(img, dtype=np.float32)


def _resize_gray(arr: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return arr
    h, w = arr.shape[:2]
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    img = Image.fromarray(arr.astype(np.uint8), mode="L").resize((new_w, new_h), Image.BILINEAR)
    return np.array(img, dtype=np.float32)


def _sliding_window_view_2d(a: np.ndarray, window_shape: tuple[int, int]) -> np.ndarray:
    # numpy >= 1.20 tem sliding_window_view
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return sliding_window_view(a, window_shape)
    except Exception as e:
        raise RuntimeError(
            "Seu NumPy não possui sliding_window_view. Atualize com: python3.11 -m pip install -U numpy"
        ) from e


def encontrar_roi_por_template_numpy(
    canvas_png_bytes: bytes,
    template_path: str,
    scale: float = 0.25,
    roi_padding_px: int = 120,
) -> tuple[int, int, int, int]:
    """
    Encontra onde o template aparece no canvas via SSD (sum of squared differences),
    rodando em escala reduzida pra ficar rápido.

    ✅ Agora com AUTO-SCALE: se o template for maior que o canvas,
    ajusta automaticamente a escala para caber.
    """
    canvas_gray = _to_gray_array(canvas_png_bytes)
    template_gray = _to_gray_array_from_path(template_path)

    H0, W0 = canvas_gray.shape
    ht0, wt0 = template_gray.shape

    # --- AUTO-SCALE: garante template menor que canvas ---
    # Queremos (ht0*scale < H0*scale) e (wt0*scale < W0*scale) => equivalente a ht0 < H0 e wt0 < W0,
    # mas como às vezes canvas/template vêm de fontes com tamanhos diferentes, vamos ajustar por segurança.
    # Se template já é maior que canvas no original, vamos reduzir MAIS ainda o template via scale efetivo.
    scale_eff = scale

    # Se template for maior que o canvas, força um scale menor o suficiente
    if ht0 >= H0 or wt0 >= W0:
        # calcula o scale máximo para o template caber (com folga)
        s_h = (H0 - 2) / max(1, ht0)
        s_w = (W0 - 2) / max(1, wt0)
        s_max = min(s_h, s_w)

        # também respeita o scale solicitado (pega o menor)
        scale_eff = min(scale_eff, s_max * 0.95)  # 5% de folga

        if scale_eff <= 0 or scale_eff >= 1:
            # se ainda der estranho, usa um valor bem pequeno
            scale_eff = 0.15

    # Para debug
    print(f"[TemplateMatch] canvas={W0}x{H0} template={wt0}x{ht0} scale_in={scale} scale_eff={scale_eff:.3f}")

    # reduz escala
    canvas_small = _resize_gray(canvas_gray, scale_eff)
    tpl_small = _resize_gray(template_gray, scale_eff)

    H, W = canvas_small.shape
    h, w = tpl_small.shape

    if h >= H or w >= W:
        raise RuntimeError(
            f"Template ainda maior que canvas após scale_eff={scale_eff:.3f}. "
            f"canvas_small={W}x{H} tpl_small={w}x{h}. "
            f"Recorte mais o template (posição.png) para só a região do mapa."
        )

    windows = _sliding_window_view_2d(canvas_small, (h, w))
    diff = windows - tpl_small
    ssd = np.sum(diff * diff, axis=(2, 3))

    y, x = np.unravel_index(np.argmin(ssd), ssd.shape)

    # converte coords de volta pro original (dividindo pela escala efetiva)
    x_orig = int(x / scale_eff)
    y_orig = int(y / scale_eff)
    w_orig = int(w / scale_eff)
    h_orig = int(h / scale_eff)

    x0 = max(0, x_orig - roi_padding_px)
    y0 = max(0, y_orig - roi_padding_px)
    x1 = min(W0, x_orig + w_orig + roi_padding_px)
    y1 = min(H0, y_orig + h_orig + roi_padding_px)

    return (x0, y0, x1, y1)



# -------------------------
# CANVAS: bolinhas verdes na ROI + valida modal + reservar
# -------------------------

def _coletar_pontos_verdes(png_bytes: bytes, step: int = 7, roi: tuple[int, int, int, int] | None = None):
    img = Image.open(BytesIO(png_bytes)).convert("RGB")
    arr = np.array(img)  # H x W x 3
    H, W = arr.shape[0], arr.shape[1]

    if roi is None:
        x0, y0, x1, y1 = 0, 0, W, H
    else:
        x0, y0, x1, y1 = roi
        x0 = max(0, min(W, x0)); x1 = max(0, min(W, x1))
        y0 = max(0, min(H, y0)); y1 = max(0, min(H, y1))

    sub = arr[y0:y1, x0:x1, :]

    r = sub[:, :, 0].astype(np.int16)
    g = sub[:, :, 1].astype(np.int16)
    b = sub[:, :, 2].astype(np.int16)

    # Threshold "verde" (ajuste se necessário)
    mask = (g > 150) & (r < 130) & (b < 130) & ((g - r) > 40) & ((g - b) > 40)

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return []

    seen = set()
    pts = []
    for x, y in sorted(zip(xs.tolist(), ys.tolist()), key=lambda t: (t[1], t[0])):
        key = (x // step, y // step)
        if key in seen:
            continue
        seen.add(key)
        pts.append((int(x + x0), int(y + y0)))

    return pts


def _esperar_modal_reserva(page, timeout=12000):
    modal = page.locator("div.md-dialog-container.md-dialog-fullscreen").first
    modal.wait_for(state="visible", timeout=timeout)
    modal.locator("span.md-dialog-title", has_text="Reservar mesa").wait_for(state="visible", timeout=timeout)
    return modal


def _ler_posicao_no_modal(modal) -> str:
    pos = modal.locator("div:has(i.md-icon:has-text('desktop_windows')) span").first
    pos.wait_for(state="visible", timeout=8000)
    return pos.inner_text().strip()


def _clicar_cancelar(modal):
    modal.locator("button.md-button:has-text('Cancelar')").first.click(timeout=8000)


def _clicar_reservar(modal):
    modal.locator("button.md-button:has-text('Reservar')").last.click(timeout=8000)


def procurar_e_reservar_por_template_numpy(
    page,
    posicao_desejada: str,
    template_path: str,
    canvas_selector: str = "canvas",
    scale: float = 0.25,
    roi_padding_px: int = 120,
    max_tentativas: int | None = None,
) -> bool:
    canvas = page.locator(canvas_selector).first
    canvas.wait_for(state="visible", timeout=20000)

    # tempo pro mapa renderizar
    page.wait_for_timeout(1200)

    canvas_png = canvas.screenshot()

    # 1) ROI pelo template (sem OpenCV)
    for attempt in range(1, 4):
        try:
            roi = encontrar_roi_por_template_numpy(
                canvas_png_bytes=canvas_png,
                template_path=template_path,
                scale=scale,
                roi_padding_px=roi_padding_px,
            )
            break
        except Exception as exc:
            logger.warning("Tentativa %s de encontrar ROI falhou: %s", attempt, exc)
            if attempt == 3:
                raise
            time.sleep(0.5 * attempt)
    logger.info("ROI encontrada pelo template: %s", roi)

    # 2) dentro da ROI, detecta pontos verdes
    pontos = _coletar_pontos_verdes(canvas_png, step=7, roi=roi)
    if not pontos:
        raise RuntimeError("Não encontrei bolinhas verdes dentro da ROI. Aumente roi_padding_px ou ajuste threshold do verde.")

    clicados = set()
    tentativas = 0

    for (x, y) in pontos:
        if max_tentativas is not None and tentativas >= max_tentativas:
            break

        key = (x // 10, y // 10)
        if key in clicados:
            continue
        clicados.add(key)

        tentativas += 1

        canvas.click(position={"x": x, "y": y}, timeout=8000)
        page.wait_for_timeout(250)

        try:
            modal = _esperar_modal_reserva(page, timeout=8000)
        except PWTimeout:
            continue

        posicao = _ler_posicao_no_modal(modal)
        print(f"[{tentativas}] Clique ({x},{y}) -> posição: {posicao}")

        if posicao == posicao_desejada:
            _clicar_reservar(modal)
            print(f"✅ Achou {posicao_desejada} e clicou em Reservar.")
            return True

        _clicar_cancelar(modal)
        modal.wait_for(state="hidden", timeout=8000)
        page.wait_for_timeout(200)

    raise RuntimeError(f"Não encontrei {posicao_desejada} na ROI do template após {tentativas} tentativas.")


if __name__ == "__main__":
    run()
