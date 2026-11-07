# M3 最終受入の追加検証（2026-10-18）

> 2026-11-07追補: 下記の「設計上の穴」は安全保留として修正し、自動回帰試験を通過した。同日、ユーザー承認後に通常DBをバックアップしてschema v4へ移行し、通常Agentへ展開した。最終受入は引き続き保留。末尾の追補を参照。

## 判定

**通常経路の試作として利用可能。M3最終受入は保留。M4本格実装は開始しない。** 実ComfyUI Desktopの停止・復旧と、旧プロセス停止を人が確認した後の別Jobでの再生成は実施した。ただし同一Jobのretryは履歴消失後に拒否され、別Job作成はそのガードを通らない。一般操作での安全な再実行契約が未完成である。

対象は`feat/m3-generation`、検証開始時のHEADは`e9e5c0c`。承認済みPhase 1設計、[M2受入記録](16-m2-final-acceptance.md)、[M3実装記録](17-m3-implementation-progress.md)、M1～M3のコード・既存試験を確認した。既存のiPhone実機生成復帰、SSE復帰、409競合、Agent側通信断、取消し試験は再実施対象から外し、未検証のDesktop停止、並行通信、性能、回帰に焦点を当てた。

## A. ComfyUI Desktop停止・再起動: 停止・復旧は合格、再実行契約は不合格／未決定

2026-10-18の試験前に、DesktopアプリPID 9268、ComfyUI backend PID 11514、localhost:8188のqueueが空であることを読み取り専用で確認した。停止の影響として、開いているComfyUIの一時停止、生成途中のRaw消失の可能性、未保存ワークフローの喪失を説明し、ユーザーから停止試験の承認を得た。通常SQLiteをオンラインバックアップ`/private/tmp/character-studio-m3-desktop.EQTJ5P/normal-before.sqlite3`へ保存し、`integrity_check=ok`、Project 2件、Asset 53件、ImageVersion 1件、Mask 1件、Workspace 4件、Job 1件、Candidate 1件とproject headを記録した。試験の書き込み先は別の`/private/tmp/character-studio-m3-desktop.EQTJ5P/runtime`だけとした。

隔離Job `c9149b5d-3dbc-409e-ada8-69b0ea6d5b85`、ComfyUI prompt `7b47ae92-bcc8-4bd2-a580-e77415d91a20` が実際に`queue_running=1`となったことを確認してから、AppleScriptの通常の`quit`命令でDesktopを終了した。Desktopとbackendが消え、localhost:8188への接続が失敗することを確認。約35.7秒でJobは`FAILED`（`ConnectionResetError`）、候補0件、合成結果0件、attempt 1件、ImageVersion 1件、project head不変、隔離DB `integrity_check=ok`。通常DBも停止後に`integrity_check=ok`、件数と両project headがバックアップ時と一致した。自動再実行・二重候補・二重採用は観測されていない。

`open -a 'Comfy Desktop'`でDesktopアプリPID 81398を再起動したが、その時点ではMacがロック中で既存インスタンス選択画面が開いたまま、backendは未起動だった。10月22日にユーザーがロック解除後、既存のStandalone ComfyUIインスタンスを選んだ。新backend PID 90793、localhost:8188の`system_stats`はComfyUI 0.40.0、queueは空。旧promptの`/history/{id}`は`{}`。元Jobの`retry`は`Previous ComfyUI prompt may still be running`で拒否され、`FAILED`・attempt 1件・候補0件のまま。これは履歴が消えた場合に自動二重生成を防ぐ安全側の動作である。

旧backend PID 11514の停止と新PID 90793、空キュー、旧Jobの失敗状態を人が照合した後、**同一Job retryではなく**別の新Job `4afe37ee-81fe-4e46-a096-e2c450419aa8`を同じ隔離Workspaceに1件作成した。新ComfyUI prompt `a94e0e54-df85-483a-b8f2-426ce484b375`は746.1秒で`success`、候補 `3e382ab1-55d6-46fc-8846-b7ec0a9fa8aa`が1件。旧Jobは`FAILED`・候補0件、新旧Jobのattemptは各1件、project head不変、マスク外・alpha変更各0画素、SQLite `integrity_check=ok`。終了後のqueueは空、旧prompt historyは空、新prompt historyは`success`。通常DBは再度`integrity_check=ok`、Project 2／Asset 53／ImageVersion 1／Mask 1／Workspace 4／Job 1／Candidate 1と両project headがバックアップ時に一致した。ComfyUIは実生成可能に復旧した。

再生成には約12分26秒かかり、実行中のMac全体swapは観測値12,457.50MiB（約12.17GiB）、システム空き率は最低18%程度。終了後はswap 7,671.56MiB（約7.49GiB）、空き率75%。単発の観測であり、前回の約3分35秒より遅くなった原因は未特定。長時間・高swapの性能リスクは残る。

**発見した設計上の穴:** `retry`は不明な旧promptを拒否するが、別のidempotency keyによる`create_job`は旧Jobの未解決promptを確認しない。旧backendを確実に停止した今回の手動試験では二重生成は起きなかった。しかしAgent側接続断などで旧ComfyUI promptが実行中のまま新Jobを作れば、二重生成し得る。安全な管理者解決手順と新Job側のブロック方法を決めるまで、一般ユーザー向けの「安全確認後に再実行できる」を合格としない。製品コードは今回変更していない。

## B. HTTP接続の安定性: 条件付き合格

通常のデータディレクトリと分離した`/private/tmp`の使い捨てSQLiteに、832×1216のWorkspaceとfake rendererによる成功済みJobを作成した。Python AgentとNext.js production serverをともに127.0.0.1の空きポートへ起動し、4つのHTTPクライアントを同時に実行。1つはWorkspace保存、1つは約12MBのWorkspace再読込、1つはJob状態取得、1つは`Last-Event-ID`付きSSE再接続を反復した。読込側は1KBだけ受信した大きなGETを途中切断した。旧HTTP接続リセット修正済みコードを使用した。

5分間の結果は保存263回、再読込337回、Job GET 578回、SSE接続20回、GET途中切断42回。失敗0件、SQLite `integrity_check=ok`、最終revision 265で成功保存数と一致。project head、確定Asset、候補件数は不変。応答時間は保存が平均132.9ms／最大173.8ms、再読込が平均76.2ms／最大108.4ms、Job GETが平均11.0ms／最大87.7ms。SSEはイベントがなければ約25秒待つ設計であり、観測最大25.30秒を接続不良とは扱わない。

別の15秒試験では保存14回、再読込17回、Job GET 29回、SSE接続9回、GET途中切断2回を実施し、失敗0件。さらに`Content-Length: 12000000`を宣言したPUT本文を途中で切断し、最終revisionが成功保存14回分だけ進んだこと、headと候補が不変であることを確認した。

再現条件はMac内のlocalhostで隔離したAgent／Next productionを起動し、保存側は同一Workspaceのフェザー値を3pxと4pxで交互にPUT、読込側は同じWorkspaceをGET、Job側はSUCCEEDEDの同一JobをGET、SSE側は切断と`Last-Event-ID`再接続を反復する。再現用の一時スクリプトは`/private/tmp/character-studio-m3-stability.py`であり、リポジトリには含めていない。通常LANサイトや通常SQLiteには負荷を書き込んでいない。

これは**PCとスマホに相当する別HTTPクライアント**の並行試験であり、PCとiPhoneの実機同時操作ではない。5分間を超える長期運用、LAN gateway越しの高負荷、接続リセットの根因の完全な確定は未検証。実iPhoneの単独M3操作と生成中復帰は前記M3実装記録で合格済み。

## C. Mac M4・16GBの実生成性能: 測定実施、運用余裕は未確定

測定環境はMac M4、16GiB、macOS Sequoia 15.3.2（build 24D2082）、ComfyUI 0.40.0、Animagine XL 3.1、832×1216、localhost:8188。Phase 0の`TEST001_base.png`と`TEST001_precise_chest_mask.png`を読み取り、使い捨てDB `/private/tmp/character-studio-m3-perf.KPIqCv` にプロジェクトを作成した。通常DB・確定Assetには書き込んでいない。色変更、denoise 0.30、25 steps、CFG 7、seed 42003のJobを1件投入し、約10秒ごとに`sysctl vm.swapusage`、ComfyUI `/system_stats`、Next.js `/`のGET応答を21回観測した。再現用の一時スクリプトは`/private/tmp/character-studio-m3-perf.py`で、リポジトリには含めていない。

Job `4aa9895e-6f59-4f9c-bcdd-a6c5ded7a480` は214.5秒で`SUCCEEDED`、候補 `6c2c17ec-3d96-405e-96b1-bfcf14674275` が1件。ImageVersionは元の1件のままで、採用していない。候補のRaw／Strict SHA-256はそれぞれ`a20eb936ce625c4a992169cc3b6582c414bfaca11d18d580e7e0811bdc980ef7`／`25c2f5a0d6269386673aad347e54adcfdb7b96f882bcb2eef0c41369ed5fc8cf`。`composite_results`のマスク外変更0画素、最大差分0、alpha変更0画素、SQLite `integrity_check=ok`。ComfyUIのRawファイル`CharacterStudio_M3_Color_Raw_00005_.png`は既存の出力フォルダに残る。

Mac全体のswap使用量は最初の観測4,138.25MiB、最大10,556.94MiB（差約6,419MiB）、生成後の読み取りでは8,769.69MiB。ComfyUIが返す`ram_free`は最小203.0MiBで、**Mac全体の空きメモリ量でもピーク使用量でもない**。21回の画面GETはすべてHTTP 200、最大286.9ms。ただしHTTP GETは描画・スクロール・ボタン操作の体感を測らない。`memory_pressure -Q`の生成中サンプリングは収集スクリプトの文字列抽出ミスで数値欠落し、生成後のシステム空き率80%のみ取得した。Mac全体のピーク常駐メモリや実UI操作応答は**未測定**。swapが大きく増えたため、16GiB機での同時アプリ利用には注意が必要である。

## D. 回帰試験: 合格

- Python `unittest discover -s tests -v`: 37件合格、約4.75秒。M1のマスク外変更0画素、RGBA透明情報保持、不変Asset、M2、M3のStrict以外採用禁止、STALE拒否、直列実行、取消しと再実行ガードを含む。
- Vitest: 6件合格。
- Playwright: 42件合格、約56.2秒。Desktop Chromium、Mobile Chromium、Mobile WebKit**エミュレーション**の既存E2Eを再実行。実iPhone試験とは数えない。
- Next.js production build／TypeScript検査: 成功。稼働中サイトの`.next`を壊さないよう、Webアプリのコピーを一時ディレクトリでビルドした。

新たな製品コードの不具合・修正は今回なし。既存の接続リセット修正の長時間並行試験が通過したが、根因を完全に証明したわけではない。追加ダウンロード、有料契約、ComfyUIのインターネット公開、通常DBへの試験書込は行っていない。

## 残存リスクと次の受入ゲート

1. Desktop本体停止・backend復旧・手動照合後の別Job生成は確認したが、履歴が消えた元Jobのretryは解除できず、別Job作成には未解決promptのガードがない。保存データと制作フローに影響するため、解決方法の設計承認後に最小実装と回帰試験が必要。
2. PCとiPhoneの実機同時アクセス、LAN gateway経由の長期負荷、実UI操作中のメモリ・応答は未検証。M2の公開前ゲートも[別記録](16-m2-final-acceptance.md)どおり残る。
3. 衣装追加の形崩れは画素保持とは別の画質問題。候補は自動採用せず、目視判断を維持する。

現時点でM3の**最終受入は不可（保留）**。上記未検証を合格に読み替えず、M4の本格実装を開始しない。

## 2026-11-07 安全保留修正の検証

ユーザーは「未確認の旧生成がある間は新しい生成も止め、管理者が安全確認後だけ再開する」方針を承認した。`generation_safety_holds`を追加し、失敗・Agent再起動・prompt受付成否不明時に保留を永続化する。保留中のJob作成はHTTP 409、待機Jobのworker実行は停止、retryも拒否。ComfyUI historyが終了を証明した場合は自動解除。history消失時はMacローカルの管理者CLIのみが、旧backend PID消滅、新backendが設定ポートで待受、空queue、操作理由を確認・監査記録した上で解除できる。LAN／ブラウザに解除APIは設けていない。schema v3→v4移行は未解決の既存Attemptを保守的に保留へ登録する。

- Python unittest **43件合格**（従来37件＋安全保留・管理解除・v3移行・HTTP 409の6件）。隔離一時DBとlocalhostテストサーバーを使用。M1の画素厳密保持・RGBA・不変Asset、M3のStrict採用制限・STALE拒否・直列キューも再通過。
- Vitest **6件合格**、Playwright **45件合格**（従来42件＋安全保留の日本語警告を3構成で確認）。PlaywrightのMobile WebKitは実iPhoneではなくエミュレーション。
- Next.js production build／TypeScript検査 **成功**。生成画面に安全保留の日本語説明を追加した。
- 安全保留中の既存Idempotency-Key再送は既存Jobを返し、新たな生成を作らない。HTTPで別キーの新Jobは409。旧historyが成功・失敗の終端を示す場合は保留解除後に待機Jobを実行可能。旧PIDが残る・queueが空でない場合の管理解除は拒否。管理解除は監査情報を残す。

管理者CLIの実操作も、通常DBとは別の`/private/tmp/character-studio-m3-admin.zinAMB`で確認した。旧実験のComfyUI PID 11514は消滅、現在のPID 90793は`127.0.0.1:8188`で待受、queueは空、旧promptはhistoryにないことを照合。隔離した模擬失敗Attempt `e41b1f0a-3fa2-4750-87e3-32e244b135c5`へ`ADMIN_RESTART`の監査付き解除を行い、解除後に旧Job retryと別Job作成が各`QUEUED`へ進むことを確認した。workerは起動せず、ComfyUIへの再生成・候補作成は0件。CLIの`--list`はSQLite read-onlyで動作し、schema v3を暗黙に移行しない。旧PIDの出所は管理者の独立した確認に依存するため、出所を特定できない場合は解除しない。これは**通常DB上の管理解除試験ではない**。

通常DBのオンラインバックアップを一時ディレクトリに作り、**コピーだけ**をschema v3→v4へ移行した。Project 2、Asset 53、ImageVersion 1、Mask 1、Workspace 4、Job 1、Attempt 1、Candidate 1、CompositeResult 1の件数、project head、全AssetのID・SHA-256・保存参照、全ImageVersion行が移行前後で一致。`integrity_check=ok`、このコピーでは安全保留0件。一時バックアップは試験終了時に破棄した。通常DBは引き続きschema v3であり、移行試験による変更はない。

今回の試験は**すべて隔離DB**で実施。通常SQLiteはschema v3・`integrity_check=ok`のまま。2026-11-07確認時点で通常Agentは停止中で、新コードを通常環境へ展開していない。したがって**修正済みソースと通常利用サービスを同一視しない**。通常データの事前バックアップ、隔離環境での管理CLI実操作、展開後の実ブラウザ409と再開を追加確認するまでM3最終受入は保留し、M4本格実装を開始しない。

## 2026-11-07 通常環境への展開後確認

直前の段落は展開前の状態。ユーザーが通常環境への反映を承認した後、通常DBに実行中・待機中Jobが0件であることを確認し、SQLiteオンラインバックアップをアプリ専用の`Data/backups/m3-before-v4-20261107-122715.sqlite3`へ保存した。バックアップの`integrity_check=ok`、SHA-256は`f17fc09c6cd62e08b4aceaabdcace648f2d24ec21117f22bf39f77a67c3a9a6d`。復旧時は稼働中サービスを止め、現DBを別途退避し、SQLite backup APIでバックアップを戻す。

通常DBをschema v4へ移行し、`integrity_check=ok`。Project 2、Asset 53、ImageVersion 1、Mask 1、CompositeResult 1、Workspace 4、EditOperation 1、Job 1、Attempt 1、Event 8、Candidate 1の行内容は移行前のバックアップと完全一致。未解決の安全保留は0件。既存AssetのSHA-256・確定版・project headは移行によって変化していない。

既存のproduction buildから通常LAN入口とAgentを起動。`pnpm start:lan`はpnpmの依存関係チェックで起動前に失敗したため、同じ`lan/run.mjs`を既存Nodeで直接実行した。起動時の認証付き内部projectsチェックはHTTP 200、Web `/`は200、Web `/api/m2/projects`は200。ローカルCAを使ったHTTPS入口の未認証要求は401、Python内部APIの無認証要求は403。通常DBへ試験用のJob・保留・候補を追加していない。

最初の起動は生成用環境変数が欠けていたため正常終了し、既存のcheckpoint・manifest・ComfyUI localhost URLを指定して再起動した。モデルとworkflowのSHA-256検証を通過し、ComfyUI `system_stats` 200。再起動後、Web 200、HTTPS未認証401、通常DB schema v4・整合性正常・既存件数不変・安全保留0件を再確認した。新規生成はしていない。

展開後にM1～M3のPython unittest 43件を隔離DBで再実行し、全件合格（9.106秒）。最初の試行は実行環境がテスト用localhostソケット作成を拒否したため中断し、ソケットを許可した同じ試験を再実行した。通常DBにはテスト書込をしていない。

**判定はまだ保留。** 展開後の通常環境での実ブラウザ409／管理解除は未検証。既存の隔離DB試験が43件Python・6件Vitest・45件Playwright・production build合格であることと区別する。iPhone実機で新コードのM3画面操作、PCとiPhoneの同時アクセス、長期LAN負荷、M2公開前ゲートも残る。実機未検証を合格へ読み替えず、M4本格実装を開始しない。
