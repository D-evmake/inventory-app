import streamlit as st
import pandas as pd
import hashlib
from datetime import datetime
import io
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

# ──────────────────────────────────────────────
# ページ設定
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="在庫増減チェッカー",
    page_icon="📦",
    layout="wide",
)

# ──────────────────────────────────────────────
# 認証設定（Streamlit Secrets から読み込み）
# ──────────────────────────────────────────────
# パスワードのハッシュ値を .streamlit/secrets.toml (ローカル)
# または Streamlit Cloud の Secrets 設定に記載してください。
#

_USERS: dict[str, str] = {}
if "passwords" in st.secrets:
    for uid, hashed_pw in st.secrets["passwords"].items():
        _USERS[uid] = hashed_pw


def _verify(user_id: str, password: str) -> bool:
    """ID とパスワードを検証する。"""
    if user_id not in _USERS:
        return False
    return _USERS[user_id] == hashlib.sha256(password.encode()).hexdigest()


# ──────────────────────────────────────────────
# ログイン画面
# ──────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.current_user = ""

if not st.session_state.authenticated:
    # ログインフォームを中央寄せ
    _lcol, _ccol, _rcol = st.columns([1, 2, 1])
    with _ccol:
        st.markdown(
            '<div style="text-align:center;margin-top:3rem;">'
            '<h1 style="font-size:3rem;margin-bottom:0;">📦</h1>'
            '<h2 style="margin-top:0.2rem;">在庫増減チェッカー</h2>'
            '<p style="color:#6b7280;">ログインしてください</p>'
            "</div>",
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            uid = st.text_input("ユーザー ID", placeholder="ID を入力")
            pwd = st.text_input("パスワード", type="password", placeholder="パスワードを入力")
            submitted = st.form_submit_button("ログイン", use_container_width=True)

            if submitted:
                if _verify(uid, pwd):
                    st.session_state.authenticated = True
                    st.session_state.current_user = uid
                    st.rerun()
                else:
                    st.error("⚠️ ユーザー ID またはパスワードが正しくありません。")

    st.stop()  # ログインするまでここで停止

# ──────────────────────────────────────────────
# カスタム CSS
# ──────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* デフォルトUIの非表示化 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* 画面上部などの余白を狭くする */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 1.5rem;
    }

    /* ヘッダー */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        color: white;
        text-align: center;
    }
    .main-header h1 { margin: 0; font-size: 2rem; }
    .main-header p  { margin: 0.3rem 0 0 0; opacity: 0.85; font-size: 1rem; }

    /* 増減セル色 */
    .positive { color: #2ecc71; font-weight: 700; }
    .negative { color: #e74c3c; font-weight: 700; }
    .zero     { color: #95a5a6; }

    /* データフレーム幅 */
    .stDataFrame { width: 100% !important; }

    /* 履歴カード */
    .history-card {
        background: linear-gradient(135deg, #f5f7fa, #c3cfe2);
        border-radius: 10px;
        padding: 0.8rem 1.2rem;
        margin-bottom: 0.6rem;
        border-left: 4px solid #667eea;
    }
    .history-card h4 { margin: 0 0 0.3rem 0; color: #2d3748; font-size: 0.95rem; }
    .history-card p  { margin: 0; color: #4a5568; font-size: 0.82rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# ヘッダー
# ──────────────────────────────────────────────
st.markdown(
    '<div class="main-header">'
    "<h1>📦 在庫増減チェッカー</h1>"
    "<p>複数の Excel ファイルをアップロードして、商品ごとの在庫増減を一覧比較できます</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# 列名の自動検出ヘルパー
# ──────────────────────────────────────────────
_JAN_CANDIDATES = ["JANコード", "JAN", "janコード", "jan_code", "barcode", "バーコード", "商品コード"]
_PRODUCT_CANDIDATES = ["商品名", "品名", "製品名", "品番", "商品", "アイテム名", "item", "product"]
_QTY_CANDIDATES = ["個数", "数量", "在庫数", "在庫", "stock", "quantity", "qty"]


def _find_column(columns: pd.Index, candidates: list[str]) -> str | None:
    """大文字小文字を無視して候補名に一致する列を返す。"""
    lower_map = {c.strip().lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _find_master_sheet(sheet_names: list[str]) -> str | None:
    """シート名に 'マスター' を含むシートを自動検出する。"""
    for name in sheet_names:
        if "マスター" in name or "マスタ" in name or "master" in name.lower():
            return name
    return None


def _extract_and_merge(
    xls: pd.ExcelFile,
    main_sheet: str,
    master_sheet: str,
) -> tuple[pd.DataFrame | None, str | None]:
    """
    メインシートとマスターシートからデータを抽出し、
    JANコードをキーに結合して「商品名」「個数」の対応表を返す。

    戻り値: (DataFrame or None, error_message or None)
    """
    # ── メインシート読み込み ──
    try:
        df_main = pd.read_excel(xls, sheet_name=main_sheet, engine="openpyxl")
    except Exception as e:
        return None, f"メインシート「{main_sheet}」の読み込みに失敗: {e}"

    jan_col_main = _find_column(df_main.columns, _JAN_CANDIDATES)
    qty_col = _find_column(df_main.columns, _QTY_CANDIDATES)

    if jan_col_main is None:
        return None, (
            f"メインシート「{main_sheet}」に JANコード列が見つかりません。\n"
            f"  検出対象: {', '.join(_JAN_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_main.columns.tolist())}"
        )
    if qty_col is None:
        return None, (
            f"メインシート「{main_sheet}」に 個数列が見つかりません。\n"
            f"  検出対象: {', '.join(_QTY_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_main.columns.tolist())}"
        )

    main_data = df_main[[jan_col_main, qty_col]].copy()
    main_data.columns = ["JANコード", "個数"]
    main_data["個数"] = pd.to_numeric(main_data["個数"], errors="coerce").fillna(0).astype(int)
    main_data = main_data.dropna(subset=["JANコード"])

    # ── マスターシート読み込み ──
    try:
        df_master = pd.read_excel(xls, sheet_name=master_sheet, engine="openpyxl")
    except Exception as e:
        return None, f"マスターシート「{master_sheet}」の読み込みに失敗: {e}"

    jan_col_master = _find_column(df_master.columns, _JAN_CANDIDATES)
    product_col = _find_column(df_master.columns, _PRODUCT_CANDIDATES)

    if jan_col_master is None:
        return None, (
            f"マスターシート「{master_sheet}」に JANコード列が見つかりません。\n"
            f"  検出対象: {', '.join(_JAN_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_master.columns.tolist())}"
        )
    if product_col is None:
        return None, (
            f"マスターシート「{master_sheet}」に 商品名列が見つかりません。\n"
            f"  検出対象: {', '.join(_PRODUCT_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_master.columns.tolist())}"
        )

    master_data = df_master[[jan_col_master, product_col]].copy()
    master_data.columns = ["JANコード", "商品名"]
    master_data = master_data.dropna(subset=["JANコード", "商品名"])
    # マスターの重複を除去（最初の出現を採用）
    master_data = master_data.drop_duplicates(subset=["JANコード"], keep="first")

    # ── JAN コードをキーに結合 ──
    # VLOOKUP等の数式は無視し、マスターの実データで結合
    merged = pd.merge(main_data, master_data, on="JANコード", how="left")

    # マスターに存在しない JAN コードは商品名を「（不明）」で埋める
    merged["商品名"] = merged["商品名"].fillna("（不明：マスター未登録）")

    # 商品名ごとに個数を合算
    result = merged.groupby("商品名", as_index=False)["個数"].sum()

    return result, None

def _create_pdf(df: pd.DataFrame) -> bytes:
    """データフレームからPDFを生成しバイト列で返す"""
    font_path = "ipaexg.ttf"
    font_name = "IPAexGothic"
    
    # 1. 日本語フォント(IPAexGothic)の登録
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    else:
        # フォントファイルがない場合のフォールバック（文字化けする可能性あり）
        font_name = "Helvetica"

    buffer = io.BytesIO()
    
    # A4横向きで余白を設定
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=landscape(A4),
        rightMargin=30, 
        leftMargin=30, 
        topMargin=30, 
        bottomMargin=30
    )

    # データを2次元リストに変換 (列名 + データ行)
    data = [df.columns.tolist()] + df.values.tolist()

    # 表の列幅を計算 (A4横幅842 - 左右余白60 = 782 を配分)
    # 商品名(1列目)を広く、残りを均等にする
    usable_width = 782
    num_cols = len(df.columns)
    
    if num_cols > 1:
        first_col_w = 200
        other_col_w = (usable_width - first_col_w) / (num_cols - 1)
        col_widths = [first_col_w] + [other_col_w] * (num_cols - 1)
    else:
        col_widths = [usable_width]

    table = Table(data, colWidths=col_widths)

    # テーブルのスタイル設定
    style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),      # 全体に日本語フォントを適用
        ('FONTSIZE', (0, 0), (-1, -1), 10),             # フォントサイズ
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#667eea")), # ヘッダーの背景色
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), # ヘッダーの文字色
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),          # 基本は中央揃え
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),             # 商品名のみ左揃え
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),         # 垂直中央
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),          # ヘッダーの下部余白
        ('TOPPADDING', (0, 0), (-1, 0), 8),             # ヘッダーの上部余白
        ('GRID', (0, 0), (-1, -1), 1, colors.black),    # 全体に1ptの黒い罫線
    ])
    
    # データ行に対し、1行おきに背景色をつけて見やすくする
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor("#f8fafc"))

    table.setStyle(style)
    
    # PDF構築
    doc.build([table])
    return buffer.getvalue()


# ──────────────────────────────────────────────
# セッション初期化
# ──────────────────────────────────────────────
if "slot_ids" not in st.session_state:
    # 各スロットに一意の ID を割り振る（削除対応のため連番ではなくカウンター管理）
    st.session_state.slot_ids = [0, 1]
    st.session_state.next_slot_id = 2

if "history" not in st.session_state:
    st.session_state.history = []  # list[dict]  比較履歴

# ──────────────────────────────────────────────
# スロット操作の コールバック
# ──────────────────────────────────────────────
def _add_slot():
    st.session_state.slot_ids.append(st.session_state.next_slot_id)
    st.session_state.next_slot_id += 1


def _remove_slot(slot_id: int):
    """指定 ID のスロットを削除する。最低 2 枠は維持。"""
    if len(st.session_state.slot_ids) <= 2:
        return
    st.session_state.slot_ids = [s for s in st.session_state.slot_ids if s != slot_id]


# ──────────────────────────────────────────────
# サイドバー：ファイルアップロード
# ──────────────────────────────────────────────
with st.sidebar:
    st.header("📂 ファイルアップロード")
    st.caption("上から順に **古い → 新しい** の順でアップロードしてください。")
    st.caption("💡 各ファイルは「メインシート（JANコード＋個数）」と"
               "「マスターシート（JANコード＋商品名）」を含む Excel ブックを想定しています。")

    slot_ids = st.session_state.slot_ids
    total_slots = len(slot_ids)

    uploaded_files: list[tuple[str | None, pd.DataFrame | None, str | None]] = []

    for pos, sid in enumerate(slot_ids):
        label = f"📄 {pos + 1} 番目のファイル"
        if pos == 0:
            label += "（最古）"
        elif pos == total_slots - 1:
            label += "（最新）"

        file = st.file_uploader(
            label,
            type=["xlsx", "xlsm"],
            key=f"file_{sid}",
        )

        # 削除ボタン（3 枠以上あるとき表示）
        if total_slots > 2:
            if st.button(f"✕ {pos + 1} 番目の枠を削除", key=f"del_{sid}"):
                _remove_slot(sid)
                st.rerun()

        if file is not None:
            try:
                xls = pd.ExcelFile(file, engine="openpyxl")
                sheet_names = xls.sheet_names

                # ── メインシート選択（デフォルト: 1 番目のシート）──
                main_sheet_default = 0
                main_sheet = st.selectbox(
                    f"📋 メインシート（{pos + 1} 番目）",
                    options=sheet_names,
                    index=main_sheet_default,
                    key=f"main_sheet_{sid}",
                    help="JANコードと個数が記載されたシートを選択してください",
                )

                # ── マスターシート選択（自動検出 or ユーザー指定）──
                auto_master = _find_master_sheet(sheet_names)
                if auto_master:
                    master_default_idx = sheet_names.index(auto_master)
                else:
                    # メインシートでない最初のシートを候補にする
                    master_default_idx = 1 if len(sheet_names) > 1 else 0

                master_sheet = st.selectbox(
                    f"📑 マスターシート（{pos + 1} 番目）",
                    options=sheet_names,
                    index=master_default_idx,
                    key=f"master_sheet_{sid}",
                    help="JANコードと商品名が記載されたマスターデータのシートを選択してください",
                )

                # ── データ抽出＆結合 ──
                extracted, err = _extract_and_merge(xls, main_sheet, master_sheet)
                if err:
                    uploaded_files.append((file.name, None, err))
                else:
                    uploaded_files.append((file.name, extracted, None))

            except Exception as e:
                uploaded_files.append((file.name, None, str(e)))
        else:
            uploaded_files.append((None, None, None))

    # 追加ボタン
    if st.button("＋ 新しい比較ファイルを追加", use_container_width=True):
        _add_slot()
        st.rerun()

    st.divider()
    st.markdown(f"**現在の枠数:** {total_slots}")

    # ログアウト
    st.divider()
    st.markdown(f"👤 ログイン中: **{st.session_state.current_user}**")
    if st.button("🚪 ログアウト", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.current_user = ""
        st.rerun()

# ──────────────────────────────────────────────
# メインエリア
# ──────────────────────────────────────────────

# アップロード済みファイル一覧
valid_frames: list[tuple[int, str, pd.DataFrame]] = []

for idx, (name, df, err) in enumerate(uploaded_files):
    if name is None:
        continue
    if err is not None:
        st.warning(f"⚠️ {idx + 1} 番目のファイル（{name}）の読み込みに失敗しました:\n{err}")
        continue

    # df は既に _extract_and_merge で「商品名」「個数」に整形済み
    valid_frames.append((idx + 1, name, df))

# ──────────────────────────────────────────────
# データ結合 & 表示
# ──────────────────────────────────────────────
if len(valid_frames) < 2:
    st.info("📌 比較するには **2 つ以上** の Excel ファイルをアップロードしてください。")

    # 履歴だけは表示
    if st.session_state.history:
        st.divider()
        st.subheader("🕘 比較履歴")
        for hi, h in enumerate(reversed(st.session_state.history)):
            with st.expander(f"📋 {h['timestamp']}　（{h['file_count']} ファイル / {h['product_count']} 商品）", expanded=False):
                st.dataframe(h["dataframe"], use_container_width=True)
    st.stop()

# 結合
merged = valid_frames[0][2][["商品名"]].copy()
col_labels: list[str] = []

for file_no, fname, frame in valid_frames:
    col_name = f"{file_no}番目({fname})"
    col_labels.append(col_name)
    merged = merged.merge(
        frame.rename(columns={"個数": col_name}),
        on="商品名",
        how="outer",
    )

# NaN を 0 に
for c in col_labels:
    merged[c] = merged[c].fillna(0).astype(int)

# 増減列
oldest_col = col_labels[0]
newest_col = col_labels[-1]
merged["増減数"] = merged[newest_col] - merged[oldest_col]

# ソート
merged = merged.sort_values("商品名").reset_index(drop=True)

# 色付け関数
def _style_diff(val):
    if val > 0:
        return "color: #27ae60; font-weight: 700"
    elif val < 0:
        return "color: #e74c3c; font-weight: 700"
    return "color: #95a5a6"

# ──────────────────────────────────────────────
# メインレイアウト分割
# ──────────────────────────────────────────────
left_col, right_col = st.columns([1, 2], gap="large")

with left_col:
    # ──────────────────────────────────────────────
    # フィルタリング — 最新の在庫数フィルタ
    # ──────────────────────────────────────────────
    st.subheader("🔍 最新の在庫数フィルタ")

    filter_options = [
        "フィルタなし",
        "在庫なし（0個）",
        "わずか（1〜9個）",
        "10個台（10〜19個）",
        "20個台（20〜29個）",
        "30個台（30〜39個）",
        "40個以上",
    ]

    selected_filter = st.selectbox(
        "表示条件",
        options=filter_options,
        index=0, # デフォルトは「フィルタなし」
        help="選択した条件に最新の在庫数が一致する商品だけが表示されます",
    )

    # フィルタ適用
    filtered = merged.copy()
    latest_stock = filtered[newest_col]

    if selected_filter == "在庫なし（0個）":
        filtered = filtered[latest_stock == 0]
    elif selected_filter == "わずか（1〜9個）":
        filtered = filtered[(latest_stock >= 1) & (latest_stock <= 9)]
    elif selected_filter == "10個台（10〜19個）":
        filtered = filtered[(latest_stock >= 10) & (latest_stock <= 19)]
    elif selected_filter == "20個台（20〜29個）":
        filtered = filtered[(latest_stock >= 20) & (latest_stock <= 29)]
    elif selected_filter == "30個台（30〜39個）":
        filtered = filtered[(latest_stock >= 30) & (latest_stock <= 39)]
    elif selected_filter == "40個以上":
        filtered = filtered[latest_stock >= 40]

    st.markdown("---")
    st.markdown(
        f"📌 **「{selected_filter}」** に該当する商品:  \n"
        f"**<span style='font-size:1.5rem; color:#e74c3c;'>{len(filtered)}</span>** 件 / 全 {len(merged)} 件",
        unsafe_allow_html=True
    )

    st.markdown("---")
    st.markdown("**📊 サマリー（フィルタ後）**")
    st.metric("増加した商品", f"{(filtered['増減数'] > 0).sum():,}")
    st.metric("減少した商品", f"{(filtered['増減数'] < 0).sum():,}")
    st.metric("変化なし", f"{(filtered['増減数'] == 0).sum():,}")

    st.markdown("---")
    # PDF ダウンロード
    if not filtered.empty:
        pdf_data = _create_pdf(filtered)
        st.download_button(
            label="� 結果を PDF でダウンロード",
            data=pdf_data,
            file_name="inventory_diff.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

with right_col:
    # ──────────────────────────────────────────────
    # テーブル表示
    # ──────────────────────────────────────────────
    st.subheader("📊 比較結果")

    if filtered.empty:
        st.warning("条件に該当するデータがありません。")
    else:
        styled = filtered.style.map(_style_diff, subset=["増減数"])
        st.dataframe(
            styled,
            use_container_width=True,
            height=600,
        )

# ──────────────────────────────────────────────
# 履歴へ保存
# ──────────────────────────────────────────────
file_names_key = tuple(f[1] for f in valid_frames)
already_saved = any(h.get("_key") == file_names_key for h in st.session_state.history)

if not already_saved:
    st.session_state.history.append(
        {
            "_key": file_names_key,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_count": len(valid_frames),
            "file_names": [f[1] for f in valid_frames],
            "product_count": len(merged),
            "dataframe": merged.copy(),
        }
    )

# ──────────────────────────────────────────────
# 比較履歴の表示
# ──────────────────────────────────────────────
if st.session_state.history:
    st.divider()
    st.subheader("🕘 比較履歴")
    st.caption("過去にアップロードして比較した結果が保存されています（ブラウザを閉じるまで保持）。")

    for hi, h in enumerate(reversed(st.session_state.history)):
        label = f"📋 {h['timestamp']}　—　{', '.join(h['file_names'])}　（{h['product_count']} 商品）"
        with st.expander(label, expanded=(hi == 0 and not already_saved)):
            st.dataframe(h["dataframe"], use_container_width=True)
            pdf_h = _create_pdf(h["dataframe"])
            st.download_button(
                label="� この履歴を PDF でダウンロード",
                data=pdf_h,
                file_name=f"history_{h['timestamp'].replace(':', '-')}.pdf",
                mime="application/pdf",
                key=f"dl_hist_{hi}",
                use_container_width=True,
            )

    # 履歴クリアボタン
    if st.button("🗑️ 履歴をすべてクリア", use_container_width=True):
        st.session_state.history = []
        st.rerun()
