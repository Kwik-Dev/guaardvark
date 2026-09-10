---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section {
    background: #0f172a;
    color: #e5e7eb;
  }
  .screenshot {
    position: relative;
    height: 100%;
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 16px;
    overflow: hidden;
    background: #020817;
  }
  .screenshot img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
    border-radius: 12px;
  }
  .screenshot .caption {
    position: absolute;
    left: 20px;
    right: 20px;
    bottom: 18px;
    padding: 12px 14px;
    border-radius: 10px;
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(148, 163, 184, 0.35);
    color: #f8fafc;
    font-size: 18px;
    line-height: 1.35;
    backdrop-filter: blur(2px);
  }
---

# Guaardvark — スクリーンショット機能ガイド

> 1機能・1画面・1ページ

*Guaardvarkの各主要画面をスクリーンショットで紹介するビジュアルウォークスルー。セルフホスト型・オフライン初のAIワークステーション。1機能1ページ、キャプチャ順。*


---

## 1. ダッシュボード

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-.png" alt="Dashboard" />
  <div class="caption">GuaardvarkのダッシュボードはReact/Vite Web UIのランディングページです. システムヘルス、アクティブなジョブ、最近のアクティビティ、そしてチャット・エージェント・メディア生成・ツール・プラグインなどの主要画面へのクイックアクセスをひと目で把握できます. テーマは設定から複数選択可能（Dark Gray、Light、Guaardv...</div>
</div>

---

## 2. チャット

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-chat.png" alt="Chat" />
  <div class="caption">チャット画面はGuaardvarkの主要な会話インターフェースです. すべてのメッセージは3層のAgentBrainルーターを通過します：Reflex（パターンマッチ、100ms未満、LLM呼び出しなし）→ Instinct（単一ショット）→ Deliberation（フルReACTツール使用ループ）. ローカルのOllamaモデル、またはクラウドプロ...</div>
</div>

---

## 3. コードエディタ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-code-editor.png" alt="Code Editor" />
  <div class="caption">ブラウザ上でファイルを直接表示・編集できる組み込みのMonacoコードエディタです. エージェントツールエコシステムと統合しており、AIアシスタントがワークスペース内のファイルを読み書き・パッチできます. 主要言語すべてに対応した構文ハイライトをサポートし、自己改善エンジンが修正提案を表示する画面としても使用されます.</div>
</div>

---

## 4. ドキュメント（RAG）

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-documents.png" alt="Documents" />
  <div class="caption">Documents画面はRAG（検索拡張生成）パイプラインを管理します. CLIから/ingest <path>を実行するか、このUIを使用してファイルやディレクトリをインデックス化します. GuaardvarkはBM25 + ベクターのハイブリッドストアを構築し、AST対応のコードチャンキングとエンティティ抽出を実施します. インデックス化されたドキ...</div>
</div>

---

## 5. 画像生成

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-images.png" alt="Image Generation" />
  <div class="caption">組み込みのDiffusersパイプライン（Z-Image Turbo、SDXL）を使用してオフラインで画像を生成するか、ComfyUIにルーティングしてFLUXなどのエンジンを使用します. プロンプト、解像度、バッチ数、フェイス/解剖学の設定が可能です. Apple Siliconでは、GUAARDVARK_ZIMAGE_USE_COMFYUI=1を設...</div>
</div>

---

## 6. ノート

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-notes.png" alt="Notes" />
  <div class="caption">フリーテキスト、ナレッジキャプチャ、エージェント生成の概要用の永続的なノート画面です. ノートはより広範なメモリシステム - 重要度・信頼度・トラストウェイト・ランキングを持つAgentMemory長期ストア - と統合され、RAGインデックスドキュメントと一緒に検索できます.</div>
</div>

---

## 7. フィルムクルー

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-film-crew.png" alt="Film Crew" />
  <div class="caption">Film CrewはGuaardvarkの代表的な順次メディアパイプラインです. 5つのロールエージェントがログラインを完成動画に変換します：Screenwriter → Casting（LoRA割り当て）→ Cinematographer → Storyboard（キーフレーム生成）→ Editor（タイムライン組み立て）. レンダリングは再開可能...</div>
</div>

---

## 8. キャスト / LoRA管理

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-cast.png" alt="Cast" />
  <div class="caption">Cast画面は、Film CrewのCastingエージェントがロールに割り当てる視覚的アイデンティティ - LoRAモデルやストックキャラクター - を管理します. 映画の全ショットを通して一貫したキャラクター面容を実現するには、パイプライン全体で同じLoRAシードを再利用します. キャラクターライブラリのアップロード、閲覧、整理がここで行えます.</div>
</div>

---

## 9. キャスト詳細

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-cast-1.png" alt="Cast Detail" />
  <div class="caption">単一のキャストメンバー/LORAの詳細ビュー：プレビュー画像、トリガーワード、関連メデータ、および使用された映画/ショット. Film Crew制作に視覚的一貫性をもたらすキャラクターライブラリのキュレーション・リファインを行う画面です.</div>
</div>

---

## 10. ミュージックビデオ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-music-video.png" alt="Music Video" />
  <div class="caption">Music Videoジェネレーターは、生成またはアップロードされたオーディオトラックをWan 2.2 image-to-videoで生成された動画クリップとペアリングします. オーディオ駆動のクリップ選択、ビートに合わせたカット、プロンプト強化を処理します. Film Crewと同様、ComfyUIパイプライン経由でレンダリングされ、中断后再開可能です.</div>
</div>

---

## 11. 動画エディタ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-video-editor.png" alt="Video Editor" />
  <div class="caption">動画・テキスト・オーディオレーンを備えた組み込みのShotcut-liteタイムラインエディタです. クリップのトリム、配置、レイヤー、エクスポートが可能 - Film Crewパイプラインの最終段階であり、任意の動画プロジェクト用のスタンドアロンエディタでもあります. オーバーレイ、トランジション、直接エクスポートをサポートします.</div>
</div>

---

## 12. 動画生成

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-video.png" alt="Video Generation" />
  <div class="caption">Wan 2.2、CogVideoX、LTXを使用してオフラインで動画を生成します. 解像度ティア、フレーム数、デノイズステップ、フレーム補間、プロンプト強化を設定可能. Apple Silicon（MPS）では推奨デフォルトはwan22-5b（約9.5GB VRAM）、重いA14B MoEモデルは16GB CUDAカード向けです.</div>
</div>

---

## 13. バッチ画像生成

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-batch-images.png" alt="Batch Images" />
  <div class="caption">バッチ画像画面は1回のジョブで多数の画像を生成します - キャストキーフレーム、ストーリーボード、一括アセット作成用. プロンプトリスト、解像度、バッチパラメータを定義すると、ジョブはCelery経由で非同期に実行され、画像が完了するたびにメディアライブラリに保存されます.</div>
</div>

---

## 14. オーディオ/音楽生成

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-audio.png" alt="Audio" />
  <div class="caption">Audio Foundryは、ACE-Stepでフル曲を、Stable Audio Open FXで効果音を、Chatterbox、Kokoro、Piperでニューラル音声を生成します. 音声クローンは同意ゲート付きでサポートされています. 出力は完全にオフラインで、ミュージックビデオ、映画、スタンドアロンプレイバック用にローカルメディアライブラリに保...</div>
</div>

---

## 15. 動画テキストオーバーレイ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-video-text-overlay.png" alt="Video Text Overlay" />
  <div class="caption">動画クリップにテキストオーバーレイを追加します - タイトル、キャプション、字幕、スタイリッシュなタイポグラフィ. フォント、サイズ、色、位置、タイミングを設定可能. エクスポート前の最終仕上げステップとして、Video Editorタイムライン内と専用オーバーレイツールの両方で利用できます.</div>
</div>

---

## 16. クライアント

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-clients.png" alt="Clients" />
  <div class="caption">Clients画面は、作業先のクライアントエンティティ - 組織や個人 - を管理します. クライアントはプロジェクト、ルール、ファイル生成ジョブをスコープします. 各クライアントは独自のプロジェクト、優先モデル、ルールオーバーライドを持つことができ、1つのGuaardvarkインスタンスが単一データベースから複数の顧客にサービスを提供できます.</div>
</div>

---

## 17. プロジェクト

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-projects-1.png" alt="Projects" />
  <div class="caption">Projectsはクライアントの下に関連作業をグループ化します. プロジェクトは独自のRAGインデックス、ファイル生成ジョブ、ルール、エージェントコンテキストを持ちます. プロジェクトを切り替えるとエージェントの知識とツールの動作が再スコープされ、会話と生成が現在の作業対象に集中し続けます.</div>
</div>

---

## 18. ウェブサイト

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-websites.png" alt="Websites" />
  <div class="caption">ウェブサイトエンティティ - ドメイン、ホスティング詳細、CMSタイプ、生成テンプレート - を管理します. Websites画面はFileGenバッチコンテンツエンジンにデータを提供し、WordPress対応ページをSEOターゲティングで生成できます. 競合URLのスクレイピング機能により、キーワードや商品を抽出し、同じ検索スペースを狙ったコンテンツ...</div>
</div>

---

## 19. タスク

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-tasks.png" alt="Tasks" />
  <div class="caption">Tasks画面は、システム全体の非同期ジョブ - メディア生成、RAGインデックス、ファイル生成、自己改善実行、アウトリーチ送信 - を一覧表示します. 各タスクはステータス、進捗、所有者を表示します. タスクはCeleryワーカーによって実行され、バックエンドの再起動を生き延びることができます.</div>
</div>

---

## 20. アウトリーチ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-outreach.png" alt="Outreach" />
  <div class="caption">ソーシャルアウトリーチはデフォルトで監視下にあります - 下書きは承認キューに並び、明示的な人間の承認なしには何も投稿されません. プラットフォームごとのケイデンス制限、JSONL監査証跡、ペルソナ強制、グローバルキルスイッチがすべて適用されます. オペレーターのアイデンティティは設定駆動で（ハードコードなし）、複数プラットフォームをサポートします.</div>
</div>

---

## 21. ルール & プロンプト

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-rules.png" alt="Rules" />
  <div class="caption">LLMに何を伝えるかを誘導する宣言型プロンプトバンドル. ルールはレベル（SYSTEM、PROJECT、CLIENT、USER_GLOBAL、USER_SPECIFIC、PROMPT、LEARNED）とタイプ（コマンドルール、QAテンプレート、フィルター、フォーマット、システムプロンプト）によってスコープされます. 一意のcommand_labelを持...</div>
</div>

---

## 22. ツール

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-tools.png" alt="Tools" />
  <div class="caption">ツールレジストリは、Guaardvarkが起動時に登録する約70のBaseToolサブクラスを表示します. 各ツールはカテゴリとフラグ（is_dangerous、requires_approval）を持ち、それらがMCPセキュリティ境界を形成します. Tools画面は、利用可能なもの、承認が必要なもの、MCPサーバーを介して外部エージェントに公開されて...</div>
</div>

---

## 23. エージェント（Agent Vision Control）

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-agents.png" alt="Agents" />
  <div class="caption">Agent画面（Agent Vision Control）は、AIエージェントが実際のデスクトップを見て操作するsee-think-actループを実現します：画面キャプチャ → ビジョンモデルで分析（box_2dクリック座標を出力）→ 判断 → アクション. アクション語彙にはclick、right_click、double_click、drag、ho...</div>
</div>

---

## 24. ファイル生成

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-file-generation.png" alt="File Generation" />
  <div class="caption">FileGenエンジンは、テンプレートとプロンプトから構造化コンテンツ - CSV、JSON、Markdown、WordPressページ - を大規模に生成します. 各ジョブはプロジェクトとクライアントにスコープされ、RAGソースの知識とSEOターゲティング用の競合URLスクレイピングを組み込めます. ジョブはCelery経由で非同期に実行されます.</div>
</div>

---

## 25. スウォーム

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-swarm.png" alt="Swarm" />
  <div class="caption">Swarm Orchestratorは、最大N個のコーディングエージェントを並列実行し、それぞれを分離されたgit worktreeに配置した後、依存関係対応の競合検出とテスト検証でマージします. バックエンド：claude（Claude Code、クラウド）またはcline（ローカル、ollama/gemma4:e4b）. Flight Modeはオ...</div>
</div>

---

## 26. オートサーチ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-autoresearch.png" alt="Autoresearch" />
  <div class="caption">RAG Autoresearchは、インデックス化されたナレッジベースを継続的に改善するバックグラウンドエージェントをスケジュールします - 手動の/ingestなしにドキュメントを発見・インジェスト・チャンキングします. Celery beatジョブとして実行され、ドキュメントコーパスが成長するにつれてRAGストアを最新に保ちます.</div>
</div>

---

## 27. プラグイン

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-plugins.png" alt="Plugins" />
  <div class="caption">Plugin Managerは、GuaardvarkのGPUサービスサイドカーを発見・制御します. 各プラグイン（ComfyUI、Swarm、Discord、Audio Foundryなど）はplugin.jsonマニフェスト（id、ポート、VRAM推定値、エンドポイント）を備えています. マネージャーは各プラグインの起動・停止・ヘルスチェックを行い、...</div>
</div>

---

## 28. コネクション（Interconnector）

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-connections.png" alt="Connections" />
  <div class="caption">Interconnectorは、複数のGuaardvarkインスタンス間でデータを同期します：チャット履歴、学習（自己改善修正）、画像、ファイル、バックアップ、ハードウェアプロファイル. エンティティ/ファイルバッチを承認ゲート付きでブロードキャストし、安全性のディレクティブをプッシュします. これは、ノードごとのHTTP API/MCPと組み合わせる...</div>
</div>

---

## 29. 承認

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-approvals.png" alt="Approvals" />
  <div class="caption">Approvalsキューは、2つの安全性クリティカルなシステムの人間によるゲートです：アウトリーチ下書き（明示的な承認なしには何も投稿されない）と自己改善修正（すべての提案コード変更が適用前にPendingFixとしてレビュー用にステージングされる）. キューは保留中、承認/拒否済み、監査証跡を示します.</div>
</div>

---

## 30. システムマップ

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-system-map.png" alt="System Map" />
  <div class="caption">System MapはGuaardvarkのX線です：依存関係グラフ、到達可能性分析、ツールグラフ、コードベース全体のファインビュー. backend/services/system_mapper/によって生成され、ブラストラウスの権威ある図 - 何が何に依存しているか、何が到達可能か、リスクがどこにあるかを示します. 自己改善や大規模リファクタリング...</div>
</div>

---

## 31. 設定

<div class="screenshot">
  <img src="img/Screenshot%20Capture%20-%20http---localhost-5173-settings.png" alt="Settings" />
  <div class="caption">Settings画面はワークステーション全体のコントロールパネルです：Model Management（マスタースイッチ、プロバイダー選択、Ollama/Mistral/OpenAI互換のモデルドロップダウン）、テーマ選択、MCP設定、Uncle Claudeエスカレーション設定、自己改善トグル（self_improvement_enabled、cod...</div>
</div>

---

*機能ガイド終了。完全なテキストリファレンスは[GUAARDVARK_GUIDE.md](GUAARDVARK_GUIDE.md)、システム設計は[ARCHITECTURE.md](ARCHITECTURE.md)を参照してください。*
