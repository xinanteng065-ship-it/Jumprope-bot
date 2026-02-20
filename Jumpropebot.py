import os
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
from supabase import create_client, Client

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
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")  # service_role キーを推奨（RLS回避のため）

# ★ なわ太コーチのロゴ画像URL（左上に表示されます）
LOGO_IMAGE_URL = os.environ.get("LOGO_IMAGE_URL", "")

# ★ オリジナルスタンプの画像URL（後で設定）
WELCOME_STAMP_URL = os.environ.get("WELCOME_STAMP_URL", "https://example.com/welcome_stamp.png")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY]):
    raise ValueError("🚨 必要な環境変数が設定されていません（LINE / OpenAI）")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("🚨 必要な環境変数が設定されていません（Supabase）")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

JST = timezone('Asia/Tokyo')

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
# Supabase テーブル初期化
# ==========================================
# 以下のSQLをSupabaseのSQL Editorで実行してテーブルを作成してください:
#
# CREATE TABLE IF NOT EXISTS users (
#     user_id TEXT PRIMARY KEY,
#     nickname TEXT,
#     avatar_url TEXT,                          -- ★ 追加: プロフィールアイコンURL
#     level TEXT NOT NULL DEFAULT '初心者',
#     coach_personality TEXT NOT NULL DEFAULT '優しい',
#     delivery_count INTEGER DEFAULT 0,
#     success_count INTEGER DEFAULT 0,
#     difficulty_count INTEGER DEFAULT 0,
#     support_shown INTEGER DEFAULT 0,
#     last_challenge TEXT,
#     immediate_request_count INTEGER DEFAULT 0,
#     last_immediate_request_date TEXT,
#     streak_days INTEGER DEFAULT 0,
#     last_challenge_date TEXT,
#     received_welcome_stamp INTEGER DEFAULT 0,
#     created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
#     updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
# );
#
# -- 既存テーブルへの追加はこちら:
# ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;
#
# -- updated_at を自動更新するトリガー（任意）
# CREATE OR REPLACE FUNCTION update_updated_at_column()
# RETURNS TRIGGER AS $$
# BEGIN
#     NEW.updated_at = NOW();
#     RETURN NEW;
# END;
# $$ language 'plpgsql';
#
# CREATE TRIGGER update_users_updated_at
#     BEFORE UPDATE ON users
#     FOR EACH ROW
#     EXECUTE FUNCTION update_updated_at_column();

# ==========================================
# ユーザー設定の取得
# ==========================================
def get_user_settings(user_id):
    """ユーザー設定をSupabaseから取得"""
    try:
        response = supabase.table("users").select(
            "level, nickname, avatar_url, coach_personality, delivery_count, success_count, "
            "difficulty_count, support_shown, last_challenge, streak_days, "
            "last_challenge_date, received_welcome_stamp"
        ).eq("user_id", user_id).execute()

        if not response.data:
            new_user = {
                "user_id": user_id,
                "level": "初心者",
                "coach_personality": "優しい",
                "delivery_count": 0,
                "success_count": 0,
                "difficulty_count": 0,
                "support_shown": 0,
                "streak_days": 0,
                "received_welcome_stamp": 0,
            }
            supabase.table("users").insert(new_user).execute()
            return {
                "level": "初心者",
                "nickname": None,
                "avatar_url": None,
                "coach_personality": "優しい",
                "delivery_count": 0,
                "success_count": 0,
                "difficulty_count": 0,
                "support_shown": 0,
                "last_challenge": None,
                "streak_days": 0,
                "last_challenge_date": None,
                "received_welcome_stamp": 0,
            }

        row = response.data[0]
        return {
            "level": row.get("level", "初心者"),
            "nickname": row.get("nickname"),
            "avatar_url": row.get("avatar_url"),
            "coach_personality": row.get("coach_personality", "優しい"),
            "delivery_count": row.get("delivery_count", 0),
            "success_count": row.get("success_count", 0),
            "difficulty_count": row.get("difficulty_count", 0),
            "support_shown": row.get("support_shown", 0),
            "last_challenge": row.get("last_challenge"),
            "streak_days": row.get("streak_days", 0),
            "last_challenge_date": row.get("last_challenge_date"),
            "received_welcome_stamp": row.get("received_welcome_stamp", 0),
        }

    except Exception as e:
        print(f"❌ get_user_settings error: {e}")
        return {
            "level": "初心者",
            "nickname": None,
            "avatar_url": None,
            "coach_personality": "優しい",
            "delivery_count": 0,
            "success_count": 0,
            "difficulty_count": 0,
            "support_shown": 0,
            "last_challenge": None,
            "streak_days": 0,
            "last_challenge_date": None,
            "received_welcome_stamp": 0,
        }

# ==========================================
# ユーザー設定の更新
# ==========================================
def update_user_settings(user_id, level=None, coach_personality=None, nickname=None, avatar_url=None):
    """レベル、コーチの性格、ニックネーム、アバターURLをSupabaseに更新"""
    try:
        print(f"🔧 Updating settings for {user_id[:8]}...")

        response = supabase.table("users").select(
            "level, coach_personality, nickname, avatar_url"
        ).eq("user_id", user_id).execute()

        update_data = {}

        if response.data:
            row = response.data[0]
            update_data["level"] = level if level is not None else row.get("level", "初心者")
            update_data["coach_personality"] = coach_personality if coach_personality is not None else row.get("coach_personality", "優しい")
            update_data["nickname"] = nickname if nickname is not None else row.get("nickname")
            update_data["avatar_url"] = avatar_url if avatar_url is not None else row.get("avatar_url")
            supabase.table("users").update(update_data).eq("user_id", user_id).execute()
        else:
            new_user = {
                "user_id": user_id,
                "level": level or "初心者",
                "coach_personality": coach_personality or "優しい",
                "nickname": nickname,
                "avatar_url": avatar_url,
                "delivery_count": 0,
                "success_count": 0,
                "difficulty_count": 0,
                "support_shown": 0,
                "streak_days": 0,
                "received_welcome_stamp": 0,
            }
            supabase.table("users").insert(new_user).execute()

        print(f"✅ Settings saved successfully")

    except Exception as e:
        print(f"❌ update_user_settings error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# 連続記録の更新
# ==========================================
def update_streak(user_id):
    try:
        today = datetime.now(JST).strftime("%Y-%m-%d")
        response = supabase.table("users").select("streak_days, last_challenge_date").eq("user_id", user_id).execute()
        current_streak = 0
        last_date = None
        if response.data:
            row = response.data[0]
            current_streak = row.get("streak_days") or 0
            last_date = row.get("last_challenge_date")
        if last_date == today:
            return current_streak
        elif last_date:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            today_dt = datetime.strptime(today, "%Y-%m-%d")
            diff_days = (today_dt - last_dt).days
            if diff_days == 1:
                current_streak += 1
            else:
                current_streak = 1
        else:
            current_streak = 1
        supabase.table("users").update({
            "streak_days": current_streak,
            "last_challenge_date": today,
        }).eq("user_id", user_id).execute()
        print(f"✅ Streak updated: {current_streak} days for {user_id[:8]}...")
        return current_streak
    except Exception as e:
        print(f"❌ update_streak error: {e}")
        return 0

# ==========================================
# 配信回数のカウント
# ==========================================
def increment_delivery_count(user_id, challenge_text):
    try:
        response = supabase.table("users").select("delivery_count").eq("user_id", user_id).execute()
        if response.data:
            current_count = response.data[0].get("delivery_count", 0) or 0
            supabase.table("users").update({
                "delivery_count": current_count + 1,
                "last_challenge": challenge_text,
            }).eq("user_id", user_id).execute()
        print(f"✅ Delivery count incremented for {user_id[:8]}...")
    except Exception as e:
        print(f"❌ increment_delivery_count error: {e}")

# ==========================================
# フィードバック記録
# ==========================================
def record_feedback(user_id, is_success):
    try:
        if is_success:
            response = supabase.table("users").select("success_count").eq("user_id", user_id).execute()
            if response.data:
                current = response.data[0].get("success_count", 0) or 0
                supabase.table("users").update({"success_count": current + 1}).eq("user_id", user_id).execute()
        else:
            response = supabase.table("users").select("difficulty_count").eq("user_id", user_id).execute()
            if response.data:
                current = response.data[0].get("difficulty_count", 0) or 0
                supabase.table("users").update({"difficulty_count": current + 1}).eq("user_id", user_id).execute()
        print(f"✅ Feedback recorded: {'success' if is_success else 'difficulty'}")
    except Exception as e:
        print(f"❌ record_feedback error: {e}")

def mark_support_shown(user_id):
    try:
        supabase.table("users").update({"support_shown": 1}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ mark_support_shown error: {e}")

def mark_welcome_stamp_sent(user_id):
    try:
        supabase.table("users").update({"received_welcome_stamp": 1}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ mark_welcome_stamp_sent error: {e}")


# ==========================================
# AI課題生成（IJRU対応）
# ==========================================
def generate_challenge_with_ai(level, user_history, coach_personality, streak_days):
    personality_styles = {
        "熱血": {"tone": "熱く励ます。「！」「💪」「🔥」を多用。「お前」「やってやろうぜ」「絶対いけるぞ」などの表現", "example": "よっしゃ！今日も全力でいくぞ！🔥"},
        "優しい": {"tone": "丁寧で優しく。「ですます調」。「ゆっくりでいいよ」「無理しないでね」などの配慮", "example": "今日も無理せず、楽しく練習しましょうね😊"},
        "厳しい": {"tone": "短く厳格に。「だ・である調」。「妥協するな」「できて当然」などの厳しさ", "example": "甘えは許さん。やるからには本気でやれ"},
        "フレンドリー": {"tone": "タメ口で親しみやすく。「！」を適度に。「いこ！」「やろ！」「一緒に頑張ろ」", "example": "今日も一緒に楽しく練習しよ！😊"},
        "冷静": {"tone": "論理的で分析的。「です・ます調」。「データ的に」「効率的に」などの客観的表現", "example": "本日の課題を論理的に設計しました"}
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

    level_guidelines = {
        "初心者": """【初心者向け技リスト】
基本技: 前とび、あやとび、交差とび、二重とび、はやぶさ、リットル（交差二重とび）、後ろとび、後ろあやとび、後ろ交差とび、後ろ二重とび、三重とび
目標: 縄跳びを好きになってもらう。初心者にはアドバイスを欠かさずに。三重とびの成功。それぞれの技の連続成功を目指す。
課題例: 「前とびを10回連続」「交差とびを5回連続」「後ろ二重とびを3回連続」「前とびを10秒間で何回とべるか挑戦してみる」
注意: まだEBTJやKNTJは難しすぎる。三重とびの完全な安定が最優先。""",

        "中級者": """【中級者向け技リスト】
メイン技: 三重とび、トード、EB、AS、CL、TS、EBトード、TJ、インバースTJ、EBTJ、KNTJ、インバースEBTJ、インバースKNTJ、SOAS、SOCL、SOTS、SSCL、SSTS
目標: EBTJやSOASなどの技を連続で安定できることを目標にする。
【禁止行為】5連続や10連続など多すぎる連続（3連続まで）。5回や10回成功させろなどはダメ（3回まで）。
課題例: 「EBTJを安定させて3回」「KNTJ → インバースKNTJ」「SOAS → SOCL」「三重とび → EBTJ → KNTJ」
【NG例】「EBTJ → KNTJ → SOAS → SOCL」（4連続はNG）""",

        "上級者": """【上級者向け技リスト】
基本高難度技: EBTJ、KNTJ、インバースEBTJ、インバースKNTJ、SOAS、SOCL、SOTS、SSCL、SSTS、SOOAS、SOOCL、SOOTS
O系: EBTJO、KNTJO、インバースEBTJO、インバースKNTJO
CL系: EBTJCL、KNTJCL、インバースEBTJCL、インバースKNTJCL
AS系: EBTJAS、KNTJAS、インバースEBTJAS、インバースKNTJAS
TS系: EBTJTS、KNTJTS、インバースEBTJTS、インバースKNTJTS
その他: 三重リリース、リリースOCL、四重とび、三重とび10回連続、クルーガーラップ、EBトードラップ、ASO、TS0、ASCL、ASTS
室内推奨技: ドンキー、ドンキークロス、プッシュアップ、プッシュアップクロス、カミカゼ、ロンダートから後ろ二重とび
激ムズ室内推奨技: 後ろドンキー、後ろプッシュアップ、ドンキー二重、プッシュアップ二重
【禁止】CL系、AS系、TS系は連続に入れない（単発のみ）。O系は連続に入れる場合は1個まで。
OK例: 「EBTJ → KNTJ → インバースEBTJ」「EBTJO → KNTJ」「EBTJ → KNTJ → EBTJCL」
NG例: 「EBTJO → KNTJO → インバースEBTJO」「EBTJCL → KNTJCL」""",

        "超上級者": """【超上級者向け技リスト】
基本高難度技: EBTJO、KNTJO、インバースEBTJO、インバースKNTJO、SOOAS、SOOCL、SOOTS
O系: SEBOOO、EBTJOO、KNTJOO、インバースEBTJOO、インバースKNTJOO
AS,CL,TS系（基本）: SOOOAS、SOOOCL、SOOOTS、SOOASO
四重系AS,CL,TS系: EBTJAS、EBTJCL、EBTJTS、インバースEBTJAS、インバースEBTJCL、インバースEBTJTS、KNTJAS、KNTJCL、KNTJTS、インバースKNTJAS、インバースKNTJCL、インバースKNTJTS
CL系: EBTJOCL、KNTJOCL、インバースEBTJOCL、インバースKNTJOCL
AS系: EBTJOAS、KNTJOAS、インバースEBTJOAS、インバースKNTJOAS
TS系: EBTJOTS、KNTJOTS、インバースEBTJOTS、インバースKNTJOTS
その他: リリースOOCL、五重とび、四重とび10回連続、カブースから後ろとび、カブースから後ろCL、STSOCL、SASOCL、SCLOCL、SOASOCL、SOASOAS、SOCLOCL、SOTSOCL、STSOCLO
室内推奨技: 後ろドンキー、後ろプッシュアップ、ドンキー二重、プッシュアップ二重、ドンキーtoプッシュアップ、カミカゼ、ロンダートから後ろOCLO
激ムズ室内推奨技: 後ろドンキーCL、後ろプッシュアップCL、片手後ろドンキー、片手後ろプッシュアップ
OK例: 「EBTJO → KNTJCL → インバースEBTJCL」「EBTJOO → KNTJAS」
NG例: 6連続以上はNG。AS,CL,TS系（基本）は2個まで、O系も2個まで、AS系、CL系、TS系は1個まで。"""
    }

    success_rate = 0
    difficulty_rate = 0
    if user_history['delivery_count'] > 0:
        success_rate = user_history['success_count'] / user_history['delivery_count']
        difficulty_rate = user_history['difficulty_count'] / user_history['delivery_count']

    adjustment = ""
    if user_history['delivery_count'] >= 2:
        if success_rate > 0.7:
            adjustment = "【重要】ユーザーは非常に好調です（成功率70%以上）。難度を1段階上げてください。\n単発→2技連続、2技連続→3技連続、など。ただし急激に上げすぎない。"
        elif difficulty_rate > 0.6:
            adjustment = "【重要】ユーザーは苦戦中です（難しかった率60%以上）。難度を1〜2段階下げてください。\n3技連続→2技連続、2技連続→単発、など。確実にできるレベルに戻す。"
        elif success_rate > 0.4 and difficulty_rate <= 0.4:
            adjustment = "ユーザーは順調です。現在の難度を維持してください（同じレベルで違うバリエーション）。"
        else:
            adjustment = "ユーザーの状況は中間です。少しだけ難度を下げるか、同じレベルの別パターンを試してください。"

    is_special_day = (streak_days > 0 and streak_days % 10 == 0 and streak_days <= 100)
    special_challenge_reminder = ""
    if is_special_day:
        special_challenge_reminder = f"\n\n【重要】今日は連続記録{streak_days}日目の節目です。通常の課題を出した後、採点アプリでのチャレンジを追加してください。"

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

        if is_special_day and streak_days <= 100:
            special_challenges = {
                10: {"duration": "15秒", "target": "3点超え", "message": "まずは15秒のフリースタイルを作ってみよう！"},
                20: {"duration": "30秒", "target": "5点超え", "message": "少し長めの30秒に挑戦！技のバリエーションを増やそう！"},
                30: {"duration": "30秒", "target": "6点超え", "message": "30秒で6点を目指そう！質を意識して！"},
                40: {"duration": "45秒", "target": "7点超え", "message": "45秒のフリースタイル！構成力が試されるよ！"},
                50: {"duration": "60秒", "target": "8点超え", "message": "1分間のフリースタイル！スタミナと技術の両立！"},
                60: {"duration": "60秒", "target": "9点超え", "message": "1分で9点！大会レベルに近づいてきた！"},
                70: {"duration": "75秒", "target": "9点超え", "message": "ついに大会と同じ75秒！本番さながらの緊張感を！"},
                80: {"duration": "75秒", "target": "9.5点超え", "message": "75秒で9.5点！完成度を極めよう！"},
                90: {"duration": "75秒", "target": "10点超え", "message": "10点の壁に挑戦！完璧な演技を目指して！"},
                100: {"duration": "75秒", "target": "10点超え", "message": "🎊100日達成おめでとう！！🎊 最高峰の演技で有終の美を飾ろう！"}
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
        fallback_by_personality = {
            "熱血": {"初心者": "今日のお題：\n三重とび3回連続！\n\n絶対いけるぞ！お前の力を信じてる！💪🔥", "中級者": "今日のお題：\nEBTJ → KNTJ！\n\nやってやろうぜ！全力でぶつかれ！🔥", "上級者": "今日のお題：\nSOOAS → SOOCL！\n\nお前ならできる！限界突破だ！✨💪", "超上級者": "今日のお題：\nEBTJOO → KNTJCL！\n\nお前の限界はここじゃないぞ！🔥💪"},
            "優しい": {"初心者": "今日のお題：\n三重とびを3回連続。\n\nゆっくりでいいので、焦らず練習しましょうね😊", "中級者": "今日のお題：\nEBTJを5回。\n\n無理しないでくださいね。少しずつ上達していきましょう💪", "上級者": "今日のお題：\nSOOASを1回。\n\n質を大切に、丁寧に練習してみてください✨", "超上級者": "今日のお題：\nEBTJOOを1回。\n\n焦らず、丁寧に練習しましょうね✨"},
            "厳しい": {"初心者": "今日のお題：\n三重とび5回連続。\n\nできて当然だ。甘えるな。", "中級者": "今日のお題：\nKNTJ → インバースKNTJ。\n\n妥協するな。完璧を目指せ。", "上級者": "今日のお題：\nSOOAS → SOOTS。\n\nできるまでやれ。結果が全てだ。", "超上級者": "今日のお題：\nEBTJOO → KNTJCL。\n\n限界を超えろ。それがお前の仕事だ。"},
            "フレンドリー": {"初心者": "今日のお題：\n三重とび3回連続いってみよ！\n\n楽しくやろ！一緒に頑張ろ！✨😊", "中級者": "今日のお題：\nEBTJ → KNTJ やろ！\n\n一緒に頑張ろ！絶対できるって！💪", "上級者": "今日のお題：\nSOOASいい感じで決めちゃお！\n\nお前ならいけるって！信じてる！🔥", "超上級者": "今日のお題：\nEBTJOO → KNTJCL！\n\n一緒にガチでやろ！絶対いけるって！🔥"},
            "冷静": {"初心者": "今日のお題：\n三重とび3回。\n\n安定性を重視して、効率的な動作を心がけてください。", "中級者": "今日のお題：\nEBTJ 5回。\n\n動作の効率性を分析しながら練習してください。", "上級者": "今日のお題：\nSOOAS 1回。\n\n質を分析し、データ的に最適な動作を目指してください。", "超上級者": "今日のお題：\nEBTJOO 1回。\n\n動作を論理的に分析し、効率的な練習を継続してください。"}
        }
        personality_fallback = fallback_by_personality.get(coach_personality, fallback_by_personality["優しい"])
        return personality_fallback.get(level, personality_fallback["初心者"])


def create_challenge_message(user_id, level):
    try:
        settings = get_user_settings(user_id)
        coach_personality = settings.get('coach_personality', '優しい')
        streak_days = update_streak(user_id)
        challenge = generate_challenge_with_ai(level, settings, coach_personality, streak_days)
        increment_delivery_count(user_id, challenge)
        return challenge
    except Exception as e:
        print(f"❌ create_challenge_message error: {e}")
        return "今日のお題：\n前とび30秒を安定させてみよう！"


# ==========================================
# ランキングデータ取得
# ==========================================
def get_ranking_data():
    try:
        response = supabase.table("users").select(
            "nickname, avatar_url, streak_days, level, last_challenge_date"
        ).gt("streak_days", 0).order("streak_days", desc=True).order(
            "last_challenge_date", desc=True
        ).limit(100).execute()

        ranking = []
        for row in response.data:
            nickname = row.get("nickname")
            if not nickname or nickname.strip() == "":
                nickname = "名無しのジャンパー"
            ranking.append({
                "nickname": nickname,
                "avatar_url": row.get("avatar_url") or "",
                "streak_days": row.get("streak_days", 0),
                "level": row.get("level", "初心者"),
                "last_challenge_date": row.get("last_challenge_date"),
            })
        return ranking
    except Exception as e:
        print(f"❌ get_ranking_data error: {e}")
        return []

def get_logo_html():
    if LOGO_IMAGE_URL:
        return f'<img src="{LOGO_IMAGE_URL}" alt="なわ太コーチ" class="logo-img">'
    else:
        return '<img src="/static/logo.png" alt="なわ太コーチ" class="logo-img">'

# ==========================================
# Flask Routes
# ==========================================
@app.route("/")
def index():
    return "Jump Rope AI Coach Bot Running ✅"


@app.route("/ranking")
def ranking():
    """ランキングページ — クリーン × スポーティデザイン"""
    ranking_data = get_ranking_data()
    logo_html = get_logo_html()

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ランキング — なわ太コーチ</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;900&family=Barlow+Condensed:wght@700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #f0f4f8; --surface: #ffffff; --surface2: #f7f9fc;
            --border: #e4eaf2; --text: #1a2332; --muted: #7a8da6;
            --accent: #ff5f2e; --accent2: #ff8c42;
            --gold: #f59e0b; --silver: #94a3b8; --bronze: #cd8b4a;
            --gold-bg: #fffbeb; --silver-bg: #f8fafc; --bronze-bg: #fdf6ee;
            --radius: 14px; --shadow: 0 2px 12px rgba(0,0,0,0.07); --shadow-lg: 0 8px 32px rgba(0,0,0,0.10);
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:'Noto Sans JP',sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }

        /* ナビバー */
        .navbar { background:var(--surface); border-bottom:1px solid var(--border); padding:0 20px; position:sticky; top:0; z-index:100; }
        .navbar-inner { max-width:780px; margin:0 auto; height:56px; display:flex; align-items:center; justify-content:space-between; }
        .logo-img { height:32px; width:auto; object-fit:contain; }
        .logo-text { font-size:16px; font-weight:700; color:var(--text); }
        .refresh-btn { display:inline-flex; align-items:center; gap:6px; padding:7px 16px; background:var(--surface2); border:1px solid var(--border); border-radius:100px; font-family:'Noto Sans JP',sans-serif; font-size:12px; font-weight:600; color:var(--muted); cursor:pointer; transition:all 0.2s; }
        .refresh-btn:hover { background:var(--border); color:var(--text); }
        .refresh-icon { display:inline-block; transition:transform 0.5s; }
        .refresh-btn:hover .refresh-icon { transform:rotate(180deg); }

        /* ページヘッダー */
        .page-header { max-width:780px; margin:0 auto; padding:32px 20px 24px; }
        .page-header-top { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; }
        .page-title { font-family:'Barlow Condensed',sans-serif; font-size:clamp(36px,8vw,52px); font-weight:900; letter-spacing:0.02em; line-height:1; }
        .page-title span { color:var(--accent); }
        .participant-count { font-size:13px; color:var(--muted); padding-bottom:4px; white-space:nowrap; text-align:right; }
        .participant-count strong { color:var(--text); font-size:20px; font-family:'Barlow Condensed',sans-serif; font-weight:700; display:block; }

        /* コンテンツ */
        .wrapper { max-width:780px; margin:0 auto; padding:0 20px 60px; }

        /* 表彰台 */
        .podium-grid { display:grid; grid-template-columns:1fr 1.12fr 1fr; gap:10px; margin-bottom:20px; align-items:end; }
        .podium-card { background:var(--surface); border:1.5px solid var(--border); border-radius:var(--radius); padding:20px 12px 18px; text-align:center; box-shadow:var(--shadow); transition:transform 0.25s,box-shadow 0.25s; position:relative; overflow:hidden; }
        .podium-card::after { content:''; position:absolute; bottom:0; left:0; right:0; height:3px; }
        .podium-card:hover { transform:translateY(-5px); box-shadow:var(--shadow-lg); }
        .podium-1 { background:var(--gold-bg); border-color:rgba(245,158,11,0.3); }
        .podium-1::after { background:var(--gold); }
        .podium-2 { background:var(--silver-bg); border-color:rgba(148,163,184,0.3); }
        .podium-2::after { background:var(--silver); }
        .podium-3 { background:var(--bronze-bg); border-color:rgba(205,139,74,0.3); }
        .podium-3::after { background:var(--bronze); }

        /* アバター（表彰台） */
        .podium-avatar { width:52px; height:52px; border-radius:50%; margin:0 auto 10px; display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:700; overflow:hidden; border:2.5px solid rgba(0,0,0,0.06); }
        .podium-1 .podium-avatar { width:60px; height:60px; border-color:var(--gold); }
        .podium-avatar img { width:100%; height:100%; object-fit:cover; }
        .av-gold { background:linear-gradient(135deg,#fde68a,#f59e0b); color:#78350f; }
        .av-silver { background:linear-gradient(135deg,#e2e8f0,#94a3b8); color:#334155; }
        .av-bronze { background:linear-gradient(135deg,#fde8cc,#cd8b4a); color:#7c2d12; }
        .av-blue { background:linear-gradient(135deg,#dbeafe,#3b82f6); color:#1e3a8a; }

        .medal-icon { font-size:18px; margin-bottom:4px; display:block; }
        .podium-1 .medal-icon { font-size:22px; }
        .podium-place { font-family:'Barlow Condensed',sans-serif; font-size:11px; font-weight:700; letter-spacing:0.15em; margin-bottom:6px; }
        .podium-1 .podium-place { color:var(--gold); }
        .podium-2 .podium-place { color:var(--silver); }
        .podium-3 .podium-place { color:var(--bronze); }
        .podium-name { font-size:12px; font-weight:700; color:var(--text); margin-bottom:8px; word-break:break-word; line-height:1.4; }
        .podium-1 .podium-name { font-size:14px; }
        .podium-streak-val { font-family:'Barlow Condensed',sans-serif; font-size:36px; font-weight:900; line-height:1; }
        .podium-1 .podium-streak-val { font-size:44px; color:var(--gold); }
        .podium-2 .podium-streak-val { color:var(--silver); }
        .podium-3 .podium-streak-val { color:var(--bronze); }
        .podium-streak-unit { font-size:11px; color:var(--muted); margin-top:2px; }
        .podium-level { display:inline-block; font-size:10px; font-weight:600; padding:2px 8px; border-radius:100px; margin-top:8px; background:rgba(0,0,0,0.05); color:var(--muted); }

        /* ランキングリスト */
        .rank-list { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow); overflow:hidden; }
        .rank-list-header { display:grid; grid-template-columns:56px 1fr auto; padding:10px 20px; background:var(--surface2); border-bottom:1px solid var(--border); font-size:10px; font-weight:700; color:var(--muted); letter-spacing:0.1em; text-transform:uppercase; }
        .rank-row { display:grid; grid-template-columns:56px 1fr auto; align-items:center; padding:12px 20px; border-bottom:1px solid var(--border); transition:background 0.15s; animation:fadeSlide 0.35s ease both; }
        @keyframes fadeSlide { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
        .rank-row:last-child { border-bottom:none; }
        .rank-row:hover { background:var(--surface2); }
        .rank-row:nth-child(1){animation-delay:.04s} .rank-row:nth-child(2){animation-delay:.08s} .rank-row:nth-child(3){animation-delay:.12s}
        .rank-row:nth-child(4){animation-delay:.16s} .rank-row:nth-child(5){animation-delay:.20s} .rank-row:nth-child(6){animation-delay:.24s}
        .rank-row:nth-child(7){animation-delay:.28s} .rank-row:nth-child(8){animation-delay:.32s} .rank-row:nth-child(9){animation-delay:.36s}
        .rank-row:nth-child(10){animation-delay:.40s}

        .rank-pos { font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:700; color:var(--muted); text-align:center; }
        .rank-row:nth-child(1) .rank-pos { color:var(--gold); }
        .rank-row:nth-child(2) .rank-pos { color:var(--silver); }
        .rank-row:nth-child(3) .rank-pos { color:var(--bronze); }

        .rank-user { display:flex; align-items:center; gap:11px; min-width:0; }
        .rank-avatar { width:38px; height:38px; border-radius:50%; flex-shrink:0; overflow:hidden; display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700; border:2px solid var(--border); }
        .rank-avatar img { width:100%; height:100%; object-fit:cover; }
        .rank-row:nth-child(1) .rank-avatar { border-color:var(--gold); }
        .rank-row:nth-child(2) .rank-avatar { border-color:var(--silver); }
        .rank-row:nth-child(3) .rank-avatar { border-color:var(--bronze); }
        .rank-info { min-width:0; }
        .rank-name { font-size:14px; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .rank-level { font-size:11px; color:var(--muted); margin-top:1px; }

        .rank-streak { display:flex; align-items:center; gap:5px; padding:6px 13px; background:#fff5f0; border:1px solid rgba(255,95,46,0.2); border-radius:100px; white-space:nowrap; }
        .streak-num { font-family:'Barlow Condensed',sans-serif; font-size:20px; font-weight:900; color:var(--accent); line-height:1; }
        .streak-label { font-size:11px; color:var(--muted); font-weight:500; }

        .empty { text-align:center; padding:64px 20px; }
        .empty-icon { font-size:52px; opacity:.25; margin-bottom:16px; }
        .empty-title { font-size:16px; font-weight:700; color:var(--muted); margin-bottom:6px; }
        .empty-sub { font-size:13px; color:var(--muted); opacity:.7; }
        .footer { text-align:center; margin-top:32px; font-size:12px; color:var(--muted); }

        @media(max-width:480px) {
            .podium-grid { grid-template-columns:1fr 1.08fr 1fr; gap:6px; }
            .podium-card { padding:14px 8px 14px; }
            .rank-list-header,.rank-row { padding-left:14px; padding-right:14px; }
            .rank-list-header { grid-template-columns:44px 1fr auto; }
            .rank-row { grid-template-columns:44px 1fr auto; }
        }
    </style>
</head>
<body>
<nav class="navbar">
    <div class="navbar-inner">""" + logo_html + """
        <button class="refresh-btn" onclick="location.reload()">
            <span class="refresh-icon">↻</span> 更新
        </button>
    </div>
</nav>
<div class="page-header">
    <div class="page-header-top">
        <div class="page-title">STREAK<br><span>RANKING</span></div>
        <div class="participant-count"><strong>{{ ranking_data|length }}</strong> 人参加中</div>
    </div>
</div>
<div class="wrapper">
    {% if ranking_data|length >= 3 %}
    <div class="podium-grid">
        <div class="podium-card podium-2">
            {% if ranking_data[1]['avatar_url'] %}<div class="podium-avatar av-silver"><img src="{{ ranking_data[1]['avatar_url'] }}" onerror="this.style.display='none';this.parentNode.innerHTML='{{ ranking_data[1]['nickname'][0] }}'"></div>{% else %}<div class="podium-avatar av-silver">{{ ranking_data[1]['nickname'][0] }}</div>{% endif %}
            <span class="medal-icon">🥈</span><div class="podium-place">2ND</div>
            <div class="podium-name">{{ ranking_data[1]['nickname'] }}</div>
            <div class="podium-streak-val">{{ ranking_data[1]['streak_days'] }}</div>
            <div class="podium-streak-unit">日連続</div><div class="podium-level">{{ ranking_data[1]['level'] }}</div>
        </div>
        <div class="podium-card podium-1">
            {% if ranking_data[0]['avatar_url'] %}<div class="podium-avatar av-gold"><img src="{{ ranking_data[0]['avatar_url'] }}" onerror="this.style.display='none';this.parentNode.innerHTML='{{ ranking_data[0]['nickname'][0] }}'"></div>{% else %}<div class="podium-avatar av-gold">{{ ranking_data[0]['nickname'][0] }}</div>{% endif %}
            <span class="medal-icon">🥇</span><div class="podium-place">1ST</div>
            <div class="podium-name">{{ ranking_data[0]['nickname'] }}</div>
            <div class="podium-streak-val">{{ ranking_data[0]['streak_days'] }}</div>
            <div class="podium-streak-unit">日連続</div><div class="podium-level">{{ ranking_data[0]['level'] }}</div>
        </div>
        <div class="podium-card podium-3">
            {% if ranking_data[2]['avatar_url'] %}<div class="podium-avatar av-bronze"><img src="{{ ranking_data[2]['avatar_url'] }}" onerror="this.style.display='none';this.parentNode.innerHTML='{{ ranking_data[2]['nickname'][0] }}'"></div>{% else %}<div class="podium-avatar av-bronze">{{ ranking_data[2]['nickname'][0] }}</div>{% endif %}
            <span class="medal-icon">🥉</span><div class="podium-place">3RD</div>
            <div class="podium-name">{{ ranking_data[2]['nickname'] }}</div>
            <div class="podium-streak-val">{{ ranking_data[2]['streak_days'] }}</div>
            <div class="podium-streak-unit">日連続</div><div class="podium-level">{{ ranking_data[2]['level'] }}</div>
        </div>
    </div>
    {% endif %}
    <div class="rank-list">
        <div class="rank-list-header"><span style="text-align:center">#</span><span style="padding-left:8px">ユーザー</span><span>連続記録</span></div>
        {% if ranking_data|length > 0 %}
        {% for user in ranking_data %}
        <div class="rank-row">
            <div class="rank-pos">{{ loop.index }}</div>
            <div class="rank-user">
                {% if user['avatar_url'] %}
                <div class="rank-avatar av-blue"><img src="{{ user['avatar_url'] }}" onerror="this.style.display='none'"></div>
                {% else %}
                <div class="rank-avatar {% if loop.index==1 %}av-gold{% elif loop.index==2 %}av-silver{% elif loop.index==3 %}av-bronze{% else %}av-blue{% endif %}">{{ user['nickname'][0] }}</div>
                {% endif %}
                <div class="rank-info">
                    <div class="rank-name">{{ user['nickname'] }}</div>
                    <div class="rank-level">{{ user['level'] }}</div>
                </div>
            </div>
            <div class="rank-streak"><span>🔥</span><span class="streak-num">{{ user['streak_days'] }}</span><span class="streak-label">日</span></div>
        </div>
        {% endfor %}
        {% else %}
        <div class="empty"><div class="empty-icon">🏆</div><div class="empty-title">まだランキングデータがありません</div><div class="empty-sub">毎日「今すぐ」を送って記録をつけよう！</div></div>
        {% endif %}
    </div>
    <div class="footer">© なわ太コーチ — Jump Rope AI Coach</div>
</div>
</body></html>"""
    return render_template_string(html, ranking_data=ranking_data)



@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """設定画面 — クリーン × スポーティデザイン"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>エラー</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;600;700&display=swap" rel="stylesheet">
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Noto Sans JP',sans-serif;background:#f0f4f8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}.card{background:#fff;border-radius:16px;padding:48px 32px;text-align:center;max-width:340px;width:100%;box-shadow:0 4px 20px rgba(0,0,0,0.08)}.icon{font-size:48px;margin-bottom:16px}h2{font-size:18px;color:#1a2332;margin-bottom:10px}p{font-size:14px;color:#7a8da6;line-height:1.7}</style></head>
<body><div class="card"><div class="icon">⚠️</div><h2>アクセスエラー</h2><p>ユーザーIDが見つかりません。<br>LINEから再度アクセスしてください。</p></div></body></html>""", 400

        if request.method == 'POST':
            new_level = request.form.get('level')
            new_personality = request.form.get('coach_personality', '優しい')
            new_nickname = request.form.get('nickname', '').strip()
            new_avatar_url = request.form.get('avatar_url', '').strip()
            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n⚙️ [{timestamp}] Settings update POST received")
            print(f"   User ID: {user_id[:8]}...")
            if new_nickname and len(new_nickname) > 10:
                new_nickname = new_nickname[:10]
            update_user_settings(user_id, level=new_level, coach_personality=new_personality, nickname=new_nickname, avatar_url=new_avatar_url if new_avatar_url else None)
            ranking_url = f"{APP_PUBLIC_URL}/ranking"
            logo_html = get_logo_html()
            return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>設定完了</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;600;700;900&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'Noto Sans JP',sans-serif;background:#f0f4f8;min-height:100vh;display:flex;flex-direction:column}}.navbar{{background:#fff;border-bottom:1px solid #e4eaf2;padding:0 20px}}.navbar-inner{{max-width:520px;margin:0 auto;height:56px;display:flex;align-items:center}}.logo-img{{height:30px;width:auto;object-fit:contain}}.logo-text{{font-size:15px;font-weight:700;color:#1a2332}}.body{{flex:1;display:flex;align-items:center;justify-content:center;padding:32px 20px}}.card{{background:#fff;border-radius:20px;padding:48px 32px;text-align:center;max-width:380px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,0.09);animation:pop 0.45s cubic-bezier(0.34,1.56,0.64,1) both}}@keyframes pop{{from{{opacity:0;transform:scale(0.85)}}to{{opacity:1;transform:scale(1)}}}}.check{{width:72px;height:72px;background:linear-gradient(135deg,#34d399,#10b981);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 24px;font-size:34px;box-shadow:0 0 32px rgba(16,185,129,0.25)}}h2{{font-size:22px;font-weight:700;color:#1a2332;margin-bottom:8px}}p{{font-size:14px;color:#7a8da6;line-height:1.7;margin-bottom:28px}}.btn{{display:inline-flex;align-items:center;gap:8px;padding:13px 28px;background:linear-gradient(135deg,#ff5f2e,#ff8c42);color:#fff;text-decoration:none;border-radius:100px;font-size:14px;font-weight:700;box-shadow:0 4px 16px rgba(255,95,46,0.3);transition:all 0.2s}}.btn:hover{{transform:translateY(-2px);box-shadow:0 6px 24px rgba(255,95,46,0.4)}}.note{{margin-top:18px;font-size:12px;color:#b0bec5}}</style></head>
<body><nav class="navbar"><div class="navbar-inner">{logo_html}</div></nav>
<div class="body"><div class="card"><div class="check">✓</div><h2>設定を保存しました！</h2><p>「今すぐ」と送ると<br>新しい設定で課題が届きます。</p>
<a href="{ranking_url}" class="btn">🔥 ランキングを見る</a><div class="note">LINEに戻ってください</div></div></div></body></html>"""

        current_settings = get_user_settings(user_id)
        current_nickname    = current_settings.get('nickname', '') or ''
        current_level       = current_settings['level']
        current_personality = current_settings.get('coach_personality', '優しい')
        current_avatar_url  = current_settings.get('avatar_url', '') or ''
        ranking_url = f"{APP_PUBLIC_URL}/ranking"
        logo_html = get_logo_html()

        personality_emojis = {"熱血":"🔥","優しい":"😊","厳しい":"💪","フレンドリー":"✌️","冷静":"🧠"}
        personality_descs  = {"熱血":"情熱的に鼓舞する","優しい":"丁寧で穏やかに","厳しい":"ストイックに追い込む","フレンドリー":"タメ口で親しみやすく","冷静":"論理的・分析的に"}

        initial = current_nickname[0] if current_nickname else '?'
        if current_avatar_url:
            hero_avatar_inner = f'<img src="{current_avatar_url}" onerror="this.style.display=\'none\'" style="width:100%;height:100%;object-fit:cover;">'
            url_preview_inner = f'<img src="{current_avatar_url}" onerror="this.style.display=\'none\'" style="width:100%;height:100%;object-fit:cover;">'
            url_preview_text  = 'アイコンが設定されています'
            hero_has_image    = 'has-image'
        else:
            hero_avatar_inner = f'<span>{initial}</span>'
            url_preview_inner = f'<span>{initial}</span>'
            url_preview_text  = 'URLを入力するとプレビューが表示されます'
            hero_has_image    = ''

        html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>練習設定 — なわ太コーチ</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;900&family=Barlow+Condensed:wght@700;900&display=swap" rel="stylesheet">
<style>
:root{{--bg:#f0f4f8;--surface:#fff;--surface2:#f7f9fc;--border:#e4eaf2;--border-focus:#3b82f6;--text:#1a2332;--muted:#7a8da6;--accent:#ff5f2e;--accent2:#ff8c42;--radius:14px;--shadow:0 2px 12px rgba(0,0,0,0.07)}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Noto Sans JP',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
.navbar{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 20px;position:sticky;top:0;z-index:100}}
.navbar-inner{{max-width:520px;margin:0 auto;height:56px;display:flex;align-items:center;justify-content:space-between}}
.logo-img{{height:30px;width:auto;object-fit:contain}}
.logo-text{{font-size:15px;font-weight:700;color:var(--text)}}
.nav-ranking{{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:var(--muted);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:100px;transition:all 0.2s}}
.nav-ranking:hover{{color:var(--text);background:var(--border)}}
.wrapper{{max-width:520px;margin:0 auto;padding:28px 20px 60px}}
.profile-hero{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px 20px;display:flex;align-items:center;gap:16px;margin-bottom:20px;box-shadow:var(--shadow)}}
.hero-avatar{{width:64px;height:64px;border-radius:50%;border:3px solid var(--border);background:linear-gradient(135deg,#dbeafe,#3b82f6);display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700;color:#fff;overflow:hidden;flex-shrink:0;transition:border-color 0.2s}}
.hero-avatar.has-image{{border-color:var(--accent)}}
.hero-info{{flex:1;min-width:0}}
.hero-name{{font-size:18px;font-weight:700;margin-bottom:3px}}
.hero-meta{{font-size:13px;color:var(--muted)}}
.hero-edit{{font-size:11px;color:var(--accent);margin-top:4px}}
.section{{margin-bottom:20px}}
.section-label{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:0.12em;text-transform:uppercase;margin-bottom:8px;padding-left:2px}}
.input-card{{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);transition:border-color 0.2s,box-shadow 0.2s}}
.input-card:focus-within{{border-color:var(--border-focus);box-shadow:0 0 0 3px rgba(59,130,246,0.1)}}
.input-row{{display:flex;align-items:center;padding:0 14px}}
.input-icon{{font-size:16px;margin-right:10px;flex-shrink:0}}
.input-field{{flex:1;background:transparent;border:none;outline:none;font-family:'Noto Sans JP',sans-serif;font-size:15px;font-weight:500;color:var(--text);padding:14px 0}}
.input-field::placeholder{{color:var(--muted);font-weight:400}}
.input-counter{{font-size:11px;color:var(--muted);flex-shrink:0}}
.input-hint{{font-size:11px;color:var(--muted);padding:0 14px 10px;opacity:.75}}
.avatar-preview-row{{display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--surface2);border-top:1px solid var(--border)}}
.preview-circle{{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#dbeafe,#3b82f6);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#fff;overflow:hidden;flex-shrink:0;border:2px solid var(--border)}}
.preview-circle img{{width:100%;height:100%;object-fit:cover}}
.preview-text{{font-size:11px;color:var(--muted)}}
.level-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.level-card{{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);padding:14px 12px;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);position:relative}}
.level-card:hover{{border-color:rgba(59,130,246,0.4);transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.09)}}
.level-card.active{{border-color:var(--accent);background:#fff8f6;box-shadow:0 0 0 1px rgba(255,95,46,0.15),0 4px 16px rgba(255,95,46,0.1)}}
.level-check{{position:absolute;top:10px;right:10px;width:18px;height:18px;border-radius:50%;border:1.5px solid var(--muted);display:flex;align-items:center;justify-content:center;font-size:10px;color:transparent;transition:all 0.2s}}
.level-card.active .level-check{{background:var(--accent);border-color:var(--accent);color:#fff}}
.level-name{{font-size:15px;font-weight:700;margin-bottom:3px}}
.level-desc{{font-size:11px;color:var(--muted);line-height:1.5}}
.personality-list{{display:flex;flex-direction:column;gap:8px}}
.personality-card{{display:flex;align-items:center;gap:12px;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);padding:12px 14px;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow)}}
.personality-card:hover{{border-color:rgba(59,130,246,0.4);transform:translateX(2px)}}
.personality-card.active{{border-color:var(--accent);background:#fff8f6}}
.p-emoji{{font-size:22px;width:32px;text-align:center;flex-shrink:0}}
.p-info{{flex:1}}
.p-name{{font-size:14px;font-weight:700}}
.p-desc{{font-size:11px;color:var(--muted);margin-top:2px}}
.p-radio{{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--muted);display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all 0.2s}}
.personality-card.active .p-radio{{border-color:var(--accent);background:var(--accent)}}
.personality-card.active .p-radio::after{{content:'';width:6px;height:6px;background:#fff;border-radius:50%}}
.save-btn{{width:100%;padding:16px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;border:none;border-radius:var(--radius);font-family:'Noto Sans JP',sans-serif;font-size:16px;font-weight:700;cursor:pointer;box-shadow:0 4px 16px rgba(255,95,46,0.3);transition:all 0.25s;margin-top:8px}}
.save-btn:hover{{transform:translateY(-2px);box-shadow:0 6px 24px rgba(255,95,46,0.4)}}
.save-btn:active{{transform:translateY(0)}}
.ranking-banner{{display:flex;align-items:center;justify-content:space-between;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);padding:16px;text-decoration:none;box-shadow:var(--shadow);margin-top:12px;transition:all 0.2s}}
.ranking-banner:hover{{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 16px rgba(255,95,46,0.1)}}
.rb-left{{display:flex;align-items:center;gap:12px}}
.rb-icon{{font-size:28px}}
.rb-title{{font-size:14px;font-weight:700;color:var(--text)}}
.rb-sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.rb-arrow{{font-size:20px;color:var(--muted)}}
.divider{{height:1px;background:var(--border);margin:20px 0}}
@media(max-width:400px){{.level-grid{{grid-template-columns:1fr}}.profile-hero{{flex-direction:column;text-align:center}}}}
</style>
</head>
<body>
<nav class="navbar">
<div class="navbar-inner">{logo_html}<a href="{ranking_url}" class="nav-ranking">🔥 ランキング</a></div>
</nav>
<div class="wrapper">
<div class="profile-hero">
<div class="hero-avatar {hero_has_image}" id="heroAvatar">{hero_avatar_inner}</div>
<div class="hero-info">
<div class="hero-name" id="heroName">{current_nickname or '名前を設定しよう'}</div>
<div class="hero-meta">{current_level} ・ {current_personality}コーチ</div>
<div class="hero-edit">✏️ 設定を編集中</div>
</div>
</div>
<form method="POST" id="settingsForm">
<input type="hidden" name="level" id="levelInput" value="{current_level}">
<input type="hidden" name="coach_personality" id="personalityInput" value="{current_personality}">
<div class="section">
<div class="section-label">ニックネーム</div>
<div class="input-card">
<div class="input-row"><span class="input-icon">✏️</span>
<input type="text" name="nickname" class="input-field" value="{current_nickname}" maxlength="10" placeholder="例：ジャンプ太郎" id="nicknameInput" oninput="updateNickname(this)">
<span class="input-counter" id="charCounter">{len(current_nickname)}/10</span></div>
<div class="input-hint">ランキングに表示されます（10文字まで）</div>
</div>
</div>
<div class="section">
<div class="section-label">プロフィールアイコン</div>
<div class="input-card">
<div class="input-row"><span class="input-icon">🖼️</span>
<input type="url" name="avatar_url" class="input-field" value="{current_avatar_url}" placeholder="画像URLを入力（https://...）" id="avatarUrlInput" oninput="updateAvatarPreview(this.value)">
</div>
<div class="input-hint">SNSやGoogleフォト等の公開URLを入力 → ランキングにも表示されます</div>
<div class="avatar-preview-row">
<div class="preview-circle" id="previewCircle">{url_preview_inner}</div>
<div class="preview-text" id="previewText">{url_preview_text}</div>
</div>
</div>
</div>
<div class="divider"></div>
<div class="section">
<div class="section-label">🎯 練習レベル</div>
<div class="level-grid">"""

        for lname, linfo in USER_LEVELS.items():
            is_active = 'active' if lname == current_level else ''
            html += f'<div class="level-card {is_active}" onclick="selectLevel(\'{lname}\',this)"><div class="level-check">✓</div><div class="level-name">{lname}</div><div class="level-desc">{linfo["description"]}</div></div>'

        html += """</div></div>
<div class="divider"></div>
<div class="section">
<div class="section-label">😊 コーチの性格</div>
<div class="personality-list">"""

        for pname in COACH_PERSONALITIES:
            is_active = 'active' if pname == current_personality else ''
            emoji = personality_emojis.get(pname, "😊")
            desc  = personality_descs.get(pname, "")
            html += f'<div class="personality-card {is_active}" onclick="selectPersonality(\'{pname}\',this)"><div class="p-emoji">{emoji}</div><div class="p-info"><div class="p-name">{pname}</div><div class="p-desc">{desc}</div></div><div class="p-radio"></div></div>'

        html += f"""</div></div>
<button type="submit" class="save-btn">💾 設定を保存する</button>
</form>
<a href="{ranking_url}" class="ranking-banner"><div class="rb-left"><div class="rb-icon">🏆</div><div><div class="rb-title">連続記録ランキング</div><div class="rb-sub">みんなの記録をチェック！</div></div></div><div class="rb-arrow">›</div></a>
</div>
<script>
function updateNickname(input){{
    document.getElementById('charCounter').textContent=input.value.length+'/10';
    document.getElementById('heroName').textContent=input.value||'名前を設定しよう';
}}
function updateAvatarPreview(url){{
    const heroAvatar=document.getElementById('heroAvatar');
    const previewCircle=document.getElementById('previewCircle');
    const previewText=document.getElementById('previewText');
    const nickname=document.getElementById('nicknameInput').value;
    const initial=nickname?nickname[0]:'?';
    if(url&&url.startsWith('http')){{
        heroAvatar.innerHTML=`<img src="${{url}}" onerror="this.style.display='none'" style="width:100%;height:100%;object-fit:cover;">`;
        heroAvatar.classList.add('has-image');
        previewCircle.innerHTML=`<img src="${{url}}" onerror="this.style.display='none'" style="width:100%;height:100%;object-fit:cover;">`;
        previewText.textContent='画像を設定済み';
    }}else{{
        heroAvatar.innerHTML=`<span>${{initial}}</span>`;
        heroAvatar.classList.remove('has-image');
        previewCircle.innerHTML=`<span>${{initial}}</span>`;
        previewText.textContent='URLを入力するとプレビューが表示されます';
    }}
}}
function selectLevel(name,el){{
    document.querySelectorAll('.level-card').forEach(c=>c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('levelInput').value=name;
}}
function selectPersonality(name,el){{
    document.querySelectorAll('.personality-card').forEach(c=>c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('personalityInput').value=name;
}}
</script>
</body></html>"""
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
                "・上級者：競技フリースタイル選手\n"
                "・超上級者：EBTJOASなど高難易度技\n\n"
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

        if text == "今すぐ":
            today = datetime.now(JST).strftime("%Y-%m-%d")
            resp = supabase.table("users").select("immediate_request_count, last_immediate_request_date").eq("user_id", user_id).execute()
            immediate_count = 0
            last_request_date = None
            if resp.data:
                immediate_count = resp.data[0].get("immediate_request_count") or 0
                last_request_date = resp.data[0].get("last_immediate_request_date")
            if last_request_date != today:
                immediate_count = 0
                supabase.table("users").update({"immediate_request_count": 0, "last_immediate_request_date": today}).eq("user_id", user_id).execute()
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
            supabase.table("users").update({"immediate_request_count": immediate_count + 1, "last_immediate_request_date": today}).eq("user_id", user_id).execute()
            print(f"🚀 [{timestamp}] Immediate delivery requested by {user_id[:8]}... ({immediate_count + 1}/3 today)")
            challenge_content = create_challenge_message(user_id, settings['level'])
            full_message = challenge_content + "\n\n💬 フィードバック\n「できた」「難しかった」と送ると、次回の課題が調整されます！"
            messages = [TextSendMessage(text=full_message)]
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

        if text in ["できた", "成功", "できました", "クリア", "達成"]:
            record_feedback(user_id, is_success=True)
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

        if text in ["難しかった", "できなかった", "無理", "難しい", "厳しい"]:
            record_feedback(user_id, is_success=False)
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
print("🚀 Initializing Jump Rope AI Coach Bot (Supabase Edition)")
print("=" * 70 + "\n")

try:
    test_resp = supabase.table("users").select("user_id").limit(1).execute()
    print("✅ Supabase connection OK")
except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    print("   テーブルが存在するか確認してください。")

startup_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
print(f"\n{'=' * 70}")
print(f"✅ Bot initialized at {startup_time}")
print(f"{'=' * 70}\n")

if __name__ == "__main__":
    print("🔧 Running in development mode (Flask built-in server)")
    app.run(host='0.0.0.0', port=10000, debug=False)