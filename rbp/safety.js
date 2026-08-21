// rbp/safety.js — SafetyVector（安全制約ベクトル）算出
// data/pesticides.js（PESTICIDE_DB）に依存するアプリケーション固有ロジック。
// 散布記録(sprayHistory)から使用回数・直近散布・ローテーション状態を算出する。

function computeUsageState(sprayHistory, year) {
  const usage = {};
  for (const dateKey of Object.keys(sprayHistory)) {
    if (!dateKey.startsWith(String(year))) continue;
    const ids = sprayHistory[dateKey].pesticideIds || [];
    ids.forEach(id => { usage[id] = (usage[id] || 0) + 1; });
  }
  return usage;
}

function findLastSpray(sprayHistory, targetDateStr) {
  const targetTime = new Date(targetDateStr + 'T00:00:00').getTime();
  const keys = Object.keys(sprayHistory)
    .filter(k => (sprayHistory[k].pesticideIds || []).length > 0)
    .filter(k => new Date(k + 'T00:00:00').getTime() < targetTime)
    .sort((a, b) => new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00'));

  if (keys.length === 0) return null;

  const lastDate = keys[0];
  const pesticideIds = sprayHistory[lastDate].pesticideIds || [];
  const systems = pesticideIds
    .map(id => (PESTICIDE_DB.find(p => p.id === id) || {}).system)
    .filter(Boolean);

  return { date: lastDate, pesticideIds, systems };
}

function computeIntervalDays(lastSprayDate, targetDateStr) {
  if (!lastSprayDate) return null;
  const t1 = new Date(lastSprayDate + 'T00:00:00').getTime();
  const t2 = new Date(targetDateStr + 'T00:00:00').getTime();
  return Math.round((t2 - t1) / 86400000);
}

// 指定の散布日に使われた薬剤の系統コード集合を取り出す
function systemCodesUsedOn(dateKey, sprayHistory) {
  const ids = sprayHistory[dateKey].pesticideIds || [];
  return new Set(ids.map(id => (PESTICIDE_DB.find(p => p.id === id) || {}).systemCode).filter(Boolean));
}

// 各系統について「直近散布日から遡って、何回連続でその系統が使われ続けているか」
// （＝連続run長）を計算する。同一系統の連続使用は抵抗性発達リスクの指標になる。
// 一度でも使われなかった日を跨いだ系統は、そこでrunが途切れたものとして以降カウントしない。
function computeRotationState(sprayHistory, targetDateStr, lookbackCount = 5) {
  const targetTime = new Date(targetDateStr + 'T00:00:00').getTime();
  const keys = Object.keys(sprayHistory)
    .filter(k => (sprayHistory[k].pesticideIds || []).length > 0)
    .filter(k => new Date(k + 'T00:00:00').getTime() < targetTime)
    .sort((a, b) => new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00'))
    .slice(0, lookbackCount);

  if (keys.length === 0) return {};

  // 直近散布日（keys[0]）に使われた系統を、連続runの起点として1回とカウントする
  const [mostRecentKey, ...earlierKeys] = keys;
  const initialSystems = systemCodesUsedOn(mostRecentKey, sprayHistory);
  const initialState = {
    rotationState: Object.fromEntries([...initialSystems].map(s => [s, 1])),
    activeSystems: initialSystems, // まだrunが途切れていない系統の集合
  };

  // 直近から古い方向へ1日ずつ遡り、まだ連続している系統だけをrunに残しながら加算していく
  const { rotationState } = earlierKeys.reduce(({ rotationState, activeSystems }, dateKey) => {
    const systemsUsed = systemCodesUsedOn(dateKey, sprayHistory);
    const stillContinuing = new Set([...activeSystems].filter(s => systemsUsed.has(s)));
    const updatedState = { ...rotationState };
    stillContinuing.forEach(s => { updatedState[s] = (updatedState[s] || 0) + 1; });
    return { rotationState: updatedState, activeSystems: stillContinuing };
  }, initialState);

  return rotationState;
}

function buildSafetyVector(sprayHistory, targetDateStr) {
  const year = targetDateStr.slice(0, 4);
  const usageState = computeUsageState(sprayHistory, year);
  const last = findLastSpray(sprayHistory, targetDateStr);
  const lastSprayDate = last ? last.date : null;
  const intervalDays = computeIntervalDays(lastSprayDate, targetDateStr);
  const rotationState = computeRotationState(sprayHistory, targetDateStr);

  return {
    targetDate: targetDateStr,
    usageState,
    lastSprayDate,
    lastPesticideIds: last ? last.pesticideIds : [],
    lastSystems: last ? last.systems : [],
    intervalDays,
    rotationState,
  };
}
