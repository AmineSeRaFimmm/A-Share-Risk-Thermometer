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

  function normalizeTradeDate(value) {
    const match = String(value || '').match(/^(\d{4}-\d{2}-\d{2})/);
    return match ? match[1] : '';
  }

  function validateTradeDate({ tradeDate, sessionDate, calendar = [], notBefore = null } = {}) {
    const day = normalizeTradeDate(tradeDate);
    const session = normalizeTradeDate(sessionDate);
    const floor = normalizeTradeDate(notBefore);
    if (!day) return { ok: false, code: 'INVALID' };
    if (session && day > session) return { ok: false, code: 'FUTURE' };
    if (floor && day < floor) return { ok: false, code: 'BEFORE_EXECUTION' };
    const sessions = new Set((calendar || []).map(normalizeTradeDate).filter(Boolean));
    if (sessions.size && !sessions.has(day)) return { ok: false, code: 'NON_TRADING_DAY' };
    return { ok: true, code: 'OK' };
  }

  function freshQuote(snapshot, code, { active, nowMs, todayYmd, maxAgeMs } = {}) {
    if (!active) return null;
    const fetchedAt = Number(snapshot?.fetchedAt);
    const age = Number(nowMs) - fetchedAt;
    if (!Number.isFinite(fetchedAt) || !Number.isFinite(age) || age < 0 || age > Number(maxAgeMs)) return null;
    const normalizedCode = String(code || '').replace(/\D/g, '').padStart(6, '0');
    const quote = snapshot?.quotes?.[normalizedCode];
    if (!(Number(quote?.price) > 0) || normalizeTradeDate(quote?.quote_date) !== normalizeTradeDate(todayYmd)) return null;
    return quote;
  }

  function openExecutionLabel({ lag, tail = false } = {}) {
    if (tail && Number(lag) === 0) return '14:50尾盘买';
    if (Number(lag) === 1) return 'T+1可确认';
    if (Number(lag) === 0) return '待T+1开盘';
    return '窗口外';
  }

  function satelliteCloseLabel({ closeCode = '', phase = 'pending', genericClosing = false } = {}) {
    const code = String(closeCode || '').toUpperCase();
    const suffix = phase === 'executed' ? '已执行' : phase === 'real_pending' ? '待平' : '待执行';
    if (code === 'LOCAL_STOP_LOSS') return `止损${suffix}`;
    if (code === 'LOCAL_TAKE_PROFIT') return `止盈${suffix}`;
    return genericClosing ? (phase === 'executed' ? '策略已平' : '策略待平') : '';
  }

  function satelliteHistoryStartDate(positions, journal, explicitBasisDate = null) {
    const satellites = (positions || []).filter(pos => String(pos?.sleeve || '').toLowerCase() === 'satellite');
    if (!satellites.length) return '';
    const identity = new Set();
    const entryDates = [];
    for (const pos of satellites) {
      for (const value of [pos?.key, pos?.name, pos?.etf_code]) {
        const normalized = String(value || '').trim();
        if (normalized) identity.add(normalized);
      }
      const entry = normalizeTradeDate(pos?.entry_bar_date || pos?.buy_date);
      if (entry) entryDates.push(entry);
    }
    const earliestEntry = entryDates.slice().sort()[0] || '';
    const candidates = [...entryDates, normalizeTradeDate(explicitBasisDate)].filter(Boolean);
    const mutationTypes = new Set(['BUY', 'OPEN', 'ADD', 'REDUCE', 'CLOSE', 'SYNC']);
    for (const row of journal || []) {
      if (!mutationTypes.has(String(row?.type || '').toUpperCase())) continue;
      const day = normalizeTradeDate(row?.trade_date || row?.ts);
      if (!day || (earliestEntry && day < earliestEntry)) continue;
      const sleeve = String(row?.sleeve || '').toLowerCase();
      const rowValues = [row?.key, row?.name, row?.etf_code].map(value => String(value || '').trim()).filter(Boolean);
      const matchesCurrent = rowValues.some(value => identity.has(value));
      const clearlyCore = sleeve === 'core'
        || rowValues.includes('510300')
        || rowValues.some(value => value.includes('沪深300'));
      if (sleeve === 'satellite' || matchesCurrent || (!clearlyCore && rowValues.length)) candidates.push(day);
    }
    return candidates.sort().at(-1) || '';
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
    normalizeTradeDate,
    validateTradeDate,
    freshQuote,
    openExecutionLabel,
    satelliteCloseLabel,
    satelliteHistoryStartDate,
  };
}));
