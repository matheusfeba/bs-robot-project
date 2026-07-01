import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright


BASE_URL = os.getenv("BS_ROBOT_BASE_URL", "https://bradescoseguros.neowrk.com")
BOOKING_URL = os.getenv("BS_ROBOT_BOOKING_URL", "https://bradescoseguros.neowrk.com/desk4me/desk/booking")

EMAIL = os.getenv("BS_ROBOT_EMAIL", "")
SENHA = os.getenv("BS_ROBOT_PASS", "")
POSICAO_DESEJADA = os.getenv("BS_ROBOT_POSICAO", "P-06-156")

if not EMAIL or not SENHA:
    raise SystemExit(
        "Erro: BS_ROBOT_EMAIL e BS_ROBOT_PASS devem ser definidos via variáveis de ambiente."
    )

# Data alvo: DD/MM ou DD/MM/YYYY. Se não informada, usa hoje +7 dias.
TARGET_DATE_STR = os.getenv("BS_ROBOT_TARGET_DATE", "")

DEBUG_DIR = Path(__file__).parent / "images"

MESES_PT = {
    "Janeiro": 1, "Fevereiro": 2, "Março": 3, "Abril": 4,
    "Maio": 5, "Junho": 6, "Julho": 7, "Agosto": 8,
    "Setembro": 9, "Outubro": 10, "Novembro": 11, "Dezembro": 12,
}


def setup_logging() -> None:
    log_file = Path(__file__).parent / "images" / "robot.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(str(log_file), mode="w", encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


setup_logging()
logger = logging.getLogger(__name__)


def parse_target_date(date_str: str) -> datetime:
    today = datetime.today()
    parts = date_str.strip().split("/")
    if len(parts) < 2:
        raise ValueError(f"Formato inválido: {date_str}. Use DD/MM ou DD/MM/YYYY")
    day, month = int(parts[0]), int(parts[1])
    year = int(parts[2]) if len(parts) == 3 else today.year
    candidate = datetime(year, month, day)
    # If the date already passed and no year was given, try next year
    if candidate.date() < today.date() and len(parts) == 2:
        candidate = datetime(year + 1, month, day)
    return candidate


TARGET_DATE = parse_target_date(TARGET_DATE_STR) if TARGET_DATE_STR else datetime.today() + timedelta(days=7)


def _debug_screenshot(page, name: str):
    try:
        path = DEBUG_DIR / f"debug_{name}.png"
        page.screenshot(path=str(path))
        logger.info("Screenshot salvo: %s", path.name)
    except Exception as e:
        logger.debug("Screenshot falhou (%s): %s", name, e)


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
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"Não consegui clicar em {selectors}. Erro: {last_exc}")


# ─────────────────────────── LOGIN ───────────────────────────

def fazer_login(page):
    logger.info("Acessando %s", BASE_URL)
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # If the Email field isn't visible yet, we're on the landing page — click Entrar
    try:
        page.get_by_placeholder("Email").wait_for(state="visible", timeout=3000)
        logger.info("Email field já visível, sem landing page")
    except PWTimeout:
        logger.info("Landing page detectada — clicando em Entrar")
        page.locator("a:has-text('Entrar'), button:has-text('Entrar')").first.click(timeout=8000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(1000)

    logger.info("Preenchendo credenciais: %s", EMAIL)
    page.get_by_placeholder("Email").fill(EMAIL, timeout=10000)
    page.get_by_placeholder("Senha").fill(SENHA, timeout=8000)

    _debug_screenshot(page, "01_credenciais")

    page.locator("button:has-text('Entrar')").first.click(timeout=8000)

    # Step 1: wait for OAuth redirect away from the identity provider
    try:
        page.wait_for_url(
            lambda url: "Account/Login" not in url and "login" not in url.lower(),
            timeout=30000,
        )
        logger.info("OAuth redirect detectado: %s", page.url[:80])
    except PWTimeout:
        logger.warning("Timeout esperando redirect OAuth")

    # Step 2: wait for the /signin SPA callback to finish and redirect to the main app
    try:
        page.wait_for_url(lambda url: "/signin" not in url, timeout=30000)
        logger.info("App carregado: %s", page.url)
    except PWTimeout:
        logger.warning("Timeout esperando redirect do /signin — continuando")

    # Step 3: wait for DOM only (networkidle hangs on SPAs with background polling)
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    logger.info("Login concluído. URL: %s", page.url)
    _debug_screenshot(page, "02_pos_login")


def fechar_popups(page):
    """Dismiss any feedback/survey dialog that appears after login."""
    page.wait_for_timeout(1500)

    selectors = [
        # Angular Material icon button with 'close' icon
        "button.md-icon-button:has(i.md-icon:text('close'))",
        "button.md-icon-button:has(md-icon:text('close'))",
        # Generic close/dismiss buttons
        "button[aria-label='Close']",
        "button[aria-label='Fechar']",
        "[class*='close']:visible",
        # Sometimes the X is a bare button with × text
        "button:text('×'), button:text('✕')",
    ]

    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=1500):
                elem.click(timeout=3000, force=True)
                logger.info("Popup fechado com: %s", sel)
                page.wait_for_timeout(600)
                return
        except Exception:
            continue

    # Last resort: Escape
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        logger.debug("Pressionou Escape para fechar popup")
    except Exception:
        pass


# ─────────────────────────── CALENDÁRIO ───────────────────────────

def _calendario_esta_aberto(page) -> bool:
    """Returns True if the calendar picker dialog is currently visible."""
    indicators = [
        "md-calendar",
        ".md-calendar",
        ".md-datepicker-calendar-pane",
        ".md-datepicker-calendar-pane.md-pane-open",
        # The calendar footer has LIMPAR button — only visible when calendar is open
        "button:has-text('LIMPAR')",
        "button:has-text('Limpar')",
    ]
    for sel in indicators:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    return False


def _tentar_abrir_e_verificar(page, selector: str, force: bool = False) -> bool:
    """Click the selector and return True if the calendar actually opened."""
    try:
        elem = page.locator(selector).first
        if not elem.is_visible(timeout=1500):
            return False
        elem.click(force=force, timeout=5000)
        page.wait_for_timeout(900)
        if _calendario_esta_aberto(page):
            logger.info("Calendário aberto via: %s", selector)
            return True
    except Exception:
        pass
    return False


def abrir_calendario(page):
    """Click the correct calendar trigger and verify the picker opens."""
    # Save current page HTML for debugging calendar structure
    try:
        cal_html = page.evaluate("""
            () => {
                const el = document.querySelector('md-datepicker, [class*="datepicker"], [ng-model*="date"]');
                return el ? el.outerHTML.slice(0, 500) : 'not found';
            }
        """)
        logger.debug("Calendário HTML: %s", cal_html)
    except Exception:
        pass

    # Ordered list of selectors to try — from most specific to most generic
    attempts = [
        (".md-datepicker-triangle-button", False),
        ("md-datepicker button", False),
        ("button.md-datepicker-triangle-button", False),
        (".md-datepicker-button", False),
        ("md-datepicker input", False),
        ("button[aria-label*='calendar' i]", False),
    ]

    for sel, force in attempts:
        if _tentar_abrir_e_verificar(page, sel, force):
            return

    # Fallback: click the calendar icon near the Data label in Calendário section
    try:
        container = page.locator("md-input-container, .md-input-container, div").filter(
            has=page.locator("label:has-text('Data')")
        ).first
        for part in ["button", "i.md-icon", "md-icon", "[class*='icon']"]:
            try:
                elem = container.locator(part).first
                if elem.is_visible(timeout=800):
                    elem.click(force=True, timeout=4000)
                    page.wait_for_timeout(900)
                    if _calendario_esta_aberto(page):
                        logger.info("Calendário aberto via container Data + %s", part)
                        return
            except Exception:
                continue
    except Exception as e:
        logger.debug("Fallback container: %s", e)

    # Last resort: JavaScript — click datepicker triangle button or all calendar_today icons
    for js in [
        # Angular Material datepicker triangle button
        """() => {
            const btn = document.querySelector('.md-datepicker-triangle-button');
            if (btn) { btn.click(); return 'triangle-btn'; }
            return null;
        }""",
        # Any visible calendar_today icon
        """() => {
            for (const el of document.querySelectorAll('i, md-icon, span')) {
                if (el.textContent.trim() === 'calendar_today' && el.offsetParent) {
                    const trigger = el.closest('button, [role="button"], md-datepicker') || el;
                    trigger.click();
                    return 'calendar_today icon';
                }
            }
            return null;
        }""",
    ]:
        try:
            result = page.evaluate(js)
            if result:
                page.wait_for_timeout(900)
                if _calendario_esta_aberto(page):
                    logger.info("Calendário aberto via JS: %s", result)
                    return
        except Exception:
            pass

    _debug_screenshot(page, "erro_abrir_calendario")
    # Dump all buttons for diagnosis
    try:
        btns = page.evaluate("""
            () => Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(e => e.offsetParent)
                .map(e => e.className + ' | ' + e.textContent.trim().slice(0, 40))
                .slice(0, 20)
        """)
        logger.error("Botões visíveis: %s", btns)
    except Exception:
        pass
    raise RuntimeError("Não consegui abrir o calendário")


def _cal_container() -> str:
    """Return JS IIFE that resolves to the calendar container element.

    Anchors on the 'Limpar'/'LIMPAR' button (case-insensitive), which only
    appears when the calendar picker is open.
    """
    return r"""
        (() => {
            const months = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                            'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
            // Case-insensitive match on LIMPAR (may be title-case in DOM even if CSS shows all-caps)
            const buttons = Array.from(document.querySelectorAll('button'));
            const limpar = buttons.find(b =>
                b.textContent.trim().toLowerCase() === 'limpar' && b.offsetParent !== null
            );
            if (!limpar) return null;
            // Walk up until we find an ancestor that contains a Portuguese month name
            let el = limpar.parentElement;
            for (let i = 0; i < 15; i++) {
                if (!el || el.tagName === 'BODY') return null;
                if (months.some(m => el.textContent.includes(m))) return el;
                el = el.parentElement;
            }
            return null;
        })()
    """


def _ler_mes_ano_calendario(page) -> tuple[int, int]:
    """Read month and year from the open calendar dialog."""
    # Primary: use the calendar container anchored on LIMPAR
    text = page.evaluate(f"""
        () => {{
            const container = {_cal_container().strip()};
            return container ? container.textContent : '';
        }}
    """)

    if text:
        for nome, num in MESES_PT.items():
            idx = text.find(nome)
            if idx >= 0:
                # No word-boundary — month and year may be adjacent ("Junho2026")
                vicinity = text[max(0, idx - 2): idx + len(nome) + 25]
                m = re.search(r'(20\d{2})', vicinity)
                if m:
                    return num, int(m.group(1))

    # Fallback: scan the entire page body text
    try:
        body = page.inner_text("body", timeout=3000)
        for nome, num in MESES_PT.items():
            idx = body.find(nome)
            if idx >= 0:
                vicinity = body[max(0, idx - 2): idx + len(nome) + 25]
                m = re.search(r'(20\d{2})', vicinity)
                if m:
                    return num, int(m.group(1))
    except Exception as e:
        logger.debug("Body scan: %s", e)

    # Debug: dump buttons found to understand what's on the page
    try:
        info = page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent)
                    .map(b => '"' + b.textContent.trim().slice(0,30) + '"');
                return btns.join(', ');
            }
        """)
        logger.error("Botões visíveis no calendário: %s", info)
    except Exception:
        pass

    raise RuntimeError("Não consegui detectar mês/ano do calendário")


def _navegar_calendario(page, direction: str):
    """Click the prev/next arrow in the open calendar.

    The nav buttons may have SVG/icon content with no textContent, so we use
    an exclusion approach: any button in the calendar that is NOT a footer
    button (limpar/cancelar/ok) and NOT a day number is a nav button.
    """
    result = page.evaluate(f"""
        () => {{
            const container = {_cal_container().strip()};
            if (!container) return 'no-container';

            const footerWords = ['limpar', 'cancelar', 'ok'];
            const allBtns = Array.from(container.querySelectorAll('button'));

            // 1. Try literal '<' / '>' first (works when buttons use text chars)
            const symbol = '{('>' if direction == 'next' else '<')}';
            const symBtn = allBtns.find(b =>
                (b.textContent.trim() === symbol || (b.innerText || '').trim() === symbol)
                && b.offsetParent !== null
            );
            if (symBtn) {{ symBtn.click(); return 'symbol:' + symbol; }}

            // 2. Exclusion: filter out footer buttons and day-number buttons
            const navBtns = allBtns.filter(b => {{
                if (!b.offsetParent) return false;
                const txt = b.textContent.trim().toLowerCase();
                if (footerWords.includes(txt)) return false;
                // Exclude day numbers (pure digit strings 1–31)
                if (/^\d{{1,2}}$/.test(txt) && parseInt(txt) >= 1 && parseInt(txt) <= 31) return false;
                return true;
            }});

            if (navBtns.length === 0) {{
                const all = allBtns.map(b => '"' + b.textContent.trim().slice(0,15) + '"').join(', ');
                return 'no-nav-btns (all: ' + all + ')';
            }}

            // First button = prev, last = next
            const btn = '{direction}' === 'next' ? navBtns[navBtns.length - 1] : navBtns[0];
            btn.click();
            return 'exclusion[' + navBtns.length + ']:' + btn.textContent.trim().slice(0, 10);
        }}
    """)

    logger.debug("Nav '%s': %s", direction, result)
    if result and result.startswith('no'):
        logger.warning("Navegação '%s' possivelmente falhou: %s", direction, result)
    page.wait_for_timeout(450)


def _clicar_dia(page, dia: int):
    """Click a specific day number in the calendar grid (skips other-month cells)."""
    dia_str = str(dia)

    clicked = page.evaluate(f"""
        () => {{
            const container = {_cal_container().strip()};
            if (!container) return 'no-container';

            // Look for td cells whose direct span text matches the day
            const tds = Array.from(container.querySelectorAll('td'));
            for (const td of tds) {{
                // Skip cells that clearly belong to another month (greyed out / disabled)
                const cls = (td.className || '') + (td.getAttribute('class') || '');
                if (cls.includes('disabled') || cls.includes('other-month') || cls.includes('out-of')) continue;

                const spans = Array.from(td.querySelectorAll('span'));
                const match = spans.find(s => s.textContent.trim() === '{dia_str}');
                if (match && match.offsetParent !== null) {{
                    match.click();
                    return 'span-in-td';
                }}
                // td itself might be the clickable element
                if (td.textContent.trim() === '{dia_str}' && td.offsetParent !== null) {{
                    td.click();
                    return 'td';
                }}
            }}

            // Wider search: any span inside the calendar with that text
            const spans = Array.from(container.querySelectorAll('span'));
            for (const sp of spans) {{
                if (sp.textContent.trim() === '{dia_str}' && sp.offsetParent !== null) {{
                    const parent = sp.closest('td');
                    if (parent) {{
                        const cls = parent.className || '';
                        if (cls.includes('disabled') || cls.includes('other-month')) continue;
                    }}
                    sp.click();
                    return 'span-fallback';
                }}
            }}
            return null;
        }}
    """)

    if not clicked:
        _debug_screenshot(page, f"erro_dia_{dia_str}")
        raise RuntimeError(f"Não consegui clicar no dia {dia_str}")

    logger.info("Clicou no dia %s (%s)", dia_str, clicked)


def selecionar_data(page, alvo: datetime):
    logger.info("Selecionando data: %s", alvo.strftime("%d/%m/%Y"))
    abrir_calendario(page)
    page.wait_for_timeout(800)
    _debug_screenshot(page, "05_calendario_aberto")

    dia_alvo, mes_alvo, ano_alvo = alvo.day, alvo.month, alvo.year

    for _ in range(36):
        try:
            mes_vis, ano_vis = _ler_mes_ano_calendario(page)
            logger.info("Calendário em: %s/%d", list(MESES_PT.keys())[mes_vis - 1], ano_vis)
        except RuntimeError:
            _debug_screenshot(page, "erro_leitura_mes")
            raise

        if (ano_vis, mes_vis) == (ano_alvo, mes_alvo):
            break

        _navegar_calendario(page, "next" if (ano_vis, mes_vis) < (ano_alvo, mes_alvo) else "prev")
    else:
        raise RuntimeError("Não consegui navegar até o mês/ano alvo no calendário.")

    _clicar_dia(page, dia_alvo)
    page.wait_for_timeout(300)

    # Click OK — anchor on LIMPAR's sibling
    ok_clicked = page.evaluate(f"""
        () => {{
            const container = {_cal_container().strip()};
            if (!container) return false;
            const okBtn = Array.from(container.querySelectorAll('button'))
                .find(b => b.textContent.trim() === 'OK');
            if (okBtn) {{ okBtn.click(); return true; }}
            return false;
        }}
    """)
    if not ok_clicked:
        # Fallback: any visible OK button
        find_and_click(page, ["button:has-text('OK')"], timeout=6000)

    logger.info("Data confirmada: %s", alvo.strftime("%d/%m/%Y"))
    page.wait_for_timeout(1500)


# ─────────────────────────── CANVAS: OPENLAYERS JS ───────────────────────────

_OL_FIND_MAP_JS = r"""
    (function findOLMap() {
        const canvas = document.querySelector('canvas');
        if (!canvas) return null;
        const vp = canvas.closest('.ol-viewport');
        const container = vp ? vp.parentElement : null;
        if (!container) return null;

        function isMap(v) {
            return v && typeof v === 'object'
                && typeof v.getLayers === 'function'
                && typeof v.getPixelFromCoordinate === 'function';
        }

        // Walk DOM up from the map container, checking own properties
        let el = container;
        while (el && el !== document.body) {
            for (const prop of Object.getOwnPropertyNames(el)) {
                try { if (isMap(el[prop])) return el[prop]; } catch(e) {}
            }
            // Also check Vue 3 component context
            const comp = el.__vueParentComponent;
            if (comp) {
                for (const ctx of [comp.ctx, comp.setupState, comp.data]) {
                    if (!ctx) continue;
                    for (const k of Object.keys(ctx)) {
                        try {
                            const v = ctx[k];
                            if (isMap(v)) return v;
                            if (v && typeof v === 'object') {
                                for (const k2 of Object.keys(v || {})) {
                                    try { if (isMap(v[k2])) return v[k2]; } catch(e) {}
                                }
                            }
                        } catch(e) {}
                    }
                }
            }
            el = el.parentElement;
        }
        return null;
    })()
"""


def _encontrar_posicao_ol_js(page, posicao: str):
    """Locate a desk feature in the OpenLayers map and return its canvas pixel (x, y)."""
    result = page.evaluate(f"""
        () => {{
            const map = {_OL_FIND_MAP_JS};
            if (!map) return {{error: 'map-not-found'}};

            let found = null;
            try {{
                map.getLayers().forEach(layer => {{
                    if (found) return;
                    try {{
                        const src = layer.getSource?.();
                        if (!src?.getFeatures) return;
                        for (const feat of src.getFeatures()) {{
                            const props = feat.getProperties?.() || {{}};
                            if (JSON.stringify(props).includes({repr(posicao)})) {{
                                const geom = feat.getGeometry();
                                if (!geom) continue;
                                let coord;
                                if (geom.getType() === 'Point') {{
                                    coord = geom.getCoordinates();
                                }} else {{
                                    const ext = geom.getExtent();
                                    coord = [(ext[0]+ext[2])/2, (ext[1]+ext[3])/2];
                                }}
                                const px = map.getPixelFromCoordinate(coord);
                                if (px) found = {{x: Math.round(px[0]), y: Math.round(px[1])}};
                                break;
                            }}
                        }}
                    }} catch(e) {{}}
                }});
            }} catch(e) {{ return {{error: String(e)}}; }}
            return found;
        }}
    """)
    if result and isinstance(result, dict) and 'x' in result:
        return (result['x'], result['y'])
    if result and isinstance(result, dict):
        logger.debug("OL JS resultado: %s", result)
    return None


def _listar_features_ol_js(page) -> list[str]:
    """Return the first 30 feature property blobs for debugging."""
    result = page.evaluate(f"""
        () => {{
            const map = {_OL_FIND_MAP_JS};
            if (!map) return ['MAP_NOT_FOUND'];
            const names = [];
            try {{
                map.getLayers().forEach(layer => {{
                    try {{
                        const src = layer.getSource?.();
                        if (!src?.getFeatures) return;
                        for (const f of src.getFeatures()) {{
                            names.push(JSON.stringify(f.getProperties?.() || {{}}).slice(0,200));
                        }}
                    }} catch(e) {{}}
                }});
            }} catch(e) {{}}
            return names.slice(0, 30);
        }}
    """)
    return result or []


# ─────────────────────────── CANVAS: CARD INFO + MODAL ───────────────────────────

def _esperar_info_card(page, timeout=3000):
    """Wait for the OpenLayers feature-info card (#feature-informations) to become visible."""
    card = page.locator("#feature-informations").first
    try:
        card.wait_for(state="visible", timeout=timeout)
        return card
    except PWTimeout:
        return None


def _ler_posicao_no_card(card) -> str | None:
    """Read a P-XX-XXX desk code from the OL feature-info card."""
    try:
        text = card.inner_text(timeout=2000)
        m = re.search(r'P-\d{2}-\d{3}', text)
        if m:
            return m.group()
    except Exception:
        pass
    return None


def _esperar_modal_reserva(page, timeout=8000):
    """Wait for a reservation confirmation dialog that contains a 'Reservar' button."""
    try:
        modal = page.locator("div.md-dialog-container, md-dialog").first
        modal.wait_for(state="visible", timeout=timeout)
        return modal
    except PWTimeout:
        raise PWTimeout("Modal de reserva não encontrado")


def _ler_posicao_no_modal(modal) -> str:
    """Read a P-XX-XXX desk code from a reservation modal (strict regex only)."""
    try:
        full_text = modal.inner_text(timeout=3000)
        m = re.search(r'P-\d{2}-\d{3}', full_text)
        if m:
            return m.group()
    except Exception:
        pass
    raise RuntimeError("Código P-XX-XXX não encontrado no modal")


def _clicar_reservar_no_card(card):
    """Click the Reservar button inside the OL feature-info card."""
    card.locator("button:has-text('Reservar'), button:has-text('RESERVAR')").first.click(
        timeout=5000, force=True
    )


def _clicar_reservar(modal):
    modal.locator("button:has-text('Reservar')").last.click(timeout=8000)


def _fechar_card(page):
    """Dismiss the OL feature-info card."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        btn = page.locator("#feature-informations button[class*='close'], #feature-informations button[aria-label*='close' i], #feature-informations button:has(i:text('close'))").first
        if btn.is_visible(timeout=0):
            btn.click(force=True)
    except Exception:
        pass


# ─────────────────────────── PROCURAR E RESERVAR ───────────────────────────

def procurar_e_reservar(page, posicao_desejada: str, canvas_selector: str = "canvas") -> bool:
    canvas = page.locator(canvas_selector).first
    canvas.wait_for(state="visible", timeout=20000)

    # Allow map to fully render availability after date selection
    logger.info("Aguardando renderização do mapa após seleção de data...")
    page.wait_for_timeout(4000)

    # Save canvas screenshot for debugging
    canvas_png = canvas.screenshot()
    if DEBUG_DIR.exists():
        (DEBUG_DIR / "debug_canvas.png").write_bytes(canvas_png)
        logger.info("Canvas salvo em debug_canvas.png")

    # ── Strategy 1: OpenLayers JS API ──────────────────────────────────────────
    logger.info("Localizando %s via OpenLayers JS...", posicao_desejada)
    pixel = _encontrar_posicao_ol_js(page, posicao_desejada)

    if pixel:
        logger.info("OL JS encontrou %s em pixel (%d,%d)", posicao_desejada, *pixel)
        candidates: list[tuple[int, int]] = [pixel]
    else:
        # Debug: list what features the map has so we understand the property schema
        logger.info("OL JS não encontrou direto. Listando features para debug...")
        features = _listar_features_ol_js(page)
        if features and features[0] != 'MAP_NOT_FOUND':
            for i, f in enumerate(features[:15], 1):
                logger.info("  [feat %d] %s", i, f[:120])
        elif features and features[0] == 'MAP_NOT_FOUND':
            logger.warning("Mapa OL não acessível via JS. Usando varredura de grade.")
        else:
            logger.warning("Nenhuma feature encontrada via JS.")

        # ── Strategy 2: Top-to-bottom full canvas scan ─────────────────────────
        # The upper desk section (P-06-156) is always in the upper portion of the
        # canvas; the main desk grid is always lower. Scanning top-to-bottom
        # guarantees we reach P-06-156's area before wasting time on the main grid.
        # The canvas position shifts significantly between sessions so fixed
        # coordinates are unreliable — full coverage with fast timeouts is safer.
        candidates = [
            (x, y)
            for y in range(11, 545, 15)
            for x in range(11, 695, 15)
        ]
        logger.info("Varredura topo→baixo: %d candidatos (step 15px)", len(candidates))

    clicados: set[tuple[int, int]] = set()
    tentativas = 0
    card_logged = False

    for x, y in candidates:
        cell = (x // 10, y // 10)
        if cell in clicados:
            continue
        clicados.add(cell)
        tentativas += 1

        if tentativas > 800:
            break

        # Dismiss any open card before clicking a new position
        _fechar_card(page)
        page.wait_for_timeout(100)

        try:
            canvas.click(position={"x": x, "y": y}, force=True, timeout=5000)
        except Exception as e:
            logger.debug("Click falhou (%d,%d): %s", x, y, e)
            continue

        page.wait_for_timeout(100)

        # ── Check for OL feature-info card (#feature-informations) ─────────────
        card = _esperar_info_card(page, timeout=350)
        if card:
            # On the first card appearance, save its HTML for debugging
            if not card_logged:
                card_logged = True
                try:
                    html = card.inner_html(timeout=2000)
                    logger.info("Primeiro card HTML: %s", html[:600])
                    _debug_screenshot(page, "card_primeiro")
                except Exception:
                    pass

            posicao = _ler_posicao_no_card(card)
            if posicao:
                logger.info("[%d] (%d,%d) → card: %s", tentativas, x, y, posicao)
                if posicao == posicao_desejada:
                    logger.info("Posição encontrada! Clicando em Reservar...")
                    try:
                        _clicar_reservar_no_card(card)
                        logger.info("Clicou em Reservar no card")
                        page.wait_for_timeout(1500)
                        _debug_screenshot(page, "pos_reservar_card")

                        # Handle optional confirmation dialog
                        try:
                            modal = _esperar_modal_reserva(page, timeout=8000)
                            _clicar_reservar(modal)
                            logger.info("Confirmou reserva no modal")
                        except PWTimeout:
                            pass  # Reservation confirmed directly from card

                        logger.info("✅ Posição %s reservada!", posicao_desejada)
                        return True
                    except Exception as e:
                        logger.error("Erro ao clicar Reservar no card: %s", e)
                        _debug_screenshot(page, "erro_reservar")
                        raise

                _fechar_card(page)
                page.wait_for_timeout(200)
                continue

            # Card showed but no P-XX-XXX found — log first occurrence
            if not card_logged or tentativas <= 3:
                try:
                    card_text = card.inner_text(timeout=1500)
                    logger.debug("Card sem código P- em (%d,%d): %s", x, y, card_text[:100])
                except Exception:
                    pass
            _fechar_card(page)
            page.wait_for_timeout(150)
            continue

        # ── Fallback: check for md-dialog (some desks may open dialog directly) ─
        try:
            modal = _esperar_modal_reserva(page, timeout=350)
            posicao = _ler_posicao_no_modal(modal)
            logger.info("[%d] (%d,%d) → modal: %s", tentativas, x, y, posicao)
            if posicao == posicao_desejada:
                # Log full modal HTML and screenshot before clicking Reservar
                try:
                    html = modal.inner_html(timeout=2000)
                    logger.info("Modal HTML (antes de Reservar): %s", html[:1200])
                except Exception:
                    pass
                _debug_screenshot(page, "modal_antes_reservar")

                _clicar_reservar(modal)
                logger.info("Clicou em Reservar no modal")

                # Wait for the modal to close (server processes the booking request)
                try:
                    modal.wait_for(state="hidden", timeout=15000)
                    logger.info("Modal fechou após Reservar")
                except PWTimeout:
                    logger.warning("Modal ainda visível após 15s — pode haver erro")
                    _debug_screenshot(page, "modal_nao_fechou")

                page.wait_for_timeout(2000)
                _debug_screenshot(page, "pos_reservar_modal")

                # Check for any follow-up confirmation step
                for conf_sel in [
                    "button:has-text('Confirmar')",
                    "button:has-text('Sim')",
                    "button:has-text('OK')",
                    "button:has-text('Concluir')",
                    "button:has-text('CONFIRMAR')",
                ]:
                    try:
                        btn = page.locator(conf_sel).first
                        if btn.is_visible(timeout=2000):
                            btn.click(timeout=5000)
                            logger.info("Confirmação adicional clicada: %s", conf_sel)
                            page.wait_for_timeout(3000)
                            _debug_screenshot(page, "pos_confirmar_extra")
                            break
                    except Exception:
                        pass

                # Check for success or error text on page
                try:
                    body = page.inner_text("body", timeout=3000)
                    for word in ["sucesso", "confirmad", "reservad", "erro", "indisponível", "ocupad"]:
                        if word.lower() in body.lower():
                            logger.info("Texto pós-reserva contém: '%s'", word)
                except Exception:
                    pass

                _debug_screenshot(page, "08_final_estado")
                logger.info("✅ Posição %s — fluxo de reserva concluído!", posicao_desejada)
                return True
            try:
                modal.locator("button:has-text('Cancelar')").first.click(timeout=4000)
                modal.wait_for(state="hidden", timeout=4000)
            except Exception:
                page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except (PWTimeout, RuntimeError):
            pass  # Nothing appeared at this position

    raise RuntimeError(
        f"Posição '{posicao_desejada}' não encontrada após {tentativas} tentativas."
    )


# ─────────────────────────── MAIN ───────────────────────────

def run():
    logger.info("=== BS Robot iniciando ===")
    logger.info("Data alvo: %s | Posição: %s", TARGET_DATE.strftime("%d/%m/%Y"), POSICAO_DESEJADA)

    with sync_playwright() as p:
        headless = os.getenv("BS_ROBOT_HEADLESS", "").lower() in ("true", "1") or bool(os.getenv("CI"))
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="pt-BR",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        try:
            fazer_login(page)
            fechar_popups(page)

            logger.info("Navegando para %s", BOOKING_URL)
            page.goto(BOOKING_URL, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=20000)
            _debug_screenshot(page, "03_booking")

            selecionar_data(page, TARGET_DATE)
            _debug_screenshot(page, "06_pos_data")

            procurar_e_reservar(page, posicao_desejada=POSICAO_DESEJADA)

            logger.info("=== Reserva concluída! ===")
            _debug_screenshot(page, "07_concluido")

        except Exception as e:
            logger.error("Erro: %s", e)
            _debug_screenshot(page, "erro_final")
            raise
        finally:
            try:
                input("Pressione ENTER para fechar o navegador...")
            except EOFError:
                pass
            context.close()
            browser.close()


if __name__ == "__main__":
    run()
