# ComfyUIジョブの状態遷移・失敗・再実行設計

## 設計原則

- Mac M4・16GBでは生成同時実行数を1とする。
- 論理Jobと実際のAttemptを分離する。
- 生成成功とユーザー採用を別状態として扱う。
- 入力Assetと設定はJob作成時に不変スナップショットにする。
- 再実行で過去の出力やログを上書きしない。
- Jobの進捗はページや端末の接続状態から独立させる。

## Job状態

```text
CREATED
  → QUEUED
  → LEASED
  → RUNNING
  → RAW_OUTPUT_READY
  → COMPOSITING
  → VALIDATING
  → SUCCEEDED
```

終端または異常状態：

- `FAILED`
- `CANCELLED`
- `TIMED_OUT`
- `WORKER_LOST`

キャンセル要求は過渡状態として`CANCEL_REQUESTED`を使用できる。ComfyUI側で直ちに停止できない場合、UIには「キャンセル処理中」と表示し、完了した生出力を採用候補へ自動昇格させない。

## Candidate状態

```text
REVIEW_PENDING
  → ADOPTED
  → REJECTED
  → STALE
```

Jobが`SUCCEEDED`でも自動採用しない。画素検証済みStrict Outputだけが`REVIEW_PENDING`になれる。

## 直列キュー

- Generation Agentごとの同時lease数を1とする。
- FIFOを基本とする。
- キャンセル済みJobを実行前に除外する。
- Phase 1ではユーザー優先度変更や並べ替えを必須にしない。
- キュー位置は推定値として表示し、確約時間として扱わない。
- 約3分の基準時間から概算待ち時間を表示できるが、実測平均と分散も併記する。

## Job作成時の入力

- `sourceVersionId`
- 正本画像Asset IDとSHA-256
- Mask ID、Asset ID、SHA-256、feather設定
- Guide IDとSHA-256
- prompt / negative prompt
- preset snapshot
- model name / hash
- workflow revision
- workflow JSON SHA-256
- seed
- idempotency key

Job作成後にWorkspaceのマスクやガイドが変更されても、Job入力は変化しない。

## Workflow revision

- Workflow JSONはGeneration Agent配下でGit管理する。
- revision IDは公開後に再利用しない。
- manifestに操作種別、JSON相対パス、対応パラメータschema、SHA-256を記録する。
- Job実行前に実ファイルのSHA-256をmanifestと照合する。
- 不一致時はComfyUIへ送信せず`WORKFLOW_HASH_MISMATCH`で失敗させる。
- JobAttemptへrevision IDと実ファイルSHA-256を保存する。
- 過去Jobから参照されるrevisionを削除しない。

## Model file verification

- モデル初回登録時にファイル全体のSHA-256を計算する。
- ファイルサイズ、更新日時などの変更を検知した場合にSHA-256を再計算する。
- 通常Jobでは検証済みのファイル情報を照合し、確認したモデルhashをJobAttemptへ保存する。
- モデルが見つからない場合は`MODEL_FILE_MISSING`、変更後の再検証が完了していない場合は`MODEL_NOT_VERIFIED`としてComfyUI送信前に停止する。
- モデルファイルが大きいため、変更がない状態でJobごとに全ファイルを再hashしない。

## 進捗通知

ブラウザ向けにSSEを使い、次のイベントを送る。

- `job.status.changed`
- `job.queue.position.changed`
- `job.progress.updated`
- `job.attempt.failed`
- `candidate.ready`

SSE切断時は再接続し、最後のイベントID以降を取得する。イベントを取り逃しても、`GET /generation-jobs/{id}`の現在状態から復元できることを必須とする。

## エラー分類

### 再実行候補

- ComfyUI一時停止・接続失敗
- Generation Agent再起動
- 一時ファイルI/O失敗
- 明示されたタイムアウト

### 入力修正が必要

- 空マスク
- 寸法不一致
- 非二値マスク
- 不正なプリセット値
- 元Asset不在またはハッシュ不一致

### 手動判断が必要

- ComfyUIは成功したが要求衣装と異なる
- 余分な手足などの生成品質問題
- 保持検証失敗
- モデルまたはWorkflow revision不一致

## 再実行

- 再実行は同じGenerationJobへ新しいJobAttemptを追加する。
- 同一条件再試行では入力、workflow、seedを固定する。
- 別seed再試行はユーザーの明示操作とし、新しいseedを記録する。
- マスク、ガイド、promptを変更する場合は、新しいEditOperationとGenerationJobを作る。
- 自動再試行回数には上限を設ける。既定値は設計時未決定とする。

## 冪等性

Job作成APIは端末再送に備えて`Idempotency-Key`を受け取る。同じ所有者、同じ操作範囲、同じキーの再送では新しいJobを作らず既存Jobを返す。

採用APIも同様に重複採用を防止する。通信切断後に再送されても、既に採用済みなら同じ結果を返す。

## 採用競合

候補は`sourceVersionId`を持つ。採用トランザクションで次を比較する。

```text
candidate.sourceVersionId
  == expectedHeadVersionId
  == project.headVersionId
```

不一致なら候補を`STALE`とし、確定版を変更しない。ユーザーには次を提示する。

- 候補を閲覧だけ続ける。
- 現在の最新版を入力にして再生成する。
- 現在の最新版から新しいマスク・ガイド編集を開始する。

ラスター画像の自動rebaseや自動マージは行わない。

## Agent再起動時

- `LEASED`または`RUNNING`のAttemptを無条件に成功扱いしない。
- ComfyUI履歴と出力ファイルを照合できる場合だけ回収処理を行う。
- 回収不能なら`WORKER_LOST`とし、再実行可能にする。
- 確定版と採用済みAssetには影響させない。
