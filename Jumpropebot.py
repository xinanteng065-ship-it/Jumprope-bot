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
LOGO_IMAGE_URL = os.environ.get("LOGO_IMAGE_URL")

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
            "level, nickname, coach_personality, delivery_count, success_count, "
            "difficulty_count, support_shown, last_challenge, streak_days, "
            "last_challenge_date, received_welcome_stamp"
        ).eq("user_id", user_id).execute()

        if not response.data:
            # 新規ユーザーを作成
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

        # 現在の設定を取得
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
            # 新規ユーザー
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

        # 連続記録の判定
        if last_date == today:
            # 今日すでに課題をもらっている場合は何もしない
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
        # まず現在の値を取得してインクリメント
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

    # 10日ごとの特別課題判定
    is_special_day = (streak_days > 0 and streak_days % 10 == 0 and streak_days <= 100)

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

        # 10日ごとの特別課題（採点アプリ）
        if is_special_day and streak_days <= 100:
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

        # 連続記録を更新
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

@app.route("/ranking")
def ranking():
    """ランキングページ - 明るいクリーンデザイン"""
    ranking_data = get_ranking_data()

    # ロゴHTML（環境変数 LOGO_IMAGE_URL が設定されていれば画像、なければテキスト）
    if LOGO_IMAGE_URL:
        logo_html = f'<img src="{LOGO_IMAGE_URL}" alt="なわ太コーチ" style="height:30px;width:auto;object-fit:contain;display:block;">'
    else:
        logo_html = '<span style="font-size:15px;font-weight:700;color:#1e293b;">🪢 なわ太コーチ</span>'

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>連続記録ランキング — なわ太コーチ</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;900&family=Barlow+Condensed:wght@700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:       #f1f5f9;
            --surface:  #ffffff;
            --surf2:    #f8fafc;
            --border:   #e2e8f0;
            --text:     #1e293b;
            --muted:    #64748b;
            --accent:   #f97316;
            --acc2:     #fb923c;
            --gold:     #f59e0b;
            --silver:   #94a3b8;
            --bronze:   #b87333;
            --r:        14px;
            --sh:       0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.06);
            --sh2:      0 8px 32px rgba(0,0,0,0.11);
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: "Noto Sans JP", sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }

        /* ─── ナビバー ─── */
        .nav {
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            position: sticky; top:0; z-index:99;
            padding: 0 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .nav-in {
            max-width: 740px; margin: 0 auto;
            height: 56px;
            display: flex; align-items: center; justify-content: space-between;
        }
        .refresh-btn {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 7px 16px;
            background: var(--surf2); border: 1px solid var(--border);
            border-radius: 100px;
            font-size: 12px; font-weight: 600; color: var(--muted);
            cursor: pointer; font-family: inherit; transition: .2s;
        }
        .refresh-btn:hover { background: var(--border); color: var(--text); }
        .spin { display: inline-block; transition: transform .5s; }
        .refresh-btn:hover .spin { transform: rotate(180deg); }

        /* ─── ヒーローバナー ─── */
        .hero {
            background: linear-gradient(135deg, #fff7ed 0%, #ffffff 50%, #eff6ff 100%);
            border-bottom: 1px solid var(--border);
            padding: 32px 20px 28px;
        }
        .hero-in {
            max-width: 740px; margin: 0 auto;
            display: flex; align-items: flex-end; justify-content: space-between; gap: 16px;
        }
        .hero-title {
            font-family: "Barlow Condensed", sans-serif;
            font-size: clamp(44px, 10vw, 64px);
            font-weight: 900;
            line-height: .92;
            letter-spacing: .01em;
            color: var(--text);
        }
        .hero-title .hl { color: var(--accent); }
        .hero-stat { text-align: right; padding-bottom: 4px; }
        .hero-stat-num {
            font-family: "Barlow Condensed", sans-serif;
            font-size: 36px; font-weight: 900;
            color: var(--text); line-height: 1;
        }
        .hero-stat-lbl { font-size: 12px; color: var(--muted); margin-top: 2px; }

        /* ─── コンテンツ ─── */
        .wrap { max-width: 740px; margin: 0 auto; padding: 24px 20px 60px; }

        /* ─── 表彰台 ─── */
        .podium { display: grid; grid-template-columns: 1fr 1.12fr 1fr; gap: 10px; margin-bottom: 20px; align-items: end; }
        .pod {
            background: var(--surface);
            border: 1.5px solid var(--border);
            border-radius: var(--r);
            padding: 22px 12px 18px;
            text-align: center;
            box-shadow: var(--sh);
            transition: .25s;
            position: relative; overflow: hidden;
        }
        .pod::after { content:""; position:absolute; bottom:0; left:0; right:0; height:3px; }
        .pod:hover { transform: translateY(-5px); box-shadow: var(--sh2); }

        .pod-1 { background: linear-gradient(170deg,#fffbeb,#fff); border-color: rgba(245,158,11,.3); }
        .pod-1::after { background: var(--gold); }
        .pod-2 { background: linear-gradient(170deg,#f8fafc,#fff); border-color: rgba(148,163,184,.3); }
        .pod-2::after { background: var(--silver); }
        .pod-3 { background: linear-gradient(170deg,#fdf8f0,#fff); border-color: rgba(184,115,51,.3); }
        .pod-3::after { background: var(--bronze); }

        /* アバター */
        .pod-av {
            width: 52px; height: 52px; border-radius: 50%;
            margin: 0 auto 10px;
            display: flex; align-items: center; justify-content: center;
            font-family: "Barlow Condensed", sans-serif;
            font-size: 22px; font-weight: 900;
            border: 2.5px solid rgba(0,0,0,.07);
        }
        .pod-1 .pod-av { width: 62px; height: 62px; font-size: 26px; border-color: var(--gold); }
        .av-g { background: linear-gradient(135deg,#fde68a,#f59e0b); color: #78350f; }
        .av-s { background: linear-gradient(135deg,#e2e8f0,#94a3b8); color: #334155; }
        .av-b { background: linear-gradient(135deg,#fde8cc,#b87333); color: #7c2d12; }
        .av-n { background: linear-gradient(135deg,#dbeafe,#3b82f6); color: #1e3a8a; }

        .pod-medal { font-size: 22px; display: block; margin-bottom: 4px; }
        .pod-1 .pod-medal { font-size: 28px; }
        .pod-place {
            font-family: "Barlow Condensed", sans-serif;
            font-size: 10px; font-weight: 700; letter-spacing: .18em;
            margin-bottom: 5px;
        }
        .pod-1 .pod-place { color: var(--gold); }
        .pod-2 .pod-place { color: var(--silver); }
        .pod-3 .pod-place { color: var(--bronze); }
        .pod-name { font-size: 12px; font-weight: 700; color: var(--text); margin-bottom: 8px; word-break: break-word; line-height: 1.4; }
        .pod-1 .pod-name { font-size: 14px; }
        .pod-num {
            font-family: "Barlow Condensed", sans-serif;
            font-size: 40px; font-weight: 900; line-height: 1;
        }
        .pod-1 .pod-num { font-size: 50px; color: var(--gold); }
        .pod-2 .pod-num { color: var(--silver); }
        .pod-3 .pod-num { color: var(--bronze); }
        .pod-unit { font-size: 11px; color: var(--muted); margin-top: 2px; }
        .pod-lv { display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 100px; margin-top: 8px; background: rgba(0,0,0,.05); color: var(--muted); }

        /* ─── ランキングリスト ─── */
        .rank-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--r);
            box-shadow: var(--sh);
            overflow: hidden;
        }
        .rank-head {
            display: grid; grid-template-columns: 52px 1fr auto;
            padding: 10px 20px;
            background: var(--surf2);
            border-bottom: 1px solid var(--border);
            font-size: 10px; font-weight: 700; color: var(--muted);
            letter-spacing: .1em; text-transform: uppercase;
        }
        .rank-row {
            display: grid; grid-template-columns: 52px 1fr auto;
            align-items: center;
            padding: 13px 20px;
            border-bottom: 1px solid var(--border);
            transition: background .15s;
            animation: slide .35s ease both;
        }
        @keyframes slide { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
        .rank-row:last-child { border-bottom: none; }
        .rank-row:hover { background: var(--surf2); }
        .rank-row:nth-child(1){animation-delay:.04s} .rank-row:nth-child(2){animation-delay:.08s}
        .rank-row:nth-child(3){animation-delay:.11s} .rank-row:nth-child(4){animation-delay:.14s}
        .rank-row:nth-child(5){animation-delay:.17s} .rank-row:nth-child(6){animation-delay:.20s}
        .rank-row:nth-child(7){animation-delay:.23s} .rank-row:nth-child(8){animation-delay:.26s}
        .rank-row:nth-child(9){animation-delay:.29s} .rank-row:nth-child(10){animation-delay:.32s}

        .pos {
            font-family: "Barlow Condensed", sans-serif;
            font-size: 22px; font-weight: 700;
            color: var(--muted); text-align: center;
        }
        .rank-row:nth-child(1) .pos { color: var(--gold); }
        .rank-row:nth-child(2) .pos { color: var(--silver); }
        .rank-row:nth-child(3) .pos { color: var(--bronze); }

        .user-cell { display: flex; align-items: center; gap: 11px; min-width: 0; }
        .list-av {
            width: 38px; height: 38px; border-radius: 50%;
            flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            font-family: "Barlow Condensed", sans-serif;
            font-size: 15px; font-weight: 900;
            border: 2px solid var(--border);
        }
        .rank-row:nth-child(1) .list-av { border-color: var(--gold); }
        .rank-row:nth-child(2) .list-av { border-color: var(--silver); }
        .rank-row:nth-child(3) .list-av { border-color: var(--bronze); }

        .u-name { font-size: 14px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .u-lv { font-size: 11px; color: var(--muted); margin-top: 1px; }

        .badge {
            display: flex; align-items: center; gap: 5px;
            padding: 6px 13px;
            background: #fff7ed;
            border: 1px solid rgba(249,115,22,.2);
            border-radius: 100px; white-space: nowrap;
        }
        .rank-row:nth-child(1) .badge { background: #fffbeb; border-color: rgba(245,158,11,.3); }
        .rank-row:nth-child(2) .badge { background: #f8fafc; border-color: rgba(148,163,184,.3); }
        .rank-row:nth-child(3) .badge { background: #fdf8f0; border-color: rgba(184,115,51,.3); }
        .b-num {
            font-family: "Barlow Condensed", sans-serif;
            font-size: 20px; font-weight: 900; color: var(--accent); line-height: 1;
        }
        .rank-row:nth-child(1) .b-num { color: var(--gold); }
        .rank-row:nth-child(2) .b-num { color: var(--silver); }
        .rank-row:nth-child(3) .b-num { color: var(--bronze); }
        .b-unit { font-size: 11px; color: var(--muted); }

        .empty { text-align: center; padding: 60px 20px; }
        .empty-ic { font-size: 52px; opacity: .2; margin-bottom: 16px; }
        .empty-t { font-size: 16px; font-weight: 700; color: var(--muted); margin-bottom: 6px; }
        .empty-s { font-size: 13px; color: var(--muted); opacity: .7; }
        .footer { text-align: center; margin-top: 32px; font-size: 12px; color: var(--muted); }

        @media(max-width:480px) {
            .podium { gap:6px; }
            .pod { padding: 14px 8px 14px; }
            .pod-1 .pod-av { width:50px; height:50px; font-size:22px; }
            .pod-1 .pod-num { font-size:40px; }
            .rank-head,.rank-row { padding-left:14px; padding-right:14px; }
            .rank-head { grid-template-columns: 42px 1fr auto; }
            .rank-row { grid-template-columns: 42px 1fr auto; }
            .list-av { width:34px; height:34px; font-size:13px; }
        }
    </style>
</head>
<body>

<nav class="nav">
    <div class="nav-in">
        """ + logo_html + """
        <button class="refresh-btn" onclick="location.reload()"><span class="spin">↻</span> 更新</button>
    </div>
</nav>

<div class="hero">
    <div class="hero-in">
        <div class="hero-title">STREAK<br><span class="hl">RANKING</span></div>
        <div class="hero-stat">
            <div class="hero-stat-num">{{ ranking_data|length }}</div>
            <div class="hero-stat-lbl">人が参加中</div>
        </div>
    </div>
</div>

<div class="wrap">

{% if ranking_data|length >= 3 %}
<div class="podium">
    <!-- 2位 -->
    <div class="pod pod-2">
        <div class="pod-av av-s">{{ ranking_data[1]['nickname'][0] }}</div>
        <span class="pod-medal">🥈</span>
        <div class="pod-place">2ND PLACE</div>
        <div class="pod-name">{{ ranking_data[1]['nickname'] }}</div>
        <div class="pod-num">{{ ranking_data[1]['streak_days'] }}</div>
        <div class="pod-unit">日連続</div>
        <div class="pod-lv">{{ ranking_data[1]['level'] }}</div>
    </div>
    <!-- 1位 -->
    <div class="pod pod-1">
        <div class="pod-av av-g">{{ ranking_data[0]['nickname'][0] }}</div>
        <span class="pod-medal">🥇</span>
        <div class="pod-place">1ST PLACE</div>
        <div class="pod-name">{{ ranking_data[0]['nickname'] }}</div>
        <div class="pod-num">{{ ranking_data[0]['streak_days'] }}</div>
        <div class="pod-unit">日連続</div>
        <div class="pod-lv">{{ ranking_data[0]['level'] }}</div>
    </div>
    <!-- 3位 -->
    <div class="pod pod-3">
        <div class="pod-av av-b">{{ ranking_data[2]['nickname'][0] }}</div>
        <span class="pod-medal">🥉</span>
        <div class="pod-place">3RD PLACE</div>
        <div class="pod-name">{{ ranking_data[2]['nickname'] }}</div>
        <div class="pod-num">{{ ranking_data[2]['streak_days'] }}</div>
        <div class="pod-unit">日連続</div>
        <div class="pod-lv">{{ ranking_data[2]['level'] }}</div>
    </div>
</div>
{% endif %}

<div class="rank-card">
    <div class="rank-head">
        <span style="text-align:center">#</span>
        <span style="padding-left:8px">ユーザー</span>
        <span>連続記録</span>
    </div>
    {% if ranking_data|length > 0 %}
    {% for user in ranking_data %}
    <div class="rank-row">
        <div class="pos">{{ loop.index }}</div>
        <div class="user-cell">
            <div class="list-av {% if loop.index==1 %}av-g{% elif loop.index==2 %}av-s{% elif loop.index==3 %}av-b{% else %}av-n{% endif %}">{{ user['nickname'][0] }}</div>
            <div>
                <div class="u-name">{{ user['nickname'] }}</div>
                <div class="u-lv">{{ user['level'] }}</div>
            </div>
        </div>
        <div class="badge"><span>🔥</span><span class="b-num">{{ user['streak_days'] }}</span><span class="b-unit">日</span></div>
    </div>
    {% endfor %}
    {% else %}
    <div class="empty">
        <div class="empty-ic">🏆</div>
        <div class="empty-t">まだランキングデータがありません</div>
        <div class="empty-s">毎日「今すぐ」を送って記録をつけよう！</div>
    </div>
    {% endif %}
</div>

<div class="footer">© なわ太コーチ — Jump Rope AI Coach</div>
</div>
</body>
</html>
"""
    return render_template_string(html, ranking_data=ranking_data)




@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """設定画面 - 明るいクリーンデザイン"""
    try:
        user_id = request.args.get('user_id')

        # ロゴHTML（環境変数 LOGO_IMAGE_URL が設定されていれば画像、なければテキスト）
        if LOGO_IMAGE_URL:
            logo_html = f'<img src="{LOGO_IMAGE_URL}" alt="なわ太コーチ" style="height:30px;width:auto;object-fit:contain;display:block;">'
        else:
            logo_html = '<span style="font-size:15px;font-weight:700;color:#1e293b;">🪢 なわ太コーチ</span>'

        if not user_id:
            return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>エラー — なわ太コーチ</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;600;700&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:"Noto Sans JP",sans-serif;background:#f1f5f9;min-height:100vh;display:flex;flex-direction:column}}
.nav{{background:#fff;border-bottom:1px solid #e2e8f0;padding:0 20px}}.nav-in{{max-width:520px;margin:0 auto;height:56px;display:flex;align-items:center}}
.body{{flex:1;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:#fff;border-radius:16px;padding:48px 32px;text-align:center;max-width:340px;width:100%;box-shadow:0 1px 3px rgba(0,0,0,0.06),0 4px 16px rgba(0,0,0,0.06)}}
.ic{{font-size:48px;margin-bottom:16px}}h2{{font-size:18px;color:#1e293b;margin-bottom:10px;font-weight:700}}p{{font-size:14px;color:#64748b;line-height:1.7}}</style></head>
<body>
<nav class="nav"><div class="nav-in">{logo_html}</div></nav>
<div class="body"><div class="card"><div class="ic">⚠️</div><h2>アクセスエラー</h2><p>ユーザーIDが見つかりません。<br>LINEから再度アクセスしてください。</p></div></div>
</body></html>""", 400

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
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>設定完了 — なわ太コーチ</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;600;700;900&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:"Noto Sans JP",sans-serif;background:#f1f5f9;min-height:100vh;display:flex;flex-direction:column}}
.nav{{background:#fff;border-bottom:1px solid #e2e8f0;padding:0 20px;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}.nav-in{{max-width:520px;margin:0 auto;height:56px;display:flex;align-items:center}}
.body{{flex:1;display:flex;align-items:center;justify-content:center;padding:32px 20px}}
.card{{background:#fff;border-radius:20px;padding:48px 32px;text-align:center;max-width:380px;width:100%;box-shadow:0 1px 3px rgba(0,0,0,0.06),0 8px 32px rgba(0,0,0,0.08);animation:pop .45s cubic-bezier(.34,1.56,.64,1) both}}
@keyframes pop{{from{{opacity:0;transform:scale(.85)}}to{{opacity:1;transform:scale(1)}}}}
.check{{width:72px;height:72px;background:linear-gradient(135deg,#34d399,#10b981);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 24px;font-size:34px;box-shadow:0 0 32px rgba(16,185,129,.25)}}
h2{{font-size:22px;font-weight:700;color:#1e293b;margin-bottom:8px}}p{{font-size:14px;color:#64748b;line-height:1.7;margin-bottom:28px}}
.btn{{display:inline-flex;align-items:center;gap:8px;padding:13px 28px;background:linear-gradient(135deg,#f97316,#fb923c);color:#fff;text-decoration:none;border-radius:100px;font-size:14px;font-weight:700;box-shadow:0 4px 16px rgba(249,115,22,.35);transition:.2s}}
.btn:hover{{transform:translateY(-2px);box-shadow:0 6px 24px rgba(249,115,22,.45)}}
.note{{margin-top:18px;font-size:12px;color:#94a3b8}}</style></head>
<body>
<nav class="nav"><div class="nav-in">{logo_html}</div></nav>
<div class="body"><div class="card">
<div class="check">✓</div>
<h2>設定を保存しました！</h2>
<p>「今すぐ」と送信すると<br>新しい設定で課題が届きます。</p>
<a href="{ranking_url}" class="btn">🔥 ランキングを見る</a>
<div class="note">LINEの画面に戻ってください</div>
</div></div>
</body></html>"""

        current_settings = get_user_settings(user_id)
        current_nickname = current_settings.get('nickname', '') or ''
        current_level = current_settings['level']
        current_personality = current_settings.get('coach_personality', '優しい')

        personality_emojis = {"熱血":"🔥","優しい":"😊","厳しい":"💪","フレンドリー":"✌️","冷静":"🧠"}
        personality_descs = {"熱血":"情熱的に鼓舞する","優しい":"丁寧で穏やかに","厳しい":"ストイックに追い込む","フレンドリー":"タメ口で親しみやすく","冷静":"論理的・分析的に"}

        ranking_url = f"{APP_PUBLIC_URL}/ranking"
        initial = current_nickname[0] if current_nickname else "？"

        level_cards_html = ""
        for lname, linfo in USER_LEVELS.items():
            active = "active" if lname == current_level else ""
            level_cards_html += f"""<div class="lv-card {active}" onclick="selLv('{lname}',this)">
  <div class="lv-chk">✓</div>
  <div class="lv-name">{lname}</div>
  <div class="lv-desc">{linfo['description']}</div>
</div>"""

        pers_cards_html = ""
        for pname in COACH_PERSONALITIES:
            active = "active" if pname == current_personality else ""
            emoji = personality_emojis.get(pname, "😊")
            desc = personality_descs.get(pname, "")
            pers_cards_html += f"""<div class="p-card {active}" onclick="selP('{pname}',this)">
  <div class="p-em">{emoji}</div>
  <div class="p-info"><div class="p-name">{pname}</div><div class="p-desc">{desc}</div></div>
  <div class="p-dot"></div>
</div>"""

        html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>練習設定 — なわ太コーチ</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;900&family=Barlow+Condensed:wght@700;900&display=swap" rel="stylesheet">
<style>
:root{{--bg:#f1f5f9;--surface:#fff;--surf2:#f8fafc;--border:#e2e8f0;--text:#1e293b;--muted:#64748b;--accent:#f97316;--acc2:#fb923c;--r:14px;--sh:0 1px 3px rgba(0,0,0,0.06),0 4px 16px rgba(0,0,0,0.06)}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Noto Sans JP",sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}

/* ナビ */
.nav{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 20px;position:sticky;top:0;z-index:99;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
.nav-in{{max-width:520px;margin:0 auto;height:56px;display:flex;align-items:center;justify-content:space-between}}
.nav-link{{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:var(--muted);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:100px;transition:.2s}}
.nav-link:hover{{color:var(--text);background:var(--surf2)}}

/* ラッパー */
.wrap{{max-width:520px;margin:0 auto;padding:24px 20px 60px}}

/* プロフィールカード */
.profile-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:20px;display:flex;align-items:center;gap:16px;margin-bottom:24px;box-shadow:var(--sh)}}
.av{{width:58px;height:58px;border-radius:50%;border:3px solid #e2e8f0;background:linear-gradient(135deg,#dbeafe,#3b82f6);display:flex;align-items:center;justify-content:center;font-family:"Barlow Condensed",sans-serif;font-size:24px;font-weight:900;color:#fff;flex-shrink:0;transition:border-color .2s}}
.p-name-lg{{font-size:17px;font-weight:700;margin-bottom:2px}}
.p-meta{{font-size:13px;color:var(--muted)}}
.p-hint{{font-size:11px;color:var(--accent);margin-top:4px}}

/* セクション */
.sec{{margin-bottom:20px}}
.sec-lbl{{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;margin-bottom:8px;padding-left:2px}}

/* テキスト入力 */
.inp-box{{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh);transition:border-color .2s,box-shadow .2s}}
.inp-box:focus-within{{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}}
.inp-row{{display:flex;align-items:center;padding:0 14px}}
.inp-ic{{font-size:16px;margin-right:10px;flex-shrink:0}}
.inp-f{{flex:1;background:transparent;border:none;outline:none;font-family:"Noto Sans JP",sans-serif;font-size:15px;font-weight:500;color:var(--text);padding:14px 0}}
.inp-f::placeholder{{color:var(--muted);font-weight:400}}
.inp-cnt{{font-size:11px;color:var(--muted);flex-shrink:0}}
.inp-hint{{font-size:11px;color:var(--muted);padding:0 14px 10px;opacity:.75}}

/* レベルカード */
.lv-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.lv-card{{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--r);padding:14px 12px;cursor:pointer;transition:all .2s;box-shadow:var(--sh);position:relative}}
.lv-card:hover{{border-color:rgba(59,130,246,.4);transform:translateY(-2px);box-shadow:0 6px 24px rgba(0,0,0,0.09)}}
.lv-card.active{{border-color:var(--accent);background:#fff7ed;box-shadow:0 0 0 1px rgba(249,115,22,.15),0 4px 16px rgba(249,115,22,.1)}}
.lv-chk{{position:absolute;top:10px;right:10px;width:18px;height:18px;border-radius:50%;border:1.5px solid var(--muted);display:flex;align-items:center;justify-content:center;font-size:10px;color:transparent;transition:.2s}}
.lv-card.active .lv-chk{{background:var(--accent);border-color:var(--accent);color:#fff}}
.lv-name{{font-size:15px;font-weight:700;margin-bottom:3px}}
.lv-desc{{font-size:11px;color:var(--muted);line-height:1.5}}

/* パーソナリティ */
.p-list{{display:flex;flex-direction:column;gap:8px}}
.p-card{{display:flex;align-items:center;gap:12px;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--r);padding:12px 14px;cursor:pointer;transition:all .2s;box-shadow:var(--sh)}}
.p-card:hover{{border-color:rgba(59,130,246,.4);transform:translateX(3px)}}
.p-card.active{{border-color:var(--accent);background:#fff7ed}}
.p-em{{font-size:22px;width:32px;text-align:center;flex-shrink:0}}
.p-info{{flex:1}}
.p-name{{font-size:14px;font-weight:700}}
.p-desc{{font-size:11px;color:var(--muted);margin-top:2px}}
.p-dot{{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--muted);display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:.2s}}
.p-card.active .p-dot{{border-color:var(--accent);background:var(--accent)}}
.p-card.active .p-dot::after{{content:"";width:6px;height:6px;background:#fff;border-radius:50%}}

/* 保存ボタン */
.save-btn{{width:100%;padding:16px;background:linear-gradient(135deg,var(--accent),var(--acc2));color:#fff;border:none;border-radius:var(--r);font-family:"Noto Sans JP",sans-serif;font-size:16px;font-weight:700;cursor:pointer;box-shadow:0 4px 16px rgba(249,115,22,.35);transition:.25s;margin-top:8px}}
.save-btn:hover{{transform:translateY(-2px);box-shadow:0 6px 24px rgba(249,115,22,.45)}}
.save-btn:active{{transform:translateY(0)}}

/* ランキングバナー */
.rank-banner{{display:flex;align-items:center;justify-content:space-between;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--r);padding:16px;text-decoration:none;box-shadow:var(--sh);margin-top:12px;transition:.2s}}
.rank-banner:hover{{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 6px 24px rgba(249,115,22,.1)}}
.rb-l{{display:flex;align-items:center;gap:12px}}
.rb-ic{{font-size:28px}}
.rb-t{{font-size:14px;font-weight:700;color:var(--text)}}
.rb-s{{font-size:12px;color:var(--muted);margin-top:2px}}
.rb-arr{{font-size:20px;color:var(--muted)}}
.divider{{height:1px;background:var(--border);margin:20px 0}}
@media(max-width:400px){{.lv-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-in">
    {logo_html}
    <a href="{ranking_url}" class="nav-link">🔥 ランキング</a>
  </div>
</nav>

<div class="wrap">

<div class="profile-card">
  <div class="av" id="avCircle">{initial}</div>
  <div>
    <div class="p-name-lg" id="heroName">{current_nickname or '名前を設定しよう'}</div>
    <div class="p-meta">{current_level} ・ {current_personality}コーチ</div>
    <div class="p-hint">✏️ 設定を編集中</div>
  </div>
</div>

<form method="POST" id="sf">
  <input type="hidden" name="level" id="lvInp" value="{current_level}">
  <input type="hidden" name="coach_personality" id="pInp" value="{current_personality}">

  <div class="sec">
    <div class="sec-lbl">ニックネーム</div>
    <div class="inp-box">
      <div class="inp-row">
        <span class="inp-ic">✏️</span>
        <input type="text" name="nickname" class="inp-f" value="{current_nickname}" maxlength="10" placeholder="例：ジャンプ太郎" id="nickInp" oninput="onNick(this)">
        <span class="inp-cnt" id="cnt">{len(current_nickname)}/10</span>
      </div>
      <div class="inp-hint">ランキングに表示されます（10文字まで）</div>
    </div>
  </div>

  <div class="divider"></div>

  <div class="sec">
    <div class="sec-lbl">🎯 練習レベル</div>
    <div class="lv-grid">{level_cards_html}</div>
  </div>

  <div class="divider"></div>

  <div class="sec">
    <div class="sec-lbl">😊 コーチの性格</div>
    <div class="p-list">{pers_cards_html}</div>
  </div>

  <button type="submit" class="save-btn">💾 設定を保存する</button>
</form>

<a href="{ranking_url}" class="rank-banner">
  <div class="rb-l"><div class="rb-ic">🏆</div><div><div class="rb-t">連続記録ランキング</div><div class="rb-s">みんなの記録をチェック！</div></div></div>
  <div class="rb-arr">›</div>
</a>
</div>

<script>
function onNick(el){{
  document.getElementById("cnt").textContent=el.value.length+"/10";
  document.getElementById("heroName").textContent=el.value||"名前を設定しよう";
  document.getElementById("avCircle").textContent=el.value?el.value[0]:"？";
}}
function selLv(name,el){{
  document.querySelectorAll(".lv-card").forEach(c=>c.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("lvInp").value=name;
}}
function selP(name,el){{
  document.querySelectorAll(".p-card").forEach(c=>c.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("pInp").value=name;
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

            # 今日の即時配信回数をチェック
            resp = supabase.table("users").select(
                "immediate_request_count, last_immediate_request_date"
            ).eq("user_id", user_id).execute()

            immediate_count = 0
            last_request_date = None

            if resp.data:
                immediate_count = resp.data[0].get("immediate_request_count") or 0
                last_request_date = resp.data[0].get("last_immediate_request_date")

            # 日付が変わっていたらカウントをリセット
            if last_request_date != today:
                immediate_count = 0
                supabase.table("users").update({
                    "immediate_request_count": 0,
                    "last_immediate_request_date": today,
                }).eq("user_id", user_id).execute()

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
            supabase.table("users").update({
                "immediate_request_count": immediate_count + 1,
                "last_immediate_request_date": today,
            }).eq("user_id", user_id).execute()

            print(f"🚀 [{timestamp}] Immediate delivery requested by {user_id[:8]}... ({immediate_count + 1}/3 today)")

            # 課題を生成してreplyで返信
            challenge_content = create_challenge_message(user_id, settings['level'])

            full_message = challenge_content + "\n\n💬 フィードバック\n「できた」「難しかった」と送ると、次回の課題が調整されます！"

            messages = [TextSendMessage(text=full_message)]

            # 応援メッセージ（配信3回以降、1回だけ）
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

# Supabase接続確認
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