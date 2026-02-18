import os
import json
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
WELCOME_STAMP_URL = os.environ.get("WELCOME_STAMP_URL", "https://example.com/welcome_stamp.png")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("🚨 必要な環境変数が設定されていません")

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
# ユーザー設定の取得
# ==========================================
def get_user_settings(user_id):
    """ユーザー設定を取得"""
    try:
        res = supabase.table("users").select("*").eq("user_id", user_id).execute()

        if res.data:
            row = res.data[0]
            return {
                'level': row.get('level', '初心者'),
                'nickname': row.get('nickname'),
                'coach_personality': row.get('coach_personality', '優しい'),
                'delivery_count': row.get('delivery_count', 0),
                'success_count': row.get('success_count', 0),
                'difficulty_count': row.get('difficulty_count', 0),
                'support_shown': row.get('support_shown', 0),
                'last_challenge': row.get('last_challenge'),
                'streak_days': row.get('streak_days', 0),
                'last_challenge_date': row.get('last_challenge_date'),
                'received_welcome_stamp': row.get('received_welcome_stamp', 0),
                'immediate_request_count': row.get('immediate_request_count', 0),
                'last_immediate_request_date': row.get('last_immediate_request_date'),
            }
        else:
            # 新規ユーザー作成
            new_user = {
                'user_id': user_id,
                'level': '初心者',
                'coach_personality': '優しい',
                'delivery_count': 0,
                'success_count': 0,
                'difficulty_count': 0,
                'support_shown': 0,
                'streak_days': 0,
                'received_welcome_stamp': 0,
                'immediate_request_count': 0,
            }
            supabase.table("users").insert(new_user).execute()
            return {
                **new_user,
                'nickname': None,
                'last_challenge': None,
                'last_challenge_date': None,
                'last_immediate_request_date': None,
            }

    except Exception as e:
        print(f"❌ get_user_settings error: {e}")
        return {
            'level': '初心者', 'nickname': None, 'coach_personality': '優しい',
            'delivery_count': 0, 'success_count': 0, 'difficulty_count': 0,
            'support_shown': 0, 'last_challenge': None, 'streak_days': 0,
            'last_challenge_date': None, 'received_welcome_stamp': 0,
            'immediate_request_count': 0, 'last_immediate_request_date': None,
        }

# ==========================================
# ユーザー設定の更新
# ==========================================
def update_user_settings(user_id, level=None, coach_personality=None, nickname=None):
    """レベル、コーチの性格、ニックネームを更新"""
    try:
        print(f"🔧 Updating settings for {user_id[:8]}...")
        current = get_user_settings(user_id)
        update_data = {
            'user_id': user_id,
            'level': level if level is not None else current['level'],
            'coach_personality': coach_personality if coach_personality is not None else current['coach_personality'],
            'nickname': nickname if nickname is not None else current['nickname'],
        }
        supabase.table("users").upsert(update_data).execute()
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
        res = supabase.table("users").select("streak_days, last_challenge_date").eq("user_id", user_id).execute()

        current_streak = 0
        last_date = None

        if res.data:
            current_streak = res.data[0].get('streak_days') or 0
            last_date = res.data[0].get('last_challenge_date')

        # 連続記録の判定
        if last_date == today:
            return current_streak
        elif last_date:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            today_dt = datetime.strptime(today, "%Y-%m-%d")
            diff_days = (today_dt - last_dt).days
            current_streak = current_streak + 1 if diff_days == 1 else 1
        else:
            current_streak = 1

        supabase.table("users").update({
            'streak_days': current_streak,
            'last_challenge_date': today
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
        res = supabase.table("users").select("delivery_count").eq("user_id", user_id).execute()
        current_count = res.data[0].get('delivery_count', 0) if res.data else 0
        supabase.table("users").update({
            'delivery_count': current_count + 1,
            'last_challenge': challenge_text
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
        res = supabase.table("users").select("success_count, difficulty_count").eq("user_id", user_id).execute()
        if res.data:
            row = res.data[0]
            if is_success:
                supabase.table("users").update({
                    'success_count': (row.get('success_count') or 0) + 1
                }).eq("user_id", user_id).execute()
            else:
                supabase.table("users").update({
                    'difficulty_count': (row.get('difficulty_count') or 0) + 1
                }).eq("user_id", user_id).execute()
        print(f"✅ Feedback recorded: {'success' if is_success else 'difficulty'}")
    except Exception as e:
        print(f"❌ record_feedback error: {e}")

# ==========================================
# 応援メッセージフラグ
# ==========================================
def mark_support_shown(user_id):
    """応援メッセージを表示済みにする"""
    try:
        supabase.table("users").update({'support_shown': 1}).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"❌ mark_support_shown error: {e}")

# ==========================================
# ウェルカムスタンプ送信済みフラグ
# ==========================================
def mark_welcome_stamp_sent(user_id):
    """ウェルカムスタンプを送信済みにする"""
    try:
        supabase.table("users").update({'received_welcome_stamp': 1}).eq("user_id", user_id).execute()
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
- 段階的な難度上昇を意識する
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

課題例:
- 初めのうちは「KNTJを安定させて1回」など単発
- 「できた」の回数が増えてきたら「EBTJ → インバースEBTJ」など2連続
- さらに慣れてきたら「EBTJ → KNTJ → SOCL」など3連続
- さらに慣れたら「インバースEBTJO → KNTJ → EBTJCL」など難易度の高い3連続
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
- STSOCL / SASOCL / SCLOCL / SOASOCL / SOASOAS / SOCLOCL / SOTSOCL / STSOCLO

室内推奨技:
- 後ろドンキー
- 後ろプッシュアップ
- ドンキー二重
- プッシュアップ二重
- ドンキーtoプッシュアップ
- カミカゼ
- ロンダートから後ろOCLO

激ムズ室内推奨技（室内推奨技を全部クリアしてから出すように）
- 後ろドンキーCL / 後ろプッシュアップCL
- 片手後ろドンキー / 片手後ろプッシュアップ
- SOASOCL → OCLO → SOCLOCL
- STSOCL → OCL → OCLO → SOTSOCL

【重要な難度ガイドライン】
- 最初は基本高難度技の3連続から（例: SOOAS → KNTJO → インバースEBTJO）
- 慣れてきたらO系やAS,CL,TS系の技連続（例: EBTJOO → SOOASO）
- さらに慣れたらAS系,CL系,TS系などの単発（例: KNTJOAS）"""
    }

    success_rate = 0
    difficulty_rate = 0

    if user_history['delivery_count'] > 0:
        success_rate = user_history['success_count'] / user_history['delivery_count']
        difficulty_rate = user_history['difficulty_count'] / user_history['delivery_count']

    adjustment = ""
    if user_history['delivery_count'] >= 2:
        if success_rate > 0.7:
            adjustment = "【重要】ユーザーは非常に好調です（成功率70%以上）。難度を1段階上げてください。"
        elif difficulty_rate > 0.6:
            adjustment = "【重要】ユーザーは苦戦中です（難しかった率60%以上）。難度を1〜2段階下げてください。"
        elif success_rate > 0.4 and difficulty_rate <= 0.4:
            adjustment = "ユーザーは順調です。現在の難度を維持してください。"
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
- 「フロー」「リカバリー」「クリーンフィニッシュ」は使用禁止
- 抽象的表現は絶対NG
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
                100: {"duration": "75秒", "target": "10点超え", "message": "🎊100日達成おめでとう！！🎊 最高峰の演技で有終の美を飾ろう！"},
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
                "超上級者": "今日のお題：\nEBTJOO → KNTJCL！\n\n限界を超えろ！お前ならできる！🔥💪"
            },
            "優しい": {
                "初心者": "今日のお題：\n三重とびを3回連続。\n\nゆっくりでいいので、焦らず練習しましょうね😊",
                "中級者": "今日のお題：\nEBTJを5回。\n\n無理しないでくださいね。少しずつ上達していきましょう💪",
                "上級者": "今日のお題：\nSOOASを1回。\n\n質を大切に、丁寧に練習してみてください✨",
                "超上級者": "今日のお題：\nEBTJOを安定させて1回。\n\n無理せず丁寧に。質を大切に練習しましょう✨"
            },
            "厳しい": {
                "初心者": "今日のお題：\n三重とび5回連続。\n\nできて当然だ。甘えるな。",
                "中級者": "今日のお題：\nKNTJ → インバースKNTJ。\n\n妥協するな。完璧を目指せ。",
                "上級者": "今日のお題：\nSOOAS → SOOTS。\n\nできるまでやれ。結果が全てだ。",
                "超上級者": "今日のお題：\nEBTJOO → KNTJTS。\n\n限界など存在しない。やり続けろ。"
            },
            "フレンドリー": {
                "初心者": "今日のお題：\n三重とび3回連続いってみよ！\n\n楽しくやろ！一緒に頑張ろ！✨😊",
                "中級者": "今日のお題：\nEBTJ → KNTJ やろ！\n\n一緒に頑張ろ！絶対できるって！💪",
                "上級者": "今日のお題：\nSOOASいい感じで決めちゃお！\n\nお前ならいけるって！信じてる！🔥",
                "超上級者": "今日のお題：\nEBTJOOかっこよく決めちゃお！\n\n一緒に限界突破しよ！絶対いける！🔥"
            },
            "冷静": {
                "初心者": "今日のお題：\n三重とび3回。\n\n安定性を重視して、効率的な動作を心がけてください。",
                "中級者": "今日のお題：\nEBTJ 5回。\n\n動作の効率性を分析しながら練習してください。",
                "上級者": "今日のお題：\nSOOAS 1回。\n\n質を分析し、データ的に最適な動作を目指してください。",
                "超上級者": "今日のお題：\nEBTJOO 1回。\n\n動作を論理的に分析し、最適化してください。"
            }
        }
        personality_fallback = fallback_by_personality.get(coach_personality, fallback_by_personality["優しい"])
        return personality_fallback.get(level, personality_fallback.get("初心者", "今日のお題：\n前とび30秒を安定させてみよう！"))


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
    """全ユーザーのランキングデータを取得"""
    try:
        res = supabase.table("users") \
            .select("nickname, streak_days, level, last_challenge_date") \
            .gt("streak_days", 0) \
            .order("streak_days", desc=True) \
            .order("last_challenge_date", desc=True) \
            .limit(200) \
            .execute()

        ranking = []
        for row in res.data:
            ranking.append({
                'nickname': row.get('nickname') or '名無しのジャンパー',
                'streak_days': row.get('streak_days', 0),
                'level': row.get('level', '初心者'),
                'last_challenge_date': row.get('last_challenge_date', ''),
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
    return "Jump Rope AI Coach Bot Running ✅"

# ==========================================
# ランキングページ（ページネーション対応）
# ==========================================
@app.route("/ranking")
def ranking():
    ranking_data = get_ranking_data()
    total_count = len(ranking_data)
    ranking_data_json = json.dumps(ranking_data, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>連続記録ランキング - なわ太コーチ</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&family=Bebas+Neue&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0d0f14;
            --surface: #161920;
            --surface2: #1e2230;
            --border: #2a2f3f;
            --text: #e8eaf0;
            --text-muted: #6b7194;
            --accent: #f97316;
            --gold: #f59e0b;
            --silver: #94a3b8;
            --bronze: #cd7c4a;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Noto Sans JP', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background-image:
                linear-gradient(rgba(249,115,22,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(249,115,22,0.03) 1px, transparent 1px);
            background-size: 48px 48px;
            pointer-events: none;
            z-index: 0;
        }
        .page-wrapper {
            position: relative;
            z-index: 1;
            max-width: 600px;
            margin: 0 auto;
            padding: 0 16px 80px;
        }
        .header {
            padding: 36px 0 20px;
            text-align: center;
        }
        .header-label {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 11px;
            letter-spacing: 4px;
            color: var(--accent);
            margin-bottom: 8px;
            display: block;
        }
        .header h1 {
            font-size: clamp(22px, 6vw, 32px);
            font-weight: 900;
            color: var(--text);
            line-height: 1.2;
        }
        .header-sub {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 6px;
        }
        .header-divider {
            width: 36px;
            height: 2px;
            background: var(--accent);
            margin: 14px auto 0;
        }
        .total-count {
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            margin: 16px 0 20px;
        }
        .total-count strong { color: var(--accent); }

        /* ページナビ */
        .page-nav {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .nav-arrow {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text-muted);
            font-size: 18px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .nav-arrow:hover:not(:disabled) {
            border-color: var(--accent);
            color: var(--accent);
        }
        .nav-arrow:disabled { opacity: 0.25; cursor: not-allowed; }
        .nav-tabs {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            justify-content: center;
        }
        .nav-tab {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text-muted);
            font-size: 13px;
            font-weight: 700;
            font-family: 'Noto Sans JP', sans-serif;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .nav-tab:hover { border-color: var(--accent); color: var(--accent); }
        .nav-tab.active {
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }
        .page-label {
            font-size: 12px;
            color: var(--text-muted);
            text-align: center;
            margin-bottom: 14px;
        }
        .page-label strong { color: var(--accent); }

        /* ランキングリスト */
        .ranking-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .ranking-item {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px 16px;
            display: flex;
            align-items: center;
            gap: 14px;
            transition: border-color 0.2s, transform 0.15s;
            opacity: 0;
            transform: translateY(14px);
            animation: fadeUp 0.32s ease forwards;
        }
        .ranking-item:hover {
            border-color: rgba(249,115,22,0.35);
            transform: translateX(4px);
        }

        /* トップ3 */
        .ranking-item.rank-1 {
            background: rgba(245,158,11,0.07);
            border-color: rgba(245,158,11,0.28);
        }
        .ranking-item.rank-2 {
            background: rgba(148,163,184,0.05);
            border-color: rgba(148,163,184,0.22);
        }
        .ranking-item.rank-3 {
            background: rgba(205,124,74,0.06);
            border-color: rgba(205,124,74,0.22);
        }

        /* アニメーション遅延 */
        .ranking-item:nth-child(1)  { animation-delay: 0.04s; }
        .ranking-item:nth-child(2)  { animation-delay: 0.08s; }
        .ranking-item:nth-child(3)  { animation-delay: 0.12s; }
        .ranking-item:nth-child(4)  { animation-delay: 0.16s; }
        .ranking-item:nth-child(5)  { animation-delay: 0.20s; }
        .ranking-item:nth-child(6)  { animation-delay: 0.24s; }
        .ranking-item:nth-child(7)  { animation-delay: 0.28s; }
        .ranking-item:nth-child(8)  { animation-delay: 0.32s; }
        .ranking-item:nth-child(9)  { animation-delay: 0.36s; }
        .ranking-item:nth-child(10) { animation-delay: 0.40s; }

        @keyframes fadeUp {
            to { opacity: 1; transform: translateY(0); }
        }

        /* 1位グロウパルス */
        @keyframes pulse-glow {
            0%, 100% { box-shadow: 0 0 0 0 rgba(245,158,11,0); }
            50% { box-shadow: 0 0 20px 4px rgba(245,158,11,0.14); }
        }
        .ranking-item.rank-1 {
            animation: fadeUp 0.32s ease 0.04s forwards, pulse-glow 2.8s ease-in-out 0.5s infinite;
        }

        /* 順位バッジ */
        .rank-badge {
            width: 38px;
            height: 38px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
            font-size: 14px;
            flex-shrink: 0;
            background: var(--surface2);
            color: var(--text-muted);
            border: 1px solid var(--border);
        }
        .rank-badge.gold   { background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.4); color: var(--gold); font-size: 22px; }
        .rank-badge.silver { background: rgba(148,163,184,0.09); border-color: rgba(148,163,184,0.35); color: var(--silver); font-size: 22px; }
        .rank-badge.bronze { background: rgba(205,124,74,0.09); border-color: rgba(205,124,74,0.35); color: var(--bronze); font-size: 22px; }

        .user-info { flex: 1; min-width: 0; }
        .user-nickname {
            font-size: 15px;
            font-weight: 700;
            color: var(--text);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .user-level { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

        .streak-block { text-align: right; flex-shrink: 0; }
        .streak-value {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 28px;
            line-height: 1;
            color: var(--accent);
            letter-spacing: 1px;
        }
        .streak-unit { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

        /* 1位シマー */
        @keyframes shimmer {
            0%   { background-position: -200% center; }
            100% { background-position:  200% center; }
        }
        .rank-1 .streak-value {
            background: linear-gradient(90deg, var(--gold) 0%, #fde68a 40%, var(--gold) 60%, #f59e0b 100%);
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: shimmer 3s linear infinite;
        }
        .rank-2 .streak-value { color: var(--silver); }
        .rank-3 .streak-value { color: var(--bronze); }

        /* 空状態 */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-muted);
        }
        .empty-state-icon { font-size: 52px; opacity: 0.4; margin-bottom: 14px; }

        /* 更新ボタン */
        .refresh-btn {
            display: block;
            width: 100%;
            max-width: 220px;
            margin: 24px auto 0;
            padding: 11px;
            background: transparent;
            border: 1px solid var(--border);
            border-radius: 10px;
            color: var(--text-muted);
            font-size: 13px;
            font-family: 'Noto Sans JP', sans-serif;
            cursor: pointer;
            transition: all 0.2s;
        }
        .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }

        @media (max-width: 400px) {
            .page-wrapper { padding: 0 12px 60px; }
            .user-nickname { font-size: 14px; }
            .streak-value { font-size: 24px; }
            .rank-badge { width: 34px; height: 34px; font-size: 20px; }
        }
    </style>
</head>
<body>
<div class="page-wrapper">
    <div class="header">
        <span class="header-label">STREAK RANKING</span>
        <h1>連続記録<br>ランキング</h1>
        <p class="header-sub">なわ太コーチ — 毎日続けるあなたを称えます</p>
        <div class="header-divider"></div>
    </div>

    <div class="total-count">
        全 <strong id="totalCount">0</strong> 名が挑戦中
    </div>

    <div class="page-nav" id="pageNav">
        <button class="nav-arrow" id="prevBtn" onclick="changePage(currentPage - 1)" disabled>&#8249;</button>
        <div class="nav-tabs" id="navTabs"></div>
        <button class="nav-arrow" id="nextBtn" onclick="changePage(currentPage + 1)">&#8250;</button>
    </div>

    <div class="page-label" id="pageLabel"></div>

    <div class="ranking-list" id="rankingList"></div>

    <button class="refresh-btn" onclick="location.reload()">&#x1F504; 最新に更新</button>
</div>

<script>
const ALL_DATA = RANKING_DATA_PLACEHOLDER;
const PER_PAGE = 10;
const totalPages = Math.max(1, Math.ceil(ALL_DATA.length / PER_PAGE));
let currentPage = 1;

document.getElementById('totalCount').textContent = ALL_DATA.length;

function escapeHtml(str) {
    return String(str || '')
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function getRankBadge(rank) {
    if (rank === 1) return '<div class="rank-badge gold">&#x1F947;</div>';
    if (rank === 2) return '<div class="rank-badge silver">&#x1F948;</div>';
    if (rank === 3) return '<div class="rank-badge bronze">&#x1F949;</div>';
    return '<div class="rank-badge">' + rank + '</div>';
}

function getRankClass(rank) {
    if (rank === 1) return 'rank-1';
    if (rank === 2) return 'rank-2';
    if (rank === 3) return 'rank-3';
    return '';
}

function renderPage(page) {
    currentPage = page;
    const start = (page - 1) * PER_PAGE;
    const end   = start + PER_PAGE;
    const pageData = ALL_DATA.slice(start, end);

    const list = document.getElementById('rankingList');
    if (pageData.length === 0) {
        list.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#x1F4CA;</div><p>まだランキングデータがありません</p></div>';
    } else {
        list.innerHTML = pageData.map(function(user, i) {
            var rank = start + i + 1;
            return '<div class="ranking-item ' + getRankClass(rank) + '">'
                + getRankBadge(rank)
                + '<div class="user-info">'
                + '<div class="user-nickname">' + escapeHtml(user.nickname) + '</div>'
                + '<div class="user-level">' + escapeHtml(user.level) + '</div>'
                + '</div>'
                + '<div class="streak-block">'
                + '<div class="streak-value">' + user.streak_days + '</div>'
                + '<div class="streak-unit">日連続</div>'
                + '</div></div>';
        }).join('');
    }

    // ページラベル
    var startRank = start + 1;
    var endRank   = Math.min(end, ALL_DATA.length);
    document.getElementById('pageLabel').innerHTML =
        '<strong style="color:var(--accent)">' + startRank + '〜' + endRank + '</strong> 位を表示中';

    renderTabs();
}

function renderTabs() {
    var tabs = '';
    for (var i = 1; i <= totalPages; i++) {
        tabs += '<button class="nav-tab' + (i === currentPage ? ' active' : '') + '" onclick="changePage(' + i + ')">' + i + '</button>';
    }
    document.getElementById('navTabs').innerHTML = tabs;

    document.getElementById('prevBtn').disabled = currentPage === 1;
    document.getElementById('nextBtn').disabled = currentPage === totalPages;

    // タブが1ページだけなら矢印エリアを隠す
    if (totalPages <= 1) {
        document.getElementById('pageNav').style.display = 'none';
        document.getElementById('pageLabel').style.display = 'none';
    }
}

function changePage(page) {
    if (page < 1 || page > totalPages) return;
    renderPage(page);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

renderPage(1);
</script>
</body>
</html>
"""

    ranking_data_json = json.dumps(ranking_data, ensure_ascii=False)
    html = html.replace("RANKING_DATA_PLACEHOLDER", ranking_data_json)
    html = html.replace("TOTAL_COUNT_PLACEHOLDER", str(total_count))
    return html


# ==========================================
# 設定画面
# ==========================================
@app.route("/settings", methods=['GET', 'POST'])
def settings():
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
                    body { font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #667eea, #764ba2); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
                    .container { background: white; padding: 40px 30px; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); text-align: center; max-width: 400px; }
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
                    body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #667eea, #764ba2); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
                    .container {{ background: white; padding: 50px 30px; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); text-align: center; max-width: 400px; animation: slideIn 0.4s ease-out; }}
                    @keyframes slideIn {{ from {{ opacity: 0; transform: translateY(-20px); }} to {{ opacity: 1; transform: translateY(0); }} }}
                    .success-icon {{ width: 80px; height: 80px; background: #00B900; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 25px; font-size: 45px; color: white; }}
                    h2 {{ color: #333; margin-bottom: 20px; font-size: 26px; }}
                    p {{ color: #666; font-size: 18px; line-height: 1.8; }}
                    .back-notice {{ margin-top: 30px; padding: 15px; background: #f8f9fa; border-radius: 8px; color: #555; font-size: 15px; }}
                    .ranking-link {{ display: inline-block; margin-top: 20px; padding: 12px 25px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; transition: all 0.3s ease; }}
                    .ranking-link:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(102,126,234,0.4); }}
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
        current_nickname = current_settings.get('nickname') or ''
        current_personality = current_settings.get('coach_personality', '優しい')

        level_options = ''
        for level_name, level_info in USER_LEVELS.items():
            selected = 'selected' if level_name == current_settings['level'] else ''
            level_options += f'<option value="{level_name}" {selected}>{level_name}（{level_info["description"]}）</option>'

        personality_options = ''
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
                body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; display: flex; align-items: center; justify-content: center; }}
                .container {{ max-width: 420px; width: 100%; background: white; padding: 35px 30px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); animation: fadeIn 0.5s ease-out; }}
                @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(20px); }} to {{ opacity: 1; transform: translateY(0); }} }}
                .header {{ text-align: center; margin-bottom: 30px; }}
                .header-icon {{ font-size: 48px; margin-bottom: 10px; }}
                h2 {{ color: #2c3e50; font-size: 24px; font-weight: 600; margin-bottom: 8px; }}
                .subtitle {{ color: #7f8c8d; font-size: 14px; }}
                .current-settings {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); padding: 15px; border-radius: 12px; margin-bottom: 25px; color: white; font-size: 14px; text-align: center; }}
                .current-settings strong {{ font-weight: 600; }}
                .form-group {{ margin-bottom: 25px; }}
                label {{ display: flex; align-items: center; gap: 8px; color: #2c3e50; font-weight: 600; font-size: 15px; margin-bottom: 10px; }}
                .label-icon {{ font-size: 18px; }}
                select, input[type="text"] {{ width: 100%; padding: 14px 16px; font-size: 16px; border: 2px solid #e0e0e0; border-radius: 12px; background-color: #f8f9fa; transition: all 0.3s ease; font-family: inherit; }}
                select {{ cursor: pointer; appearance: none; background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e"); background-repeat: no-repeat; background-position: right 12px center; background-size: 20px; padding-right: 40px; }}
                select:focus, input[type="text"]:focus {{ outline: none; border-color: #667eea; background-color: white; box-shadow: 0 0 0 3px rgba(102,126,234,0.1); }}
                .nickname-hint {{ font-size: 12px; color: #7f8c8d; margin-top: 5px; }}
                button {{ width: 100%; padding: 16px; background: linear-gradient(135deg, #00B900 0%, #00a000 100%); color: white; border: none; border-radius: 12px; font-size: 17px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 4px 15px rgba(0,185,0,0.3); margin-top: 10px; }}
                button:hover {{ background: linear-gradient(135deg, #00a000 0%, #008f00 100%); transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,185,0,0.4); }}
                button:active {{ transform: translateY(0); }}
                .divider {{ height: 1px; background: linear-gradient(to right, transparent, #e0e0e0, transparent); margin: 25px 0; }}
                .ranking-link {{ display: block; text-align: center; margin-top: 15px; padding: 12px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; text-decoration: none; border-radius: 10px; font-weight: 600; transition: all 0.3s ease; }}
                .ranking-link:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(102,126,234,0.4); }}
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
                        <label><span class="label-icon">👤</span>ニックネーム（ランキング表示用）</label>
                        <input type="text" name="nickname" value="{current_nickname}" maxlength="10" placeholder="例: ジャンプ太郎">
                        <div class="nickname-hint">※ランキングに表示されます（10文字まで）</div>
                    </div>
                    <div class="divider"></div>
                    <div class="form-group">
                        <label><span class="label-icon">🎯</span>レベル</label>
                        <select name="level">{level_options}</select>
                    </div>
                    <div class="divider"></div>
                    <div class="form-group">
                        <label><span class="label-icon">😊</span>コーチの性格</label>
                        <select name="coach_personality">{personality_options}</select>
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


# ==========================================
# LINE Webhook
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
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
    try:
        user_id = event.source.user_id
        text = event.message.text.strip()
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"💬 [{timestamp}] Message from {user_id[:8]}...: '{text}'")

        settings_data = get_user_settings(user_id)

        # 初回ユーザーチェック
        if settings_data['delivery_count'] == 0 and text not in ["設定", "今すぐ", "できた", "難しかった", "友だちに紹介する", "ランキング"]:
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

        # 設定
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

        # ランキング
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

        # 今すぐ（1日3回まで）
        if text == "今すぐ":
            today = datetime.now(JST).strftime("%Y-%m-%d")
            immediate_count = settings_data.get('immediate_request_count', 0) or 0
            last_request_date = settings_data.get('last_immediate_request_date')

            # 日付が変わっていたらリセット
            if last_request_date != today:
                immediate_count = 0
                supabase.table("users").update({
                    'immediate_request_count': 0,
                    'last_immediate_request_date': today
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

            # カウントを増やす
            supabase.table("users").update({
                'immediate_request_count': immediate_count + 1,
                'last_immediate_request_date': today
            }).eq("user_id", user_id).execute()

            print(f"🚀 [{timestamp}] Immediate delivery requested by {user_id[:8]}... ({immediate_count + 1}/3 today)")

            challenge_content = create_challenge_message(user_id, settings_data['level'])
            full_message = challenge_content + "\n\n💬 フィードバック\n「できた」「難しかった」と送ると、次回の課題が調整されます！"
            messages = [TextSendMessage(text=full_message)]

            if settings_data['delivery_count'] >= 3 and settings_data['support_shown'] == 0:
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

        # できた
        if text in ["できた", "成功", "できました", "クリア", "達成"]:
            record_feedback(user_id, is_success=True)
            personality = settings_data.get('coach_personality', '優しい')
            praise_by_personality = {
                "熱血": "素晴らしい！！その調子だ！🔥 次回はもっと難しい技にチャレンジだ！💪",
                "優しい": "素晴らしい！💪 次回の課題で少しレベルアップしますね。無理せず頑張りましょう✨",
                "厳しい": "まだまだこれからだ。次はもっと高みを目指せ。",
                "フレンドリー": "やばい！すごいじゃん！✨ 次もこの調子でいこ！一緒に頑張ろ！",
                "冷静": "データ的に良好です。次回は難度を0.2段階上げます。継続してください。"
            }
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=praise_by_personality.get(personality, praise_by_personality["優しい"])))
            print(f"✅ [{timestamp}] Success feedback recorded")
            return

        # 難しかった
        if text in ["難しかった", "できなかった", "無理", "難しい", "厳しい"]:
            record_feedback(user_id, is_success=False)
            personality = settings_data.get('coach_personality', '優しい')
            encouragement_by_personality = {
                "熱血": "大丈夫だ！お前ならできる！🔥 次回は少し軽めにするから、絶対いけるぞ！💪",
                "優しい": "大丈夫！次回は少し軽めの課題にしますね。焦らず続けましょう🙌 ゆっくりでいいからね",
                "厳しい": "できなかったか。次回は少し戻すが、すぐにまた挑戦してもらう。諦めるな。",
                "フレンドリー": "大丈夫大丈夫！次は少し軽くするね。焦らずいこ！一緒に頑張ろ😊",
                "冷静": "難度設定を調整します。次回は0.3段階下げて再トライしてください。"
            }
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=encouragement_by_personality.get(personality, encouragement_by_personality["優しい"])))
            print(f"⚠️ [{timestamp}] Difficulty feedback recorded")
            return

        # 友だちに紹介する
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

        # デフォルトヘルプ
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
# 起動
# ==========================================
print("\n" + "=" * 70)
print("🚀 Initializing Jump Rope AI Coach Bot (Supabase Edition)")
print("=" * 70 + "\n")

startup_time = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
print(f"✅ Bot initialized at {startup_time}")
print("=" * 70 + "\n")

if __name__ == "__main__":
    print("🔧 Running in development mode")
    app.run(host='0.0.0.0', port=10000, debug=False)