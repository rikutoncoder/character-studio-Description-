# M3 生成・比較・採用 実装記録（2026-10-17）

## 現在の到達点

M3の保存契約、生成キュー、ComfyUIでの合成・採用経路、およびWeb UIを実装した。Mac上の既存ComfyUIとAnimagine XL 3.1で色変更2件・衣装追加2件を実行し、Raw→Strictの画素検証に成功した。分離した一時DBへの実ブラウザJob投入→比較→Strict採用も通過した。追加の安全性試験では実ComfyUI生成中の取消し、実ComfyUIとのAgent側通信断、Agent HTTPプロセス再起動、SSE再接続、実ComfyUI Rawを使った実ブラウザ採用409、iPhone実機の生成中復帰を確認した。**判定は「通常経路の試作として利用可能、M3の最終受入は保留」。** ComfyUI Desktop本体の停止・再起動とM2公開前残件は継続。追加ダウンロード・有料契約は行っていない。

## 保存・安全設計

- SQLite schema v3に `edit_operations`、`generation_jobs`、`job_attempts`、`job_events`、`candidates` を追加。M1 Asset・Mask・ImageVersion、M2 Workspaceには上書きしない。
- Job作成時にWorkspace revisionを確認し、M1の二値Mask検証を通して不変Maskを作る。元画像・ガイド・MaskのAsset IDとSHA-256、prompt、preset、workflow/model fingerprintを固定する。空・全面Maskは送信前に拒否。
- Python APIとは別のSQLite接続を持つworkerがFIFOで1件ずつ実行。再起動時に進行中Jobを`WORKER_LOST`として扱い、自動再送しない。cancel要求後のRawは候補にしない。retryはAttempt追加型。
- RawとStrictを別Assetに保存し、M1の`strict_composite`とPNG再読込後の`validate_strict_output`を実行。マスク外・alpha違反の候補は作らない。
- 採用はStrictだけに限定。Candidateのsource、APIのexpectedHead、現在のproject headが一致することをSQLiteトランザクションで確認し、新しいCANONICAL_IMAGEとImageVersionを追加する。409時にheadを変更しない。採用済みの同じIdempotency-Keyは同じ結果を返す。
- Next.js Application APIだけをブラウザへ公開し、Python内部APIとComfyUIはlocalhost限定。端末認証は既存のLAN入口に委ねる。JobイベントはDBへ保存し、SSEのLast-Event-IDから再送できる。

## ComfyUI Workflow

`src/character_studio/workflows/`に、Phase 0のAnimagine XL 3.1出力PNGに埋め込まれたAPI promptを参照して、色変更・衣装構造追加のWorkflowを各1本追加した。色変更は初期denoise 0.30、構造追加は0.58。入力画像に選択ガイドをPythonでalpha合成してComfyUIへ渡し、二値MaskでInpaintModelConditioningを行う。ComfyUI側のSaveImage出力は**Rawのみ**として取得し、厳密保持合成はPython M1コアで行う。

manifestはWorkflowファイルとローカルAnimagine XL 3.1 checkpointのSHA-256を固定する。モデルファイルはGitへ入れず、`CHARACTER_STUDIO_MODEL_PATH`で指定する。モデルのサイズ・更新時刻が変わった場合だけ再hashする。ファイル不一致・ディスク空き2GiB未満なら生成前に失敗させる。

起動時の環境変数例：

```text
CHARACTER_STUDIO_COMFY_MANIFEST=<repo>/services/generation-agent/src/character_studio/workflows/manifest.json
CHARACTER_STUDIO_MODEL_PATH=<Mac上のanimagine-xl-3.1.safetensorsの絶対パス>
CHARACTER_STUDIO_COMFY_URL=http://127.0.0.1:8188
```

これらが未設定なら、Job作成は503で安全に停止する。ComfyUIをインターネットへ公開しない。既存のLAN入口からPythonへ環境変数が引き継がれる。安全性試験後のretryガードは、全Jobが終了したことを確認してからLAN入口とAgentを正常再起動し反映した。再起動後のAgent、Next.js、LAN入口の起動チェックは通過した。

## UIとAPI

- Workspaceが保存済みのときだけ「生成へ」を表示する。
- 色変更／衣装構造追加、prompt、negative prompt、denoise安全範囲、steps、CFG、seedを指定できる。
- Job状態、待ち順、失敗理由、cancel、同条件または別seedでの再実行を表示。Job IDをURLへ保持し、再読込後もGETで復元する。
- 元画像／AI生出力／Strictの三画像と差分・Mask境界を表示。Rawに採用操作はない。候補の採用・不採用を分ける。
- `POST /api/generation-jobs`、`GET /api/generation-jobs/:id`、SSE `/events`、`/cancel`、`/retry`、`GET /api/candidates/:id`、`/adopt`、`/reject`を追加。ブラウザからPythonへ直接接続しない。

## 試験

- Python unittest **37件合格**（既存M1/M2 22件＋M3 15件）。取消し後の再実行ガード、prompt受付後のHTTP切断、ComfyUI履歴確認を追加。HTTP結合試験の大きなWorkspace応答で、別々の全件実行中に接続リセットが計2回発生した。調査で一部POST経路がリクエスト本文を読まずに応答していたことを確認し、Workspace作成・複製、Job取消し、候補不採用で本文を読み切るよう修正。HTTP/1.1の同一接続で大きなWorkspace応答の後に次のGETが通る回帰試験を追加し、修正後は全37件を10回連続実行してすべて合格。さらに最終アサーション追加後の37件も合格。LAN入口・Agentを正常再起動して新コードを反映した。ただし以前の断続的失敗との因果関係は確証できていないため、長期の再発監視を残す。
- Vitest **6件合格**。Playwright **42件合格**（既存M2 33件＋M3画面のDesktop Chromium／Mobile Chromium／Mobile WebKitエミュレーション9件）。SSE切断・再接続後、Last-Event-IDを使ってJobの現在状態へ復帰する試験を含む。追加のbackend変更後はproduction buildを再実行していない（UI本体の変更なし）。
- Playwright WebKitはiPhone実機ではない。iPhone SafariでM3画面表示、画面回転、バックグラウンドからの画面復帰を確認。生成中の実機復帰は別項に記録する。

## 実ComfyUI・Animagine XL 3.1試験

Mac M4／16GB、ComfyUI 0.40.0、832×1216、localhost API。Phase 0の入力画像・Maskを読み取り、M3 adapterがガイドを元画像へ事前合成してアップロードした。M1の`strict_composite`はフェザー3pxで実行し、出力PNGの再読込検証まで行った。時間はモデル照合・アップロード後のComfyUI送信からRaw取得・Strict検証まで。RAMはComfyUIの`system_stats.ram_free`を5秒ごとにサンプルした最低値で、Mac全体のピーク使用量とは異なる。

- 色変更: 358.0秒、観測最小空きRAM 133.5MiB、Raw SHA-256 `102451e7a345482a1353f8a8e687d55aba67e08517f65c56b840134087fc60c6`、Strict SHA-256 `66bd25cfc40f51f42e868322126e45e3270a6cda19d0f53fd731605528e069ab`。マスク外0画素、alpha変更0。胸部の青色変更をRawで目視確認。
- 衣装追加・広域ガイド: 327.3秒、観測最小空きRAM 406.6MiB。マスク外0画素。ただしRawの左右に大きな余分な形が生じ、画質は不合格。
- 衣装追加・前開きシルエットガイド: 246.1秒、観測最小空きRAM 213.1MiB、Raw SHA-256 `57f96df180e6c5ad0fa0012ffc1e63fa2ac0befac24845d39f222341337d316f`、Strict SHA-256 `8130a3feba7611e460b8c44ab00e2f1cb7e7f604fd40e966244efc2f466e30a8`。マスク外0画素、alpha変更0。前開きの黒いブルゾンとして大幅に改善したが、腰付近の形や細部には手動確認・修正が必要。

RawはComfyUI既存の出力フォルダに`CharacterStudio_M3_Color_Raw_00001_.png`、`CharacterStudio_M3_Structure_Raw_00001_.png`、`CharacterStudio_M3_Structure_Raw_00002_.png`として残る。元のPhase 0画像とMaskは変更していない。取得したRawを自動採用していない。

## 実ブラウザ→ComfyUI→採用の通し試験

2026-10-17、Mac上のheadless ChromiumとNext.js production serverを使用。通常のユーザーデータと分離した一時SQLite DBにPhase 0の素体画像・胸部Maskを登録し、仮着色ガイドを設定した。保存済みWorkspaceの生成画面から色変更Jobを投入し、URL上のJob IDで画面を開き直して復帰した。実ComfyUIの結果を元画像・AI生出力・厳密保持結果の各表示で比較した後、Strictだけを新版として採用した。

- Job `69ff23e6-7e03-4462-80ee-b7ea528c79f1`、候補 `c5a2aeee-faa6-4101-a5df-a9f8389f8721`、採用版 `702bbafa-817c-45af-ad9c-00af71265310`。
- ブラウザ再接続から採用まで約253.2秒。マスク外変更0画素、alpha変更0画素。SQLite `integrity_check=ok`、採用版はproject headと一致。
- 生成中のM2閲覧APIはHTTP 200、約0.010秒で応答。実測は1回のみであり、負荷試験ではない。
- 比較画面でRaw・Strict・マスク境界が表示され、Strict以外に採用ボタンがないことを確認。スクリーンショットはローカルの `/private/tmp/character-studio-m3-e2e-result.png` に保存。
- 通常データの再起動前SQLiteオンラインバックアップを作成し、再起動後の `integrity_check=ok`、Project 2件、Asset 47件、ImageVersion 1件、Workspace 3件がバックアップ前と同数、通常DBのGeneration Jobは0件と確認。通し試験による採用は一時DBだけに実施。

再起動・通し試験で見つかった不具合を修正した。workerのSQLite接続をworkerスレッド内で開くよう変更。Next.jsが内部で`localhost`へ正規化する場合も、ループバックHostに一致するOriginならM3のPOSTを受け付けるよう変更。Job状態更新でEventSourceを毎回再作成するループを止め、Playwright回帰試験を追加した。Playwright開発サーバーがproduction buildを置き換えるため、最終試験後に改めてbuildし、LANサイトを起動し直した。

## 追加の安全性・復帰試験

いずれも2026-10-17、通常のプロジェクトDBから分離した`/private/tmp`の一時DBで実施。実ComfyUI取消し以外はfake rendererを使用し、ComfyUI Desktop自体の接続を切断していない。

- 実ComfyUI取消し: Job `0b71b456-4ab2-4598-a0a4-9c0b12cdfa1e`。ComfyUI prompt受理後、RUNNING中に取消し要求。約314.81秒後に`CANCELLED`、候補0件。ComfyUI内部の実行は続いたが、結果を候補化せず確定版も変えなかった。`tests/manual_m3_real_cancel.py`で再現可能。
- Agent HTTPプロセス再起動: Job `fa0e0c39-bb35-4a5e-9e6b-01d82b6265ec`。fake rendererの実行中にプロセスを終了し再起動。`WORKER_LOST`、候補0件、attempt 1件、再実行拒否。二重実行しなかった。`tests/manual_m3_restart.py`で再現可能。
- ComfyUI通信断の模擬: fake HTTPサーバーが`/prompt`受理後に`/history`接続を閉じるunit testを追加。promptが実行中または状態不明ならretryを拒否し、完了が確認できてからのみ再実行を許す。`/prompt`応答自体が切れ、受理されたか不明な場合は`SUBMISSION_UNCERTAIN`として自動retryを拒否する。これは安全側に止める設計であり、実ComfyUI Desktopの切断試験ではない。
- 実ComfyUIとのAgent側通信断: 分離した`/private/tmp/character-studio-m3-disconnect.VTnydU`を使用し、実ComfyUIがprompt `c8bb5402-dd6c-4bbc-b737-f404dd421190`を受理した後、テスト用Adapterの最初の`/history`読み取りを意図的に接続リセットとした。Job `fe85053c-fd82-4dd4-ad40-cbf7334903ff`は`FAILED`、候補0件、実行中のretry拒否。ComfyUI本体は止めず、約543秒後に元promptが`success`で終了したことを履歴で確認。終了後はretryをキューへ追加できたが、workerを起動せず2回目の生成は行っていない。再現用スクリプトは`tests/manual_m3_real_disconnect.py`。ComfyUIにはRaw出力`CharacterStudio_M3_Color_Raw_00004_.png`が残る。この試験は実モデル・実ComfyUIとの通信断だが、Desktopプロセスそのものの停止試験ではない。
- SSE切断・再接続: Desktop Chromium／Mobile Chromium／Mobile WebKitのPlaywrightで、イベント接続を閉じて`Last-Event-ID`付きで再接続。Jobが現在の`SUCCEEDED`状態へ復帰した。
- 採用直前の競合: 実Chromium→Next production→Python APIで、候補作成後に別のImageVersionへproject headを進めてから採用。HTTP 409、候補`STALE`、head不変。rendererはfakeであり実ComfyUI生成ではない。`tests/manual/conflict-m3.mjs`と`tests/manual_m3_conflict_server.py`で再現可能。
- 実ComfyUI Raw候補の採用競合: 上記通信断試験で実ComfyUIが生成した`CharacterStudio_M3_Color_Raw_00004_.png`（SHA-256 `3977f039f4835ada11e283603c773f64b9b5924aec38974300f7a607126fba19`）を、分離した`/private/tmp/character-studio-m3-conflict.PxaJaD`内の候補Rawへ使用し、AssetのSHA-256一致を検証。候補作成後、同じ一時DBでproject headを先へ進め、実Chromium→Next production→Python APIから採用操作。HTTP 409、候補`STALE`、head `d97179ea-10db-4b33-9346-0f888a4f014d`は不変。新しいComfyUI生成は行っていない。試験用localhostサーバーは停止し、通常LANサイトは稼働継続。
- iPhone Safari実機: LAN上のM3画面を開き、画面回転とSafariバックグラウンド復帰で画面維持を確認。実生成中にSafariから離れて戻り、ページ再読み込み後も同じJob状態へ復帰した。Job `2e85546e-d226-4c03-bd0d-a4b026d4bc8d`は12:58:50 UTC開始、13:05:07 UTCに`SUCCEEDED`（約6分17秒）。iPhone画面で元画像・AI生出力・厳密保持結果の比較画像表示を確認。読み取り専用のSQLite照合では候補`REVIEW_PENDING`、採用版なし、マスク外変更0画素、alpha変更0画素。新しいAgentへLAN入口を再起動した後も、Safariのページ更新で同じ比較画面へ戻れた。通常のユーザープロジェクトにJob・候補が1件残るが、確定版は変えていない。ブラウザの正確なバージョンは記録できていないため、バージョン別の互換性合格とはしない。

retryは前回のComfyUI prompt IDが残る場合、`/queue`と`/history`で終了を確認するまで拒否する。Agentが実行途中で失われprompt IDがない場合や送信成否不明の場合も拒否する。これにより自動二重実行を避ける一方、確認不能なJobの再実行には将来の管理者向け解決手順が必要となる。

## 受入前の残作業

1. 実ComfyUIとのAgent側通信断、および実ComfyUI Rawを使った採用直前409は通過した。ただしDesktopプロセス自体の停止・再起動は未検証。実Rawの409試験ではRawを一時DBへ再利用しており、生成から競合までの完全な連続実行ではない。
2. iPhone Safari実機の生成完了・候補表示は確認済み。ただしSafari／Chrome／Edgeの現行版と直前メジャー版を網羅した試験は未実施。
3. 衣装追加は精密なシルエットガイドがないと大きな形崩れが生じる。候補を自動採用せず、見た目の判断と必要な手動修正を制作フローに残す。
4. Python HTTP結合試験で大きなWorkspace応答の途中に接続リセットが計2回発生。未読POST本文という原因候補を修正し、同一ソケットで次のリクエストが通る試験と全37件の10回連続実行は合格した。断続的失敗の根因と断定できないため、実利用・CIで再発を監視する。
5. M2の両ガイド同時消去のiPhone保存画素・Undo／Redoは2026-10-18に合格。Next.js→Python→SQLiteの隔離環境で100回の保存・再読込も合格した。実機大ストローク性能、ブラウザの現行／直前版別試験、複数クライアントや長期反復保存は公開前ゲートとして継続。詳細は[16-m2-final-acceptance.md](16-m2-final-acceptance.md)。
