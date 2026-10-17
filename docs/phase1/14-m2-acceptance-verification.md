# M2画像編集Workspace 受入検証（2026-10-17）

## 結論

M2の自動検証範囲は合格。ただし、承認済み設計で必須とした**実機iPhone Safari**と、Safari・Chrome・Edgeの現行／直前メジャー版の実ブラウザ互換性は未検証。このため、**M2の最終受入は保留**とする。以下の「合格」は試験した環境・経路に限る。ComfyUI、生成ジョブ、候補採用、LAN向けHTTPS／端末認証は本判定の対象外。

## 環境と再現手順

- 検証機: Mac M4、16GB、macOS 15.3.2。画像座標は832×1216。
- Playwright 1.58.2: bundled Chromium 145.0.7632.6のDesktop Chromeプロファイル（DPR 1）、Pixel 7プロファイル（DPR 2.625）、bundled WebKit 26.0のiPhone 13プロファイル（DPR 3）。いずれも**エミュレーション**。Google Chrome本体、Safari本体、Edge本体の動作確認ではない。
- `apps/web`で `pnpm test`、`pnpm build`、`pnpm test:e2e --workers=2`。PlaywrightはNext.js開発サーバーをlocalhostに起動し、HTTP APIを試験用の永続スナップショットで差し替える。
- `services/generation-agent`で `PYTHONPATH=src python -m unittest discover -s tests -v` と `PYTHONPATH=src python tests/benchmark_workspace.py`。Python 3.12のPillow/NumPy導入済み実行環境を使用。内部API試験のみlocalhostソケットを使う。
- production buildとPlaywrightは同じ`.next`出力を共有するため**同時実行しない**。同時実行した最初の試行は両者が干渉して失敗し、順次再実行で解消した。製品不具合として数えない。
- 接続されたiPhoneは見つからず、実機操作・回転・バックグラウンド復帰・実機IndexedDB復旧は実施していない。

## 受入基準

1. **異なるviewport／ズーム／パン／DPRのマスク画素一致: 合格（エミュレーション）**。3構成とも、画面操作で同じ画像座標の3点連続ストロークを描き、自動保存後のbase64マスクを`strokePixels`の期待値と全1,011,712画素で比較し一致。再読込後も同一マスク、Undo履歴はリセット。期待値は製品と同じラスタ関数を使うため、この試験は主にUI座標変換・保存経路を検証する。HTTP保存先はモックのため、実APIまで一体化したUI E2Eは未検証。
2. **一本指→二本指の切替: 合格（エミュレーション）**。二本目で未確定ストロークを取り消し、先に一本を離しても残るポインタが離れるまでマスク保存なし、Undoなし。画面入力は合成Pointer Eventsを含み、実機マルチタッチとは区別する。
3. **両ガイドとマスクの連動Undo／Redo: 合格（エミュレーション）**。マスク消去で仮着色・シルエット両ガイドの同じ画素のalphaが0となり、一回のUndoで三者が戻り、Redoで三者が消えることを保存データで確認。
4. **自動保存／IndexedDB復旧／再読込／409競合: 合格（エミュレーション）**。失敗した自動保存の復旧スナップショットをIndexedDBで読み取り、再起動時の承認後に保存。競合中はUndo可能な未保存内容を維持し、読み直しのキャンセルでは破棄せず、確認後のみ読み直す。別Workspaceへの保存は元Workspaceを変えず編集画素を保持。
5. **大ストロークの応答・保存・容量: 合格（測定完了、性能基準値は未定）**。下記実測は単発・試験環境値であり、SLAを証明しない。WebKitエミュレーションで指を離した後の処理が約1秒に達する点、IndexedDB容量の環境差がリスク。
6. **回帰試験／ビルド: 合格**。Python unittest 22件（M1の17件を含む）、Vitest 6件、Playwright 30件（3構成×10件）、production build／TypeScript検査を順次実行。既存WebKitの409テストは初回の全件実行で描画準備前にクリックして失敗したが、準備待ちを追加後に単独3回連続・全件30件とも合格。
7. **実機iPhone Safari: 未検証**。一本指・二本指、画面回転、バックグラウンド復帰、IndexedDB復旧は要実機確認。
8. **実ブラウザ互換性: 未検証**。Safari／Chrome／Edgeの現行・直前メジャー版は今回いずれも実ブラウザでは確認していない。PlaywrightのChromium／WebKit版を代用して合格としない。

## 性能実測

256画像pxブラシ、画像座標(150,150)→(700,1050)、60移動ステップ、832×1216。`tests/e2e/acceptance.spec.ts`を実行し、ポインタ解放の処理時間、解放から自動保存完了まで、Chromium CDPのJSヒープ、失敗保存後の復旧データを測定した。APIはモック。ブラウザ入力補間が異なるため、この長いストロークの着色画素数はブラウザ間で完全一致を期待する条件ではない。厳密画素一致は上記の同一画像座標テストで検証した。

- Desktop Chromium: 解放処理251ms、保存完了まで2,121ms、着色321,468画素、JSヒープ16.93→57.37→27.48MiB（前／ストローク後／保存後）。
- Mobile Chromium: 解放処理231ms、保存完了まで2,100ms、着色321,468画素、JSヒープ16.36→53.41→27.38MiB。
- Mobile WebKit: 解放処理954ms、保存完了まで2,826ms、着色322,694画素。CDPヒープ計測不可。
- 復旧スナップショットのJSON論理サイズは各構成11.58MiB。`navigator.storage.estimate()`の報告使用量はChromium 0.55MiB、WebKit 25.73MiB。圧縮・実装差があるため、論理サイズと報告値は直接比較しない。空き容量の長期保証やiPhone実機のクォータ試験ではない。
- Pythonコアの3回のSQLite・PNG保存（毎回異なる496,451画素マスク）: 23.6／23.8／28.1ms。単一プロセスのpeak RSSは準備後85.70MiB→3回保存後150.38MiB。localhost HTTP、Next.js転送、ネットワークを含まない。再現コマンドは上記。

## 変更点と残るリスク

- `WorkspaceEditor.tsx`に現在のview transformを読み取るテスト用data属性を追加。保存契約、マスク正本、M1コード・SQLite migrationには変更なし。
- `acceptance.spec.ts`にUIからの画素一致、二本指、両ガイド、復旧、409、性能試験を追加。`benchmark_workspace.py`を追加。
- 既存`workspace.spec.ts`の409テストは、Konvaキャンバスとfit倍率が揃ってから描画し、Undo可能な編集ができたことを確認してから保存するよう修正。製品側の競合処理は変更していない。
- E2Eの保存先はモックであり、ブラウザ→Next.js Application API→Python内部API→SQLiteを一本の実環境で通す試験は残る。Pythonの内部API結合試験では409とM1不変性を確認済み。
- WebKitエミュレーションの大ストローク解放約954msは体感上遅い可能性がある。実機で手触りを確認し、必要ならM2範囲で応答改善する。
- iPhone Safariの実機確認と、実ブラウザの対象バージョン別確認が終わるまで最終受入しない。
