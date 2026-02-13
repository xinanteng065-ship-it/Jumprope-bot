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
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://yourapp.onrender.com")
BOOTH_SUPPORT_URL = "https://yourapp.booth.pm/items/xxxxxxx"
LINE_BOT_ID = os.environ.get("LINE_BOT_ID", "@xxxxxxxx")

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
        "description": "三重とび連続〜SOASレベル",
        "focus": "技の安定とフロー"
    },
    "上級者": {
        "description": "競技フリースタイル選手",
        "focus": "質・構成・大会意識"
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
                delivery_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                difficulty_count INTEGER DEFAULT 0,
                support_shown INTEGER DEFAULT 0,
                last_delivery_date TEXT,
                last_challenge TEXT
            )
        ''')

        # 既存テーブルへのカラム追加（必要に応じて）
        columns_to_add = [
            ("last_delivery_date", "TEXT"),
            ("last_challenge", "TEXT"),
            ("success_count", "INTEGER DEFAULT 0"),
            ("difficulty_count", "INTEGER DEFAULT 0")
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
            SELECT delivery_time, level, delivery_count, success_count, 
                   difficulty_count, support_shown, last_delivery_date, last_challenge 
            FROM users WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()

        if not row:
            cursor.execute('''
                INSERT INTO users (user_id, delivery_time, level, delivery_count, 
                                 success_count, difficulty_count, support_shown) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, '07:00', '初心者', 0, 0, 0, 0))
            conn.commit()
            conn.close()
            return {
                'time': '07:00', 'level': '初心者', 'delivery_count': 0,
                'success_count': 0, 'difficulty_count': 0, 'support_shown': 0,
                'last_delivery_date': None, 'last_challenge': None
            }

        result = {
            'time': row['delivery_time'],
            'level': row['level'],
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
            'time': '07:00', 'level': '初心者', 'delivery_count': 0,
            'success_count': 0, 'difficulty_count': 0, 'support_shown': 0,
            'last_delivery_date': None, 'last_challenge': None
        }

# ==========================================
# ユーザー設定の更新
# ==========================================
def update_user_settings(user_id, delivery_time, level):
    """配信時間とレベルを更新"""
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
        print(f"   Time: '{delivery_time}', Level: '{level}'")

        cursor.execute('''
            INSERT INTO users (user_id, delivery_time, level, delivery_count, 
                             success_count, difficulty_count, support_shown, last_delivery_date)
            VALUES (?, ?, ?, 0, 0, 0, 0, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                delivery_time = excluded.delivery_time,
                level = excluded.level,
                last_delivery_date = NULL
        ''', (user_id, delivery_time, level))

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
def generate_challenge_with_ai(level, user_history):
    """AIで練習課題を生成"""
    
    # IJRU採点視点を含むシステムプロンプト
    system_prompt = """あなたは縄跳びフリースタイル競技のAIコーチです。
IJRU（International Jump Rope Union）の最新ルールに基づき、以下の採点視点を理解しています：

【IJRU採点の3要素】
1. **Difficulty（難度）**: 技の難しさ
2. **Presentation（表現）**: フロー・安定性・構成
3. **Deductions（減点）**: ミス・停止・構成不足

【課題設計の原則】
- 毎日3〜10分で完結する内容
- 成功条件を明確にする
- 週1〜2回は「採点アプリで確認」を促す
- IJRU視点（安定性・フロー・構成）を反映

【トーン】
- コーチのように前向き
- 読むのに5秒以内
- "今日やってみよう"と思える内容"""

    # レベル別の課題ガイドライン
    level_guidelines = {
        "初心者": """【初心者向け課題】
対象技: 前とび、後ろとび、あやとび、二重とび、三重とび
目的: 基礎安定と成功体験
課題例:
- 前とび30秒を安定させる
- 二重とび1回成功
- 三重とびにチャレンジ（1回でOK）
- あやとびと二重とびを交互に5セット

注意: 絶対に難しすぎる課題は出さない""",

        "中級者": """【中級者向け課題】
対象技: 三重とび連続、TJ、SOAS、EBとび
目的: 技の安定とフロー意識
課題例:
- 三重とびを3回連続
- TJを安定優先で5回
- SOASをゆっくり確認（1回成功でOK）
- EB → 二重とび → EB のフロー練習

たまに採点アプリを勧める（例: 30秒演技で採点してみよう）""",

        "上級者": """【上級者向け課題】
対象技: 競技フリースタイルレベル
目的: 質・構成・大会意識
課題タイプ:
1. 軽い確認（例: SOOASを安定優先で1回）
2. 連続チャレンジ（例: EBTJ→インバースEBTJ→KNTJ）
3. 採点ミッション（例: 30秒演技で5点以上を目指し採点アプリで確認）
4. 構成練習（例: 難度技2つ → リカバリー → クリーンフィニッシュ）

IJRU視点を明示的に意識させる課題を含める"""
    }

    # ユーザー履歴の分析
    success_rate = 0
    difficulty_rate = 0
    
    if user_history['delivery_count'] > 0:
        success_rate = user_history['success_count'] / user_history['delivery_count']
        difficulty_rate = user_history['difficulty_count'] / user_history['delivery_count']
    
    adjustment = ""
    if user_history['delivery_count'] >= 3:  # 最低3回配信後から調整開始
        if success_rate > 0.7:
            adjustment = "ユーザーは好調です。少し難度を上げてチャレンジさせましょう。"
        elif difficulty_rate > 0.5:
            adjustment = "ユーザーは苦戦中です。今日は軽めで達成感を感じられる課題にしてください。"
        elif success_rate > 0.4 and difficulty_rate < 0.3:
            adjustment = "ユーザーは順調です。現在の難度を維持してください。"

    # プロンプト生成
    user_prompt = f"""今日の練習課題を1つ生成してください。

【ユーザー情報】
レベル: {level}
配信回数: {user_history['delivery_count']}回
成功回数: {user_history['success_count']}回
難しかった回数: {user_history['difficulty_count']}回
前回の課題: {user_history.get('last_challenge', 'なし')}
{adjustment}

{level_guidelines[level]}

【出力形式】
必ず以下の形式で出力してください:

今日のお題：
（短く具体的な課題。1〜2文で完結）

必要に応じて以下を追加:
→ 採点アプリで確認してみよう

【禁止事項】
- 長文説明は不要
- 前回と同じ課題は避ける
- "###"や"**"などのMarkdown記法は使わない
- 絵文字は適度に使ってOK"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_completion_tokens=300,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"❌ OpenAI API Error: {e}")
        # フォールバック課題
        fallback = {
            "初心者": "今日のお題：\n前とび30秒を安定させてみよう！🏃‍♂️",
            "中級者": "今日のお題：\n三重とびを2回連続で成功させよう！💪",
            "上級者": "今日のお題：\nSOASを安定優先で1回。質を意識して！✨"
        }
        return fallback.get(level, fallback["初心者"])

# ==========================================
# 課題メッセージ作成
# ==========================================
def create_challenge_message(user_id, level):
    """練習課題メッセージを作成"""
    try:
        settings = get_user_settings(user_id)
        challenge = generate_challenge_with_ai(level, settings)
        
        increment_delivery_count(user_id, challenge)
        
        return challenge
    except Exception as e:
        print(f"❌ create_challenge_message error: {e}")
        return "今日のお題：\n基礎技を1つ、安定優先で練習してみよう！"

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

            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n⚙️ [{timestamp}] Settings update POST received")
            print(f"   User ID: {user_id[:8]}...")
            print(f"   Form data: time={new_time}, level={new_level}")

            update_user_settings(user_id, new_time, new_level)

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
                    現在の設定: <strong>{current_settings['time']}</strong> に <strong>{current_settings['level']}</strong>レベル
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
                "縄跳びAIコーチへようこそ！🎉\n\n"
                "このBotは毎日あなたのレベルに合った練習課題をお届けします。\n\n"
                "📝 まずは設定から始めましょう：\n"
                "「設定」と送信して、配信時間とレベルを設定してください。\n\n"
                "💡 または今すぐ試したい場合は：\n"
                "「今すぐ」と送信してください！\n\n"
                "【レベルについて】\n"
                "・初心者：前とび〜三重とび\n"
                "・中級者：三重とび連続〜SOAS\n"
                "・上級者：競技フリースタイル選手"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=welcome_text))
            print(f"👋 [{timestamp}] Welcome message sent to new user")
            return

        # 今すぐ課題を配信
        if text == "今すぐ":
            print(f"🚀 [{timestamp}] Immediate delivery requested by {user_id[:8]}...")
            threading.Thread(target=send_challenge_to_user, args=(user_id, settings['level']), daemon=True).start()
            return

        # フィードバック: 成功
        if text in ["できた", "成功", "できました", "クリア", "達成"]:
            record_feedback(user_id, is_success=True)
            reply_text = "素晴らしい！💪 次回の課題で少しレベルアップしますね。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            print(f"✅ [{timestamp}] Success feedback recorded")
            return

        # フィードバック: 難しかった
        if text in ["難しかった", "できなかった", "無理", "難しい", "厳しい"]:
            record_feedback(user_id, is_success=False)
            reply_text = "大丈夫！次回は少し軽めの課題にしますね。焦らず続けましょう🙌"
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