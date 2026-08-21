# GIT コミット・プッシュ時の注意事項

## 🔴 絶対にコミット・プッシュしてはいけないもの

### 1. シークレット情報（.env ファイル）
- **.env** は .gitignore で除外済み
- ただし **git 履歴に一度でもコミットされた場合**、.gitignore だけでは削除できない
- 万が一履歴に残っている場合は `git filter-branch` または `git lfs` で除去が必要

### 2. Slack Webhook URL
- `.env` に `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...` として保存
- **ソースコード（.py, .js, .html, .json, .txt, .md）にハードコードしない**
- 既にソースコード内に埋め込まれていないことをコミット前に確認

### 3. API キー全般
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `CLOUDFLARE_API_TOKEN`
- `SMTP_HOST`, `SENDER_EMAIL`, `SENDER_APP_PASSWORD`
- これらは全て `.env` に保存し、`.gitignore` で除外

## ✅ コミット前に確認すべきこと

```bash
# 1. ステージング対象の確認
git status

# 2. 差分のレビュー（シークレットが含まれていないか）
git diff --cached

# 3. .gitignore の確認
cat .gitignore
# 以下が含まれているべき:
#   .env
#   *.pyc
#   __pycache__/
#   data/eval_boxes_custom.json
```

## 🟢 コミットして良いもの

- ソースコード（.py, .js, .html, .css）
- 設定テンプレート（.env.example — 実際の値は抜く）
- ドキュメント（.md, .txt）
- ビルド成果物以外（node_modules/, dist/, *.o, *.hi）

## 💡 安全なコミット手順

```bash
# 1. .env がステージされていないことを確認
git status | grep .env
# （何も表示されれば OK）

# 2. Slack URL が diff に含まれていないことを確認
git diff --cached | grep -i "hooks.slack.com"
# （何も表示されれば OK）

# 3. コミット
git add <files>
git commit -m "message"

# 4. プッシュ
git push origin main
```

## 🆘 万が一コミットしてしまった場合

```bash
# 直近のコミットからシークレットを除去
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch .env' \
  --prune-empty --tag-name-filter cat -- --all

# 強制的にプッシュ（履歴を書き換えるため --force 必要）
git push origin main --force
```

---
**最終更新: 2026-08-17**
