import os
import sqlite3
from datetime import datetime
from pytz import timezone
from flask import Flask, request, abort, render_template_string, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FollowEvent, ImageSendMessage
)
from openai import OpenAI

app = Flask(__name__)

# ==========================================
# 環境変数の読み込み
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://jumprope-bot.onrender.com")
BOOTH_SUPPORT_URL = "https://visai.booth.pm/items/7763380"
LINE_BOT_ID = os.environ.get("LINE_BOT_ID", "@698rtcqz")

# ★ オリジナルスタンプの画像URL（後で設定）
WELCOME_STAMP_URL = os.environ.get("WELCOME_STAMP_URL", "https://example.com/welcome_stamp.png")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    raise ValueError("🚨 必要な環境変数が設定されていません")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

JST = timezone('Asia/Tokyo')

# データベースパス（永続化対応）
# Renderの場合は /data ディレクトリを使用
if os.path.exists('/data'):
    DB_PATH = '/data/rope_users.db'
else:
    # ローカル開発環境
    DB_PATH = os.path.join(os.path.dirname(__file__), "rope_users.db")

# レベル設定
USER_LEVELS = {
    "初心者": {
        "description": "前とび〜三重とび",
        "focus": "基礎安定と成功体験"
    },
    "中級者": {
        "description": "TJ〜SOASレベル",
        "focus": "技の安定とフロー"
    },
    "上級者": {
        "description": "選手レベル",
        "focus": "質・構成・大会意識"
    },
    "超上級者": {
        "description": "EBTJOASレベル",
        "focus": "高難易度技の取得"
    }
}

# コーチの性格設定
COACH_PERSONALITIES = ["熱血", "優しい", "厳しい", "フレンドリー", "冷静"]

# ==========================================
# データベース接続
# ==========================================
def get_db():
    """SQLite接続を取得"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# データベース初期化
# ==========================================
def init_database():
    """テーブルを作成"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nickname TEXT,
                level TEXT NOT NULL DEFAULT '初心者',
                coach_personality TEXT NOT NULL DEFAULT '優しい',
                delivery_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                difficulty_count INTEGER DEFAULT 0,
                support_shown INTEGER DEFAULT 0,
                last_challenge TEXT,
                immediate_request_count INTEGER DEFAULT 0,
                last_immediate_request_date TEXT,
                streak_days INTEGER DEFAULT 0,
                last_challenge_date TEXT,
                received_welcome_stamp INTEGER DEFAULT 0
            )
        ''')

        # 既存テーブルへのカラム追加（必要に応じて）
        columns_to_add = [
            ("nickname", "TEXT"),
            ("last_challenge", "TEXT"),
            ("success_count", "INTEGER DEFAULT 0"),
            ("difficulty_count", "INTEGER DEFAULT 0"),
            ("coach_personality", "TEXT DEFAULT '優しい'"),
            ("immediate_request_count", "INTEGER DEFAULT 0"),
            ("last_immediate_request_date", "TEXT"),
            ("streak_days", "INTEGER DEFAULT 0"),
            ("last_challenge_date", "TEXT"),
            ("received_welcome_stamp", "INTEGER DEFAULT 0")
        ]

        for column_name, column_type in columns_to_add:
            try:
                cursor.execute(f"SELECT {column_name} FROM users LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                print(f"✅ Added {column_name} column")

        conn.commit()
        conn.close()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")

# ==========================================
# ユーザー設定の取得
# ==========================================
def get_user_settings(user_id):
    """ユーザー設定を取得"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT level, nickname, coach_personality, delivery_count, success_count, 
                   difficulty_count, support_shown, last_challenge, streak_days, 
                   last_challenge_date, received_welcome_stamp
            FROM users WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()

        if not row:
            cursor.execute('''
                INSERT INTO users (user_id, level, coach_personality, delivery_count, 
                                 success_count, difficulty_count, support_shown, streak_days,
                                 received_welcome_stamp) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, '初心者', '優しい', 0, 0, 0, 0, 0, 0))
            conn.commit()
            conn.close()
            return {
                'level': '初心者', 'nickname': None, 'coach_personality': '優しい',
                'delivery_count': 0, 'success_count': 0, 'difficulty_count': 0, 
                'support_shown': 0, 'last_challenge': None, 'streak_days': 0,
                'last_challenge_date': None, 'received_welcome_stamp': 0
            }

        result = {
            'level': row['level'],
            'nickname': row['nickname'] if 'nickname' in row.keys() else None,
            'coach_personality': row['coach_personality'] if 'coach_personality' in row.keys() else '優しい',
            'delivery_count': row['delivery_count'],
            'success_count': row['success_count'],
            'difficulty_count': row['difficulty_count'],
            'support_shown': row['support_shown'],
            'last_challenge': row['last_challenge'],
            'streak_days': row['streak_days'] if 'streak_days' in row.keys() else 0,
            'last_challenge_date': row['last_challenge_date'] if 'last_challenge_date' in row.keys() else None,
            'received_welcome_stamp': row['received_welcome_stamp'] if 'received_welcome_stamp' in row.keys() else 0
        }

        conn.close()
        return result

    except Exception as e:
        print(f"❌ get_user_settings error: {e}")
        return {
            'level': '初心者', 'nickname': None, 'coach_personality': '優しい',
            'delivery_count': 0, 'success_count': 0, 'difficulty_count': 0,
            'support_shown': 0, 'last_challenge': None, 'streak_days': 0,
            'last_challenge_date': None, 'received_welcome_stamp': 0
        }

# ==========================================
# ユーザー設定の更新
# ==========================================
def update_user_settings(user_id, level=None, coach_personality=None, nickname=None):
    """レベル、コーチの性格、ニックネームを更新"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        print(f"🔧 Updating settings for {user_id[:8]}...")

        # 現在の設定を取得
        cursor.execute('SELECT level, coach_personality, nickname FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()

        if row:
            # 既存ユーザー: 指定されたフィールドのみ更新
            current_level = level if level is not None else row['level']
            current_personality = coach_personality if coach_personality is not None else row['coach_personality']
            current_nickname = nickname if nickname is not None else row['nickname']

            cursor.execute('''
                UPDATE users 
                SET level = ?, coach_personality = ?, nickname = ?
                WHERE user_id = ?
            ''', (current_level, current_personality, current_nickname, user_id))
        else:
            # 新規ユーザー
            cursor.execute('''
                INSERT INTO users (user_id, level, coach_personality, nickname, delivery_count, 
                                 success_count, difficulty_count, support_shown, streak_days,
                                 received_welcome_stamp)
                VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0)
            ''', (user_id, level or '初心者', coach_personality or '優しい', nickname))

        conn.commit()
        conn.close()
        print(f"✅ Settings saved successfully")

    except Exception as e:
        print(f"❌ update_user_settings error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# 連続記録の更新
# ==========================================
def update_streak(user_id):
    """連続記録を更新（今日課題をもらった場合）"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        today = datetime.now(JST).strftime("%Y-%m-%d")

        cursor.execute('''
            SELECT streak_days, last_challenge_date 
            FROM users WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()

        current_streak = 0
        last_date = None

        if row:
            current_streak = row['streak_days'] or 0
            last_date = row['last_challenge_date']

        # 連続記録の判定
        if last_date == today:
            # 今日すでに課題をもらっている場合は何もしない
            conn.close()
            return current_streak
        elif last_date:
            # 前回の日付をチェック
            last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            today_dt = datetime.strptime(today, "%Y-%m-%d")
            diff_days = (today_dt - last_dt).days

            if diff_days == 1:
                # 連続している場合は+1
                current_streak += 1
            else:
                # 途切れている場合はリセット
                current_streak = 1
        else:
            # 初回の場合
            current_streak = 1

        # データベース更新
        cursor.execute('''
            UPDATE users 
            SET streak_days = ?, last_challenge_date = ?
            WHERE user_id = ?
        ''', (current_streak, today, user_id))

        conn.commit()
        conn.close()

        print(f"✅ Streak updated: {current_streak} days for {user_id[:8]}...")
        return current_streak

    except Exception as e:
        print(f"❌ update_streak error: {e}")
        return 0

# ==========================================
# 配信回数のカウント
# ==========================================
def increment_delivery_count(user_id, challenge_text):
    """配信回数を1増やし、課題を記録"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users 
            SET delivery_count = delivery_count + 1, 
                last_challenge = ? 
            WHERE user_id = ?
        ''', (challenge_text, user_id))

        conn.commit()
        conn.close()
        print(f"✅ Delivery count incremented for {user_id[:8]}...")
    except Exception as e:
        print(f"❌ increment_delivery_count error: {e}")

# ==========================================
# フィードバック記録
# ==========================================
def record_feedback(user_id, is_success):
    """ユーザーのフィードバックを記録（成功/難しかった）"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        if is_success:
            cursor.execute('UPDATE users SET success_count = success_count + 1 WHERE user_id = ?', (user_id,))
        else:
            cursor.execute('UPDATE users SET difficulty_count = difficulty_count + 1 WHERE user_id = ?', (user_id,))

        conn.commit()
        conn.close()
        print(f"✅ Feedback recorded: {'success' if is_success else 'difficulty'}")
    except Exception as e:
        print(f"❌ record_feedback error: {e}")

# ==========================================
# 応援メッセージフラグ
# ==========================================
def mark_support_shown(user_id):
    """応援メッセージを表示済みにする"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET support_shown = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ mark_support_shown error: {e}")

# ==========================================
# ウェルカムスタンプ送信済みフラグ
# ==========================================
def mark_welcome_stamp_sent(user_id):
    """ウェルカムスタンプを送信済みにする"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET received_welcome_stamp = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ mark_welcome_stamp_sent error: {e}")

# ==========================================
# AI課題生成（IJRU対応）
# ==========================================
def generate_challenge_with_ai(level, user_history, coach_personality, streak_days):
    """AIで練習課題を生成（実際の競技技を使用）"""

    # コーチの性格別の口調と特徴を明確に定義
    personality_styles = {
        "熱血": {
            "tone": "熱く励ます。「！」「💪」「🔥」を多用。「お前」「やってやろうぜ」「絶対いけるぞ」などの表現",
            "example": "よっしゃ！今日も全力でいくぞ！🔥"
        },
        "優しい": {
            "tone": "丁寧で優しく。「ですます調」。「ゆっくりでいいよ」「無理しないでね」などの配慮",
            "example": "今日も無理せず、楽しく練習しましょうね😊"
        },
        "厳しい": {
            "tone": "短く厳格に。「だ・である調」。「妥協するな」「できて当然」などの厳しさ",
            "example": "甘えは許さん。やるからには本気でやれ"
        },
        "フレンドリー": {
            "tone": "タメ口で親しみやすく。「！」を適度に。「いこ！」「やろ！」「一緒に頑張ろ」",
            "example": "今日も一緒に楽しく練習しよ！😊"
        },
        "冷静": {
            "tone": "論理的で分析的。「です・ます調」。「データ的に」「効率的に」などの客観的表現",
            "example": "本日の課題を論理的に設計しました"
        }
    }

    current_style = personality_styles.get(coach_personality, personality_styles["優しい"])

    system_prompt = f"""あなたは縄跳びフリースタイル競技のAIコーチです。
実際の競技で使われる技名を使って、具体的な練習課題を出します。

【重要】あなたのコーチとしての性格は「{coach_personality}」です。
この性格を絶対に守ってください。他の性格に変わってはいけません。

【{coach_personality}コーチの口調と特徴】
{current_style["tone"]}
例: {current_style["example"]}

【重要な禁止事項】
- 「フロー」「リカバリー」「クリーンフィニッシュ」という言葉は存在しないので絶対に使わない
- 抽象的な表現は一切使わない
- 必ず具体的な技名を使う
- 指定された性格以外の口調は絶対に使わない

【課題設計の原則】
- 毎日3〜10分で完結する内容
- 成功条件を明確にする（回数・秒数など）
- 技の組み合わせパターンを工夫する
- 前回と違う課題を出す
- 段階的な難度上昇を意識する（「できた」の数が増えれば文字数の長い技を少し増やすなど）
- 技だけでなく、アドバイスや励まし、応援のメッセージも入れる"""

    # 実際の技リスト
    level_guidelines = {
        "初心者": """【初心者向け技リスト】
基本技:
- 前とび
- あやとび
- 交差とび
- 二重とび
- はやぶさ
- リットル（交差二重とび）
- 後ろとび
- 後ろあやとび
- 後ろ交差とび
- 後ろ二重とび
- 三重とび

目標:
- 縄跳びを好きになってもらう
- 初心者にはアドバイスを欠かさずに
- 三重とびの成功
- それぞれの技の連続成功を目指す

課題例:
- 「前とびを10回連続」
- 「交差とびを5回連続」
- 「後ろ二重とびを3回連続」
- 「前とびを10秒間で何回とべるか挑戦してみる」

注意:
- まだEBTJやKNTJは難しすぎる
- 三重とびの完全な安定が最優先""",

        "中級者": """【中級者向け技リスト】
メイン技:
- 三重とび
- トード
- EB
- AS
- CL
- TS
- EBトード
- TJ
- インバースTJ
- EBTJ
- KNTJ
- インバースEBTJ
- インバースKNTJ
- SOAS
- SOCL
- SOTS
- SSCL
- SSTS

目標:
- 縄跳び競技の技を覚えてもらう
- EBTJやSOASなどの技を連続で安定できることを目標にする

【重要な難度ガイドライン】
- 最初は単体練習から始める（例: 三重とびを5回連続）
- 慣れてきたら単発の技（例: トード）
- さらに慣れたら（例: TJやEBTJなど）
- 最終的には（例: EBTJ → KNTJ → SOCL）

【禁止行為】
- 5連続や10連続など多すぎる連続（3連続まで）
- 5回や10回成功させろなどはダメ（3回まで）

課題パターン:
1. 単体練習: 「EBTJを1回」「KNTJを3回」
2. 基本の組み合わせ: 「EBTJ → KNTJ」「三重とび → EBTJ」
3. 3技連続: 「EBTJ → KNTJ → 三重とび」

課題例:
- 「EBTJを安定させて3回」
- 「KNTJ → インバースKNTJ」
- 「SOAS → SOCL」（これはOK）
- 「三重とび → EBTJ → KNTJ」
- 「インバースEBTJを1回成功」

【NG例】
- ❌「EBTJ → KNTJ → SOAS → SOCL」（4連続はNG）
- ❌「AS,CL,TS,EB,トード,EBトード」は連続技に入れてはいけない

注意:
- 速さより安定性
- 段階的に難度を上げる""",

        "上級者": """【上級者向け技リスト】

基本高難度技:
- EBTJ、KNTJ、インバースEBTJ、インバースKNTJ
- SOAS、SOCL、SOTS
- SSCL、SSTS
- SOOAS、SOOCL、SOOTS

O系（Open系）:
- EBTJO、KNTJO
- インバースEBTJO、インバースKNTJO

CL系:
- EBTJCL、KNTJCL
- インバースEBTJCL、インバースKNTJCL

AS系:
- EBTJAS、KNTJAS
- インバースEBTJAS、インバースKNTJAS

TS系:
- EBTJTS、KNTJTS
- インバースEBTJTS、インバースKNTJTS

その他:
- 三重リリース
- リリースOCL
- 四重とび
- 三重とび10回連続
- クルーガーラップ
- EBトードラップ
- ASO
- TS0
- ASCL
- ASTS

室内推奨技:
- ドンキー
- ドンキークロス
- プッシュアップ
- プッシュアップクロス
- カミカゼ
- ロンダートから後ろ二重とび

激ムズ室内推奨技（室内推奨技を全部クリアしてから出すように）
- 後ろドンキー
- 後ろプッシュアップ
- ドンキー二重
- プッシュアップ二重

【重要な難度ガイドライン】
- 最初は基本高難度技の単発から（例: SOOASを1回）
- 慣れてきたら2技連続（例: EBTJ → インバースEBTJ）
- さらに慣れたら3技連続（例: EBTJ → インバースEBTJ → KNTJ）

【禁止の組み合わせ】
- CL系、AS系、TS系は連続に入れない（単発のみ）
- O系は連続に入れる場合は1個まで

【OK例】
- ✅「EBTJ → KNTJ → インバースEBTJ」（基本技のみ）
- ✅「EBTJO → KNTJ」（O系は1個まで）
- ✅「EBTJ → KNTJ → EBTJCL」（CL系は最後に1つ）

【NG例】
- ❌「EBTJO → KNTJO → インバースEBTJO」（O系3連続はNG）
- ❌「EBTJCL → KNTJCL」（CL系連続はNG）
- ❌「EBTJAS → KNTJAS」（AS系連続はNG）
- ❌「EBTJTS → KNTJTS」（TS系連続はNG）

課題パターン:
1. 単体確認: 「SOOASを1回」
2. 基本の組み合わせ: 「EBTJ → インバースEBTJ」
3. 3技連続: 「EBTJ → インバースKNTJ → KNTJ」
4. O系練習: 「EBTJO → KNTJ」（O系は1個）
5. CL/AS/TS系: 「EBTJ → EBTJCL」（最後に1つだけ）

週1回程度の特別課題（その他・室内のみ）:
- 「三重リリースに挑戦」
- 「ドンキーを室内で練習」
- 「プッシュアップを室内で練習」
- 「ロンダートから後ろ二重とびに挑戦」

課題例:
- 初めのうちは「KNTJを安定させて1回」など単発
- 「できた」の回数が増えてきたら「EBTJ → インバースEBTJ」など2連続
- さらに慣れてきたら「EBTJ → KNTJ → SOCL」など3連続
- さらに慣れたら「インバースEBTJO → KNTJ → EBTJCL」など難易度の高い（文字列の長い）3連続
- 「三重リリースに挑戦」（三日に一回程度の特別課題）
- 「ドンキーを室内で練習」（三日に一回程度の特別課題）""",

                  "超上級者": """【超上級者向け技リスト】

基本高難度技:
- EBTJO、KNTJO、インバースEBTJO、インバースKNTJO
- SOOAS、SOOCL、SOOTS

O系（Open系）:
- SEBOOO,EBTJOO、KNTJOO
- インバースEBTJOO、インバースKNTJOO

AS,CL,TS系（基本）:
- SOOOAS,SOOOCL,SOOOTS,SOOASO

四重系AS,CL,TS系
- EBTJAS,EBTJCL,EBTJTS,
インバースEBTJAS,インバースEBTJCL,インバースEBTJTS,
KNTJAS,KNTJCL,KNTJTS,
インバースKNTJAS,インバースKNTJCL,インバースKNTJTS

CL系:
- EBTJOCL、KNTJOCL
- インバースEBTJOCL、インバースKNTJOCL

AS系:
- EBTJOAS、KNTJOAS
- インバースEBTJOAS、インバースKNTJOAS

TS系:
- EBTJOTS、KNTJOTS
- インバースEBTJOTS、インバースKNTJOTS

その他:
- リリースOOCL
- 五重とび
- 四重とび10回連続
- カブースから後ろとび
- カブースから後ろCL
- STSOCL
- SASOCL
- SCLOCL
- SOASOCL
- SOASOAS
- SOCLOCL
- SOTSOCL
- STSOCLO

室内推奨技:
- 後ろドンキー
- 後ろプッシュアップ
- ドンキー二重
- プッシュアップ二重
- ドンキーtoプッシュアップ
- カミカゼ
- ロンダートから後ろOCLO

激ムズ室内推奨技（室内推奨技を全部クリアしてから出すように）
- 後ろドンキーCL
- 後ろプッシュアップCL
- 片手後ろドンキー
- 片手後ろプッシュアップ
- SOASOCL → OCLO → SOCLOCL
- STSOCL → OCL → OCLO → SOTSOCL

【重要な難度ガイドライン】
- 最初は基本高難度技の3連続から（例: SOOAS → KNTJO → インバースEBTJO）
- 慣れてきたらO系やAS,CL,TS系の技連続（例: EBTJOO → SOOASO）
- さらに慣れたらAS系,CL系,TS系などの単発（例: KNTJOAS）

【OK例】
- ✅「EBTJO → KNTJCL → インバースEBTJCL」
- ✅「EBTJOO → KNTJAS」
- ✅「EBTJOCL → SOOAS → EBTJCL → インバースKNTJO → SOOOTS」（慣れるまではダメ）

【NG例】
- ❌「EBTJO → KNTJOCL → インバースEBTJOO → KNTJAS → インバースEBTJCL → SOOCL」（6連続以上はNG）
- ❌「KNTJOCL → インバースEBTJOO → SOOASO → KNTJOO」（AS,CL,TS系（基本）は2個まで、O系も2個まで、AS系、CL系、TS系は1個まで）

課題パターン:
1. 単体確認: 「SOOASOを1回」
2. 基本の組み合わせ: 「EBTJCL → インバースEBTJCL → KNTJTS」
3. 3技連続: 「EBTJ → インバースKNTJ → KNTJ」
4. O系練習: 「EBTJOO → KNTJCL → SOOOCL」（O系は1個）
5. CL/AS/TS系: 「EBTJOCL → EBTJO → KNTJCL」（1つだけ）

週1回程度の特別課題（その他・室内のみ）:
- 「リリースOOCLに挑戦」
- 「後ろドンキーを室内で練習」
- 「後ろSOASOCLを練習」
- 「ロンダートから後ろOCLOに挑戦」"""
    }

    # ユーザー履歴の分析（直近3回のフィードバックを重視）
    success_rate = 0
    difficulty_rate = 0

    if user_history['delivery_count'] > 0:
        # 全体の成功率
        success_rate = user_history['success_count'] / user_history['delivery_count']
        difficulty_rate = user_history['difficulty_count'] / user_history['delivery_count']

    adjustment = ""
    if user_history['delivery_count'] >= 2:
        # 直近2回以上のデータがある場合
        if success_rate > 0.7:
            adjustment = "【重要】ユーザーは非常に好調です（成功率70%以上）。難度を1段階上げてください。\n単発→2技連続、2技連続→3技連続、など。ただし急激に上げすぎない。"
        elif difficulty_rate > 0.6:
            adjustment = "【重要】ユーザーは苦戦中です（難しかった率60%以上）。難度を1〜2段階下げてください。\n3技連続→2技連続、2技連続→単発、など。確実にできるレベルに戻す。"
        elif success_rate > 0.4 and difficulty_rate <= 0.4:
            adjustment = "ユーザーは順調です。現在の難度を維持してください（同じレベルで違うバリエーション）。"
        else:
            adjustment = "ユーザーの状況は中間です。少しだけ難度を下げるか、同じレベルの別パターンを試してください。"

    # 10日ごとの特別課題判定（採点アプリ）- 10日から100日まで
    is_special_day = (streak_days > 0 and streak_days % 10 == 0 and streak_days <= 100)

    special_challenge_reminder = ""
    if is_special_day:
        special_challenge_reminder = f"\n\n【重要】今日は連続記録{streak_days}日目の節目です。通常の課題を出した後、採点アプリでのチャレンジを追加してください。段階的に難度が上がる特別課題を用意しています。"

    # プロンプト生成
    user_prompt = f"""今日の練習課題を1つ生成してください。

【ユーザー情報】
レベル: {level}
コーチの性格: {coach_personality}
連続記録: {streak_days}日目
配信回数: {user_history['delivery_count']}回
成功回数: {user_history['success_count']}回
難しかった回数: {user_history['difficulty_count']}回
成功率: {success_rate:.1%}
難しかった率: {difficulty_rate:.1%}
前回の課題: {user_history.get('last_challenge', 'なし')}

【難度調整指示】
{adjustment}
{special_challenge_reminder}

{level_guidelines[level]}

【出力形式】
必ず以下の形式で、{coach_personality}の性格を100%反映した口調で出力してください：

今日のお題：
（具体的な技名を使った課題。1〜2文で完結。）

（励ましや応援のメッセージを1〜2文で追加。{coach_personality}の性格を強く反映させる）

【出力例（{coach_personality}コーチ）】
{current_style["example"]}

【絶対に禁止】
- 「フロー」「リカバリー」「クリーンフィニッシュ」は存在しない言葉なので使用禁止
- 「基礎技」「難しい技」などの抽象的表現は絶対NG
- 前回と全く同じ課題は避ける
- "###"や"**"は使わない
- 採点アプリへのリンクは含めない（別途表示されます）
- 指定された性格（{coach_personality}）以外の口調は絶対に使わない"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_completion_tokens=400,
            temperature=0.7
        )
        challenge_text = response.choices[0].message.content.strip()

        # 10日ごとの特別課題（採点アプリ）- 段階的にレベルアップ
        if is_special_day and streak_days <= 100:
            # 連続記録に応じた課題設定
            special_challenges = {
                10: {
                    "duration": "15秒",
                    "target": "3点超え",
                    "message": "まずは15秒のフリースタイルを作ってみよう！"
                },
                20: {
                    "duration": "30秒",
                    "target": "5点超え",
                    "message": "少し長めの30秒に挑戦！技のバリエーションを増やそう！"
                },
                30: {
                    "duration": "30秒",
                    "target": "6点超え",
                    "message": "30秒で6点を目指そう！質を意識して！"
                },
                40: {
                    "duration": "45秒",
                    "target": "7点超え",
                    "message": "45秒のフリースタイル！構成力が試されるよ！"
                },
                50: {
                    "duration": "60秒",
                    "target": "8点超え",
                    "message": "1分間のフリースタイル！スタミナと技術の両立！"
                },
                60: {
                    "duration": "60秒",
                    "target": "9点超え",
                    "message": "1分で9点！大会レベルに近づいてきた！"
                },
                70: {
                    "duration": "75秒",
                    "target": "9点超え",
                    "message": "ついに大会と同じ75秒！本番さながらの緊張感を！"
                },
                80: {
                    "duration": "75秒",
                    "target": "9.5点超え",
                    "message": "75秒で9.5点！完成度を極めよう！"
                },
                90: {
                    "duration": "75秒",
                    "target": "10点超え",
                    "message": "10点の壁に挑戦！完璧な演技を目指して！"
                },
                100: {
                    "duration": "75秒",
                    "target": "10点超え",
                    "message": "🎊100日達成おめでとう！！🎊 最高峰の演技で有終の美を飾ろう！"
                }
            }
            
            challenge_info = special_challenges.get(streak_days)
            if challenge_info:
                challenge_text += (
                    f"\n\n🎉 連続記録{streak_days}日目達成！特別課題！\n"
                    "📊 採点アプリで挑戦！\n"
                    "→ 採点アプリ: https://jumprope-scorer.netlify.app\n"
                    "→ 使い方: https://official-jumprope-scorer.netlify.app\n\n"
                    f"【今回の課題】\n"
                    f"{challenge_info['duration']}のフリースタイルを作って最終得点{challenge_info['target']}を目指そう！\n"
                    f"（プレゼンテーションは0.6、ミスとリクワイヤードエレメンツの減点も含む）\n\n"
                    f"💬 {challenge_info['message']}"
                )

        return challenge_text

    except Exception as e:
        print(f"❌ OpenAI API Error: {e}")
        # フォールバック課題（性格に応じて変える）
        fallback_by_personality = {
            "熱血": {
                "初心者": "今日のお題：\n三重とび3回連続！\n\n絶対いけるぞ！お前の力を信じてる！💪🔥",
                "中級者": "今日のお題：\nEBTJ → KNTJ！\n\nやってやろうぜ！全力でぶつかれ！🔥",
                "上級者": "今日のお題：\nSOOAS → SOOCL！\n\nお前ならできる！限界突破だ！✨💪"
            },
            "優しい": {
                "初心者": "今日のお題：\n三重とびを3回連続。\n\nゆっくりでいいので、焦らず練習しましょうね😊",
                "中級者": "今日のお題：\nEBTJを5回。\n\n無理しないでくださいね。少しずつ上達していきましょう💪",
                "上級者": "今日のお題：\nSOOASを1回。\n\n質を大切に、丁寧に練習してみてください✨"
            },
            "厳しい": {
                "初心者": "今日のお題：\n三重とび5回連続。\n\nできて当然だ。甘えるな。",
                "中級者": "今日のお題：\nKNTJ → インバースKNTJ。\n\n妥協するな。完璧を目指せ。",
                "上級者": "今日のお題：\nSOOAS → SOOTS。\n\nできるまでやれ。結果が全てだ。"
            },
            "フレンドリー": {
                "初心者": "今日のお題：\n三重とび3回連続いってみよ！\n\n楽しくやろ！一緒に頑張ろ！✨😊",
                "中級者": "今日のお題：\nEBTJ → KNTJ やろ！\n\n一緒に頑張ろ！絶対できるって！💪",
                "上級者": "今日のお題：\nSOOASいい感じで決めちゃお！\n\nお前ならいけるって！信じてる！🔥"
            },
            "冷静": {
                "初心者": "今日のお題：\n三重とび3回。\n\n安定性を重視して、効率的な動作を心がけてください。",
                "中級者": "今日のお題：\nEBTJ 5回。\n\n動作の効率性を分析しながら練習してください。",
                "上級者": "今日のお題：\nSOOAS 1回。\n\n質を分析し、データ的に最適な動作を目指してください。"
            }
        }
        personality_fallback = fallback_by_personality.get(coach_personality, fallback_by_personality["優しい"])
        return personality_fallback.get(level, personality_fallback["初心者"])


def create_challenge_message(user_id, level):
    """練習課題メッセージを作成"""
    try:
        settings = get_user_settings(user_id)
        coach_personality = settings.get('coach_personality', '優しい')
        
        # 連続記録を更新
        streak_days = update_streak(user_id)
        
        challenge = generate_challenge_with_ai(level, settings, coach_personality, streak_days)

        increment_delivery_count(user_id, challenge)

        # 連続記録のメッセージは削除
        return challenge
    except Exception as e:
        print(f"❌ create_challenge_message error: {e}")
        return "今日のお題：\n前とび30秒を安定させてみよう！"

# ==========================================
# ランキングデータ取得
# ==========================================
def get_ranking_data():
    """全ユーザーのランキングデータを取得"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # user_idでグループ化して重複を防ぐ
        # ニックネームが空文字列の場合も'名無しのジャンパー'として扱う
        cursor.execute('''
            SELECT 
                CASE 
                    WHEN nickname IS NULL OR nickname = '' THEN '名無しのジャンパー'
                    ELSE nickname
                END as display_nickname,
                streak_days, 
                level, 
                last_challenge_date
            FROM users
            WHERE streak_days > 0
            ORDER BY streak_days DESC, last_challenge_date DESC
            LIMIT 100
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        ranking = []
        for row in rows:
            ranking.append({
                'nickname': row['display_nickname'],
                'streak_days': row['streak_days'],
                'level': row['level'],
                'last_challenge_date': row['last_challenge_date']
            })
        
        return ranking
    except Exception as e:
        print(f"❌ get_ranking_data error: {e}")
        return []

# ==========================================
# Flask Routes
# ==========================================
@app.route("/")
def index():
    """ヘルスチェック用エンドポイント"""
    return "Jump Rope AI Coach Bot Running ✅"

@app.route("/ranking")
def ranking():
    """ランキングページ - 落ち着いたデザイン"""
    ranking_data = get_ranking_data()
    
    html = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>連続記録ランキング - なわ太コーチ</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            color: #2c3e50;
            margin-bottom: 40px;
            padding-top: 20px;
        }
        
        .header h1 {
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #1a202c;
        }
        
        .header p {
            font-size: 14px;
            color: #718096;
        }
        
        .refresh-container {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .refresh-btn {
            background: #4a5568;
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s ease;
        }
        
        .refresh-btn:hover {
            background: #2d3748;
        }
        
        .refresh-btn:active {
            transform: scale(0.98);
        }
        
        .podium {
            display: flex;
            justify-content: center;
            align-items: flex-end;
            gap: 12px;
            margin-bottom: 40px;
        }
        
        .podium-item {
            background: white;
            border-radius: 12px;
            padding: 20px 16px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            border: 1px solid #e2e8f0;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        
        .podium-item:hover {
            transform: translateY(-4px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }
        
        .podium-1 {
            order: 2;
            width: 160px;
            border-top: 3px solid #f59e0b;
        }
        
        .podium-2 {
            order: 1;
            width: 140px;
            border-top: 3px solid #9ca3af;
        }
        
        .podium-3 {
            order: 3;
            width: 140px;
            border-top: 3px solid #cd7f32;
        }
        
        .medal {
            font-size: 36px;
            margin-bottom: 8px;
            display: block;
        }
        
        .podium-nickname {
            font-size: 14px;
            font-weight: 600;
            color: #2d3748;
            margin-bottom: 8px;
            word-break: break-word;
            line-height: 1.4;
        }
        
        .podium-streak {
            font-size: 24px;
            font-weight: 700;
            color: #1a202c;
            margin-bottom: 4px;
        }
        
        .podium-label {
            font-size: 12px;
            color: #718096;
        }
        
        .ranking-list {
            background: white;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            border: 1px solid #e2e8f0;
        }
        
        .ranking-title {
            font-size: 18px;
            font-weight: 600;
            color: #1a202c;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 2px solid #e2e8f0;
        }
        
        .ranking-item {
            display: flex;
            align-items: center;
            padding: 14px 12px;
            border-bottom: 1px solid #f7fafc;
            transition: background 0.2s ease;
        }
        
        .ranking-item:hover {
            background: #f7fafc;
            border-radius: 8px;
        }
        
        .ranking-item:last-child {
            border-bottom: none;
        }
        
        .rank-number {
            font-size: 16px;
            font-weight: 700;
            width: 40px;
            text-align: center;
            color: #4a5568;
        }
        
        .user-info {
            flex: 1;
            padding: 0 16px;
        }
        
        .user-nickname {
            font-size: 13px;
            font-weight: 600;
            color: #2d3748;
            margin-bottom: 2px;
        }
        
        .user-level {
            font-size: 11px;
            color: #a0aec0;
        }
        
        .streak-badge {
            background: #edf2f7;
            color: #2d3748;
            padding: 6px 14px;
            border-radius: 16px;
            font-size: 13px;
            font-weight: 600;
        }
        
        .fire-emoji {
            margin-right: 2px;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #a0aec0;
        }
        
        .empty-state-icon {
            font-size: 64px;
            margin-bottom: 16px;
            opacity: 0.5;
        }
        
        .empty-state h3 {
            font-size: 18px;
            color: #4a5568;
            margin-bottom: 8px;
        }
        
        .empty-state p {
            font-size: 14px;
        }
        
        @media (max-width: 600px) {
            .header h1 {
                font-size: 24px;
            }
            
            .podium {
                flex-direction: column;
                align-items: center;
            }
            
            .podium-item {
                width: 100% !important;
                max-width: 280px;
            }
            
            .podium-1 {
                order: 1;
            }
            
            .podium-2 {
                order: 2;
            }
            
            .podium-3 {
                order: 3;
            }
            
            .user-nickname {
                font-size: 12px;
            }
            
            .podium-nickname {
                font-size: 13px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔥 連続記録ランキング</h1>
            <p>なわ太コーチ - 毎日練習を続けているユーザー</p>
        </div>
        
        <div class="refresh-container">
            <button class="refresh-btn" onclick="location.reload()">🔄 最新に更新</button>
        </div>
        
        {% if ranking_data|length >= 3 %}
        <div class="podium">
            <div class="podium-item podium-2">
                <span class="medal">🥈</span>
                <div class="podium-nickname">{{ ranking_data[1]['nickname'] }}</div>
                <div class="podium-streak">{{ ranking_data[1]['streak_days'] }}</div>
                <div class="podium-label">日連続</div>
            </div>
            <div class="podium-item podium-1">
                <span class="medal">🥇</span>
                <div class="podium-nickname">{{ ranking_data[0]['nickname'] }}</div>
                <div class="podium-streak">{{ ranking_data[0]['streak_days'] }}</div>
                <div class="podium-label">日連続</div>
            </div>
            <div class="podium-item podium-3">
                <span class="medal">🥉</span>
                <div class="podium-nickname">{{ ranking_data[2]['nickname'] }}</div>
                <div class="podium-streak">{{ ranking_data[2]['streak_days'] }}</div>
                <div class="podium-label">日連続</div>
            </div>
        </div>
        {% endif %}
        
        <div class="ranking-list">
            <div class="ranking-title">全ユーザーランキング</div>
            {% if ranking_data|length > 0 %}
                {% for user in ranking_data %}
                <div class="ranking-item">
                    <div class="rank-number">{{ loop.index }}</div>
                    <div class="user-info">
                        <div class="user-nickname">{{ user['nickname'] }}</div>
                        <div class="user-level">{{ user['level'] }}</div>
                    </div>
                    <div class="streak-badge">
                        <span class="fire-emoji">🔥</span>{{ user['streak_days'] }}日
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📊</div>
                    <h3>まだランキングデータがありません</h3>
                    <p>連続記録を達成したユーザーがここに表示されます</p>
                </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""
    
    return render_template_string(html, ranking_data=ranking_data)

@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """設定画面 - ニックネーム設定追加"""
    try:
        user_id = request.args.get('user_id')

        if not user_id:
            return """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>エラー</title>
                <style>
                    body {
                        font-family: -apple-system, sans-serif;
                        background: linear-gradient(135deg, #667eea, #764ba2);
                        min-height: 100vh;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 20px;
                    }
                    .container {
                        background: white;
                        padding: 40px 30px;
                        border-radius: 16px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        text-align: center;
                        max-width: 400px;
                    }
                    h2 { color: #e74c3c; margin-bottom: 15px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h2>⚠️ エラー</h2>
                    <p>ユーザーIDが見つかりません。<br>LINEから再度アクセスしてください。</p>
                </div>
            </body>
            </html>
            """, 400

        if request.method == 'POST':
            new_level = request.form.get('level')
            new_personality = request.form.get('coach_personality', '優しい')
            new_nickname = request.form.get('nickname', '').strip()

            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n⚙️ [{timestamp}] Settings update POST received")
            print(f"   User ID: {user_id[:8]}...")
            print(f"   Form data: level={new_level}, personality={new_personality}, nickname={new_nickname}")

            # ニックネームの長さ制限（10文字まで）
            if new_nickname and len(new_nickname) > 10:
                new_nickname = new_nickname[:10]

            update_user_settings(user_id, level=new_level, coach_personality=new_personality, nickname=new_nickname)

            ranking_url = f"{APP_PUBLIC_URL}/ranking"
            
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>設定完了</title>
                <style>
                    body {{
                        font-family: -apple-system, sans-serif;
                        background: linear-gradient(135deg, #667eea, #764ba2);
                        min-height: 100vh;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 20px;
                    }}
                    .container {{
                        background: white;
                        padding: 50px 30px;
                        border-radius: 16px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        text-align: center;
                        max-width: 400px;
                        animation: slideIn 0.4s ease-out;
                    }}
                    @keyframes slideIn {{
                        from {{ opacity: 0; transform: translateY(-20px); }}
                        to {{ opacity: 1; transform: translateY(0); }}
                    }}
                    .success-icon {{
                        width: 80px;
                        height: 80px;
                        background: #00B900;
                        border-radius: 50%;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        margin: 0 auto 25px;
                        font-size: 45px;
                        color: white;
                    }}
                    h2 {{ color: #333; margin-bottom: 20px; font-size: 26px; }}
                    p {{ color: #666; font-size: 18px; line-height: 1.8; }}
                    .back-notice {{
                        margin-top: 30px;
                        padding: 15px;
                        background: #f8f9fa;
                        border-radius: 8px;
                        color: #555;
                        font-size: 15px;
                    }}
                    .ranking-link {{
                        display: inline-block;
                        margin-top: 20px;
                        padding: 12px 25px;
                        background: linear-gradient(135deg, #667eea, #764ba2);
                        color: white;
                        text-decoration: none;
                        border-radius: 8px;
                        font-weight: 600;
                        transition: all 0.3s ease;
                    }}
                    .ranking-link:hover {{
                        transform: translateY(-2px);
                        box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success-icon">✓</div>
                    <h2>設定を保存しました！</h2>
                    <p>「今すぐ」と送信すると課題が届きます。</p>
                    <a href="{ranking_url}" class="ranking-link">🔥 ランキングを見る</a>
                    <div class="back-notice">LINEの画面に戻ってください</div>
                </div>
            </body>
            </html>
            """

        current_settings = get_user_settings(user_id)
        current_nickname = current_settings.get('nickname', '')

        # レベルのオプション生成
        level_options = ''
        for level_name, level_info in USER_LEVELS.items():
            selected = 'selected' if level_name == current_settings['level'] else ''
            level_options += f'<option value="{level_name}" {selected}>{level_name}（{level_info["description"]}）</option>'

        # コーチの性格のオプション生成
        personality_options = ''
        current_personality = current_settings.get('coach_personality', '優しい')

        for personality_name in COACH_PERSONALITIES:
            selected = 'selected' if personality_name == current_personality else ''
            personality_options += f'<option value="{personality_name}" {selected}>{personality_name}</option>'

        ranking_url = f"{APP_PUBLIC_URL}/ranking"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>練習設定 - なわ太コーチ</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: -apple-system, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 20px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .container {{
                    max-width: 420px;
                    width: 100%;
                    background: white;
                    padding: 35px 30px;
                    border-radius: 20px;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                    animation: fadeIn 0.5s ease-out;
                }}
                @keyframes fadeIn {{
                    from {{ opacity: 0; transform: translateY(20px); }}
                    to {{ opacity: 1; transform: translateY(0); }}
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                }}
                .header-icon {{ font-size: 48px; margin-bottom: 10px; }}
                h2 {{
                    color: #2c3e50;
                    font-size: 24px;
                    font-weight: 600;
                    margin-bottom: 8px;
                }}
                .subtitle {{ color: #7f8c8d; font-size: 14px; }}
                .current-settings {{
                    background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                    padding: 15px;
                    border-radius: 12px;
                    margin-bottom: 25px;
                    color: white;
                    font-size: 14px;
                    text-align: center;
                }}
                .current-settings strong {{ font-weight: 600; }}
                .form-group {{ margin-bottom: 25px; }}
                label {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    color: #2c3e50;
                    font-weight: 600;
                    font-size: 15px;
                    margin-bottom: 10px;
                }}
                .label-icon {{ font-size: 18px; }}
                select, input[type="text"] {{
                    width: 100%;
                    padding: 14px 16px;
                    font-size: 16px;
                    border: 2px solid #e0e0e0;
                    border-radius: 12px;
                    background-color: #f8f9fa;
                    transition: all 0.3s ease;
                    font-family: inherit;
                }}
                select {{
                    cursor: pointer;
                    appearance: none;
                    background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
                    background-repeat: no-repeat;
                    background-position: right 12px center;
                    background-size: 20px;
                    padding-right: 40px;
                }}
                select:focus, input[type="text"]:focus {{
                    outline: none;
                    border-color: #667eea;
                    background-color: white;
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                }}
                .nickname-hint {{
                    font-size: 12px;
                    color: #7f8c8d;
                    margin-top: 5px;
                }}
                button {{
                    width: 100%;
                    padding: 16px;
                    background: linear-gradient(135deg, #00B900 0%, #00a000 100%);
                    color: white;
                    border: none;
                    border-radius: 12px;
                    font-size: 17px;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    box-shadow: 0 4px 15px rgba(0, 185, 0, 0.3);
                    margin-top: 10px;
                }}
                button:hover {{
                    background: linear-gradient(135deg, #00a000 0%, #008f00 100%);
                    transform: translateY(-2px);
                    box-shadow: 0 6px 20px rgba(0, 185, 0, 0.4);
                }}
                button:active {{ transform: translateY(0); }}
                .divider {{
                    height: 1px;
                    background: linear-gradient(to right, transparent, #e0e0e0, transparent);
                    margin: 25px 0;
                }}
                .ranking-link {{
                    display: block;
                    text-align: center;
                    margin-top: 15px;
                    padding: 12px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    color: white;
                    text-decoration: none;
                    border-radius: 10px;
                    font-weight: 600;
                    transition: all 0.3s ease;
                }}
                .ranking-link:hover {{
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="header-icon">🏋️</div>
                    <h2>練習設定</h2>
                    <p class="subtitle">レベルとコーチの性格を設定できます</p>
                </div>
                <div class="current-settings">
                    現在の設定: <strong>{current_settings['level']}</strong>レベル（<strong>{current_personality}</strong>コーチ）<br>
                    ニックネーム: <strong>{current_nickname or '未設定'}</strong>
                </div>
                <form method="POST">
                    <div class="form-group">
                        <label>
                            <span class="label-icon">👤</span>
                            ニックネーム（ランキング表示用）
                        </label>
                        <input type="text" name="nickname" value="{current_nickname}" maxlength="10" placeholder="例: ジャンプ太郎">
                        <div class="nickname-hint">※ランキングに表示されます（10文字まで）</div>
                    </div>
                    <div class="divider"></div>
                    <div class="form-group">
                        <label>
                            <span class="label-icon">🎯</span>
                            レベル
                        </label>
                        <select name="level">
                            {level_options}
                        </select>
                    </div>
                    <div class="divider"></div>
                    <div class="form-group">
                        <label>
                            <span class="label-icon">😊</span>
                            コーチの性格
                        </label>
                        <select name="coach_personality">
                            {personality_options}
                        </select>
                    </div>
                    <button type="submit">💾 設定を保存する</button>
                </form>
                <a href="{ranking_url}" class="ranking-link">🔥 ランキングを見る</a>
            </div>
        </body>
        </html>
        """

        return render_template_string(html)

    except Exception as e:
        print(f"❌ Settings page error: {e}")
        import traceback
        traceback.print_exc()
        return f"Internal Server Error: {str(e)}", 500

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhook"""
    try:
        signature = request.headers.get("X-Line-Signature")
        body = request.get_data(as_text=True)

        webhook_handler.handle(body, signature)
        return "OK"
    except InvalidSignatureError:
        print(f"❌ Invalid signature")
        abort(400)
    except Exception as e:
        print(f"❌ Callback error: {e}")
        import traceback
        traceback.print_exc()
        return "OK"

@webhook_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """LINEメッセージを受信したときの処理"""
    try:
        user_id = event.source.user_id
        text = event.message.text.strip()
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        print(f"💬 [{timestamp}] Message from {user_id[:8]}...: '{text}'")

        # 初回ユーザーチェック（配信回数が0の場合）
        settings = get_user_settings(user_id)
        if settings['delivery_count'] == 0 and text not in ["設定", "今すぐ", "できた", "難しかった", "友だちに紹介する", "ランキング"]:
            welcome_text = (
                "こんにちは！なわ太コーチです！\n\n"
                "このBotは毎日あなたのレベルに合った練習課題をお届けします。\n\n"
                "📝 まずは設定から始めましょう：\n"
                "「設定」と送信して、レベル・コーチの性格・ニックネームを設定してください。\n\n"
                "💡 または今すぐ試したい場合は：\n"
                "「今すぐ」と送信してください！\n\n"
                "【レベルについて】\n"
                "・初心者：前とび〜三重とび\n"
                "・中級者：三重とび連続〜SOAS\n"
                "・上級者：競技フリースタイル選手\n\n"
                "・超上級者：EBTJOASなど高難易度技"
                "【コーチの性格】\n"
                "・熱血：情熱的な励まし\n"
                "・優しい：丁寧で穏やか\n"
                "・厳しい：ストイックに\n"
                "・フレンドリー：タメ口で親しみやすく\n"
                "・冷静：論理的で分析的\n\n"
                "🔥 毎日「今すぐ」を送って連続記録を伸ばそう！"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))
            print(f"👋 [{timestamp}] Welcome message sent to new user")
            return

        # 設定画面へのリンクを送信
        if text == "設定":
            settings_url = f"{APP_PUBLIC_URL}/settings?user_id={user_id}"
            reply_text = (
                "⚙️ 設定\n"
                "以下のリンクからレベル、コーチの性格、ニックネームを変更できます。\n\n"
                f"{settings_url}\n\n"
                "※リンクを知っている人は誰でも設定を変更できてしまうため、他人に教えないでください。"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"⚙️ [{timestamp}] Settings link sent")
            return

        # ランキングページへのリンクを送信
        if text == "ランキング":
            ranking_url = f"{APP_PUBLIC_URL}/ranking"
            reply_text = (
                "🔥 連続記録ランキング\n\n"
                "全ユーザーの連続記録ランキングを見ることができます！\n\n"
                f"{ranking_url}\n\n"
                "💡 ニックネームは「設定」から変更できます。"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"🏆 [{timestamp}] Ranking link sent")
            return

        # 今すぐ課題を配信（1日3回まで、replyで即座に返信）
        if text == "今すぐ":
            # 今日の日付を取得
            today = datetime.now(JST).strftime("%Y-%m-%d")

            # 今日の即時配信回数をチェック
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT immediate_request_count, last_immediate_request_date 
                FROM users WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()

            immediate_count = 0
            last_request_date = None

            if row:
                immediate_count = row['immediate_request_count'] or 0
                last_request_date = row['last_immediate_request_date']

            # 日付が変わっていたらカウントをリセット
            if last_request_date != today:
                immediate_count = 0
                cursor.execute('''
                    UPDATE users 
                    SET immediate_request_count = 0, last_immediate_request_date = ?
                    WHERE user_id = ?
                ''', (today, user_id))
                conn.commit()

            conn.close()

            # 1日3回までの制限チェック
            if immediate_count >= 3:
                reply_text = (
                    "⚠️ 本日の「今すぐ」は3回まで利用できます。\n\n"
                    "すでに3回使用済みです。\n"
                    "明日またお試しください！\n\n"
                    "💡 毎日続けて連続記録を伸ばそう🔥"
                )
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                print(f"🚫 [{timestamp}] Immediate delivery limit reached for {user_id[:8]}...")
                return

            # カウントを増やす
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET immediate_request_count = ?, last_immediate_request_date = ?
                WHERE user_id = ?
            ''', (immediate_count + 1, today, user_id))
            conn.commit()
            conn.close()

            print(f"🚀 [{timestamp}] Immediate delivery requested by {user_id[:8]}... ({immediate_count + 1}/3 today)")

            # 課題を生成してreplyで返信
            challenge_content = create_challenge_message(user_id, settings['level'])

            # フィードバック促進を課題に追加
            full_message = challenge_content + "\n\n💬 フィードバック\n「できた」「難しかった」と送ると、次回の課題が調整されます！"

            messages = [TextSendMessage(text=full_message)]

            # 応援メッセージ（10回配信後、1回だけ）
            if settings['delivery_count'] >= 3 and settings['support_shown'] == 0:
                support_message = (
                    "いつも練習お疲れ様です！🙏\n\n"
                    "このなわ太コーチは個人開発で、サーバー代やAI利用料を自腹で運営しています。\n\n"
                    "もし応援していただけるなら、100円の応援PDFをBoothに置いています。\n"
                    "無理はしないでください🙏\n\n"
                    f"↓応援はこちらから\n{BOOTH_SUPPORT_URL}"
                )
                messages.append(TextSendMessage(text=support_message))
                mark_support_shown(user_id)
                print(f"💝 [{timestamp}] Support message added")

            line_bot_api.reply_message(event.reply_token, messages)
            print(f"✅ [{timestamp}] Challenge sent via reply")
            return

        # フィードバック: 成功
        if text in ["できた", "成功", "できました", "クリア", "達成"]:
            record_feedback(user_id, is_success=True)

            # コーチの性格に応じた褒め言葉
            personality = settings.get('coach_personality', '優しい')
            praise_by_personality = {
                "熱血": "素晴らしい！！その調子だ！🔥 次回はもっと難しい技にチャレンジだ！💪",
                "優しい": "素晴らしい！💪 次回の課題で少しレベルアップしますね。無理せず頑張りましょう✨",
                "厳しい": "まだまだこれからだ。次はもっと高みを目指せ。",
                "フレンドリー": "やばい！すごいじゃん！✨ 次もこの調子でいこ！一緒に頑張ろ！",
                "冷静": "データ的に良好です。次回は難度を0.2段階上げます。継続してください。"
            }
            reply_text = praise_by_personality.get(personality, praise_by_personality["優しい"])

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"✅ [{timestamp}] Success feedback recorded")
            return

        # フィードバック: 難しかった
        if text in ["難しかった", "できなかった", "無理", "難しい", "厳しい"]:
            record_feedback(user_id, is_success=False)

            # コーチの性格に応じた励まし
            personality = settings.get('coach_personality', '優しい')
            encouragement_by_personality = {
                "熱血": "大丈夫だ！お前ならできる！🔥 次回は少し軽めにするから、絶対いけるぞ！💪",
                "優しい": "大丈夫！次回は少し軽めの課題にしますね。焦らず続けましょう🙌 ゆっくりでいいからね",
                "厳しい": "できなかったか。次回は少し戻すが、すぐにまた挑戦してもらう。諦めるな。",
                "フレンドリー": "大丈夫大丈夫！次は少し軽くするね。焦らずいこ！一緒に頑張ろ😊",
                "冷静": "難度設定を調整します。次回は0.3段階下げて再トライしてください。"
            }
            reply_text = encouragement_by_personality.get(personality, encouragement_by_personality["優しい"])

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"⚠️ [{timestamp}] Difficulty feedback recorded")
            return

        # 友だちに紹介する機能
        if text in ["友だちに紹介する", "友達に紹介する", "紹介"]:
            line_add_url = f"https://line.me/R/ti/p/{LINE_BOT_ID}"
            reply_text = (
                "📢 友だちに紹介\n\n"
                "なわ太コーチを友だちに紹介していただきありがとうございます！\n\n"
                "以下のリンクを友だちに転送してください👇\n\n"
                f"🔗 友だち追加リンク\n{line_add_url}\n\n"
                "💡 紹介してくれると開発の励みになります！"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"👥 [{timestamp}] Friend referral sent")
            return

        # デフォルトのヘルプメニュー
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=(
                "💡メニュー\n"
                "・「今すぐ」: 今すぐ課題を受信（1日3回まで）\n"
                "・「設定」: レベルやコーチの性格、ニックネームを変更\n"
                "・「ランキング」: 連続記録ランキングを見る\n"
                "・「できた」「難しかった」: フィードバック\n"
                "・「友だちに紹介する」: 友だちに紹介\n\n"
                "🔥 毎日「今すぐ」を送って連続記録を伸ばそう！"
            ))
        )
        print(f"ℹ️ [{timestamp}] Help menu sent")

    except Exception as e:
        print(f"❌ handle_message error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# アプリケーション起動時の初期化
# ==========================================
print("\n" + "=" * 70)
print("🚀 Initializing Jump Rope AI Coach Bot")
print("=" * 70 + "\n")

init_database()

startup_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
print(f"\n{'=' * 70}")
print(f"✅ Bot initialized at {startup_time}")
print(f"{'=' * 70}\n")

if __name__ == "__main__":
    print("🔧 Running in development mode (Flask built-in server)")
    app.run(host='0.0.0.0', port=10000, debug=False)