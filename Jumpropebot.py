import os
import sqlite3
import threading
import time
import random
from datetime import datetime, timedelta
from pytz import timezone
from flask import Flask, request, abort, render_template_string
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI

app = Flask(__name__)

# ==========================================
# 環境変数の読み込み
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://jumprope-bot.onrender.com")
BOOTH_SUPPORT_URL = "https://yourapp.booth.pm/items/xxxxxxx"
LINE_BOT_ID = os.environ.get("LINE_BOT_ID", "@698rtcqz")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    raise ValueError("🚨 必要な環境変数が設定されていません")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

JST = timezone('Asia/Tokyo')
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
    }
}

# コーチの性格設定
COACH_PERSONALITIES = {
    "熱血": {
        "tone": "熱血コーチ"
    },
    "優しい": {
        "tone": "優しいコーチ"
    },
    "厳しい": {
        "tone": "厳しいコーチ"
    },
    "フレンドリー": {
        "tone": "フレンドリー"
    },
    "冷静": {
        "tone": "冷静な分析官"
    }
}

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
                delivery_time TEXT NOT NULL DEFAULT '07:00',
                level TEXT NOT NULL DEFAULT '初心者',
                coach_personality TEXT NOT NULL DEFAULT '優しい',
                delivery_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                difficulty_count INTEGER DEFAULT 0,
                support_shown INTEGER DEFAULT 0,
                last_delivery_date TEXT,
                last_challenge TEXT,
                immediate_request_count INTEGER DEFAULT 0,
                last_immediate_request_date TEXT
            )
        ''')

        # 既存テーブルへのカラム追加（必要に応じて）
        columns_to_add = [
            ("last_delivery_date", "TEXT"),
            ("last_challenge", "TEXT"),
            ("success_count", "INTEGER DEFAULT 0"),
            ("difficulty_count", "INTEGER DEFAULT 0"),
            ("coach_personality", "TEXT DEFAULT '優しい'"),
            ("immediate_request_count", "INTEGER DEFAULT 0"),
            ("last_immediate_request_date", "TEXT")
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
            SELECT delivery_time, level, coach_personality, delivery_count, success_count, 
                   difficulty_count, support_shown, last_delivery_date, last_challenge 
            FROM users WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()

        if not row:
            cursor.execute('''
                INSERT INTO users (user_id, delivery_time, level, coach_personality, delivery_count, 
                                 success_count, difficulty_count, support_shown) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, '07:00', '初心者', '優しい', 0, 0, 0, 0))
            conn.commit()
            conn.close()
            return {
                'time': '07:00', 'level': '初心者', 'coach_personality': '優しい',
                'delivery_count': 0, 'success_count': 0, 'difficulty_count': 0, 
                'support_shown': 0, 'last_delivery_date': None, 'last_challenge': None
            }

        result = {
            'time': row['delivery_time'],
            'level': row['level'],
            'coach_personality': row['coach_personality'] if 'coach_personality' in row.keys() else '優しい',
            'delivery_count': row['delivery_count'],
            'success_count': row['success_count'],
            'difficulty_count': row['difficulty_count'],
            'support_shown': row['support_shown'],
            'last_delivery_date': row['last_delivery_date'],
            'last_challenge': row['last_challenge']
        }

        conn.close()
        return result

    except Exception as e:
        print(f"❌ get_user_settings error: {e}")
        return {
            'time': '07:00', 'level': '初心者', 'coach_personality': '優しい',
            'delivery_count': 0, 'success_count': 0, 'difficulty_count': 0,
            'support_shown': 0, 'last_delivery_date': None, 'last_challenge': None
        }

# ==========================================
# ユーザー設定の更新
# ==========================================
def update_user_settings(user_id, delivery_time, level, coach_personality='優しい'):
    """配信時間、レベル、コーチの性格を更新"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        if delivery_time and ':' in delivery_time:
            parts = delivery_time.split(':')
            if len(parts) >= 2:
                hour = parts[0].strip().zfill(2)
                minute = parts[1].strip().zfill(2)
                delivery_time = f"{hour}:{minute}"

        print(f"🔧 Updating settings for {user_id[:8]}...")
        print(f"   Time: '{delivery_time}', Level: '{level}', Personality: '{coach_personality}'")

        cursor.execute('''
            INSERT INTO users (user_id, delivery_time, level, coach_personality, delivery_count, 
                             success_count, difficulty_count, support_shown, last_delivery_date)
            VALUES (?, ?, ?, ?, 0, 0, 0, 0, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                delivery_time = excluded.delivery_time,
                level = excluded.level,
                coach_personality = excluded.coach_personality,
                last_delivery_date = NULL
        ''', (user_id, delivery_time, level, coach_personality))

        conn.commit()
        conn.close()
        print(f"✅ Settings saved successfully")

    except Exception as e:
        print(f"❌ update_user_settings error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# 配信回数のカウント
# ==========================================
def increment_delivery_count(user_id, challenge_text):
    """配信回数を1増やし、今日の日付と課題を記録"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        today = datetime.now(JST).strftime("%Y-%m-%d")

        cursor.execute('''
            UPDATE users 
            SET delivery_count = delivery_count + 1, 
                last_delivery_date = ?, 
                last_challenge = ? 
            WHERE user_id = ?
        ''', (today, challenge_text, user_id))

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
# 配信対象ユーザーを取得
# ==========================================
def get_users_for_delivery(target_time):
    """指定時刻に配信すべきユーザーを取得（今日まだ配信していない人のみ）"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        today = datetime.now(JST).strftime("%Y-%m-%d")

        cursor.execute('''
            SELECT user_id, level, delivery_time FROM users 
            WHERE (last_delivery_date IS NULL OR last_delivery_date != ?)
        ''', (today,))

        all_candidates = cursor.fetchall()
        matched_users = []

        for row in all_candidates:
            db_time = row['delivery_time'].strip()
            if db_time == target_time:
                matched_users.append((row['user_id'], row['level']))

        conn.close()
        return matched_users

    except Exception as e:
        print(f"❌ get_users_for_delivery error: {e}")
        return []

# ==========================================
# AI課題生成（IJRU対応）
# ==========================================
def generate_challenge_with_ai(level, user_history, coach_personality):
    """AIで練習課題を生成（実際の競技技を使用）"""
    
    # コーチの性格を反映したシステムプロンプト
    personality_tone = COACH_PERSONALITIES.get(coach_personality, COACH_PERSONALITIES["優しい"])["tone"]
    
    system_prompt = f"""あなたは縄跳びフリースタイル競技のAIコーチです。
実際の競技で使われる技名を使って、具体的な練習課題を出します。

【コーチの性格】
{personality_tone}

【重要な禁止事項】
- 「フロー」「リカバリー」「クリーンフィニッシュ」という言葉は存在しないので絶対に使わない
- 抽象的な表現は一切使わない
- 必ず具体的な技名を使う

【課題設計の原則】
- 毎日3〜10分で完結する内容
- 成功条件を明確にする（回数・秒数など）
- 技の組み合わせパターンを工夫する
- 前回と違う課題を出す
- 段階的な難度上昇を意識する"""

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
- 三重とびの安定
- 連続成功を目指す

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
- AS
- CL
- TS
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

【重要な難度ガイドライン】
- 最初は単体練習から始める（例: 三重とびを5回連続）
- 慣れてきたら2技連続（例: EBTJ → KNTJ）
- さらに慣れたら3技連続（例: EBTJ → KNTJ → SOAS）

【禁止の組み合わせ】
- CL系、AS系、TS系は連続に入れない（単発のみ）
- O系は連続に入れる場合は1個まで

課題パターン:
1. 単体練習: 「EBTJを5回」「KNTJを3回」
2. 基本の組み合わせ: 「EBTJ → KNTJ」「三重とび → EBTJ」
3. 3技連続: 「EBTJ → KNTJ → 三重とび」

課題例:
- 「EBTJを安定させて5回」
- 「KNTJ → インバースKNTJ」
- 「SOAS → SOCL」（これはOK）
- 「三重とび → EBTJ → KNTJ」
- 「インバースEBTJを1回成功」

【NG例】
- ❌「SOCL → SOAS → SOTS」（CL系連続はNG）
- ❌「EBTJ → KNTJ → SOAS → SOCL」（CL系連続はNG）

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
- 四重とび
- 三重とび10回連続

室内推奨技:
- ドンキー
- プッシュアップ
- ロンダートから後ろ二重とび

【重要な難度ガイドライン】
- 最初は基本高難度技の単発から（例: SOOASを1回）
- 慣れてきたら2技連続（例: EBTJ → インバースEBTJ）
- さらに慣れたら3技連続（例: EBTJ → インバースEBTJ → KNTJ）

【禁止の組み合わせ】
- CL系、AS系、TS系は連続に入れない（単発のみ or 最後に1つだけ）
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

週1回程度の特別課題（その他・室内・採点系）:
- 「三重リリースに挑戦」
- 「ドンキーを室内で練習」
- 「プッシュアップを室内で練習」
- 「ロンダートから後ろ二重とびに挑戦」
- 「採点アプリで15秒フリースタイルを作ってみよう」
- 「採点アプリで最終得点3点超えを目指そう（プレゼン0.6、リクワイヤードエレメンツとミス含む）」
- 最終目標: 75秒フリースタイル（得点5点→6点→8点→10点→12点）（プレゼン0.6、リクワイヤードエレメンツとミス含む）

課題例:
- 「SOOASを安定させて1回」
- 「EBTJ → インバースEBTJ → KNTJ」
- 「EBTJO → KNTJ」
- 「EBTJ → KNTJ → EBTJCL」
- 「三重リリースに挑戦」（週1の特別課題）
- 「SSCL → SSTS」
- 「ドンキーを室内で練習」（週1の特別課題）
- 「採点アプリで15秒フリースタイル（最終得点3点超え目標）」（週1の特別課題）"""
    }

    # ユーザー履歴の分析
    success_rate = 0
    difficulty_rate = 0
    
    if user_history['delivery_count'] > 0:
        success_rate = user_history['success_count'] / user_history['delivery_count']
        difficulty_rate = user_history['difficulty_count'] / user_history['delivery_count']
    
    adjustment = ""
    if user_history['delivery_count'] >= 3:
        if success_rate > 0.7:
            adjustment = "ユーザーは好調です。少し難度を上げて良いですが、段階的に（単発→2技連続→3技連続）。"
        elif difficulty_rate > 0.5:
            adjustment = "ユーザーは苦戦中です。今より簡単な課題に戻してください（連続を減らすか単発に）。"
        elif success_rate > 0.4 and difficulty_rate < 0.3:
            adjustment = "ユーザーは順調です。現在の難度を維持。"

    # 週1回の特別課題判定（その他・室内・採点系）
    special_challenge_reminder = ""
    if user_history['delivery_count'] > 0 and user_history['delivery_count'] % 7 == 0:
        if level == "上級者":
            special_challenge_reminder = "\n\n【重要】今日は週1回の特別課題を出してください。以下から選択:\n- その他技（三重リリース）\n- 室内推奨技（ドンキー、プッシュアップ、ロンダートから後ろ二重とび）\n- 採点アプリ課題（15秒フリースタイル、得点3点超えなど）"
        else:
            special_challenge_reminder = "\n\n【重要】今日は週1回の特別課題を出してください（普段より少し変わった課題）。"

    # プロンプト生成
    user_prompt = f"""今日の練習課題を1つ生成してください。

【ユーザー情報】
レベル: {level}
コーチの性格: {coach_personality}
配信回数: {user_history['delivery_count']}回
成功回数: {user_history['success_count']}回
難しかった回数: {user_history['difficulty_count']}回
前回の課題: {user_history.get('last_challenge', 'なし')}
{adjustment}
{special_challenge_reminder}

{level_guidelines[level]}

【出力形式】
必ず以下の形式で、コーチの性格を反映した口調で出力:

今日のお題：
（具体的な技名を使った課題。1〜2文で完結。性格に合わせた口調で）

採点アプリ課題の場合は以下を追加:
→ 採点アプリ: https://jumprope-scorer.netlify.app
→ 採点アプリの使い方: https://official-jumprope-scorer.netlify.app

【絶対に禁止】
- 「フロー」「リカバリー」「クリーンフィニッシュ」は存在しない言葉なので使用禁止
- 「基礎技」「難しい技」などの抽象的表現は絶対NG
- CL系、AS系、TS系を連続に入れるのは禁止
- O系を連続に2個以上入れるのは禁止
- 前回と全く同じ課題は避ける
- "###"や"**"は使わない"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_completion_tokens=300,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"❌ OpenAI API Error: {e}")
        # フォールバック課題（性格に応じて変える）
        fallback_by_personality = {
            "熱血": {
                "初心者": "今日のお題：\n三重とび3回連続！絶対いけるぞ！🔥",
                "中級者": "今日のお題：\nEBTJ → KNTJ！やってやろうぜ！💪",
                "上級者": "今日のお題：\nSOOAS → SOOCL！お前ならできる！✨"
            },
            "優しい": {
                "初心者": "今日のお題：\n三重とびを3回連続。ゆっくりでいいよ🏃‍♂️",
                "中級者": "今日のお題：\nEBTJを5回。無理しないでね💪",
                "上級者": "今日のお題：\nSOOASを1回。質を大切に✨"
            },
            "厳しい": {
                "初心者": "今日のお題：\n三重とび5回連続。できて当然だ",
                "中級者": "今日のお題：\nKNTJ → インバースKNTJ。妥協するな",
                "上級者": "今日のお題：\nSOOAS → SOOTS。できるまでやれ"
            },
            "フレンドリー": {
                "初心者": "今日のお題：\n三重とび3回連続いってみよ！✨",
                "中級者": "今日のお題：\nEBTJ → KNTJ やろ！一緒に頑張ろ！😊",
                "上級者": "今日のお題：\nSOOASいい感じで決めちゃお！🔥"
            },
            "冷静": {
                "初心者": "今日のお題：\n三重とび3回。安定性を重視してください",
                "中級者": "今日のお題：\nEBTJ 5回。効率的な動作を意識",
                "上級者": "今日のお題：\nSOOAS 1回。質を分析してください"
            }
        }
        personality_fallback = fallback_by_personality.get(coach_personality, fallback_by_personality["優しい"])
        return personality_fallback.get(level, personality_fallback["初心者"])


def create_challenge_message(user_id, level):
    """練習課題メッセージを作成"""
    try:
        settings = get_user_settings(user_id)
        coach_personality = settings.get('coach_personality', '優しい')
        challenge = generate_challenge_with_ai(level, settings, coach_personality)
        
        increment_delivery_count(user_id, challenge)
        
        return challenge
    except Exception as e:
        print(f"❌ create_challenge_message error: {e}")
        return "今日のお題：\n前とび30秒を安定させてみよう！"

# ==========================================
# 課題配信（Push送信）
# ==========================================
def send_challenge_to_user(user_id, level):
    """ユーザーに課題をPush送信"""
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    try:
        print(f"📤 [{timestamp}] Sending challenge to {user_id[:8]}... (Level: {level})")

        challenge_content = create_challenge_message(user_id, level)
        
        # フィードバック促進を課題に追加
        full_message = challenge_content + "\n\n💬 フィードバック\n「できた」「難しかった」と送ると、次回の課題が調整されます！"
        
        messages = [TextSendMessage(text=full_message)]

        settings = get_user_settings(user_id)
        if settings['delivery_count'] >= 10 and settings['support_shown'] == 0:
            support_message = (
                "いつも練習お疲れ様です！🙏\n\n"
                "この縄跳びAIコーチは個人開発で、サーバー代やAI利用料を自腹で運営しています。\n\n"
                "もし応援していただけるなら、100円の応援PDFをBoothに置いています。\n"
                "無理はしないでください🙏\n\n"
                f"↓応援はこちらから\n{BOOTH_SUPPORT_URL}"
            )
            messages.append(TextSendMessage(text=support_message))
            mark_support_shown(user_id)
            print(f"💝 [{timestamp}] Support message added")

        line_bot_api.push_message(user_id, messages)
        print(f"✅ [{timestamp}] Successfully sent to {user_id[:8]}...")

    except Exception as e:
        print(f"❌ [{timestamp}] Push error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# スケジューラー
# ==========================================
def schedule_checker():
    """毎分00秒に正確に実行するスケジューラー"""
    print("🚀 Scheduler thread started")

    now = datetime.now(JST)
    seconds_to_wait = 60 - now.second
    if now.microsecond > 0:
        seconds_to_wait -= now.microsecond / 1000000.0

    print(f"⏱️ Waiting {seconds_to_wait:.2f}s to sync with next minute...")
    time.sleep(seconds_to_wait)

    last_checked_minute = None

    while True:
        try:
            now_jst = datetime.now(JST)
            current_time_str = now_jst.strftime("%H:%M")
            current_minute_key = now_jst.strftime("%Y%m%d%H%M")
            timestamp = now_jst.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            if current_minute_key == last_checked_minute:
                time.sleep(0.5)
                continue

            last_checked_minute = current_minute_key
            print(f"\n⏰ [{timestamp}] Checking deliveries for {current_time_str}")

            # デバッグ: 全ユーザーの設定を表示
            try:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('SELECT user_id, delivery_time, level, last_delivery_date FROM users')
                all_users = cursor.fetchall()
                conn.close()

                print(f"📊 Total registered users: {len(all_users)}")
                for row in all_users:
                    user_id = row['user_id']
                    delivery_time = row['delivery_time'].strip()
                    level = row['level']
                    last_date = row['last_delivery_date']

                    today = datetime.now(JST).strftime("%Y-%m-%d")
                    match = delivery_time == current_time_str
                    already_delivered = (last_date == today)

                    status = "✅ DELIVER" if (match and not already_delivered) else "⏭️ Skip"
                    if match and already_delivered:
                        status = "✓ Already sent today"

                    print(f"   {status} | User: {user_id[:8]}... | Time: '{delivery_time}' | Level: {level} | Last: {last_date}")
            except Exception as e:
                print(f"⚠️ Debug query failed: {e}")

            targets = get_users_for_delivery(current_time_str)

            if targets:
                print(f"📬 Found {len(targets)} user(s) to deliver")
                for user_id, level in targets:
                    print(f"   → Delivering to {user_id[:8]}... ({level})")
                    threading.Thread(target=send_challenge_to_user, args=(user_id, level), daemon=True).start()
            else:
                print(f"   ℹ️ No deliveries for {current_time_str}")

            now = datetime.now(JST)
            seconds_to_wait = 60 - now.second
            if now.microsecond > 0:
                seconds_to_wait -= now.microsecond / 1000000.0
            if seconds_to_wait < 1:
                seconds_to_wait = 60 + seconds_to_wait

            time.sleep(seconds_to_wait)

        except Exception as e:
            error_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"❌ [{error_time}] Scheduler error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

# ==========================================
# Flask Routes
# ==========================================
@app.route("/")
def index():
    """ヘルスチェック用エンドポイント"""
    return "Jump Rope AI Coach Bot Running ✅"

@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """設定画面"""
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
            new_time = request.form.get('delivery_time')
            new_level = request.form.get('level')
            new_personality = request.form.get('coach_personality', '優しい')

            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n⚙️ [{timestamp}] Settings update POST received")
            print(f"   User ID: {user_id[:8]}...")
            print(f"   Form data: time={new_time}, level={new_level}, personality={new_personality}")

            update_user_settings(user_id, new_time, new_level, new_personality)

            return """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>設定完了</title>
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
                        padding: 50px 30px;
                        border-radius: 16px;
                        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        text-align: center;
                        max-width: 400px;
                        animation: slideIn 0.4s ease-out;
                    }
                    @keyframes slideIn {
                        from { opacity: 0; transform: translateY(-20px); }
                        to { opacity: 1; transform: translateY(0); }
                    }
                    .success-icon {
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
                    }
                    h2 { color: #333; margin-bottom: 20px; font-size: 26px; }
                    p { color: #666; font-size: 18px; line-height: 1.8; }
                    .back-notice {
                        margin-top: 30px;
                        padding: 15px;
                        background: #f8f9fa;
                        border-radius: 8px;
                        color: #555;
                        font-size: 15px;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success-icon">✓</div>
                    <h2>設定を保存しました！</h2>
                    <p>設定した時間に課題が届きます。</p>
                    <div class="back-notice">LINEの画面に戻ってください</div>
                </div>
            </body>
            </html>
            """

        current_settings = get_user_settings(user_id)

        level_options = ''
        for level_name, level_info in USER_LEVELS.items():
            selected = 'selected' if level_name == current_settings['level'] else ''
            level_options += f'<option value="{level_name}" {selected}>{level_name}（{level_info["description"]}）</option>'

        personality_options = ''
        current_personality = current_settings.get('coach_personality', '優しい')
        for personality_name, personality_info in COACH_PERSONALITIES.items():
            selected = 'selected' if personality_name == current_personality else ''
            personality_options += f'<option value="{personality_name}" {selected}>{personality_name}（{personality_info["tone"]}）</option>'

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>練習設定 - 縄跳びAIコーチ</title>
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
                input[type="time"], select {{
                    width: 100%;
                    padding: 14px 16px;
                    font-size: 16px;
                    border: 2px solid #e0e0e0;
                    border-radius: 12px;
                    background-color: #f8f9fa;
                    transition: all 0.3s ease;
                    font-family: inherit;
                }}
                input[type="time"]:focus, select:focus {{
                    outline: none;
                    border-color: #667eea;
                    background-color: white;
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
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
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="header-icon">🏋️</div>
                    <h2>練習設定</h2>
                    <p class="subtitle">配信時間とレベルを設定できます</p>
                </div>
                <div class="current-settings">
                    現在の設定: <strong>{current_settings['time']}</strong> に <strong>{current_settings['level']}</strong>レベル（<strong>{current_personality}</strong>コーチ）
                </div>
                <form method="POST">
                    <div class="form-group">
                        <label>
                            <span class="label-icon">🕐</span>
                            配信時間
                        </label>
                        <input type="time" name="delivery_time" value="{current_settings['time']}" required>
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
        if settings['delivery_count'] == 0 and text not in ["設定", "今すぐ"]:
            welcome_text = (
                "Jumprope-botです！\n\n"
                "このBotは毎日あなたのレベルに合った練習課題をお届けします。\n\n"
                "📝 まずは設定から始めましょう：\n"
                "「設定」と送信して、配信時間・レベル・コーチの性格を設定してください。\n\n"
                "💡 または今すぐ試したい場合は：\n"
                "「今すぐ」と送信してください！\n\n"
                "【レベルについて】\n"
                "・初心者：前とび〜三重とび\n"
                "・中級者：三重とび連続〜SOAS\n"
                "・上級者：競技フリースタイル選手\n\n"
                "【コーチの性格】\n"
                "・熱血：情熱的な励まし\n"
                "・優しい：丁寧で穏やか\n"
                "・厳しい：ストイックに\n"
                "・フレンドリー：タメ口で親しみやすく\n"
                "・冷静：論理的で分析的\n\n"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))
            print(f"👋 [{timestamp}] Welcome message sent to new user")
            return

        # 今すぐ課題を配信（1日3回まで）
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
                    "💡 設定した時間の自動配信は制限なく届きますよ✨"
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
            threading.Thread(target=send_challenge_to_user, args=(user_id, settings['level']), daemon=True).start()
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

        # 設定画面へのリンクを送信
        if text == "設定":
            settings_url = f"{APP_PUBLIC_URL}/settings?user_id={user_id}"
            reply_text = (
                "⚙️ 設定\n"
                "以下のリンクから配信時間とレベルを変更できます。\n\n"
                f"{settings_url}\n\n"
                "※リンクを知っている人は誰でも設定を変更できてしまうため、他人に教えないでください。"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"⚙️ [{timestamp}] Settings link sent")
            return

        # 友だちに紹介する機能
        if text in ["友だちに紹介する", "友達に紹介する", "紹介"]:
            line_add_url = f"https://line.me/R/ti/p/{LINE_BOT_ID}"
            reply_text = (
                "📢 友だちに紹介\n\n"
                "縄跳びAIコーチを友だちに紹介していただきありがとうございます！\n\n"
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
                "・「今すぐ」: 今すぐ課題を受信\n"
                "・「設定」: 時間やレベルを変更\n"
                "・「できた」「難しかった」: フィードバック\n"
                "・「友だちに紹介する」: 友だちに紹介"
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

# スケジューラースレッドを起動
scheduler_thread = threading.Thread(target=schedule_checker, daemon=True)
scheduler_thread.start()

startup_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
print(f"\n{'=' * 70}")
print(f"✅ Bot initialized at {startup_time}")
print(f"✅ Scheduler thread started")
print(f"{'=' * 70}\n")

if __name__ == "__main__":
    print("🔧 Running in development mode (Flask built-in server)")
    app.run(host='0.0.0.0', port=10000, debug=False)
