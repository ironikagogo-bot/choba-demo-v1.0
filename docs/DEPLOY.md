# 帳場を「Macなし」で動かす — Render 公開手順（保存版）

このフォルダ（`chouba-deploy`）を Render に載せると、`https://xxxx.onrender.com` のURLになり、
**Macを閉じても、どの端末からでも**帳場を開けます。人に見せる・外で使う用。

---

## 先に知っておくこと（1分）
- このクラウド版は**ダミー顧客が最初から入った「見せる用」**（`CHOUBA_DEMO=1`）。**実顧客データは載せない**。
- **AI下書きを本物で動かす**には、あとで環境変数 `ANTHROPIC_API_KEY` を入れる（未設定でもテンプレ下書きで動く）。
- 無料枠は**15分さわらないと眠り、次に開くと起動に30〜50秒**かかる（仕様は変わるので要確認）。**人に見せる直前に一度開いて温めておく**と待たされない。
- 使うもの：無料のGitHubアカウントと無料のRenderアカウント（どちらもクレジットカード不要）。

---

## ステップ1：コードをGitHubに置く（ブラウザだけ・gitコマンド不要）

1. 私が渡した `chouba-deploy.zip` をダブルクリックして解凍 → `chouba-deploy` フォルダができる。
2. https://github.com/join で無料登録。
3. 右上の「＋」→「**New repository**」。
   - Repository name：例 `chouba`。**Private** を選んでよい。「Create repository」。
4. 次の画面の「**uploading an existing file**」（青いリンク）をクリック。
5. `chouba-deploy` フォルダを開き、**中身をすべて**（`app` フォルダ・`Dockerfile`・`requirements.txt` など）まとめてドラッグしてページに落とす。
   - ※ `app` フォルダごとドラッグすればOK。中身の階層は保たれる。
6. 下の「**Commit changes**」を押す。アップロード完了。

---

## ステップ2：Renderで公開する

7. https://render.com にアクセス →「Get Started」→「**GitHub**でサインイン」（連携を許可）。
8. ダッシュボードで「**New +**」→「**Web Service**」。
9. さっきの `chouba` リポジトリを選び「**Connect**」。
   - Renderが `Dockerfile` を自動で見つける（Language欄が Docker になる）。
10. 設定はほぼ既定でOK：
    - Name：`chouba`（好きな名前）
    - Region：`Singapore`（日本から近い）
    - Instance Type：**Free** を選ぶ
11. 「**Create Web Service**」。ビルドが始まる（数分）。ログが緑になり `Live` になれば完成。
12. 画面上部の `https://chouba-xxxx.onrender.com` があなたの公開URL。開くと新UIが出る。

---

## ステップ3：本物のAI下書きにする（任意・あとでOK）

13. Anthropicのキーを取る：https://console.anthropic.com →「API Keys」→「Create Key」→ `sk-ant-…` をコピー。
14. Renderのサービス画面 → 左メニュー「**Environment**」→「**Add Environment Variable**」。
    - Key：`ANTHROPIC_API_KEY`　Value：さっきのキー。「Save Changes」。
15. 自動で再デプロイされる。以後、下書きが本物の生成になる（ヘッダーの「テンプレ下書き」表示が消える）。
    - ※ キーは他人に見せない。ブラウザには置かず、この環境変数（サーバーの秘密）として入れる。

---

## コードを直したいとき（更新のしかた）
GitHubのリポジトリで該当ファイルを差し替え（Upload filesで上書き）してCommitすると、**Renderが自動で再デプロイ**する。私が更新版を渡したら、GitHubに上げ直すだけ。

## うまくいかない時
- ビルドが赤い：`Dockerfile` と `requirements.txt` がリポジトリ直下にあるか確認（`app` の中ではなく、フォルダの一番上）。
- 開けるが「テンプレ下書き」と出る：`ANTHROPIC_API_KEY` 未設定。ステップ3を実施。
- 最初のアクセスが遅い：無料枠が眠っていた復帰（30〜50秒）。少し待つ／事前に温める。
- データが毎回リセット：無料枠は再デプロイで初期化される仕様。ダミーは毎回自動で入るので見せる用途は問題なし。
