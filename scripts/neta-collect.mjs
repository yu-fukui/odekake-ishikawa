/**
 * ネタ帳を自動で埋める（1リポジトリ＝1アカウント）
 * ------------------------------------------------------------------
 * 何をするか:
 *   neta/設定.json の「通す質問」でWeb検索し、出典URLの辿れる出来事だけを
 *   neta/ネタ帳.md の「書き足す場所」の下に追記して、コミットする。
 *   投稿文は書かない。事実と、括弧で「誰に何が起きるか」だけ。
 *
 * 動かし方:
 *   ANTHROPIC_API_KEY=xxx node scripts/neta-collect.mjs
 *   環境変数 DRY_RUN=1 を付けるとファイルを書かずに結果だけ出す。
 *
 * 何度実行しても壊れない:
 *   すでにネタ帳にある行（記号と空白のゆれを無視して同じもの）は入れない。
 *   新しい行が0件ならコミットもしない。
 *
 * 依存なし（Node 20以上の fetch をそのまま使う）。
 * ------------------------------------------------------------------
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { 巡回 } from './巡回.mjs';

const 設定パス = 'neta/設定.json';
const ネタ帳パス = 'neta/ネタ帳.md';
const 見出し = '## 書き足す場所';
const 使わない見出し = '## 使ってほしくないネタ';
const 調べもの見出し = '## 調べてほしいもの';

const APIキー = process.env.ANTHROPIC_API_KEY;
const 書かない = process.env.DRY_RUN === '1';

if (!APIキー) {
  console.error('ANTHROPIC_API_KEY がありません');
  process.exit(1);
}
if (!existsSync(設定パス)) {
  console.error(`${設定パス} がありません`);
  process.exit(1);
}

const 設定 = JSON.parse(readFileSync(設定パス, 'utf8'));
const 今日 = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Tokyo' }); // yyyy-mm-dd

const ネタ帳 = existsSync(ネタ帳パス) ? readFileSync(ネタ帳パス, 'utf8') : 初期ネタ帳();

// ---- 「使ってほしくないネタ」を読み取って、指示に混ぜる ----
const 使わない = 節を取り出す(ネタ帳, 使わない見出し)
  .split('\n')
  .map((s) => s.replace(/^[-*・]\s*/, '').trim())
  .filter((s) => s && !s.startsWith('（') && !s.startsWith('('));

// ---- 「調べてほしいもの」を読む（代表が手で書いた、店名だけのメモ） ----
const 調べもの = 節を取り出す(ネタ帳, 調べもの見出し)
  .split('\n')
  .map((s) => s.trim())
  .filter((s) => /^[-*・]\s*\S/.test(s))
  .map((s) => s.replace(/^[-*・]\s*/, '').trim())
  .filter((s) => s && !s.startsWith('（') && !s.startsWith('('));

// ---- すでに入っている行（重複よけ） ----
const 既存 = new Set(
  ネタ帳
    .split('\n')
    .map((s) => 整える(s))
    .filter(Boolean)
);

// ==================================================================
//  巡回先を直接見に行く（2026-09-21 代表指示）
// ==================================================================
const 巡回先パス = 'neta/巡回先.json';
let 候補一覧 = '（巡回先の設定がありません）';
let 候補件数 = 0;

if (existsSync(巡回先パス)) {
  const 巡回先 = JSON.parse(readFileSync(巡回先パス, 'utf8'));
  const { 候補, 取れた, 取れなかった, 捨てた } = await 巡回(巡回先);
  console.log(`巡回: ${取れた.length}件の巡回先から取得、${取れなかった.length}件が失敗、見出しで${捨てた ?? 0}件を除外`);
  for (const s of 取れなかった) console.log(`  取れず: ${s}`);

  // すでにネタ帳に URL がある候補は外す
  const 使い済みURL = new Set([...ネタ帳.matchAll(/https?:\/\/[^\s)）]+/g)].map((m) => m[0].replace(/[.,、。]+$/, '')));
  // 出典を一覧ページに固定した先（ふーぽ新店速報など）は、全部が同じURLになる。
  // URL で落とすと2回目以降まるごと消えるので、対象から外す。重複は行の文面で弾く。
  const 新しい = 候補.filter((c) => c.出典固定 || !使い済みURL.has(c.url));
  // 多すぎると AI の返しが途中で切れる（2026-10-08 石川版で130件）。上限を決めて、
  // 巡回先ごとに1件ずつ順に取って（新しい順）、どの巡回先も落とさないようにする。
  const 渡す上限 = 巡回先['AIに渡す最大件数'] ?? 80;
  const 残り = 順に取る(新しい, 渡す上限);
  候補件数 = 残り.length;
  console.log(`候補: ${候補.length}件 → ネタ帳に無いもの ${新しい.length}件 → AI に渡す ${残り.length}件（上限 ${渡す上限}）`);

  候補一覧 = 残り.length
    ? 残り.map((c) => `- [${c.名前}]${c.日付 ? ` ${c.日付}` : ''} ${c.タイトル}\n  ${c.url}`).join('\n')
    : '（今日は新しいものがありませんでした）';

  console.log(`候補一覧の分量: ${候補一覧.length} 文字`);

  // 巡回の動作確認だけしたいとき。API を叩かずに終わる。
  if (process.env.CRAWL_ONLY === '1') {
    console.log('--- CRAWL_ONLY なので、ここで終わります ---');
    console.log(候補一覧);
    出力('added', '0');
    process.exit(0);
  }
} else {
  console.log(`${巡回先パス} がないので、Web検索だけで集めます。`);
}

// ==================================================================
//  指示文
// ==================================================================
const 指示 = `あなたは編集部の情報収集担当です。**ネタ帳を埋めるのが仕事で、投稿文は書きません。**

## 通す質問

${設定.通す質問}

答えが出ない出来事は入れないでください。

## 集めるもの

${(設定.集めるもの ?? []).map((s) => `- ${s}`).join('\n')}

## 集めないもの

${(設定.集めないもの ?? []).map((s) => `- ${s}`).join('\n')}
- 自社の作業手順、社内の習慣、お客様に言われたこと（実測で表示6〜15。読まれません）
- 出典が辿れない「〜だそうです」
- 災害・事件・病気など、人の被害が絡む出来事
- クライアントの未公開情報
${使わない.length ? `\n## このネタ帳で使ってほしくないもの\n\n${使わない.map((s) => `- ${s}`).join('\n')}` : ''}

${調べもの.length ? `## 調べてほしいもの（代表が手で書いたメモ。**いちばん優先**）

下は、代表が見かけて気になったものです。店名だけ、うろ覚えのこともあります。

${調べもの.map((s) => `- ${s}`).join('\n')}

**この1件ずつをWeb検索して、実在と、場所・開店時期を確かめてください。**
見つかったら、ふつうの1行にしてネタ帳に入れてください。出典は、
**店や施設の公式（公式サイト・公式Instagram・公式Facebook）を第一に。**
公式が見つからないときは、報道や地域メディアの記事でもかまいません。

確かめられなかったものは、**入れないでください。** 翌日また探します。
返しの JSON の \`調べて分かったもの\` には、**メモの文と、それを元に items へ入れた行の両方**を
入れてください。**items に入れていないものは、ここに書かないでください。**
入れた行が一致しないとメモは消えず、翌日また探します。

` : ''}## 新店の扱い

${設定['新店の扱い'] ?? '新店も集める。店名・場所・開店時期を書く。'}

新店は、催しと違って「いつ行ってもよい」話です。会期がないので、日付で外さないでください。
店名と市町名が分かれば入れてください。

**候補一覧の [URALA オープン情報] と [ふーぽ 新店速報] は新店です。**
ここに使えるものがあるのに、催しだけで ${設定.件数 ?? '3〜5'} 件を埋めてしまうことがありました。
**新店を1件以上は必ず入れてください。**

## 書き方

1件1行、事実だけ。括弧で「誰に何が起きるか」を一言。例：

- 9/19-20 鯖江でめがねのお祭り。産地の工場が開く（→ ふだん見えない工程を見せる日）
- 県内に お堀の風景が見えるレストランができた（→ 立地そのものが説明になっている）

括弧が思いつかなければ空でかまいません。**入れないより入れるほうがよいです。**
感想や書き出し案は付けないでください（それは制作担当の仕事です）。

### 出典にあることだけを書く（2026-10-08 運用部・石川担当の点検で見つかった）

**行の本文（「（→」より前）は、出典のページに書かれていることだけで書いてください。**
「入場無料」「◯年ぶり」「朝から夜まで」「人気店の味を食べ比べ」のような言葉は、
出典のページにその言葉（同じ意味の文）があるときだけ書きます。店名や催し名から推して足さないこと。
実際に、出典に無い「入場無料」「7年ぶりに一斉スタートに戻る」がネタ帳に入り、そのまま投稿文になりました。

**括弧「（→ ……）」は、出典にある事実から言える見立ての一言です。** 新しい事実（数字・時間・料金・
「◯◯できる」）を括弧の中で足さないでください。括弧の中身は、投稿文では事実として使われないことがあります。

## 公式Instagramも探す（2026-10-01 代表指示「インスタリンクは積極的に載せたい。出典元とインスタの両方が望ましい」）

1件ごとに、**その店・施設・主催の公式Instagramアカウント**を検索で探してください。
見つかったら instagram に**アカウントのURL**（https://www.instagram.com/アカウント名/ の形）を入れる。

- 検索で実在を確かめたものだけ。**アカウント名を推測で作らない**
- 投稿（/p/…）ではなく、アカウントのトップのURLにする
- 見つからなければ空でよい（入れないより、間違ったものを入れるほうが悪い）
- source は記事や公式サイトを優先。インスタしか無いときは source にインスタを入れてもよい

## 越えてはいけない線

- 投稿文を書かない。集めた事実を置くだけ
- **確認できない情報を入れない。出典URLが辿れるものだけ**
- ネタ帳にすでにある出来事を、言い換えて入れ直さない

## すでにネタ帳に入っているもの（これと同じ出来事は入れない）

${[...既存].filter((s) => s.startsWith('- ') && !/^- https?:\/\//.test(s)).slice(0, 120).join('\n') || '（まだありません）'}

## 今日の候補（巡回先から自動で取ってきたもの。ここから選ぶのが本線）

下は、福井の市町・観光協会・商業施設・道の駅のRSSと、ふーぽのイベント一覧から
今朝そのまま取ってきた見出しです。**すでにネタ帳にあるURLは除いてあります。**

**まず、この中から選んでください。** 見出しだけでは中身が分からないものは、
Web検索でそのURLを開いて確かめてから書いてください。

## そのあと、かならず検索でも探してください（2026-09-22 代表指示・二本立て）

**巡回先だけでは落ちる出来事があります。** 巡回先は 県・市町・観光協会・商業施設・道の駅・
ポータルに限られていて、**企業やお店が自分のサイトだけで告知したもの**は、どこにも載りません。

実際に落ちました。トヨタカローラ福井の複合施設「C-Base Fukui」（2026-09-19 坂井市春江町に
グランドオープン）は、一次情報がディーラーの自社サイトだったため巡回では拾えず、
**検索だけで集めている個人アカウント側は拾えていました。**

**漏れをなくすことを、重複を避けることより優先します。**

- 上の候補から選んだあと、**検索でも別に探して、最低2件は検索から入れてください**
- 探す言葉の例：「福井 オープン 新施設」「福井県 グランドオープン ◯月」
  「福井 リニューアル 開業」「坂井市 新しい店」など、**市町名を変えて何度か**
- 巡回先に無い情報源（福井新聞、企業やお店の公式サイト、施設の公式 Instagram の告知ページ）でも、
  **出典URLが辿れるならかまいません**
- 候補一覧と内容が重なったら、**重なったほうを捨てて構いません**。
  ただし「巡回で拾えたから検索はしない」はしないこと
${(設定['見落としやすい催し'] ?? []).length ? `
## 見落としやすい催し（巡回先に載らない。毎回ここを確認する）

**主催が民間の実行委員会の催しは、市も観光協会も自分の発表としては出しません。**
そのため巡回先41本のどこにも現れず、実際に RENEW・さばえまつり・日野川大花火を落としました。
企業の自社サイト告知（上の C-Base Fukui）とは別の穴です。

${(設定['見落としやすい催し'] ?? []).map((s) => `- ${s}`).join('\n')}

**公式サイトか公式SNSを検索して、今年の日程を確かめてください。**
開催まで2か月以内のものがあれば、ネタ帳に入れてください。
すでにネタ帳にあるものは入れ直さなくてかまいません。月の表記は目安で、年によって変わります。
` : ''}
${候補一覧}

## 今日

${今日}（日本時間）。集めるのは月曜と木曜の週2回なので、前回の収集から後に出たもの（3〜4日分）を中心に、当日以降に使えるものを ${設定.件数 ?? '3〜5'} 件。
**うち最低2件は、巡回の候補一覧に無いもの（検索で見つけたもの）にしてください。**
**source には、上の候補に載っている URL をそのまま使ってください。** URLを作らない・推測しない。
候補に無いものを足すときは、検索で実在を確かめた URL を使うこと。

## 返し方

最後に、次の形の JSON だけを \`\`\`json のコードブロックで出してください。
説明は要りません。source は必ず実在する、辿れるURLにしてください。

\`\`\`json
{"items":[{"line":"- 本文（→ 誰に何が起きるか）","source":"https://…","instagram":"https://www.instagram.com/…/（無ければ空）"}],"調べて分かったもの":[{"メモ":"元のメモの文","入れた行":"- items に入れた行とまったく同じ文"}]}
\`\`\``;

// ==================================================================
//  API を叩く
// ==================================================================
// 石川版などでは、福井だけの言い回しを region.json の「言いかえ」で置きかえる（福井版は空なので変わらない）
const 地域 = existsSync('region.json') ? JSON.parse(readFileSync('region.json', 'utf8')) : {};
const 地域の指示 = (地域['言いかえ'] ?? []).reduce((文, [元, 先]) => 文.split(元).join(先), 指示);
const 本文 = await claudeに聞く(地域の指示);
const 取れた = JSONを取り出す(本文);

if (!取れた || !Array.isArray(取れた.items)) {
  // 本文が空・JSONなし ＝ 集められなかった。9/16〜10/8 の福井では、0件の日はすべてこれか API の失敗で、
  // 「新しいものが無かった」日は無かった。黄色の警告では気づけなかったので、失敗（赤）にする（2026-10-08）。
  console.log('::error::JSON を取り出せませんでした（本文が空か、形式が違う）。今日は1件も入れていません');
  console.log('返ってきた本文の先頭:');
  console.log(本文.slice(0, 1500) || '（空）');
  出力('added', '0');
  process.exit(1);
}

// ==================================================================
//  重複をよけて、追記する
// ==================================================================
const 入れる = [];
const とばした = [];

for (const item of 取れた.items) {
  const line = 整形(item?.line);
  const source = String(item?.source ?? '').trim();
  if (!line) continue;
  if (!/^https?:\/\//.test(source)) {
    とばした.push(`${line}（出典URLが無い）`);
    continue;
  }
  if (既存.has(整える(line)) || 既存.has(整える(印を取る(line)))) {
    とばした.push(`${line}（すでにある）`);
    continue;
  }
  既存.add(整える(line));
  // 公式Instagram（アカウントのトップだけ受け付ける）。2026-10-01 代表指示
  const ig = String(item?.instagram ?? '').trim().replace(/\?.*$/, '');
  const インスタ = /^https:\/\/(www\.)?instagram\.com\/[A-Za-z0-9._]+\/?$/.test(ig) && !/instagram\.com\/(p|reel|explore|stories)\//.test(ig)
    ? ig.replace(/\/?$/, '/') : '';
  入れる.push({ line: インスタ && インスタ !== source ? `${line} ［Instagram: ${インスタ}］` : line, source });
}

console.log(`集まった: ${取れた.items.length}件 ／ 入れる: ${入れる.length}件 ／ とばした: ${とばした.length}件`);
for (const s of とばした) console.log(`  とばした: ${s}`);

if (入れる.length === 0) {
  console.log('新しい行がないので、ファイルは変更しません。');
  出力('added', '0');
  process.exit(0);
}

const 塊 = [
  `### ${今日}`,
  '',
  ...入れる.map((x) => x.line),
  '',
  '<details><summary>出典</summary>',
  '',
  ...入れる.map((x) => `- ${x.source}`),
  '',
  '</details>',
  ''
].join('\n');

let 新しいネタ帳 = 見出しの下に入れる(ネタ帳, 見出し, 塊);

// ---- 調べて分かったメモを「調べてほしいもの」欄から消す ----
// 「分かった」と言われただけでは消さない。**実際にネタ帳へ入った行があるときだけ**消す。
// 消したのに入っていない、という取りこぼしが起きたため（2026-09-23 RENEW）。
const 入れた行 = new Set(入れる.map((x) => 整える(x.line)));
const 分かった = (取れた['調べて分かったもの'] ?? [])
  .map((x) => (typeof x === 'string' ? { メモ: x, 入れた行: '' } : x ?? {}))
  .map((x) => ({
    メモ: String(x['メモ'] ?? '').replace(/^[-*・]\s*/, '').trim(),
    行: 整える(String(x['入れた行'] ?? ''))
  }))
  .filter((x) => x.メモ && 調べもの.some((m) => 整える(m) === 整える(x.メモ)))
  .filter((x) => {
    if (x.行 && 入れた行.has(x.行)) return true;
    console.log(`  消しません（ネタ帳に入っていない）: ${x.メモ}`);
    return false;
  })
  .map((x) => x.メモ);

if (分かった.length) {
  新しいネタ帳 = メモを消す(新しいネタ帳, 調べもの見出し, 分かった);
  console.log(`調べてほしいもの: ${分かった.length}件が分かったので欄から消します`);
  for (const s of 分かった) console.log(`  解決: ${s}`);
}
const 残ったメモ = 調べもの.length - 分かった.length;
if (調べもの.length) console.log(`調べてほしいもの: ${調べもの.length}件のうち ${残ったメモ}件はまだ分からず、欄に残します`);

if (書かない) {
  console.log('--- DRY_RUN なので書きません。入るのはこれです ---');
  console.log(塊);
} else {
  writeFileSync(ネタ帳パス, 新しいネタ帳, 'utf8');
  console.log(`${ネタ帳パス} に ${入れる.length}件を追記しました。`);
}
出力('added', String(入れる.length));


// ==================================================================
//  小物
// ==================================================================

async function claudeに聞く(prompt) {
  // web_search（サーバー側ツール）は途中で stop_reason: "pause_turn" を返して
  // 「続きを頼む」ことがある。その場合は返ってきた content をそのまま assistant として
  // 積み、同じ会話を送り直す（最大3回）。これをしないと本文が空のまま返ってくる。
  const messages = [{ role: 'user', content: prompt }];
  let data = null;
  let 検索した = 0, 入力 = 0, 出力t = 0;
  for (let 回 = 0; 回 < 4; 回++) {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': APIキー,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 設定.モデル ?? 'claude-sonnet-5',
        // 候補が多い日は 8000 では返しの JSON の途中で切れた（2026-10-08 石川版、候補130件）
        max_tokens: 16000,
        tools: [{ type: 'web_search_20250305', name: 'web_search', max_uses: 設定.検索回数 ?? 8 }],
        messages
      })
    });

    if (!res.ok) {
      throw new Error(`Anthropic API が ${res.status} を返しました: ${(await res.text()).slice(0, 500)}`);
    }
    data = await res.json();
    console.log(`API 応答: stop_reason=${data.stop_reason} / content=${(data.content ?? []).length}ブロック（${回 + 1}回目）`);
    const u = data.usage ?? {};
    検索した += u.server_tool_use?.web_search_requests ?? 0;
    入力 += u.input_tokens ?? 0;
    出力t += u.output_tokens ?? 0;
    if (data.stop_reason !== 'pause_turn') break;
    messages.push({ role: 'assistant', content: data.content });
  }
  // 実際にいくら使ったかを毎回残す。見積りでなく実測で判断するため（続き呼び出しぶんも合算）。
  // Sonnet 5: 入力 $2/Mtok、出力 $10/Mtok、Web検索 $10/1000回（2026-09 時点）
  const 概算 = (入力 / 1e6) * 2 + (出力t / 1e6) * 10 + 検索した * 0.01;
  console.log(
    `使った分: 入力 ${入力} tok ／ 出力 ${出力t} tok ／ Web検索 ${検索した} 回` +
      `（上限 ${設定.検索回数 ?? 8}）／ 概算 $${概算.toFixed(4)}`
  );
  if (data?.stop_reason === 'max_tokens') {
    console.log('::warning::max_tokens で切れました。設定.検索回数 を減らすか max_tokens を上げてください');
  }
  return (data?.content ?? [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('\n');
}

function 順に取る(候補たち, 上限) {
  if (候補たち.length <= 上限) return 候補たち;
  const 先ごと = new Map();
  for (const c of 候補たち) {
    if (!先ごと.has(c.名前)) 先ごと.set(c.名前, []);
    先ごと.get(c.名前).push(c);
  }
  const 列 = [...先ごと.values()];
  const 出 = [];
  for (let i = 0; 出.length < 上限; i++) {
    let 足した = false;
    for (const l of 列) {
      if (i < l.length && 出.length < 上限) { 出.push(l[i]); 足した = true; }
    }
    if (!足した) break;
  }
  return 出;
}

function JSONを取り出す(text) {
  const m = text.match(/```json\s*([\s\S]*?)```/);
  const 素 = m ? m[1] : text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1);
  try {
    return JSON.parse(素);
  } catch {
    // 途中で切れたとき（max_tokens）は、items の中で閉じている {…} だけを拾う（2026-10-08）
    const 拾った = 途中までのitems(text);
    if (拾った.length) {
      console.log(`::warning::返しの JSON が途中で切れていました。閉じている ${拾った.length} 件だけを使います`);
      return { items: 拾った, 調べて分かったもの: [] };
    }
    return null;
  }
}

export function 途中までのitems(text) {
  const i = text.indexOf('"items"');
  if (i === -1) return [];
  const 始 = text.indexOf('[', i);
  if (始 === -1) return [];
  const 出 = [];
  let 深さ = 0, 頭 = -1, 文字列 = false, 逃がし = false;
  for (let k = 始 + 1; k < text.length; k++) {
    const c = text[k];
    if (文字列) {
      if (逃がし) 逃がし = false;
      else if (c === '\\') 逃がし = true;
      else if (c === '"') 文字列 = false;
      continue;
    }
    if (c === '"') { 文字列 = true; continue; }
    if (c === '{') { if (深さ === 0) 頭 = k; 深さ++; }
    else if (c === '}') {
      深さ--;
      if (深さ === 0 && 頭 !== -1) {
        try {
          const o = JSON.parse(text.slice(頭, k + 1));
          if (o && typeof o.line === 'string') 出.push(o);
        } catch { /* 壊れたものは捨てる */ }
        頭 = -1;
      }
    } else if (c === ']' && 深さ === 0) break;
  }
  return 出;
}

function 節を取り出す(md, 見出し文字) {
  const i = md.indexOf(見出し文字);
  if (i === -1) return '';
  const 後ろ = md.slice(i + 見出し文字.length);
  const j = 後ろ.search(/\n##\s/);
  return j === -1 ? 後ろ : 後ろ.slice(0, j);
}

/** 「調べてほしいもの」の節から、分かった行だけを消す。ほかの節には触らない。 */
function メモを消す(md, 見出し文字, 消す行) {
  const i = md.indexOf(見出し文字);
  if (i === -1) return md;
  const 節の頭 = md.indexOf('\n', i) + 1;
  const 次 = md.slice(節の頭).search(/\n##\s/);
  const 節の尾 = 次 === -1 ? md.length : 節の頭 + 次 + 1;
  const 前 = md.slice(0, 節の頭);
  const 節 = md.slice(節の頭, 節の尾);
  const 後 = md.slice(節の尾);
  const 消す = new Set(消す行.map((s) => 整える(s)));
  const 残り = 節.split('\n').filter((行) => {
    const 素 = 整える(行.replace(/^[-*・]\s*/, ''));
    return !(素 && 消す.has(素));
  });
  return 前 + 残り.join('\n') + 後;
}

function 見出しの下に入れる(md, 見出し文字, 塊) {
  const i = md.indexOf(見出し文字);
  if (i === -1) {
    // 見出しが無いときは、黙って別の場所に入れず、末尾に見出しごと作る
    return `${md.trimEnd()}\n\n${見出し文字}\n\n${塊}`;
  }
  const 改行 = md.indexOf('\n', i);
  const 前 = md.slice(0, 改行 + 1);
  const 後 = md.slice(改行 + 1);
  return `${前}\n${塊}${後.startsWith('\n') ? 後 : '\n' + 後}`;
}

function 整形(s) {
  const t = 引用の印を取る(String(s ?? '')).replace(/\r/g, '').trim();
  if (!t) return '';
  return t.startsWith('- ') ? t : `- ${印を取る(t)}`;
}

// Web検索の引用の印（<cite index="61-3">…</cite>）が行に混ざることがある（2026-10-08 石川版）。
// 「(cite index=…>」のように頭の < が欠けた形もあったので、それも外す。
export function 引用の印を取る(s) {
  return String(s)
    .replace(/<\/\s*cite\s*>/gi, '')
    .replace(/&lt;\/\s*cite\s*&gt;/gi, '')
    .replace(/&lt;cite\s+index\s*=\s*"[^"]*"\s*&gt;/gi, '')
    .replace(/[(<（]?\s*cite\s+index\s*=\s*"[^"]*"\s*(?:>|&gt;)/gi, '')
    .replace(/[ \t]{2,}/g, ' ');
}

function 印を取る(s) {
  return String(s).replace(/^[-*・•]\s*/, '');
}

function 整える(s) {
  return String(s ?? '')
    .replace(/\s*［Instagram:[^］]*］/g, '')   // 同じ出来事かどうかは、インスタの印を外して比べる
    .replace(/ /g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function 出力(key, value) {
  if (process.env.GITHUB_OUTPUT) {
    writeFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`, { flag: 'a' });
  }
}

function 初期ネタ帳() {
  return `# ネタ帳\n\n${調べもの見出し}\n\n（店名だけ、うろ覚えでもここに書けば、翌朝の収集が調べて本体に入れます）\n\n${使わない見出し}\n\n（ここに書いたものは集めません）\n\n${見出し}\n`;
}
