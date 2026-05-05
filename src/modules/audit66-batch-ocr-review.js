/**
 * audit66-batch-ocr-review.js
 * Mesa de revisão em lote OCR → Compasso
 * Build: audit-66-local-ocr
 *
 * Contrato CPP preservado:
 *   - Nenhuma associação automática
 *   - OCR bruto preservado
 *   - Toda ação requer confirmação humana explícita
 *   - Nenhuma harmonia inferida
 *   - Nenhuma letra inferida
 */

const AUDIT66_BUILD = 'audit-66-local-ocr';
const STORAGE_KEY = 'cpp_professional_omr_protocol_v1';

function byId(id) { return document.getElementById(id); }
function asArray(v) { return Array.isArray(v) ? v : []; }
function safeJson(raw, fb = {}) { try { return raw ? JSON.parse(raw) : fb; } catch { return fb; } }
function loadProtocol() { return safeJson(localStorage.getItem(STORAGE_KEY), {}); }
function saveProtocol(p) { localStorage.setItem(STORAGE_KEY, JSON.stringify(p || {})); }
function uid() { return `b66_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`; }
function now() { return new Date().toISOString(); }
function esc(v) { return String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;'); }

const CHORD_RE = /^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?\d*(?:\([^)]*\))?(?:\/[A-G](?:#|b)?)?$/;

function getBlocks(protocol) {
  const fusion = asArray(protocol?.fusion?.text_blocks_index);
  if (fusion.length) return fusion;
  return asArray(protocol?.ocr?.text_blocks).map((b, i) => ({
    ...b,
    fusion_id: b?.fusion_id || b?.id || `fx${String(i+1).padStart(4,'0')}`,
  }));
}

function getMeasures(protocol) {
  return asArray(protocol.measures).map((m, i) => ({
    id: m?.measure_id || m?.id || `m${String(i).padStart(3,'0')}`,
    label: `Compasso ${m?.number ?? m?.measure_number ?? i}`,
  }));
}

function blockKind(b) {
  if (b?.chord_candidate === true || b?.classification === 'possible_chord' || CHORD_RE.test(b?.text || '')) return 'chord';
  if (['possible_lyric','lyric_syllable_fragment','lyric_hyphen_or_continuation'].includes(b?.classification)) return 'lyric';
  return 'other';
}

function isAssigned(b) {
  return Boolean(b?.assignment?.measure_id && b?.assignment?.status === 'assigned_human_batch');
}

function sortBlocks(blocks) {
  return [...blocks].sort((a, b) => {
    const pa = a?.page || a?.page_number || 0;
    const pb = b?.page || b?.page_number || 0;
    if (pa !== pb) return pa - pb;
    const ya = a?.bbox?.vertices?.[0]?.y ?? 9999;
    const yb = b?.bbox?.vertices?.[0]?.y ?? 9999;
    return ya - yb;
  });
}

let _batchActions = [];

function buildCard(block, measures, filterKind) {
  const text = block?.text || block?.raw_text || block?.normalized_text || '';
  if (!text) return null;
  const kind = blockKind(block);
  if (filterKind === 'chord' && kind !== 'chord') return null;
  if (filterKind === 'lyric' && kind !== 'lyric') return null;
  const assigned = isAssigned(block);
  if (filterKind === 'pending' && assigned) return null;

  const id = block?.fusion_id || block?.id || uid();
  const page = block?.page || block?.page_number || null;
  const conf = block?.confidence != null ? `${Math.round(block.confidence * 100)}%` : '—';
  const kindLabel = kind === 'chord' ? '🎼 Cifra' : kind === 'lyric' ? '📝 Letra' : '📄 Texto';
  const pageLabel = page ? `p.${page}` : '';
  const yLabel = block?.bbox?.vertices?.[0]?.y != null ? `y:${block.bbox.vertices[0].y}` : '';
  const statusBadge = assigned
    ? `<span style="color:var(--green,#22c55e);font-size:0.75em;">✓ associado</span>`
    : `<span style="color:var(--yellow,#eab308);font-size:0.75em;">⏳ pendente</span>`;

  const measureOptions = measures.map(m =>
    `<option value="${esc(m.id)}">${esc(m.label)}</option>`
  ).join('');

  const card = document.createElement('div');
  card.style.cssText = `
    background:var(--card-bg,#1e293b);
    border:1px solid var(--border,#334155);
    border-radius:0.5rem;
    padding:0.6rem 0.7rem;
    display:flex;
    flex-direction:column;
    gap:0.35rem;
    font-size:0.85em;
  `;
  card.dataset.blockId = id;
  card.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:0.4rem;">
      <span style="font-weight:600;word-break:break-all;">${esc(text)}</span>
      ${statusBadge}
    </div>
    <div style="opacity:0.6;font-size:0.78em;">${kindLabel} ${pageLabel ? '· '+pageLabel : ''} ${yLabel ? '· '+yLabel : ''} · conf:${conf}</div>
    <select class="batch-measure-select" data-block-id="${esc(id)}" style="font-size:0.82em;padding:0.2rem 0.3rem;border-radius:0.3rem;">
      <option value="">— compasso —</option>
      ${measureOptions}
    </select>
    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
      <button class="batch-btn-confirm ok" data-block-id="${esc(id)}" data-kind="${esc(kind)}" data-text="${esc(text)}" style="font-size:0.78em;padding:0.2rem 0.5rem;flex:1;">Confirmar</button>
      <button class="batch-btn-reject warn" data-block-id="${esc(id)}" data-kind="${esc(kind)}" data-text="${esc(text)}" style="font-size:0.78em;padding:0.2rem 0.5rem;">✕</button>
      <button class="batch-btn-gap ghost" data-block-id="${esc(id)}" style="font-size:0.78em;padding:0.2rem 0.5rem;">Lacuna</button>
    </div>
  `;
  return card;
}

function renderBatch() {
  const protocol = loadProtocol();
  const grid = byId('batchCardGrid');
  const counter = byId('batchCounter');
  const defaultSelect = byId('batchDefaultMeasure');
  const filterKind = byId('batchFilterKind')?.value || 'all';
  if (!grid) return;

  const measures = getMeasures(protocol);
  const blocks = sortBlocks(getBlocks(protocol));

  // Populate default measure select
  if (defaultSelect) {
    const prev = defaultSelect.value;
    defaultSelect.innerHTML = '<option value="">— selecione —</option>' +
      measures.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');
    defaultSelect.value = prev;
  }

  grid.innerHTML = '';
  let shown = 0;
  blocks.forEach(block => {
    const card = buildCard(block, measures, filterKind);
    if (!card) return;
    shown++;
    grid.appendChild(card);
  });

  if (counter) counter.textContent = `${shown} bloco(s) exibido(s) de ${blocks.length} total`;
  if (shown === 0) grid.innerHTML = '<p style="opacity:0.5;padding:1rem;">Nenhum bloco OCR encontrado. Processe uma partitura primeiro.</p>';

  // Bind card buttons
  grid.querySelectorAll('.batch-btn-confirm').forEach(btn => {
    btn.onclick = () => applyBatchAction(btn.dataset.blockId, btn.dataset.kind, btn.dataset.text, 'confirm');
  });
  grid.querySelectorAll('.batch-btn-reject').forEach(btn => {
    btn.onclick = () => applyBatchAction(btn.dataset.blockId, btn.dataset.kind, btn.dataset.text, 'reject');
  });
  grid.querySelectorAll('.batch-btn-gap').forEach(btn => {
    btn.onclick = () => applyBatchAction(btn.dataset.blockId, null, null, 'gap');
  });

  // Fill measure selects with default if set
  const defaultMeasure = defaultSelect?.value || '';
  if (defaultMeasure) {
    grid.querySelectorAll('.batch-measure-select').forEach(sel => {
      if (!sel.value) sel.value = defaultMeasure;
    });
  }
}

function applyBatchAction(blockId, kind, text, action) {
  const protocol = loadProtocol();
  const measures = getMeasures(protocol);

  // Find selected measure for this card
  const cardSel = document.querySelector(`.batch-measure-select[data-block-id="${CSS.escape(blockId)}"]`);
  const measureId = cardSel?.value || byId('batchDefaultMeasure')?.value || '';

  protocol.review ||= [];
  protocol.assisted_review ||= {};
  protocol.assisted_review.audit_66 ||= { version: 'audit-66', created_at: now(), description: 'Revisão em lote OCR→Compasso.' };

  const review = {
    id: uid(),
    audit: 'audit-66',
    type: 'batch_ocr_review',
    timestamp: now(),
    source: 'human_batch_explicit',
    block_id: blockId,
    text: text || null,
    kind: kind || null,
    measure_id: measureId || null,
    action,
    effects: {
      modifies_protocol: true,
      modifies_ocr_raw_text: false,
      infers_lyrics: false,
      infers_harmony: false,
      aligns_ocr_to_measure_without_geometry: false,
      marks_playable_ready_automatically: false,
      applies_human_review_without_user_action: false,
    },
  };

  // Apply to fusion blocks or ocr blocks
  const fusionBlocks = asArray(protocol?.fusion?.text_blocks_index);
  const ocrBlocks = asArray(protocol?.ocr?.text_blocks);
  const targetArray = fusionBlocks.length ? fusionBlocks : ocrBlocks;

  const blockIdx = targetArray.findIndex(b =>
    (b?.fusion_id || b?.id) === blockId
  );

  if (action === 'confirm' && measureId) {
    if (blockIdx >= 0) {
      targetArray[blockIdx].assignment = {
        measure_id: measureId,
        status: 'assigned_human_batch',
        source: 'audit66_batch_human',
        assigned_at: now(),
        review_id: review.id,
      };
    }
    // Also apply to protocol.measures approved evidence
    const measure = asArray(protocol.measures).find((m, i) => {
      const mid = m?.measure_id || m?.id || `m${String(i).padStart(3,'0')}`;
      return mid === measureId;
    });
    if (measure) {
      measure.approved_evidence ||= {};
      if (kind === 'chord') {
        measure.approved_evidence.chords ||= [];
        measure.approved_evidence.chords.push({
          target_id: blockId,
          text: text,
          source: 'audit66_batch_human',
          review_id: review.id,
          confirmed_at: now(),
        });
      } else if (kind === 'lyric') {
        measure.approved_evidence.lyrics ||= [];
        measure.approved_evidence.lyrics.push({
          target_id: blockId,
          text: text,
          source: 'audit66_batch_human',
          review_id: review.id,
          confirmed_at: now(),
        });
      }
    }
    review.applied = true;
    review.reason = 'confirmed_by_human_batch';
  } else if (action === 'confirm' && !measureId) {
    setOutput(`⚠ Selecione um compasso antes de confirmar o bloco "${esc(text)}".`);
    return;
  } else if (action === 'reject') {
    if (blockIdx >= 0) {
      targetArray[blockIdx].assignment = {
        status: 'rejected_human_batch',
        source: 'audit66_batch_human',
        rejected_at: now(),
        review_id: review.id,
      };
    }
    protocol.rejected_candidates ||= [];
    protocol.rejected_candidates.push({
      target_id: blockId,
      text,
      kind,
      source: 'audit66_batch_human',
      review_id: review.id,
      rejected_at: now(),
    });
    review.applied = true;
    review.reason = 'rejected_by_human_batch';
  } else if (action === 'gap') {
    if (!measureId) {
      setOutput(`⚠ Selecione um compasso para marcar lacuna.`);
      return;
    }
    const measure = asArray(protocol.measures).find((m, i) => {
      const mid = m?.measure_id || m?.id || `m${String(i).padStart(3,'0')}`;
      return mid === measureId;
    });
    if (measure) {
      measure.lacunae ||= [];
      measure.lacunae.push({
        id: uid(),
        type: 'measure_needs_visual_review',
        source: 'audit66_batch_human',
        review_id: review.id,
        created_at: now(),
        note: 'lacuna marcada pela revisão em lote',
      });
    }
    review.applied = true;
    review.reason = 'gap_marked_by_human_batch';
  }

  if (fusionBlocks.length && protocol?.fusion?.text_blocks_index) {
    protocol.fusion.text_blocks_index = targetArray;
  } else if (protocol?.ocr?.text_blocks) {
    protocol.ocr.text_blocks = targetArray;
  }

  protocol.review.push(review);
  _batchActions.push(review);
  saveProtocol(protocol);
  setOutput(`✓ ${_batchActions.length} ação(ões) aplicada(s) nesta sessão. Última: ${action} — "${text || blockId}"`);
  renderBatch();
}

function setOutput(msg) {
  const el = byId('batchOutput');
  if (el) el.textContent = msg;
}

function exportBatchReview() {
  const protocol = loadProtocol();
  const batchReviews = asArray(protocol?.review).filter(r => r?.audit === 'audit-66');
  const payload = {
    export_type: 'cpp_batch_ocr_review',
    audit: 'audit-66',
    build: AUDIT66_BUILD,
    exported_at: now(),
    total_batch_actions: batchReviews.length,
    session_actions: _batchActions.length,
    reviews: batchReviews,
    safety_contract: {
      modifies_ocr_raw_text: false,
      infers_lyrics: false,
      infers_harmony: false,
      aligns_ocr_to_measure_without_geometry: false,
      marks_playable_ready_automatically: false,
    },
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `cpp_batch_review_audit66_${Date.now()}.json`;
  a.click();
}

function initBatchReview() {
  const refresh = byId('btnBatchRefresh');
  if (refresh) refresh.onclick = renderBatch;

  const exportBtn = byId('btnBatchExport');
  if (exportBtn) exportBtn.onclick = exportBatchReview;

  const filterSel = byId('batchFilterKind');
  if (filterSel) filterSel.onchange = renderBatch;

  const defaultSel = byId('batchDefaultMeasure');
  if (defaultSel) defaultSel.onchange = () => {
    const defaultMeasure = defaultSel.value;
    document.querySelectorAll('.batch-measure-select').forEach(sel => {
      if (!sel.value) sel.value = defaultMeasure;
    });
  };

  renderBatch();

  // Re-render after any protocol update from other modules
  window.addEventListener('cpp_protocol_updated', renderBatch);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBatchReview);
} else {
  initBatchReview();
}
