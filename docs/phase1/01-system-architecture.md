# Phase 1システムアーキテクチャ

## 推奨構成

Phase 1は、ローカルファーストのモジュラーモノリスと独立したGeneration Agentで構成する。

```text
PC / 認証済みスマホブラウザ
        │ HTTP(S) / REST / SSE
        ▼
Next.js Web + Application API
        │
        ├── Domain / Application
        │   ├── Project / Asset / Version
        │   ├── Edit / Candidate / Adoption
        │   └── Device / Session
        │
        ├── Metadata Repository Port
        │           └── SQLite Adapter
        │
        ├── Asset Storage Port
        │           └── Local Filesystem Adapter
        │
        └── Generation Gateway Port
                    │ localhost
                    ▼
             Python Generation Agent
                    ├── Serial Queue Executor
                    ├── ComfyUI Adapter
                    ├── Strict Compositor
                    └── Pixel Validator
                            │ localhost
                            ▼
                         ComfyUI
```

## 責務境界

### Webクライアント

- Canvas描画と入力処理
- マスク・ガイドの編集
- ジョブ作成、進捗表示、比較、採用操作
- 未確定編集のUndo/Redo
- サーバーから取得した状態の表示

クライアントは厳密保持合成の正本を作らない。ブラウザCanvasの出力差や端末差を確定結果へ持ち込まないためである。

### Application API

- 認証と端末セッション
- ユースケースの実行
- バリデーションと認可
- 版競合の比較交換
- SQLiteとAsset Storageをまたぐ整合性制御
- Generation Agentへの依頼

### Generation Agent

- ComfyUIワークフローの組み立てと実行
- Mac M4・16GB向けの直列実行
- 生出力の回収
- Pillow / NumPyによる厳密保持合成
- 保存後の画素再検証
- 実行時間、条件、エラー、検証値の記録

### ComfyUI

- `127.0.0.1`へ限定して待ち受ける。
- ブラウザやスマホから直接接続させない。
- Character Studioの認証境界として扱わない。

## ローカルからクラウドへの移行

移行時に交換するのはInfrastructure AdapterとGeneration Gatewayの実装に限定する。

```text
Phase 1                         将来
SQLite Adapter          →      PostgreSQL Adapter
Local Filesystem        →      Object Storage Adapter
Local Agent Call        →      Cloud Queue / Lease API
Local Pairing Auth      →      Hosted Identity Adapter
```

Mac側Generation Agentは、将来もComfyUIへlocalhost接続する。クラウド移行後はMacからクラウドへ外向きの認証済み接続を開始し、ジョブをleaseする方式を候補とする。外部からMacやComfyUIへポート転送する方式は採用しない。

## 依存方向

DomainはNext.js、SQLite、Supabase、ComfyUI、Pillowを参照しない。ApplicationはDomainとPortを参照する。Infrastructure AdapterがPortを実装する。

この依存方向により、ローカル試作固有の保存先や通信手段が、版管理や採用規則へ混入するのを防ぐ。

## LAN公開とセキュリティ境界

- Character Studio Web/APIだけをHTTPSでLANへ公開する。
- ComfyUIとGeneration Agentの内部APIはlocalhost限定とする。
- 初回起動時はMac上でオーナーセッションを作る。
- スマホは短時間有効の単回ペアリングコードを入力する。
- ペアリング後は端末別の失効可能なセッションを使用する。
- 画像URLは認証済みAPI経由、または短時間有効な署名付きURL相当で提供する。
- Origin、Host、CSRF、リクエストサイズを検証する。
- HTTPから編集API、Asset、認証情報を提供しない。
- Character Studio専用のローカル認証局をMac上で作成する。
- 認証局の秘密鍵はMacの安全な資格情報領域で保護し、ブラウザやスマホへ配布しない。
- スマホへ配布するのは公開証明書だけとし、初回に信頼設定を行う。
- 安定したLAN内ホスト名をサーバー証明書へ含め、証明書更新時も接続先名を維持する。
- 初回設定時はMac画面とスマホに表示する証明書指紋を照合する。
- 証明書の期限切れ前に更新案内を表示し、サーバー証明書を更新できるようにする。

## 障害分離

- ComfyUIが停止してもWeb、版閲覧、マスク編集、既存Asset表示は継続する。
- Generation Agentが再起動した場合、RUNNINGだった試行を自動成功扱いにしない。
- SQLiteまたはAsset保存失敗時は、確定版の先頭を更新しない。
- 候補採用は、Asset検証とDBトランザクションが完了して初めて成立する。
