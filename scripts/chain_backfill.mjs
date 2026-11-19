// Read Paper Exchange state at historical HyperEVM blocks for the D1 outage window.
// Output JSONL rows hold raw on-chain integers (decimal strings) and the block's own timestamp.
// Nothing is interpolated: a failed sample is retried on the next run, never filled in.
//   node backfill.mjs <out.jsonl> [fromISO] [toISO] [stepMinutes]
import fs from 'node:fs';

const OUT = process.argv[2] ?? 'chain-backfill.jsonl';
const FROM = Date.parse(process.argv[3] ?? '2026-10-27T15:40:00Z') / 1000;
const TO = Date.parse(process.argv[4] ?? '2026-11-19T04:05:00Z') / 1000;
const STEP = Number(process.argv[5] ?? 5) * 60;
const RPCS = ['https://rpc.hyperlend.finance/archive', 'https://hyperliquid.drpc.org', 'https://rpc.purroofgroup.com'];
const EX = '0x6cd5661646289fb6e65ea5c032310fded797d0a2';
const IMPL_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc';
const SEL = { lp: '0x0cdd5fc4', tvl: '0x61d027b3', oi: '0xcb163b03' }; // oi(uint256 instrumentId)
const INSTRUMENTS = [0, 1]; // BTC, ETH
const word = n => n.toString(16).padStart(64, '0');

let id = 0, turn = 0;
async function batch(reqs) {
  for (let attempt = 0; attempt < 6; attempt++) {
    const url = RPCS[turn++ % RPCS.length];
    try {
      const body = reqs.map(([method, params]) => ({ jsonrpc: '2.0', id: ++id, method, params }));
      const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
      const j = await r.json();
      if (!Array.isArray(j)) throw new Error('non-batch response ' + JSON.stringify(j).slice(0, 120));
      const byId = new Map(j.map(x => [x.id, x]));
      const out = body.map(b => byId.get(b.id));
      if (out.some(x => !x || x.error)) throw new Error('item error ' + JSON.stringify(out.find(x => !x || x.error)).slice(0, 160));
      return out.map(x => x.result);
    } catch (e) { if (attempt === 5) throw e; await new Promise(r => setTimeout(r, 1000 * (attempt + 1))); }
  }
}

const done = new Set();
if (fs.existsSync(OUT)) for (const line of fs.readFileSync(OUT, 'utf8').split('\n')) if (line.trim()) done.add(JSON.parse(line).target);
const targets = []; for (let t = FROM; t <= TO; t += STEP) if (!done.has(t)) targets.push(t);
console.error('targets', targets.length, 'already', done.size);

const [head] = await batch([['eth_getBlockByNumber', ['latest', false]]]);
const hn = Number(head.number), ht = Number(head.timestamp);
const [ref] = await batch([['eth_getBlockByNumber', ['0x' + (hn - 2_000_000).toString(16), false]]]);
const rate = 2_000_000 / (ht - Number(ref.timestamp));
const guess = t => Math.round(hn - (ht - t) * rate);

const CHUNK = 8;
for (let i = 0; i < targets.length; i += CHUNK) {
  const group = targets.slice(i, i + CHUNK);
  // Two refinement passes so each sample's block lands within a few seconds of its target time.
  let blocks = group.map(guess);
  for (let pass = 0; pass < 2; pass++) {
    const ts = await batch(blocks.map(b => ['eth_getBlockByNumber', ['0x' + b.toString(16), false]]));
    blocks = blocks.map((b, k) => b + Math.round((group[k] - Number(ts[k].timestamp)) * rate));
  }
  const reqs = [];
  for (const b of blocks) {
    const tag = '0x' + b.toString(16);
    reqs.push(['eth_getBlockByNumber', [tag, false]], ['eth_getStorageAt', [EX, IMPL_SLOT, tag]],
      ['eth_call', [{ to: EX, data: SEL.lp }, tag]], ['eth_call', [{ to: EX, data: SEL.tvl }, tag]],
      ...INSTRUMENTS.map(n => ['eth_call', [{ to: EX, data: SEL.oi + word(n) }, tag]]));
  }
  const res = await batch(reqs);
  const per = 4 + INSTRUMENTS.length, lines = [];
  group.forEach((target, k) => {
    const r = res.slice(k * per, (k + 1) * per);
    const words = h => (h.slice(2).match(/.{64}/g) ?? []).map(w => BigInt('0x' + w).toString());
    lines.push(JSON.stringify({
      target, at: Number(r[0].timestamp) * 1000, block: Number(r[0].number), implementation: '0x' + r[1].slice(26),
      lpRaw: BigInt(r[2]).toString(), tvlRaw: BigInt(r[3]).toString(),
      instruments: INSTRUMENTS.map((n, j) => { const w = words(r[4 + j]); return { id: n, longOiRaw: w[0], shortOiRaw: w[1], longCapRaw: w[2], shortCapRaw: w[3], priceScaleRaw: w[4] }; }),
    }));
  });
  fs.appendFileSync(OUT, lines.join('\n') + '\n');
  if ((i / CHUNK) % 50 === 0) console.error(new Date(group[0] * 1000).toISOString(), i + group.length, '/', targets.length);
}
console.error('done');
