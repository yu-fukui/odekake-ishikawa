#!/usr/bin/env bash
# 指定したファイルに変化があればコミットして push する。
# GitHub Actions の各ワークフローから呼ばれる。
#
#   ./scripts/commit_file.sh [対象ファイル] [コミットメッセージ]
#
# 引数を省略すると、投稿済み記録 (state/posted.json) を対象にする。
set -euo pipefail

TARGET="${1:-state/posted.json}"
MESSAGE="${2:-chore: 投稿済み記録を更新}"

if [ "${DRY_RUN:-false}" = "true" ]; then
  echo "dry-run のためコミットしません。"
  exit 0
fi

# まだ git に入っていない新しいファイルも拾うため、add してから index で比べる
# （2026-10-08 石川版で、初めての投稿の state/posted.json が「変更なし」と見なされ、記録が残らなかった）
git add -- "${TARGET}" 2>/dev/null || true
if git diff --cached --quiet -- "${TARGET}"; then
  echo "${TARGET} に変更なし。コミットはしません。"
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -m "${MESSAGE}" -- "${TARGET}"

# 画面や他のワークフローからの編集と衝突しうるので、rebase してから push する。
# 同じ実行で作った別のファイル（まだコミットしていないもの）があっても止まらないよう --autostash を付ける
for attempt in 1 2 3 4; do
  if git pull --rebase --autostash origin "${GITHUB_REF_NAME}" && git push; then
    exit 0
  fi
  wait=$((2 ** attempt))
  echo "push に失敗。${wait} 秒待って再試行します。"
  sleep "${wait}"
done
exit 1
