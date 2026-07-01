# BS Robot — Reserva Automática de Mesa

Robô de automação para reserva da mesa **P-06-156** (6° Andar — Bradesco Ômega) no portal [Neowrk da Bradesco Seguros](https://bradescoseguros.neowrk.com). Executa automaticamente toda **segunda e terça-feira às 00:00 BRT**, reservando a mesa para a semana seguinte (+7 dias).

## Como funciona

O robô usa [Playwright](https://playwright.dev/python/) para controlar um navegador Chromium de forma headless e realiza as seguintes etapas:

1. **Login** — acessa o portal, preenche as credenciais e conclui o fluxo OAuth
2. **Navegação** — acessa a tela de reserva de mesas (`/desk4me/desk/booking`)
3. **Seleção de data** — abre o calendário e seleciona a data alvo (hoje + 7 dias por padrão)
4. **Varredura do mapa** — o mapa de plantas baixas é renderizado via OpenLayers em um `<canvas>`. O robô percorre uma grade de coordenadas pixel a pixel, clicando em cada posição com `force=True` para detectar mesas disponíveis (modal de reserva) ou ocupadas (card de informações)
5. **Reserva** — ao encontrar a mesa desejada disponível, clica em "Reservar" no modal e aguarda a confirmação do servidor
6. **Logs e screenshots** — salva logs detalhados e screenshots de debug em `app/images/`

## Pré-requisitos

- Python 3.11+
- Chromium (instalado pelo Playwright)

## Configuração local

```bash
# Clonar o repositório
git clone <url-do-repo>
cd bs-robot-project

# Criar e ativar ambiente virtual
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# Instalar dependências
pip install -r requirements.txt
playwright install chromium
```

## Variáveis de ambiente

| Variável | Descrição | Padrão |
|---|---|---|
| `BS_ROBOT_EMAIL` | E-mail de login | — |
| `BS_ROBOT_PASS` | Senha de login | — |
| `BS_ROBOT_POSICAO` | Código da mesa desejada | `P-06-156` |
| `BS_ROBOT_TARGET_DATE` | Data alvo (`DD/MM` ou `DD/MM/YYYY`). Se vazio, usa hoje +7 dias | `""` |
| `BS_ROBOT_HEADLESS` | Modo headless (`true`/`false`) | `false` |

## Execução manual

```bash
# Reservar para a data padrão (hoje +7 dias)
BS_ROBOT_EMAIL=seu@email.com BS_ROBOT_PASS=suasenha python app/main.py

# Reservar para uma data específica
BS_ROBOT_EMAIL=seu@email.com BS_ROBOT_PASS=suasenha BS_ROBOT_TARGET_DATE=07/07 python app/main.py

# Modo headless (sem abrir janela do navegador)
BS_ROBOT_HEADLESS=true BS_ROBOT_EMAIL=seu@email.com BS_ROBOT_PASS=suasenha python app/main.py
```

## GitHub Actions — Execução automática

O workflow `.github/workflows/reserva-automatica.yml` executa o robô automaticamente toda segunda e terça-feira às 00:00 BRT (03:00 UTC).

### Configurar os segredos

No repositório GitHub, acesse **Settings → Secrets and variables → Actions** e crie:

| Secret | Valor |
|---|---|
| `BS_ROBOT_EMAIL` | E-mail de login no Neowrk |
| `BS_ROBOT_PASS` | Senha do portal |

Após configurar os segredos e fazer push para a branch `main`, o agendamento estará ativo.

### Disparar manualmente

Na aba **Actions** do repositório, selecione o workflow **"Reserva Automática — BS Robot"** e clique em **"Run workflow"**. É possível informar uma data e posição específicas.

### Artefatos de debug

Ao final de cada execução, o workflow salva automaticamente os screenshots de debug e o `robot.log` como artefato (retido por 7 dias), acessível na aba Actions.

## Estrutura do projeto

```
bs-robot-project/
├── app/
│   ├── main.py          # Robô principal
│   └── images/          # Screenshots de debug e logs gerados em tempo de execução
├── .github/
│   └── workflows/
│       └── reserva-automatica.yml  # Workflow do GitHub Actions
├── requirements.txt
└── README.md
```

## Observações técnicas

- O mapa OpenLayers renderiza em posições de pixel levemente diferentes a cada sessão. O scan usa uma varredura ordenada por proximidade à última posição conhecida de P-06-156 (`y≈199`, `x≈451`) para encontrá-la mais rapidamente
- Cliques no canvas precisam de `force=True` pois o elemento `card-container#feature-informations` intercepta eventos de ponteiro
- A reserva é confirmada quando o modal fecha após o clique em "Reservar" e o texto "Sua reserva foi efetuada com sucesso" aparece na página
