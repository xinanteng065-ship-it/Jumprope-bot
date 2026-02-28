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
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")



# ★ オリジナルスタンプの画像URL
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

# ==========================================
# ユーザー設定の取得
# ==========================================
def get_user_settings(user_id):
    """ユーザー設定をSupabaseから取得"""
    try:
        response = supabase.table("users").select(
            "level, nickname, coach_personality, delivery_count, success_count, "
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
def update_user_settings(user_id, level=None, coach_personality=None, nickname=None):
    """レベル、コーチの性格、ニックネームをSupabaseに更新"""
    try:
        print(f"🔧 Updating settings for {user_id[:8]}...")

        response = supabase.table("users").select(
            "level, coach_personality, nickname"
        ).eq("user_id", user_id).execute()

        update_data = {}

        if response.data:
            row = response.data[0]
            update_data["level"] = level if level is not None else row.get("level", "初心者")
            update_data["coach_personality"] = coach_personality if coach_personality is not None else row.get("coach_personality", "優しい")
            update_data["nickname"] = nickname if nickname is not None else row.get("nickname")
            supabase.table("users").update(update_data).eq("user_id", user_id).execute()
        else:
            new_user = {
                "user_id": user_id,
                "level": level or "初心者",
                "coach_personality": coach_personality or "優しい",
                "nickname": nickname,
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
    """連続記録を更新（今日課題をもらった場合）"""
    try:
        today = datetime.now(JST).strftime("%Y-%m-%d")

        response = supabase.table("users").select(
            "streak_days, last_challenge_date"
        ).eq("user_id", user_id).execute()

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
    """配信回数を1増やし、課題を記録"""
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
    """ユーザーのフィードバックを記録（成功/難しかった）"""
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

# ==========================================
# 応援メッセージフラグ
# ==========================================
def mark_support_shown(user_id):
    """応援メッセージを表示済みにする"""
    try:
        supabase.table("users").update({"support_shown": 1}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ mark_support_shown error: {e}")

# ==========================================
# ウェルカムスタンプ送信済みフラグ
# ==========================================
def mark_welcome_stamp_sent(user_id):
    """ウェルカムスタンプを送信済みにする"""
    try:
        supabase.table("users").update({"received_welcome_stamp": 1}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ mark_welcome_stamp_sent error: {e}")

# ==========================================
# AI課題生成（IJRU対応）
# ==========================================
def generate_challenge_with_ai(level, user_history, coach_personality, streak_days):
    """AIで練習課題を生成（実際の競技技を使用）"""

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

2日に1回程度の特別課題（その他・室内のみ）:
- 「三重リリースに挑戦」
- 「ドンキーを室内で練習」
- 「プッシュアップを室内で練習」
- 「ロンダートから後ろ二重とびに挑戦」

課題例:
- 初めのうちは「KNTJを安定させて1回」など単発
- 「できた」の回数が増えてきたら「EBTJ → インバースEBTJ」など2連続
- さらに慣れてきたら「EBTJ → KNTJ → SOCL」など3連続
- さらに慣れたら「インバースEBTJO → KNTJ → EBTJCL」など難易度の高い（文字列の長い）3連続
- 「三重リリースに挑戦」（2日に1回程度の特別課題）
- 「ドンキーを室内で練習」（2日に1回程度の特別課題）""",

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

2日に1回程度の特別課題（その他・室内のみ）:
- 「リリースOOCLに挑戦」
- 「後ろドンキーを室内で練習」
- 「後ろSOASOCLを練習」
- 「ロンダートから後ろOCLOに挑戦」"""
    }

    # ユーザー履歴の分析
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

    # ----------------------------------------
    # ★ 変更点1: 5日ごとに採点アプリ特別課題を出す
    #    （旧: streak_days % 10 == 0）
    # ----------------------------------------
    is_special_day = (streak_days > 0 and streak_days % 5 == 0 and streak_days <= 100)

    special_challenge_reminder = ""
    if is_special_day:
        special_challenge_reminder = f"\n\n【重要】今日は連続記録{streak_days}日目の節目です。通常の課題を出した後、採点アプリでのチャレンジを追加してください。段階的に難度が上がる特別課題を用意しています。"

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

        # ----------------------------------------
        # ★ 変更点2: 採点アプリ特別課題を5日ごとに対応
        #    5日おきに細かく難度が上がるよう20段階に拡張
        #    既存の10,20,...,100日目の内容はそのまま引き継ぎ
        # ----------------------------------------
        if is_special_day and streak_days <= 100:
            special_challenges = {
                5: {
                    "duration": "10秒",
                    "target": "2点超え",
                    "message": "まず10秒で採点アプリを試してみよう！雰囲気を掴むだけでOK！"
                },
                10: {
                    "duration": "15秒",
                    "target": "3点超え",
                    "message": "まずは15秒のフリースタイルを作ってみよう！"
                },
                15: {
                    "duration": "20秒",
                    "target": "4点超え",
                    "message": "20秒に伸ばして、技のつなぎを意識しよう！"
                },
                20: {
                    "duration": "30秒",
                    "target": "5点超え",
                    "message": "少し長めの30秒に挑戦！技のバリエーションを増やそう！"
                },
                25: {
                    "duration": "30秒",
                    "target": "5.5点超え",
                    "message": "30秒をより安定させて5.5点を目指そう！"
                },
                30: {
                    "duration": "30秒",
                    "target": "6点超え",
                    "message": "30秒で6点を目指そう！質を意識して！"
                },
                35: {
                    "duration": "45秒",
                    "target": "6.5点超え",
                    "message": "45秒に挑戦！後半もペースを落とさないように！"
                },
                40: {
                    "duration": "45秒",
                    "target": "7点超え",
                    "message": "45秒のフリースタイル！構成力が試されるよ！"
                },
                45: {
                    "duration": "60秒",
                    "target": "7.5点超え",
                    "message": "いよいよ1分！スタミナ配分を意識しよう！"
                },
                50: {
                    "duration": "60秒",
                    "target": "8点超え",
                    "message": "1分間のフリースタイル！スタミナと技術の両立！"
                },
                55: {
                    "duration": "60秒",
                    "target": "8.5点超え",
                    "message": "1分で8.5点！ミスを減らして完成度を高めよう！"
                },
                60: {
                    "duration": "60秒",
                    "target": "9点超え",
                    "message": "1分で9点！大会レベルに近づいてきた！"
                },
                65: {
                    "duration": "75秒",
                    "target": "9点超え",
                    "message": "大会と同じ75秒に初挑戦！完走することを意識しよう！"
                },
                70: {
                    "duration": "75秒",
                    "target": "9点超え",
                    "message": "ついに大会と同じ75秒！本番さながらの緊張感を！"
                },
                75: {
                    "duration": "75秒",
                    "target": "9.3点超え",
                    "message": "75秒の質を上げよう！安定感を磨いて！"
                },
                80: {
                    "duration": "75秒",
                    "target": "9.5点超え",
                    "message": "75秒で9.5点！完成度を極めよう！"
                },
                85: {
                    "duration": "75秒",
                    "target": "9.8点超え",
                    "message": "9.8点の壁に挑戦！ほぼ完璧な演技を目指して！"
                },
                90: {
                    "duration": "75秒",
                    "target": "10点超え",
                    "message": "10点の壁に挑戦！完璧な演技を目指して！"
                },
                95: {
                    "duration": "75秒",
                    "target": "10点超え",
                    "message": "残り5日！最高の演技で100日を迎えよう！"
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
        fallback_by_personality = {
            "熱血": {
                "初心者": "今日のお題：\n三重とび3回連続！\n\n絶対いけるぞ！お前の力を信じてる！💪🔥",
                "中級者": "今日のお題：\nEBTJ → KNTJ！\n\nやってやろうぜ！全力でぶつかれ！🔥",
                "上級者": "今日のお題：\nSOOAS → SOOCL！\n\nお前ならできる！限界突破だ！✨💪",
                "超上級者": "今日のお題：\nEBTJOO → KNTJCL！\n\nお前の限界はここじゃないぞ！🔥💪"
            },
            "優しい": {
                "初心者": "今日のお題：\n三重とびを3回連続。\n\nゆっくりでいいので、焦らず練習しましょうね😊",
                "中級者": "今日のお題：\nEBTJを5回。\n\n無理しないでくださいね。少しずつ上達していきましょう💪",
                "上級者": "今日のお題：\nSOOASを1回。\n\n質を大切に、丁寧に練習してみてください✨",
                "超上級者": "今日のお題：\nEBTJOOを1回。\n\n焦らず、丁寧に練習しましょうね✨"
            },
            "厳しい": {
                "初心者": "今日のお題：\n三重とび5回連続。\n\nできて当然だ。甘えるな。",
                "中級者": "今日のお題：\nKNTJ → インバースKNTJ。\n\n妥協するな。完璧を目指せ。",
                "上級者": "今日のお題：\nSOOAS → SOOTS。\n\nできるまでやれ。結果が全てだ。",
                "超上級者": "今日のお題：\nEBTJOO → KNTJCL。\n\n限界を超えろ。それがお前の仕事だ。"
            },
            "フレンドリー": {
                "初心者": "今日のお題：\n三重とび3回連続いってみよ！\n\n楽しくやろ！一緒に頑張ろ！✨😊",
                "中級者": "今日のお題：\nEBTJ → KNTJ やろ！\n\n一緒に頑張ろ！絶対できるって！💪",
                "上級者": "今日のお題：\nSOOASいい感じで決めちゃお！\n\nお前ならいけるって！信じてる！🔥",
                "超上級者": "今日のお題：\nEBTJOO → KNTJCL！\n\n一緒にガチでやろ！絶対いけるって！🔥"
            },
            "冷静": {
                "初心者": "今日のお題：\n三重とび3回。\n\n安定性を重視して、効率的な動作を心がけてください。",
                "中級者": "今日のお題：\nEBTJ 5回。\n\n動作の効率性を分析しながら練習してください。",
                "上級者": "今日のお題：\nSOOAS 1回。\n\n質を分析し、データ的に最適な動作を目指してください。",
                "超上級者": "今日のお題：\nEBTJOO 1回。\n\n動作を論理的に分析し、効率的な練習を継続してください。"
            }
        }
        personality_fallback = fallback_by_personality.get(coach_personality, fallback_by_personality["優しい"])
        return personality_fallback.get(level, personality_fallback["初心者"])


def create_challenge_message(user_id, level):
    """練習課題メッセージを作成"""
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
    """全ユーザーのランキングデータをSupabaseから取得"""
    try:
        response = supabase.table("users").select(
            "nickname, streak_days, level, last_challenge_date"
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
                "streak_days": row.get("streak_days", 0),
                "level": row.get("level", "初心者"),
                "last_challenge_date": row.get("last_challenge_date"),
            })

        return ranking
    except Exception as e:
        print(f"❌ get_ranking_data error: {e}")
        return []



# ==========================================
# 共通CSSテーマ（落ち着いたダークトーン）
# ==========================================
COMMON_THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;600;700&family=DM+Serif+Display:ital@0;1&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
    /* ── Palette ── */
    --ink:        #0f1117;
    --ink-mid:    #1e2130;
    --ink-soft:   #2c3147;
    --surface:    #191d2b;
    --card:       #1e2235;
    --card-hov:   #232840;
    --border:     rgba(255,255,255,0.07);
    --border-hi:  rgba(255,255,255,0.14);
    --text:       #e8ecf4;
    --text-mid:   #9ba3bb;
    --text-soft:  #636b82;
    --accent:     #c8a97e;        /* warm gold */
    --accent-hi:  #dbbf98;
    --accent-lo:  rgba(200,169,126,0.12);
    --accent-lo2: rgba(200,169,126,0.06);
    --green:      #5fb88a;
    --green-lo:   rgba(95,184,138,0.12);
    --red:        #e07070;
    --blue:       #6e9ed4;
    --blue-lo:    rgba(110,158,212,0.12);
    /* ── Gold medals ── */
    --gold:       #d4a843;
    --gold-lo:    rgba(212,168,67,0.14);
    --silver:     #8ea0b8;
    --silver-lo:  rgba(142,160,184,0.14);
    --bronze:     #b07a52;
    --bronze-lo:  rgba(176,122,82,0.14);
    /* ── Spacing / radius ── */
    --r:   10px;
    --r-lg:16px;
    --r-xl:22px;
    --sh:  0 2px 8px rgba(0,0,0,0.35), 0 8px 32px rgba(0,0,0,0.25);
    --sh2: 0 4px 24px rgba(0,0,0,0.55);
}

*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

html { scroll-behavior: smooth; }

body {
    font-family: 'Noto Sans JP', sans-serif;
    background: var(--ink);
    color: var(--text);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    /* subtle noise texture */
    background-image:
        radial-gradient(ellipse 80% 60% at 10% 0%, rgba(200,169,126,0.05) 0%, transparent 60%),
        radial-gradient(ellipse 60% 40% at 90% 100%, rgba(110,158,212,0.04) 0%, transparent 60%);
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--ink); }
::-webkit-scrollbar-thumb { background: var(--ink-soft); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-soft); }

/* ── Navbar ── */
.nav {
    background: rgba(15,17,23,0.92);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 200;
}
.nav-in {
    max-width: 800px; margin: 0 auto;
    height: 58px;
    padding: 0 24px;
    display: flex; align-items: center; justify-content: space-between;
}
.logo-text {
    font-family: 'DM Serif Display', serif;
    font-size: 17px;
    color: var(--text);
    letter-spacing: 0.01em;
}
.nav-link {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 16px;
    border: 1px solid var(--border-hi);
    border-radius: 100px;
    font-size: 12px; font-weight: 500; color: var(--text-mid);
    text-decoration: none;
    transition: all .2s;
    letter-spacing: 0.03em;
}
.nav-link:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-lo2);
}

/* ── Utility button ── */
.ghost-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 16px;
    border: 1px solid var(--border-hi);
    border-radius: 100px;
    background: transparent;
    font-family: 'Noto Sans JP', sans-serif;
    font-size: 12px; font-weight: 500; color: var(--text-mid);
    cursor: pointer;
    transition: all .2s;
    letter-spacing: 0.03em;
}
.ghost-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-lo2);
}

/* ── Page wrapper ── */
.wrap {
    max-width: 800px;
    margin: 0 auto;
    padding: 32px 20px 80px;
}

/* ── Section label ── */
.sec-lbl {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-soft);
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 12px;
    padding-left: 2px;
}

/* ── Card ── */
.card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    box-shadow: var(--sh);
}
.card:hover { border-color: var(--border-hi); }

/* ── Divider ── */
.divider {
    height: 1px;
    background: var(--border);
    margin: 28px 0;
}

/* ── Tag / pill ── */
.pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 500;
    background: var(--accent-lo);
    color: var(--accent);
    letter-spacing: 0.02em;
}

/* ── Animations ── */
@keyframes fadeUp {
    from { opacity:0; transform:translateY(10px); }
    to   { opacity:1; transform:translateY(0); }
}
@keyframes scaleIn {
    from { opacity:0; transform:scale(.9); }
    to   { opacity:1; transform:scale(1); }
}

/* ── Responsive ── */
@media(max-width:520px) {
    .wrap { padding: 24px 16px 60px; }
    .nav-in { padding: 0 16px; }
}
"""

# ==========================================
# ランキングページ
# ==========================================
@app.route("/ranking")
def ranking():
    """ランキングページ"""
    ranking_data = get_ranking_data()

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Streak Ranking — なわ太コーチ</title>
<style>
""" + COMMON_THEME_CSS + """

/* ════════════════════════════════════
   ランキング専用スタイル
════════════════════════════════════ */

/* ── Hero ── */
.hero {
    border-bottom: 1px solid var(--border);
    padding: 48px 24px 40px;
    position: relative;
    overflow: hidden;
}
.hero::before {
    content: '';
    position: absolute;
    top: -40%; left: 50%;
    transform: translateX(-50%);
    width: 600px; height: 400px;
    background: radial-gradient(ellipse, rgba(200,169,126,0.08) 0%, transparent 70%);
    pointer-events: none;
}
.hero-in {
    max-width: 800px; margin: 0 auto;
    display: flex; align-items: flex-end; justify-content: space-between; gap: 20px;
    position: relative;
}
.hero-eyebrow {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.22em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 10px;
}
.hero-title {
    font-family: 'DM Serif Display', serif;
    font-size: clamp(40px, 9vw, 68px);
    line-height: 0.95;
    color: var(--text);
    letter-spacing: -0.01em;
}
.hero-title em {
    font-style: italic;
    color: var(--accent);
}
.hero-stat {
    text-align: right;
    padding-bottom: 6px;
    flex-shrink: 0;
}
.hero-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 42px;
    font-weight: 700;
    color: var(--text);
    line-height: 1;
}
.hero-lbl {
    font-size: 11px;
    color: var(--text-soft);
    margin-top: 4px;
    letter-spacing: 0.06em;
}

/* ── Podium ── */
.podium-section {
    margin-bottom: 24px;
}
.podium {
    display: grid;
    grid-template-columns: 1fr 1.08fr 1fr;
    gap: 12px;
    align-items: end;
}
.pod {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: 24px 14px 20px;
    text-align: center;
    box-shadow: var(--sh);
    transition: transform .25s, box-shadow .25s, border-color .25s;
    position: relative;
    overflow: hidden;
    animation: fadeUp .5s ease both;
}
.pod:hover {
    transform: translateY(-4px);
    box-shadow: var(--sh2);
}
.pod::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
}
.pod-1 { border-color: rgba(212,168,67,0.25); animation-delay: .05s; }
.pod-1::after { background: linear-gradient(90deg, transparent, var(--gold), transparent); }
.pod-1::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: radial-gradient(ellipse at 50% 0%, rgba(212,168,67,0.06) 0%, transparent 70%);
    pointer-events: none;
}
.pod-2 { animation-delay: .0s; }
.pod-2::after { background: linear-gradient(90deg, transparent, var(--silver), transparent); }
.pod-3 { animation-delay: .10s; }
.pod-3::after { background: linear-gradient(90deg, transparent, var(--bronze), transparent); }

/* アバター */
.pod-av {
    width: 52px; height: 52px;
    border-radius: 50%;
    margin: 0 auto 12px;
    display: flex; align-items: center; justify-content: center;
    font-family: 'DM Serif Display', serif;
    font-size: 20px;
    border: 1.5px solid;
}
.pod-1 .pod-av { width: 60px; height: 60px; font-size: 24px; }
.av-gold   { background: rgba(212,168,67,0.12);  border-color: rgba(212,168,67,0.4);  color: var(--gold); }
.av-silver { background: rgba(142,160,184,0.1);  border-color: rgba(142,160,184,0.3); color: var(--silver); }
.av-bronze { background: rgba(176,122,82,0.1);   border-color: rgba(176,122,82,0.3);  color: var(--bronze); }
.av-def    { background: rgba(110,158,212,0.1);  border-color: rgba(110,158,212,0.25);color: var(--blue); }

.pod-rank {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 4px;
}
.pod-1 .pod-rank { color: var(--gold); }
.pod-2 .pod-rank { color: var(--silver); }
.pod-3 .pod-rank { color: var(--bronze); }

.pod-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 12px;
    word-break: break-word;
    line-height: 1.4;
}
.pod-1 .pod-name { font-size: 14px; }

.pod-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 38px;
    font-weight: 700;
    line-height: 1;
}
.pod-1 .pod-num { font-size: 48px; color: var(--gold); }
.pod-2 .pod-num { color: var(--silver); }
.pod-3 .pod-num { color: var(--bronze); }
.pod-unit { font-size: 11px; color: var(--text-soft); margin-top: 4px; }

.pod-lv {
    display: inline-block;
    margin-top: 10px;
    font-size: 10px;
    font-weight: 500;
    padding: 2px 9px;
    border-radius: 100px;
    background: var(--accent-lo2);
    color: var(--text-soft);
    letter-spacing: 0.04em;
}

/* ── List table ── */
.rank-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    box-shadow: var(--sh);
    overflow: hidden;
}
.rank-head {
    display: grid;
    grid-template-columns: 56px 1fr auto;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 10px;
    font-weight: 600;
    color: var(--text-soft);
    letter-spacing: 0.14em;
    text-transform: uppercase;
}
.rank-row {
    display: grid;
    grid-template-columns: 56px 1fr auto;
    align-items: center;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    transition: background .15s;
    animation: fadeUp .4s ease both;
}
.rank-row:last-child { border-bottom: none; }
.rank-row:hover { background: rgba(255,255,255,0.025); }
{% for i in range(15) %}
.rank-row:nth-child({{ i + 1 }}) { animation-delay: {{ i * 0.04 }}s; }
{% endfor %}

/* 順位数字 */
.pos {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 700;
    color: var(--text-soft);
    text-align: center;
}
.rank-row:nth-child(1) .pos { color: var(--gold); }
.rank-row:nth-child(2) .pos { color: var(--silver); }
.rank-row:nth-child(3) .pos { color: var(--bronze); }

/* ユーザーセル */
.user-cell {
    display: flex; align-items: center; gap: 12px;
    min-width: 0;
}
.list-av {
    width: 38px; height: 38px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-family: 'DM Serif Display', serif;
    font-size: 15px;
    border: 1.5px solid var(--border-hi);
}
.rank-row:nth-child(1) .list-av { border-color: rgba(212,168,67,0.4); }
.rank-row:nth-child(2) .list-av { border-color: rgba(142,160,184,0.3); }
.rank-row:nth-child(3) .list-av { border-color: rgba(176,122,82,0.3); }

.u-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.u-lv {
    font-size: 11px;
    color: var(--text-soft);
    margin-top: 2px;
}

/* ストリークバッジ */
.streak-badge {
    display: flex; align-items: baseline; gap: 3px;
}
.s-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    color: var(--accent);
    line-height: 1;
}
.rank-row:nth-child(1) .s-num { color: var(--gold); }
.rank-row:nth-child(2) .s-num { color: var(--silver); }
.rank-row:nth-child(3) .s-num { color: var(--bronze); }
.s-unit { font-size: 11px; color: var(--text-soft); }

/* Empty state */
.empty {
    text-align: center;
    padding: 72px 20px;
}
.empty-ic { font-size: 48px; opacity: .2; margin-bottom: 18px; }
.empty-t  { font-size: 16px; font-weight: 600; color: var(--text-mid); margin-bottom: 6px; }
.empty-s  { font-size: 13px; color: var(--text-soft); }

.footer {
    text-align: center;
    margin-top: 36px;
    font-size: 11px;
    color: var(--text-soft);
    letter-spacing: 0.06em;
}

@media(max-width:480px) {
    .podium  { gap: 8px; }
    .pod     { padding: 16px 10px 16px; }
    .pod-1 .pod-num { font-size: 38px; }
    .rank-head, .rank-row { padding-left: 14px; padding-right: 14px; }
}
</style>
</head>
<body>

<nav class="nav">
    <div class="nav-in">
        <button class="ghost-btn" onclick="location.reload()">
            ↻ &nbsp;更新
        </button>
    </div>
</nav>

<div class="hero">
    <div class="hero-in">
        <div>
            <div class="hero-eyebrow">Leaderboard</div>
            <div class="hero-title">Streak<br><em>Ranking</em></div>
        </div>
        <div class="hero-stat">
            <div class="hero-num">{{ ranking_data|length }}</div>
            <div class="hero-lbl">参加者</div>
        </div>
    </div>
</div>

<div class="wrap">

{% if ranking_data|length >= 3 %}
<div class="podium-section">
    <div class="sec-lbl">Top 3</div>
    <div class="podium">
        <!-- 2位 -->
        <div class="pod pod-2">
            <div class="pod-av av-silver">{{ ranking_data[1]['nickname'][0] }}</div>
            <div class="pod-rank">2nd Place</div>
            <div class="pod-name">{{ ranking_data[1]['nickname'] }}</div>
            <div class="pod-num">{{ ranking_data[1]['streak_days'] }}</div>
            <div class="pod-unit">日連続</div>
            <div class="pod-lv">{{ ranking_data[1]['level'] }}</div>
        </div>
        <!-- 1位 -->
        <div class="pod pod-1">
            <div class="pod-av av-gold">{{ ranking_data[0]['nickname'][0] }}</div>
            <div class="pod-rank">1st Place</div>
            <div class="pod-name">{{ ranking_data[0]['nickname'] }}</div>
            <div class="pod-num">{{ ranking_data[0]['streak_days'] }}</div>
            <div class="pod-unit">日連続</div>
            <div class="pod-lv">{{ ranking_data[0]['level'] }}</div>
        </div>
        <!-- 3位 -->
        <div class="pod pod-3">
            <div class="pod-av av-bronze">{{ ranking_data[2]['nickname'][0] }}</div>
            <div class="pod-rank">3rd Place</div>
            <div class="pod-name">{{ ranking_data[2]['nickname'] }}</div>
            <div class="pod-num">{{ ranking_data[2]['streak_days'] }}</div>
            <div class="pod-unit">日連続</div>
            <div class="pod-lv">{{ ranking_data[2]['level'] }}</div>
        </div>
    </div>
</div>
{% endif %}

<div class="sec-lbl">Full Ranking</div>
<div class="rank-card">
    <div class="rank-head">
        <span style="text-align:center;">#</span>
        <span style="padding-left:8px;">Player</span>
        <span>Streak</span>
    </div>

    {% if ranking_data|length > 0 %}
    {% for user in ranking_data %}
    <div class="rank-row">
        <div class="pos">{{ loop.index }}</div>
        <div class="user-cell">
            <div class="list-av
                {% if loop.index == 1 %}av-gold
                {% elif loop.index == 2 %}av-silver
                {% elif loop.index == 3 %}av-bronze
                {% else %}av-def{% endif %}">
                {{ user['nickname'][0] }}
            </div>
            <div>
                <div class="u-name">{{ user['nickname'] }}</div>
                <div class="u-lv">{{ user['level'] }}</div>
            </div>
        </div>
        <div class="streak-badge">
            <span class="s-num">{{ user['streak_days'] }}</span>
            <span class="s-unit">日</span>
        </div>
    </div>
    {% endfor %}
    {% else %}
    <div class="empty">
        <div class="empty-ic">🏆</div>
        <div class="empty-t">まだランキングデータがありません</div>
        <div class="empty-s">毎日「今すぐ」を送って記録をつけよう</div>
    </div>
    {% endif %}
</div>

<div class="footer">© なわ太コーチ — Jump Rope AI Coach</div>

</div><!-- /wrap -->
</body>
</html>
"""
    return render_template_string(html, ranking_data=ranking_data)


# ==========================================
# 設定ページ
# ==========================================
@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """設定画面"""
    try:
        user_id = request.args.get('user_id')

        if not user_id:
            return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>エラー — なわ太コーチ</title>
<style>
{COMMON_THEME_CSS}
.body-center {{
    min-height:100vh;
    display:flex; flex-direction:column;
}}
.center {{
    flex:1;
    display:flex; align-items:center; justify-content:center;
    padding:40px 20px;
}}
.err-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-xl);
    padding: 52px 36px;
    text-align: center;
    max-width: 360px;
    width: 100%;
    box-shadow: var(--sh2);
}}
.err-ic {{ font-size: 44px; margin-bottom: 20px; opacity:.6; }}
.err-title {{ font-family:'DM Serif Display',serif; font-size:22px; color:var(--text); margin-bottom:10px; }}
.err-desc  {{ font-size:13px; color:var(--text-mid); line-height:1.7; }}
</style>
</head>
<body class="body-center">
<div class="center">
<div class="err-card">
<div class="err-ic">⚠️</div>
<div class="err-title">アクセスエラー</div>
<p class="err-desc">ユーザーIDが見つかりません。<br>LINEから再度アクセスしてください。</p>
</div>
</div>
</body>
</html>""", 400

        if request.method == 'POST':
            new_level = request.form.get('level')
            new_personality = request.form.get('coach_personality', '優しい')
            new_nickname = request.form.get('nickname', '').strip()

            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n⚙️ [{timestamp}] Settings update POST received")
            print(f"   User ID: {user_id[:8]}...")
            print(f"   Form data: level={new_level}, personality={new_personality}, nickname={new_nickname}")

            if new_nickname and len(new_nickname) > 10:
                new_nickname = new_nickname[:10]

            update_user_settings(user_id, level=new_level, coach_personality=new_personality, nickname=new_nickname)

            ranking_url = f"{APP_PUBLIC_URL}/ranking"

            return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>設定完了 — なわ太コーチ</title>
<style>
{COMMON_THEME_CSS}
.body-center {{ min-height:100vh; display:flex; flex-direction:column; }}
.center {{ flex:1; display:flex; align-items:center; justify-content:center; padding:40px 20px; }}
.done-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-xl);
    padding: 52px 36px;
    text-align: center;
    max-width: 380px;
    width: 100%;
    box-shadow: var(--sh2);
    animation: scaleIn .45s cubic-bezier(.34,1.56,.64,1) both;
}}
.check-circle {{
    width: 72px; height: 72px;
    border-radius: 50%;
    background: var(--green-lo);
    border: 1.5px solid rgba(95,184,138,0.3);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 28px;
    font-size: 30px;
}}
.done-title {{
    font-family: 'DM Serif Display', serif;
    font-size: 24px;
    color: var(--text);
    margin-bottom: 10px;
}}
.done-desc {{
    font-size: 13px;
    color: var(--text-mid);
    line-height: 1.8;
    margin-bottom: 32px;
}}
.primary-btn {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 13px 28px;
    background: var(--accent);
    color: var(--ink);
    text-decoration: none;
    border-radius: 100px;
    font-size: 13px; font-weight: 700;
    letter-spacing: 0.04em;
    transition: all .2s;
    box-shadow: 0 4px 20px rgba(200,169,126,0.3);
}}
.primary-btn:hover {{
    background: var(--accent-hi);
    transform: translateY(-2px);
    box-shadow: 0 6px 28px rgba(200,169,126,0.4);
}}
.done-note {{
    margin-top: 20px;
    font-size: 11px;
    color: var(--text-soft);
    letter-spacing: 0.04em;
}}
</style>
</head>
<body class="body-center">
<div class="center">
<div class="done-card">
<div class="check-circle">✓</div>
<div class="done-title">設定を保存しました</div>
<p class="done-desc">「今すぐ」と送信すると<br>新しい設定で課題が届きます。</p>
<a href="{ranking_url}" class="primary-btn">🏆 ランキングを見る</a>
<div class="done-note">LINEの画面に戻ってください</div>
</div>
</div>
</body>
</html>"""

        current_settings = get_user_settings(user_id)
        current_nickname   = current_settings.get('nickname', '') or ''
        current_level      = current_settings['level']
        current_personality= current_settings.get('coach_personality', '優しい')

        personality_emojis = {
            "熱血":"🔥","優しい":"🌿","厳しい":"⚡","フレンドリー":"☀️","冷静":"🔬"
        }
        personality_descs = {
            "熱血":   "情熱的に鼓舞する",
            "優しい": "丁寧で穏やかに",
            "厳しい": "ストイックに追い込む",
            "フレンドリー": "タメ口で親しみやすく",
            "冷静":   "論理的・分析的に"
        }

        ranking_url = f"{APP_PUBLIC_URL}/ranking"
        initial = current_nickname[0] if current_nickname else "？"
        nick_len = len(current_nickname)

        # レベルカード生成
        level_cards_html = ""
        for lname, linfo in USER_LEVELS.items():
            active = "active" if lname == current_level else ""
            level_cards_html += f"""
<div class="lv-card {active}" onclick="selLv('{lname}',this)">
    <div class="lv-chk">✓</div>
    <div class="lv-name">{lname}</div>
    <div class="lv-desc">{linfo['description']}</div>
</div>"""

        # パーソナリティカード生成
        pers_cards_html = ""
        for pname in COACH_PERSONALITIES:
            active = "active" if pname == current_personality else ""
            emoji = personality_emojis.get(pname, "")
            desc  = personality_descs.get(pname, "")
            pers_cards_html += f"""
<div class="p-card {active}" onclick="selP('{pname}',this)">
    <div class="p-em">{emoji}</div>
    <div class="p-info">
        <div class="p-name">{pname}</div>
        <div class="p-desc">{desc}</div>
    </div>
    <div class="p-radio"></div>
</div>"""

        html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>練習設定 — なわ太コーチ</title>
<style>
{COMMON_THEME_CSS}

/* ════════════════════════════════════
   設定ページ専用スタイル
════════════════════════════════════ */

/* ── プロフィールカード ── */
.profile-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: 20px;
    display: flex; align-items: center; gap: 16px;
    margin-bottom: 32px;
    box-shadow: var(--sh);
}}
.av-circle {{
    width: 56px; height: 56px;
    border-radius: 50%;
    border: 1.5px solid var(--border-hi);
    background: var(--blue-lo);
    display: flex; align-items: center; justify-content: center;
    font-family: 'DM Serif Display', serif;
    font-size: 22px;
    color: var(--blue);
    flex-shrink: 0;
    transition: border-color .2s;
}}
.p-name-lg  {{ font-size: 16px; font-weight: 600; color: var(--text); margin-bottom: 3px; }}
.p-meta     {{ font-size: 12px; color: var(--text-soft); }}
.p-edit-hint{{ font-size: 11px; color: var(--accent); margin-top: 4px; opacity:.8; }}

/* ── テキスト入力 ── */
.inp-wrap {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r);
    overflow: hidden;
    box-shadow: var(--sh);
    transition: border-color .2s, box-shadow .2s;
}}
.inp-wrap:focus-within {{
    border-color: rgba(200,169,126,0.5);
    box-shadow: 0 0 0 3px rgba(200,169,126,0.08);
}}
.inp-row {{
    display: flex; align-items: center;
    padding: 0 14px;
}}
.inp-ic {{ font-size: 15px; margin-right: 10px; flex-shrink: 0; opacity: .7; }}
.inp-field {{
    flex: 1;
    background: transparent;
    border: none; outline: none;
    font-family: 'Noto Sans JP', sans-serif;
    font-size: 15px;
    font-weight: 500;
    color: var(--text);
    padding: 13px 0;
}}
.inp-field::placeholder {{ color: var(--text-soft); font-weight: 400; }}
.inp-cnt {{ font-size: 11px; color: var(--text-soft); flex-shrink: 0; font-family: 'JetBrains Mono', monospace; }}
.inp-hint {{
    font-size: 11px; color: var(--text-soft);
    padding: 0 14px 10px;
    border-top: 1px solid var(--border);
    padding-top: 8px;
    opacity: .8;
}}

/* ── レベル選択グリッド ── */
.lv-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
}}
.lv-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r);
    padding: 16px 14px;
    cursor: pointer;
    transition: all .2s;
    box-shadow: var(--sh);
    position: relative;
}}
.lv-card:hover {{
    border-color: var(--border-hi);
    transform: translateY(-2px);
    background: var(--card-hov);
}}
.lv-card.active {{
    border-color: rgba(200,169,126,0.5);
    background: var(--accent-lo);
}}
.lv-chk {{
    position: absolute; top: 10px; right: 10px;
    width: 18px; height: 18px;
    border-radius: 50%;
    border: 1.5px solid var(--text-soft);
    display: flex; align-items: center; justify-content: center;
    font-size: 9px;
    color: transparent;
    transition: all .2s;
}}
.lv-card.active .lv-chk {{
    background: var(--accent);
    border-color: var(--accent);
    color: var(--ink);
}}
.lv-name {{ font-size: 14px; font-weight: 600; color: var(--text); margin-bottom: 4px; }}
.lv-desc {{ font-size: 11px; color: var(--text-soft); line-height: 1.5; }}

/* ── パーソナリティリスト ── */
.p-list {{ display: flex; flex-direction: column; gap: 8px; }}
.p-card {{
    display: flex; align-items: center; gap: 14px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r);
    padding: 13px 16px;
    cursor: pointer;
    transition: all .2s;
    box-shadow: var(--sh);
}}
.p-card:hover {{
    border-color: var(--border-hi);
    background: var(--card-hov);
}}
.p-card.active {{
    border-color: rgba(200,169,126,0.5);
    background: var(--accent-lo);
}}
.p-em   {{ font-size: 20px; width: 28px; text-align: center; flex-shrink: 0; }}
.p-info {{ flex: 1; min-width: 0; }}
.p-name {{ font-size: 14px; font-weight: 600; color: var(--text); }}
.p-desc {{ font-size: 11px; color: var(--text-soft); margin-top: 2px; }}
.p-radio {{
    width: 18px; height: 18px;
    border-radius: 50%;
    border: 1.5px solid var(--text-soft);
    flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    transition: all .2s;
}}
.p-card.active .p-radio {{
    border-color: var(--accent);
    background: var(--accent);
}}
.p-card.active .p-radio::after {{
    content: '';
    width: 6px; height: 6px;
    background: var(--ink);
    border-radius: 50%;
}}

/* ── 保存ボタン ── */
.save-btn {{
    width: 100%;
    padding: 15px;
    background: var(--accent);
    color: var(--ink);
    border: none;
    border-radius: var(--r);
    font-family: 'Noto Sans JP', sans-serif;
    font-size: 15px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 0.04em;
    transition: all .25s;
    box-shadow: 0 4px 20px rgba(200,169,126,0.25);
    margin-top: 8px;
}}
.save-btn:hover {{
    background: var(--accent-hi);
    transform: translateY(-2px);
    box-shadow: 0 6px 28px rgba(200,169,126,0.35);
}}
.save-btn:active {{ transform: translateY(0); }}

/* ── ランキングバナー ── */
.rank-banner {{
    display: flex; align-items: center; justify-content: space-between;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: 18px 20px;
    text-decoration: none;
    box-shadow: var(--sh);
    margin-top: 14px;
    transition: all .2s;
}}
.rank-banner:hover {{
    border-color: rgba(200,169,126,0.35);
    background: var(--card-hov);
    transform: translateY(-2px);
}}
.rb-l {{ display: flex; align-items: center; gap: 14px; }}
.rb-ic {{ font-size: 26px; }}
.rb-title {{ font-size: 14px; font-weight: 600; color: var(--text); }}
.rb-sub   {{ font-size: 11px; color: var(--text-soft); margin-top: 2px; }}
.rb-arr   {{ font-size: 18px; color: var(--text-soft); }}

@media(max-width:400px) {{
    .lv-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<nav class="nav">
    <div class="nav-in">
        <a href="{ranking_url}" class="nav-link">🏆 ランキング</a>
    </div>
</nav>

<div class="wrap">

<div class="profile-card">
    <div class="av-circle" id="avCircle">{initial}</div>
    <div>
        <div class="p-name-lg" id="heroName">{current_nickname or '名前を設定しよう'}</div>
        <div class="p-meta">{current_level} · {current_personality}コーチ</div>
        <div class="p-edit-hint">設定を編集中</div>
    </div>
</div>

<form method="POST" id="sf">
    <input type="hidden" name="level" id="lvInp" value="{current_level}">
    <input type="hidden" name="coach_personality" id="pInp" value="{current_personality}">

    <!-- ── ニックネーム ── -->
    <div style="margin-bottom:28px;">
        <div class="sec-lbl">ニックネーム</div>
        <div class="inp-wrap">
            <div class="inp-row">
                <span class="inp-ic">✏️</span>
                <input
                    type="text"
                    name="nickname"
                    class="inp-field"
                    value="{current_nickname}"
                    maxlength="10"
                    placeholder="例：ジャンプ太郎"
                    id="nickInp"
                    oninput="onNick(this)"
                >
                <span class="inp-cnt" id="cnt">{nick_len}/10</span>
            </div>
            <div class="inp-hint">ランキングに表示されます（10文字まで）</div>
        </div>
    </div>

    <div class="divider"></div>

    <!-- ── レベル ── -->
    <div style="margin-bottom:28px;">
        <div class="sec-lbl">練習レベル</div>
        <div class="lv-grid">{level_cards_html}</div>
    </div>

    <div class="divider"></div>

    <!-- ── コーチ性格 ── -->
    <div style="margin-bottom:28px;">
        <div class="sec-lbl">コーチの性格</div>
        <div class="p-list">{pers_cards_html}</div>
    </div>

    <button type="submit" class="save-btn">設定を保存する</button>
</form>

<a href="{ranking_url}" class="rank-banner">
    <div class="rb-l">
        <div class="rb-ic">🏆</div>
        <div>
            <div class="rb-title">連続記録ランキング</div>
            <div class="rb-sub">みんなの記録をチェック</div>
        </div>
    </div>
    <div class="rb-arr">›</div>
</a>

</div><!-- /wrap -->

<script>
function onNick(el) {{
    document.getElementById('cnt').textContent = el.value.length + '/10';
    document.getElementById('heroName').textContent = el.value || '名前を設定しよう';
    document.getElementById('avCircle').textContent = el.value ? el.value[0] : '？';
}}
function selLv(name, el) {{
    document.querySelectorAll('.lv-card').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('lvInp').value = name;
}}
function selP(name, el) {{
    document.querySelectorAll('.p-card').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('pInp').value = name;
}}
</script>
</body>
</html>"""

        return render_template_string(html)

    except Exception as e:
        print(f"❌ Settings page error: {e}")
        import traceback
        traceback.print_exc()
        return f"Internal Server Error: {str(e)}", 500


# ==========================================
# LINE Webhook
# ==========================================
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

        # 今すぐ課題を配信（1日3回まで）
        if text == "今すぐ":
            today = datetime.now(JST).strftime("%Y-%m-%d")

            resp = supabase.table("users").select(
                "immediate_request_count, last_immediate_request_date"
            ).eq("user_id", user_id).execute()

            immediate_count = 0
            last_request_date = None

            if resp.data:
                immediate_count = resp.data[0].get("immediate_request_count") or 0
                last_request_date = resp.data[0].get("last_immediate_request_date")

            if last_request_date != today:
                immediate_count = 0
                supabase.table("users").update({
                    "immediate_request_count": 0,
                    "last_immediate_request_date": today,
                }).eq("user_id", user_id).execute()

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

            supabase.table("users").update({
                "immediate_request_count": immediate_count + 1,
                "last_immediate_request_date": today,
            }).eq("user_id", user_id).execute()

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

        # フィードバック: 成功
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

        # フィードバック: 難しかった
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
