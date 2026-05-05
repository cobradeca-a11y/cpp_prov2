# Auditoria 66 — OCR Local + Revisão em Lote

**Build:** audit-66-local-ocr  
**Data:** 2026-05-04  
**Status:** IMPLEMENTADO

---

## O que foi implementado

### 1. Motor OCR local via pytesseract (`backend/ocr_engine_local.py`)

- OCR local sem nenhuma credencial externa
- Funciona com PDF (via PyMuPDF) e imagens PNG/JPG
- Detecta candidatos a cifra via regex `_CHORD_RE` em nível de token
- Produz `bbox` por palavra com confiança Tesseract
- Fallback para extração de texto puro sem bbox quando `image_to_data` falha
- Cache CPP preservado (mesmo protocolo da auditoria 46)
- Contrato CPP integralmente preservado: nenhuma harmonia inferida, nenhuma letra inferida, nenhum alinhamento automático

**Ativação:** `OCR_ENGINE=tesseract` no `.env` (já configurado como padrão)

**Requisitos de sistema:**
```
pip install pytesseract PyMuPDF Pillow
apt install tesseract-ocr tesseract-ocr-por   # Linux
brew install tesseract                          # macOS
```

### 2. Roteamento em `backend/ocr_engine.py`

- Adicionado `TESSERACT_ENGINE = "tesseract"`
- `build_ocr_contract()` agora roteia para `ocr_engine_local` quando `OCR_ENGINE=tesseract`
- Google Vision continua disponível via `OCR_ENGINE=google_vision`
- Motores não reconhecidos retornam mensagem clara sugerindo `tesseract` ou `google_vision`

### 3. Health endpoint aprimorado (`backend/main.py`)

- Expõe `ocr_engine`, `ocr_engine_active`, `ocr_note` no `/health`
- Permite verificar via frontend qual motor está ativo antes de processar

### 4. Revisão em lote — painel `2C` (`src/modules/audit66-batch-ocr-review.js`)

- Grid de cards para todos os blocos OCR ordenados por página e posição Y
- Filtros: todos / somente cifras / somente letra / somente pendentes
- Seletor de compasso padrão para aplicar em lote com um clique
- Ações por card: Confirmar (associa a compasso) / ✕ Rejeitar / Lacuna
- Atualiza `protocol.measures[].approved_evidence` e `assignment` nos blocos de fusão
- Exporta JSON de auditoria com todas as ações da sessão
- Evento `cpp_protocol_updated` para integração com outros módulos
- Contrato CPP 100% preservado: toda ação é humana e explícita

---

## Arquivos modificados

| Arquivo | Tipo |
|---|---|
| `backend/ocr_engine_local.py` | NOVO |
| `backend/ocr_engine.py` | PATCH — roteamento + TESSERACT_ENGINE |
| `backend/main.py` | PATCH — health endpoint |
| `backend/requirements.txt` | PATCH — pytesseract, Pillow |
| `backend/.env` | PATCH — OCR_ENGINE=tesseract por padrão |
| `src/modules/audit66-batch-ocr-review.js` | NOVO |
| `index.html` | PATCH — painel 2C + script audit66 |

---

## Próximos passos sugeridos

- **Geometria assistida:** quando o usuário confirma um bloco via painel 2C, marcar a geometria do compasso como `status: "available"` se bbox estiver presente, desbloqueando o pipeline de associação automática
- **Pré-filtro de ruído:** adicionar lista de termos de ruído típicos de partitura (dinâmicas, articulações) para suprimir antes de exibir candidatos
- **Agrupamento por sistema:** agrupar cards por sistema/linha de partitura para contexto visual mais claro
