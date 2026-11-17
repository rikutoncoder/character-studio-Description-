# M4 版履歴・復元 実装記録（2026-11-17）

## 到達点

承認済み[実装計画](10-implementation-plan.md)のF4に沿って、プロジェクトごとの版一覧・画像・親版・操作種別・生成指示を表示する画面を追加した。プロジェクト一覧の「版履歴を見る」から開ける。過去版を選んでも現在版は変わらず、確認後の「この版を基に新版を作成」で初めて復元する。PCとスマホ相当のレイアウトを用意した。

復元は選択版の不変Canonical AssetをSHA-256で検証して再利用し、現在版を親とする新しいImageVersionを追加する。`restored_from_version_id`に選択元を記録し、旧版やAsset行を更新・削除しない。`expectedHeadVersionId`の比較交換で別端末の先頭版変更を409で拒否する。Idempotency-Keyの同一依頼は同じ新版を返し、異なる依頼へのキー再利用は409。ブラウザは不明な応答時の依頼キーをsessionStorageへ保持し、再読込後に同じ依頼を確認できる。

SQLite schema v5で復元元のnullable列と`version_restore_requests`監査表を追加した。既存版は復元元nullのまま。HTTP契約は`GET /api/m2/projects/{projectId}/versions`、`GET /api/m2/versions/{versionId}`、`POST /api/m2/versions/{versionId}/restore`。Python内部APIは引き続き127.0.0.1限定で、ブラウザはNext.js経由とする。

## 検証

- M1〜M4 Python unittest 45件合格（9.686秒）。復元で旧版・Assetが不変、RGBA画像のAssetを再利用、同一依頼の二重作成防止、先頭版競合409、既存DBからのmigrationを確認。
- 版履歴UIのPlaywright 6ケース合格（4.7秒）。Desktop Chromium、Mobile Chromium、Mobile WebKitエミュレーションで、新版追加と409時の表示を確認。WebKitエミュレーションをiPhone実機とは数えない。最初の実行は試験locatorがNext.jsのroute announcerも拾って3件失敗し、locatorを限定して再実行した。製品の409表示自体は出ていた。
- TypeScriptチェックとNext.js production build成功。ビルド時に`/versions/[projectId]`のrouteを確認した。
- 通常DBのオンラインバックアップを一時コピーとしてschema v4→v5へ移行し、既存のProject 2、Asset 56、ImageVersion 1、Mask 1、CompositeResult 1、Workspace 5、Job 1、Candidate 1などを全件照合。`integrity_check=ok`。

## 通常環境への反映

通常DBの実行中・待機中Job 0、安全保留0、`integrity_check=ok`を確認し、`~/Library/Application Support/CharacterStudio/Data/backups/m4-before-v5-20261117-170933.sqlite3`へオンラインバックアップした。SHA-256は`f463c96ea10c23b0e7588790cbae21200ddd5ca7903c6820bf239844b4e4c58c`。復旧が必要な場合はLAN入口・Agentを停止し、現DBを追加退避してからSQLite backup APIで戻す。稼働中のDBファイルを直接上書きしない。

通常DBをv5へ移行し、バックアップと既存全行が一致、整合性正常、復元依頼0件。新しいproduction build後、macOS管理サービス`CharacterStudioM3Validation`が旧ビルドを先に自動起動したため、`launchctl kickstart -k`で完成済みビルドを再読込した。Macの版履歴ページHTTP 200、`iPhone動作確認`プロジェクトの版履歴API HTTP 200、版1件・現在版ID一致を確認。Python Agentはlocalhost:8765、Next.jsはlocalhost:3000、既存HTTPS入口はLAN内3443で稼働している。ComfyUIは停止していない。

## 未完了

通常のユーザーデータで復元ボタンは押していない。実ブラウザから通常DBへの復元書込、iPhone実機のM4画面・復元後表示、複数端末が同時に復元する操作は未検証。安全な隔離DBでのHTTP・UI試験は合格したが、これらを実機合格とはしない。M4の受入判定は残る実機・通し試験後に行う。M3のローカルマイルストーン受入とM5公開前残件は[別記録](18-m3-acceptance-verification.md)を参照。
