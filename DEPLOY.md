# Deploy — CPP Professional OMR

## Backend → Render.com

1. Suba o projeto para um repositório GitHub (público ou privado)
2. Acesse https://render.com → **New → Web Service**
3. Conecte o repositório
4. Render detecta o `render.yaml` automaticamente — clique em **Apply**
5. Aguarde o build (~3-5 min na primeira vez, inclui Tesseract)
6. Copie a URL gerada (ex: `https://cpp-pro-backend.onrender.com`)

> **Plano gratuito:** o serviço dorme após 15 min sem uso. O primeiro request após o sleep demora ~30s (cold start). Para uso contínuo, considere o plano Starter ($7/mês).

### Variáveis de ambiente no Render
Já definidas no `render.yaml`:
- `OCR_ENGINE=tesseract`
- `AUDIVERIS_CMD=audiveris`

Audiveris não estará disponível no Render (binário externo). O fluxo via MusicXML e OCR local (tesseract) funciona normalmente.

---

## Frontend → Vercel

### Opção A — Deploy via CLI
```bash
npm i -g vercel
cd cpp_pro_v2
vercel --prod
```

### Opção B — Deploy via painel
1. Acesse https://vercel.com → **New Project**
2. Importe o repositório GitHub
3. **Framework Preset:** Other
4. **Root Directory:** `/` (raiz do projeto)
5. Clique em **Deploy**

---

## Configurar URL do backend no frontend

Após o deploy do backend no Render, copie a URL e cole no campo
**"Backend OMR"** no topo do app antes de processar.

Ex: `https://cpp-pro-backend.onrender.com`

Essa URL fica salva no campo — não precisa digitar toda vez.

---

## Rodar localmente com Docker

```bash
docker compose up --build
# backend: http://localhost:8787
# frontend: python -m http.server 8080 (na raiz do projeto)
```
