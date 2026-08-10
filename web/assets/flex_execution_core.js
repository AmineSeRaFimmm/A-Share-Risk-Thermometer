(function initFlexExecutionCore(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.FlexExecutionCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildFlexExecutionCore() {
  const ONE_WAY_COST_RATE = 0.0001;
  const ETF_LOT_SIZE = 100;

  function buyOrderFromBudget(budget, price, cashAvailable) {
    const px = Number(price);
    const cap = Math.min(Number(budget) || 0, Number(cashAvailable) || 0);
    if (!(px > 0) || !(cap > 0)) return { qty: 0, gross: 0, fee: 0, cash_required: 0 };
    const qty = Math.floor(cap / (px * (1 + ONE_WAY_COST_RATE) * ETF_LOT_SIZE)) * ETF_LOT_SIZE;
    const gross = qty * px;
    const fee = gross * ONE_WAY_COST_RATE;
    return { qty, gross, fee, cash_required: gross + fee };
  }

  function sellQuantity(positionQty, price, { amount = null, pct = null } = {}) {
    const held = Number(positionQty) || 0;
    const px = Number(price);
    if (!(held > 0) || !(px > 0)) return 0;
    const requestedAmount = Number(amount);
    const requestedPct = Number(pct);
    if ((requestedAmount > 0 && requestedAmount >= held * px - 1e-6) || requestedPct >= 100) return held;
    const raw = requestedAmount > 0
      ? requestedAmount / px
      : held * Math.min(100, Math.max(0, requestedPct || 0)) / 100;
    return Math.floor(raw / ETF_LOT_SIZE) * ETF_LOT_SIZE;
  }

  function quoteTimestampIsUsable({ quoteEpochSeconds, nowMs, quoteYmd, todayYmd, phase }) {
    const epoch = Number(quoteEpochSeconds);
    if (!Number.isFinite(epoch) || !(epoch > 0) || quoteYmd !== todayYmd) return false;
    const ageMs = Number(nowMs) - epoch * 1000;
    if (!Number.isFinite(ageMs) || ageMs < -120000) return false;
    return phase !== 'intraday' || ageMs <= 180000;
  }

  function sortJournalNewestFirst(rows) {
    return (Array.isArray(rows) ? rows : []).slice().sort((a, b) =>
      String(b?.ts || b?.trade_date || '').localeCompare(String(a?.ts || a?.trade_date || ''))
    );
  }

  function firstPositivePrice(values) {
    for (const value of values || []) {
      const price = Number(value);
      if (Number.isFinite(price) && price > 0) return price;
    }
    return null;
  }

  function reductionInstruction(mode, amount, pct) {
    if (mode === 'amount') {
      const value = Number(amount);
      return { amount: value > 0 ? value : null, pct: null };
    }
    const value = Number(pct);
    return { amount: null, pct: value > 0 ? Math.min(100, value) : null };
  }

  function eodDecisionGate({ markDate, requiredDate, missing = 0, staleCount = 0 } = {}) {
    const mark = String(markDate || '').slice(0, 10);
    const required = String(requiredDate || '').slice(0, 10);
    if (!mark || Number(missing) > 0) return { ok: false, code: 'MISSING' };
    if (Number(staleCount) > 0 || (required && mark < required)) {
      return { ok: false, code: 'STALE' };
    }
    return { ok: true, code: 'OK' };
  }

  return {
    ONE_WAY_COST_RATE,
    ETF_LOT_SIZE,
    buyOrderFromBudget,
    sellQuantity,
    quoteTimestampIsUsable,
    sortJournalNewestFirst,
    firstPositivePrice,
    reductionInstruction,
    eodDecisionGate,
  };
}));
