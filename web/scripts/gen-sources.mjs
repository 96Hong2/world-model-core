// data/parsed/<source_id>.source.json 을 읽어 화면이 쓰는 자료 목록(src/lib/source-registry.ts)을 만든다.
//
// 서버 /ask 응답의 evidence 에는 source_id 만 있고 파일 이름·경로는 없다. 근거 서랍이
// "원본 위치"를 보여주려면 그 매핑이 화면 쪽에 있어야 해서 빌드 시점에 한 번 굽는다.
// 자료가 늘면 `npm run gen:sources` 를 다시 돌린다.
//
// data/ 는 읽기만 한다. 쓰는 곳은 web/ 안뿐이다.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { basename, dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');
const parsedDir = resolve(repoRoot, 'data', 'parsed');
const out = resolve(here, '..', 'src', 'lib', 'source-registry.ts');

const files = readdirSync(parsedDir)
  .filter((f) => f.endsWith('.source.json'))
  .sort();

// 슬랙 채널은 근거 위치에 ID 로만 적혀 있다(`slack:C087J33P9PT/1772062465.171759`).
// 화면에 `#C087J33P9PT` 를 그대로 내보내면 사람이 어느 채널인지 알 수 없다. 덤프 첫 줄에
// 채널 이름이 함께 있으므로 여기서 뽑아 굽는다.
const slackDir = resolve(repoRoot, 'data', 'sources', 'slack');
const channels = {};
let slackWorkspace = '';
try {
  for (const file of readdirSync(slackDir).filter((f) => f.endsWith('.jsonl'))) {
    const raw = readFileSync(resolve(slackDir, file), 'utf8');
    const first = raw.slice(0, raw.indexOf('\n'));
    const row = JSON.parse(first);
    if (row.channel_id && row.channel_name) channels[row.channel_id] = row.channel_name;
    // 워크스페이스 주소도 덤프 안에 있다. 짐작하지 않고 원문에서 읽는다.
    const found = /https:\/\/([a-z0-9-]+)\.slack\.com/.exec(raw);
    if (found && !slackWorkspace) slackWorkspace = found[1];
  }
} catch {
  // 슬랙 덤프가 없는 환경이면 매핑 없이 간다. 화면은 ID 를 그대로 보여준다.
}

if (files.length === 0) {
  console.error(`자료 메타를 찾지 못했습니다: ${parsedDir}`);
  process.exit(1);
}

const entries = files.map((file) => {
  const meta = JSON.parse(readFileSync(resolve(parsedDir, file), 'utf8'));
  const location = meta.canonical_location ?? '';
  const record = meta.source_of_record_for;
  return {
    source_id: meta.source_id,
    // 서버의 graphview.source_labels 도 basename 을 쓴다. 화면 두 곳의 자료 이름을 맞춘다.
    title: basename(location) || meta.source_id,
    source_type: meta.source_type ?? '',
    canonical_location: location,
    origin: meta.origin ?? '',
    source_of_record_for: Array.isArray(record) ? record.join(' · ') : (record ?? ''),
  };
});

const body = entries
  .map(
    (e) => `  ${JSON.stringify(e.source_id)}: {
    source_id: ${JSON.stringify(e.source_id)},
    title: ${JSON.stringify(e.title)},
    source_type: ${JSON.stringify(e.source_type)},
    canonical_location: ${JSON.stringify(e.canonical_location)},
    origin: ${JSON.stringify(e.origin)},
    source_of_record_for: ${JSON.stringify(e.source_of_record_for)},
  },`,
  )
  .join('\n');

const contents = `// 이 파일은 scripts/gen-sources.mjs 가 만든다. 손으로 고치지 않는다.
// 원본: data/parsed/<source_id>.source.json (자료 ${entries.length}종)
// 다시 만들려면: npm run gen:sources

export interface SourceInfo {
  source_id: string;
  /** 화면에 적는 자료 이름. 경로가 아니라 파일 이름만 쓴다. */
  title: string;
  source_type: string;
  canonical_location: string;
  origin: string;
  source_of_record_for: string;
}

export const SOURCE_REGISTRY: Record<string, SourceInfo> = {
${body}
};

/** 슬랙 채널 ID → 채널 이름. 근거 위치에 ID 만 적혀 있어 화면이 이걸로 풀어 쓴다. */
export const SLACK_CHANNELS: Record<string, string> = {
${Object.entries(channels)
  .map(([id, name]) => `  ${JSON.stringify(id)}: ${JSON.stringify(name)},`)
  .join('\n')}
};

/** 슬랙 워크스페이스 주소. 덤프의 permalink 에서 읽었다(짐작한 값이 아니다). */
export const SLACK_WORKSPACE = ${JSON.stringify(slackWorkspace)};
`;

writeFileSync(out, contents, 'utf8');
console.log(
  `자료 ${entries.length}종 · 슬랙 채널 ${Object.keys(channels).length}개` +
    `${slackWorkspace ? ` · 워크스페이스 ${slackWorkspace}` : ' · 워크스페이스 못 찾음'}` +
    ` 를 ${out} 에 적었습니다.`,
);
